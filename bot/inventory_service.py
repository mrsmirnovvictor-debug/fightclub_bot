"""Инвентарь: покупка, надевание, починка и износ после боёв.

Здесь собраны все действия с вещами — ими пользуются и мини-апп, и лавка
в личке бота, чтобы правила были одни на всех.
"""

from __future__ import annotations

import random

from bot.database import Database
from bot.game.equipment import (
    REPAIR_PRICE_PER_POINT,
    Item,
    OwnedItem,
    RepairResult,
    Slot,
    apply_fight_wear,
    describe_requirements,
    get_item,
    repair,
)
from bot.models import Player


class InventoryError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


async def buy(db: Database, player: Player, code: str) -> OwnedItem:
    """Купить вещь в лавке. Надевать можно потом — хоть через пять уровней."""
    item: Item | None = get_item(code)
    if item is None:
        raise InventoryError("Такого товара в лавке нет.")
    if player.level < item.level_required:
        raise InventoryError(
            f"«{item.title}» открывается на {item.level_required} уровне, "
            f"а у тебя {player.level}."
        )
    if not player.can_afford(item.price):
        raise InventoryError(
            f"Не хватает кредитов: «{item.title}» стоит {item.price} 💰, "
            f"а на счету {player.credits} 💰."
        )

    player.pay(item.price)
    await db.save_player(player)
    owned = await db.add_gear(player.user_id, item.code)
    player.gear.append(owned)
    return owned


async def equip(
    db: Database, player: Player, item_id: int, slot: Slot | None = None
) -> OwnedItem:
    """Надеть вещь из инвентаря. Занятый слот освобождается сам."""
    owned = player.find_gear(item_id)
    if owned is None:
        raise InventoryError("Такой вещи в инвентаре нет.")
    if owned.is_equipped:
        raise InventoryError(f"«{owned.title}» уже надета.")

    target = slot or owned.item.slots[0]
    if target not in owned.item.slots:
        raise InventoryError(f"«{owned.title}» в этот слот не надевается.")
    if not player.can_equip(owned.item):
        raise InventoryError(
            f"Пока не по плечу: нужно {describe_requirements(owned.item)}."
        )

    busy = player.gear_in_slot(target)
    if busy is not None:
        busy.slot = None
        await db.save_gear(busy)

    owned.slot = target
    await db.save_gear(owned)
    return owned


async def unequip(db: Database, player: Player, slot: Slot) -> OwnedItem:
    """Снять вещь со слота — она вернётся в инвентарь."""
    owned = player.gear_in_slot(slot)
    if owned is None:
        raise InventoryError("Слот и так пуст.")
    owned.slot = None
    await db.save_gear(owned)
    return owned


async def repair_item(
    db: Database,
    player: Player,
    item_id: int,
    points: int | None = None,
    rng: random.Random | None = None,
) -> RepairResult:
    """Починить вещь за кредиты: один пункт износа — один кредит."""
    owned = player.find_gear(item_id)
    if owned is None:
        raise InventoryError("Такой вещи в инвентаре нет.")
    if owned.wear <= 0:
        raise InventoryError(f"«{owned.title}» и так как новая.")

    points = owned.wear if points is None else max(0, min(points, owned.wear))
    if points <= 0:
        raise InventoryError("Чинить нужно хотя бы на один пункт.")
    price = points * REPAIR_PRICE_PER_POINT
    if not player.can_afford(price):
        raise InventoryError(
            f"Не хватает кредитов: починка на {points} — это {price} 💰, "
            f"а на счету {player.credits} 💰."
        )

    result = repair(owned, points, rng)
    player.pay(result.price)
    await db.save_player(player)
    if result.destroyed:
        await db.delete_gear(owned.id)
        player.drop_gear(owned)
    else:
        await db.save_gear(owned)
    return result


async def wear_after_fight(
    db: Database, player: Player, won: bool, rng: random.Random | None = None
) -> list[OwnedItem]:
    """Пройтись износом по надетому. Вернуть то, что рассыпалось в труху."""
    damaged, broken = apply_fight_wear(player.equipped, won, rng)
    for owned in damaged:
        if owned.is_worn_out:
            await db.delete_gear(owned.id)
            player.drop_gear(owned)
        else:
            await db.save_gear(owned)
    return broken
