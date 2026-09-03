"""Проведение дуэлей в ветке группы: вызовы, раунды, таймеры, итоги."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import math
import random
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from bot.config import Config
from bot.database import Database
from bot.game.classes import Zone, block_combo, block_title
from bot.game.equipment import BARE_HANDS_ICON
from bot.game.modes import FightMode
from bot.game.combat import (
    MATCH_ROUNDS,
    MAX_MISSED_TURNS,
    TURNS_PER_ROUND,
    Action,
    Fighter,
    RoundResult,
    boxing_round,
    resolve_round,
    round_is_over,
    turn_in_round,
)
from bot.game.economy import (
    DRAW_EXP_SHARE,
    LOSS_EXP_SHARE,
    REPEAT_WINDOW_HOURS,
    apply_share,
    consolation_exp,
    pro_exp,
    rating_delta,
    repeat_share,
    win_exp,
)
from bot.game.narrator import (
    corner_break,
    duel_intro,
    fight_board,
    mention,
    player_link,
    standoff_card,
    esc,
    finish_report,
    health_warning,
    ready_mark,
    rewards_report,
    round_report,
)
from bot.inventory_service import wear_after_fight
from bot.keyboards import challenge_keyboard, fight_keyboard, standoff_keyboard
from bot.messaging import Announcer
from bot.models import Player, ProgressReport

logger = logging.getLogger(__name__)

class DuelError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


ChatKey = tuple[int, int | None]


@dataclass
class Challenge:
    id: int
    # Чата может не быть: вызов, брошенный в мини-аппе, не привязан к ветке.
    # Тогда судья молчит, а всё остальное идёт своим чередом.
    chat_id: int | None
    thread_id: int | None
    challenger: Player
    target_id: int | None = None
    message_id: int | None = None
    task: asyncio.Task | None = None
    chat_title: str = ""
    mode: FightMode = FightMode.FIST

    @property
    def key(self) -> ChatKey | None:
        return None if self.chat_id is None else (self.chat_id, self.thread_id)


@dataclass
class Choice:
    """Незавершённый выбор бойца на ход: один удар и один блок."""

    attack: Zone | None = None
    block: tuple[Zone, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.attack is not None and bool(self.block)

    def to_action(self) -> Action:
        """Что боец успел нажать, то и уходит в ход."""
        return Action(attack=self.attack, block=self.block)

    @property
    def is_empty(self) -> bool:
        return self.attack is None and not self.block

    def describe(self, fighter: Fighter) -> str:
        zone = self.attack.title if self.attack else "—"
        block = block_title(self.block) if self.block else "—"
        return f"{fighter.weapon_icon} {zone}\n🛡 {block}"


@dataclass
class DuelSession:
    id: int
    chat_id: int | None
    thread_id: int | None
    fighters: dict[int, Fighter]
    order: tuple[int, int]
    chat_title: str = ""  # название группы — станет местом рождения новичка
    mode: FightMode = FightMode.FIST
    round_number: int = 0
    choices: dict[int, Choice] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)
    prompt_message_id: int | None = None
    # Кого позвать, когда бой кончится: турнир так узнаёт победителя пары
    on_finish: object | None = None
    standoff_message_id: int | None = None
    started: bool = False  # гонг прозвучал
    timer: asyncio.Task | None = None
    resolving: bool = False
    # Бойцы разведены по углам и ждут гонга: панели в этот момент нет
    resting: bool = False
    # Сколько бойцы отдыхают между раундами. У боя в мини-аппе — нисколько:
    # там некого ждать, обе стороны уже смотрят на экран.
    round_break: int = 0
    # Ходы боя как их посчитал движок. В ветке они уходят словами судьи, а
    # мини-апп рисует по ним разбор сам — и тем же списком потом ляжет лог
    # в историю боёв.
    rounds: list[RoundResult] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def key(self) -> ChatKey | None:
        """Ветка, которую занял бой. None — бой идёт в мини-аппе."""
        return None if self.chat_id is None else (self.chat_id, self.thread_id)

    @property
    def in_app(self) -> bool:
        return self.chat_id is None

    def opponent_of(self, user_id: int) -> Fighter:
        other_id = next(uid for uid in self.order if uid != user_id)
        return self.fighters[other_id]

    def choice_of(self, user_id: int) -> Choice:
        return self.choices.setdefault(user_id, Choice())

    @property
    def challenger_id(self) -> int:
        """Тот, кто бросил вызов: ему и решать, выходить ли на ринг."""
        return self.order[0]

    def is_ready(self, user_id: int) -> bool:
        return self.choice_of(user_id).is_ready

    @property
    def panel(self) -> str:
        """Значок удара на кнопках — один на двоих.

        Оружие у бойцов может быть разное, а панель одна: если значки не
        сошлись, показываем кулак, а бьёт каждый тем, что у него в руке.
        """
        sides = [self.fighters[user_id] for user_id in self.order]
        icon = sides[0].weapon_icon
        return icon if all(side.weapon_icon == icon for side in sides) else BARE_HANDS_ICON


class DuelService:
    """Держит активные вызовы и бои, шлёт судейские сообщения в ветку."""

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
        self.rng = rng or random.Random()
        self._ids = itertools.count(1)
        self._challenges: dict[int, Challenge] = {}
        self._duels: dict[int, DuelSession] = {}
        self._duel_by_chat: dict[ChatKey, int] = {}
        self._busy: dict[int, str] = {}  # user_id -> "challenge" | "duel"

    # ---------- вызовы ----------

    async def open_challenge(
        self,
        chat_id: int | None,
        thread_id: int | None,
        challenger: Player,
        target: Player | None = None,
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
    ) -> Challenge:
        if self._busy.get(challenger.user_id):
            raise DuelError("Ты уже в бою или у тебя висит незакрытый вызов.")
        if not challenger.can_fight():
            raise DuelError(health_warning(challenger))
        if target is not None:
            if target.user_id == challenger.user_id:
                raise DuelError("С самим собой драться — это уже другой фильм.")
            if self._busy.get(target.user_id) == "duel":
                raise DuelError(f"{target.nickname} сейчас на ринге. Дождись конца боя.")
            if not target.can_fight():
                raise DuelError(health_warning(target, is_self=False))
        if chat_id is not None and (chat_id, thread_id) in self._duel_by_chat:
            raise DuelError("В этой ветке уже идёт бой. Один ринг — одна пара.")

        challenge = Challenge(
            id=next(self._ids),
            chat_id=chat_id,
            thread_id=thread_id,
            challenger=challenger,
            target_id=target.user_id if target else None,
            chat_title=chat_title,
            mode=mode,
        )
        # Каким боем вызвали, таким и объявляем: /fight — это бой с оружием,
        # и написать про кулачный значит соврать ещё до первого удара.
        if target is None:
            text = (
                f"{mode.emoji} <b>{player_link(challenger)}</b> "
                f"({challenger.fclass.label}, {challenger.level} ур.) "
                f"вызывает любого желающего на {mode.title}.\n\n"
                "Кто примет вызов?"
            )
        else:
            text = (
                f"{mode.emoji} <b>{player_link(challenger)}</b> "
                f"({challenger.fclass.label}, {challenger.level} ур.) "
                f"вызывает <b>{player_link(target)}</b> "
                f"({target.fclass.label}, {target.level} ур.) на {mode.title}.\n\n"
                "Слово за вызванным."
            )
        message = await self._send(
            chat_id, thread_id, text, reply_markup=challenge_keyboard(challenge.id)
        )
        challenge.message_id = message.message_id if message else None
        challenge.task = asyncio.create_task(self._expire_challenge(challenge))
        self._challenges[challenge.id] = challenge
        self._busy[challenger.user_id] = "challenge"
        return challenge

    async def _expire_challenge(self, challenge: Challenge) -> None:
        try:
            await asyncio.sleep(self.config.challenge_timeout)
        except asyncio.CancelledError:
            return
        if self._challenges.get(challenge.id) is not challenge:
            return
        self._drop_challenge(challenge)
        await self._edit(
            challenge.chat_id,
            challenge.message_id,
            f"🥱 Вызов от <b>{esc(challenge.challenger.nickname)}</b> "
            "остался без ответа. Ринг свободен.",
        )

    def _drop_challenge(self, challenge: Challenge) -> None:
        self._challenges.pop(challenge.id, None)
        if self._busy.get(challenge.challenger.user_id) == "challenge":
            self._busy.pop(challenge.challenger.user_id, None)
        if challenge.task and not challenge.task.done():
            challenge.task.cancel()

    async def _withdraw_challenges_of(self, user_id: int) -> None:
        for challenge in list(self._challenges.values()):
            if challenge.challenger.user_id != user_id:
                continue
            self._drop_challenge(challenge)
            await self._edit(
                challenge.chat_id,
                challenge.message_id,
                f"↩️ Вызов от <b>{esc(challenge.challenger.nickname)}</b> снят: "
                "боец ушёл драться в другую пару.",
            )

    async def cancel_challenge(self, challenge_id: int, user_id: int) -> None:
        challenge = self._challenges.get(challenge_id)
        if challenge is None:
            raise DuelError("Этот вызов уже неактуален.")
        if challenge.challenger.user_id != user_id:
            raise DuelError("Отозвать вызов может только тот, кто его бросил.")
        self._drop_challenge(challenge)
        await self._edit(
            challenge.chat_id,
            challenge.message_id,
            f"🚪 <b>{esc(challenge.challenger.nickname)}</b> передумал(а) драться.",
        )

    async def accept_challenge(self, challenge_id: int, opponent: Player) -> DuelSession:
        challenge = self._challenges.get(challenge_id)
        if challenge is None:
            raise DuelError("Этот вызов уже неактуален.")
        if opponent.user_id == challenge.challenger.user_id:
            raise DuelError("Свой собственный вызов принять нельзя.")
        if challenge.target_id is not None and challenge.target_id != opponent.user_id:
            raise DuelError("Этот вызов адресован другому бойцу.")
        if self._busy.get(opponent.user_id) == "duel":
            raise DuelError("Ты уже на ринге в другом бою.")
        if not opponent.can_fight():
            raise DuelError(health_warning(opponent))
        if self._busy.get(opponent.user_id) == "challenge":
            # у принимающего висел свой вызов — снимаем его, драка важнее
            await self._withdraw_challenges_of(opponent.user_id)
        if challenge.key is not None and challenge.key in self._duel_by_chat:
            raise DuelError("В этой ветке уже идёт бой.")

        self._drop_challenge(challenge)
        challenger = await self.db.get_player(challenge.challenger.user_id)
        if challenger is None:  # pragma: no cover - персонажа удалили посреди вызова
            raise DuelError("Соперник куда-то пропал вместе со своим персонажем.")
        if not challenger.can_fight():
            # Подстраховка: здоровье само по себе только растёт, но вызов мог
            # провисеть дольше, чем живут наши записи о том, кто чем занят.
            raise DuelError(health_warning(challenger, is_self=False))

        await self._edit(
            challenge.chat_id,
            challenge.message_id,
            f"🥊 <b>{esc(opponent.nickname)}</b> принимает вызов "
            f"<b>{esc(challenger.nickname)}</b>. Ринг занят!",
        )
        if challenge.chat_id is None:
            # Вызов из мини-аппа: стойки нет. Она нужна, чтобы вызвавший
            # успел посмотреть на соперника в ветке; здесь он и так смотрит
            # на экран, и лишний экран между «принял» и гонгом только мешает.
            return await self.start_duel(
                None,
                None,
                challenger,
                opponent,
                challenge.chat_title,
                challenge.mode,
            )
        return await self.open_standoff(
            challenge.chat_id,
            challenge.thread_id,
            challenger,
            opponent,
            challenge.chat_title,
            challenge.mode,
        )

    # ---------- бой ----------

    async def open_standoff(
        self,
        chat_id: int,
        thread_id: int | None,
        first: Player,
        second: Player,
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
    ) -> DuelSession:
        """Свести бойцов лицом к лицу и дать вызвавшему посмотреть на соперника."""
        session = self._make_session(chat_id, thread_id, first, second, chat_title, mode)
        message = await self._send(
            chat_id,
            thread_id,
            standoff_card(first, second, mode=mode),
            reply_markup=standoff_keyboard(session.id),
        )
        session.standoff_message_id = message.message_id if message else None
        session.timer = asyncio.create_task(self._standoff_timer(session))
        return session

    async def confirm_duel(self, duel_id: int, user_id: int) -> DuelSession:
        """Вызвавший посмотрел на соперника и выходит на ринг."""
        session = self._pending(duel_id)
        if user_id != session.challenger_id:
            raise DuelError("Решает тот, кто бросил вызов. Жди.")
        self._cancel_timer(session)
        session.started = True
        await self._repaint_standoff(session, "✅ Бойцы сошлись.")
        await self._send(
            session.chat_id,
            session.thread_id,
            duel_intro(
                *(session.fighters[uid] for uid in session.order), mode=session.mode
            ),
        )
        await self._start_round(session)
        return session

    async def decline_duel(self, duel_id: int, user_id: int) -> None:
        """Разойтись без боя: соперник оказался не по зубам."""
        session = self._pending(duel_id)
        if user_id not in session.fighters:
            raise DuelError("Это не твой бой.")
        self._cancel_timer(session)
        who = session.fighters[user_id].name
        self._forget(session)
        await self._repaint_standoff(
            session, f"🚪 <b>{esc(who)}</b> отказывается от боя. Ринг свободен."
        )

    def _pending(self, duel_id: int) -> DuelSession:
        session = self._duels.get(duel_id)
        if session is None:
            raise DuelError("Этот бой уже неактуален.")
        if session.started:
            raise DuelError("Бой уже идёт.")
        return session

    def _standoff_players(self, session: DuelSession) -> tuple[Player, Player]:
        return tuple(session.players[uid] for uid in session.order)

    async def _repaint_standoff(self, session: DuelSession, decision: str) -> None:
        """Перерисовать карточку стойки — всегда в режиме этого боя.

        Карточка обновляется из трёх мест, и режим у неё в умолчании кулачный:
        забыть его хоть раз значит показать вооружённых бойцов голыми — с
        уроном без оружия и подписью «кулачный бой».
        """
        await self._edit(
            session.chat_id,
            session.standoff_message_id,
            standoff_card(
                *self._standoff_players(session),
                decision=decision,
                mode=session.mode,
            ),
        )

    async def _standoff_timer(self, session: DuelSession) -> None:
        try:
            await asyncio.sleep(self.config.challenge_timeout)
        except asyncio.CancelledError:
            return
        if session.started or session.id not in self._duels:
            return
        self._forget(session)
        await self._repaint_standoff(
            session, "🥱 Никто не вышел на ринг. Бой не состоялся."
        )

    def _make_session(
        self,
        chat_id: int | None,
        thread_id: int | None,
        first: Player,
        second: Player,
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
    ) -> DuelSession:
        if chat_id is not None and (chat_id, thread_id) in self._duel_by_chat:
            raise DuelError("В этой ветке уже идёт бой.")
        for player in (first, second):
            if not player.can_fight():
                raise DuelError(health_warning(player, is_self=False))

        fighter_a = Fighter.from_player(first, armed=mode.armed)
        fighter_b = Fighter.from_player(second, armed=mode.armed)
        session = DuelSession(
            id=next(self._ids),
            chat_id=chat_id,
            thread_id=thread_id,
            fighters={fighter_a.user_id: fighter_a, fighter_b.user_id: fighter_b},
            order=(fighter_a.user_id, fighter_b.user_id),
            chat_title=chat_title,
            mode=mode,
            players={first.user_id: first, second.user_id: second},
            # В ветке бойцы расходятся по углам, в мини-аппе — нет
            round_break=0 if chat_id is None else self.config.round_break,
        )
        self._duels[session.id] = session
        if session.key is not None:
            self._duel_by_chat[session.key] = session.id
        self._busy[fighter_a.user_id] = "duel"
        self._busy[fighter_b.user_id] = "duel"
        return session

    def _forget(self, session: DuelSession) -> None:
        self._duels.pop(session.id, None)
        if session.key is not None:
            self._duel_by_chat.pop(session.key, None)
        for user_id in session.order:
            self._busy.pop(user_id, None)

    async def start_duel(
        self,
        chat_id: int | None,
        thread_id: int | None,
        first: Player,
        second: Player,
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
        on_finish=None,
    ) -> DuelSession:
        """Свести бойцов и сразу дать гонг — без стойки."""
        session = self._make_session(chat_id, thread_id, first, second, chat_title, mode)
        session.started = True
        session.on_finish = on_finish
        await self._send(
            chat_id,
            thread_id,
            duel_intro(*(session.fighters[uid] for uid in session.order), mode=mode),
        )
        await self._start_round(session)
        return session

    async def _start_round(self, session: DuelSession) -> None:
        session.round_number += 1
        session.choices = {}
        session.resolving = False
        session.resting = False
        message = await self._send(
            session.chat_id,
            session.thread_id,
            self._prompt_text(session),
            reply_markup=fight_keyboard(session.id, session.panel),
        )
        session.prompt_message_id = message.message_id if message else None
        session.timer = asyncio.create_task(
            self._round_timer(session, session.round_number)
        )

    def _prompt_text(self, session: DuelSession) -> str:
        first, second = (session.fighters[uid] for uid in session.order)
        lines = [
            f"<b>🔔 Раунд {boxing_round(session.round_number)}"
            f", удар {turn_in_round(session.round_number)}"
            f" из {TURNS_PER_ROUND}. Бойцы, выбирайте.</b>",
            "",
        ]
        board = fight_board(
            [(first, second)], lambda side: ready_mark(session.is_ready(side.user_id))
        )
        lines.append("<pre>" + "\n".join(board) + "</pre>")
        skipping = [side for side in (first, second) if side.missed_turns]
        if skipping:
            lines.append("")
            for side in skipping:
                left = MAX_MISSED_TURNS - side.missed_turns
                lines.append(
                    f"⚠️ {mention(side)}: пропусков подряд {side.missed_turns}, "
                    f"осталось {left}"
                )
        lines += [
            "",
            f"⏱️ {self.config.turn_timeout} сек. Выберите удар и блок.",
        ]
        return "\n".join(lines)

    async def _round_timer(self, session: DuelSession, round_number: int) -> None:
        try:
            await asyncio.sleep(self.config.turn_timeout)
        except asyncio.CancelledError:
            return
        async with session.lock:
            if session.round_number != round_number or session.resolving:
                return
            if session.id not in self._duels:
                return
            session.resolving = True
        await self._resolve(session)

    async def handle_choice(
        self, duel_id: int, user_id: int, action: str, zone_value: str, slot: int = 0
    ) -> str:
        # slot остался в кнопках прошлой версии: второго оружия больше нет,
        # и номер удара ни на что не влияет
        """Обработать нажатие бойца. Возвращает текст для приватного ответа."""
        session = self._duels.get(duel_id)
        if session is None:
            raise DuelError("Этот бой уже закончился.")
        if user_id not in session.fighters:
            raise DuelError("Ты не участвуешь в этом бою. Болей за своих.")
        if not session.started:
            raise DuelError("Гонга ещё не было.")

        async with session.lock:
            if session.resolving:
                raise DuelError("Раунд уже считается, поздно.")
            fighter = session.fighters[user_id]
            choice = session.choice_of(user_id)
            was_ready = choice.is_ready
            if action not in {"attack", "block"}:
                raise DuelError("Непонятное действие.")
            try:
                zone = Zone(zone_value)
            except ValueError as error:  # устаревшая кнопка из прошлой версии
                raise DuelError("Эта кнопка уже не работает.") from error

            if action == "attack":
                choice.attack = zone
            else:
                choice.block = block_combo(zone, fighter.block_width)

            now_ready = choice.is_ready
            both_ready = all(session.is_ready(uid) for uid in session.order)
            if both_ready:
                session.resolving = True

        if both_ready:
            await self._resolve(session)
        elif was_ready != now_ready:
            await self._repaint_prompt(session)

        return f"{choice.describe(fighter)}{self._hint(choice, fighter)}"

    def _hint(self, choice: Choice, fighter: Fighter) -> str:
        if choice.attack is None:
            return "\nОсталось выбрать удар."
        if not choice.block:
            return "\nОсталось выбрать блок."
        return "\nГотов. Ждём соперника."

    async def _repaint_prompt(self, session: DuelSession) -> None:
        """Обновить панель: изменилась готовность бойцов.

        Правка косметическая: если Telegram упрётся в лимит, её просто
        не будет, а бой поедет дальше.
        """
        await self._edit(
            session.chat_id,
            session.prompt_message_id,
            self._prompt_text(session),
            reply_markup=fight_keyboard(session.id, session.panel),
            cosmetic=True,
        )

    async def _resolve(self, session: DuelSession) -> None:
        self._cancel_timer(session)
        first_id, second_id = session.order
        first, second = session.fighters[first_id], session.fighters[second_id]
        actions = {
            user_id: session.choices.get(user_id, Choice()).to_action()
            for user_id in session.order
        }

        result = resolve_round(
            first,
            actions[first_id],
            second,
            actions[second_id],
            session.round_number,
            self.rng,
        )

        session.rounds.append(result)
        # Итог раунда встаёт на место его же панели: так за раунд уходит
        # два обращения к чату вместо четырёх, и бой не упирается в лимит
        await self._close_panel(
            session, round_report(result, session.fighters, self.rng)
        )

        if result.finished:
            await self._finish(session, result)
        elif round_is_over(session.round_number):
            await self._call_a_break(session)
        else:
            await self._start_round(session)

    async def _call_a_break(self, session: DuelSession) -> None:
        """Раунд отбоксирован — судья разводит бойцов по углам.

        Минута отдыха здесь не только для красоты: за неё успевает
        освободиться минутный запас обращений к чату, и следующий раунд
        начинается без пауз посреди боя.
        """
        finished = boxing_round(session.round_number)
        rest = session.round_break
        session.resting = rest > 0
        await self._send(
            session.chat_id,
            session.thread_id,
            corner_break(
                *(session.fighters[uid] for uid in session.order),
                round_number=finished,
                total=MATCH_ROUNDS,
                seconds=rest,
            ),
        )
        if rest <= 0:
            await self._start_round(session)
            return
        session.timer = asyncio.create_task(self._break_timer(session, rest))

    async def _break_timer(self, session: DuelSession, seconds: int) -> None:
        """Отсчитать перерыв и вывести бойцов на новый раунд."""
        turn = session.round_number
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        # За минуту бой мог закончиться иначе — проверяем, что он всё ещё наш
        if self._duels.get(session.id) is not session or session.round_number != turn:
            return
        await self._start_round(session)

    async def _finish(self, session: DuelSession, result: RoundResult) -> None:
        self._cancel_timer(session)
        # Обычно панель уже погашена итогом раунда. Осталась висеть — гасим
        # здесь, чтобы под законченным боем не остались живые кнопки.
        if session.prompt_message_id is not None:
            await self._close_panel(session, "🔒 Бой окончен.")
        self._forget(session)

        rewards = await self._apply_results(session, result)
        text = finish_report(result, session.fighters, self.rng)
        if rewards:
            text += "\n\n" + rewards
        await self._send(session.chat_id, session.thread_id, text)
        if session.on_finish is not None:
            await session.on_finish(session, result)
        await self.db.add_duel(
            chat_id=session.chat_id,
            thread_id=session.thread_id,
            challenger_id=session.order[0],
            opponent_id=session.order[1],
            winner_id=result.winner_id,
            rounds=session.round_number,
            end_reason=result.end_reason.value if result.end_reason else None,
            mode=session.mode,
        )

    async def _apply_results(self, session: DuelSession, result: RoundResult) -> str:
        """Начислить опыт, кредиты и рейтинг. Вернуть блок текста с итогами."""
        players: dict[int, Player] = {}
        for user_id in session.order:
            player = await self.db.get_player(user_id)
            if player is not None:
                players[user_id] = player
        if not players:  # pragma: no cover - персонажей удалили по ходу боя
            return ""

        previous_fights = await self.db.count_recent_duels_between(
            session.order[0], session.order[1], REPEAT_WINDOW_HOURS
        )
        share = repeat_share(previous_fights)
        # Уровни фиксируем до начисления: иначе взятый в этом же бою уровень
        # перекосит награду второго бойца.
        levels = {
            user_id: players[user_id].level
            if user_id in players
            else session.fighters[user_id].level
            for user_id in session.order
        }

        rows: list[tuple[Player, ProgressReport]] = []
        broken: list[tuple[Player, list]] = []
        for user_id, player in players.items():
            fighter = session.fighters[user_id]
            opponent = session.opponent_of(user_id)
            my_level = levels[user_id]
            opponent_level = levels[opponent.user_id]
            won = result.winner_id == user_id

            full_exp = win_exp(fighter.damage_dealt, my_level, opponent_level)
            if won:
                player.wins += 1
                exp = full_exp
            elif result.winner_id is None:
                player.draws += 1
                exp = consolation_exp(full_exp, DRAW_EXP_SHARE)
            else:
                player.losses += 1
                exp = consolation_exp(full_exp, LOSS_EXP_SHARE)

            delta = rating_delta(won, my_level, opponent_level)
            if share < 1.0:
                exp = apply_share(exp, share)
                delta = int(math.copysign(apply_share(abs(delta), share), delta))

            if player.birthplace is None and session.chat_title:
                # Место рождения — группа, где боец впервые вышел на ринг
                player.birthplace = session.chat_title
            # Вещи снашиваются до того, как фиксируем здоровье: развалившаяся
            # экипировка уменьшает запас, и HP надо обрезать по новому потолку.
            # На кулаках вещи остались в раздевалке — снашивать нечего.
            ruined = (
                await wear_after_fight(self.db, player, won, self.rng)
                if session.mode.armed
                else []
            )
            if ruined:
                broken.append((player, ruined))
            player.set_hp(fighter.hp)
            # Подписка множит уже урезанное: полтора от того, что боец
            # действительно заработал в этом бою
            report = player.grant_exp(pro_exp(exp, player.is_pro()))
            report.rating_delta = delta
            player.apply_rating(delta)
            await self.db.save_player(player)
            rows.append((player, report))

        return rewards_report(rows, share, previous_fights, broken)

    def _cancel_timer(self, session: DuelSession) -> None:
        """Снять таймер раунда.

        Раунд может считаться прямо из задачи-таймера (когда время вышло),
        поэтому себя же отменять нельзя — иначе CancelledError оборвёт подсчёт
        итогов на первом же await.
        """
        timer = session.timer
        session.timer = None
        if timer and not timer.done() and timer is not asyncio.current_task():
            timer.cancel()

    # ---------- состояние ----------

    def duel_in_chat(self, chat_id: int, thread_id: int | None) -> DuelSession | None:
        duel_id = self._duel_by_chat.get((chat_id, thread_id))
        return self._duels.get(duel_id) if duel_id else None

    # ---------- то, что нужно мини-аппу ----------

    def open_challenges(self) -> list[Challenge]:
        """Вызовы, ждущие соперника, — свежие сверху.

        Мини-апп показывает их списком: адресный вызов виден только тому,
        кому он брошен, и самому вызвавшему, чтобы было что отозвать.
        """
        return sorted(self._challenges.values(), key=lambda c: c.id, reverse=True)

    def challenge_for(self, user_id: int, challenge: Challenge) -> bool:
        """Видит ли этот боец этот вызов."""
        if challenge.challenger.user_id == user_id:
            return True
        return challenge.target_id in (None, user_id)

    def challenge_of_user(self, user_id: int) -> Challenge | None:
        """Свой вызов, который висит в ожидании ответа."""
        for challenge in self._challenges.values():
            if challenge.challenger.user_id == user_id:
                return challenge
        return None

    def get_challenge(self, challenge_id: int) -> Challenge | None:
        return self._challenges.get(challenge_id)

    async def withdraw_challenge(self, user_id: int) -> bool:
        """Отозвать свой вызов. False — отзывать было нечего."""
        challenge = self.challenge_of_user(user_id)
        if challenge is None:
            return False
        await self._withdraw_challenges_of(user_id)
        return True

    def duel_of_user(self, user_id: int) -> DuelSession | None:
        for session in self._duels.values():
            if user_id in session.fighters:
                return session
        return None

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._busy

    async def shutdown(self) -> None:
        for session in list(self._duels.values()):
            self._cancel_timer(session)
            with contextlib.suppress(Exception):
                await self._send(
                    session.chat_id,
                    session.thread_id,
                    "⚠️ Судья прерывает бой: бот уходит на перезагрузку. "
                    "Результат не засчитан.",
                )
        for challenge in list(self._challenges.values()):
            if challenge.task and not challenge.task.done():
                challenge.task.cancel()
        self._duels.clear()
        self._duel_by_chat.clear()
        self._challenges.clear()
        self._busy.clear()

    # ---------- отправка ----------

    async def _close_panel(self, session: DuelSession, text: str) -> None:
        """Погасить панель раунда, оставив на её месте итог.

        Кнопки убираем пустой разметкой: без неё Telegram оставит старые, и
        по ним можно будет ходить в уже сыгранном раунде.
        """
        await self._edit(
            session.chat_id,
            session.prompt_message_id,
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        session.prompt_message_id = None

    async def _send(self, chat_id: int, thread_id: int | None, text: str, **kwargs):
        return await self.voice.send(chat_id, thread_id, text, **kwargs)

    async def _edit(self, chat_id: int, message_id: int | None, text: str, **kwargs):
        return await self.voice.edit(chat_id, message_id, text, **kwargs)
