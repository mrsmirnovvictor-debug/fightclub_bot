"""Рейды: отряд живых бойцов против одного босса.

Сначала сбор: игрок объявляет рейд на столько-то человек, остальные
записываются кнопкой, пока не выйдет время или не наберётся состав. Потом
бой волнами.

**Волна** — это по разу на каждого, кто ещё стоит. Нажал удар и блок —
размен с боссом считается сразу, не нажал за отпущенное время — пропустил,
а босс своё отработал. Волна закрывается, когда отстрелялись все живые или
вышло время; слова судьи за всю волну уходят одним сообщением, иначе десять
человек выберут минутный запас чата за одну волну.

Правила рейда — в `bot/game/raid.py`, здесь таймеры, сообщения и база.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import random
import time
from dataclasses import dataclass, field, replace

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from bot.board_service import RAID, Board, Pin
from bot.config import Config
from bot.database import Database
from bot.game.classes import Zone, block_combo, block_title
from bot.game.injuries import Injury
from bot.game.combat import (
    Action,
    Fighter,
    group_aftermath,
    resolve_round,
    spread_heal,
)
from bot.game.fightlog import turn_payload
from bot.game.narrator import (
    echo_lines,
    plural,
    board_raid,
    board_raid_over,
    board_window_open,
    board_window_over,
    esc,
    health_warning,
    plain,
    raid_break,
    raid_intro,
    raid_lobby_card,
    raid_panel,
    raid_result,
    strike_lines,
)
from bot.game.potions import get_potion
from bot.potions_service import PotionError, buy_potion
from bot.game.raid import (
    BOSS_ID,
    Window,
    FATIGUE_WAVES,
    Boss,
    CELLAR_RAID,
    RAID_KINDS,
    RaidKind,
    RaidOutcome,
    Temper,
    boss_action,
    boss_stance,
    judge_raid,
    raid_foes,
)
from bot.injury_service import hurt_player
from bot.inventory_service import wear_after_fight
from bot.keyboards import raid_lobby_keyboard
from bot.messaging import Announcer
from bot.models import Player

# Стороны в рейде: отряд заодно, противники против. Нужны приёмам десятой
# ступени — по ним они отличают своих от чужих. Банда вся на одной
# стороне: гопники друг друга не бьют
SQUAD_SIDE, BOSS_SIDE = 0, 1

logger = logging.getLogger(__name__)

# Как часто сверяемся с расписанием. Окно длится два часа, так что
# полминуты запаздывания никто не заметит, а чаще будить бота незачем
WINDOW_TICK = 30

ChatKey = tuple[int, int | None]


class RaidError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class Choice:
    """Незавершённый выбор бойца на свой размен: удар каждой рукой и один блок."""

    attacks: dict[int, Zone] = field(default_factory=dict)
    block: tuple[Zone, ...] = ()

    def ready_for(self, weapons: int = 1) -> bool:
        chosen = [self.attacks.get(hand) for hand in range(weapons)]
        return all(zone is not None for zone in chosen) and bool(self.block)

    def to_action(self, weapons: int = 1) -> Action:
        return Action(
            attacks=tuple(self.attacks.get(hand) for hand in range(weapons)),
            block=self.block,
        )

    @property
    def attack(self) -> Zone | None:
        return self.attacks.get(0)

    @property
    def is_empty(self) -> bool:
        return not self.attacks and not self.block


@dataclass
class RaidLobby:
    """Сбор отряда: кто записался и сколько ждём."""

    id: int
    chat_id: int | None
    thread_id: int | None
    kind: RaidKind
    size: int
    opener_id: int
    chat_title: str = ""
    # Номер записи в базе: по нему считается «один рейд в сутки»
    record_id: int = 0
    members: dict[int, str] = field(default_factory=dict)  # боец → прозвище
    levels: dict[int, int] = field(default_factory=dict)
    message_id: int | None = None
    task: asyncio.Task | None = None
    # Объявления о сборе, развешанные по веткам клуба
    pins: list[Pin] = field(default_factory=list)
    # Когда объявили сбор: по этой метке считается обратный отсчёт. Часы
    # монотонные — перевод системного времени сбор не сломает.
    opened_at: float = field(default_factory=time.monotonic)

    @property
    def boss(self) -> Boss:
        """Лицо рейда: по нему картинка, прозвище и запись в истории."""
        return self.kind.leader

    @property
    def total(self) -> int:
        return len(self.members)

    @property
    def is_full(self) -> bool:
        return self.total >= self.size

    @property
    def can_start(self) -> bool:
        return self.total >= self.kind.min_party

    def seconds_left(self, timeout: int) -> int:
        """Сколько ещё ждут отставших. Время вышло — ноль, не отрицательное."""
        return max(0, round(timeout - (time.monotonic() - self.opened_at)))

    @property
    def key(self) -> ChatKey | None:
        return None if self.chat_id is None else (self.chat_id, self.thread_id)


@dataclass
class RaidSession:
    """Идущий рейд: отряд, противники и текущая волна.

    Противник не один. В казино он всё ещё один — банда из одного
    человека, — а на стадионе их пятеро и больше, и валить их приходится
    по человеку. Поэтому здесь не «босс», а `enemies`: номер → боец.

    **Отряд идёт по банде по кругу.** У бойца в каждый миг один
    противник — тот, до кого дошла очередь, — и после своего размена он
    переходит к следующему: отработал всех, вернулся к первому. Поэтому
    рейд не свалка: бить сразу всех нельзя, и кого бьёшь сейчас, видно.

    Круг один на отряд: все начинают с лидера и идут в ногу, пока
    кто-нибудь не промолчит свой ход. Промолчавший всё равно сдвигается —
    его бьёт тот, кого он должен был бить, — так что из круга не
    выпадают.

    Сложность отсюда и берётся. Банда всегда на двоих глубже отряда, и
    урон приходится разносить по всем, а бьют в ответ столько раз,
    сколько в отряде живых.
    """

    id: int
    chat_id: int | None
    thread_id: int | None
    kind: RaidKind
    enemies: dict[int, Fighter]
    fighters: dict[int, Fighter]
    chat_title: str = ""
    record_id: int = 0
    wave: int = 0
    # Ударов игроков с последней передышки
    strikes: int = 0
    # Сквозной номер размена: по нему судья считает удары в рассказе
    turn_number: int = 0
    choices: dict[int, Choice] = field(default_factory=dict)
    acted: set[int] = field(default_factory=set)
    # Слова судьи за текущую волну и разбор по ходам за весь рейд
    said: list[str] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    # Кого покалечили: боец → травма. Записывается в конце рейда, когда
    # бойцы подняты из базы
    hurt: dict[int, Injury] = field(default_factory=dict)
    fallen: list[int] = field(default_factory=list)
    # Кого из банды свалили в этой волне: номера противников
    dropped: list[int] = field(default_factory=list)
    prompt_message_id: int | None = None
    timer: asyncio.Task | None = None
    resting: bool = False
    finished: bool = False
    # В какой стойке каждый противник стоит эту волну: номер → стойка.
    # Меняется каждую волну, и поэтому её нельзя заучить: кто читает
    # аналитика — держится за ней, кто жмёт одну кнопку — за круг
    # получает ровно столько же, сколько получал бы от противника без
    # характера
    stances: dict[int, int] = field(default_factory=dict)
    # Кого боец бьёт сейчас: боец → номер противника. Сдвигается после
    # каждого его размена — и после промолчанного тоже
    aim: dict[int, int] = field(default_factory=dict)
    summary: list[str] = field(default_factory=list)
    # Кому сколько досталось из кошелька: считается один раз на итоге
    shares: dict[int, int] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ---------- кто против кого ----------

    @property
    def boss(self) -> Boss:
        """Лицо рейда: по нему картинка, прозвище и запись в истории."""
        return self.kind.leader

    @property
    def order(self) -> list[int]:
        """Номера противников в том порядке, в каком банда их выставляет."""
        return list(self.enemies)

    @property
    def standing(self) -> list[int]:
        """Кто из противников ещё на ногах."""
        return [number for number, foe in self.enemies.items() if foe.alive]

    @property
    def gang(self) -> bool:
        """Противник не один — табло и рассказ идут по всей банде."""
        return len(self.enemies) > 1

    def template_of(self, number: int) -> Boss:
        """Шаблон противника: его повадки, прозвище и картинка.

        Банда собирается из шаблонов по порядку, поэтому номер и место в
        наборе — одно и то же.
        """
        roster = self.kind.roster(len(self.fighters))
        index = BOSS_ID - number
        return roster[index] if 0 <= index < len(roster) else self.kind.leader

    def temper_of(self, number: int) -> Temper:
        """Характер противника в этой волне — повёрнутый на здешнюю стойку.

        Одно место на весь клуб: по этим числам он кидает кости, по ним
        же его читает аналитик подписчика. Разойтись им нельзя.
        """
        return self.template_of(number).temper.turned(self.stances.get(number, 0))

    def foe_of(self, user_id: int) -> Fighter | None:
        """Кого этот боец бьёт прямо сейчас. None — некого."""
        number = self.aim.get(user_id)
        return None if number is None else self.enemies.get(number)

    def take_aim(self) -> None:
        """Навести каждого живого бойца на живого противника.

        Сюда попадают только те, у кого цели ещё нет или она уже лежит:
        тот, чья очередь не дошла до конца, остаётся на своём. Новый
        боец начинает с первого по порядку — с лидера.
        """
        standing = self.standing
        if not standing:
            self.aim = {}
            return
        self.aim = {
            user_id: number
            for user_id, number in self.aim.items()
            if self.fighters.get(user_id) is not None
            and self.fighters[user_id].alive
            and number in self.enemies
            and self.enemies[number].alive
        }
        for user_id in self.alive_ids:
            self.aim.setdefault(user_id, standing[0])

    def next_foe(self, number: int) -> int | None:
        """Следующий живой по кругу после этого. None — банда кончилась.

        Круг идёт по порядку, в котором банда выставляет своих, и мёртвых
        перешагивает: отработал последнего — вернулся к первому.
        """
        order = self.order
        if number not in order:
            return self.standing[0] if self.standing else None
        start = order.index(number)
        for step in range(1, len(order) + 1):
            candidate = order[(start + step) % len(order)]
            if self.enemies[candidate].alive:
                return candidate
        return None

    def move_on(self, user_id: int) -> None:
        """Сдвинуть бойца на следующего по кругу — после его размена.

        Сдвигается и тот, кто промолчал: его уже ударил тот, кого он
        должен был бить, и держать его против того же значило бы, что
        молчание ничего не меняет.
        """
        number = self.aim.get(user_id)
        if number is None:
            return
        following = self.next_foe(number)
        if following is None:
            self.aim.pop(user_id, None)
        else:
            self.aim[user_id] = following

    @property
    def key(self) -> ChatKey | None:
        return None if self.chat_id is None else (self.chat_id, self.thread_id)

    @property
    def alive_ids(self) -> list[int]:
        return [uid for uid, fighter in self.fighters.items() if fighter.alive]

    @property
    def wave_over(self) -> bool:
        """Все живые отстрелялись — волну можно закрывать."""
        return all(uid in self.acted for uid in self.alive_ids)

    def choice_of(self, user_id: int) -> Choice:
        return self.choices.setdefault(user_id, Choice())

    def waiting_for(self) -> list[int]:
        return [uid for uid in self.alive_ids if uid not in self.acted]


class RaidService:
    """Сбор отряда, волны рейда и раздача призов."""

    def __init__(
        self,
        bot: Bot,
        db: Database,
        config: Config,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.voice = Announcer(bot)
        self.board = Board(db, self.voice)
        self.rng = rng or random.Random()
        self._ids = itertools.count(1)
        self._lobbies: dict[int, RaidLobby] = {}
        self._raids: dict[int, RaidSession] = {}
        self._by_chat: dict[ChatKey, int] = {}
        self._busy: dict[int, str] = {}  # боец → «lobby» или «raid»
        self._results: dict[int, RaidSession] = {}
        self._watch: asyncio.Task | None = None
        # Закрепы объявлений об открытых окнах: по рейду на запись, и
        # снимаются, когда окно закроется. Рейдов в городе два, расписания
        # у них свои, и одним полем они бы друг друга затирали
        self._window_pins: dict[str, list[Pin]] = {}
        self._window_open: dict[str, Window] = {}

    # ---------- расписание ----------

    def start_watching(self) -> None:
        """Следить за расписанием: открылось окно — объявить, закрылось — снять.

        Отдельной задачей, а не проверкой на входе игрока: объявление
        нужно тем, кто сейчас не в приложении, — иначе о рейде узнают
        только те, кто и так смотрел на карту в нужную минуту.
        """
        if self._watch is None and not self.config.raid_any_time:
            self._watch = asyncio.create_task(self._watch_windows())

    async def _watch_windows(self) -> None:
        while True:
            try:
                await self._check_window()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - расписание не стоит бота
                logger.exception("Не вышло объявить окно рейда")
            await asyncio.sleep(WINDOW_TICK)

    async def _check_window(self, moment: int | None = None) -> None:
        """Одна сверка с расписанием — по каждому рейду города отдельно."""
        for kind in RAID_KINDS:
            await self._check_kind_window(self._tuned(kind), moment)

    async def _check_kind_window(
        self, kind: RaidKind, moment: int | None = None
    ) -> None:
        """Объявить открытое окно этого рейда и закрыть прошлое."""
        window = self.window_now(moment, kind)
        was = self._window_open.get(kind.code)
        if was and (window is None or window.start != was.start):
            self._window_open.pop(kind.code, None)
            pins = self._window_pins.pop(kind.code, [])
            if pins:
                await self.board.close(pins, board_window_over(kind, was))
        if window is None or kind.code in self._window_open:
            return
        # Заявку на объявление ставим в базе: перезапуск бота посреди окна
        # не должен объявлять его во второй раз. Ключ с кодом рейда —
        # окна двух рейдов могут начаться в один и тот же час
        if not await self.db.mark_announced(
            0, f"raid-window:{kind.code}:{window.start}"
        ):
            self._window_open[kind.code] = window
            return
        self._window_open[kind.code] = window
        self._window_pins[kind.code] = await self.board.announce(
            RAID,
            board_window_open(kind, window),
            disable_web_page_preview=True,
        )

    # ---------- сбор отряда ----------

    def window_now(
        self, moment: int | None = None, kind: RaidKind = CELLAR_RAID
    ) -> Window | None:
        """Открыт ли этот рейд. Выключатель в настройках снимает расписание."""
        if self.config.raid_any_time:
            return kind.any_window(moment)
        return kind.window_now(moment)

    def _tuned(self, kind: RaidKind) -> RaidKind:
        """Рейд с правками из настроек: кошель и прибавка здоровья — оттуда.

        Настройки правят числа, а не правила: состав банды, расписание и
        пропуск лежат в `bot/game/raid.py` и переменной среды не меняются.
        """
        if kind.code == CELLAR_RAID.code:
            return replace(
                kind,
                purse=self.config.raid_purse,
                hp_share=self.config.raid_boss_hp_share,
                turn_seconds=self.config.raid_turn_timeout,
            )
        return replace(
            kind,
            purse=self.config.raid_gang_purse,
            turn_seconds=self.config.raid_gang_turn_timeout,
        )

    async def _admit(
        self, player: Player, buy: bool = False, kind: RaidKind = CELLAR_RAID
    ) -> None:
        """Пустить бойца в рейд: расписание, победа в окне и пропуск.

        Пропуск списывается один раз на окно. Проиграл или вышел из лобби —
        заходи снова бесплатно, окно уже открыто. Победил — окно закрылось,
        и следующая попытка будет только в следующем.

        Окна двух рейдов могут начаться в один и тот же час, поэтому и
        пропуск, и победа считаются по коду рейда вместе с часом: билет
        на матч не должен сходить за талон казино.
        """
        window = self.window_now(kind=kind)
        if window is None:
            raise RaidError(
                f"{kind.title}: закрыто. Пускают {kind.schedule_text()} — "
                f"ближайшее окно {kind.next_window().title}."
            )
        seen = await self.db.raid_window(player.user_id, window.start, kind.code)
        if seen and seen["won"]:
            raise RaidError(
                "Своё в это окно ты уже взял. Следующее — "
                f"{kind.next_window().title}."
            )
        if not player.can_fight():
            raise RaidError(health_warning(player))
        if seen:  # пропуск за это окно уже отдан, ходи сколько хочешь
            return

        ticket = get_potion(kind.pass_code)
        if player.potion_count(kind.pass_code) <= 0 and not buy:
            raise RaidError(
                f"Нужен {ticket.title}: он лежит в лавке клуба, "
                f"раздел «Прочее», за {ticket.price} 💰."
            )
        # Окно занимаем до оплаты и атомарно: два нажатия подряд не спишут
        # два пропуска, а не срослось — отпустим обратно
        if not await self.db.start_raid_window(
            player.user_id, window.start, kind.code
        ):
            return
        try:
            if player.potion_count(kind.pass_code) <= 0:
                await buy_potion(self.db, player, kind.pass_code)
            await self.db.take_potion(player.user_id, kind.pass_code)
        except PotionError as error:
            await self.db.drop_raid_window(player.user_id, window.start, kind.code)
            raise RaidError(str(error)) from error
        player.potions[kind.pass_code] = max(
            0, player.potion_count(kind.pass_code) - 1
        )

    async def open_raid(
        self,
        chat_id: int | None,
        thread_id: int | None,
        opener: Player,
        kind: RaidKind = CELLAR_RAID,
        chat_title: str = "",
        buy: bool = False,
    ) -> RaidLobby:
        if chat_id is not None and (chat_id, thread_id) in self._by_chat:
            raise RaidError("В этой ветке уже собирают рейд или дерутся.")
        if self._busy.get(opener.user_id):
            raise RaidError("Ты уже записан в рейд.")
        kind = self._tuned(kind)
        await self._admit(opener, buy, kind)

        record_id = await self.db.open_raid_record(
            chat_id=chat_id,
            thread_id=thread_id,
            opener_id=opener.user_id,
            boss=kind.leader.code,
            size=kind.max_party,
        )
        lobby = RaidLobby(
            id=next(self._ids),
            chat_id=chat_id,
            thread_id=thread_id,
            kind=kind,
            size=kind.max_party,
            opener_id=opener.user_id,
            chat_title=chat_title,
            record_id=record_id,
        )
        lobby.members[opener.user_id] = opener.nickname
        lobby.levels[opener.user_id] = opener.level
        self._lobbies[lobby.id] = lobby
        if lobby.key is not None:
            self._by_chat[lobby.key] = lobby.id
        self._busy[opener.user_id] = "lobby"
        self._results.pop(opener.user_id, None)

        message = await self.voice.send(
            chat_id,
            thread_id,
            raid_lobby_card(lobby, self.config.raid_lobby_timeout),
            reply_markup=raid_lobby_keyboard(lobby),
        )
        lobby.message_id = message.message_id if message else None
        # Отряд собирают в мини-аппе, и в чате об этом иначе не узнать:
        # объявление зовёт тех, кто сейчас не в приложении
        lobby.pins = await self.board.announce(
            RAID,
            board_raid(lobby, self.config.raid_lobby_timeout),
            skip=(chat_id, thread_id),
            disable_web_page_preview=True,
        )
        lobby.task = asyncio.create_task(self._lobby_timer(lobby))
        return lobby

    async def join(
        self, lobby_id: int, player: Player, buy: bool = False
    ) -> RaidLobby:
        lobby = self._lobbies.get(lobby_id)
        if lobby is None:
            raise RaidError("Этот сбор уже закрыт.")
        if player.user_id in lobby.members:
            raise RaidError("Ты уже записан.")
        if self._busy.get(player.user_id):
            raise RaidError("Ты уже записан в другой бой.")
        if lobby.is_full:
            raise RaidError("Мест в отряде больше нет.")
        await self._admit(player, buy, lobby.kind)

        lobby.members[player.user_id] = player.nickname
        lobby.levels[player.user_id] = player.level
        self._busy[player.user_id] = "lobby"
        self._results.pop(player.user_id, None)

        if lobby.is_full:
            await self._start_from_lobby(lobby)
        else:
            await self._refresh_lobby(lobby)
        return lobby

    async def leave(self, lobby_id: int, user_id: int) -> RaidLobby:
        lobby = self._lobbies.get(lobby_id)
        if lobby is None:
            raise RaidError("Этот сбор уже закрыт.")
        if user_id not in lobby.members:
            raise RaidError("Тебя и так нет в отряде.")
        lobby.members.pop(user_id, None)
        lobby.levels.pop(user_id, None)
        self._busy.pop(user_id, None)
        if not lobby.members:
            await self._cancel_lobby(lobby, "Все разошлись — рейд отменён.")
        else:
            await self._refresh_lobby(lobby)
        return lobby

    async def start_now(self, lobby_id: int, user_id: int) -> RaidSession | None:
        """Выйти, не дожидаясь ни полного отряда, ни конца отсчёта.

        Право на это одно у созвавшего: он платил за сбор и он решает, идти
        ли вчетвером. Остальным остаётся ждать или выйти из отряда.
        """
        lobby = self._lobbies.get(lobby_id)
        if lobby is None:
            raise RaidError("Этот сбор уже закрыт.")
        if lobby.opener_id != user_id:
            raise RaidError("Выводит отряд тот, кто его собрал.")
        if not lobby.can_start:
            need = lobby.kind.min_party
            raise RaidError(
                f"Столько не выйдет: нужно хотя бы {need} "
                f"{plural(need, 'боец', 'бойца', 'бойцов')}."
            )
        return await self._start_from_lobby(lobby)

    async def _refresh_lobby(self, lobby: RaidLobby) -> None:
        await self.voice.edit(
            lobby.chat_id,
            lobby.message_id,
            raid_lobby_card(lobby, self.config.raid_lobby_timeout),
            reply_markup=raid_lobby_keyboard(lobby),
            cosmetic=True,
        )

    async def _lobby_timer(self, lobby: RaidLobby) -> None:
        try:
            await asyncio.sleep(self.config.raid_lobby_timeout)
        except asyncio.CancelledError:  # pragma: no cover - обычная отмена
            return
        if lobby.id not in self._lobbies:
            return
        if lobby.can_start:
            await self._start_from_lobby(lobby)
        else:
            await self._cancel_lobby(lobby, "Все разошлись — рейд отменён.")

    async def _cancel_lobby(self, lobby: RaidLobby, why: str) -> None:
        """Сбор не состоялся. Пропуск не возвращаем: окно уже открыто, и
        зайти в подвал снова можно бесплатно до самого его конца."""
        self._forget_lobby(lobby)
        await self.db.drop_raid_record(lobby.record_id)
        await self.voice.edit(lobby.chat_id, lobby.message_id, f"🚫 {why}")

    def _forget_lobby(self, lobby: RaidLobby, started: bool = False) -> None:
        self._lobbies.pop(lobby.id, None)
        if lobby.key is not None and self._by_chat.get(lobby.key) == lobby.id:
            self._by_chat.pop(lobby.key, None)
        for user_id in lobby.members:
            if self._busy.get(user_id) == "lobby":
                self._busy.pop(user_id, None)
        if lobby.task and not lobby.task.done():
            if lobby.task is not asyncio.current_task():
                lobby.task.cancel()
        # Сбор кончился — объявление снимаем с закрепа: звать больше некуда
        if lobby.pins:
            asyncio.create_task(
                self.board.close(lobby.pins, board_raid_over(lobby, started))
            )

    # ---------- бой ----------

    async def _start_from_lobby(self, lobby: RaidLobby) -> RaidSession | None:
        members = dict(lobby.members)
        self._forget_lobby(lobby, started=True)

        players: dict[int, Player] = {}
        for user_id in members:
            player = await self.db.get_player(user_id)
            if player is not None and player.can_fight():
                players[user_id] = player
        if len(players) < lobby.kind.min_party:
            await self.db.drop_raid_record(lobby.record_id)
            await self.voice.edit(
                lobby.chat_id, lobby.message_id, "🚫 Отряд разбежался — рейд отменён."
            )
            return None

        fighters = {
            user_id: Fighter.from_player(player, armed=True)
            for user_id, player in players.items()
        }
        # Банда собирается под отряд, который вышел на самом деле, а не
        # под тот, что записался: кто не дошёл, того и противника нет
        enemies = raid_foes(
            lobby.kind, [fighter.level for fighter in fighters.values()]
        )
        session = RaidSession(
            id=next(self._ids),
            chat_id=lobby.chat_id,
            thread_id=lobby.thread_id,
            kind=lobby.kind,
            enemies=enemies,
            fighters=fighters,
            chat_title=lobby.chat_title,
            record_id=lobby.record_id,
        )
        self._raids[session.id] = session
        if session.key is not None:
            self._by_chat[session.key] = session.id
        for user_id in fighters:
            self._busy[user_id] = "raid"

        await self.voice.edit(
            lobby.chat_id, lobby.message_id, "🔔 Отряд собран — начинаем."
        )
        await self.voice.send(session.chat_id, session.thread_id, raid_intro(session))
        await self._start_wave(session)
        return session

    async def _start_wave(self, session: RaidSession) -> None:
        session.wave += 1
        # Новая волна — новая стойка, и у каждого противника своя: между
        # волнами они перекладывают щит и меняют замах
        session.stances = {
            number: boss_stance(self.rng) for number in session.enemies
        }
        # И наводка: кто остался без цели — на первого из живых
        session.take_aim()
        session.choices = {}
        session.acted = set()
        session.said = []
        session.fallen = []
        session.dropped = []
        session.resting = False
        message = await self.voice.send(
            session.chat_id,
            session.thread_id,
            raid_panel(session, session.kind.turn_seconds),
        )
        session.prompt_message_id = message.message_id if message else None
        session.timer = asyncio.create_task(self._wave_timer(session, session.wave))

    async def _wave_timer(self, session: RaidSession, wave: int) -> None:
        try:
            await asyncio.sleep(session.kind.turn_seconds)
        except asyncio.CancelledError:  # pragma: no cover - обычная отмена
            return
        if self._raids.get(session.id) is not session or session.wave != wave:
            return
        # Время вышло: кто не нажал, тот пропустил удар — но босс своё берёт
        await self.skip_the_rest(session)

    async def skip_the_rest(self, session: RaidSession) -> None:
        """Дожать волну за тех, кто промолчал."""
        async with session.lock:
            if session.finished:
                return
            for user_id in session.waiting_for():
                self._exchange(session, user_id, Action())
                if self._judge(session) is not None:
                    break
            await self._close_wave(session)

    async def handle_choice(
        self, raid_id: int, user_id: int, action: str, zone_value: str, hand: int = 0
    ) -> str:
        """Нажатие бойца. Выбрал и удар, и блок — размен считается сразу."""
        session = self._raids.get(raid_id)
        if session is None:
            raise RaidError("Этот рейд уже закончился.")
        fighter = session.fighters.get(user_id)
        if fighter is None:
            raise RaidError("Ты не в этом рейде. Болей за своих.")
        if not fighter.alive:
            raise RaidError("Тебя уже вынесли — смотри со стороны.")
        if session.resting:
            raise RaidError("Передышка. Босс отдыхает, и ты пока тоже.")
        if user_id in session.acted:
            raise RaidError("В этой волне ты уже отработал. Жди следующую.")
        if action not in {"attack", "block"}:
            raise RaidError("Непонятное действие.")
        try:
            zone = Zone(zone_value)
        except ValueError as error:  # устаревшая кнопка из прошлой версии
            raise RaidError("Эта кнопка уже не работает.") from error

        async with session.lock:
            if user_id in session.acted:  # успел проскочить, пока ждали замок
                raise RaidError("В этой волне ты уже отработал.")
            choice = session.choice_of(user_id)
            if action == "attack":
                choice.attacks[hand] = zone
            else:
                choice.block = block_combo(zone, fighter.block_width)
            if not choice.ready_for(fighter.attacks_per_round):
                await self._repaint(session)
                return self._hint(choice, fighter)

            self._exchange(
                session, user_id, choice.to_action(fighter.attacks_per_round)
            )
            if self._judge(session) is not None or session.wave_over:
                await self._close_wave(session)
            else:
                await self._repaint(session)
        return self._hint(choice, fighter)

    def _exchange(self, session: RaidSession, user_id: int, action: Action) -> None:
        """Один размен: боец против своего противника. Тот бьёт наугад.

        Против кого — решает линия: пока оба стоят, боец весь рейд
        работает одного и того же гопника. Добил — на его место из банды
        выйдет следующий, и это уже другой размен.

        Движку отдаём номер волны, а не сквозной номер размена. Номер раунда
        он берёт для усталости: чем дольше идёт бой, тем сильнее бьют оба.
        Волна — это по разу на каждого, то есть ровно один раунд для всех, а
        сквозной счётчик рос бы вдесятеро быстрее в отряде из десяти человек:
        к пятой волне обычный удар выбивал бы под сотню.
        """
        fighter = session.fighters[user_id]
        session.acted.add(user_id)
        number = session.aim.get(user_id)
        enemy = session.enemies.get(number) if number is not None else None
        if enemy is None or not enemy.alive:
            # Противника не осталось: банда кончилась, а волна ещё идёт.
            # Бить некого, и удар в воздух не размен — ни в счётчик, ни в
            # журнал он не идёт, иначе в разборе боя завелись бы пустые ходы
            return
        session.turn_number += 1
        result = resolve_round(
            fighter,
            action,
            enemy,
            boss_action(enemy, self.rng, session.temper_of(number)),
            session.wave,
            self.rng,
            # Усталость растянута на длину рейда, а не дуэли
            limit=FATIGUE_WAVES,
        )
        # Слова судьи собираются один раз: и в ветку, и в мини-апп, и в лог
        said = strike_lines(result, {user_id: fighter, number: enemy}, self.rng)
        # Вторая половина приёмов десятой ступени. Заготовки союзникам
        # ложатся на весь отряд, включая тех, кто в этой волне не ходил, а
        # «Массовый удар» достаёт всю банду — в казино банда из одного
        # человека, и там он по-прежнему молчит
        everyone = self._cast(session)
        said.extend(
            echo_lines(
                group_aftermath(result.strikes, everyone, self._sides(session)),
                everyone,
            )
        )
        session.said.extend(said)
        session.rounds.append(turn_payload(result, said))
        session.hurt.update(result.injuries)
        session.strikes += 1
        if not fighter.alive:
            session.fallen.append(user_id)
        if not enemy.alive:
            session.dropped.append(number)
        # Своё отработал — дальше по кругу. Следующего он увидит сразу,
        # не дожидаясь волны: его очередь уже сдвинулась
        session.move_on(user_id)

    def _cast(self, session: RaidSession) -> dict[int, Fighter]:
        """Все, кто на поле: отряд и банда. У каждого свой номер."""
        return {**session.fighters, **session.enemies}

    def _sides(self, session: RaidSession) -> dict[int, int]:
        """Отряд заодно, банда заодно: гопники друг друга не бьют."""
        sides = dict.fromkeys(session.fighters, SQUAD_SIDE)
        for number in session.enemies:
            sides[number] = BOSS_SIDE
        return sides

    async def use_ability(self, raid_id: int, user_id: int, code: str) -> str:
        """Нажать приём в рейде. Жмут до выбора удара и блока."""
        session = self._raids.get(raid_id)
        if session is None:
            raise RaidError("Этого рейда уже нет.")
        if user_id not in session.fighters:
            raise RaidError("Ты не в этом отряде.")
        fighter = session.fighters[user_id]
        if code not in fighter.loadout:
            raise RaidError("Этот приём не выучен.")
        # Причину отказа называет движок: он один знает, что именно не
        # сложилось — норма хода, уже лежащая заготовка или кошелёк. Своя
        # проверка здесь когда-то всё сводила к энергии, и боец с полной
        # шкалой читал «не хватает энергии»
        try:
            ability = fighter.use(code).ability
        except ValueError as error:
            raise RaidError(str(error)) from error
        if not ability.heal:
            return f"{ability.icon} {ability.title} наготове."

        gained = fighter.heal_by(ability.heal)
        fighter.charges.pop()
        allies = [
            one for other_id, one in session.fighters.items() if other_id != user_id
        ]
        healed = spread_heal(ability, fighter, allies)
        line = f"{ability.icon} {ability.title}: +{gained} ❤️"
        if healed:
            line += f", и ещё {len(healed)} бойцам отряда"
        return line

    def _judge(self, session: RaidSession) -> RaidOutcome | None:
        return judge_raid(session.enemies.values(), session.fighters)

    def _hint(self, choice: Choice, fighter: Fighter) -> str:
        icons = fighter.weapon_icons
        lines = [
            f"{icons[hand] if hand < len(icons) else '👊'} "
            f"{choice.attacks[hand].title if hand in choice.attacks else '—'}"
            for hand in range(fighter.attacks_per_round)
        ]
        lines.append(f"🛡 {block_title(choice.block) if choice.block else '—'}")
        ready = choice.ready_for(fighter.attacks_per_round)
        lines.append("Ждём остальных." if ready else "Осталось выбрать ещё.")
        return "\n".join(lines)

    async def _repaint(self, session: RaidSession) -> None:
        """Обновить панель волны. Правка косметическая: не дойдёт — не беда."""
        await self.voice.edit(
            session.chat_id,
            session.prompt_message_id,
            raid_panel(session, session.kind.turn_seconds),
            cosmetic=True,
        )

    async def _close_wave(self, session: RaidSession) -> None:
        """Волна отработана: рассказать, что вышло, и позвать следующую."""
        self._cancel_timer(session)
        await self._close_panel(session, self._wave_report(session))

        # Рейд идёт, пока кто-нибудь не упадёт: босс или отряд. Счётчика
        # волн у него нет — раньше на тридцатой судья закрывал бой
        # поражением, и отряд, у которого никто даже не был ранен, уходил
        # ни с чем. Доводит бой до конца усталость: она растёт с каждой
        # волной и дальше тридцатой, а с ней растёт и урон
        outcome = self._judge(session)
        if outcome is not None:
            await self._finish(session, outcome)
        elif session.strikes >= self.config.raid_strikes_per_break:
            await self._take_a_break(session)
        else:
            await self._start_wave(session)

    def _wave_report(self, session: RaidSession) -> str:
        lines = [f"<b>⚔️ Волна {session.wave}</b>", ""]
        lines += session.said or ["Все промолчали — били их одних."]
        if session.dropped:
            names = ", ".join(
                f"<b>{esc(session.enemies[number].name)}</b>"
                for number in session.dropped
            )
            left = len(session.standing)
            tail = (
                f" В банде остаётся {left} "
                f"{plural(left, 'человек', 'человека', 'человек')}."
                if left
                else ""
            )
            lines += ["", f"🩸 Готов: {names}.{tail}"]
        if session.fallen:
            names = ", ".join(
                f"<b>{esc(session.fighters[uid].name)}</b>" for uid in session.fallen
            )
            lines += ["", f"💀 Больше не встают: {names}"]
        return "\n".join(lines)

    async def _take_a_break(self, session: RaidSession) -> None:
        """Передышка после шести ударов: отряд переводит дух."""
        session.strikes = 0
        rest = self.config.raid_break
        session.resting = rest > 0
        await self.voice.send(
            session.chat_id, session.thread_id, raid_break(session, rest)
        )
        if rest <= 0:
            await self._start_wave(session)
            return
        session.timer = asyncio.create_task(self._break_timer(session, rest))

    async def _break_timer(self, session: RaidSession, seconds: int) -> None:
        wave = session.wave
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:  # pragma: no cover - обычная отмена
            return
        if self._raids.get(session.id) is not session or session.wave != wave:
            return
        await self._start_wave(session)

    async def _close_panel(self, session: RaidSession, text: str) -> None:
        """Погасить панель волны, оставив на её месте рассказ."""
        await self.voice.edit(
            session.chat_id,
            session.prompt_message_id,
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        session.prompt_message_id = None

    async def _finish(self, session: RaidSession, outcome: RaidOutcome) -> None:
        self._cancel_timer(session)
        if session.prompt_message_id is not None:
            await self._close_panel(session, "🔒 Рейд окончен.")
        self._forget_raid(session)

        prizes = await self._apply_results(session, outcome)
        text = raid_result(session, outcome, prizes, session.shares)
        await self.voice.send(session.chat_id, session.thread_id, text)
        session.finished = True
        session.summary = [plain(line) for line in text.split("\n")]
        for user_id in session.fighters:
            self._results[user_id] = session
        await self.db.close_raid_record(
            session.record_id,
            outcome=outcome.end.value,
            waves=session.wave,
            boss_level=session.enemies[BOSS_ID].level,
            members=[
                (
                    user_id,
                    fighter.damage_dealt,
                    fighter.alive,
                    # В журнале у приза одна клетка, а упасть может и
                    # вещь, и склянка. Пишем первое: вещь идёт раньше
                    # склянки, и в истории останется то, что заметнее
                    next(iter(prizes.get(user_id) or ()), None),
                )
                for user_id, fighter in session.fighters.items()
            ],
        )

    async def _apply_results(
        self, session: RaidSession, outcome: RaidOutcome
    ) -> dict[int, list[str]]:
        """Кошель отряду и добыча сверх него: что именно — решает рейд.

        Броска на добычу три, и один из них общий: склянка с банды падает
        либо всем сразу, либо никому. Поэтому он кидается здесь, до
        обхода отряда, а не внутри него — иначе «всем сразу» означало бы
        «каждому по своему броску», то есть совсем другое правило.

        Здоровье и износ вещей записываются в любом случае: рейд не
        разбирает, победил ты или нет.
        """
        prizes: dict[int, list[str]] = {}
        spoils = session.kind.spoils
        # Один бросок на весь отряд: пьют все или никто
        shared = spoils.potion_for(self.rng) if outcome.won else None
        party = list(session.fighters)
        # Платим всем, кто вышел, а не только выжившим: упавший тоже
        # дрался, и его урон валил банду
        shares = session.kind.shares(len(party)) if outcome.won else []
        session.shares = dict(zip(party, shares))
        top = set(outcome.top) if outcome.won else set()
        window = self.window_now(kind=session.kind)
        for user_id, fighter in session.fighters.items():
            player = await self.db.get_player(user_id)
            if player is None:  # pragma: no cover - персонажа удалили по ходу
                continue
            # Рейд идёт по своему счёту: гопники и босс — не люди, и
            # валят их толпой. В победах и поражениях бойца остаются
            # только те, кого он бил сам
            # Травма — до всего прочего: снятая ею экипировка
            # меняет и запас здоровья, и то, что снашивается износом
            injury = session.hurt.get(user_id)
            if injury is not None:
                await hurt_player(self.db, player, injury)
            player.raid_fights += 1
            if outcome.won:
                player.raid_wins += 1
                player.credits += session.shares.get(user_id, 0)
            # Ничья в подвале бывает: отряд и босс легли в один ход. Она
            # идёт ничьёй и по износу — None вместо False, как в рейтинге
            ruined = await wear_after_fight(
                self.db, player, None if outcome.draw else outcome.won, self.rng
            )
            if ruined:  # pragma: no cover - износ считается своим тестом
                logger.info("Рейд износил вещи бойца %s: %s", user_id, len(ruined))
            if player.birthplace is None and session.chat_title:
                player.birthplace = session.chat_title
            player.set_hp(fighter.hp)
            await self.db.save_player(player)
            mine: list[str] = []
            if user_id in top:
                # Лучшему по урону — вещь с полки рейда, а если полки у
                # него нет, то склянка: у подвала добыча такая
                thing = spoils.item_for(self.rng)
                if thing:
                    await self.db.add_gear(user_id, thing)
                    mine.append(thing)
                elixir = spoils.elixir_for(self.rng)
                if elixir:
                    player.potions[elixir] = await self.db.add_potion(
                        user_id, elixir
                    )
                    mine.append(elixir)
            if shared:
                player.potions[shared] = await self.db.add_potion(user_id, shared)
                mine.append(shared)
            if mine:
                prizes[user_id] = mine
            # Победа закрывает окно: второй раз в этот промежуток не пустят
            if outcome.won and window is not None:
                await self.db.close_raid_window(
                    user_id, window.start, session.kind.code
                )
        return prizes

    def _cancel_timer(self, session: RaidSession) -> None:
        timer = session.timer
        session.timer = None
        if timer and not timer.done() and timer is not asyncio.current_task():
            timer.cancel()

    def _forget_raid(self, session: RaidSession) -> None:
        self._raids.pop(session.id, None)
        if session.key is not None and self._by_chat.get(session.key) == session.id:
            self._by_chat.pop(session.key, None)
        for user_id in session.fighters:
            if self._busy.get(user_id) == "raid":
                self._busy.pop(user_id, None)

    # ---------- состояние ----------

    def lobby_of_user(self, user_id: int) -> RaidLobby | None:
        for lobby in self._lobbies.values():
            if user_id in lobby.members:
                return lobby
        return None

    def open_lobbies(self) -> list[RaidLobby]:
        return sorted(self._lobbies.values(), key=lambda row: row.id, reverse=True)

    def raid_of_user(self, user_id: int) -> RaidSession | None:
        for session in self._raids.values():
            if user_id in session.fighters:
                return session
        return None

    def result_of_user(self, user_id: int) -> RaidSession | None:
        return self._results.get(user_id)

    def forget_result(self, user_id: int) -> None:
        self._results.pop(user_id, None)

    def get_lobby(self, lobby_id: int) -> RaidLobby | None:
        return self._lobbies.get(lobby_id)

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._busy

    async def shutdown(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._watch
            self._watch = None
        tasks = [lobby.task for lobby in self._lobbies.values() if lobby.task]
        tasks += [raid.timer for raid in self._raids.values() if raid.timer]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._lobbies.clear()
        self._raids.clear()
        self._by_chat.clear()
        self._busy.clear()
        self._results.clear()


__all__ = ["RaidError", "RaidLobby", "RaidService", "RaidSession"]
