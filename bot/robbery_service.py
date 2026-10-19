"""Живое ограбление: нападение на бойца прямо в доме.

Это третий бой, который живёт в мини-аппе, — после штурма точки и
группового. От них он отличается тем, с чего начинается: жертву никто
не спрашивал. Дуэль принимают, в рейд записываются, на точку идут
отрядом, а тут один нажал — и драка уже идёт.

**Обе стороны — живые люди.** Поэтому раунд раскладывается по парам тем
же `pair_up`, что и групповой бой, и судит его тот же `judge`: драка
пятеро на пятерых — это и есть бой стенка на стенку, и второго такого
движка заводить незачем.

**Молчуна ведёт бросок.** Сорок пять секунд на ход, а дальше раунд
считается без него. Жертва не выбирала этот бой, и держать драку на
том, нажмёт ли она кнопку, нельзя: тогда любое ограбление кончалось бы
ничем, стоило ограбленному закрыть приложение.

**Вмешаться можно на ходу.** Свои подходят прямо посреди драки — и к
напавшему, и к жертве, если у той есть семья. Пара новичку находится со
следующего раунда: `pair_up` раскладывает живых заново на каждом.

**Ограбление живёт в памяти.** Как рейд, штурм и групповой бой: упал
процесс — драка пропала, и ничьи деньги не тронуты. В базу уходит
только развязка.

Правила и числа — в `bot.game.robbery`, хранение — в `bot.database`,
экраны — в `bot.webapp.crowd`.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import random
import time
from dataclasses import dataclass, field

from bot.database import Database
from bot.game.battle import (
    BLUE,
    MAX_BATTLE_ROUNDS,
    RED,
    BattleKind,
    BattleOutcome,
    judge,
    pair_up,
)
from bot.game.classes import Zone, block_combo
from bot.game.combat import Action, Fighter, resolve_round
from bot.game.economy import (
    DRAW_EXP_SHARE,
    LOSS_EXP_SHARE,
    consolation_exp,
    pro_exp,
    win_exp,
)
from bot.game.fightlog import turn_payload
from bot.game.health import now_ts
from bot.game.narrator import strike_lines
from bot.game.robbery import (
    ATTACKS_PER_LICENCE,
    GOAL_REWARD,
    LICENCE_PRICE,
    MAX_SIDE,
    TURN_SECONDS,
    attacks_in,
    goal_done,
    loot_of,
    safe_left,
)
from bot.game.presence import is_online
from bot.game.work import week_of
from bot.injury_service import hurt_player
from bot.models import Player
from bot.raid_service import Choice

logger = logging.getLogger(__name__)


class RobberyError(Exception):
    """Отказ по ограблению, который показывают игроку как есть."""


@dataclass
class Robbery:
    """Идущая драка: кто на кого, кто подошёл и чей сейчас ход."""

    id: int
    place: str
    place_title: str
    # Что это за драка: `rob` — ограбление, `arrest` — задержание. Бой
    # у них один, разная только развязка
    kind: str
    attacker: int
    victim: int
    fighters: dict[int, Fighter]
    sides: dict[int, int]
    names: dict[int, str]
    # Чей клан у бойца. Ноль — ничей: такой в драку не вмешивается
    clans: dict[int, int]
    # Кто из дерущихся при значке полиции. От этого зависит развязка:
    # мафия, которую побил полицейский, едет в тюрьму
    badges: set[int] = field(default_factory=set)
    round_number: int = 0
    choices: dict[int, Choice] = field(default_factory=dict)
    acted: set[int] = field(default_factory=set)
    pairs: list[tuple[int, int]] = field(default_factory=list)
    idle: list[int] = field(default_factory=list)
    hurt: dict[int, object] = field(default_factory=dict)
    rounds: list[dict] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    finished: bool = False
    won: bool = False
    # Судья развёл стороны поровну: опыт тогда идёт по ничьей
    draw: bool = False
    loot: int = 0
    # Премия семье за закрытую недельную цель. Ноль — не закрыли
    bonus: int = 0
    # Кого посадили и на сколько: по строке на задержанного
    jailed: list[dict] = field(default_factory=list)
    # Сколько награбленного вернулось в участок
    recovered: int = 0
    timer: asyncio.Task | None = None
    started_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def alive_ids(self) -> list[int]:
        return [one for one, who in self.fighters.items() if who.alive]

    def side_of(self, user_id: int) -> int:
        return self.sides.get(user_id, BLUE)

    def side_ids(self, side: int) -> list[int]:
        return [one for one in self.fighters if self.side_of(one) == side]

    @property
    def paired(self) -> list[int]:
        """Кому в этом раунде досталась пара. Остальные его пропускают."""
        return [one for pair in self.pairs for one in pair]

    @property
    def round_over(self) -> bool:
        return all(
            one in self.acted for one in self.paired if self.fighters[one].alive
        )

    def choice_of(self, user_id: int) -> Choice:
        return self.choices.setdefault(user_id, Choice())

    def foe_of(self, user_id: int) -> Fighter | None:
        """С кем боец дерётся в этом раунде. Пусто — пары не досталось."""
        for first, second in self.pairs:
            if first == user_id:
                return self.fighters.get(second)
            if second == user_id:
                return self.fighters.get(first)
        return None

    def seconds_left(self, timeout: int = TURN_SECONDS) -> int:
        return max(0, round(timeout - (time.monotonic() - self.started_at)))


class RobberyService:
    """Служба ограблений: лицензии, нападения и их развязка."""

    def __init__(
        self,
        db: Database,
        rng: random.Random | None = None,
        turn_seconds: int = TURN_SECONDS,
    ) -> None:
        self.db = db
        self.rng = rng or random.Random()
        self.turn_seconds = turn_seconds
        self._ids = itertools.count(1)
        self._raids: dict[int, Robbery] = {}
        # Чем боец занят: двух драк на человека не бывает
        self._busy: dict[int, int] = {}
        # Развязка, которую ещё не закрыли кнопкой «Понятно»
        self._results: dict[int, Robbery] = {}

    # ---------- кто где ----------

    def is_busy(self, user_id: int) -> bool:
        """Занят ли боец: этим дорога и решает, отпускать ли его."""
        return user_id in self._busy

    def of_user(self, user_id: int) -> Robbery | None:
        one = self._raids.get(self._busy.get(user_id, 0))
        return one if one is not None else None

    def result_of(self, user_id: int) -> Robbery | None:
        return self._results.get(user_id)

    def forget_result(self, user_id: int) -> None:
        """Закрыть итог — и выйти из драки, если она забыла отпустить.

        Кнопка «Понятно» обязана работать всегда. Драка, которая
        кончилась, но не развела бойцов, держит их у себя насовсем: ни
        уйти из дома, ни ударить, ни нажать хоть что-то. Это последний
        выход из неё, не требующий перезапуска бота.
        """
        self._results.pop(user_id, None)
        raid_id = self._busy.get(user_id)
        if raid_id is None:
            return
        raid = self._raids.get(raid_id)
        if raid is not None and not raid.finished:
            return
        self._busy.pop(user_id, None)
        self._raids.pop(raid_id, None)
        logger.warning("Боец %s вышел из зависшей драки %s", user_id, raid_id)

    def at(self, place: str) -> list[Robbery]:
        """Какие драки идут в этом доме: их видно всем, кто тут стоит."""
        return [one for one in self._raids.values() if one.place == place]

    # ---------- лицензии ----------

    async def licences(self, user_id: int, now: int | None = None) -> dict[str, int]:
        """Сколько нападений на руках и сколько пачек боец взял всего."""
        left, bought = await self.db.licences(user_id)
        return {
            "left": left,
            "bought": bought,
            "pack": ATTACKS_PER_LICENCE,
            "price": LICENCE_PRICE,
        }

    async def buy(
        self, player: Player, count: int = 1, now: int | None = None
    ) -> int:
        """Купить лицензии в особняке. Возвращает остаток нападений.

        Платит боец из своего кармана, а не из сейфа: лицензия — его
        право на нападение, и добыча тоже пойдёт ему одному. Потолка на
        покупку нет: потолок тут цена, а срока у пачки нет вовсе.
        """
        from bot.game.clans import MANSION

        moment = now_ts() if now is None else now
        if count < 1:
            raise RobberyError("Сколько лицензий брать?")
        if player.clan is None:
            raise RobberyError("Лицензии выдают своим. Сперва вступи в семью.")
        if player.where(moment) != MANSION:
            raise RobberyError("За лицензией приходят в особняк.")
        price = LICENCE_PRICE * count
        if player.credits < price:
            raise RobberyError(f"Не хватает: лицензия стоит {LICENCE_PRICE} 💰.")
        player.credits -= price
        await self.db.save_player(player)
        await self.db.buy_licences(
            player.user_id, count, attacks_in(count)
        )
        left, _ = await self.db.licences(player.user_id)
        logger.info(
            "Боец %s купил лицензий: %s, нападений на руках %s",
            player.user_id, count, left,
        )
        return left

    async def _spend(self, user_id: int, now: int) -> None:
        """Списать одно нападение. Нечего — так и говорим."""
        if not await self.db.spend_licence(user_id):
            raise RobberyError(
                "Нет лицензии на ограбление. Её берут в особняке, у своих."
            )

    # ---------- нападение ----------

    async def rob(
        self, player: Player, victim_id: int, now: int | None = None
    ) -> Robbery:
        """Напасть на бойца. Драка начинается сразу, без согласия жертвы."""
        from bot.game.locations import get_location

        moment = now_ts() if now is None else now
        if player.clan is None:
            raise RobberyError("Грабят семьями. Одиночке лицензию не выпишут.")
        if self._busy.get(player.user_id):
            raise RobberyError("Ты уже в деле: сперва закончи его.")
        if victim_id == player.user_id:
            raise RobberyError("Себя грабить не из чего.")

        victim = await self.db.get_player(victim_id)
        if victim is None:
            raise RobberyError("Такого бойца нет.")
        here = player.where(moment)
        if victim.where(moment) != here or victim.in_transit(moment):
            raise RobberyError("Его тут уже нет.")
        if not is_online(victim.seen_at, moment):
            raise RobberyError("Его нет в сети: грабить некого.")
        if victim.clan is not None and player.clan is not None:
            if victim.clan.clan.id == player.clan.clan.id:
                raise RobberyError("Своих не трогают.")
        if self._busy.get(victim_id):
            raise RobberyError("Его уже держат — дождись развязки.")
        if not player.can_fight(moment):
            raise RobberyError("Сперва подлечись: в таком виде не нападают.")
        if not victim.can_fight(moment):
            raise RobberyError("Он и так еле стоит. Не сегодня.")
        rest = safe_left(await self.db.robbed_at(victim_id), moment)
        if rest:
            raise RobberyError(
                f"Его уже грабили — не трогают ещё {max(1, rest // 3600)} ч."
            )

        await self._spend(player.user_id, moment)
        place = get_location(here)
        raid = Robbery(
            id=next(self._ids),
            place=here,
            place_title=place.title if place else here,
            kind="rob",
            attacker=player.user_id,
            victim=victim_id,
            fighters={
                player.user_id: Fighter.from_player(player, armed=True),
                victim_id: Fighter.from_player(victim, armed=True),
            },
            sides={player.user_id: RED, victim_id: BLUE},
            names={player.user_id: player.nickname, victim_id: victim.nickname},
            clans={
                player.user_id: player.clan.clan.id,
                victim_id: victim.clan.clan.id if victim.clan else 0,
            },
        )
        self._raids[raid.id] = raid
        self._busy[player.user_id] = raid.id
        self._busy[victim_id] = raid.id
        logger.info(
            "Ограбление в %s: боец %s напал на %s", here, player.user_id, victim_id
        )
        self._open_round(raid)
        return raid

    async def arrest(
        self, officer: Player, suspect_id: int, now: int | None = None
    ) -> Robbery:
        """Задержать разыскиваемого. Та же драка, другая развязка.

        Лицензии задержание не стоит: лицензию покупают, чтобы нападать,
        а задерживать — это работа. Зато нужен ордер, а ордер берётся из
        заявления жертвы, а не из кассы.
        """
        from bot.game.locations import get_location
        from bot.police_service import has_warrant, serves_in_police

        moment = now_ts() if now is None else now
        if not serves_in_police(officer):
            raise RobberyError("Задержание — дело полиции.")
        if self._busy.get(officer.user_id):
            raise RobberyError("Ты уже в деле: сперва закончи его.")
        if suspect_id == officer.user_id:
            raise RobberyError("Себя задерживать не за что.")

        suspect = await self.db.get_player(suspect_id)
        if suspect is None:
            raise RobberyError("Такого бойца нет.")
        if not await has_warrant(self.db, suspect_id):
            raise RobberyError("На него нет ордера: заявления никто не писал.")
        here = officer.where(moment)
        if suspect.where(moment) != here or suspect.in_transit(moment):
            raise RobberyError("Его тут уже нет.")
        if not is_online(suspect.seen_at, moment):
            raise RobberyError("Его нет в сети: задерживать некого.")
        if self._busy.get(suspect_id):
            raise RobberyError("Его уже держат — дождись развязки.")
        if not officer.can_fight(moment):
            raise RobberyError("Сперва подлечись: в таком виде не задерживают.")
        if not suspect.can_fight(moment):
            raise RobberyError("Он и так еле стоит. Не сегодня.")

        place = get_location(here)
        raid = Robbery(
            id=next(self._ids),
            place=here,
            place_title=place.title if place else here,
            kind="arrest",
            attacker=officer.user_id,
            victim=suspect_id,
            fighters={
                officer.user_id: Fighter.from_player(officer, armed=True),
                suspect_id: Fighter.from_player(suspect, armed=True),
            },
            sides={officer.user_id: RED, suspect_id: BLUE},
            names={officer.user_id: officer.nickname,
                   suspect_id: suspect.nickname},
            clans={
                officer.user_id: 0,
                suspect_id: suspect.clan.clan.id if suspect.clan else 0,
            },
            badges={officer.user_id},
        )
        self._raids[raid.id] = raid
        self._busy[officer.user_id] = raid.id
        self._busy[suspect_id] = raid.id
        logger.info(
            "Задержание в %s: сотрудник %s берёт %s",
            here, officer.user_id, suspect_id,
        )
        self._open_round(raid)
        return raid

    async def join(
        self, raid_id: int, player: Player, now: int | None = None
    ) -> Robbery:
        """Вмешаться в драку на стороне своей семьи. Тоже по лицензии."""
        moment = now_ts() if now is None else now
        raid = self._raids.get(raid_id)
        if raid is None:
            raise RobberyError("Эта драка уже кончилась.")
        if player.user_id in raid.fighters:
            return raid
        if self._busy.get(player.user_id):
            raise RobberyError("Ты уже в деле: сперва закончи его.")
        if player.clan is None:
            raise RobberyError("Вмешиваются за семью. У тебя её нет.")
        if player.where(moment) != raid.place or player.in_transit(moment):
            raise RobberyError("Драка идёт не здесь. Дойди до места.")
        if not player.can_fight(moment):
            raise RobberyError("Сперва подлечись: в таком виде не лезут.")

        mine = player.clan.clan.id
        side = self._side_for(raid, mine)
        if side is None:
            raise RobberyError("Это не твоя драка: ни одна сторона тебе не своя.")
        if len(raid.side_ids(side)) >= MAX_SIDE:
            raise RobberyError(f"На этой стороне мест нет: больше {MAX_SIDE} не лезут.")

        await self._spend(player.user_id, moment)
        self._enlist(raid, player, side, clan_id=mine)
        logger.info(
            "В ограбление %s вмешался боец %s за сторону %s",
            raid.id, player.user_id, side,
        )
        return raid

    async def step_in(
        self, raid_id: int, officer: Player, now: int | None = None
    ) -> Robbery:
        """Полицейский встаёт на сторону того, на кого напали.

        Лицензии не просим: лицензию покупают, чтобы нападать. Защищать
        — это работа, и платить за право её делать патрульный не должен.
        """
        from bot.police_service import serves_in_police

        moment = now_ts() if now is None else now
        raid = self._raids.get(raid_id)
        if raid is None:
            raise RobberyError("Эта драка уже кончилась.")
        if officer.user_id in raid.fighters:
            return raid
        if not serves_in_police(officer):
            raise RobberyError("Так вмешивается полиция, а ты не при значке.")
        if self._busy.get(officer.user_id):
            raise RobberyError("Ты уже в деле: сперва закончи его.")
        if officer.where(moment) != raid.place or officer.in_transit(moment):
            raise RobberyError("Драка идёт не здесь. Дойди до места.")
        if not officer.can_fight(moment):
            raise RobberyError("Сперва подлечись: в таком виде не лезут.")

        side = self._side_for_badge(raid)
        if len(raid.side_ids(side)) >= MAX_SIDE:
            raise RobberyError(f"На этой стороне мест нет: больше {MAX_SIDE} не лезут.")
        self._enlist(raid, officer, side, clan_id=0, badge=True)
        logger.info(
            "В драку %s вмешался сотрудник %s за сторону %s",
            raid.id, officer.user_id, side,
        )
        return raid

    def _enlist(
        self, raid: Robbery, player: Player, side: int, clan_id: int,
        badge: bool = False,
    ) -> None:
        """Поставить бойца в строй. Пара найдётся со следующего раунда."""
        raid.fighters[player.user_id] = Fighter.from_player(player, armed=True)
        raid.sides[player.user_id] = side
        raid.names[player.user_id] = player.nickname
        raid.clans[player.user_id] = clan_id
        if badge:
            raid.badges.add(player.user_id)
        self._busy[player.user_id] = raid.id

    @staticmethod
    def _side_for_badge(raid: Robbery) -> int:
        """На чьей стороне встанет полицейский.

        Где уже есть значок — туда; а если значка нет ни на ком, то за
        того, на кого напали. Другой стороны у полиции в этой драке нет.
        """
        for side in (RED, 1 - RED):
            if any(one in raid.badges for one in raid.side_ids(side)):
                return side
        return raid.side_of(raid.victim)

    @staticmethod
    def _side_for(raid: Robbery, clan_id: int) -> int | None:
        """На чьей стороне этот клан. None — ни на чьей.

        Своей считается сторона, за которую дерётся хоть один человек
        той же семьи. Поэтому к жертве-одиночке помощь не приходит: у
        неё нет семьи, а чужой в этой драке делать нечего.
        """
        for side in (RED, BLUE):
            if any(raid.clans.get(one) == clan_id for one in raid.side_ids(side)):
                return side
        return None

    # ---------- кнопки ----------

    async def choose(
        self, raid_id: int, user_id: int, action: str, zone_value: str,
        hand: int = 0,
    ) -> str:
        """Нажатие бойца. Выбрал и удар, и блок — ход принят."""
        raid = self._raids.get(raid_id)
        if raid is None:
            raise RobberyError("Эта драка уже кончилась.")
        if user_id not in raid.fighters:
            raise RobberyError("Ты не в этой драке.")
        fighter = raid.fighters[user_id]
        if not fighter.alive:
            raise RobberyError("Тебя уже вынесли.")
        if user_id in raid.acted:
            raise RobberyError("Ход уже принят.")
        if user_id not in raid.paired:
            raise RobberyError("В этом раунде пары тебе не досталось.")

        choice = raid.choice_of(user_id)
        if action == "attack":
            choice.attacks[hand] = Zone(zone_value)
        elif action == "block":
            choice.block = block_combo(Zone(zone_value), fighter.block_width)
        else:  # pragma: no cover - кнопок всего две
            raise RobberyError("Непонятное нажатие.")

        if not choice.ready_for(fighter.attacks_per_round):
            return ""
        async with raid.lock:
            if raid.finished or user_id in raid.acted:
                return ""
            raid.acted.add(user_id)
            if raid.round_over:
                await self._play_round(raid)
        return ""

    async def use_ability(self, raid_id: int, user_id: int, code: str) -> str:
        """Нажать приём. Жмут до удара и блока, как везде."""
        raid = self._raids.get(raid_id)
        if raid is None:
            raise RobberyError("Эта драка уже кончилась.")
        fighter = raid.fighters.get(user_id)
        if fighter is None or not fighter.alive:
            raise RobberyError("Приёмы применяет тот, кто стоит на ногах.")
        try:
            charge = fighter.use(code)
        except ValueError as error:
            raise RobberyError(str(error)) from error
        return f"{charge.ability.title} наготове."

    # ---------- раунд ----------

    def _open_round(self, raid: Robbery) -> None:
        """Начать раунд: разложить живых по парам и дать право хода."""
        raid.round_number += 1
        raid.acted = set()
        raid.choices = {}
        raid.started_at = time.monotonic()
        raid.pairs, raid.idle = pair_up(
            raid.fighters, raid.sides, BattleKind.TEAM, raid.round_number
        )
        if raid.timer is not None:
            raid.timer.cancel()
        raid.timer = asyncio.create_task(self._round_timer(raid, raid.round_number))

    async def _round_timer(self, raid: Robbery, number: int) -> None:
        """Молчуны пропускают ход, и раунд считается без них."""
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(self.turn_seconds)
            if self._raids.get(raid.id) is not raid or raid.round_number != number:
                return
            async with raid.lock:
                if raid.finished or raid.round_number != number:
                    return
                raid.acted.update(
                    one for one in raid.paired if raid.fighters[one].alive
                )
                await self._play_round(raid)

    def _action_of(self, raid: Robbery, user_id: int) -> Action:
        weapons = raid.fighters[user_id].attacks_per_round
        return raid.choices.get(user_id, Choice()).to_action(weapons)

    async def _play_round(self, raid: Robbery) -> None:
        """Свести раунд: по размену на пару, потом слово судьи.

        Сорвалось что угодно — драку всё равно закрываем. Висящая
        драка хуже любой ошибки в ней: из неё не выйти, по городу не
        пройти, и чинится это только перезапуском бота.
        """
        try:
            await self._round(raid)
        except Exception:
            logger.exception("Раунд драки %s сорвался", raid.id)
            await self._bail_out(raid)

    async def _bail_out(self, raid: Robbery) -> None:
        """Отпустить всех из драки, которая сломалась на полуслове."""
        if raid.id not in self._raids and not any(
            self._busy.get(one) == raid.id for one in raid.fighters
        ):
            return
        raid.finished = True
        raid.summary = ["Драка оборвалась: судья развёл всех по углам."]
        if raid.timer is not None:
            raid.timer.cancel()
        self._raids.pop(raid.id, None)
        for user_id in raid.fighters:
            self._busy.pop(user_id, None)
            self._results[user_id] = raid
        logger.warning("Драка %s оборвана: бойцы отпущены", raid.id)

    async def _round(self, raid: Robbery) -> None:
        """Сам раунд: по размену на пару, потом слово судьи."""
        for first_id, second_id in raid.pairs:
            first, second = raid.fighters[first_id], raid.fighters[second_id]
            if not (first.alive and second.alive):
                continue
            result = resolve_round(
                first,
                self._action_of(raid, first_id),
                second,
                self._action_of(raid, second_id),
                raid.round_number,
                self.rng,
                # Усталость растянута на всю драку, а не на дуэль
                limit=MAX_BATTLE_ROUNDS,
            )
            raid.hurt.update(result.injuries)
            said = strike_lines(result, raid.fighters, self.rng)
            raid.rounds.append(turn_payload(result, said))

        outcome = judge(
            raid.fighters, raid.sides, BattleKind.TEAM, raid.round_number
        )
        if outcome.finished:
            await self._finish(raid, outcome)
        else:
            self._open_round(raid)

    # ---------- развязка ----------

    async def _finish(self, raid: Robbery, outcome: BattleOutcome) -> None:
        """Свести драку: добыча, здоровье, тюрьма и недельные счета."""
        if raid.finished:  # pragma: no cover - две развязки разом
            return
        raid.finished = True
        raid.won = outcome.winning_team == RED
        raid.draw = bool(outcome.draw)
        if raid.timer is not None:
            raid.timer.cancel()
        moment = now_ts()

        # Развязка трогает многое: карман, здоровье, дела, тюрьму,
        # недельные счета. Сломается что-нибудь одно — бойцы всё равно
        # должны выйти из драки: иначе им не уйти из дома и не нажать
        # ни одной кнопки, пока бота не перезапустят
        try:
            if raid.kind == "arrest":
                await self._finish_arrest(raid, moment)
            else:
                await self._finish_robbery(raid, moment)
            raid.summary = self._summary(raid, outcome)
        except Exception:
            logger.exception("Развязка драки %s сорвалась", raid.id)
            raid.summary = raid.summary or [
                "Драка кончена, но итог посчитать не вышло."
            ]
        finally:
            self._raids.pop(raid.id, None)
            for user_id in raid.fighters:
                self._busy.pop(user_id, None)
                self._results[user_id] = raid
        logger.info(
            "Драка %s (%s) кончена: %s, добыча %s",
            raid.id, raid.kind, "взяли" if raid.won else "отбились", raid.loot,
        )

    async def _finish_robbery(self, raid: Robbery, moment: int) -> None:
        """Развязка ограбления: карман, покой, дело и счёт семьи."""
        victim = await self.db.get_player(raid.victim)
        robber = await self.db.get_player(raid.attacker)
        if raid.won and victim is not None and robber is not None:
            # Бьют по карману, а не по счёту: на карте деньги целы
            raid.loot = loot_of(victim.credits)
            victim.credits -= raid.loot
            robber.credits += raid.loot
        await self._settle(raid, victim)
        await self._settle(raid, robber)
        for user_id in raid.fighters:
            if user_id not in (raid.victim, raid.attacker):
                await self._settle(raid, await self.db.get_player(user_id))

        # Покой ограбленного — от развязки, кто бы ни победил
        await self.db.mark_robbed(raid.victim, moment)
        # Дело заводится на любое нападение, а не только на удачное:
        # полиция видит и то, от чего отбились
        await self.db.open_case(
            raid.victim, raid.attacker, raid.place, raid.loot,
            int(raid.won), moment,
        )
        clan_id = raid.clans.get(raid.attacker, 0)
        if clan_id:
            await self._count_for_clan(raid, clan_id, moment)
        # Полицейский на стороне жертвы и победа жертвы — это посадка
        if not raid.won and self._badge_on(raid, 1 - RED):
            await self._lock_up(raid, raid.side_ids(RED), moment)

    async def _finish_arrest(self, raid: Robbery, moment: int) -> None:
        """Развязка задержания: награбленное в участок, мафия в тюрьму."""
        for user_id in raid.fighters:
            await self._settle(raid, await self.db.get_player(user_id))
        if not raid.won:
            # Отбился — ордер остаётся, и его возьмут в следующий раз
            return
        from bot.police_service import reward_arrest

        raid.recovered = await self.db.close_cases(
            raid.victim, raid.attacker, moment
        )
        await reward_arrest(self.db, raid.attacker, raid.recovered, moment)
        await self._lock_up(raid, [raid.victim], moment)

    @staticmethod
    def _badge_on(raid: Robbery, side: int) -> bool:
        """Был ли на этой стороне хоть один при значке."""
        return any(one in raid.badges for one in raid.side_ids(side))

    async def _lock_up(
        self, raid: Robbery, user_ids: list[int], moment: int
    ) -> None:
        """Посадить проигравших. Сидят только те, кто в семье.

        Одиночку сажать не за что: он не мафия, и ордер выписан не на
        него. В ограблении на стороне налётчика одиночек и не бывает,
        а вот в помощь мафии мог влезть кто угодно — проверяем.
        """
        from bot.police_service import jail

        for user_id in user_ids:
            if not raid.clans.get(user_id):
                continue
            until = await jail(self.db, user_id, moment)
            raid.jailed.append(
                {
                    "user_id": user_id,
                    "name": raid.names.get(user_id, "боец"),
                    "until": until,
                    "hours": max(1, (until - moment) // 3600),
                }
            )

    async def _count_for_clan(
        self, raid: Robbery, clan_id: int, moment: int
    ) -> None:
        """Засчитать семье нападение и выдать премию, если цель закрыта.

        Премию кладём сразу, а не в конце недели: свести её было бы
        некому — часов у клуба нет, — а ждать понедельника, чтобы
        получить за уже сделанное, незачем. Выдаётся она один раз, и это
        держит сама база: `pay_clan_goal` и отмечает, и проверяет.
        """
        from bot.bonus_service import note_raid

        # Нападение идёт в два счёта: в старую недельную цель семьи и в
        # бонусный журнал. Счётчика на двоих не делаем — цель считает
        # своё, пулл своё, и правила у них разные
        await note_raid(self.db, clan_id, moment)
        attacks = await self.db.add_clan_attack(clan_id, week_of(moment))
        if not goal_done(attacks):
            return
        if not await self.db.pay_clan_goal(clan_id, week_of(moment)):
            return
        await self.db.move_vault(clan_id, GOAL_REWARD)
        raid.bonus = GOAL_REWARD
        logger.info(
            "Семья %s закрыла недельную цель: %s в сейф", clan_id, GOAL_REWARD
        )

    async def _settle(self, raid: Robbery, player: Player | None) -> None:
        """Записать бойцу, чем для него кончилась драка.

        Здоровье, травмы и опыт — да, износ вещей — нет: ограбление
        коротко, и платит за него тот, у кого взяли деньги. Снашивать
        ему заодно и куртку значило бы наказать дважды за одно.
        """
        if player is None:  # pragma: no cover - персонажа удалили по ходу
            return
        fighter = raid.fighters.get(player.user_id)
        if fighter is None:  # pragma: no cover - в драке были все, кто записан
            return
        injury = raid.hurt.get(player.user_id)
        if injury is not None:
            await hurt_player(self.db, player, injury)
        player.set_hp(fighter.hp)
        player.grant_exp(self._exp_for(raid, player))
        await self.db.save_player(player)

    def _exp_for(self, raid: Robbery, player: Player) -> int:
        """Опыт за драку — тот же, что за бой на ринге.

        Считается по нанесённому урону и по уровню противной стороны,
        как в дуэли: драка в подворотне — такой же бой, и опыт за неё
        должен быть таким же. Рейтинг при этом не двигается: рейтинг —
        лестница ринга, и ограблением по ней не поднимаются.
        """
        fighter = raid.fighters[player.user_id]
        mine = raid.side_of(player.user_id)
        others = [
            raid.fighters[one].level
            for one in raid.fighters
            if raid.side_of(one) != mine
        ]
        against = round(sum(others) / len(others)) if others else fighter.level
        full = win_exp(fighter.damage_dealt, fighter.level, against)
        won = raid.won if mine == RED else not raid.won
        if raid.draw:
            full = consolation_exp(full, DRAW_EXP_SHARE)
        elif not won:
            full = consolation_exp(full, LOSS_EXP_SHARE)
        return pro_exp(full, player.is_pro())

    def _summary(self, raid: Robbery, outcome: BattleOutcome) -> list[str]:
        first = (
            self._arrest_line(raid, outcome)
            if raid.kind == "arrest"
            else self._robbery_line(raid, outcome)
        )
        lines = [
            first,
            f"Дрались в {raid.place_title}, раундов: {raid.round_number}.",
        ]
        if raid.bonus:
            lines.append(
                f"Недельная цель семьи закрыта: {raid.bonus} 💰 в сейф."
            )
        for one in raid.jailed:
            lines.append(f"{one['name']} сел на {one['hours']} ч.")
        return lines

    def _robbery_line(self, raid: Robbery, outcome: BattleOutcome) -> str:
        robber = raid.names.get(raid.attacker, "налётчик")
        victim = raid.names.get(raid.victim, "боец")
        if raid.won:
            return (
                f"{robber} обобрал {victim}: {raid.loot} 💰 из кармана."
                if raid.loot
                else f"{robber} взял верх, но карман {victim} оказался пуст."
            )
        if outcome.draw:
            return "Разняли: оба на ногах, деньги при своих."
        if raid.jailed:
            return f"{victim} отбился, и за налётчиками приехали."
        return f"{victim} отбился — брать нечего."

    def _arrest_line(self, raid: Robbery, outcome: BattleOutcome) -> str:
        officer = raid.names.get(raid.attacker, "патрульный")
        suspect = raid.names.get(raid.victim, "задержанный")
        if raid.won:
            return (
                f"{officer} взял {suspect}. В участок вернулось "
                f"{raid.recovered} 💰."
                if raid.recovered
                else f"{officer} взял {suspect}. Возвращать было нечего."
            )
        if outcome.draw:
            return "Разняли. Ордер остаётся в силе."
        return f"{suspect} ушёл от {officer}. Ордер остаётся в силе."

    async def close(self) -> None:
        """Погасить таймеры: бот выключается."""
        for one in list(self._raids.values()):
            if one.timer is not None:
                one.timer.cancel()
        self._raids.clear()
        self._busy.clear()


__all__ = ["Robbery", "RobberyError", "RobberyService"]
