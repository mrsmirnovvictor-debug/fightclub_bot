"""Комиссионка: выставить свою вещь, снять с продажи, купить чужую.

Вещь на комиссии не лежит в двух местах сразу: выставил — она ушла из
рюкзака, снял или продал — вернулась к кому-то в рюкзак. Между этими двумя
состояниями её нет ни у кого, и надеть её нельзя.

Износ переезжает вместе с вещью: покупают её такой, какая есть.
"""

from __future__ import annotations

from typing import Any

from bot.database import Database
from bot.game.equipment import Item, get_item
from bot.game.market import fee_of, payout, price_is_sane, price_range
from bot.models import Player


class MarketError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


def _item_of(code: str) -> Item:
    item = get_item(code)
    if item is None:  # pragma: no cover - вещь пропала из каталога
        raise MarketError("Такой вещи в клубе больше нет.")
    return item


def price_hint(item: Item) -> str:
    """Что написать рядом с полем цены."""
    limits = price_range(item)
    if limits is None:
        return "Такого на прилавке нет — цена любая."
    return f"От {limits[0]} до {limits[1]} 💰"


async def sell_lot(db: Database, player: Player, gear_id: int, price: int) -> int:
    """Выставить свою вещь. Возвращает номер лота."""
    owned = next((row for row in player.gear if row.id == gear_id), None)
    if owned is None:
        raise MarketError("Этой вещи у тебя нет.")
    if owned.slot is not None:
        raise MarketError(
            f"«{owned.title}» на тебе надета. Сними её — тогда и выставляй."
        )
    if not price_is_sane(owned.item, price):
        limits = price_range(owned.item)
        raise MarketError(
            "Цена не та: за «{title}» просят от {low} до {high} 💰.".format(
                title=owned.title, low=limits[0], high=limits[1]
            )
            if limits
            else "Цена должна быть больше нуля."
        )

    await db.delete_gear(owned.id)
    player.gear = [row for row in player.gear if row.id != owned.id]
    return await db.add_lot(
        seller_id=player.user_id,
        code=owned.code,
        wear=owned.wear,
        max_wear=owned.max_wear,
        price=int(price),
    )


async def withdraw_lot(db: Database, player: Player, lot_id: int) -> str:
    """Снять свою вещь с продажи. Возвращает её название."""
    lot = await db.market_lot(lot_id)
    if lot is None:
        raise MarketError("Этого лота уже нет.")
    if lot["seller_id"] != player.user_id:
        raise MarketError("Снять с продажи может только тот, кто выставил.")
    if not await db.take_lot(lot_id):  # pragma: no cover - забрали между двумя строками
        raise MarketError("Этого лота уже нет.")

    owned = await db.add_gear(
        player.user_id, lot["code"], max_wear=lot["max_wear"], wear=lot["wear"]
    )
    player.gear.append(owned)
    return owned.title


async def buy_lot(db: Database, player: Player, lot_id: int) -> dict[str, Any]:
    """Купить чужую вещь. Возвращает, что купили и за сколько."""
    lot = await db.market_lot(lot_id)
    if lot is None:
        raise MarketError("Этот лот уже разобрали.")
    if lot["seller_id"] == player.user_id:
        raise MarketError("Это твой же лот. Сними его с продажи, а не покупай.")
    item = _item_of(lot["code"])
    price = int(lot["price"])
    if not player.can_afford(price):
        raise MarketError(
            f"Не хватает кредитов: «{item.title}» стоит {price} 💰, "
            f"а на счету {player.credits} 💰."
        )
    # Лот забирает тот, чей DELETE прошёл первым: деньги считаем уже после
    if not await db.take_lot(lot_id):
        raise MarketError("Этот лот уже разобрали.")

    player.pay(price)
    await db.save_player(player)
    owned = await db.add_gear(
        player.user_id, lot["code"], max_wear=lot["max_wear"], wear=lot["wear"]
    )
    player.gear.append(owned)

    earned = payout(price)
    seller = await db.get_player(lot["seller_id"])
    if seller is not None:  # pragma: no branch - продавец мог удалить персонажа
        seller.grant_credits(earned)
        await db.save_player(seller)
    return {
        "title": item.title,
        "price": price,
        "fee": fee_of(price),
        "earned": earned,
        "seller": lot["seller"] or "боец без имени",
    }


__all__ = ["MarketError", "buy_lot", "price_hint", "sell_lot", "withdraw_lot"]
