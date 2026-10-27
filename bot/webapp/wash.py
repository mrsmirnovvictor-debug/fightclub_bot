"""Автомойка глазами мини-аппа: номера и то, что за ними стоит.

Мыть машины город пока только обещает, а номера перебивает уже сейчас —
и это не украшение, а единственный способ снять с машины розыск. Поэтому
экран устроен вокруг одного вопроса: какой у машины номер и что с ним
не так.

**Чужой машины тут нет.** Перебивают своё: то, что на руках у бойца.
Стоящее на парковке сперва забирают — мойка не гараж.

**Про розыск пишем прямо.** Машина в розыске — так и сказано, и сказано
же, что новый номер его снимет. Прятать это незачем: хозяин угнанной
машины и так знает, что её ищут, а угонщик — тем более.
"""

from __future__ import annotations

from typing import Any

from bot.game.cars import RESTAMP_PRICE, get_car
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.models import Player
from bot.webapp.bank import purse_payload


def car_row(player: Player, row: dict, price: int, moment: int) -> dict[str, Any]:
    """Строка машины: номер, прошлое и можно ли его переписать."""
    car = get_car(str(row["code"]))
    stolen = bool(row.get("stolen_from"))
    wanted = bool(row.get("stolen_filed"))
    return {
        "id": int(row["id"]),
        "code": str(row["code"]),
        "title": car.title if car else str(row["code"]),
        "image": car.image if car else "",
        "plate": str(row.get("plate") or ""),
        "stolen": stolen,
        "wanted": wanted,
        "service": bool(car and car.service),
        "can_restamp": bool(
            car and not car.service and player.can_afford(price, moment, Service.WASH)
        ),
        "why": (
            "Служебный номер городской — его не трогают."
            if car and car.service
            else "" if player.can_afford(price, moment, Service.WASH)
            else f"Не хватает: нужно {price} 💰."
        ),
    }


async def build_wash(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Экран мойки: свои машины, их номера и цена нового."""
    from bot.car_service import at_hand

    moment = now_ts() if now is None else now
    place = get_location(player.where(moment))
    price = player.price_here(RESTAMP_PRICE, moment, Service.WASH)
    keys = await at_hand(db, player, moment)
    cars = [car_row(player, row, price, moment) for row in keys]
    return {
        "title": place.title if place else "Автомойка",
        "price": price,
        "full_price": RESTAMP_PRICE,
        "cars": cars,
        "credits": player.credits,
        "purse": purse_payload(player, moment, Service.WASH),
        "note": _note(cars),
        "said": "",
    }


def _note(cars: list[dict[str, Any]]) -> str:
    if not cars:
        return (
            "Машин при тебе нет. Стоящую на парковке сперва забери — "
            "сюда её своим ходом не подать."
        )
    if any(one["wanted"] for one in cars):
        return (
            "Одна из твоих машин в розыске. Новый номер снимет с неё "
            "прошлое — вместе с ним и розыск."
        )
    return "Перебить номер — значит выдать машине новое имя в городе."


__all__ = ["build_wash", "car_row"]
