"""Участок глазами мини-аппа: дежурная часть, розыск и свои дела.

Экран один, а читают его двое, и видят разное. Сотрудник — сводку по
городу: все нападения, кого ищут, что в копилке и как идёт недельная
норма. Пострадавший — только свои дела: по какому написать заявление и
где забрать возвращённое.

**Чужих дел гражданскому не показываем.** Не из скромности: сводка по
городу — рабочий инструмент, и лежать он должен там же, где значок. А
вот своё дело видит и жертва, и она же решает, писать ли заявление.

**Заявление — её право, а не обязанность.** Никто не пишет его за неё,
и срока у него нет: обобрали в среду, пришёл в субботу — ордер всё
равно выпишут.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import format_term, now_ts
from bot.game.locations import get_location
from bot.game.police import ARREST_FEE, QUOTA_BONUS, jail_left
from bot.models import Player


def case_row(one: dict, now: int, mine: bool = False) -> dict[str, Any]:
    """Строка дежурной части: кто, кого, где и чем кончилось."""
    place = get_location(str(one["place"]))
    return {
        "id": int(one["id"]),
        "victim_id": int(one["victim_id"]),
        "suspect_id": int(one["suspect_id"]),
        "place": str(one["place"]),
        "place_title": place.title if place else str(one["place"]),
        "loot": int(one["loot"]),
        "won": bool(one["won"]),
        "at": club_moment(int(one["robbed_at"])) if one["robbed_at"] else "",
        "filed": bool(one["filed_at"]),
        "arrested": bool(one["arrested_at"]),
        "repaid": bool(one["repaid_at"]),
        # Заявление пишет только сам пострадавший и только раз
        "can_file": bool(mine and not one["filed_at"] and not one["arrested_at"]),
        # Деньги забирает он же, и только если их вернули
        "can_collect": bool(
            mine and one["arrested_at"] and not one["repaid_at"] and one["loot"]
        ),
    }


def wanted_row(one: dict, names: dict[int, str]) -> dict[str, Any]:
    """Строка розыска: по кому ордер и на сколько он наворотил."""
    suspect = int(one["suspect_id"])
    return {
        "user_id": suspect,
        "name": names.get(suspect, "неизвестный"),
        "cases": int(one["cases"]),
        "loot": int(one["loot"] or 0),
    }


async def duty_jobs(db, player: Player, now: int) -> list[dict[str, Any]]:
    """Места участка: три ступени и что с каждой у этого бойца.

    Берут здесь, а не в агентстве: в полицию приходят в полицию. Тест
    агентства тут не нужен — его роль играет академия.
    """
    from bot.game.work import hired_at_house
    from bot.work_service import vacancies

    rows = await vacancies(db, player, now, board=hired_at_house("vcpd"))
    out = []
    for row in rows:
        one = row["vacancy"]
        out.append({
            "code": one.code,
            "title": one.title,
            "emoji": one.emoji,
            "salary": one.salary,
            "hours": one.hours,
            "note": one.note,
            "months": one.months,
            "months_text": one.months_text,
            "limited": row["limited"],
            "seats": one.seats,
            "taken": row["taken_seats"],
            "taken_by": row["taken_by"],
            "mine": row["mine"],
            "lacks": row["lacks"],
            "can_take": bool(
                not row["lacks"] and not player.works
                and (not row["limited"] or row["free_seats"])
            ),
        })
    return out


async def build_police_board(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Дежурная часть и свои дела — то, чем участок занят кроме прав."""
    from bot.police_service import board, officers, serves_in_police, term_of

    moment = now_ts() if now is None else now
    badge = serves_in_police(player)
    mine = [
        case_row(one, moment, mine=True)
        for one in await db.cases_of_victim(player.user_id)
    ]
    body: dict[str, Any] = {
        "badge": badge,
        # Свои дела видит каждый: это его дело, а не городская сводка
        "mine": mine,
        "owed": sum(one["loot"] for one in mine if one["can_collect"]),
        "cases": [],
        "wanted": [],
        "vault": 0,
        "officers": 0,
        "week": {},
        "fee": ARREST_FEE,
        "quota_bonus": QUOTA_BONUS,
        "jail": await term_of(db, player.user_id, moment),
        "jobs": await duty_jobs(db, player, moment),
    }
    if not badge:
        return body

    sheet = await board(db, player, None, moment)
    names = await _names(db, sheet)
    body["cases"] = [case_row(one, moment) for one in sheet["cases"]]
    for row, raw in zip(body["cases"], sheet["cases"]):
        row["victim"] = names.get(int(raw["victim_id"]), "боец")
        row["suspect"] = names.get(int(raw["suspect_id"]), "боец")
    body["wanted"] = [wanted_row(one, names) for one in sheet["wanted"]]
    body["vault"] = sheet["vault"]
    body["officers"] = sheet["officers"]
    body["week"] = sheet["week"]
    return body


async def _names(db, sheet: dict) -> dict[int, str]:
    """Ники всех, кто попал в сводку, одним заходом."""
    ids = set()
    for one in sheet["cases"]:
        ids.add(int(one["victim_id"]))
        ids.add(int(one["suspect_id"]))
    for one in sheet["wanted"]:
        ids.add(int(one["suspect_id"]))
    out: dict[int, str] = {}
    for user_id in ids:
        player = await db.get_player(user_id)
        if player is not None:
            out[user_id] = player.nickname
    return out


def jail_note(term: dict[str, int], now: int) -> str:
    """Строка про срок. Пусто — боец не сидит."""
    left = jail_left(int(term.get("until", 0)), now)
    if not left:
        return ""
    return f"Ты сидишь: до освобождения {format_term(left)}."


__all__ = [
    "build_police_board",
    "case_row",
    "duty_jobs",
    "jail_note",
    "wanted_row",
]
