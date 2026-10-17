"""Живой штурм точки: сбор отряда, волны и кнопки.

Это вторая половина войны за территории. Первая — `bot.turf_service` —
знает, кому точка принадлежит, сколько она приносит и кто её защищает;
здесь то, что происходит между «пошли» и «взяли».

**Осада живёт в памяти, а не в базе.** Как рейд и как групповой бой: она
кончается за один присест, и хранить её негде — упал процесс, и осада
пропала вместе с ним. Точка при этом не пострадает: пока бой не сведён,
в базе ничего не менялось.

**Точку берут ногами.** Созвать отряд можно только стоя у самого дома, и
записаться в чужой сбор — тоже. Иначе карта войны стала бы пультом:
выбрал строку в особняке, нажал — и взял казино через весь город. Дорога
на время сбора и штурма запирается: `is_busy` отвечает на вопрос дороги,
можно ли уходить.

**Сбор идёт в мини-аппе, а не в чате.** У рейда лобби живёт сообщением в
ветке группы, потому что рейд собирают в группе. Семью собирают у самой
точки, и ветки у неё нет: лобби здесь — это экран, на который смотрят
все, кто дошёл.

**Гарнизон бьёт наугад.** Защитник не обязан сидеть в сети: его людей
ведёт бросок, а не рука. Это и есть цена обороны — зато и потерять точку
во сне нельзя так просто: гарнизон дерётся настоящими характеристиками
своих бойцов.

**Волна — по разу на каждого.** Номер волны идёт в движок усталости, и
сквозной счётчик разменов тут не годится: он рос бы вдесятеро быстрее в
большом отряде. Ту же беду мы уже ловили в рейде.

Правила — в `bot.game.turf`, владение и доход — в `bot.turf_service`,
экран — в `bot.webapp.turf`.
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
from bot.game.classes import Zone, block_combo
from bot.game.combat import Action, Fighter, random_action, resolve_round
from bot.game.fightlog import turn_payload
from bot.game.health import now_ts
from bot.game.narrator import strike_lines
from bot.game.turf import (
    CLAN,
    MAX_GARRISON,
    MAX_SPOTS_PER_OWNER,
    Owner,
    Spot,
    calm_left,
    get_spot,
)
from bot.models import Player
from bot.raid_service import Choice
from bot.turf_service import (
    MAX_WAVES,
    TurfError,
    garrison,
    hand_over,
    owner_of,
    owner_title,
    survey,
)

logger = logging.getLogger(__name__)

# Сколько секунд лобби ждёт, прежде чем раствориться само. Семья
# собирается не в чате, и напомнить о себе ей нечем — кроме срока
LOBBY_SECONDS = 180
# Сколько у бойца есть на свой ход. Промолчал — волна уходит без него
TURN_SECONDS = 45


@dataclass
class Muster:
    """Сбор отряда: кто созвал, куда идут и кто уже записался."""

    id: int
    spot: Spot
    owner: Owner
    leader: int
    members: dict[int, str] = field(default_factory=dict)
    opened_at: float = field(default_factory=time.monotonic)
    timer: asyncio.Task | None = None

    @property
    def full(self) -> bool:
        return len(self.members) >= MAX_GARRISON

    def seconds_left(self, timeout: int = LOBBY_SECONDS) -> int:
        return max(0, round(timeout - (time.monotonic() - self.opened_at)))


@dataclass
class Siege:
    """Идущий штурм: отряд, гарнизон и текущая волна."""

    id: int
    spot: Spot
    attacker: Owner
    defender: Owner
    party: dict[int, Fighter]
    wall: dict[int, Fighter]
    defender_title: str = ""
    wave: int = 0
    turn_number: int = 0
    choices: dict[int, Choice] = field(default_factory=dict)
    acted: set[int] = field(default_factory=set)
    aim: dict[int, int] = field(default_factory=dict)
    said: list[str] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    finished: bool = False
    won: bool = False
    # Сколько прежнему владельцу накапало за неполные сутки
    handed: int = 0
    # Сколько выставленных на точке полегло насовсем
    fallen: int = 0
    timer: asyncio.Task | None = None
    started_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def order(self) -> list[int]:
        return list(self.wall)

    @property
    def alive_ids(self) -> list[int]:
        return [one for one, who in self.party.items() if who.alive]

    @property
    def standing(self) -> list[int]:
        return [one for one, who in self.wall.items() if who.alive]

    @property
    def wave_over(self) -> bool:
        """Отработали ли все живые в этой волне."""
        return all(one in self.acted for one in self.alive_ids)

    def choice_of(self, user_id: int) -> Choice:
        return self.choices.setdefault(user_id, Choice())

    def foe_of(self, user_id: int) -> Fighter | None:
        number = self.aim.get(user_id)
        return None if number is None else self.wall.get(number)

    def take_aim(self) -> None:
        """Навести каждого живого на живого защитника.

        Тот, чья цель ещё стоит, остаётся на ней: отряд не скачет по
        гарнизону, а доламывает начатое.
        """
        standing = self.standing
        if not standing:
            return
        for user_id in self.alive_ids:
            number = self.aim.get(user_id)
            if number is None or number not in standing:
                self.aim[user_id] = standing[0]

    def move_on(self, user_id: int) -> None:
        """Перевести бойца на следующего защитника по кругу."""
        standing = self.standing
        if not standing:
            self.aim.pop(user_id, None)
            return
        number = self.aim.get(user_id)
        if number not in standing:
            self.aim[user_id] = standing[0]
            return
        at = standing.index(number)
        self.aim[user_id] = standing[(at + 1) % len(standing)]

    def seconds_left(self, timeout: int = TURN_SECONDS) -> int:
        return max(0, round(timeout - (time.monotonic() - self.started_at)))


class TurfWar:
    """Служба живых штурмов: сборы, волны и исход."""

    def __init__(
        self,
        db: Database,
        rng: random.Random | None = None,
        turn_seconds: int = TURN_SECONDS,
        lobby_seconds: int = LOBBY_SECONDS,
    ) -> None:
        self.db = db
        self.rng = rng or random.Random()
        self.turn_seconds = turn_seconds
        self.lobby_seconds = lobby_seconds
        self._ids = itertools.count(1)
        self._musters: dict[int, Muster] = {}
        self._sieges: dict[int, Siege] = {}
        # Чем боец сейчас занят: сбором или боем. Двух осад на человека
        # не бывает — ни своих, ни чужих
        self._busy: dict[int, int] = {}
        # Исход, который боец ещё не закрыл: панель показывает его, пока
        # не нажмут «Понятно»
        self._results: dict[int, Siege] = {}

    # ---------- кто где ----------

    def muster_of_user(self, user_id: int) -> Muster | None:
        one = self._musters.get(self._busy.get(user_id, 0))
        return one if one is not None else None

    def siege_of_user(self, user_id: int) -> Siege | None:
        one = self._sieges.get(self._busy.get(user_id, 0))
        return one if one is not None else None

    def result_of_user(self, user_id: int) -> Siege | None:
        return self._results.get(user_id)

    def forget_result(self, user_id: int) -> None:
        self._results.pop(user_id, None)

    def is_busy(self, user_id: int) -> bool:
        """Занят ли боец: этим дорога и решает, отпускать ли его.

        Точку берут ногами, и уйти из-под неё посреди сбора или волны
        нельзя — иначе отряд собирался бы у дома, а дрался из кармана.
        """
        return user_id in self._busy

    def musters(self) -> list[Muster]:
        return list(self._musters.values())

    def sieges(self) -> list[Siege]:
        return list(self._sieges.values())

    def muster_at(self, code: str) -> Muster | None:
        """Идёт ли сбор на эту точку. Двух сборов на точку не бывает."""
        return next(
            (one for one in self._musters.values() if one.spot.code == code), None
        )

    def siege_at(self, code: str) -> Siege | None:
        return next(
            (one for one in self._sieges.values() if one.spot.code == code), None
        )

    # ---------- сбор ----------

    async def open_muster(
        self, player: Player, code: str, now: int | None = None
    ) -> Muster:
        """Созвать отряд на точку. Созвавший записан первым."""
        moment = now_ts() if now is None else now
        spot = get_spot(code)
        if spot is None:
            raise TurfError("Такой точки в городе нет.")
        mine = owner_of(player)
        if mine.nobody:
            raise TurfError(
                "За территории воюют семьи и службы. Вступи в клан или иди "
                "на службу."
            )
        if self._busy.get(player.user_id):
            raise TurfError("Ты уже в деле: сперва закончи его.")
        # Точку берут ногами. Созвать отряд на казино, сидя в особняке,
        # нельзя: карта войны на то и карта, чтобы по ней ходить
        if player.where(moment) != code:
            raise TurfError(
                f"До {spot.genitive} надо дойти: отряд собирают на месте."
            )
        if self.muster_at(code) is not None or self.siege_at(code) is not None:
            raise TurfError(f"За {spot.genitive} уже дерутся. Подожди.")

        rows = await survey(self.db, moment)
        here = next(one for one in rows if one.spot.code == code)
        if here.owner.same(mine):
            raise TurfError(f"{spot.title} и так за вами.")
        rest = calm_left(here.fought_at, moment)
        if rest:
            raise TurfError(
                f"За {spot.genitive} недавно дрались — точка отдыхает ещё "
                f"{max(1, rest // 3600)} ч."
            )
        # Потолок в четыре точки считается здесь, а не на развязке:
        # собрать отряд, дойти и услышать «мест нет» — издевательство.
        #
        # Считаем вместе с теми точками, за которые эта же сторона дерётся
        # прямо сейчас. Без этого потолок обходился в одну минуту: четыре
        # отряда, созванных разом, все проходили проверку по базе — в ней
        # ещё ничего не записано, — и семья брала весь город
        # Потолок — забота семей. Город охраняет сколько угодно точек:
        # он их не присваивает, а патрулирует, и ограничивать его числом
        # значило бы сказать, что часть города ему не положена
        held = [one for one in rows if one.owner.same(mine)] if mine.kind == CLAN else []
        coming = [
            one for one in self._sieges.values() if one.attacker.same(mine)
        ] + [
            one for one in self._musters.values() if one.owner.same(mine)
        ] if mine.kind == CLAN else []
        if mine.kind == CLAN and len(held) + len(coming) >= MAX_SPOTS_PER_OWNER:
            raise TurfError(
                f"Больше {MAX_SPOTS_PER_OWNER} точек разом не удержать. "
                "Сперва отдайте какую-нибудь."
            )
        if not player.can_fight():
            raise TurfError("Сперва подлечись: в таком виде на штурм не ходят.")

        muster = Muster(
            id=next(self._ids),
            spot=spot,
            owner=mine,
            leader=player.user_id,
            members={player.user_id: player.nickname},
        )
        self._musters[muster.id] = muster
        self._busy[player.user_id] = muster.id
        muster.timer = asyncio.create_task(self._muster_timer(muster))
        logger.info(
            "Сбор на точку %s созвал боец %s", code, player.user_id
        )
        return muster

    async def join(self, muster_id: int, player: Player) -> Muster:
        """Записаться в отряд. Только к своим."""
        muster = self._musters.get(muster_id)
        if muster is None:
            raise TurfError("Этот сбор уже закончился.")
        if player.user_id in muster.members:
            return muster
        if self._busy.get(player.user_id):
            raise TurfError("Ты уже в деле: сперва закончи его.")
        if not owner_of(player).same(muster.owner):
            raise TurfError("Это чужой сбор — к ним не ходят.")
        if player.where() != muster.spot.code:
            raise TurfError(
                f"Отряд стоит у {muster.spot.genitive} — туда и приходи."
            )
        if muster.full:
            raise TurfError(f"В отряде мест нет: больше {MAX_GARRISON} не берут.")
        if not player.can_fight():
            raise TurfError("Сперва подлечись: в таком виде на штурм не ходят.")
        muster.members[player.user_id] = player.nickname
        self._busy[player.user_id] = muster.id
        return muster

    async def leave(self, muster_id: int, user_id: int) -> None:
        """Выйти из отряда. Ушёл созвавший — сбор расходится."""
        muster = self._musters.get(muster_id)
        if muster is None:
            return
        muster.members.pop(user_id, None)
        self._busy.pop(user_id, None)
        if user_id == muster.leader or not muster.members:
            self._drop_muster(muster)

    def _drop_muster(self, muster: Muster) -> None:
        if muster.timer is not None:
            muster.timer.cancel()
        self._musters.pop(muster.id, None)
        for one in muster.members:
            if self._busy.get(one) == muster.id:
                self._busy.pop(one, None)

    async def _muster_timer(self, muster: Muster) -> None:
        """Сбор не ждёт вечно: сроку три минуты, и он расходится сам."""
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(self.lobby_seconds)
            if self._musters.get(muster.id) is muster:
                self._drop_muster(muster)

    # ---------- выход на штурм ----------

    async def march(
        self, muster_id: int, user_id: int, now: int | None = None
    ) -> Siege:
        """Вести отряд. Слово созвавшего — ждать полного состава не надо."""
        moment = now_ts() if now is None else now
        muster = self._musters.get(muster_id)
        if muster is None:
            raise TurfError("Этот сбор уже закончился.")
        if user_id != muster.leader:
            raise TurfError("Вести отряд — дело того, кто его созвал.")

        # На штурм идут те, кто стоит у дома и держится на ногах.
        # Ушедшего или добитого отряд не ждёт: он записывался, а не шёл
        party: dict[int, Fighter] = {}
        for one in muster.members:
            player = await self.db.get_player(one)
            if player is None or not player.can_fight():
                continue
            if player.where(moment) != muster.spot.code:
                continue
            party[one] = Fighter.from_player(player, armed=True)
        if not party:
            self._drop_muster(muster)
            raise TurfError("Отряд разбежался — штурм отменён.")

        rows = await survey(self.db, moment)
        here = next(one for one in rows if one.spot.code == muster.spot.code)
        wall = await garrison(
            self.db, here.owner, [one.level for one in party.values()], moment,
            code=muster.spot.code,
        )
        siege = Siege(
            id=next(self._ids),
            spot=muster.spot,
            attacker=muster.owner,
            defender=here.owner,
            party=party,
            # Номера защитников отрицательные: так их не спутать с
            # бойцами отряда ни в прицеле, ни в журнале
            wall={-(number + 1): one for number, one in enumerate(wall)},
            defender_title=await owner_title(self.db, here.owner),
        )
        self._drop_muster(muster)
        self._sieges[siege.id] = siege
        for one in party:
            self._busy[one] = siege.id
        logger.info(
            "Штурм точки %s: отряд %s против гарнизона %s",
            siege.spot.code, len(party), len(wall),
        )
        if not wall:
            # Защищать некому: семья взяла точку и не выставила на неё
            # никого. Драться не с кем — отряд просто входит и забирает
            await self._finish(siege, won=True)
            return siege
        self._open_wave(siege)
        return siege

    def _open_wave(self, siege: Siege) -> None:
        """Начать волну: навести прицелы и дать всем право хода."""
        siege.wave += 1
        siege.acted = set()
        siege.choices = {}
        siege.said = []
        siege.started_at = time.monotonic()
        siege.take_aim()
        if siege.timer is not None:
            siege.timer.cancel()
        siege.timer = asyncio.create_task(self._wave_timer(siege, siege.wave))

    async def _wave_timer(self, siege: Siege, wave: int) -> None:
        """Молчуны пропускают ход, и волна идёт дальше без них."""
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(self.turn_seconds)
            if self._sieges.get(siege.id) is not siege or siege.wave != wave:
                return
            async with siege.lock:
                if siege.finished or siege.wave != wave:
                    return
                for user_id in list(siege.alive_ids):
                    if user_id not in siege.acted:
                        self._exchange(siege, user_id, Action())
                await self._close_wave(siege)

    # ---------- кнопки ----------

    async def choose(
        self, siege_id: int, user_id: int, action: str, zone_value: str,
        hand: int = 0,
    ) -> str:
        """Нажатие бойца. Выбрал и удар, и блок — размен считается сразу."""
        siege = self._sieges.get(siege_id)
        if siege is None:
            raise TurfError("Этот штурм уже закончился.")
        fighter = siege.party.get(user_id)
        if fighter is None:
            raise TurfError("Ты не в этом отряде. Болей за своих.")
        if not fighter.alive:
            raise TurfError("Тебя уже вынесли — смотри со стороны.")
        if user_id in siege.acted:
            raise TurfError("В этой волне ты уже отработал. Жди следующую.")
        if action not in {"attack", "block"}:
            raise TurfError("Непонятное действие.")
        try:
            zone = Zone(zone_value)
        except ValueError as error:
            raise TurfError("Эта кнопка уже не работает.") from error

        async with siege.lock:
            if user_id in siege.acted:
                raise TurfError("В этой волне ты уже отработал.")
            choice = siege.choice_of(user_id)
            if action == "attack":
                choice.attacks[hand] = zone
            else:
                choice.block = block_combo(zone, fighter.block_width)
            if not choice.ready_for(fighter.attacks_per_round):
                return "Выбери и удар, и блок."
            self._exchange(
                siege, user_id, choice.to_action(fighter.attacks_per_round)
            )
            if self._judge(siege) is not None or siege.wave_over:
                await self._close_wave(siege)
        return "Ход принят."

    async def use_ability(self, siege_id: int, user_id: int, code: str) -> str:
        """Нажать приём: заготовка ложится и ждёт своего момента."""
        siege = self._sieges.get(siege_id)
        if siege is None:
            raise TurfError("Этот штурм уже закончился.")
        fighter = siege.party.get(user_id)
        if fighter is None or not fighter.alive:
            raise TurfError("Приёмы применяет тот, кто стоит на ногах.")
        try:
            charge = fighter.use(code)
        except ValueError as error:
            raise TurfError(str(error)) from error
        return f"{charge.ability.title} наготове."

    # ---------- размен ----------

    def _exchange(self, siege: Siege, user_id: int, action: Action) -> None:
        """Один размен: боец против своего защитника. Тот бьёт наугад."""
        fighter = siege.party[user_id]
        siege.acted.add(user_id)
        number = siege.aim.get(user_id)
        foe = siege.wall.get(number) if number is not None else None
        if foe is None or not foe.alive:
            # Защитника не осталось: бить в воздух — не размен
            return
        siege.turn_number += 1
        result = resolve_round(
            fighter,
            action,
            foe,
            random_action(foe, self.rng),
            siege.wave,
            self.rng,
            # Усталость растянута на длину штурма, а не дуэли
            limit=MAX_WAVES,
        )
        said = strike_lines(
            result, {user_id: fighter, number: foe}, self.rng
        )
        siege.said.extend(said)
        siege.rounds.append(turn_payload(result, said))
        if not foe.alive:
            siege.move_on(user_id)

    def _judge(self, siege: Siege) -> bool | None:
        """Кончился ли штурм. None — дерёмся дальше."""
        if not siege.standing:
            return True
        if not siege.alive_ids:
            return False
        return None

    async def _close_wave(self, siege: Siege) -> None:
        """Свести волну: либо исход, либо следующая."""
        done = self._judge(siege)
        if done is None and siege.wave >= MAX_WAVES:
            # Сорок волн — и судья разводит стороны: точка остаётся за
            # своими. Без этого штурм двух полных отрядов шёл бы вечно:
            # усталость замедлена впятеро и сама его не кончит
            done = False
        if done is None:
            self._open_wave(siege)
            return
        await self._finish(siege, won=done)

    async def _finish(self, siege: Siege, won: bool) -> None:
        """Свести штурм: записать точку и разложить итог по бойцам."""
        if siege.finished:  # pragma: no cover - две развязки разом
            return
        siege.finished = True
        siege.won = won
        if siege.timer is not None:
            siege.timer.cancel()
        moment = now_ts()
        if won:
            # Прежнего владельца рассчитываем за неполные сутки: своего
            # следующего расчёта у него уже не будет
            row = await self.db.turf_row(siege.spot.code)
            if row is not None:
                # Считаем до того, как выставленные полягут: платят
                # только за то время, что точку охраняли
                siege.handed = await hand_over(
                    self.db, siege.spot, siege.defender, row.paid_until,
                    moment,
                    guarded=bool(await self.db.hirelings_at(siege.spot.code)),
                )
            # Отбили точку — выставленные на ней гибнут насовсем
            siege.fallen = await self.db.clear_spot(siege.spot.code)
            await self.db.take_turf(
                siege.spot.code,
                siege.attacker.kind,
                siege.attacker.key,
                moment,
            )
        else:
            await self.db.hold_turf(siege.spot.code, moment)
        siege.summary = [
            # Волн ноль — драки не было вовсе: точка стояла пустая
            f"{siege.spot.title} взят без боя: охраны на нём не было."
            if won and not siege.wave
            else f"{siege.spot.title} взят за {siege.wave} волн."
            if won
            else f"{siege.spot.title} отбит: гарнизон устоял.",
            f"Держал точку: {siege.defender_title}.",
        ]
        if siege.fallen:
            siege.summary.append(
                f"Выставленных полегло: {siege.fallen}. Насовсем."
            )
        if siege.handed:
            siege.summary.append(
                f"Прежним в казну ушло {siege.handed} 💰 за время владения."
            )
        if won:
            # Взятая точка стоит пустой: прежних выставленных вынесли, а
            # своих ещё нет. Без этой строки про охрану узнают через
            # сутки, по пустой казне
            siege.summary.append(
                "Точка пустая и дохода не даёт: выставь на ней человека."
            )
        self._sieges.pop(siege.id, None)
        for user_id in siege.party:
            self._busy.pop(user_id, None)
            self._results[user_id] = siege
        logger.info(
            "Штурм точки %s кончен: %s за %s волн",
            siege.spot.code, "взяли" if won else "отбили", siege.wave,
        )

    async def close(self) -> None:
        """Погасить таймеры: бот выключается."""
        for one in list(self._musters.values()):
            if one.timer is not None:
                one.timer.cancel()
        for one in list(self._sieges.values()):
            if one.timer is not None:
                one.timer.cancel()


__all__ = [
    "LOBBY_SECONDS",
    "TURN_SECONDS",
    "Muster",
    "Siege",
    "TurfWar",
]
