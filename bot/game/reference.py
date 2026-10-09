"""Эталонный боец: как выглядит осмысленно прокачанный игрок на своём уровне.

На этих сборках сходится баланс — их гоняет и симулятор, и тесты. Прокачка
здесь не крайность: профильная характеристика, немного силы (без неё нечем
бить) и немного выносливости. Билд, который полностью сливает силу, играет
заведомо слабее, и это нормально.
"""

from __future__ import annotations

from bot.game.classes import START_POINTS, FighterClass, Stat, Stats
from bot.game.economy import MICRO_UPS_PER_LEVEL
from bot.game.equipment import (
    ALL_SLOTS,
    FAN_ITEMS,
    JEWEL_ITEMS,
    RING_SLOTS,
    SHOWCASE,
    Equipment,
    Item,
    OwnedItem,
    Slot,
)

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


def fan_kit(fclass: FighterClass, level: int) -> dict[Slot, Item]:
    """Комплект из фанатского магазина: своё — оттуда, остальное клубное.

    Фанатская экипировка стоит десять уровней дохода за вещь, поэтому в
    эталон она не идёт. Но носить её будут — и в бою против такого же
    одетого круг классов обязан держаться, иначе тематический магазин
    ломает игру тем, кто до него добрался. Этим комплектом круг и
    проверяется; им же одевают фанатских NPC.
    """
    kit = best_kit(fclass, level)
    for slot in ALL_SLOTS:
        mine = [
            item
            for item in FAN_ITEMS
            if item.slot is slot
            and item.level_required <= level
            and fclass.code in item.for_classes
        ]
        # Чужую линию не подбираем: танк не наденет беговые кроссовки,
        # даже если в его слоте пусто, — он донашивает клубное
        if mine:
            kit[slot] = max(mine, key=lambda item: item.price)
    return kit


def fan_only_kit(fclass: FighterClass, level: int) -> dict[Slot, Item]:
    """Одна фанатская линия и ничего больше — форма, а не гардероб.

    Этим одевают гопников. Игроку фанатское добирается клубным
    (`fan_kit`): он ходит в своём и докупает, что приглянулось. Гопник же
    не покупатель — на нём форма сектора, и если в линии чего-то нет, то
    у него этого нет вовсе. Перчаток в линии нет — значит, дерётся
    голыми руками, а не в клубных.

    Вторую руку отсюда всё равно не возьмут: `boss_kit` наполняет её
    только тем, что написано у самого бойца. Но сюда она попадает — на
    случай, если этим комплектом однажды оденут не NPC.
    """
    return {
        slot: max(mine, key=lambda item: item.price)
        for slot in ALL_SLOTS
        if (
            mine := [
                item
                for item in FAN_ITEMS
                if item.slot is slot
                and item.level_required <= level
                and fclass.code in item.for_classes
            ]
        )
    }


def jewel_kit(fclass: FighterClass, level: int) -> dict[Slot, Item]:
    """Клубный гардероб плюс украшения: ожерелье и три одинаковых кольца.

    У ювелира прилавок свой, и в эталон он не идёт: клетки под украшения
    пустуют у каждого, кто к ювелиру не заходил. Но заходить будут, и
    против такого же одетого круг классов обязан держаться — украшения
    торгуют теми же процентами, что и сеты, а кольцо надевается трижды.
    Этим комплектом круг и проверяется.

    Кольцо берётся одно и то же во все три клетки: так носить их и будут —
    кольца не уникальны, и лучшее своё кольцо боец купит трижды, а не
    станет собирать набор послабее.
    """
    kit = best_kit(fclass, level)
    mine = [
        item
        for item in JEWEL_ITEMS
        if item.level_required <= level and fclass.code in item.for_classes
    ]
    def best(shelf: list[Item]) -> Item:
        return max(shelf, key=lambda item: (item.level_required, item.price))

    necklaces = [item for item in mine if item.slot is Slot.NECKLACE]
    rings = [item for item in mine if item.is_ring]
    if necklaces:
        kit[Slot.NECKLACE] = best(necklaces)
    if rings:
        for slot in RING_SLOTS:
            kit[slot] = best(rings)
    return kit


def equipment_of(kit: dict[Slot, Item]) -> Equipment:
    """Комплект, надетый по слотам."""
    return Equipment(
        items={slot: OwnedItem(item=item, slot=slot) for slot, item in kit.items()}
    )


def reference_equipment(fclass: FighterClass, level: int) -> Equipment:
    """Полный комплект своего уровня, надетый по слотам."""
    return equipment_of(best_kit(fclass, level))


def kit_price(fclass: FighterClass, level: int) -> int:
    return sum(item.price for item in best_kit(fclass, level).values())
