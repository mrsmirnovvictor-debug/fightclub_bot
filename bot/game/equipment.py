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

from bot.game import art
from bot.game.classes import ALL_STATS, ALL_ZONES, Stats, Zone


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
        """Как слот называется в предложении: «сюда надевается ...»."""
        return SLOT_TITLES[self]

    @property
    def section(self) -> str:
        """Как называется тип товара в лавке и в инвентаре."""
        return SLOT_SECTIONS[self]

    @property
    def placeholder(self) -> str:
        """Подложка пустого слота: тень того, что сюда надевается."""
        return art.slot(self.value)

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

# Тип товара на витрине: это ярлык раздела, а не часть предложения, поэтому
# он короче и называет часть тела, а не саму вещь.
SLOT_SECTIONS: dict[Slot, str] = {
    Slot.HEAD: "голова",
    Slot.WEAPON: "оружие",
    Slot.JACKET: "тело",
    Slot.BELT: "пояс",
    Slot.GLOVES: "перчатки",
    Slot.SHIELD: "щиты",
    Slot.PANTS: "ноги",
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

# Какие зоны прикрывает одежда из слота. Перчатки и оружие брони не дают:
# кулаки и ладони — не зона удара. Щит закрывает всё, но только если он щит.
SLOT_ZONES: dict[Slot, tuple[Zone, ...]] = {
    Slot.HEAD: (Zone.HEAD,),
    Slot.JACKET: (Zone.CHEST, Zone.BELLY),
    Slot.BELT: (Zone.BELT,),
    Slot.PANTS: (Zone.BELT, Zone.LEGS),
    Slot.BOOTS: (Zone.LEGS,),
    Slot.SHIELD: ALL_ZONES,
}

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

# ---------- проценты на вещах ----------

# Точность, уворот, крит и антикрит вещи дают долями. На первых ступенях
# доля маленькая, на последних заметная, но и там мы держимся заметно ниже
# «половины»: точность в 50% с одной вещи обнулила бы уворот трикстера,
# антикрит такого размера — крит ассасина. Пары должны спорить, а не стирать
# друг друга.
EARLY_LEVELS = 5
EARLY_SHARE_CAP = 0.05
LATE_SHARE_CAP = 0.10


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
    # Выносливости на вещах нет и не будет: её растят только руками, по очку
    # за ап и по очку автоматом за уровень. Вещь может дать лишь запас
    # здоровья — но не сопротивление и не антикрит, которые идут от стата.
    hp: int = 0
    # Оружие добавляет свой урон к тому, что боец выбивает силой
    damage_min: int = 0
    damage_max: int = 0
    # Одежда держит удар в те зоны, которые прикрывает; щит — во все сразу
    armor_min: int = 0
    armor_max: int = 0
    # Обе половины каждой пары можно носить на себе: одни вещи давят
    # чужую защиту, другие поднимают свою.
    accuracy: float = 0.0  # доля к точности: сбивает уворот соперника
    dodge: float = 0.0  # доля к увороту
    crit: float = 0.0  # доля к шансу крита
    anticrit: float = 0.0  # доля к антикриту: сбивает крит соперника
    counter: float = 0.0  # доля к шансу контрудара
    level_required: int = 1
    requires: Stats = field(default_factory=Stats)  # характеристики под надевание
    price: int = 0
    # Цена в звёздах Telegram. Больше нуля — вещь из лавки мага: за кредиты
    # её не купить, и на прилавке клуба она не лежит.
    stars: int = 0
    # Награда, а не товар: такую вещь не купишь ни за кредиты, ни за звёзды —
    # её выдают. Клинок ассасина приходит вместе с подпиской PRO.
    reward: bool = False
    # Кому вещь в первую очередь: коды классов. Пустой набор — всем поровну.
    for_classes: tuple[str, ...] = ()

    @property
    def bonus(self) -> Stats:
        return Stats(
            strength=self.strength,
            agility=self.agility,
            intuition=self.intuition,
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
    def is_magic(self) -> bool:
        """Вещь из лавки мага: продаётся только за звёзды."""
        return self.stars > 0

    @property
    def on_sale(self) -> bool:
        """Вещь вообще продаётся: награду с полки не возьмёшь."""
        return not self.reward

    @property
    def zones(self) -> tuple[Zone, ...]:
        """Куда вещь принимает удар. Щитом считается только настоящий щит."""
        if not (self.armor_min or self.armor_max):
            return ()
        if self.slot is Slot.SHIELD and not self.is_shield:
            return ()  # во второй руке оружие, а не щит
        return SLOT_ZONES.get(self.slot, ())

    def roll_armor(self, rng: random.Random | None = None) -> int:
        rng = rng or random
        if self.armor_max <= 0:
            return 0
        return rng.randint(min(self.armor_min, self.armor_max), self.armor_max)

    def roll_damage(self, rng: random.Random | None = None) -> int:
        rng = rng or random
        if self.damage_max <= 0:
            return 0
        return rng.randint(min(self.damage_min, self.damage_max), self.damage_max)

    def describe_damage(self) -> str:
        if self.damage_max <= 0:
            return ""
        return f"{self.damage_min}–{self.damage_max}"

    def describe_armor(self) -> str:
        if self.armor_max <= 0:
            return ""
        return f"{self.armor_min}–{self.armor_max}"

    @property
    def slots(self) -> tuple[Slot, ...]:
        """Куда вещь можно надеть. Оружие берут и во вторую руку."""
        if self.is_weapon:
            return (Slot.WEAPON, Slot.SHIELD)
        return (self.slot,)

    def describe_bonus(self) -> str:
        parts = []
        if self.damage_max:
            parts.append(f"👊{self.describe_damage()}")
        if self.armor_max:
            parts.append(f"🛡{self.describe_armor()}")
        for label, value in (
            ("💪", self.strength),
            ("🤸", self.agility),
            ("🔮", self.intuition),
            ("❤️", self.hp),
        ):
            if value:
                parts.append(f"{label}+{value}")
        for label, share in (
            ("🎯", self.accuracy),
            ("🌀", self.dodge),
            ("💥", self.crit),
            ("🚫", self.anticrit),
            ("🔄", self.counter),
        ):
            if share:
                parts.append(f"{label}+{share:.0%}")
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


# ---------- каталог ----------
#
# Товар открывается уровнем: с 4-го идёт оружие, с 5-го — куртки, штаны,
# наручи и пояса, с 6-го обновляется оружие и приходят головные уборы с
# обувью, с 7-го — оружие, перчатки и щиты, с 8-го — последнее оружие.
# В каждом наборе есть вещь под каждый класс, но кредитов на всё не хватит:
# на уровень капает около сотни, а набор стоит втрое дороже — в этом и развилка.

# Картинки предметов лежат в R2, адреса собраны в bot.game.art
ART = art.BUCKET
WEAPON_ART = art.WEAPONS
ADDED_ART = art.ADDED
ITEM_ART = art.ITEMS
MAGIC_ART = art.MAGIC

WARRIOR, ROGUE, ASSASSIN, TANK = "warrior", "rogue", "assassin", "tank"

ITEMS: tuple[Item, ...] = (
    # ---------- с чего начинают: уровни 1–3 ----------
    Item(
        "wraps",
        "Бинты",
        Slot.GLOVES,
        "🩹",
        strength=1,
        image=f"{ITEM_ART}/Hand_wraps_game_inventory_icon_202608281513.jpeg",
        accuracy=0.02,
        requires=Stats(strength=4),
        price=35,
        for_classes=(WARRIOR, TANK),
    ),
    Item(
        "bandana",
        "Бандана",
        Slot.HEAD,
        "🧢",
        intuition=1,
        image=f"{ITEM_ART}/bandana.jpeg_202608281514.jpeg",
        armor_min=0,
        armor_max=1,
        anticrit=0.02,
        requires=Stats(intuition=4),
        price=40,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "canvas_pants",
        "Брезентовые штаны",
        Slot.PANTS,
        "👖",
        hp=6,
        image=f"{ITEM_ART}/Folded_work_trousers_game_icon_202608281512.jpeg",
        armor_min=0,
        armor_max=1,
        level_required=2,
        requires=Stats(endurance=5),
        price=45,
        for_classes=(TANK,),
    ),
    Item(
        "wide_belt",
        "Широкий пояс",
        Slot.BELT,
        "🥋",
        hp=6,
        image=f"{ITEM_ART}/Leather_belt_game_icon_202608281513.jpeg",
        armor_min=0,
        armor_max=1,
        level_required=2,
        requires=Stats(endurance=5),
        price=50,
        for_classes=(TANK, WARRIOR),
    ),
    Item(
        "sneakers",
        "Кеды",
        Slot.BOOTS,
        "👟",
        image=f"{ITEM_ART}/sneakers.jpeg",
        agility=1,
        armor_min=0,
        armor_max=1,
        level_required=2,
        requires=Stats(agility=5),
        price=60,
        for_classes=(ROGUE,),
    ),
    Item(
        "bar_lid",
        "Крышка от бочки",
        Slot.SHIELD,
        "🛢",
        kind=ItemKind.SHIELD,
        image=f"{ADDED_ART}/Game_inventory_shield_icon_202608280250.jpeg",
        hp=6,
        armor_min=1,
        armor_max=2,
        anticrit=0.03,
        level_required=3,
        requires=Stats(strength=6, endurance=5),
        price=70,
        for_classes=(TANK,),
    ),
    Item(
        "knuckles",
        "Кастет",
        Slot.WEAPON,
        "🔩",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Brass_knuckle_game_inventory_icon_202608280127.jpeg",
        instrumental="кастетом",
        strength=1,
        damage_min=2,
        damage_max=4,
        accuracy=0.02,
        level_required=3,
        requires=Stats(strength=6),
        price=80,
        for_classes=(WARRIOR,),
    ),
    Item(
        "leather_jacket",
        "Косуха",
        Slot.JACKET,
        "🧥",
        hp=11,
        image=f"{ITEM_ART}/Battered_leather_biker_jacket_icon_202608281513.jpeg",
        armor_min=1,
        armor_max=3,
        level_required=3,
        requires=Stats(endurance=6),
        price=90,
        for_classes=(TANK, WARRIOR),
    ),
    # ---------- 4 уровень: первое настоящее оружие ----------
    Item(
        "pipe",
        "Деревянная бита",
        Slot.WEAPON,
        "🪵",
        image=f"{WEAPON_ART}/Customized_wooden_baseball_bat_icon_202608280128.jpeg",
        kind=ItemKind.WEAPON,
        instrumental="деревянной битой",
        strength=1,
        damage_min=4,
        damage_max=7,
        accuracy=0.03,
        level_required=4,
        requires=Stats(strength=12),
        price=150,
        for_classes=(WARRIOR,),
    ),
    Item(
        "switchblade",
        "Выкидуха",
        Slot.WEAPON,
        "🔪",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Folding_knife_game_inventory_icon_202608280132.jpeg",
        instrumental="выкидухой",
        agility=1,
        damage_min=4,
        damage_max=7,
        dodge=0.03,
        level_required=4,
        requires=Stats(agility=12),
        price=150,
        for_classes=(ROGUE,),
    ),
    Item(
        "awl",
        "Строительный нож",
        Slot.WEAPON,
        "🔪",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Utility_knife_game_inventory_icon_202608280152.jpeg",
        instrumental="строительным ножом",
        intuition=1,
        damage_min=2,
        damage_max=9,
        crit=0.03,
        level_required=4,
        requires=Stats(intuition=12),
        price=150,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "crowbar",
        "Монтировка",
        Slot.WEAPON,
        "🔧",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Game_inventory_crowbar_icon_202608280137.jpeg",
        instrumental="монтировкой",
        damage_min=5,
        damage_max=6,
        accuracy=0.03,
        anticrit=0.03,
        level_required=4,
        requires=Stats(endurance=12),
        price=150,
        for_classes=(TANK,),
    ),
    Item(
        "knife",
        "Нож",
        Slot.WEAPON,
        "🔪",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Open_folding_knife_game_icon_202608280130.jpeg",
        instrumental="ножом",
        damage_min=4,
        damage_max=7,
        level_required=4,
        price=110,
        # Ни процентов, ни требований: первое, что берут, когда не накопил
        # на своё. Дешевле классового оружия ровно потому, что голое.
    ),
    # ---------- 5 уровень: куртки, штаны, наручи, пояса ----------
    Item(
        "biker_jacket",
        "Клёпаная косуха",
        Slot.JACKET,
        "🧥",
        hp=16,
        image=f"{ITEM_ART}/Leather_biker_jacket_game_icon_202608281513.jpeg",
        armor_min=3,
        armor_max=5,
        anticrit=0.03,
        level_required=5,
        requires=Stats(endurance=13),
        price=80,
        for_classes=(TANK, WARRIOR),
    ),
    Item(
        "denim_vest",
        "Джинсовка без рукавов",
        Slot.JACKET,
        "🦺",
        agility=1,
        image=f"{ITEM_ART}/Denim_vest_game_inventory_icon_202608281513.jpeg",
        hp=8,
        armor_min=2,
        armor_max=3,
        dodge=0.03,
        level_required=5,
        requires=Stats(agility=13),
        price=80,
        for_classes=(ROGUE, ASSASSIN),
    ),
    Item(
        "padded_pants",
        "Брезент с накладками",
        Slot.PANTS,
        "👖",
        hp=6,
        image=f"{ITEM_ART}/Canvas_trousers_game_inventory_icon_202608281512.jpeg",
        armor_min=2,
        armor_max=4,
        level_required=5,
        requires=Stats(endurance=13),
        price=80,
        for_classes=(TANK, WARRIOR),
    ),
    Item(
        "track_pants",
        "Спортивки",
        Slot.PANTS,
        "🩳",
        agility=1,
        image=f"{ITEM_ART}/Tracksuit_bottoms_game_icon_202608281512.jpeg",
        hp=4,
        armor_min=1,
        armor_max=2,
        dodge=0.03,
        level_required=5,
        requires=Stats(agility=13),
        price=80,
        for_classes=(ROGUE,),
    ),
    Item(
        "leather_bracers",
        "Кожаные наручи",
        Slot.GLOVES,
        "🦾",
        strength=1,
        image=f"{ITEM_ART}/Leather_forearm_bracers_icon_202608281513.jpeg",
        accuracy=0.04,
        level_required=5,
        requires=Stats(strength=13),
        price=80,
        for_classes=(WARRIOR, TANK),
    ),
    Item(
        "dealer_bracers",
        "Наручи шулера",
        Slot.GLOVES,
        "🃏",
        intuition=1,
        image=f"{ITEM_ART}/Leather_dealer_cuffs_icon_202608281515.jpeg",
        hp=4,
        anticrit=0.04,
        level_required=5,
        requires=Stats(intuition=13),
        price=80,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "buckle_belt",
        "Ремень с бляхой",
        Slot.BELT,
        "🥋",
        strength=1,
        image=f"{ITEM_ART}/Leather_belt_game_inventory_icon_202608281513.jpeg",
        armor_min=2,
        armor_max=3,
        level_required=5,
        requires=Stats(strength=13),
        price=80,
        for_classes=(WARRIOR,),
    ),
    Item(
        "sheath_belt",
        "Пояс с ножнами",
        Slot.BELT,
        "🗡",
        intuition=1,
        image=f"{ITEM_ART}/Leather_belt_game_inventory_icon_202608281513%20(1).jpeg",
        armor_min=1,
        armor_max=2,
        crit=0.03,
        level_required=5,
        requires=Stats(intuition=13),
        price=80,
        for_classes=(ASSASSIN, ROGUE),
    ),
    # ---------- 6 уровень: оружие, головные уборы, обувь ----------
    Item(
        "bat",
        "Бита",
        Slot.WEAPON,
        "🏏",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Bat_game_inventory_icon_202608280129.jpeg",
        instrumental="битой",
        strength=2,
        damage_min=7,
        damage_max=11,
        accuracy=0.04,
        level_required=6,
        requires=Stats(strength=16),
        price=180,
        for_classes=(WARRIOR,),
    ),
    Item(
        "machete",
        "Гладиус",
        Slot.WEAPON,
        "🗡",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Roman_gladius_game_icon_202608280153.jpeg",
        instrumental="гладиусом",
        agility=2,
        damage_min=7,
        damage_max=11,
        dodge=0.04,
        level_required=6,
        requires=Stats(agility=16),
        price=180,
        for_classes=(ROGUE,),
    ),
    Item(
        "stiletto",
        "Стилет",
        Slot.WEAPON,
        "🪡",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Stiletto_game_inventory_icon_202608280134.jpeg",
        instrumental="стилетом",
        intuition=2,
        damage_min=4,
        damage_max=14,
        crit=0.04,
        level_required=6,
        requires=Stats(intuition=16),
        price=180,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "sledge",
        "Кувалда",
        Slot.WEAPON,
        "🔨",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Sledgehammer_game_inventory_icon_202608280138.jpeg",
        instrumental="кувалдой",
        damage_min=8,
        damage_max=10,
        accuracy=0.04,
        anticrit=0.04,
        level_required=6,
        requires=Stats(endurance=16),
        price=180,
        for_classes=(TANK,),
    ),
    Item(
        "moto_helmet",
        "Мотошлем",
        Slot.HEAD,
        "🪖",
        hp=14,
        image=f"{ITEM_ART}/Scuffed_motorcycle_helmet_game_icon_202608281513.jpeg",
        armor_min=3,
        armor_max=5,
        anticrit=0.05,
        level_required=6,
        requires=Stats(endurance=15),
        price=100,
        for_classes=(TANK, WARRIOR),
    ),
    Item(
        "visor_cap",
        "Кепка с козырьком",
        Slot.HEAD,
        "🧢",
        intuition=2,
        image=f"{ITEM_ART}/Cap_game_inventory_icon_202608281513.jpeg",
        hp=4,
        armor_min=1,
        armor_max=2,
        crit=0.04,
        level_required=6,
        requires=Stats(intuition=15),
        price=100,
        for_classes=(ASSASSIN, ROGUE),
    ),
    Item(
        "army_boots",
        "Берцы",
        Slot.BOOTS,
        "🥾",
        hp=6,
        image=f"{ITEM_ART}/Combat_boots_game_inventory_icon_202608281512.jpeg",
        armor_min=2,
        armor_max=4,
        level_required=6,
        requires=Stats(endurance=15),
        price=100,
        for_classes=(WARRIOR, TANK),
    ),
    Item(
        "runners",
        "Беговые кроссовки",
        Slot.BOOTS,
        "👟",
        agility=2,
        image=f"{ITEM_ART}/Worn_trainers_game_inventory_icon_202608281511.jpeg",
        hp=4,
        armor_min=1,
        armor_max=2,
        dodge=0.04,
        level_required=6,
        requires=Stats(agility=15),
        price=100,
        for_classes=(ROGUE, ASSASSIN),
    ),
    # ---------- 7 уровень: оружие, перчатки, щиты ----------
    Item(
        "fire_axe",
        "Уличный топор",
        Slot.WEAPON,
        "🪓",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Customized_street_axe_game_icon_202608280139.jpeg",
        instrumental="топором",
        strength=2,
        damage_min=9,
        damage_max=16,
        accuracy=0.05,
        level_required=7,
        requires=Stats(strength=18),
        price=240,
        for_classes=(WARRIOR,),
    ),
    Item(
        "balisong",
        "Балисонг",
        Slot.WEAPON,
        "🦋",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Butterfly_knife_game_inventory_icon_202608280133.jpeg",
        instrumental="балисонгом",
        agility=2,
        damage_min=9,
        damage_max=16,
        dodge=0.05,
        level_required=7,
        requires=Stats(agility=18),
        price=240,
        for_classes=(ROGUE,),
    ),
    Item(
        "ice_pick",
        "Керамбит",
        Slot.WEAPON,
        "🪝",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Karambit_knife_game_icon_202608280141.jpeg",
        instrumental="керамбитом",
        intuition=2,
        damage_min=5,
        damage_max=20,
        crit=0.05,
        level_required=7,
        requires=Stats(intuition=18),
        price=240,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "chain",
        "Цепь с замком",
        Slot.WEAPON,
        "⛓",
        kind=ItemKind.WEAPON,
        image=f"{ADDED_ART}/Bicycle_chain_and_padlock_icon_202608280249.jpeg",
        instrumental="цепью",
        damage_min=11,
        damage_max=14,
        accuracy=0.05,
        anticrit=0.05,
        level_required=7,
        requires=Stats(endurance=18),
        price=240,
        for_classes=(TANK,),
    ),
    Item(
        "battered_gloves",
        "Битые перчатки",
        Slot.GLOVES,
        "🥊",
        strength=2,
        image=f"{ITEM_ART}/Leather_fighting_gloves_game_icon_202608281512.jpeg",
        accuracy=0.06,
        level_required=7,
        requires=Stats(strength=17),
        price=130,
        for_classes=(WARRIOR, TANK),
    ),
    Item(
        "fingerless_gloves",
        "Перчатки без пальцев",
        Slot.GLOVES,
        "🧤",
        agility=2,
        image=f"{ITEM_ART}/Fingerless_gloves_game_icon_202608281512.jpeg",
        hp=4,
        dodge=0.05,
        level_required=7,
        requires=Stats(agility=17),
        price=130,
        for_classes=(ROGUE,),
    ),
    Item(
        "card_gloves",
        "Перчатки крупье",
        Slot.GLOVES,
        "🎴",
        intuition=2,
        image=f"{ITEM_ART}/Croupier_gloves_game_inventory_icon_202608281512.jpeg",
        hp=4,
        crit=0.05,
        level_required=7,
        requires=Stats(intuition=17),
        price=130,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "road_sign",
        "Дорожный знак",
        Slot.SHIELD,
        "🚧",
        kind=ItemKind.SHIELD,
        image=f"{ADDED_ART}/Shield_made_from_road_sign_202608280250.jpeg",
        hp=22,
        armor_min=3,
        armor_max=4,
        anticrit=0.06,
        level_required=7,
        requires=Stats(strength=15, endurance=17),
        price=130,
        for_classes=(TANK,),
    ),
    Item(
        "buckler",
        "Щиток",
        Slot.SHIELD,
        "🛡",
        kind=ItemKind.SHIELD,
        image=f"{ADDED_ART}/Game_inventory_steel_buckler_icon_202608280250.jpeg",
        agility=1,
        hp=6,
        armor_min=2,
        armor_max=3,
        dodge=0.03,
        level_required=7,
        requires=Stats(agility=17),
        price=130,
        for_classes=(ROGUE, WARRIOR),
    ),
    # ---------- 8 уровень: последнее оружие ----------
    Item(
        "cleaver",
        "Тесак",
        Slot.WEAPON,
        "⚔",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Butcher_cleaver_game_icon_202608280140.jpeg",
        instrumental="тесаком",
        strength=3,
        damage_min=13,
        damage_max=21,
        accuracy=0.07,
        level_required=8,
        requires=Stats(strength=20),
        price=300,
        for_classes=(WARRIOR,),
    ),
    Item(
        "razor",
        "Парные ножи",
        Slot.WEAPON,
        "✂️",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Crossed_street_knives_game_icon_202608280148.jpeg",
        instrumental="парными ножами",
        agility=3,
        damage_min=13,
        damage_max=21,
        dodge=0.06,
        level_required=8,
        requires=Stats(agility=20),
        price=300,
        for_classes=(ROGUE,),
    ),
    Item(
        "needle",
        "Тактический нож",
        Slot.WEAPON,
        "🔪",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Tactical_knife_game_inventory_icon_202608280151.jpeg",
        instrumental="тактическим ножом",
        intuition=3,
        damage_min=7,
        damage_max=27,
        crit=0.06,
        level_required=8,
        requires=Stats(intuition=20),
        price=300,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "pry_bar",
        "Лом",
        Slot.WEAPON,
        "🦯",
        kind=ItemKind.WEAPON,
        image=f"{ADDED_ART}/Forged_steel_pry_bar_icon_202608280249.jpeg",
        instrumental="ломом",
        damage_min=15,
        damage_max=19,
        accuracy=0.07,
        anticrit=0.06,
        level_required=8,
        requires=Stats(endurance=20),
        price=300,
        for_classes=(TANK,),
    ),
    Item(
        "katana",
        "Катана",
        Slot.WEAPON,
        "🗡",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Katana_game_inventory_icon_202608280154.jpeg",
        instrumental="катаной",
        strength=2,
        damage_min=12,
        damage_max=22,
        level_required=8,
        requires=Stats(strength=18),
        price=260,
        for_classes=(WARRIOR, ROGUE),
    ),
    Item(
        "greatsword",
        "Двуручный меч",
        Slot.WEAPON,
        "⚔️",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Two-handed_sword_game_icon_202608280159.jpeg",
        instrumental="двуручным мечом",
        strength=3,
        damage_min=15,
        damage_max=19,
        level_required=8,
        requires=Stats(strength=20),
        price=270,
        for_classes=(WARRIOR, TANK),
    ),
    # ---------- 9 уровень: чем добивают на потолке ----------
    # До этой ступени доходят единицы, поэтому цена кусается, а требования
    # рассчитаны на развитый билд своего класса, а не на универсала.
    Item(
        "viking_axe",
        "Секира викинга",
        Slot.WEAPON,
        "🪓",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Viking_battle_axe_game_icon_202608280222.jpeg",
        instrumental="секирой",
        strength=4,
        damage_min=17,
        damage_max=25,
        accuracy=0.08,
        level_required=9,
        requires=Stats(strength=22),
        price=400,
        for_classes=(WARRIOR,),
    ),
    Item(
        "nail_bat",
        "Бита с гвоздями",
        Slot.WEAPON,
        "🏏",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Game_inventory_icon_of_bat_202608280221.jpeg",
        instrumental="битой",
        strength=2,
        agility=2,
        damage_min=16,
        damage_max=26,
        dodge=0.07,
        level_required=9,
        requires=Stats(agility=22),
        price=400,
        for_classes=(ROGUE,),
    ),
    Item(
        "shiv",
        "Заточка",
        Slot.WEAPON,
        "📌",
        kind=ItemKind.WEAPON,
        image=f"{ADDED_ART}/Shiv_game_inventory_icon_202608280250.jpeg",
        instrumental="заточкой",
        intuition=4,
        damage_min=9,
        damage_max=33,
        crit=0.08,
        level_required=9,
        requires=Stats(intuition=22),
        price=400,
        for_classes=(ASSASSIN,),
    ),
    Item(
        "splitting_axe",
        "Колун",
        Slot.WEAPON,
        "🪵",
        kind=ItemKind.WEAPON,
        image=f"{WEAPON_ART}/Splitting_axe_game_inventory_icon_202608280223.jpeg",
        instrumental="колуном",
        strength=3,
        damage_min=19,
        damage_max=23,
        accuracy=0.08,
        anticrit=0.07,
        level_required=9,
        requires=Stats(endurance=24),
        price=400,
        for_classes=(TANK,),
    ),
    # ---------- награды: их не покупают, их выдают ----------
    Item(
        "hidden_blade",
        "Клинок ассасина",
        Slot.WEAPON,
        "🗡",
        kind=ItemKind.WEAPON,
        instrumental="скрытым клинком",
        image=f"{WEAPON_ART}/Concealed_blade_game_inventory_icon_202608291031.jpeg",
        intuition=2,
        damage_min=3,
        damage_max=9,
        crit=0.15,
        level_required=1,
        requires=Stats(intuition=7),
        reward=True,
        for_classes=(ASSASSIN,),
    ),
    # ---------- лавка мага: только за звёзды ----------
    Item(
        "lightsaber",
        "Световой меч",
        Slot.WEAPON,
        "🗡",
        kind=ItemKind.WEAPON,
        instrumental="световым мечом",
        # У мага картинка лежит в png: у клинка есть свечение по краям
        image=f"{MAGIC_ART}/lightsaber.png",
        damage_min=7,
        damage_max=15,
        dodge=0.35,
        counter=0.25,
        level_required=2,
        requires=Stats(strength=10),
        stars=250,
    ),
)

CATALOGUE: dict[str, Item] = {item.code: item for item in ITEMS}

# Витрина лавки клуба: по типам вещей в порядке слотов карточки, внутри — от
# простого к дорогому. Вещи из лавки мага сюда не попадают: за кредиты их не
# купить, и висеть на прилавке рядом с кастетом им незачем.
SHOWCASE: tuple[Item, ...] = tuple(
    sorted(
        (item for item in ITEMS if not item.is_magic and item.on_sale),
        key=lambda item: (ALL_SLOTS.index(item.slot), item.level_required, item.price),
    )
)

# Прилавок мага: только за звёзды
MAGIC_ITEMS: tuple[Item, ...] = tuple(
    sorted(
        (item for item in ITEMS if item.is_magic and item.on_sale),
        key=lambda item: item.stars,
    )
)


def shop_sections() -> list[tuple[Slot, tuple[Item, ...]]]:
    """Товары, разложенные по типам — так магазин и показывает их."""
    return [
        (slot, tuple(item for item in SHOWCASE if item.slot is slot))
        for slot in ALL_SLOTS
    ]


def items_unlocked_at(level: int) -> tuple[Item, ...]:
    """Что открывается ровно на этом уровне."""
    return tuple(item for item in SHOWCASE if item.level_required == level)


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
    def weapon_damages(self) -> tuple[tuple[int, int], ...]:
        """Прибавка к урону от каждого оружия — по порядку ударов."""
        damages = [
            (self.weapon.item.damage_min, self.weapon.item.damage_max)
            if self.weapon
            else (0, 0)
        ]
        second = self.second_weapon
        if second:
            damages.append((second.item.damage_min, second.item.damage_max))
        return tuple(damages)

    def roll_weapon_damage(self, index: int, rng: random.Random | None = None) -> int:
        """Что добавит оружие этого удара. Кулак не добавляет ничего."""
        weapon = self.weapon if index == 0 else self.second_weapon
        return weapon.item.roll_damage(rng) if weapon else 0

    def __bool__(self) -> bool:
        return bool(self.items)
