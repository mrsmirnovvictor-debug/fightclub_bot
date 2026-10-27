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
from bot.game.police import (
    ARREST_FEE,
    FINE_NO_LICENCE,
    QUOTA_BONUS,
    jail_left,
)
from bot.game.turf import owner_of_service
from bot.models import Player
from bot.police_service import traffic_sheet
from bot.webapp.bonus import build_bonus


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
    bids = await db.bids_of(player.user_id)
    out = []
    for row in rows:
        one = row["vacancy"]
        out.append({
            "code": one.code,
            "title": one.title,
            "emoji": one.emoji,
            "salary": one.salary,
            "salary_max": one.salary_max,
            "pay_text": one.pay_text,
            # Кресло начальника не занимают: на него подают заявку, а
            # сажает мэр — он же называет жалованье из вилки
            "appointed": one.appointed,
            "bid": one.code in bids,
            "can_bid": bool(
                one.appointed and not row["lacks"] and not row["mine"]
            ),
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
                not row["lacks"] and not player.works and not one.appointed
                and (not row["limited"] or row["free_seats"])
            ),
        })
    return out


CHIEF_JOB = "police_chief"


async def stolen_rows(db, player: Player, now: int) -> list[dict[str, Any]]:
    """Что у бойца угнали: номер, когда и написано ли заявление."""
    from bot.game.cars import get_car

    out = []
    for row in await db.stolen_from(player.user_id, now):
        car = get_car(str(row["code"]))
        out.append(
            {
                "id": int(row["id"]),
                "code": str(row["code"]),
                "title": car.title if car else str(row["code"]),
                "plate": str(row["plate"]),
                "at": club_moment(int(row["stolen_at"])) if row["stolen_at"] else "",
                "filed": bool(row["stolen_filed"]),
                # Кто на ней ездит, пострадавшему не говорим: это дело
                # полиции, а не повод идти разбираться самому
                "can_file": not row["stolen_filed"],
            }
        )
    return out


async def parking_rows(db, player: Player, now: int) -> list[dict[str, Any]]:
    """Стоянка у участка: что поставить и что забрать.

    Своё и только своё: в отличие от гаража особняка, городская стоянка
    ничего никому не передаёт — она просто безопасна.
    """
    from bot.car_service import mine as my_cars
    from bot.game.cars import IN_POLICE, POLICE_PARKING, get_car

    rows = await my_cars(db, player, now)
    out = []
    for row in rows:
        car = get_car(str(row["code"]))
        if car is None or car.service:
            continue
        parked = str(row.get("parked") or "")
        if parked and parked != IN_POLICE:
            continue
        out.append(
            {
                "code": str(row["code"]),
                "title": car.title,
                "plate": str(row.get("plate") or ""),
                "here": parked == IN_POLICE,
                "stolen": bool(row.get("stolen_from")),
            }
        )
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
        # Угнанное у этого бойца: по нему он и пишет заявление. Видит
        # это каждый — своё дело, а не городская сводка
        "stolen": await stolen_rows(db, player, moment),
        # Стоянка у участка: городская и безопасная, как двор особняка
        "parking": await parking_rows(db, player, moment),
        "traffic": [],
        "fine": FINE_NO_LICENCE,
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
    body["office"] = await office_payload(db, player, sheet)
    # Гараж участка: две патрульные и машина начальника. Видят его все
    # сотрудники — машины выдают им, а не кабинету, — и стоит он на
    # экране дежурной части, а не за дверью начальника
    body["garage"] = await garage_shelf(db, player, moment)
    # Бонусный пулл участка видит каждый сотрудник: зарабатывают его
    # вместе. Менять дележ может только начальник — это его настройка,
    # и она лежит у службы, а не у человека
    # Автоучёт: все живые машины города с номерами и хозяевами. Это
    # рабочая бумага патруля — по ней и видно, кого останавливать
    body["traffic"] = await traffic_sheet(db, player, moment)
    body["fine"] = FINE_NO_LICENCE
    body["bonus"] = await build_bonus(
        db, owner_of_service("police"), moment,
        can_split=player.job_code == CHIEF_JOB,
    )
    return body


# Кабинет начальника: форма и склад. Числа взяты у семьи: пятнадцать
# ячеек склада — кабинет для города ровно то же, чем особняк для семьи,
# и считать их разными мерками не из чего. Машин в кабинете нет: они
# стоят в гараже участка, потому что выдают их сотрудникам


async def garage_shelf(db, player: Player, now: int) -> list[dict[str, Any]]:
    """Гараж участка глазами сотрудника: места, разъезды и своя машина.

    Строки собирает экран машин — те же, что в таксопарке: гараж один и
    тот же, меняется только дом, в котором он стоит.
    """
    from bot.car_service import mine as my_cars, release_strangers
    from bot.game.cars import POLICE_PLACE, garage_of
    from bot.webapp.cars import garage_row

    await release_strangers(db, POLICE_PLACE, now)
    own = {one["code"]: one for one in await my_cars(db, player, now)}
    return [
        await garage_row(db, car, player, now, own)
        for car in garage_of(POLICE_PLACE)
    ]


async def office_payload(
    db, player: Player, sheet: dict[str, Any]
) -> dict[str, Any]:
    """Кабинет начальника. Пусто — боец не в кресле.

    Кабинет один на город, и открывает его не значок, а должность: в
    дежурную часть ходят все сотрудники, за форму и казну отвечает
    начальник.
    """
    from bot.duty_service import RACK_SLOTS, rack_rows, store_rows
    from bot.game.work import get_vacancy
    from bot.webapp.card import item_payload

    if player.job_code != CHIEF_JOB:
        return {}
    vacancy = get_vacancy(CHIEF_JOB)
    rack = rack_rows(player, CHIEF_JOB)
    stored = await store_rows(db, player)
    return {
        "post": vacancy.title if vacancy else "Начальник полиции",
        "vault": sheet["vault"],
        "officers": sheet["officers"],
        "week": sheet["week"],
        "salary": vacancy.salary if vacancy else 0,
        "rack": rack,
        "taken": sum(1 for one in rack if one["taken"]),
        # Склад кабинета — те же полки, что у семьи: вещь переезжает
        # сюда целиком и так же возвращается
        "store": stored,
        "slots": RACK_SLOTS,
        "free": max(0, RACK_SLOTS - len(stored)),
        # Что можно положить: снятое из рюкзака. Надетое на полку не
        # ложится — сперва его снимают в инвентаре
        "bag": [
            item_payload(player, owned)
            for owned in player.backpack
            if not owned.item.is_duty
        ],
    }


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
