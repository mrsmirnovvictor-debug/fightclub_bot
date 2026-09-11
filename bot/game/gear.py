"""Вещи: слоты, из чего состоит предмет, износ и починка.

Здесь только правила — как предмет устроен, куда надевается, как ветшает
и во что обходится починка. Самих вещей здесь нет: их держит
`bot.content.items`, и трогать содержимое лавки можно, не заглядывая
сюда. Собранная экипировка живёт в `bot.game.equipment` — он и остаётся
входом для всех остальных: и правила, и каталог видны оттуда.
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
    # Вторая рука: щит или второе оружие. Щит расширяет блок до трёх зон,
    # второе оружие даёт второй удар за ход.
    OFFHAND = "offhand"
    SHIRT = "shirt"
    BELT = "belt"
    GLOVES = "gloves"
    JACKET = "jacket"
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
        return art.slot(SLOT_ART.get(self, f"{self.value}.png"))

    @property
    def emoji(self) -> str:
        return SLOT_EMOJI[self]


# Имя файла подложки, если оно не совпадает с кодом слота. Во второй руке
# чаще держат щит, им клетка и подписана; клетка «тело» — это футболка с
# верхней одеждой, и силуэт у неё футболочный.
SLOT_ART: dict[Slot, str] = {
    Slot.OFFHAND: "shield.jpeg",
    Slot.JACKET: "shirt.png",
}

SLOT_TITLES: dict[Slot, str] = {
    Slot.HEAD: "головной убор",
    Slot.WEAPON: "оружие",
    Slot.OFFHAND: "вторая рука",
    Slot.SHIRT: "футболка",
    Slot.BELT: "пояс",
    Slot.GLOVES: "перчатки",
    Slot.JACKET: "верхняя одежда",
    Slot.PANTS: "штаны",
    Slot.BOOTS: "обувь",
}

# Тип товара на витрине: это ярлык раздела, а не часть предложения, поэтому
# он короче и называет часть тела, а не саму вещь. Исключение — вторая рука:
# на полке там лежат щиты, ими полка и подписана. Второе оружие покупают на
# полке оружия, а в какую руку его брать — решают уже в карточке.
SLOT_SECTIONS: dict[Slot, str] = {
    Slot.HEAD: "голова",
    Slot.WEAPON: "оружие",
    Slot.OFFHAND: "щиты",
    Slot.SHIRT: "футболки",
    Slot.BELT: "пояс",
    Slot.GLOVES: "перчатки",
    Slot.JACKET: "верхняя одежда",
    Slot.PANTS: "ноги",
    Slot.BOOTS: "обувь",
}

SLOT_EMOJI: dict[Slot, str] = {
    Slot.HEAD: "🎩",
    Slot.WEAPON: "🔪",
    Slot.OFFHAND: "🛡",
    Slot.SHIRT: "👕",
    Slot.BELT: "🥋",
    Slot.GLOVES: "🥊",
    Slot.JACKET: "🧥",
    Slot.PANTS: "👖",
    Slot.BOOTS: "👟",
}

# Чем бьёт боец без оружия
BARE_HANDS = "кулаком"
BARE_HANDS_ICON = "👊"

# Какие зоны прикрывает одежда из слота. Перчатки и оружие брони не дают:
# кулаки и ладони — не зона удара. Футболка и верхняя одежда прикрывают одно
# и то же — их броня складывается, как слои и складываются на самом деле.
SLOT_ZONES: dict[Slot, tuple[Zone, ...]] = {
    Slot.HEAD: (Zone.HEAD,),
    # Щит прикрывает всё сразу — тем и ценен
    Slot.OFFHAND: ALL_ZONES,
    Slot.SHIRT: (Zone.CHEST, Zone.BELLY),
    Slot.JACKET: (Zone.CHEST, Zone.BELLY),
    Slot.BELT: (Zone.BELT,),
    Slot.PANTS: (Zone.BELT, Zone.LEGS),
    Slot.BOOTS: (Zone.LEGS,),
}

# Слева направо на карточке: две колонки по четыре клетки. Футболки своей
# клетки не занимают — они надеваются под верхнюю одежду, и обе вещи живут
# в клетке «тело»: картинкой видно верхнюю, подсказкой — обе.
LEFT_SLOTS: tuple[Slot, ...] = (Slot.HEAD, Slot.WEAPON, Slot.JACKET, Slot.BELT)
RIGHT_SLOTS: tuple[Slot, ...] = (Slot.GLOVES, Slot.OFFHAND, Slot.PANTS, Slot.BOOTS)
# Что лежит в клетке под верхней одеждой
UNDER_SLOTS: dict[Slot, Slot] = {Slot.JACKET: Slot.SHIRT}
# Все слоты модели: футболка отдельная, просто без своей клетки на кукле
ALL_SLOTS: tuple[Slot, ...] = (
    Slot.HEAD,
    Slot.WEAPON,
    Slot.OFFHAND,
    Slot.SHIRT,
    Slot.BELT,
    Slot.GLOVES,
    Slot.JACKET,
    Slot.PANTS,
    Slot.BOOTS,
)

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
    """Чем предмет является в бою: оружие бьёт, остальное держит удар."""

    GEAR = "gear"  # просто вещь с бонусами
    WEAPON = "weapon"
    SHIELD = "shield"  # держат во второй руке: блок шире и броня на все зоны


@dataclass(frozen=True)
class Item:
    """Предмет экипировки: то, что одинаково у всех его экземпляров."""

    code: str
    title: str
    slot: Slot
    icon: str = ""
    # Картинка предмета для мини-аппа. Обычно её задавать не нужно: вещь
    # получает адрес от своего кода (`items/<code>.jpeg`). Строка здесь —
    # для старых файлов, чьи имена под это правило не подходят.
    image: str = ""
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
    # На каком прилавке вещь лежит. Пусто — общий товар клуба, из которого
    # собирается эталонный боец и по которому считается баланс классов.
    # Непустая строка — тематический магазин со своим прилавком и своей
    # ценой: такой товар в лестницу клуба не встаёт и в эталон не попадает.
    shelf: str = ""

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
    def picture(self) -> str:
        """Адрес картинки: свой, если задан, иначе по коду вещи."""
        return self.image or art.item(self.code)

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
        """Куда вещь принимает удар. Во второй руке щитом считается щит."""
        if not (self.armor_min or self.armor_max):
            return ()
        if self.slot is Slot.OFFHAND and not self.is_shield:
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
            return (Slot.WEAPON, Slot.OFFHAND)
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
        return self.item.picture

    @property
    def instrumental(self) -> str:
        return self.item.instrumental

    @property
    def is_weapon(self) -> bool:
        return self.item.is_weapon

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
