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

**Сила у мэра пока одна — имя.** Кресло занято, срок идёт, город видит,
кто его занял. Чего мэр может, здесь не решается: полномочий ему ещё не
выдали, и выдумывать их в служебном модуле неправильно.
"""

from __future__ import annotations

import logging

from bot.database import Database
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


async def _ballot_rows(db: Database, day: str, moment: int) -> list[dict]:
    """Кандидаты, которые дошли до бюллетеня.

    Снятых отсеиваем по отметке, а не по тому, сидит ли человек сейчас:
    снимает кандидата сама посадка (`strike_if_running`), и случиться
    она может только пока идёт выдвижение. Сел в понедельник после
    выборов — остаёшься мэром, и это не поблажка: мандат дал город, а
    не участок.
    """
    return [one for one in await db.candidates(day) if not one["dropped_at"]]


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
        return {"day": code, "seated": True, "winner": int(record["winner_id"])}
    return {"day": code, "seated": False}


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
        one = await db.get_player(int(seat["user_id"]))
        mayor_name = one.nickname if one else ""
    names = {}
    for one in rows:
        who = await db.get_player(int(one["user_id"]))
        names[int(one["user_id"])] = who.nickname if who else "кандидат"
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
