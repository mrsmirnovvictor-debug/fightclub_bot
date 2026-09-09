"""Экипировка: что на бойце надето и что он таскает в рюкзаке.

Модуль собран из двух половин и остаётся общим входом для всех остальных:
правила вещи — слоты, износ, починка — лежат в `bot.game.gear`, а сами
вещи в `bot.content.items`. Кто раньше брал каталог отсюда, берёт его
отсюда и дальше.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from bot.content.items import (
    ADDED_ART,
    ART,
    ASSASSIN,
    CATALOGUE,
    ITEM_ART,
    ITEMS,
    MAGIC_ART,
    MAGIC_ITEMS,
    ROGUE,
    SHOWCASE,
    TANK,
    WARRIOR,
    WEAPON_ART,
    get_item,
    items_unlocked_at,
    shop_sections,
)
from bot.game.classes import Stats, Zone
from bot.game.gear import (
    ALL_SLOTS,
    BARE_HANDS,
    BARE_HANDS_ICON,
    EARLY_LEVELS,
    EARLY_SHARE_CAP,
    LATE_SHARE_CAP,
    LEFT_SLOTS,
    MAX_WEAR,
    REPAIR_DEGRADE_CHANCE,
    REPAIR_PRICE_PER_POINT,
    RIGHT_SLOTS,
    SLOT_ART,
    SLOT_EMOJI,
    SLOT_SECTIONS,
    SLOT_TITLES,
    SLOT_ZONES,
    UNDER_SLOTS,
    WEAR_CHANCE_LOSS,
    WEAR_CHANCE_WIN,
    Item,
    ItemKind,
    OwnedItem,
    RepairResult,
    Slot,
    apply_fight_wear,
    can_equip,
    describe_requirements,
    missing_requirements,
    repair,
    repair_points,
    roll_fight_wear,
)

@dataclass
class Equipment:
    """Что на бойце надето: слот → надетый экземпляр."""

    items: dict[Slot, OwnedItem] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Позволяем собрать экипировку из «голых» предметов каталога:
        # так удобно в расчётах и тестах, где износ не важен.
        for slot, value in list(self.items.items()):
            if isinstance(value, Item):
                self.items[slot] = OwnedItem(item=value, slot=slot)

    @classmethod
    def from_codes(cls, codes: dict[str, str] | None) -> "Equipment":
        items: dict[Slot, OwnedItem] = {}
        for slot_value, code in (codes or {}).items():
            try:
                slot = Slot(slot_value)
            except ValueError:  # слот из будущей версии — пропускаем
                continue
            item = get_item(code)
            if item is not None and slot in item.slots:
                items[slot] = OwnedItem(item=item, slot=slot)
        return cls(items=items)

    @classmethod
    def from_owned(cls, owned: Iterable[OwnedItem]) -> "Equipment":
        """Собрать экипировку из инвентаря — берём только надетое."""
        return cls(
            items={item.slot: item for item in owned if item.slot is not None}
        )

    def get(self, slot: Slot) -> OwnedItem | None:
        return self.items.get(slot)

    @property
    def weapon(self) -> OwnedItem | None:
        """Оружие в основной руке."""
        item = self.items.get(Slot.WEAPON)
        return item if item and item.is_weapon else None

    @property
    def offhand(self) -> OwnedItem | None:
        """Что во второй руке: щит или второе оружие."""
        return self.items.get(Slot.OFFHAND)

    @property
    def has_shield(self) -> bool:
        offhand = self.offhand
        return bool(offhand and offhand.item.is_shield)

    @property
    def second_weapon(self) -> OwnedItem | None:
        offhand = self.offhand
        return offhand if offhand and offhand.item.is_weapon else None

    @property
    def weapons(self) -> tuple[OwnedItem | None, ...]:
        """Руки, которыми бьют, по порядку ударов. None — голая рука."""
        hands: list[OwnedItem | None] = [self.weapon]
        second = self.second_weapon
        if second is not None:
            hands.append(second)
        return tuple(hands)

    @property
    def weapon_names(self) -> tuple[str, ...]:
        """Чем боец бьёт каждой рукой. Без оружия — кулаком."""
        return tuple(
            hand.instrumental if hand else BARE_HANDS for hand in self.weapons
        )

    @property
    def weapon_icons(self) -> tuple[str, ...]:
        """Чем подписывать столбцы ударов: кулак или значок оружия."""
        return tuple(hand.emoji if hand else BARE_HANDS_ICON for hand in self.weapons)

    @property
    def weapon_titles(self) -> tuple[str, ...]:
        return tuple(hand.title if hand else "Кулаки" for hand in self.weapons)

    @property
    def weapon_name(self) -> str:
        """Чем боец бьёт основной рукой. Без оружия — кулаком."""
        return self.weapon.instrumental if self.weapon else BARE_HANDS

    @property
    def weapon_title(self) -> str:
        """Чем бьёт, по-человечески: для строки урона на карточке."""
        return self.weapon.title if self.weapon else "Кулаки"

    @property
    def weapon_icon(self) -> str:
        """Чем подписывать кнопки ударов: кулак или значок оружия."""
        return self.weapon.emoji if self.weapon else BARE_HANDS_ICON

    @property
    def bonus(self) -> Stats:
        total = Stats()
        for item in self.items.values():
            total = total.merge(item.bonus)
        return total

    @property
    def hp_bonus(self) -> int:
        return sum(item.hp for item in self.items.values())

    @property
    def accuracy(self) -> float:
        return sum(item.item.accuracy for item in self.items.values())

    @property
    def anticrit(self) -> float:
        return sum(item.item.anticrit for item in self.items.values())

    @property
    def dodge(self) -> float:
        return sum(item.item.dodge for item in self.items.values())

    @property
    def crit(self) -> float:
        return sum(item.item.crit for item in self.items.values())

    @property
    def counter(self) -> float:
        return sum(item.item.counter for item in self.items.values())

    def armor_range(self, zone: Zone) -> tuple[int, int]:
        """Сколько брони прикрывает эту зону: сумма по всем вещам."""
        low = high = 0
        for owned in self.items.values():
            if zone in owned.item.zones:
                low += owned.item.armor_min
                high += owned.item.armor_max
        return low, high

    def roll_armor(self, zone: Zone, rng: random.Random | None = None) -> int:
        """Бросок брони на пропущенный удар в эту зону."""
        return sum(
            owned.item.roll_armor(rng)
            for owned in self.items.values()
            if zone in owned.item.zones
        )

    @property
    def weapon_damage(self) -> tuple[int, int]:
        """Прибавка к урону от оружия основной руки. Без оружия — ничего."""
        if not self.weapon:
            return (0, 0)
        return (self.weapon.item.damage_min, self.weapon.item.damage_max)

    @property
    def weapon_damages(self) -> tuple[tuple[int, int], ...]:
        """Прибавка к урону от каждой руки — по порядку ударов."""
        return tuple(
            (hand.item.damage_min, hand.item.damage_max) if hand else (0, 0)
            for hand in self.weapons
        )

    def hand(self, index: int) -> OwnedItem | None:
        hands = self.weapons
        return hands[index] if 0 <= index < len(hands) else None

    def roll_weapon_damage(
        self, index: int = 0, rng: random.Random | None = None
    ) -> int:
        """Что добавит оружие этой руки. Кулак не добавляет ничего."""
        hand = self.hand(index)
        return hand.item.roll_damage(rng) if hand else 0

    def weapon_damage_max(self, index: int = 0) -> int:
        """Потолок прибавки этой руки — без броска."""
        hand = self.hand(index)
        return hand.item.damage_max if hand else 0

    def __bool__(self) -> bool:
        return bool(self.items)


# Модуль — общий вход: наружу видно и правила из gear, и каталог из content
__all__ = [
    "ADDED_ART",
    "ALL_SLOTS",
    "ART",
    "ASSASSIN",
    "BARE_HANDS",
    "BARE_HANDS_ICON",
    "CATALOGUE",
    "EARLY_LEVELS",
    "EARLY_SHARE_CAP",
    "Equipment",
    "ITEMS",
    "ITEM_ART",
    "Item",
    "ItemKind",
    "LATE_SHARE_CAP",
    "LEFT_SLOTS",
    "MAGIC_ART",
    "MAGIC_ITEMS",
    "MAX_WEAR",
    "OwnedItem",
    "REPAIR_DEGRADE_CHANCE",
    "REPAIR_PRICE_PER_POINT",
    "RIGHT_SLOTS",
    "ROGUE",
    "RepairResult",
    "SHOWCASE",
    "SLOT_ART",
    "SLOT_EMOJI",
    "SLOT_SECTIONS",
    "SLOT_TITLES",
    "SLOT_ZONES",
    "Slot",
    "TANK",
    "UNDER_SLOTS",
    "WARRIOR",
    "WEAPON_ART",
    "WEAR_CHANCE_LOSS",
    "WEAR_CHANCE_WIN",
    "apply_fight_wear",
    "can_equip",
    "describe_requirements",
    "get_item",
    "items_unlocked_at",
    "missing_requirements",
    "repair",
    "repair_points",
    "roll_fight_wear",
    "shop_sections",
]
