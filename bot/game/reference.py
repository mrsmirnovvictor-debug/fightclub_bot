"""Эталонный боец: как выглядит осмысленно прокачанный игрок на своём уровне.

На этих сборках сходится баланс — их гоняет и симулятор, и тесты. Прокачка
здесь не крайность: профильная характеристика, немного силы (без неё нечем
бить) и немного выносливости. Билд, который полностью сливает силу, играет
заведомо слабее, и это нормально.
"""

from __future__ import annotations

from bot.game.classes import START_POINTS, FighterClass, Stat, Stats
from bot.game.economy import MICRO_UPS_PER_LEVEL
from bot.game.equipment import ALL_SLOTS, SHOWCASE, Equipment, Item, OwnedItem, Slot

# Во что вкладывается боец каждого класса, по кругу
FOCUS: dict[str, tuple[str, ...]] = {
    "warrior": ("strength", "endurance", "strength", "agility"),
    "rogue": ("agility", "strength", "endurance", "agility"),
    "assassin": ("intuition", "strength", "endurance", "intuition"),
    "tank": ("endurance", "strength", "endurance", "intuition"),
}


def developed_stats(fclass: FighterClass, level: int) -> Stats:
    """Характеристики бойца этого уровня: база, очко за уровень, апы и старт."""
    # Очко выносливости за каждый уровень приходит само, его никто не тратит
    stats = fclass.base_stats.plus(Stat.ENDURANCE, level - 1)
    plan = FOCUS[fclass.code]
    for step in range(START_POINTS + MICRO_UPS_PER_LEVEL * (level - 1)):
        stats = stats.plus(Stat(plan[step % len(plan)]))
    return stats


def best_kit(fclass: FighterClass, level: int) -> dict[Slot, Item]:
    """Лучшее, что боец этого класса мог купить к своему уровню."""
    kit: dict[Slot, Item] = {}
    for slot in ALL_SLOTS:
        options = [
            item
            for item in SHOWCASE
            if item.slot is slot and item.level_required <= level
        ]
        mine = [item for item in options if fclass.code in item.for_classes] or options
        if mine:
            kit[slot] = max(mine, key=lambda item: (item.level_required, item.price))
    return kit


def reference_equipment(fclass: FighterClass, level: int) -> Equipment:
    """Полный комплект своего уровня, надетый по слотам."""
    return Equipment(
        items={
            slot: OwnedItem(item=item, slot=slot)
            for slot, item in best_kit(fclass, level).items()
        }
    )


def kit_price(fclass: FighterClass, level: int) -> int:
    return sum(item.price for item in best_kit(fclass, level).values())
