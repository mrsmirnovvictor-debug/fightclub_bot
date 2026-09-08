"""Цены на вторичном обороте: комиссионка и приём вещей обратно в лавку.

Игрок выставляет свою вещь, другой её покупает, клуб берёт свою долю. Здесь
только арифметика — сколько можно просить и сколько дойдёт до продавца.

Цену держим в рамках лавки: дешевле половины прилавка вещь уходила бы за
бесценок соседу-двойнику, дороже трёх цен — превращала бы комиссионку в
способ печатать кредиты из воздуха. Того, чего в лавке нет вовсе (награды,
товар мага), это не касается: сравнить не с чем, и цену назначает продавец.
"""

from __future__ import annotations

from bot.game.equipment import Item

# Доля клуба с каждой покупки
FEE = 0.05
# Во сколько раз цена может отличаться от прилавка
MIN_SHARE = 0.5
MAX_SHARE = 3.0
# Сколько лавка даёт за сданную вещь — доля от цены на прилавке. Износ на
# это не влияет: лавка берёт вещь как есть, хоть целую, хоть в труху. Доля
# мала намеренно — сдача это не способ заработать, а способ разгрузить
# рюкзак от того, что уже не наденешь.
BUYBACK_SHARE = 0.15


def has_counter_price(item: Item) -> bool:
    """Лежит ли такая вещь на прилавке клуба за кредиты."""
    return bool(item.price) and item.on_sale and not item.is_magic


def price_range(item: Item) -> tuple[int, int] | None:
    """Рамки цены для этой вещи. None — рамок нет, проси сколько хочешь."""
    if not has_counter_price(item):
        return None
    return round(item.price * MIN_SHARE), round(item.price * MAX_SHARE)


def price_is_sane(item: Item, price: int) -> bool:
    if price <= 0:
        return False
    limits = price_range(item)
    if limits is None:
        return True
    return limits[0] <= price <= limits[1]


def buyback(item: Item) -> int:
    """Сколько лавка даст за эту вещь. Ноль — такое она не принимает."""
    if not has_counter_price(item):
        return 0
    return max(1, round(item.price * BUYBACK_SHARE))


def fee_of(price: int) -> int:
    """Сколько заберёт клуб. Округляем вверх — в пользу клуба, не продавца."""
    return -(-int(price) * int(FEE * 100) // 100)


def payout(price: int) -> int:
    """Сколько дойдёт до продавца."""
    return max(0, int(price) - fee_of(price))


__all__ = [
    "BUYBACK_SHARE",
    "FEE",
    "MAX_SHARE",
    "MIN_SHARE",
    "buyback",
    "fee_of",
    "has_counter_price",
    "payout",
    "price_is_sane",
    "price_range",
]
