"""«Чёрный пуск» в действии: инстанс рейда, отряд, шаги и бои.

Сервер — единственный источник правды. Клиент не присылает ни победу,
ни урон, ни «я пришёл»: он нажимает кнопку, а всё остальное считает
этот модуль.

**Состояние живёт в базе, а не в памяти процесса.** Рейд идёт два часа
с лишним, за это время бот перезапускается, сеть рвётся, игроки уходят
и возвращаются. Поэтому ни одного `asyncio`-таймера здесь нет:
переходы, лимит рейда и бонус скорости записаны датами, а считаются в
тот миг, когда кто-нибудь посмотрел. Это тот же ленивый сбор, которым в
клубе сводятся недели и копилки.

**Шаг — это дата, а не ожидание.** У игрока хранится `departed_at` и
`arrive_at = departed_at + 10 с`. Повторное нажатие не ускоряет шаг и
не создаёт второй: пока идёт один, других команд движения нет.

**Команда идемпотентна.** У каждой есть `command_id`; повторная
возвращает прежний ответ и ничего не делает второй раз. Всё состояние
инстанса пишется одним json под проверкой версии, поэтому две команды
не могут выдать предмет дважды или открыть два боя.

Правила и карта — в `bot.game.blackout`, экран — в `bot.webapp.blackout`.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from bot.database import Database
from bot.game.blackout import (
    ARENA_ACTIONS,
    BOSSES,
    BOSS_FORCE,
    BOSS_LEVEL,
    BOSS_REINFORCEMENTS,
    CLEARANCE,
    CLEAR_HEAL,
    CONSOLE_HEALTH,
    CONSOLE_HIT,
    COOLING,
    CONTROL_IMMUNE_TURNS,
    DEFENSE_WAVES,
    DEFENSE_WAVE_TITLES,
    DRAINAGE,
    EVACUATION_PLAN,
    EXTRACTED,
    FLAG_TITLES,
    FLOODLIGHTS,
    FOE_BUILD,
    FOE_LEVEL,
    ITEM_ART,
    ITEM_SHAPES,
    ITEM_TITLES,
    MAX_PARTY,
    MIN_PARTY,
    POWER_CORE,
    QUEST_ITEMS,
    RELAY_A,
    RELAY_B,
    RESTART_KEY,
    RESTARTED,
    REINFORCEMENT_SIZE,
    RUN_LIMIT_MINUTES,
    SPEED_BONUS_MINUTES,
    SQUAD_SIZE,
    STAGE_EXTRACT,
    STAGE_RESTART,
    STAGE_SPEED,
    SYNCHRONIZER,
    VALVE_A,
    VALVE_B,
    WINDOW_TURNS,
    boss_hp,
    art_url,
    extra_windows,
    door_open,
    reward_for,
    station,
)
from bot.game.blackout import BEST_PARTY, FIGHTER_ART, group_size
from bot.game.blackout import CYCLE_DAYS, CYCLE_HOUR, CYCLE_MINUTE
from bot.game.blackout import ENTRY_WINDOW_MINUTES
from bot.game.classes import FIGHTER_CLASSES, Zone, block_combo
from bot.game.clock import MOSCOW
from bot.game.abilities import Charge, MAX_ENERGY
from bot.game.combat import Action, Fighter, group_aftermath, heal_line
from bot.game.combat import resolve_round, spread_heal
from bot.game.health import now_ts
from bot.game.reference import developed_stats
from bot.models import Player

logger = logging.getLogger(__name__)

# Сколько молчит игрок, прежде чем за него начинает защищаться сервер.
# Две минуты — те же, после которых лидерство уходит активному
AFK_SECONDS = 120
# Сколько ходов отпущено бою, прежде чем судья считает очки
MAX_FIGHT_TURNS = 60


class BlackoutError(Exception):
    """Отказ по рейду, который показывают игроку как есть."""


# ---------- цикл ----------


def cycle_code(moment: int) -> str:
    """К какому циклу относится этот час. Код — дата открытия окна.

    Окно открывается во вторник и в пятницу в девятнадцать по Москве и
    держится три часа. Считается по календарю, а не хранится: по любому
    часу видно, к какому запуску он относится.
    """
    here = datetime.fromtimestamp(moment, MOSCOW)
    for back in range(0, 8):
        day = (here - timedelta(days=back)).date()
        if day.weekday() not in CYCLE_DAYS:
            continue
        opens = datetime.combine(
            day, datetime.min.time().replace(hour=CYCLE_HOUR, minute=CYCLE_MINUTE),
            MOSCOW,
        )
        if opens.timestamp() <= moment:
            return day.isoformat()
    return here.date().isoformat()


def window_open(moment: int) -> bool:
    """Открыто ли окно входа прямо сейчас.

    Сперва разовые окна: их открывают под плейтест и показ, и
    расписание города они не трогают.
    """
    for opens, closes in extra_windows():
        if opens <= moment < closes:
            return True
    code = cycle_code(moment)
    opens = datetime.fromisoformat(code)
    start = datetime.combine(
        opens.date(),
        datetime.min.time().replace(hour=CYCLE_HOUR, minute=CYCLE_MINUTE),
        MOSCOW,
    ).timestamp()
    return start <= moment < start + ENTRY_WINDOW_MINUTES * 60


def window_opens_at(moment: int) -> int:
    """Когда откроется следующее окно входа — по расписанию или разовое."""
    soon = [one for one, _ in extra_windows() if one > moment]
    here = datetime.fromtimestamp(moment, MOSCOW)
    for ahead in range(0, 9):
        day = (here + timedelta(days=ahead)).date()
        if day.weekday() not in CYCLE_DAYS:
            continue
        opens = datetime.combine(
            day,
            datetime.min.time().replace(hour=CYCLE_HOUR, minute=CYCLE_MINUTE),
            MOSCOW,
        ).timestamp()
        if opens > moment:
            return min([int(opens)] + soon)
    return min([moment] + soon)


# ---------- состояние ----------


def blank_state(cycle: str, leader_id: int, now: int) -> dict[str, Any]:
    """Пустой инстанс: отряд собран, но станция ещё не началась."""
    return {
        "cycle": cycle,
        "leader": leader_id,
        "party": 0,
        "opened_at": now,
        "started_at": 0,
        "ends_at": 0,
        "restarted_at": 0,
        "outcome": "",
        "seed": random.randrange(1, 2**31),
        "flags": {},
        "seen": {},
        "cleared": {},
        "safe": station().start,
        "members": {},
        "fight": None,
        "console": {},
        "log": [],
        "warned": [],
    }


def member_row(player: Player, now: int, bot: bool = False) -> dict[str, Any]:
    """Строка участника: где стоит, сколько здоровья и жив ли."""
    fighter = Fighter.from_player(player)
    return {
        "name": player.nickname,
        "level": player.level,
        "class": player.class_code,
        "room": station().start,
        "from": "",
        "to": "",
        "departed": 0,
        "arrive": 0,
        "hp": fighter.hp,
        "max": fighter.max_hp,
        "down": False,
        "raised": False,
        "seen": now,
        "left": 0,
        "extracted": 0,
        "bot": bot,
        # Приёмы: шкала и заготовки живут внутри боя, но между
        # разменами боец собирается заново — значит, их надо помнить
        # тут, иначе каждое нажатие начиналось бы с нуля
        "energy": 0,
        "charges": [],
        "pressed": 0,
    }


def _restore_tricks(fighter: Fighter, row: dict) -> Fighter:
    """Вернуть бойцу шкалу энергии и нажатые заготовки из состояния.

    В клубе боец живёт в памяти весь бой, и шкала живёт вместе с ним. В
    подземелье бой идёт часами и через базу: боец собирается на каждый
    размен заново. Поэтому шкала и заготовки лежат в строке участника и
    возвращаются сюда — иначе приёмы в рейде на станцию были бы
    нажимаемы, но бесполезны.
    """
    from bot.content.abilities import CATALOGUE

    fighter.energy = max(0, min(MAX_ENERGY, int(row.get("energy") or 0)))
    fighter.pressed = max(0, int(row.get("pressed") or 0))
    fighter.charges = [
        Charge(
            ability=CATALOGUE[code],
            tier=fighter.loadout.slots.get(code, CATALOGUE[code].tier),
        )
        for code in (row.get("charges") or [])
        if code in CATALOGUE
    ]
    return fighter


def _keep_tricks(row: dict, fighter: Fighter) -> None:
    """Сложить шкалу и заготовки обратно в строку участника."""
    row["energy"] = int(fighter.energy)
    row["pressed"] = int(fighter.pressed)
    row["charges"] = [charge.ability.code for charge in fighter.charges]


def _forget_tricks(row: dict) -> None:
    """Бой кончился — шкала гаснет. Энергию между комнатами не носят."""
    row["energy"] = 0
    row["pressed"] = 0
    row["charges"] = []


def _note(state: dict, text: str, now: int) -> None:
    """Строка в журнал рейда. Журнал короткий: он читается, а не копится."""
    log = state.setdefault("log", [])
    log.append({"at": now, "text": text})
    del log[:-120]


def flags_of(state: dict) -> set[str]:
    return set(state.get("flags") or {})


def has(state: dict, flag: str) -> bool:
    return flag in (state.get("flags") or {})


def raise_flag(state: dict, flag: str, now: int) -> bool:
    """Поднять флаг. False — он уже поднят, и второй раз ничего не будет."""
    flags = state.setdefault("flags", {})
    if flag in flags:
        return False
    flags[flag] = now
    return True


def member(state: dict, user_id: int) -> dict[str, Any] | None:
    return (state.get("members") or {}).get(str(user_id))


def living(state: dict) -> list[str]:
    """Кто ещё в строю: не упал, не вышел и не эвакуировался."""
    return [
        key
        for key, one in (state.get("members") or {}).items()
        if not one["down"] and not one["left"] and not one["extracted"]
    ]


# ---------- замок инстанса ----------
#
# Всякая команда читает рейд целиком, меняет json и пишет обратно под
# проверкой версии. Разошлась версия — значит, кто-то походил первым, и
# команда считается заново на свежем состоянии. Две попытки: третья
# означала бы не гонку, а очередь, и её лучше честно отклонить.


@dataclass
class Run:
    """Инстанс рейда: строка базы вместе с разобранным состоянием."""

    id: int
    cycle: str
    phase: str
    leader_id: int
    version: int
    started_at: int
    state: dict[str, Any]

    @property
    def over(self) -> bool:
        return self.phase == "over"


async def read_run(db: Database, run_id: int) -> Run:
    row = await db.blackout_read(run_id)
    if row is None:
        raise BlackoutError("Такого рейда нет.")
    return Run(
        id=int(row["id"]),
        cycle=str(row["cycle"]),
        phase=str(row["phase"]),
        leader_id=int(row["leader_id"]),
        version=int(row["version"]),
        started_at=int(row["started_at"]),
        state=json.loads(str(row["state"]) or "{}"),
    )


async def run_of(db: Database, user_id: int) -> Run | None:
    """Рейд этого бойца. None — он ни в каком."""
    run_id = await db.blackout_of(user_id)
    if not run_id:
        return None
    return await read_run(db, run_id)


async def command(
    db: Database,
    run_id: int,
    user_id: int,
    change: Callable[[Run, int], str],
    command_id: str = "",
    now: int | None = None,
) -> str:
    """Выполнить команду над рейдом: прочитать, изменить, записать.

    Повторная команда с тем же `command_id` отдаёт прежний ответ и
    ничего не делает: сеть теряет ответы, и клиент жмёт ещё раз.
    """
    moment = now_ts() if now is None else now
    said = await db.blackout_done(run_id, command_id)
    if said is not None:
        return said
    for _ in range(2):
        run = await read_run(db, run_id)
        if member(run.state, user_id) is None:
            raise BlackoutError("Ты не в этом рейде.")
        settle(run, moment)
        answer = change(run, moment)
        phase = "over" if run.state.get("outcome") else run.phase
        if await db.blackout_write(
            run.id, run.version, phase, json.dumps(run.state), moment
        ):
            await db.blackout_remember(run_id, command_id, answer, moment)
            return answer
    raise BlackoutError("Рейд сейчас занят другой командой. Повтори.")


async def look(db: Database, run_id: int, now: int | None = None) -> Run:
    """Прочитать рейд и досчитать всё, что должно было случиться само.

    Сюда приходит каждый экран: прибытия, предупреждения и конец
    времени случаются при взгляде, а не по будильнику.
    """
    moment = now_ts() if now is None else now
    for _ in range(2):
        run = await read_run(db, run_id)
        before = json.dumps(run.state, sort_keys=True)
        settle(run, moment)
        after = json.dumps(run.state, sort_keys=True)
        if before == after:
            return run
        phase = "over" if run.state.get("outcome") else run.phase
        if await db.blackout_write(
            run.id, run.version, phase, after, moment
        ):
            run.version += 1
            run.phase = phase
            return run
    return await read_run(db, run_id)


# ---------- ленивый сбор ----------


def settle(run: Run, now: int) -> None:
    """Досчитать всё, что должно было случиться само к этому часу.

    Три вещи и ровно в этом порядке: кто дошёл, не вышло ли время и
    не пора ли предупредить. Порядок важен — дошедший в последнюю
    секунду всё-таки дошёл.
    """
    state = run.state
    if state.get("outcome"):
        return
    arrived = False
    for one in (state.get("members") or {}).values():
        if one.get("arrive") and one["arrive"] <= now:
            # Дошёл: комната меняется ровно в срок и ровно один раз
            one["room"] = one["to"] or one["room"]
            one["from"] = ""
            one["to"] = ""
            one["departed"] = 0
            one["arrive"] = 0
            arrived = True
    if arrived:
        _see_here(state, now)
        _meet_here(state, now)
    _check_time(run, now)


def _meet_here(state: dict, now: int) -> None:
    """Пришли в занятую комнату — бой начинается сам.

    Иначе отряд стоял бы посреди зомби и ждал кнопки «подраться», а
    выходы были бы заперты: занятый узел в обход не пройти.
    """
    if state.get("fight"):
        return
    for one in (state.get("members") or {}).values():
        if one["left"] or one["extracted"] or one["down"] or one["arrive"]:
            continue
        if open_fight(state, one["room"], now):
            return


def _see_here(state: dict, now: int) -> None:
    """Отметить комнаты, где стоит отряд: туман поднимается шагом."""
    seen = state.setdefault("seen", {})
    for one in (state.get("members") or {}).values():
        room = one["room"]
        if room and room not in seen:
            seen[room] = now


def _check_time(run: Run, now: int) -> None:
    """Лимит рейда и предупреждения перед ним."""
    state = run.state
    ends = int(state.get("ends_at") or 0)
    if not ends:
        return
    warned = state.setdefault("warned", [])
    for minutes in (15, 5):
        if ends - now <= minutes * 60 and minutes not in warned:
            warned.append(minutes)
            _note(state, f"До конца рейда {minutes} мин.", now)
    if now >= ends:
        state["outcome"] = state.get("outcome") or "timeout"
        state["fight"] = None
        _note(state, "Время рейда вышло. Станция закрыта.", now)


# ---------- лобби ----------


async def open_run(
    db: Database, leader: Player, now: int | None = None, test: bool = False
) -> Run:
    """Объявить рейд. Отряд собирается, пока лидер не скажет «идём».

    `test` — локальный разбор: окно входа не спрашивается и состав
    добивается ботами. В городе он закрыт, и это проверяет сервер.
    """
    moment = now_ts() if now is None else now
    if await db.blackout_of(leader.user_id):
        raise BlackoutError("Ты уже в рейде. Сперва закончи его.")
    if not test and not window_open(moment):
        opens = window_opens_at(moment)
        when = datetime.fromtimestamp(opens, MOSCOW).strftime("%d.%m %H:%M")
        raise BlackoutError(
            f"Окно входа закрыто. Следующее — {when} по Москве."
        )
    cycle = cycle_code(moment)
    state = blank_state(cycle, leader.user_id, moment)
    state["test"] = bool(test)
    state["members"][str(leader.user_id)] = member_row(leader, moment)
    _note(state, f"{leader.nickname} объявил рейд на «Контур».", moment)
    run_id = await db.blackout_open(
        cycle, leader.user_id, moment, json.dumps(state)
    )
    logger.info("Рейд «Чёрный пуск» %s открыт бойцом %s", run_id, leader.user_id)
    return await read_run(db, run_id)


async def join_run(
    db: Database, player: Player, run_id: int, now: int | None = None
) -> str:
    """Встать в отряд. Больше пяти в тоннели не лезут."""
    moment = now_ts() if now is None else now
    if await db.blackout_of(player.user_id):
        raise BlackoutError("Ты уже в рейде.")
    run = await read_run(db, run_id)
    if run.phase != "lobby":
        raise BlackoutError("Этот рейд уже ушёл на станцию.")
    if len(run.state["members"]) >= MAX_PARTY:
        raise BlackoutError(f"В отряде уже {MAX_PARTY} — больше не берут.")
    run.state["members"][str(player.user_id)] = member_row(player, moment)
    _note(run.state, f"{player.nickname} в отряде.", moment)
    if not await db.blackout_write(
        run.id, run.version, run.phase, json.dumps(run.state), moment
    ):
        raise BlackoutError("Отряд только что изменился. Повтори.")
    await db.blackout_join(run.id, player.user_id)
    return "Ты в отряде. Ждём лидера."


async def leave_run(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Выйти из отряда. Из начатого рейда выходят, а инстанс остаётся."""
    moment = now_ts() if now is None else now
    run = await run_of(db, player.user_id)
    if run is None:
        raise BlackoutError("Ты не в рейде.")
    key = str(player.user_id)
    if run.phase == "lobby":
        run.state["members"].pop(key, None)
        if not run.state["members"]:
            run.state["outcome"] = "abandoned"
    else:
        one = run.state["members"].get(key)
        if one:
            one["left"] = moment
    _note(run.state, f"{player.nickname} вышел из отряда.", moment)
    _pass_leadership(run.state, moment)
    phase = "over" if run.state.get("outcome") else run.phase
    await db.blackout_write(
        run.id, run.version, phase, json.dumps(run.state), moment
    )
    await db.blackout_part(run.id, player.user_id, moment)
    return "Ты вышел из отряда."


def _pass_leadership(state: dict, now: int) -> None:
    """Лидерство уходит активному: две минуты молчания — и оно чужое."""
    leader = str(state.get("leader") or 0)
    rows = state.get("members") or {}
    head = rows.get(leader)
    if head and not head["left"] and now - head["seen"] <= AFK_SECONDS:
        return
    for key, one in rows.items():
        if one["left"] or one["extracted"] or one.get("bot"):
            continue
        if now - one["seen"] <= AFK_SECONDS:
            if str(state.get("leader")) != key:
                state["leader"] = int(key)
                _note(state, f"{one['name']} ведёт отряд.", now)
            return


async def add_bots(
    db: Database, run: Run, upto: int, now: int
) -> None:
    """Добить состав серверными союзниками — для локального разбора.

    Бот ходит с отрядом, защищается, поднимает упавших и жмёт кнопки
    на арене. Это не замена кооперативу: в городе ботов не дают.
    """
    number = 1
    while len(run.state["members"]) < upto:
        key = str(-number)
        run.state["members"][key] = {
            "name": f"Союзник-{number}",
            "level": FOE_LEVEL,
            "class": ("tank", "warrior", "assassin", "trickster")[number % 4],
            "room": run.state["safe"],
            "from": "", "to": "", "departed": 0, "arrive": 0,
            "hp": 0, "max": 0, "down": False, "raised": False,
            "seen": now, "left": 0, "extracted": 0, "bot": True,
        }
        one = run.state["members"][key]
        fighter = _bot_fighter(key, one)
        one["hp"] = one["max"] = fighter.max_hp
        number += 1


def _bot_fighter(key: str, row: dict) -> Fighter:
    """Союзник сервера как боец: характеристики по уровню, без вещей."""
    fclass = FIGHTER_CLASSES[row.get("class") or "warrior"]
    level = int(row.get("level") or FOE_LEVEL)
    return Fighter(
        user_id=int(key),
        name=row["name"],
        fclass=fclass,
        stats=developed_stats(fclass, level),
        level=level,
    )


async def start_run(
    db: Database, player: Player, now: int | None = None, bots: int = 0
) -> str:
    """Выйти на станцию. С этой минуты идёт лимит рейда."""
    moment = now_ts() if now is None else now
    run = await run_of(db, player.user_id)
    if run is None:
        raise BlackoutError("Ты не в рейде.")
    if run.phase != "lobby":
        raise BlackoutError("Рейд уже идёт.")
    if run.state["leader"] != player.user_id:
        raise BlackoutError("Выводит отряд лидер.")
    if bots:
        await add_bots(db, run, min(MAX_PARTY, bots), moment)
    if len(run.state["members"]) < MIN_PARTY:
        raise BlackoutError(
            f"В отряде {len(run.state['members'])}; на станцию идут "
            f"от {MIN_PARTY}."
        )
    party = len(run.state["members"])
    run.state["party"] = party
    run.state["started_at"] = moment
    run.state["ends_at"] = moment + RUN_LIMIT_MINUTES * 60
    for one in run.state["members"].values():
        one["room"] = station().start
        one["seen"] = moment
    _see_here(run.state, moment)
    _note(
        run.state,
        f"Отряд на проходной. Состав {party}: группы и запас боссов "
        f"посчитаны на столько человек.",
        moment,
    )
    if not await db.blackout_write(
        run.id, run.version, "run", json.dumps(run.state), moment
    ):
        raise BlackoutError("Отряд только что изменился. Повтори.")
    await db.blackout_start(run.id, moment)
    logger.info("Рейд %s вышел на станцию: %s человек", run.id, party)
    return "Отряд на станции. Удачи."


# ---------- шаг ----------
#
# Шаг — это две даты. Сервер проверяет соседство, допуск, бой и то, что
# другого шага у игрока нет; дальше остаётся только ждать десять секунд,
# и ждёт их не процесс, а календарь.


def exits_of(state: dict, room: str) -> list[dict[str, Any]]:
    """Какие двери видно из этой комнаты и что с каждой не так."""
    place = station()
    flags = flags_of(state)
    out = []
    for door in place.doors_from(room):
        there = door.other(room)
        neighbour = place.room(there)
        if neighbour is None:  # pragma: no cover - карту проверяет валидатор
            continue
        why = door_open(door, flags)
        seen = there in (state.get("seen") or {})
        here = place.room(room)
        # Куда эта дверь ведёт на схеме. Джойстик толкают в сторону, а
        # не в список: по этому вектору и выбирается дверь
        dx = neighbour.x - (here.x if here else neighbour.x)
        dy = neighbour.y - (here.y if here else neighbour.y)
        out.append({
            "room": there,
            "dx": dx,
            "dy": dy,
            "where": _compass(dx, dy),
            # Непосещённую комнату называем дверью, а не именем: туман
            # показывает, что ход есть, но не что за ним
            "name": neighbour.name if seen else "Неизвестный проход",
            "layer": neighbour.layer,
            "seconds": door.seconds,
            "can_go": not why,
            "why": why,
            "seen": seen,
            "cleared": there in (state.get("cleared") or {}),
            "danger": bool(neighbour.encounter)
            and there not in (state.get("cleared") or {}),
        })
    return out


# Восемь румбов: столько различает палец на джойстике, и столько же
# дверей бывает у комнаты на схеме
_ROSE = (
    (0, "восток"), (45, "юго-восток"), (90, "юг"), (135, "юго-запад"),
    (180, "запад"), (225, "северо-запад"), (270, "север"),
    (315, "северо-восток"),
)


def _compass(dx: int, dy: int) -> str:
    """Куда эта дверь по схеме. Ось Y вниз, как в svg."""
    import math

    if not dx and not dy:
        return ""
    angle = math.degrees(math.atan2(dy, dx)) % 360
    best = min(_ROSE, key=lambda one: min(
        abs(angle - one[0]), 360 - abs(angle - one[0])
    ))
    return best[1]


def _locked_here(state: dict, room: str) -> str:
    """Почему из этой комнаты не выйти. Пусто — выходи."""
    fight = state.get("fight")
    if fight and fight.get("room") == room:
        return "Зомби в комнате: пока они стоят, все выходы закрыты."
    place = station()
    here = place.room(room)
    if here and here.encounter and room not in (state.get("cleared") or {}):
        return "Зомби в комнате: пока они стоят, все выходы закрыты."
    return ""


def step(run: Run, user_id: int, there: str, now: int) -> str:
    """Шагнуть в соседнюю комнату. Десять секунд, и ни одной меньше."""
    state = run.state
    one = member(state, user_id)
    if one is None:
        raise BlackoutError("Ты не в этом рейде.")
    if state.get("outcome"):
        raise BlackoutError("Рейд закончен.")
    if one["extracted"]:
        raise BlackoutError("Ты уже снаружи.")
    if one["arrive"]:
        raise BlackoutError("Ты уже в пути. Дойди сперва.")
    if one["down"]:
        raise BlackoutError("Ты не на ногах. Пусть поднимут.")
    here = one["room"]
    locked = _locked_here(state, here)
    if locked:
        raise BlackoutError(locked)
    door = station().door_between(here, there)
    if door is None:
        raise BlackoutError("Отсюда туда хода нет.")
    why = door_open(door, flags_of(state))
    if why:
        raise BlackoutError(why)
    one["from"] = here
    one["to"] = there
    one["departed"] = now
    one["arrive"] = now + door.seconds
    one["seen"] = now
    name = station().room(there)
    return f"Идёшь в «{name.name if name else there}»: {door.seconds} с."


def squad_step(run: Run, user_id: int, there: str, now: int) -> str:
    """Увести отряд разом: подтверждённая команда лидера.

    Из чужого боя человека не тянут: он остаётся, остальные идут.
    """
    state = run.state
    if state.get("leader") != user_id:
        raise BlackoutError("Отряд ведёт лидер.")
    leader = member(state, user_id)
    if leader is None:
        raise BlackoutError("Ты не в этом рейде.")
    said = step(run, user_id, there, now)
    moved = 1
    for key, one in (state.get("members") or {}).items():
        if key == str(user_id) or one["left"] or one["extracted"]:
            continue
        if one["down"] or one["arrive"] or one["room"] != leader["from"]:
            continue
        door = station().door_between(one["room"], there)
        if door is None or door_open(door, flags_of(state)):
            continue
        one["from"] = one["room"]
        one["to"] = there
        one["departed"] = now
        one["arrive"] = now + door.seconds
        moved += 1
    return f"{said} Отряд идёт следом: {moved}."


# ---------- бой ----------
#
# Бой тот же, что в клубе: удар по зоне, блок в две зоны, размен
# считает `bot.game.combat`. Сверху лежат механики боссов — телеграф за
# ход, окно уязвимости от действия на арене, щит и конечные подкрепления.
# Ни одна из них не требует ручного экшена: всё выражено статусами и
# доступными действиями.

FOE_BASE = -1000  # номера врагов идут вниз от этого: с игроками не спутать


def _zone(name: str) -> Zone | None:
    try:
        return Zone(name)
    except ValueError:
        return None


def foe_row(
    index: int, name: str, level: int, fclass: str, scale: float,
    boss: str = "", art: str = "", build: int = 0,
) -> dict[str, Any]:
    """Строка противника. Здоровье считается сразу: бой его только тратит.

    `build` — по какому уровню ему розданы характеристики. У обычного
    зомби он ниже своего: заражённый рабочий силён заразой, а не залом.
    """
    cls = FIGHTER_CLASSES[fclass]
    plain = Fighter(
        user_id=FOE_BASE - index,
        name=name,
        fclass=cls,
        stats=developed_stats(cls, build or level),
        level=level,
    )
    full = max(1, int(plain.max_hp * scale))
    return {
        "id": FOE_BASE - index,
        "name": name,
        "class": fclass,
        "level": level,
        "build": build or level,
        "hp": full,
        "max": full,
        "boss": boss,
        "art": art or art_url(FIGHTER_ART),
    }


def _foe_fighter(row: dict) -> Fighter:
    """Собрать противника в бойца: здоровье берётся из состояния.

    Запас считается в два захода. Сперва боец без прибавок — столько
    здоровья даёт ему класс и уровень; потом, если в рейде ему
    отмерено больше (отряд велик, босс крепок), недостающее
    дописывается прибавкой. Иначе отмеренное число удваивалось бы.

    Своё оружие — только у босса, и узнаётся оно по самой строке, а не
    по комнате: помощники, которых он зовёт, пожарного топора не
    берут.
    """
    from bot.game.equipment import Equipment, OwnedItem, get_item
    from bot.game.reference import best_kit

    cls = FIGHTER_CLASSES[row["class"]]
    rules = BOSSES.get(row.get("boss") or "")
    level = int(row["level"])
    # Зомби одет, а не гол. Это не придирка к виду: голый боец
    # пятнадцатого уровня втрое слабее одетого, и мерить подземелье по
    # нему — значит мерить пустоту. Рейд клуба одевает боссов так же:
    # чего нет своего, добирается лучшим с прилавка
    worn = {
        slot: OwnedItem(item=item, slot=slot)
        for slot, item in best_kit(cls, level).items()
    }
    if rules is not None:
        for code in (rules.weapon, rules.offhand):
            item = get_item(code) if code else None
            if item is not None:
                worn[item.slot] = OwnedItem(item=item, slot=item.slot)
    equipment = Equipment(items=worn)
    stats = developed_stats(
        cls, int(row.get("build") or row["level"])
    ).merge(equipment.bonus)
    plain = Fighter(
        user_id=int(row["id"]), name=row["name"], fclass=cls, stats=stats,
        level=int(row["level"]), equipment=equipment,
    )
    want = int(row["max"])
    if want > plain.max_hp:
        plain = Fighter(
            user_id=int(row["id"]), name=row["name"], fclass=cls, stats=stats,
            level=int(row["level"]), equipment=equipment,
            extra_hp=want - plain.max_hp,
        )
    plain.hp = max(0, min(int(row["hp"]), plain.max_hp))
    return plain


def _player_fighter(row: dict, player: Player | None, key: str) -> Fighter:
    """Боец отряда: вещи, приёмы и характеристики свои, здоровье — из рейда."""
    if player is None or row.get("bot"):
        fighter = _bot_fighter(key, row)
    else:
        fighter = Fighter.from_player(player)
    fighter.hp = max(0, min(int(row["hp"]), fighter.max_hp))
    row["max"] = fighter.max_hp
    return _restore_tricks(fighter, row)


def squad_for(
    room: str, size: int = 0, party: int = BEST_PARTY
) -> list[dict]:
    """Обычная группа этой комнаты под такой отряд.

    Состав растёт числом, а не здоровьем: множителя «крепче за
    состав» тут нет и не было — иначе отряд считался бы дважды, и
    пятеро, которым положено на зомби больше, получали бы этого зомби
    ещё и крепче.

    Размен тут раздельный: отряд разведён по врагам попарно, и каждый
    отвечает своему. Значит, важно одно — сколько вражеского здоровья
    приходится на одного пришедшего. Одному против двоих тяжелее
    вдвое, и это та самая сложность, которой ждут от соло.
    """
    count = size or group_size(SQUAD_SIZE.get(room, 3), party)
    return [
        foe_row(index, f"Зомби-боец {index + 1}", FOE_LEVEL, "warrior", 1.0,
                build=FOE_BUILD)
        for index in range(count)
    ]


def boss_foes(code: str, party: int) -> list[dict]:
    """Босс арены: один, зато с запасом, посчитанным на отряд."""
    rules = BOSSES[code]
    level = BOSS_LEVEL[code]
    row = foe_row(0, rules.title, level, rules.class_code, 1.0, boss=code,
                  art=rules.image)
    row["hp"] = row["max"] = boss_hp(int(row["max"]), party)
    return [row]


def open_fight(state: dict, room: str, now: int) -> dict | None:
    """Завести бой этой комнаты, если он ей положен и ещё не пройден."""
    place = station()
    here = place.room(room)
    if here is None or not here.encounter:
        return None
    if room in (state.get("cleared") or {}):
        return None
    if state.get("fight"):
        return state["fight"]
    party = int(state.get("party") or 4)
    if here.boss:
        rules = BOSSES[here.boss]
        foes = boss_foes(here.boss, party)
        fight = {
            "room": room,
            "kind": "boss",
            "boss": here.boss,
            "turn": 1,
            "foes": foes,
            "telegraph": {},
            "window": 0,
            "immune": 0,
            "shield": here.boss in ("brute", "leader"),
            "calls": 0,
            "wave": 0,
            "console": 0,
            "log": [f"{rules.title} выходит на арену."],
        }
        # Реле, снятые заранее в релейной, снимают щит Предводителя
        if here.boss == "leader" and has(state, RELAY_A) and has(state, RELAY_B):
            fight["shield"] = False
            fight["log"].append("Реле отключены заранее: щит не поднялся.")
        if here.boss == "hunter" and has(state, FLOODLIGHTS):
            fight["window"] = WINDOW_TURNS
            fight["log"].append("Прожекторы наведены: Охотник на свету.")
    else:
        fight = {
            "room": room,
            "kind": "squad",
            "boss": "",
            "turn": 1,
            "foes": squad_for(room, party=party),
            "telegraph": {},
            "window": 0,
            "immune": 0,
            "shield": False,
            "calls": 0,
            "wave": 0,
            "console": 0,
            "log": ["Зомби перекрыли выходы."],
        }
    state["fight"] = fight
    _note(state, f"Бой в «{here.name}».", now)
    return fight


def here_fighters(state: dict, room: str) -> list[str]:
    """Кто из отряда стоит в этой комнате и может драться."""
    return [
        key
        for key, one in (state.get("members") or {}).items()
        if one["room"] == room and not one["arrive"] and not one["left"]
        and not one["extracted"] and not one["down"]
    ]


def strike(
    run: Run, user_id: int, attacks: list[str], block: list[str],
    arena: str, now: int, target: int = 0,
) -> str:
    """Размен этого бойца. Нажал — посчитано сразу, как в рейде.

    Ждать остальных нельзя: в подземелье отряд разбредается по
    комнатам, кто-то отвалился, кто-то думает минуту. Поэтому ход
    закрывается на том, кто его сделал: удар, ответ противника и
    развязка считаются тут же.

    **Волна — это по разу на каждого.** Пока в волне остались не
    ходившие, бой стоит на том же номере; отстрелялись все — волна
    закрывается, и босс делает своё: меняет фазу, зовёт помощников и
    объявляет следующий удар.

    `arena` вместо удара — действие на арене: оно тоже занимает ход и
    доступно любому классу.
    """
    state = run.state
    fight = state.get("fight")
    if not fight:
        raise BlackoutError("Боя сейчас нет.")
    one = member(state, user_id)
    if one is None or one["room"] != fight["room"]:
        raise BlackoutError("Ты не в этой комнате.")
    if one["down"]:
        raise BlackoutError("Ты не на ногах.")
    one["seen"] = now
    key = str(user_id)
    if key in (fight.get("acted") or []):
        raise BlackoutError("В этой волне ты уже ходил. Ждём остальных.")

    rules = BOSSES.get(fight.get("boss") or "")
    lines: list[str] = []

    if arena:
        if arena not in ARENA_ACTIONS:
            raise BlackoutError("Такого действия на арене нет.")
        lines.append(_arena(state, fight, key, arena, now))
    else:
        zones = [z for z in (_zone(one) for one in attacks) if z]
        guard = [z for z in (_zone(one) for one in block) if z]
        if not zones or not guard:
            raise BlackoutError("Выбери и удар, и блок.")
        lines += _exchange(run, key, zones, guard, target, now)

    fight.setdefault("acted", []).append(key)

    # Победа и поражение считаются сразу: добитого врага не держат
    # живым до конца волны, а упавший отряд не ходит дальше
    if not [one for one in fight["foes"] if one["hp"] > 0]:
        return " ".join(lines + [_victory(run, now)])
    if not here_fighters(state, fight["room"]):
        return " ".join(lines + [_wipe(run, now)])

    waiting = [
        name for name in here_fighters(state, fight["room"])
        if name not in fight["acted"]
    ]
    if not waiting:
        lines += _close_wave(run, fight, rules, now)
    fight["log"] = (fight.get("log") or []) + lines
    del fight["log"][:-40]
    return " ".join(lines) or "Ход посчитан."


def _exchange(
    run: Run, key: str, zones: list, guard: list, target: int, now: int
) -> list[str]:
    """Один размен: боец и его противник бьют друг друга.

    Считает это тот же движок, что и ринг клуба. Усталость растянута
    на длину рейда, а не дуэли: подземелье идёт часами, и разгон
    дуэльной усталости выбивал бы по сотне с пятой волны.
    """
    state = run.state
    fight = state["fight"]
    rng = random.Random(f"{state.get('seed')}:{fight['room']}:{key}:{fight['turn']}")
    foes = [one for one in fight["foes"] if one["hp"] > 0]
    row = state["members"][key]
    me = _player_fighter(row, _PLAYERS.get(int(key)), key)
    here = here_fighters(state, fight["room"])
    place = here.index(key) if key in here else 0
    foe_row_now = _target_of(foes, target, place)
    foe = _foe_fighter(foe_row_now)

    # Блок берётся по ширине бойца, а не по числу нажатых кнопок: в
    # клубе человек выбирает середину блока, а движок разводит её на
    # две зоны, а со щитом на три. Подземелье когда-то брало ровно
    # нажатое — и отряд входил на станцию с блоком в одну зону, то
    # есть втрое тоньше, чем тот же боец на ринге
    if len(guard) < me.block_width:
        guard = list(block_combo(guard[0], me.block_width))
    # Удары — по числу рук. Вторая рука с оружием бьёт туда же, если
    # экран прислал одну зону: оставлять её без удара нечестно
    while len(zones) < me.attacks_per_round:
        zones.append(zones[-1])

    mine = Action(attacks=tuple(zones[: me.attacks_per_round]),
                  block=tuple(guard))
    theirs = Action(
        attacks=tuple(
            rng.choice(list(Zone)) for _ in range(foe.attacks_per_round)
        ),
        block=block_combo(rng.choice(list(Zone)), foe.block_width),
    )
    before_foe, before_me = foe.hp, me.hp
    result = resolve_round(
        me, mine, foe, theirs, fight["turn"], rng, MAX_FIGHT_TURNS
    )
    hit = before_foe - foe.hp
    took = before_me - me.hp

    # Рука босса тяжелее обычной: он отвечает каждому, кто по нему
    # ударил, и отвечает всерьёз. У обычного заражённого множителя нет
    force = BOSS_FORCE.get(foe_row_now.get("boss") or "", 1.0)
    if took and force != 1.0:
        extra = int(took * force) - took
        me.hp = max(0, me.hp - extra)
        took += extra

    # Окно уязвимости от действия на арене, щит босса и объявленный удар
    if hit and fight.get("window"):
        extra = int(hit * 0.4)
        foe.hp = max(0, foe.hp - extra)
        hit += extra
    elif hit and fight.get("shield"):
        back = int(hit * 0.5)
        foe.hp = min(foe.max_hp, foe.hp + back)
        hit -= back
    mark = (fight.get("telegraph") or {}).get("target", "")
    lines: list[str] = []
    if key == mark and not fight.get("window") and took:
        rules = BOSSES.get(fight.get("boss") or "")
        extra = int(took * 0.5)
        me.hp = max(0, me.hp - extra)
        took += extra
        lines.append(
            f"{rules.telegraph_title if rules else 'Удар'} достался "
            f"{row['name']}."
        )

    foe_row_now["hp"] = foe.hp
    row["hp"] = me.hp
    _keep_tricks(row, me)
    lines.append(
        f"{row['name']} → {foe_row_now['name']}: −{hit}, в ответ −{took}."
    )
    # Вторая половина приёмов десятой ступени — та, что достаёт не
    # противника, а комнату: «Массовый удар» бьёт остальных зомби,
    # «Зов крови» и «Бог трикстера» кладут заготовку всему отряду.
    # Считает её тот же код, что и в рейде, иначе эти приёмы в
    # подземелье работали бы наполовину и молча
    lines += _room_echo(state, fight, me, foe, result)
    if foe.hp <= 0:
        lines.append(f"{foe_row_now['name']} повержен.")
    if me.hp <= 0:
        row["down"] = True
        lines.append(f"{row['name']} упал.")
    return lines


# Стороны в комнате: отряд заодно, заражённые против
SQUAD_SIDE, FOE_SIDE = 0, 1


def _room_echo(
    state: dict, fight: dict, me: Fighter, foe: Fighter, result
) -> list[str]:
    """Разнести по комнате то, что приёмы этого размена сделали сверх удара."""
    from bot.game.narrator import echo_lines

    room = fight["room"]
    field: dict[int, Fighter] = {me.user_id: me, foe.user_id: foe}
    sides: dict[int, int] = {me.user_id: SQUAD_SIDE, foe.user_id: FOE_SIDE}
    mates: list[tuple[dict, Fighter]] = []
    for key in here_fighters(state, room):
        if int(key) == me.user_id:
            continue
        row = state["members"][key]
        mate = _player_fighter(row, _PLAYERS.get(int(key)), key)
        mates.append((row, mate))
        field[mate.user_id] = mate
        sides[mate.user_id] = SQUAD_SIDE
    others: list[tuple[dict, Fighter]] = []
    for one in fight["foes"]:
        if one["hp"] <= 0 or int(one["id"]) == foe.user_id:
            continue
        other = _foe_fighter(one)
        others.append((one, other))
        field[other.user_id] = other
        sides[other.user_id] = FOE_SIDE
    echoes = group_aftermath(result.strikes, field, sides)
    if not echoes:
        return []
    for row, mate in mates:
        row["hp"] = mate.hp
        _keep_tricks(row, mate)
    for one, other in others:
        one["hp"] = other.hp
    return list(echo_lines(echoes, field))


def use_ability(run: Run, user_id: int, code: str, now: int) -> str:
    """Нажать приём в подземелье. Жмут до удара, ровно как в рейде.

    Боёвка станции — та же, что на ринге и в рейде, и приёмы к ней
    прилагаются: без них «аналогично рейду» кончалось бы на ударе и
    блоке. Шкала копится внутри боя, между комнатами гаснет, а
    лечение расходится по тем, кто стоит в этой же комнате.
    """
    state = run.state
    fight = state.get("fight")
    if not fight:
        raise BlackoutError("Приёмы держат на бой.")
    one = member(state, user_id)
    if one is None or one["room"] != fight["room"]:
        raise BlackoutError("Ты не в этой комнате.")
    if one["down"]:
        raise BlackoutError("Ты не на ногах.")
    one["seen"] = now
    player = _PLAYERS.get(int(user_id))
    me = _player_fighter(one, player, str(user_id))
    if code not in me.loadout:
        raise BlackoutError("Этот приём не выучен.")
    # Причину отказа называет движок: он один знает, что именно не
    # сложилось — норма хода, лежащая заготовка или шкала
    try:
        charge = me.use(code)
    except ValueError as error:
        raise BlackoutError(str(error)) from error
    ability = charge.ability
    if not ability.heal:
        _keep_tricks(one, me)
        said = f"{ability.icon} {ability.title} наготове."
    else:
        gained = me.heal_by(ability.heal)
        me.charges.pop()
        mates: list[tuple[dict, Fighter]] = []
        for key in here_fighters(state, fight["room"]):
            if int(key) == int(user_id):
                continue
            row = state["members"][key]
            mates.append((row, _player_fighter(row, _PLAYERS.get(int(key)), key)))
        healed = spread_heal(ability, me, [fighter for _, fighter in mates])
        for row, fighter in mates:
            if fighter.user_id in healed:
                row["hp"] = fighter.hp
        one["hp"] = me.hp
        _keep_tricks(one, me)
        said = heal_line(ability, gained, len(healed))
    fight["log"] = (fight.get("log") or []) + [said]
    del fight["log"][:-40]
    return said


def _close_wave(run: Run, fight: dict, rules, now: int) -> list[str]:
    """Волна закрыта: все отходили. Дальше ход за ареной и боссом."""
    state = run.state
    rng = random.Random(f"{state.get('seed')}:{fight['room']}:w{fight['turn']}")
    lines: list[str] = []
    if rules is not None:
        lines += _boss_turn(state, fight, rules, rng, now)
    if fight.get("window"):
        fight["window"] -= 1
    if fight.get("immune"):
        fight["immune"] -= 1
    fight["acted"] = []
    fight["turn"] += 1
    # Норма приёмов на ход — своя у каждой волны
    for key in here_fighters(state, fight["room"]):
        state["members"][key]["pressed"] = 0
    return lines


# Игроки текущей команды: служба подставляет их сюда перед разменом,
# чтобы боец собирался с его настоящими вещами и характеристиками
_PLAYERS: dict[int, Player] = {}


def _answers_itself(state: dict, key: str, now: int) -> bool:
    """Ходит ли за этого бойца сервер: бот или ушедший в офлайн."""
    one = (state.get("members") or {}).get(key) or {}
    return bool(one.get("bot")) or now - int(one.get("seen") or 0) > AFK_SECONDS


def auto_turns(run: Run, now: int, limit: int = 8) -> list[str]:
    """Отходить за тех, за кого отвечает сервер: ботов и ушедших.

    Зовётся после живого хода и при взгляде на бой. Без этого отряд с
    одним отключившимся вставал бы намертво: волна не закрывается, пока
    не отходили все.
    """
    said: list[str] = []
    for _ in range(limit):
        state = run.state
        fight = state.get("fight")
        if not fight:
            break
        here = here_fighters(state, fight["room"])
        waiting = [
            key for key in here
            if key not in (fight.get("acted") or [])
            and _answers_itself(state, key, now)
        ]
        if not waiting:
            break
        key = waiting[0]
        rng = random.Random(f"{state.get('seed')}:{key}:{fight['turn']}")
        zones = list(Zone)
        said.append(
            strike(
                run, int(key), [rng.choice(zones).value],
                [rng.choice(zones).value],
                "", now,
            )
        )
    return said


def _target_of(foes: list[dict], want: int, index: int) -> dict:
    """Кого бьёт этот боец: выбранного, иначе главного, иначе по кругу."""
    if want:
        for one in foes:
            if int(one["id"]) == want and one["hp"] > 0:
                return one
    for one in foes:
        if one.get("boss") and one["hp"] > 0:
            return one
    return foes[index % len(foes)]


def _telegraph_target(state: dict, fight: dict, rng: random.Random) -> str:
    here = here_fighters(state, fight["room"])
    return rng.choice(here) if here else ""


def _boss_turn(
    state: dict, fight: dict, rules, rng: random.Random, now: int
) -> list[str]:
    """Что босс делает между разменами: телеграф, фазы и подкрепления."""
    lines: list[str] = []
    boss_row = next((one for one in fight["foes"] if one.get("boss")), None)
    if boss_row is None or boss_row["hp"] <= 0:
        return lines
    share = boss_row["hp"] / max(1, boss_row["max"])
    phase = rules.phase_of(share)
    if phase != fight.get("phase"):
        fight["phase"] = phase
        title = (
            rules.phase_titles[phase]
            if phase < len(rules.phase_titles) else ""
        )
        if title:
            lines.append(f"{rules.title}: {title}.")
        # Щит Громилы ломается на половине
        if rules.code == "brute" and phase >= 1 and fight.get("shield"):
            fight["shield"] = False
            lines.append("Щит Громилы раскололся.")

    # Подкрепления: их число конечно и записано правилами
    cap = BOSS_REINFORCEMENTS.get(rules.code, 0)
    standing = [
        one for one in fight["foes"]
        if one["hp"] > 0 and not one.get("boss")
    ]
    if phase >= 1 and fight.get("calls", 0) < cap and not standing:
        fight["calls"] = fight.get("calls", 0) + 1
        start = len(fight["foes"])
        for number in range(REINFORCEMENT_SIZE):
            fight["foes"].append(
                foe_row(start + number, f"Помощник {start + number}",
                        FOE_LEVEL, "warrior", 1.0, build=FOE_BUILD)
            )
        lines.append(
            f"{rules.title} зовёт помощников: волна "
            f"{fight['calls']} из {cap}."
        )

    # Телеграф на следующий ход: он виден заранее, и ответ на него есть
    target = _telegraph_target(state, fight, rng)
    if target:
        fight["telegraph"] = {
            "kind": rules.telegraph,
            "title": rules.telegraph_title,
            "target": target,
            "hint": rules.counter_hint,
            "action": rules.counter_action,
        }
        lines.append(
            f"{rules.telegraph_title}: "
            f"{state['members'][target]['name']}. {rules.counter_hint}"
        )
    return lines


def _arena(
    state: dict, fight: dict, key: str, action: str, now: int
) -> str:
    """Действие на арене: прожектор, клапан, реле, консоль.

    Любое из них занимает обычный ход и доступно любому классу. Это и
    есть ответ на телеграф: не рефлекс, а решение потратить ход.
    """
    name = state["members"][key]["name"]
    rules = BOSSES.get(fight.get("boss") or "")
    if action == "console":
        fight["console"] = min(
            CONSOLE_HEALTH, int(fight.get("console") or 0) + CONSOLE_HIT
        )
        return f"{name} прикрыл консоль."
    if rules is None or rules.counter_action != action:
        return f"{name} жмёт не ту кнопку: здесь это ничего не даёт."
    if action == "relay":
        if not fight.get("shield"):
            return f"{name}: реле уже отключены."
        fight["shield"] = False
        return f"{name} отключил реле — щит Предводителя пропал."
    # Навсегда выключить босса нельзя: после окна он держится
    # несколько ходов. Иначе отряд из четверых просто отправлял бы
    # одного жать кнопку каждую волну — и окно висело бы весь бой
    if fight.get("immune"):
        return (
            f"{name}: {rules.counter_title.lower()} пока не берёт — "
            f"{rules.title} держится ещё "
            f"{int(fight['immune'])} ход(а)."
        )
    fight["window"] = WINDOW_TURNS
    fight["immune"] = WINDOW_TURNS + CONTROL_IMMUNE_TURNS
    if action == "floodlight":
        return f"{name} навёл прожектор: Охотнику некуда уклоняться."
    return f"{name} открыл клапан: броня Громилы сошла."


def _victory(run: Run, now: int) -> str:
    """Комната зачищена. Второй раз её не заселяют никогда."""
    state = run.state
    fight = state.get("fight") or {}
    room = fight.get("room") or ""
    state["fight"] = None
    if not room:
        return "Бой кончился."
    # Бой кончился — упавших поднимают свои. Это не поблажка: иначе
    # отряд после каждой победы стоял бы без половины состава, а
    # подъём «на бой» в сценарии именно боевой, не вечный. Между
    # волнами обороны передышка та же: в сценарии она называется
    # «активировать контур», и без неё три волны подряд не отбить
    for one in (state.get("members") or {}).values():
        if one["left"] or one["extracted"]:
            continue
        one["raised"] = False
        _forget_tricks(one)
        if one["down"]:
            one["down"] = False
            one["hp"] = max(1, int(one["max"] * 0.3))
        else:
            one["hp"] = min(
                one["max"], one["hp"] + int(one["max"] * CLEAR_HEAL)
            )
    # Оборона рубки — не комната: за волной идёт волна, а не зачистка
    if fight.get("kind") == "defense":
        return wave_done(run, now)
    state.setdefault("cleared", {})[room] = now
    here = station().room(room)
    said = f"Комната «{here.name if here else room}» зачищена."
    boss = fight.get("boss") or ""
    if boss:
        flag = {"hunter": "hunter_defeated", "brute": "brute_defeated",
                "leader": "leader_defeated"}[boss]
        raise_flag(state, flag, now)
        said = f"{BOSSES[boss].title} повержен."
    if here is not None and here.safe:
        state["safe"] = room
    _note(state, said, now)
    # Безопасной считается последняя зачищенная комната: в неё и
    # откатывается попытка после полного поражения
    state["safe"] = room
    return said


def _wipe(run: Run, now: int) -> str:
    """Отряд лёг. Попытка откатывается, добытое остаётся."""
    state = run.state
    fight = state.get("fight") or {}
    room = fight.get("room") or ""
    if fight.get("kind") == "defense":
        # Пуск сорван, но модули остаются в гнёздах: трёх боссов заново
        # никто искать не будет
        for one in (state.get("members") or {}).values():
            if one["left"] or one["extracted"]:
                continue
            one["down"] = False
            one["raised"] = False
            _forget_tricks(one)
            one["hp"] = max(1, int(one["max"] * 0.4))
        return defense_failed(run, now)
    state["fight"] = None
    safe = state.get("safe") or station().start
    for one in (state.get("members") or {}).values():
        if one["left"] or one["extracted"]:
            continue
        one["down"] = False
        one["raised"] = False
        _forget_tricks(one)
        # Откат попытки — это потеря времени, а не воронка. Отряд
        # отходит в безопасную комнату, перевязывается и идёт заново:
        # возвращать его на четверти здоровья значило бы запирать рейд
        # насмерть — проиграв один раз, он проигрывал бы всегда, а
        # необратимых тупиков в этом рейде быть не должно. Цена
        # поражения — дорога обратно и сто восемьдесят минут на часах
        one["hp"] = one["max"]
        one["room"] = safe
        one["from"] = one["to"] = ""
        one["departed"] = one["arrive"] = 0
    # Незавершённый босс восстанавливается целиком; добытое не теряется
    if room:
        (state.get("cleared") or {}).pop(room, None)
    where = station().room(safe)
    _note(state, "Отряд отброшен. Боссы восстановились.", now)
    return (
        f"Отряд не выстоял. Все очнулись в «{where.name if where else safe}»."
    )


def raise_mate(run: Run, user_id: int, whom: int, now: int) -> str:
    """Поднять упавшего: один подъём на бой каждому."""
    state = run.state
    me = member(state, user_id)
    one = member(state, whom)
    if me is None or one is None:
        raise BlackoutError("Этого бойца тут нет.")
    if me["down"]:
        raise BlackoutError("Сам не на ногах.")
    if one["room"] != me["room"]:
        raise BlackoutError("Он в другой комнате.")
    if not one["down"]:
        raise BlackoutError("Он и так стоит.")
    if one["raised"]:
        raise BlackoutError("Его уже поднимали в этом бою.")
    one["down"] = False
    one["raised"] = True
    one["hp"] = max(1, int(one["max"] * 0.35))
    _note(state, f"{me['name']} поднял {one['name']}.", now)
    return f"{one['name']} снова в строю."


# ---------- задачи ----------
#
# Посещение комнаты ничего не выполняет: цель — отдельная команда.
# Поэтому «зашёл в подвал» и «открыл вентиль» — разные вещи, и
# перепутать их нельзя даже запросом мимо экрана.


@dataclass(frozen=True)
class Task:
    """Задача комнаты: что нажимают, что для этого нужно и что выходит."""

    code: str
    room: str
    title: str
    hint: str
    gives: str
    needs: tuple[str, ...] = ()
    # Нужна ли зачищенная комната. У щита насосов зомби стоят прямо на
    # нём, и крутить вентили под их руками — не задача, а подарок
    clear_first: bool = True


TASKS: tuple[Task, ...] = (
    Task("radio", "O03", "Поднять связь",
         "Выбери исправный канал по журналу смен.", CLEARANCE,
         clear_first=False),
    Task("floodlights", "F206", "Навести прожекторы",
         "Три рабочих прожектора по схеме: свет лишит Охотника уклонения.",
         FLOODLIGHTS),
    Task("valve_a", "B03", "Открыть вентиль А",
         "Порядок на щите: А, затем Б, затем насосы.", VALVE_A),
    Task("valve_b", "B05", "Открыть вентиль Б",
         "Вентиль Б открывают после А — иначе пойдёт пар.", VALVE_B,
         needs=(VALVE_A,)),
    Task("pumps", "B07", "Запустить насосы",
         "Сердечник даёт щиту переносное питание. Он не расходуется.",
         COOLING, needs=(VALVE_A, VALVE_B, POWER_CORE)),
    Task("relays", "F304", "Отключить реле",
         "Две релейные защиты арены — иначе щит Предводителя не снять.",
         RELAY_A),
    Task("evacuation", "F403", "Снять схему эвакуации",
         "Тоннели: С → дренаж → D.", EVACUATION_PLAN, clear_first=False),
    Task("drainage", "E05", "Открыть дренаж",
         "Без дренажа шлюз D не поднимется.", DRAINAGE, clear_first=False),
)

# Медпункты: безопасные комнаты, где отряд один раз восстанавливается.
# Их два на всю станцию, и это весь отдых, который она даёт
MEDBAYS = {"O09": "heal_yard", "F303": "heal_f3"}

TASK_AT = {one.room: one for one in TASKS}

# Подбор модуля — тоже команда, а не посещение
PICKUPS = {
    "F209": (SYNCHRONIZER, "hunter_defeated"),
    "F108": (POWER_CORE, "brute_defeated"),
    "F308": (RESTART_KEY, "leader_defeated"),
}


def task_here(state: dict, room: str) -> dict[str, Any] | None:
    """Что можно сделать в этой комнате и можно ли уже."""
    if room in MEDBAYS:
        flag = MEDBAYS[room]
        return {
            "code": "heal",
            "title": "Перевязаться",
            "hint": "Медпункт даёт отряду одно восстановление.",
            "done": has(state, flag),
            "can_do": not has(state, flag),
            "why": "Этот медпункт уже разобрали." if has(state, flag) else "",
        }
    task = TASK_AT.get(room)
    if task is None:
        return None
    done = has(state, task.gives) and (
        task.gives != RELAY_A or has(state, RELAY_B)
    )
    why = ""
    # Зачистка требуется там, где есть кого зачищать: у вентилей зомби
    # не стоят, и ждать от них победы — значит запереть задачу навсегда
    here = station().room(room)
    busy = bool(here and here.encounter) and room not in (
        state.get("cleared") or {}
    )
    if task.clear_first and busy:
        why = "Сперва зачисти комнату."
    else:
        missing = [one for one in task.needs if not has(state, one)]
        if missing:
            why = "Нужно: " + ", ".join(
                FLAG_TITLES.get(one, one) for one in missing
            )
    return {
        "code": task.code,
        "title": task.title,
        "hint": task.hint,
        "done": done,
        "can_do": not done and not why,
        "why": why,
    }


def do_task(run: Run, user_id: int, now: int) -> str:
    """Сделать то, что делается в этой комнате."""
    state = run.state
    one = member(state, user_id)
    if one is None:
        raise BlackoutError("Ты не в этом рейде.")
    if one["arrive"]:
        raise BlackoutError("Ты в пути.")
    room = one["room"]
    if room in MEDBAYS:
        flag = MEDBAYS[room]
        if not raise_flag(state, flag, now):
            raise BlackoutError("Этот медпункт уже разобрали.")
        for row in (state.get("members") or {}).values():
            if row["left"] or row["extracted"]:
                continue
            row["down"] = False
            row["hp"] = row["max"]
        _note(state, "Отряд перевязался в медпункте.", now)
        return "Отряд перевязан: здоровье полное."
    task = TASK_AT.get(room)
    if task is None:
        raise BlackoutError("Здесь нечего делать.")
    body = task_here(state, room)
    if body and body["done"]:
        return "Это уже сделано."
    if body and not body["can_do"]:
        raise BlackoutError(body["why"])
    # Реле две, и обе снимаются одной командой с подсказкой, какая
    # осталась: пропущенная проверка не должна запирать арену
    if task.code == "relays":
        if not has(state, RELAY_A):
            raise_flag(state, RELAY_A, now)
            _note(state, "Реле А отключено. Осталось Б.", now)
            return "Реле А отключено. Осталось реле Б."
        raise_flag(state, RELAY_B, now)
        _note(state, "Реле Б отключено.", now)
        return "Оба реле отключены: щит Предводителя не поднимется."
    raise_flag(state, task.gives, now)
    _note(state, f"{one['name']}: {task.title.lower()}.", now)
    return f"{task.title}: готово."


def take_item(run: Run, user_id: int, now: int) -> str:
    """Подобрать модуль. Победа над боссом — обязательна, копий не бывает."""
    state = run.state
    one = member(state, user_id)
    if one is None:
        raise BlackoutError("Ты не в этом рейде.")
    if one["arrive"]:
        raise BlackoutError("Ты в пути.")
    room = one["room"]
    found = PICKUPS.get(room)
    if found is None:
        raise BlackoutError("Здесь ничего не лежит.")
    item, needs = found
    if not has(state, needs):
        raise BlackoutError(
            f"Пьедестал под током: {FLAG_TITLES.get(needs, needs)} — нет."
        )
    if has(state, item):
        return f"{ITEM_TITLES[item]} уже в общем инвентаре."
    raise_flag(state, item, now)
    _note(state, f"{ITEM_TITLES[item]} — в инвентаре отряда.", now)
    return f"{ITEM_TITLES[item]} у отряда."


# ---------- рубка: пуск ----------
#
# Пуск — это не кнопка, а порядок: три гнезда, подтверждение отряда,
# три волны обороны и только потом синхронизация. Неудачная оборона
# сбрасывает подготовку, но не вынимает модули: искать трёх боссов
# заново никто не будет.


def console_of(state: dict) -> dict[str, Any]:
    """Состояние консоли рубки: что вставлено и на какой волне оборона."""
    body = state.setdefault("console", {})
    body.setdefault("slots", {})
    body.setdefault("ready", [])
    body.setdefault("wave", 0)
    body.setdefault("health", CONSOLE_HEALTH)
    return body


def install(run: Run, user_id: int, item: str, now: int) -> str:
    """Вставить модуль в гнездо. Форма гнезда своя у каждого."""
    state = run.state
    one = member(state, user_id)
    place = station()
    if one is None or one["room"] != place.restart_room:
        raise BlackoutError("Гнёзда стоят в рубке.")
    if item not in QUEST_ITEMS:
        raise BlackoutError("Такой модуль станции не нужен.")
    if not has(state, item):
        raise BlackoutError(f"{ITEM_TITLES[item]} ещё не у отряда.")
    body = console_of(state)
    if body["slots"].get(item):
        return f"{ITEM_TITLES[item]} уже в гнезде."
    body["slots"][item] = now
    _note(state, f"{ITEM_TITLES[item]} — в гнезде.", now)
    return f"{ITEM_TITLES[item]} встал в гнездо."


def confirm(run: Run, user_id: int, now: int) -> str:
    """Подтвердить готовность. Оборона начинается, когда готовы все живые."""
    state = run.state
    place = station()
    one = member(state, user_id)
    if one is None or one["room"] != place.restart_room:
        raise BlackoutError("Готовность подтверждают в рубке.")
    body = console_of(state)
    missing = [one for one in QUEST_ITEMS if not body["slots"].get(one)]
    if missing:
        raise BlackoutError(
            "Не все гнёзда заняты: "
            + ", ".join(ITEM_TITLES[one] for one in missing)
        )
    if not has(state, COOLING):
        raise BlackoutError("Охлаждение не запущено: реактор не примет пуск.")
    if str(user_id) not in body["ready"]:
        body["ready"].append(str(user_id))
    here = [
        key for key in here_fighters(state, place.restart_room)
    ]
    waiting = [key for key in here if key not in body["ready"]]
    if waiting:
        return f"Готов. Ждём ещё {len(waiting)}."
    if body["wave"] == 0:
        body["wave"] = 1
        body["health"] = CONSOLE_HEALTH
        # Подготовка — это и есть подготовка: отряд перевязывается,
        # прежде чем открыть двери. Иначе оборона превращалась бы в
        # продолжение боя с Предводителем, а в сценарии это отдельный
        # этап со своим бюджетом в восемь минут
        for row in (state.get("members") or {}).values():
            if row["left"] or row["extracted"]:
                continue
            row["down"] = False
            row["hp"] = row["max"]
        _start_wave(state, 1, now)
        return "Отряд готов. Оборона началась: первая волна."
    return "Оборона уже идёт."


def _start_wave(state: dict, number: int, now: int) -> None:
    """Выпустить очередную волну обороны рубки."""
    party = int(state.get("party") or BEST_PARTY)
    size = group_size(
        DEFENSE_WAVES[min(number, len(DEFENSE_WAVES)) - 1], party
    )
    title = DEFENSE_WAVE_TITLES[min(number, len(DEFENSE_WAVES)) - 1]
    state["fight"] = {
        "room": station().restart_room,
        "kind": "defense",
        "boss": "",
        "turn": 1,
        "foes": [
            foe_row(index, f"Зомби · {title} {index + 1}", FOE_LEVEL,
                    "warrior", 1.0, build=FOE_BUILD)
            for index in range(size)
        ],
        "telegraph": {},
        "window": 0,
        "immune": 0,
        "shield": False,
        "calls": 0,
        "wave": number,
        "console": state["console"]["health"],
        "log": [f"Волна {number} из {len(DEFENSE_WAVES)}: {title}."],
    }
    _note(state, f"Оборона рубки: волна {number}.", now)


def wave_done(run: Run, now: int) -> str:
    """Волна отбита: либо следующая, либо синхронизация."""
    state = run.state
    body = console_of(state)
    number = int(body.get("wave") or 0)
    if number < len(DEFENSE_WAVES):
        body["wave"] = number + 1
        _start_wave(state, number + 1, now)
        return f"Волна {number} отбита. Следующая пошла."
    body["wave"] = len(DEFENSE_WAVES) + 1
    _note(state, "Волны отбиты. Можно синхронизировать сеть.", now)
    return "Волны отбиты. Жми «Синхронизировать сеть»."


def synchronize(run: Run, user_id: int, now: int) -> str:
    """Пуск. Одной записью: флаг, расход модулей, награда и двери."""
    state = run.state
    place = station()
    one = member(state, user_id)
    if one is None or one["room"] != place.restart_room:
        raise BlackoutError("Сеть синхронизируют в рубке.")
    if has(state, RESTARTED):
        return "Станция уже запущена."
    body = console_of(state)
    if any(not body["slots"].get(code) for code in QUEST_ITEMS):
        raise BlackoutError("Не все три модуля в гнёздах.")
    if not has(state, COOLING):
        raise BlackoutError("Охлаждение не запущено.")
    if int(body.get("wave") or 0) <= len(DEFENSE_WAVES):
        raise BlackoutError("Сперва отбить три волны обороны.")
    if state.get("fight"):
        raise BlackoutError("В рубке ещё дерутся.")
    raise_flag(state, RESTARTED, now)
    state["restarted_at"] = now
    body["spent"] = {code: now for code in QUEST_ITEMS}
    _note(state, "Чёрный пуск: город увидит свет.", now)
    return (
        "Сеть синхронизирована. Станция запущена, главный вход закрыт — "
        "уходите аварийной лестницей."
    )


def defense_failed(run: Run, now: int) -> str:
    """Консоль продавили: подготовка сбрасывается, модули остаются."""
    state = run.state
    body = console_of(state)
    body["wave"] = 0
    body["ready"] = []
    body["health"] = CONSOLE_HEALTH
    state["fight"] = None
    _note(state, "Консоль продавили. Пуск сорван, модули в гнёздах.", now)
    return "Пуск сорван. Модули остались в гнёздах — соберитесь и начните снова."


# ---------- эвакуация ----------


async def extract(
    db: Database, run: Run, user_id: int, now: int
) -> str:
    """Выйти со станции. Награда за эвакуацию — личная."""
    state = run.state
    place = station()
    one = member(state, user_id)
    if one is None or one["room"] != place.exit_room:
        raise BlackoutError("Эвакуируются на выходе.")
    if not has(state, RESTARTED):
        raise BlackoutError("Город ждёт света: станция не запущена.")
    if not has(state, DRAINAGE):
        raise BlackoutError("Дренаж закрыт.")
    if one["extracted"]:
        return "Ты уже снаружи."
    one["extracted"] = now
    raise_flag(state, EXTRACTED, now)
    _note(state, f"{one['name']} вышел со станции.", now)
    paid = await pay(db, run, user_id, STAGE_EXTRACT, now)
    fast = (
        state.get("restarted_at")
        and now - int(state["restarted_at"]) <= SPEED_BONUS_MINUTES * 60
    )
    if fast:
        paid += await pay(db, run, user_id, STAGE_SPEED, now)
    if all(
        row["extracted"] or row["left"]
        for row in (state.get("members") or {}).values()
    ):
        state["outcome"] = "extracted"
    said = f"Ты снаружи. Начислено {paid} 💰."
    return said + (" Бонус скорости — твой." if fast else "")


async def pay(
    db: Database, run: Run, user_id: int, stage: str, now: int
) -> int:
    """Выдать часть награды. Один раз на игрока, цикл и часть."""
    if user_id <= 0:  # союзники сервера денег не получают
        return 0
    amount = reward_for(stage)
    if not await db.blackout_pay(
        user_id, run.cycle, stage, amount, run.id, now
    ):
        return 0
    player = await db.get_player(user_id)
    if player is None:  # pragma: no cover - боец удалил персонажа
        return 0
    player.credits += amount
    await db.save_player(player)
    logger.info(
        "Рейд %s: боец %s получил %s за %s", run.id, user_id, amount, stage
    )
    return amount


# ---------- снимок для экрана ----------
#
# Клиент не хранит правду и не считает её: он получает снимок вместе с
# версией и рисует его. После обрыва связи он просит снимок заново — и
# этого достаточно, чтобы продолжить с той же секунды.


def snapshot(run: Run, user_id: int, now: int) -> dict[str, Any]:
    """Всё, что видит этот игрок. Чужой инстанс сюда не попадает."""
    state = run.state
    place = station()
    one = member(state, user_id)
    if one is None:
        raise BlackoutError("Ты не в этом рейде.")
    room = place.room(one["room"])
    walking = bool(one["arrive"])
    going = place.room(one["to"]) if walking else None
    fight = state.get("fight")
    body: dict[str, Any] = {
        "run": run.id,
        "version": run.version,
        "phase": run.phase,
        "cycle": run.cycle,
        "outcome": state.get("outcome") or "",
        "leader": int(state.get("leader") or 0),
        "me": user_id,
        "party": int(state.get("party") or len(state.get("members") or {})),
        "started_at": int(state.get("started_at") or 0),
        "ends_at": int(state.get("ends_at") or 0),
        "seconds_left": max(0, int(state.get("ends_at") or 0) - now),
        "restarted_at": int(state.get("restarted_at") or 0),
        "speed_left": max(
            0,
            int(state.get("restarted_at") or 0) + SPEED_BONUS_MINUTES * 60 - now,
        ) if state.get("restarted_at") else 0,
        "room": {
            "code": one["room"],
            "name": room.name if room else one["room"],
            "layer": room.layer if room else "",
            "layer_title": room.layer_title if room else "",
            "image": room.image if room else "",
            "safe": bool(room and room.safe),
            "cleared": one["room"] in (state.get("cleared") or {}),
        },
        "walking": walking,
        "going": {
            "code": one["to"],
            "name": going.name if going else "",
            "arrive_at": int(one["arrive"] or 0),
            "seconds_left": max(0, int(one["arrive"] or 0) - now),
        } if walking else None,
        "exits": [] if walking else exits_of(state, one["room"]),
        "locked": _locked_here(state, one["room"]) if not walking else "",
        "task": task_here(state, one["room"]) if not walking else None,
        "pickup": _pickup_payload(state, one["room"]) if not walking else None,
        "items": [
            {
                "code": code,
                "title": ITEM_TITLES[code],
                "have": has(state, code),
                "image": art_url(ITEM_ART[code]),
                "shape": ITEM_SHAPES[code][1],
                "installed": bool(
                    (state.get("console") or {}).get("slots", {}).get(code)
                ),
            }
            for code in QUEST_ITEMS
        ],
        "flags": sorted(flags_of(state)),
        "goal": _goal(state),
        "squad": [
            {
                "id": int(key),
                "name": row["name"],
                "level": row["level"],
                "hp": row["hp"],
                "max": row["max"],
                "percent": int(100 * row["hp"] / max(1, row["max"])),
                "down": bool(row["down"]),
                "raised": bool(row["raised"]),
                "left": bool(row["left"]),
                "extracted": bool(row["extracted"]),
                "bot": bool(row.get("bot")),
                "room": row["room"],
                "here": row["room"] == one["room"],
                "walking": bool(row["arrive"]),
                "online": now - int(row.get("seen") or 0) <= AFK_SECONDS,
                "me": int(key) == user_id,
                "leader": int(key) == int(state.get("leader") or 0),
            }
            for key, row in (state.get("members") or {}).items()
        ],
        "fight": _fight_payload(state, fight, user_id, now) if fight else None,
        "console": _console_payload(state, one["room"]),
        "log": list(reversed((state.get("log") or [])[-12:])),
        "map": _map_payload(state, one["room"], user_id),
        "elsewhere": [
            {
                "name": row["name"],
                "room": row["to"] if row["arrive"] else row["room"],
                "layer": (
                    place.room(row["to"] if row["arrive"] else row["room"])
                    or room
                ).layer_title if place.room(
                    row["to"] if row["arrive"] else row["room"]
                ) else "",
            }
            for key, row in (state.get("members") or {}).items()
            if int(key) != user_id and not row["left"] and not row["extracted"]
            and (place.room(row["room"]) or room).layer
            != (room.layer if room else "")
        ],
        "can_extract": bool(
            one["room"] == place.exit_room and has(state, RESTARTED)
            and has(state, DRAINAGE) and not one["extracted"]
        ),
        "extracted": bool(one["extracted"]),
    }
    return body


def _pickup_payload(state: dict, room: str) -> dict[str, Any] | None:
    found = PICKUPS.get(room)
    if found is None:
        return None
    item, needs = found
    return {
        "code": item,
        "title": ITEM_TITLES[item],
        "have": has(state, item),
        "can_take": has(state, needs) and not has(state, item),
        "why": "" if has(state, needs) else "Босс ещё стоит.",
    }


def _goal(state: dict) -> str:
    """Что отряду делать дальше — одной строкой."""
    if state.get("outcome") == "timeout":
        return "Время вышло. Станция закрыта."
    if has(state, EXTRACTED):
        return "Все снаружи. Город со светом."
    if has(state, RESTARTED):
        return "Станция запущена. Уходите аварийной лестницей в тоннели."
    if not has(state, CLEARANCE):
        return "Поднять связь в диспетчерской и получить допуск."
    if not has(state, SYNCHRONIZER):
        return "Второй этаж: Охотник и фазовый синхронизатор."
    if not has(state, POWER_CORE):
        return "Первый этаж: Громила и силовой сердечник."
    if not has(state, COOLING):
        return "Подвал: два вентиля и насосы."
    if not has(state, RESTART_KEY):
        return "Третий этаж: Предводитель и ключ запуска."
    return "Рубка: три гнезда, оборона и синхронизация."


def _fight_payload(
    state: dict, fight: dict, user_id: int, now: int
) -> dict[str, Any]:
    rules = BOSSES.get(fight.get("boss") or "")
    here = here_fighters(state, fight["room"])
    return {
        "room": fight["room"],
        "kind": fight["kind"],
        "boss": fight.get("boss") or "",
        "boss_title": rules.title if rules else "",
        "phase_title": (
            rules.phase_titles[fight.get("phase", 0)]
            if rules and fight.get("phase", 0) < len(rules.phase_titles)
            else ""
        ),
        "turn": fight["turn"],
        "wave": fight.get("wave") or 0,
        "waves": len(DEFENSE_WAVES),
        "foes": [
            {
                "id": one["id"],
                "name": one["name"],
                "hp": one["hp"],
                "max": one["max"],
                "percent": int(100 * one["hp"] / max(1, one["max"])),
                "boss": bool(one.get("boss")),
                "art": one.get("art") or "",
            }
            for one in fight["foes"] if one["hp"] > 0
        ],
        "telegraph": fight.get("telegraph") or {},
        "window": int(fight.get("window") or 0),
        "shield": bool(fight.get("shield")),
        "arena": (
            {
                "code": rules.counter_action,
                "title": rules.counter_title,
                "hint": rules.counter_hint,
            }
            if rules else (
                {"code": "console", "title": "Прикрыть консоль",
                 "hint": "Целостность консоли держит пуск."}
                if fight["kind"] == "defense" else None
            )
        ),
        "acted": bool(str(user_id) in (fight.get("acted") or [])),
        "waiting": [
            state["members"][key]["name"] for key in here
            if key not in (fight.get("acted") or [])
            and not _answers_itself(state, key, now)
        ],
        "log": list(reversed((fight.get("log") or [])[-8:])),
    }


def _console_payload(state: dict, room: str) -> dict[str, Any] | None:
    if room != station().restart_room:
        return None
    body = console_of(state)
    return {
        "slots": [
            {
                "code": code,
                "title": ITEM_TITLES[code],
                "shape": ITEM_SHAPES[code][0],
                "mark": ITEM_SHAPES[code][1],
                "filled": bool(body["slots"].get(code)),
                "have": has(state, code),
            }
            for code in QUEST_ITEMS
        ],
        "ready": list(body.get("ready") or []),
        "wave": int(body.get("wave") or 0),
        "waves": len(DEFENSE_WAVES),
        "cooling": has(state, COOLING),
        "restarted": has(state, RESTARTED),
        "can_sync": bool(
            int(body.get("wave") or 0) > len(DEFENSE_WAVES)
            and not has(state, RESTARTED) and not state.get("fight")
        ),
    }


def _map_payload(state: dict, room: str, me: int = 0) -> dict[str, Any]:
    """Схема этажа с туманом: видно посещённое и двери из него."""
    place = station()
    here = place.room(room)
    layer = here.layer if here else "O"
    seen = set(state.get("seen") or {})
    doors = set()
    for code in seen:
        for door in place.doors_from(code):
            doors.add(door.other(code))
    shown = seen | doors
    # Кто где стоит: по комнате список бойцов. Отряд на схеме виден
    # весь — подземелье проходят вместе, и знать, где остальные,
    # важнее, чем красиво
    crowd: dict[str, list[dict]] = {}
    for key, row in (state.get("members") or {}).items():
        if row["left"] or row["extracted"]:
            continue
        where = row["to"] if row["arrive"] else row["room"]
        crowd.setdefault(where, []).append({
            "id": int(key),
            "name": row["name"],
            "mark": row["name"][:1].upper(),
            "walking": bool(row["arrive"]),
            "down": bool(row["down"]),
            "me": int(key) == me,
        })
    rows = []
    for one in place.rooms_on(layer):
        if one.code not in shown and one.code not in crowd:
            continue
        rows.append({
            "code": one.code,
            "name": one.name if one.code in seen else "?",
            "x": one.x,
            "y": one.y,
            "visited": one.code in seen,
            "here": one.code == room,
            "cleared": one.code in (state.get("cleared") or {}),
            "danger": bool(one.encounter)
            and one.code not in (state.get("cleared") or {}),
            "goal": bool(one.objective),
            "party": crowd.get(one.code, []),
        })
    links = []
    for door in place.doors:
        if door.a in shown and door.b in shown:
            room_a, room_b = place.room(door.a), place.room(door.b)
            if room_a and room_b and room_a.layer == layer == room_b.layer:
                links.append({"a": door.a, "b": door.b})
    return {
        "layer": layer,
        "title": here.layer_title if here else "",
        "rooms": rows,
        "links": links,
        "width": 1280,
        "height": 680,
    }


# ---------- одна дверь для всех команд ----------
#
# Снаружи у рейда ровно две ручки: посмотреть и сделать. Сделать —
# всегда под замком инстанса и всегда с `command_id`: повторный запрос
# отдаёт прежний ответ, а не делает второй шаг.


async def state_for(
    db: Database, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Снимок для экрана. Нет рейда — вернётся лобби города."""
    moment = now_ts() if now is None else now
    run_id = await db.blackout_of(player.user_id)
    if not run_id:
        return await lobby_for(db, player, moment)
    run = await look(db, run_id, moment)
    one = member(run.state, player.user_id)
    if one is not None:
        one["seen"] = moment
        _pass_leadership(run.state, moment)
        await db.blackout_write(
            run.id, run.version, run.phase, json.dumps(run.state), moment
        )
        run.version += 1
    if run.phase == "lobby":
        return _lobby_payload(run, player, moment)
    body = snapshot(run, player.user_id, moment)
    body["screen"] = "run"
    # Шкала и приёмы — только свои: чужие заготовки отряд видеть не
    # должен, да и собираются они из карточки игрока, а не из состояния
    fight = body.get("fight")
    if isinstance(fight, dict) and one is not None:
        from bot.webapp.fight import abilities_payload

        if one["room"] == fight.get("room") and not one["down"]:
            fight["abilities"] = abilities_payload(
                _restore_tricks(Fighter.from_player(player), one)
            )
    return body


async def lobby_for(
    db: Database, player: Player, now: int
) -> dict[str, Any]:
    """Город перед рейдом: окно входа, чужие отряды и своя награда."""
    cycle = cycle_code(now)
    rows = await db.blackout_live()
    paid = await db.blackout_paid(player.user_id, cycle)
    squads = []
    for row in rows:
        state = json.loads(str(row["state"]) or "{}")
        members = state.get("members") or {}
        squads.append({
            "run": int(row["id"]),
            "leader": (members.get(str(row["leader_id"])) or {}).get(
                "name", "боец"
            ),
            "size": len(members),
            "max": MAX_PARTY,
            "names": [one["name"] for one in members.values()],
            "can_join": len(members) < MAX_PARTY,
        })
    return {
        "screen": "lobby",
        "cycle": cycle,
        "window": window_open(now),
        "opens_at": window_opens_at(now),
        "squads": squads,
        "min": MIN_PARTY,
        "best": 4,
        "max": MAX_PARTY,
        "paid": paid,
        "limit": RUN_LIMIT_MINUTES,
        "overview": art_url("maps/station_overview.jpg"),
    }


def _lobby_payload(run: Run, player: Player, now: int) -> dict[str, Any]:
    members = run.state.get("members") or {}
    return {
        "screen": "squad",
        "run": run.id,
        "version": run.version,
        "cycle": run.cycle,
        "leader": int(run.state.get("leader") or 0),
        "me": player.user_id,
        "min": MIN_PARTY,
        "max": MAX_PARTY,
        "squad": [
            {
                "id": int(key),
                "name": one["name"],
                "level": one["level"],
                "bot": bool(one.get("bot")),
                "leader": int(key) == int(run.state.get("leader") or 0),
                "me": int(key) == player.user_id,
            }
            for key, one in members.items()
        ],
        "can_start": (
            int(run.state.get("leader") or 0) == player.user_id
            and len(members) >= MIN_PARTY
        ),
        "overview": art_url("maps/station_overview.jpg"),
        "log": list(reversed((run.state.get("log") or [])[-8:])),
    }


async def act(
    db: Database,
    player: Player,
    action: str,
    data: dict[str, Any],
    command_id: str = "",
    now: int | None = None,
) -> str:
    """Сделать ход рейда. Всё, что меняет состояние, проходит здесь."""
    moment = now_ts() if now is None else now
    if action == "open":
        await open_run(db, player, moment, test=bool(data.get("test")))
        return "Рейд объявлен. Зови людей."
    if action == "join":
        return await join_run(db, player, int(data.get("run") or 0), moment)
    if action == "leave":
        return await leave_run(db, player, moment)
    if action == "start":
        return await start_run(
            db, player, moment, bots=int(data.get("bots") or 0)
        )

    run_id = await db.blackout_of(player.user_id)
    if not run_id:
        raise BlackoutError("Ты не в рейде.")

    if action == "extract":
        # Эвакуация ходит в кошелёк, поэтому считается отдельно от
        # общего замка: деньги начисляет база, и дважды она не даст
        run = await look(db, run_id, moment)
        said = await extract(db, run, player.user_id, moment)
        await db.blackout_write(
            run.id, run.version,
            "over" if run.state.get("outcome") else run.phase,
            json.dumps(run.state), moment,
        )
        return said
    if action == "sync":
        run = await look(db, run_id, moment)
        said = synchronize(run, player.user_id, moment)
        await db.blackout_write(
            run.id, run.version, run.phase, json.dumps(run.state), moment
        )
        # Награда за пуск — всем, кто дошёл до рубки живым
        for key, one in (run.state.get("members") or {}).items():
            if one["left"] or int(key) <= 0:
                continue
            await pay(db, run, int(key), STAGE_RESTART, moment)
        return said

    if action in ("fight", "ability", "look"):
        # Бойцы собираются с их настоящими вещами: за ними идём в базу
        # один раз на команду, а не на каждый размен
        run = await read_run(db, run_id)
        _PLAYERS.clear()
        for key in (run.state.get("members") or {}):
            if int(key) > 0:
                one = await db.get_player(int(key))
                if one is not None:
                    _PLAYERS[int(key)] = one

    def change(run: Run, when: int) -> str:
        if action == "go":
            return step(run, player.user_id, str(data.get("room") or ""), when)
        if action == "lead":
            return squad_step(
                run, player.user_id, str(data.get("room") or ""), when
            )
        if action == "task":
            return do_task(run, player.user_id, when)
        if action == "take":
            return take_item(run, player.user_id, when)
        if action == "install":
            return install(
                run, player.user_id, str(data.get("item") or ""), when
            )
        if action == "confirm":
            return confirm(run, player.user_id, when)
        if action == "raise":
            return raise_mate(
                run, player.user_id, int(data.get("user") or 0), when
            )
        if action == "ability":
            return use_ability(
                run, player.user_id, str(data.get("code") or ""), when
            )
        if action == "fight":
            said = strike(
                run, player.user_id,
                [str(one) for one in (data.get("attacks") or [])],
                [str(one) for one in (data.get("block") or [])],
                str(data.get("arena") or ""), when,
                int(data.get("target") or 0),
            )
            # За ботов и за ушедших отходит сервер: иначе волна не
            # закроется и бой встанет
            tail = auto_turns(run, when)
            return " ".join([said] + tail).strip()
        raise BlackoutError("Непонятное действие.")

    return await command(db, run_id, player.user_id, change, command_id, moment)


__all__ = [
    "AFK_SECONDS",
    "BlackoutError",
    "Run",
    "act",
    "add_bots",
    "auto_turns",
    "command",
    "cycle_code",
    "exits_of",
    "extract",
    "join_run",
    "leave_run",
    "look",
    "member",
    "open_run",
    "pay",
    "raise_mate",
    "read_run",
    "run_of",
    "settle",
    "snapshot",
    "start_run",
    "strike",
    "state_for",
    "step",
    "take_item",
    "use_ability",
    "window_open",
    "window_opens_at",
]
