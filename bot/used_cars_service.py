"""Авторынок: выставить свою машину, снять с продажи и купить чужую.

Правила и цены — в `bot.game.used_cars`, здесь дела. Устроено оно как
комиссионка для вещей (`bot.market_service`), и расхождения с ней
только там, где машина не вещь.

**Выставленная машина с бойца снята.** Она стоит на рынке, а не в его
гараже: за руль её не сядешь, место под машину она не занимает, и
второй раз её не продашь. Снял с продажи — вернулась.

**Продают только свою насовсем.** У снятой в салоне машины свой срок и
свой хозяин, у служебной — гараж; ни ту, ни другую боец продать не
может. Оставленную на парковке тоже: она лежит в чужом гараже, и
сперва за ней надо сходить.

**Место под машину проверяют у покупателя, а не у продавца.** Машин на
руках одна, с подпиской три (`docs/cars.md`), и купить сверх этого
нельзя — иначе рынок обходил бы правило, которое салон соблюдает.
Прав при этом не спрашивают: купить можно и без них, ехать — нет.
"""

from __future__ import annotations

import logging
from typing import Any

from bot.database import Database
from bot.game.cars import get_car
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.game.used_cars import (
    fee_of,
    payout,
    price_hint,
    price_is_sane,
    sellable,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class UsedCarError(Exception):
    """Отказ рынка, который показывают игроку как есть."""


def _car_of(code: str):
    car = get_car(code)
    if car is None:
        raise UsedCarError("Такой машины в городе больше нет.")
    return car


async def sell_car(
    db: Database, player: Player, code: str, price: int,
    now: int | None = None,
) -> int:
    """Выставить свою машину. Отдаёт номер лота."""
    moment = now_ts() if now is None else now
    car = _car_of(code)
    row = await db.car_row(player.user_id, code)
    if row is None:
        raise UsedCarError("Такой машины у тебя нет.")
    if not sellable(car):
        raise UsedCarError(
            f"«{car.title}» — служебная машина. Её сдают в гараж, а не "
            "продают."
        )
    if row["until"]:
        raise UsedCarError(
            f"«{car.title}» у тебя в аренде, а не своя. Продать можно только "
            "выкупленную насовсем."
        )
    if row["parked"]:
        raise UsedCarError(
            f"«{car.title}» стоит на парковке. Сперва забери её оттуда."
        )
    if player.driving(moment) == code:
        raise UsedCarError(
            f"Ты за рулём «{car.title}». Выйди из машины — тогда и продавай."
        )
    if not price_is_sane(car, price):
        raise UsedCarError(
            f"Цена не та: за «{car.title}» просят {price_hint(car)}."
        )

    if not await db.drop_car(player.user_id, code):  # pragma: no cover
        raise UsedCarError("Такой машины у тебя нет.")
    lot = await db.add_car_lot(player.user_id, code, int(price))
    logger.info(
        "Боец %s выставил %s за %s (лот %s)", player.user_id, code, price, lot
    )
    return lot


async def withdraw_car(
    db: Database, player: Player, lot_id: int, now: int | None = None
) -> str:
    """Снять свою машину с продажи. Отдаёт её название."""
    moment = now_ts() if now is None else now
    lot = await db.car_lot(lot_id)
    if lot is None:
        raise UsedCarError("Этого лота уже нет.")
    if int(lot["seller_id"]) != player.user_id:
        raise UsedCarError("Снять с продажи может только тот, кто выставил.")
    car = _car_of(str(lot["code"]))
    await _check_room(db, player, moment, car.title)
    if not await db.take_car_lot(lot_id):  # pragma: no cover - забрали между строк
        raise UsedCarError("Этого лота уже нет.")

    await db.put_car(player.user_id, car.code, 0, moment)
    logger.info("Боец %s снял с продажи %s", player.user_id, car.code)
    return car.title


async def buy_car(
    db: Database, player: Player, lot_id: int, now: int | None = None
) -> dict[str, Any]:
    """Купить чужую машину. Отдаёт, что купили и за сколько."""
    moment = now_ts() if now is None else now
    lot = await db.car_lot(lot_id)
    if lot is None:
        raise UsedCarError("Эту машину уже забрали.")
    if int(lot["seller_id"]) == player.user_id:
        raise UsedCarError(
            "Это твой же лот. Сними его с продажи, а не покупай."
        )
    car = _car_of(str(lot["code"]))
    price = int(lot["price"])
    if await db.car_row(player.user_id, car.code) is not None:
        raise UsedCarError(
            f"«{car.title}» у тебя уже есть: двух одинаковых машин не бывает."
        )
    await _check_room(db, player, moment, car.title)
    if not player.can_afford(price, moment, Service.CARS):
        raise UsedCarError(
            f"Не хватает кредитов: «{car.title}» стоит {price} 💰, "
            f"а {player.purse_note(moment, Service.CARS)}."
        )
    # Машину забирает тот, чей DELETE прошёл первым: деньги считаем после
    if not await db.take_car_lot(lot_id):
        raise UsedCarError("Эту машину уже забрали.")

    player.pay(price, moment, Service.CARS, about=f"Покупка {car.title}")
    await db.save_player(player)
    await db.put_car(player.user_id, car.code, 0, moment)

    earned = payout(price)
    seller = await db.get_player(int(lot["seller_id"]))
    if seller is not None:  # pragma: no branch - продавец мог удалиться
        seller.grant_credits(earned)
        await db.save_player(seller)
    logger.info(
        "Боец %s купил %s за %s у бойца %s",
        player.user_id, car.code, price, lot["seller_id"],
    )
    return {
        "title": car.title,
        "price": price,
        "fee": fee_of(price),
        "earned": earned,
        "seller": lot["seller"] or "боец без имени",
    }


async def _check_room(
    db: Database, player: Player, now: int, title: str
) -> None:
    """Влезет ли бойцу ещё одна машина. Нет — отказ теми же словами."""
    from bot.car_service import room_for_another
    from bot.game.cars import cars_allowed

    if await room_for_another(db, player, now):
        return
    limit = cars_allowed(player.is_pro(now))
    raise UsedCarError(
        f"«{title}» некуда девать: машин на руках {limit} — это предел. "
        "Поставь одну на парковку или продай."
    )


__all__ = ["UsedCarError", "buy_car", "sell_car", "withdraw_car"]
