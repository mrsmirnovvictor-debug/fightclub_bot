"""Экипировка: восемь слотов, износ вещей и починка.

Предмет живёт в двух состояниях: лежит в инвентаре или надет в свой слот.
У каждого экземпляра свой износ — вещи снашиваются в боях, чинятся за
кредиты и в конце концов рассыпаются.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from bot.game.classes import ALL_STATS, Stats


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

# ---------- износ ----------

# Запас прочности новой вещи: 20 пунктов износа до трухи
MAX_WEAR = 20
# Шанс схватить пункт износа за бой — проигравший снашивает вещи вчетверо чаще
WEAR_CHANCE_LOSS = 0.75
WEAR_CHANCE_WIN = 0.10
# Починка: один пункт износа — один кредит
REPAIR_PRICE_PER_POINT = 1
# Каждая починка с этим шансом отнимает у вещи один пункт запаса прочности,
# поэтому чинить понемногу невыгодно: платишь столько же, а вещь ветшает.
REPAIR_DEGRADE_CHANCE = 0.5


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
    """Предмет экипировки: то, что одинаково у всех его экземпляров."""

    code: str
    title: str
    slot: Slot
    icon: str = ""
    image: str = ""  # картинка предмета для мини-аппа
    kind: ItemKind = ItemKind.GEAR
    # Как предмет называется в тексте боя: «кастетом», «мечом»
    instrumental: str = ""
    strength: int = 0
    agility: int = 0
    intuition: int = 0
    endurance: int = 0
    hp: int = 0  # прибавка к запасу здоровья сверх выносливости
    level_required: int = 1
    requires: Stats = field(default_factory=Stats)  # характеристики под надевание
    price: int = 0

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
    def slots(self) -> tuple[Slot, ...]:
        """Куда вещь можно надеть. Оружие берут и во вторую руку."""
        if self.is_weapon:
            return (Slot.WEAPON, Slot.SHIELD)
        return (self.slot,)

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


@dataclass
class OwnedItem:
    """Экземпляр предмета у бойца: сам предмет плюс его износ.

    `slot` — куда вещь надета; None значит, что она лежит в инвентаре.
    """

    item: Item
    id: int = 0  # номер строки в инвентаре; 0 — вещь ещё не сохранена
    wear: int = 0
    max_wear: int = MAX_WEAR
    slot: Slot | None = None

    # ---------- то, что берут у самого предмета ----------

    @property
    def code(self) -> str:
        return self.item.code

    @property
    def title(self) -> str:
        return self.item.title

    @property
    def emoji(self) -> str:
        return self.item.emoji

    @property
    def image(self) -> str:
        return self.item.image

    @property
    def instrumental(self) -> str:
        return self.item.instrumental

    @property
    def is_weapon(self) -> bool:
        return self.item.is_weapon

    @property
    def is_shield(self) -> bool:
        return self.item.is_shield

    @property
    def bonus(self) -> Stats:
        return self.item.bonus

    @property
    def hp(self) -> int:
        return self.item.hp

    def describe_bonus(self) -> str:
        return self.item.describe_bonus()

    # ---------- износ ----------

    @property
    def is_worn_out(self) -> bool:
        """Износ добрался до запаса прочности — вещи больше нет."""
        return self.wear >= self.max_wear or self.max_wear <= 0

    @property
    def repair_price(self) -> int:
        return self.wear * REPAIR_PRICE_PER_POINT

    @property
    def is_equipped(self) -> bool:
        return self.slot is not None

    def describe_wear(self) -> str:
        return f"{self.wear}/{self.max_wear}"


def missing_requirements(item: Item, level: int, stats: Stats) -> list[str]:
    """Чего не хватает, чтобы надеть вещь. Пустой список — можно надевать."""
    gaps = []
    if level < item.level_required:
        gaps.append("level")
    for stat in ALL_STATS:
        if stats.get(stat) < item.requires.get(stat):
            gaps.append(stat.value)
    return gaps


def can_equip(item: Item, level: int, stats: Stats) -> bool:
    """Требования считаем по своим характеристикам, без учёта надетого."""
    return not missing_requirements(item, level, stats)


def describe_requirements(item: Item) -> str:
    parts = [f"уровень {item.level_required}"]
    parts += [
        f"{stat.title} {item.requires.get(stat)}"
        for stat in ALL_STATS
        if item.requires.get(stat)
    ]
    return ", ".join(parts)


def roll_fight_wear(won: bool, rng: random.Random | None = None) -> bool:
    """Схватила ли надетая вещь пункт износа за этот бой.

    Ничья идёт по строке поражения — как и в рейтинге.
    """
    rng = rng or random
    return rng.random() < (WEAR_CHANCE_WIN if won else WEAR_CHANCE_LOSS)


def apply_fight_wear(
    items: Iterable[OwnedItem], won: bool, rng: random.Random | None = None
) -> tuple[list[OwnedItem], list[OwnedItem]]:
    """Пройтись износом по надетому. Вернуть (потрёпанные, рассыпавшиеся)."""
    damaged: list[OwnedItem] = []
    broken: list[OwnedItem] = []
    for owned in items:
        if not roll_fight_wear(won, rng):
            continue
        owned.wear += 1
        damaged.append(owned)
        if owned.is_worn_out:
            broken.append(owned)
    return damaged, broken


@dataclass
class RepairResult:
    """Итог починки: сколько заплатили и что стало с вещью."""

    points: int = 0
    price: int = 0
    degraded: bool = False  # запас прочности просел на пункт
    destroyed: bool = False  # чинить было уже нечего, вещь рассыпалась


def repair_points(owned: OwnedItem, credits: int) -> int:
    """Сколько пунктов износа получится снять на эти кредиты."""
    if REPAIR_PRICE_PER_POINT <= 0:  # pragma: no cover - цена всегда положительная
        return owned.wear
    return max(0, min(owned.wear, credits // REPAIR_PRICE_PER_POINT))


def repair(
    owned: OwnedItem, points: int, rng: random.Random | None = None
) -> RepairResult:
    """Снять с вещи пункты износа. Одна починка — один риск потерять прочность."""
    points = max(0, min(points, owned.wear))
    result = RepairResult(points=points, price=points * REPAIR_PRICE_PER_POINT)
    if points == 0:
        return result

    owned.wear -= points
    rng = rng or random
    if rng.random() < REPAIR_DEGRADE_CHANCE:
        owned.max_wear -= 1
        result.degraded = True
        owned.wear = min(owned.wear, max(0, owned.max_wear))
    result.destroyed = owned.is_worn_out
    return result


# Стартовый каталог: чем торгует лавка клуба.
IMAGES = "https://pub-ea6a4494c019470aa38328eec255511d.r2.dev/VEGAS%20Fight%20Club"

CATALOGUE: dict[str, Item] = {
    item.code: item
    for item in (
        Item(
            "bandana",
            "Бандана",
            Slot.HEAD,
            "🧢",
            intuition=1,
            requires=Stats(intuition=4),
            price=40,
        ),
        Item(
            "knuckles",
            "Кастет",
            Slot.WEAPON,
            "🔩",
            kind=ItemKind.WEAPON,
            instrumental="кастетом",
            strength=2,
            level_required=3,
            requires=Stats(strength=6),
            price=80,
        ),
        Item(
            "leather_jacket",
            "Косуха",
            Slot.JACKET,
            "🧥",
            endurance=1,
            hp=5,
            level_required=3,
            requires=Stats(endurance=6),
            price=90,
        ),
        Item(
            "wide_belt",
            "Широкий пояс",
            Slot.BELT,
            "🥋",
            endurance=1,
            level_required=2,
            requires=Stats(endurance=5),
            price=50,
        ),
        Item(
            "wraps",
            "Бинты",
            Slot.GLOVES,
            "🩹",
            strength=1,
            requires=Stats(strength=4),
            price=35,
        ),
        Item(
            "bar_lid",
            "Крышка от бочки",
            Slot.SHIELD,
            "🛢",
            kind=ItemKind.SHIELD,
            endurance=2,
            level_required=3,
            requires=Stats(strength=6, endurance=5),
            price=70,
        ),
        Item(
            "canvas_pants",
            "Брезентовые штаны",
            Slot.PANTS,
            "👖",
            endurance=1,
            level_required=2,
            requires=Stats(endurance=5),
            price=45,
        ),
        Item(
            "sneakers",
            "Кеды",
            Slot.BOOTS,
            "👟",
            image=f"{IMAGES}/bots/Worn_sneakers_game_asset_202608261347.jpeg",
            agility=2,
            level_required=2,
            requires=Stats(agility=5),
            price=60,
        ),
    )
}

# Что показывать в лавке: сначала дешёвое
SHOWCASE: tuple[Item, ...] = tuple(
    sorted(CATALOGUE.values(), key=lambda item: (item.price, item.title))
)


def get_item(code: str) -> Item | None:
    return CATALOGUE.get(code)


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
        return self.items.get(Slot.SHIELD)

    @property
    def has_shield(self) -> bool:
        offhand = self.offhand
        return bool(offhand and offhand.is_shield)

    @property
    def second_weapon(self) -> OwnedItem | None:
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
