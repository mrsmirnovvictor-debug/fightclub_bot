"""Жилой дом глазами мини-аппа: список квартир, сейф и кладовая.

Правил здесь нет — их держит `bot.game.estate`, действия живут в
`bot.estate_service`; тут перевод состояния в json, который умеет
нарисовать страница.

Экран один на все четыре дома и собирается из трёх кусков: подъезд
(этажи с квартирами — что свободно, что занято и кем), своя квартира
(срок, сейф, кладовая) и кошелёк. Чужую квартиру показываем занятой и с
ником жильца: дом общий, и по нему должно быть видно, к кому стучаться
бессмысленно.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.estate import (
    FLAT_SLOTS,
    GRACE_DAYS,
    RENT_DAYS,
    RENT_PRICE,
    ROOM_SHORT,
    ROOM_TITLES,
    Kept,
    Lease,
    flats_of,
)
from bot.game.health import format_term, now_ts
from bot.game.locations import Service, get_location
from bot.models import Player
from bot.webapp.bank import purse_payload


def rooms_payload(house: str, taken: dict[int, dict]) -> list[dict[str, Any]]:
    """Что в этом доме сдаётся: цена, вместимость и сколько свободно."""
    rows: list[dict[str, Any]] = []
    for rooms in sorted({flat.rooms for flat in flats_of(house)}):
        all_flats = [flat for flat in flats_of(house) if flat.rooms == rooms]
        rows.append(
            {
                "rooms": rooms,
                "title": ROOM_TITLES[rooms],
                "short": ROOM_SHORT[rooms],
                "price": RENT_PRICE[rooms],
                "slots": FLAT_SLOTS[rooms],
                "total": len(all_flats),
                "free": sum(1 for flat in all_flats if flat.number not in taken),
            }
        )
    return rows


def flat_row(
    flat, taken: dict[int, dict], player: Player, moment: int
) -> dict[str, Any]:
    """Строка квартиры в подъезде: адрес, цена и кто за дверью."""
    tenant = taken.get(flat.number)
    mine = bool(tenant and tenant["user_id"] == player.user_id)
    homeless = player.flat is None
    return {
        "number": flat.number,
        "floor": flat.floor,
        "rooms": flat.rooms,
        "rooms_title": flat.rooms_title,
        "rooms_short": ROOM_SHORT[flat.rooms],
        "title": flat.title,
        "price": player.price_here(flat.price, moment, Service.ESTATE),
        "slots": flat.slots,
        "taken": tenant is not None,
        "mine": mine,
        # Ник жильца: по нему видно, что дверь не просто занята
        "tenant": "" if mine else (tenant["nickname"] if tenant else ""),
        # Снять можно свободную, и только если своей квартиры нет вовсе
        "can_rent": tenant is None and homeless,
    }


def lease_payload(lease: Lease, moment: int) -> dict[str, Any]:
    """Своя квартира: адрес, срок и что с ним будет дальше."""
    place = get_location(lease.house)
    paid = lease.paid(moment)
    return {
        "house": lease.house,
        "house_title": place.title if place else lease.house,
        "number": lease.number,
        "floor": lease.floor,
        "rooms": lease.rooms,
        "rooms_title": lease.rooms_title,
        "title": lease.title,
        "price": lease.price,
        "slots": lease.slots,
        "rented": club_moment(lease.rented_at) if lease.rented_at else "",
        "paid_until": club_moment(lease.paid_until) if lease.paid_until else "",
        "paid": paid,
        "locked": lease.locked(moment),
        # Сколько осталось: оплаченного времени или просрочки до выселения
        "left": format_term(lease.seconds_left(moment)) if paid else "",
        "grace_left": (
            format_term(lease.grace_left(moment)) if not paid else ""
        ),
        "vault": lease.vault,
    }


def kept_row(player: Player, kept: Kept, moment: int) -> dict[str, Any]:
    """Вещь из кладовой: та же строка, что в рюкзаке, плюс когда положили.

    Строку собирает рюкзачный `item_payload`: вещь в кладовой — та же
    вещь. Зовётся он отсюда, а не сверху файла, по той же причине, что и
    у клановых полок: карточка сама берёт у экранов свои куски, и
    встречный импорт закольцевал бы оба.
    """
    from bot.game.equipment import get_item
    from bot.game.gear import OwnedItem
    from bot.webapp.card import item_payload

    item = get_item(kept.code)
    if item is None:  # pragma: no cover - вещь выкинули из раздачи
        return {}
    owned = OwnedItem(
        item=item,
        id=kept.id,
        wear=kept.wear,
        max_wear=kept.max_wear,
        mod=kept.mod,
        mod_value=kept.mod_value,
    )
    row = item_payload(player, owned)
    row["when"] = club_moment(kept.put_at) if kept.put_at else ""
    return row


def pantry_payload(
    player: Player, lease: Lease | None, items: list[Kept], moment: int
) -> dict[str, Any]:
    """Сейф и кладовая своей квартиры. Пусто — жилья нет."""
    from bot.webapp.card import item_payload

    if lease is None:
        return {
            "mine": False,
            "vault": 0,
            "slots": 0,
            "free": 0,
            "locked": True,
            "items": [],
            "bag": [],
        }
    rows = [row for row in (kept_row(player, one, moment) for one in items) if row]
    return {
        "mine": True,
        "vault": lease.vault,
        "slots": lease.slots,
        "free": max(0, lease.slots - len(rows)),
        # Срок вышел — ни положить, ни взять: не платил, не входишь
        "locked": lease.locked(moment),
        "items": rows,
        "bag": [item_payload(player, owned) for owned in player.backpack],
    }


def parking_payload(
    lease: Lease | None,
    cars: list[dict] | None,
    keys: list[dict] | None,
    moment: int,
) -> dict[str, Any]:
    """Место под машину при своей квартире. Пусто — жилья нет.

    Место одно на квартиру, какой бы она ни была: комнаты считают
    кладовую, а двор у дома один. Машина на нём остаётся хозяйской —
    парковка это место, а не передача.
    """
    from bot.game.cars import FLAT_PARKING

    if lease is None:
        return {
            "mine": False, "slots": 0, "free": 0, "locked": True,
            "cars": [], "keys": [],
        }
    rows = list(cars or [])
    return {
        "mine": True,
        "slots": FLAT_PARKING,
        "free": max(0, FLAT_PARKING - len(rows)),
        # Срок вышел — ни поставить, ни забрать: не платил, не входишь
        "locked": lease.locked(moment),
        "cars": rows,
        # Поставить можно то, что при себе: стоящую машину второй раз не
        # поставить, служебную не поставить вовсе
        "keys": [
            one for one in (keys or [])
            if not one["parked"] and not one["service"]
        ],
    }


def house_note(player: Player, house: str, moment: int) -> str:
    """Строка сверху экрана: что тут делают."""
    lease = player.flat
    if lease is None:
        return (
            f"Квартиры сдают на {RENT_DAYS} дней: {RENT_PRICE[1]} 💰 за "
            f"однокомнатную, {RENT_PRICE[2]} 💰 за двухкомнатную и "
            f"{RENT_PRICE[3]} 💰 за трёхкомнатную. В каждой сейф и кладовая."
        )
    if lease.house != house:
        at = get_location(lease.house)
        return (
            f"Ты снимаешь {lease.title} в «{at.title if at else lease.house}». "
            "Квартира на бойца одна: здесь можно только посмотреть."
        )
    if lease.locked(moment):
        left = format_term(lease.grace_left(moment))
        return (
            f"Аренда {lease.title} кончилась — квартира заперта. Продли её, "
            f"иначе через {left} жильё сдадут другому, а вещи и кредиты "
            "вернутся тебе."
        )
    return (
        f"Твоя {lease.rooms_title} {lease.title}, {lease.floor}-й этаж. "
        f"Оплачена ещё {format_term(lease.seconds_left(moment))}."
    )


def build_house(
    player: Player,
    house: str,
    taken: dict[int, dict],
    items: list[Kept] | None = None,
    now: int | None = None,
    parking: list[dict] | None = None,
    keys: list[dict] | None = None,
) -> dict[str, Any]:
    """Экран жилого дома: подъезд, своя квартира, сейф и кладовая.

    `taken` — кто живёт в этом доме: {номер: жилец}. Свободные квартиры
    в базе не лежат вовсе, их считает планировка, — поэтому список
    этажей собирается здесь, а не приходит из базы.
    """
    moment = now_ts() if now is None else now
    place = get_location(house)
    lease = player.flat
    here = lease if lease is not None and lease.house == house else None
    flats = flats_of(house)
    levels = sorted({flat.floor for flat in flats})
    return {
        "house": house,
        "title": place.title if place else house,
        "floors": len(levels),
        "flats": len(flats),
        "rent_days": RENT_DAYS,
        "grace_days": GRACE_DAYS,
        "credits": player.credits,
        # Чем платят за жильё: тот же выбор кошелька, что в лавке
        "purse": purse_payload(player, moment, Service.ESTATE),
        "rooms": rooms_payload(house, taken),
        "levels": [
            {
                "level": level,
                "flats": [
                    flat_row(flat, taken, player, moment)
                    for flat in flats
                    if flat.floor == level
                ],
            }
            for level in levels
        ],
        # Своя квартира: здешняя или в другом доме — на экране это разные
        # вещи, и страница не должна их путать
        "mine": lease_payload(here, moment) if here else {},
        "elsewhere": (
            lease_payload(lease, moment)
            if lease is not None and here is None
            else {}
        ),
        "pantry": pantry_payload(player, here, items or [], moment),
        # Место под машину: оно при квартире, и смотрят его там же, где
        # кладовую, — это та же полка, только для машины
        "parking": parking_payload(here, parking, keys, moment),
        "note": house_note(player, house, moment),
        "said": "",
    }


__all__ = [
    "build_house",
    "flat_row",
    "house_note",
    "kept_row",
    "lease_payload",
    "parking_payload",
    "pantry_payload",
    "rooms_payload",
]
