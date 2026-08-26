"""Экипировка: восемь слотов и их влияние на бойца.

Магазина и выдачи предметов пока нет — здесь описана только механика:
слоты, каталог и суммирование бонусов. Карточка персонажа рисует слоты
(пустые или занятые), а расчёт здоровья и характеристик уже умеет
учитывать надетое.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

from bot.game.classes import Stats


class Slot(str, Enum):
    """Слоты в том порядке, в каком они идут на карточке."""

    HEAD = "head"
    WEAPON = "weapon"
    JACKET = "jacket"
    BELT = "belt"
    GLOVES = "gloves"
    SHIELD = "shield"
    PANTS = "pants"
    BOOTS = "boots"

    @property
    def title(self) -> str:
        return SLOT_TITLES[self]

    @property
    def emoji(self) -> str:
        return SLOT_EMOJI[self]


SLOT_TITLES: dict[Slot, str] = {
    Slot.HEAD: "головной убор",
    Slot.WEAPON: "оружие",
    Slot.JACKET: "куртка",
    Slot.BELT: "пояс",
    Slot.GLOVES: "перчатки",
    Slot.SHIELD: "щит",
    Slot.PANTS: "штаны",
    Slot.BOOTS: "обувь",
}

SLOT_EMOJI: dict[Slot, str] = {
    Slot.HEAD: "🎩",
    Slot.WEAPON: "🔪",
    Slot.JACKET: "🧥",
    Slot.BELT: "🥋",
    Slot.GLOVES: "🥊",
    Slot.SHIELD: "🛡",
    Slot.PANTS: "👖",
    Slot.BOOTS: "👟",
}

# Чем бьёт боец без оружия
BARE_HANDS = "кулаком"
BARE_HANDS_ICON = "👊"

# Слева направо на карточке: две колонки по четыре слота
LEFT_SLOTS: tuple[Slot, ...] = (Slot.HEAD, Slot.WEAPON, Slot.JACKET, Slot.BELT)
RIGHT_SLOTS: tuple[Slot, ...] = (Slot.GLOVES, Slot.SHIELD, Slot.PANTS, Slot.BOOTS)
ALL_SLOTS: tuple[Slot, ...] = LEFT_SLOTS + RIGHT_SLOTS


class ItemKind(str, Enum):
    """Чем предмет является в бою.

    В слот щита можно поставить и второе оружие — тогда боец бьёт дважды
    за раунд, но лишается прибавки к блоку.
    """

    GEAR = "gear"  # просто вещь с бонусами
    WEAPON = "weapon"
    SHIELD = "shield"


@dataclass(frozen=True)
class Item:
    """Предмет экипировки."""

    code: str
    title: str
    slot: Slot
    icon: str = ""
    kind: ItemKind = ItemKind.GEAR
    # Как предмет называется в тексте боя: «кастетом», «мечом»
    instrumental: str = ""
    strength: int = 0
    agility: int = 0
    intuition: int = 0
    endurance: int = 0
    hp: int = 0  # прибавка к запасу здоровья сверх выносливости
    level_required: int = 1
    price: int = 0  # под будущий магазин

    @property
    def weapon(self) -> Item | None:
        """Оружие в основной руке."""
        item = self.items.get(Slot.WEAPON)
        return item if item and item.is_weapon else None

    @property
    def offhand(self) -> Item | None:
        """Что во второй руке: щит или второе оружие."""
        return self.items.get(Slot.SHIELD)

    @property
    def has_shield(self) -> bool:
        offhand = self.offhand
        return bool(offhand and offhand.is_shield)

    @property
    def second_weapon(self) -> Item | None:
        offhand = self.offhand
        return offhand if offhand and offhand.is_weapon else None

    @property
    def weapon_names(self) -> tuple[str, ...]:
        """Чем боец бьёт в этом раунде. Без оружия — кулаком."""
        names = [self.weapon.instrumental if self.weapon else BARE_HANDS]
        second = self.second_weapon
        if second:
            names.append(second.instrumental or BARE_HANDS)
        return tuple(names)

    @property
    def weapon_icons(self) -> tuple[str, ...]:
        """Чем подписывать колонки ударов: кулак или значок оружия."""
        icons = [self.weapon.emoji if self.weapon else BARE_HANDS_ICON]
        second = self.second_weapon
        if second:
            icons.append(second.emoji)
        return tuple(icons)

    @property
    def bonus(self) -> Stats:
        return Stats(
            strength=self.strength,
            agility=self.agility,
            intuition=self.intuition,
            endurance=self.endurance,
        )

    @property
    def emoji(self) -> str:
        return self.icon or self.slot.emoji

    @property
    def is_weapon(self) -> bool:
        return self.kind is ItemKind.WEAPON

    @property
    def is_shield(self) -> bool:
        return self.kind is ItemKind.SHIELD

    @property
    def in_hand(self) -> str:
        """Чем бьют этим предметом. Пустая строка — предмет не оружие."""
        return self.instrumental if self.is_weapon else ""

    def describe_bonus(self) -> str:
        parts = []
        for label, value in (
            ("💪", self.strength),
            ("🤸", self.agility),
            ("🔮", self.intuition),
            ("🫀", self.endurance),
            ("❤️", self.hp),
        ):
            if value:
                parts.append(f"{label}+{value}")
        return " ".join(parts)


# Стартовый каталог. Предметы пока никому не выдаются — это заготовка
# под магазин, но карточка и расчёты уже умеют с ними работать.
CATALOGUE: dict[str, Item] = {
    item.code: item
    for item in (
        Item("bandana", "Бандана", Slot.HEAD, "🧢", intuition=1, price=40),
        Item(
            "knuckles",
            "Кастет",
            Slot.WEAPON,
            "🔩",
            kind=ItemKind.WEAPON,
            instrumental="кастетом",
            strength=2,
            price=80,
        ),
        Item("leather_jacket", "Косуха", Slot.JACKET, "🧥", endurance=1, hp=5, price=90),
        Item("wide_belt", "Широкий пояс", Slot.BELT, "🥋", endurance=1, price=50),
        Item("wraps", "Бинты", Slot.GLOVES, "🩹", strength=1, price=35),
        Item(
            "bar_lid",
            "Крышка от бочки",
            Slot.SHIELD,
            "🛢",
            kind=ItemKind.SHIELD,
            endurance=2,
            price=70,
        ),
        Item("canvas_pants", "Брезентовые штаны", Slot.PANTS, "👖", endurance=1, price=45),
        Item("sneakers", "Кеды", Slot.BOOTS, "👟", agility=2, price=60),
    )
}


def get_item(code: str) -> Item | None:
    return CATALOGUE.get(code)


@dataclass
class Equipment:
    """Что на бойце надето: слот → предмет."""

    items: dict[Slot, Item] = field(default_factory=dict)

    @classmethod
    def from_codes(cls, codes: dict[str, str] | None) -> "Equipment":
        items: dict[Slot, Item] = {}
        for slot_value, code in (codes or {}).items():
            try:
                slot = Slot(slot_value)
            except ValueError:  # слот из будущей версии — пропускаем
                continue
            item = get_item(code)
            if item is not None and item.slot is slot:
                items[slot] = item
        return cls(items=items)

    @classmethod
    def from_json(cls, raw: str | None) -> "Equipment":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):  # pragma: no cover - битые данные в БД
            return cls()
        return cls.from_codes(data if isinstance(data, dict) else None)

    def to_codes(self) -> dict[str, str]:
        return {slot.value: item.code for slot, item in self.items.items()}

    def to_json(self) -> str | None:
        codes = self.to_codes()
        return json.dumps(codes, ensure_ascii=False) if codes else None

    def get(self, slot: Slot) -> Item | None:
        return self.items.get(slot)

    def equip(self, item: Item) -> Item | None:
        """Надеть предмет, вернуть то, что было в слоте."""
        previous = self.items.get(item.slot)
        self.items[item.slot] = item
        return previous

    def take_off(self, slot: Slot) -> Item | None:
        return self.items.pop(slot, None)

    @property
    def weapon(self) -> Item | None:
        """Оружие в основной руке."""
        item = self.items.get(Slot.WEAPON)
        return item if item and item.is_weapon else None

    @property
    def offhand(self) -> Item | None:
        """Что во второй руке: щит или второе оружие."""
        return self.items.get(Slot.SHIELD)

    @property
    def has_shield(self) -> bool:
        offhand = self.offhand
        return bool(offhand and offhand.is_shield)

    @property
    def second_weapon(self) -> Item | None:
        offhand = self.offhand
        return offhand if offhand and offhand.is_weapon else None

    @property
    def weapon_names(self) -> tuple[str, ...]:
        """Чем боец бьёт в этом раунде. Без оружия — кулаком."""
        names = [self.weapon.instrumental if self.weapon else BARE_HANDS]
        second = self.second_weapon
        if second:
            names.append(second.instrumental or BARE_HANDS)
        return tuple(names)

    @property
    def weapon_icons(self) -> tuple[str, ...]:
        """Чем подписывать колонки ударов: кулак или значок оружия."""
        icons = [self.weapon.emoji if self.weapon else BARE_HANDS_ICON]
        second = self.second_weapon
        if second:
            icons.append(second.emoji)
        return tuple(icons)

    @property
    def bonus(self) -> Stats:
        total = Stats()
        for item in self.items.values():
            total = total.merge(item.bonus)
        return total

    @property
    def hp_bonus(self) -> int:
        return sum(item.hp for item in self.items.values())

    def __bool__(self) -> bool:
        return bool(self.items)
