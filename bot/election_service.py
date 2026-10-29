"""Выборы мэра: выдвижение, голосование и смена кресла.

Календарь живёт в `bot.game.elections` и ничего не хранит: по любому
часу видно, к каким выборам он относится и что в этот час можно. Здесь
то, чего из календаря не выведешь, — кто выдвинулся, кто за кого отдал
голос и кто в итоге сел в кресло.

**Итог сводит тот, кто пришёл смотреть.** Часов у клуба нет ни у
работы, ни у полиса, ни у дохода с точек, — и здесь их тоже нет. В
девять утра понедельника никто не нажимает кнопку: просто с этой
минуты любой запрос, который спросит про мэра, застанет уже нового.

**Голос один и необратим.** Проголосовал — всё; передумать нельзя, и
это держит первичный ключ в базе, а не проверка в коде.

**Сидящий кандидат снимается.** Попал в тюрьму, пока шло выдвижение, —
в бюллетень не попал. Снятие тоже лениво: его отмечает первый, кто
заглянул в список кандидатов, и однажды снятый остаётся снятым.

**Мэр решает одно — кресла начальников.** Пять кресел города не
занимают, в них назначают, и назначает он (`bot.work_service`). Чего
мэр может ещё, здесь не решается: права ему выдаёт правило, а не
служебный модуль.

**Кресло не пустует.** В бюллетене всегда стоит городская управа
(`bot.game.city`), и всё, что живые не выиграли, достаётся ей: тишина и
ничья перестали быть исходом выборов. Кнопок у неё нет, поэтому кресла
начальников она занимает сама — по заявкам и стажу.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.city import MAYOR_ID as CITY_MAYOR
from bot.game.city import MAYOR_PROGRAM, name_of
from bot.game.elections import (
    COUNTING,
    NOMINATION,
    PROGRAM_MAX,
    VOTING,
    day_code,
    election_day,
    phase,
    phase_ends,
    term_from,
    winner_of,
)
from bot.game.health import now_ts
from bot.game.police import jail_left
from bot.models import Player

logger = logging.getLogger(__name__)

# Где выдвигаются. Голосуют откуда угодно: бюллетень приходит сам
ADMINISTRATION = "city_administration"


class ElectionError(Exception):
    """Отказ по выборам, который показывают игроку как есть."""


async def nominate(
    db: Database, player: Player, program: str = "", now: int | None = None
) -> str:
    """Выдвинуть свою кандидатуру. Регистрация без разговоров."""
    moment = now_ts() if now is None else now
    if player.where(moment) != ADMINISTRATION:
        raise ElectionError("Кандидатуру выдвигают в администрации города.")
    if phase(moment) != NOMINATION:
        raise ElectionError(
            "Сейчас не выдвигаются. Приём — со среды девяти утра до "
            "субботы восьми вечера на неделе выборов."
        )
    until, _ = await db.jail_term(player.user_id)
    if jail_left(until, moment):
        raise ElectionError("Из тюрьмы в мэры не выдвигаются.")

    day = day_code(election_day(moment))
    text = program.strip()[:PROGRAM_MAX]
    if not await db.nominate(day, player.user_id, text, moment):
        raise ElectionError("Ты уже выдвинулся на эти выборы.")
    logger.info("Кандидат %s на выборы %s", player.user_id, day)
    return "Кандидатура принята. Город тебя увидит в бюллетене."


async def vote(
    db: Database, player: Player, for_id: int, now: int | None = None
) -> str:
    """Отдать свой единственный голос."""
    moment = now_ts() if now is None else now
    if phase(moment) != VOTING:
        raise ElectionError("Голосование сейчас не идёт.")
    day = day_code(election_day(moment))
    standing = {
        int(one["user_id"])
        for one in await _ballot_rows(db, day, moment)
    }
    if for_id not in standing:
        raise ElectionError("Такого кандидата в бюллетене нет.")
    if not await db.cast_ballot(day, player.user_id, for_id, moment):
        raise ElectionError("Ты уже голосовал. Голос один.")
    logger.info("Боец %s отдал голос за %s", player.user_id, for_id)
    return "Голос принят. Спасибо за участие."


def _city_row(day: str) -> dict:
    """Городская управа в бюллетене: кандидат, которого не выдвигали.

    Строки в базе у неё нет и быть не может: управа — не боец, а
    устройство города. В бюллетень её кладёт сам бюллетень, и лежит
    она там всегда, в любых выборах.
    """
    return {
        "day": day,
        "user_id": CITY_MAYOR,
        "program": MAYOR_PROGRAM,
        "at": 0,
        "dropped_at": 0,
    }


async def _ballot_rows(db: Database, day: str, moment: int) -> list[dict]:
    """Кандидаты, которые дошли до бюллетеня.

    Снятых отсеиваем по отметке, а не по тому, сидит ли человек сейчас:
    снимает кандидата сама посадка (`strike_if_running`), и случиться
    она может только пока идёт выдвижение. Сел в понедельник после
    выборов — остаёшься мэром, и это не поблажка: мандат дал город, а
    не участок.

    Последней в списке всегда стоит **городская управа**: город
    выставляет себя сам и снять себя не может. Стоит она последней
    нарочно — живой кандидат в бюллетене идёт первым.
    """
    rows = [one for one in await db.candidates(day) if not one["dropped_at"]]
    return rows + [_city_row(day)]


async def strike_if_running(
    db: Database, user_id: int, now: int | None = None
) -> bool:
    """Снять кандидата, если он сел, пока шло выдвижение.

    Зовёт это тюрьма, а не выборы: снятие — следствие посадки, и ловить
    его потом по дате было бы гаданием. В журнале отсидок лежит только
    последняя, а кандидат мог сесть и выйти ещё до бюллетеня.
    """
    moment = now_ts() if now is None else now
    if phase(moment) != NOMINATION:
        return False
    day = day_code(election_day(moment))
    if not await db.drop_candidate(day, user_id, moment):
        return False
    logger.info("Кандидат %s снят с выборов %s: сел", user_id, day)
    return True


async def settle(db: Database, now: int | None = None) -> dict:
    """Свести прошедшие выборы и посадить мэра, если его час настал.

    Зовётся отовсюду, где спрашивают про мэра или про выборы: в девять
    утра понедельника кнопку никто не нажимает, и смена кресла должна
    случаться сама собой при первом же взгляде.
    """
    moment = now_ts() if now is None else now
    day = election_day(moment)
    # Сводим предыдущие выборы: текущие ещё идут или ещё впереди
    done = _previous(day)
    code = day_code(done)
    if moment < term_from(done):
        return {"day": code, "seated": False}

    record = await db.election(code)
    if record is None:
        tally = await db.tally(code)
        live = {
            int(one["user_id"]) for one in await _ballot_rows(db, code, moment)
        }
        # Голоса за снятого не считаются: он не в бюллетене
        tally = {one: votes for one, votes in tally.items() if one in live}
        winner, votes = winner_of(tally)
        if not winner:
            # Победителя нет: не голосовал никто или вышла ничья. Кресло
            # пустым город не держит — его занимает управа. Так выборы
            # всегда чем-то кончаются, а «никто» перестаёт быть исходом
            winner = CITY_MAYOR
            votes = tally.get(CITY_MAYOR, 0)
        await db.close_election(
            code, winner, votes, sum(tally.values()), moment
        )
        record = await db.election(code)
        logger.info(
            "Выборы %s сведены: победитель %s с %s голосами", code, winner, votes
        )

    seat = await db.mayor()
    if record and record["winner_id"] and seat.get("day") != code:
        await db.seat_mayor(
            int(record["winner_id"]), term_from(done), code
        )
        logger.info("Мэр %s вступил в права", record["winner_id"])
        await _city_appoints(db, moment)
        return {"day": code, "seated": True, "winner": int(record["winner_id"])}
    await _city_appoints(db, moment)
    return {"day": code, "seated": False}


async def _city_appoints(db: Database, moment: int) -> None:
    """Дать городскому мэру занять пустые кресла.

    Зовётся из того же ленивого свода, что и смена кресла: мэр у города
    не нажимает кнопок, и назначать за него должно то, что и так
    случается при каждом взгляде.
    """
    from bot.work_service import city_appoints

    await city_appoints(db, moment)


def _previous(day):
    """Предыдущие выборы — первое воскресенье прошлого месяца."""
    from bot.game.elections import first_sunday

    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return first_sunday(year, month)


async def state(
    db: Database, player: Player, now: int | None = None
) -> dict:
    """Всё о выборах для экрана и для бюллетеня."""
    moment = now_ts() if now is None else now
    await settle(db, moment)
    day = election_day(moment)
    code = day_code(day)
    now_phase = phase(moment)
    rows = await _ballot_rows(db, code, moment)
    counted = await db.tally(code) if now_phase == COUNTING else {}
    seat = await db.mayor()
    mayor_name = ""
    if seat.get("user_id"):
        mayor_name = name_of(int(seat["user_id"]))
        if not mayor_name:
            one = await db.get_player(int(seat["user_id"]))
            mayor_name = one.nickname if one else ""
    names = {}
    for one in rows:
        who_id = int(one["user_id"])
        city = name_of(who_id)
        if city:
            names[who_id] = city
            continue
        who = await db.get_player(who_id)
        names[who_id] = who.nickname if who else "кандидат"
    return {
        "day": code,
        "phase": now_phase,
        "ends_at": phase_ends(moment),
        "seconds_left": max(0, phase_ends(moment) - moment),
        "candidates": [
            {
                "user_id": int(one["user_id"]),
                "name": names.get(int(one["user_id"]), "кандидат"),
                "program": str(one["program"]),
                "votes": counted.get(int(one["user_id"]), 0),
            }
            for one in rows
        ],
        "mine": await db.ballot_of(code, player.user_id),
        "standing": any(
            int(one["user_id"]) == player.user_id for one in rows
        ),
        "mayor": {"user_id": int(seat.get("user_id") or 0), "name": mayor_name},
        "program_max": PROGRAM_MAX,
    }


__all__ = [
    "ADMINISTRATION",
    "ElectionError",
    "nominate",
    "strike_if_running",
    "settle",
    "state",
    "vote",
]
