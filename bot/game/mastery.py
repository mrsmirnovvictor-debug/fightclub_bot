"""Владение оружием: четыре вида, три хвата и два пула очков.

Класс бойца говорит, чем ему сподручнее драться, но ничего не
запрещает. Прежде оружие держалось на одном уровне и характеристиках:
любой боец мог взять что угодно, лишь бы хватило силы, и выбор оружия
не был выбором. Владение делает его выбором — и выбором игрока, а не
класса: рекомендации у класса есть, запретов нет.

**Два пула, и переносить между ними нельзя.** За уровень боец получает
очко вида и очко хвата. Вид — чем он бьёт (ножи, дубины, молоты, мечи),
хват — как он это держит (второе оружие, двуручное, щит). Один пул
сделал бы выбор ложным: взял щит — остался без оружия своего уровня,
и все ходили бы одинаково.

**Порог равен уровню вещи.** Меч седьмого уровня требует семи очков в
мечах. Очков вида к седьмому уровню ровно семь, поэтому вещь своего
уровня носит тот, кто вложил всё в одно направление; кто разложил очки
по двум видам — носит оружие пониже, зато вдвое шире выбор. Это и есть
цена распыления, и она видна без таблиц.

Сами вещи, их семейства и хваты — в `bot.game.gear` и
`bot.content.items`; очки живут в карточке бойца; экран раздачи — в
мини-аппе. Здесь только правила: сколько очков, что чему требуется и
какая раскладка законна.
"""

from __future__ import annotations

from enum import Enum


class WeaponFamily(str, Enum):
    """Вид оружия: чем боец бьёт."""

    KNIVES = "knives_knuckles"
    CLUBS = "clubs_maces"
    HAMMERS = "hammers_axes"
    SWORDS = "swords"

    @property
    def title(self) -> str:
        return FAMILY_TITLES[self]

    @property
    def emoji(self) -> str:
        return FAMILY_EMOJI[self]

    @property
    def advised(self) -> tuple[str, ...]:
        """Каким классам этот вид рекомендован. Это совет, а не запрет."""
        return FAMILY_ADVICE[self]


class HoldType(str, Enum):
    """Тип владения: как боец держит оружие."""

    OFFHAND = "offhand"
    TWO_HANDED = "two_handed"
    SHIELD = "shield"

    @property
    def title(self) -> str:
        return HOLD_TITLES[self]

    @property
    def emoji(self) -> str:
        return HOLD_EMOJI[self]

    @property
    def advised(self) -> tuple[str, ...]:
        """Каким классам этот хват рекомендован. Это совет, а не запрет."""
        return HOLD_ADVICE[self]


FAMILY_TITLES: dict[WeaponFamily, str] = {
    WeaponFamily.KNIVES: "Ножи и кастеты",
    WeaponFamily.CLUBS: "Дубины и булавы",
    WeaponFamily.HAMMERS: "Молоты и топоры",
    WeaponFamily.SWORDS: "Мечи",
}

FAMILY_EMOJI: dict[WeaponFamily, str] = {
    WeaponFamily.KNIVES: "🔪",
    WeaponFamily.CLUBS: "🏏",
    WeaponFamily.HAMMERS: "🔨",
    WeaponFamily.SWORDS: "⚔️",
}

# Кому что сподручнее. Рекомендация идёт от того, чем класс дерётся в
# игре: трикстер живёт на скорости и коротком клинке, танк — на весе
# удара, ассасин — на длинном клинке, воин — на том, что держат одной
# рукой и бьют наотмашь
FAMILY_ADVICE: dict[WeaponFamily, tuple[str, ...]] = {
    WeaponFamily.KNIVES: ("rogue",),
    WeaponFamily.CLUBS: ("warrior",),
    WeaponFamily.HAMMERS: ("tank",),
    WeaponFamily.SWORDS: ("assassin",),
}

HOLD_TITLES: dict[HoldType, str] = {
    HoldType.OFFHAND: "Второе оружие",
    HoldType.TWO_HANDED: "Двуручное оружие",
    HoldType.SHIELD: "Щиты",
}

HOLD_EMOJI: dict[HoldType, str] = {
    HoldType.OFFHAND: "🗡",
    HoldType.TWO_HANDED: "🪓",
    HoldType.SHIELD: "🛡",
}

# Второй рукой работают быстрые классы и воин, двуручным — те, кто
# вкладывается в один большой удар, щитом — те, кому есть что держать.
# Танку щит положен по устройству класса, трикстеру — потому что блок у
# него узкий и широкий блок щита закрывает его слабое место
HOLD_ADVICE: dict[HoldType, tuple[str, ...]] = {
    HoldType.OFFHAND: ("rogue", "assassin", "warrior"),
    HoldType.TWO_HANDED: ("warrior", "assassin"),
    HoldType.SHIELD: ("tank", "warrior", "rogue"),
}

ALL_FAMILIES: tuple[WeaponFamily, ...] = tuple(WeaponFamily)
ALL_HOLDS: tuple[HoldType, ...] = tuple(HoldType)

# Сколько очков каждого пула боец получает за уровень и сколько у него
# есть на первом. Единица на старте — не щедрость, а условие: без неё
# новичок до второго уровня не мог бы взять в руки даже дубинку первого
# уровня, которая требует владения в единицу
POINTS_PER_LEVEL = 1
START_POINTS = 1


def points_at(level: int) -> int:
    """Сколько очков одного пула у бойца этого уровня — всего, с учётом вложенных.

    Старт плюс по очку за каждое повышение: на первом уровне одно, на
    седьмом семь. Число нарочно совпадает с уровнем — на нём держится
    порог вещей: специалист носит оружие своего уровня ровно потому, что
    очков у него столько же, сколько уровней.
    """
    return START_POINTS + POINTS_PER_LEVEL * (max(1, level) - 1)


def required_for(level_required: int) -> int:
    """Какое владение требует вещь этого уровня.

    Порог равен уровню вещи. Это самое простое правило из возможных, и
    оно же самое честное: игрок видит уровень вещи на прилавке и сразу
    знает, сколько очков в неё вложить. Лестница помягче — скажем,
    половина уровня — открывала бы два направления разом, и выбор вида
    снова перестал бы быть выбором.
    """
    return max(0, level_required)


def advice_for(class_code: str) -> tuple[tuple[WeaponFamily, ...], tuple[HoldType, ...]]:
    """Что рекомендовано этому классу: виды и хваты.

    Нужно только экрану — подсветить своё. Запрета за этим нет: любой
    класс вкладывает очки куда хочет, и матрица боя это допускает.
    """
    families = tuple(one for one in ALL_FAMILIES if class_code in one.advised)
    holds = tuple(one for one in ALL_HOLDS if class_code in one.advised)
    return families, holds


def spent(points: dict[str, int]) -> int:
    """Сколько очков пула уже вложено. Отрицательных вложений не бывает."""
    return sum(max(0, value) for value in points.values())


def free_points(level: int, points: dict[str, int]) -> int:
    """Сколько очков пула осталось свободными у бойца этого уровня.

    Считается, а не хранится: иначе остаток и вложенное разошлись бы
    при первой же правке лестницы, и спорить с базой пришлось бы о том,
    чего в ней нет.
    """
    return max(0, points_at(level) - spent(points))


def may_spend(level: int, points: dict[str, int], amount: int = 1) -> bool:
    """Хватает ли свободных очков на такое вложение."""
    return 0 < amount <= free_points(level, points)


def enough(invested: int, level_required: int) -> bool:
    """Хватает ли вложенного в это направление на вещь такого уровня.

    `invested` — очки в нужном виде или хвате, а не весь пул: владение
    мечами не помогает держать молот.
    """
    return max(0, invested) >= required_for(level_required)


__all__ = [
    "ALL_FAMILIES",
    "ALL_HOLDS",
    "FAMILY_ADVICE",
    "HOLD_ADVICE",
    "HoldType",
    "POINTS_PER_LEVEL",
    "START_POINTS",
    "WeaponFamily",
    "advice_for",
    "free_points",
    "enough",
    "may_spend",
    "points_at",
    "required_for",
    "spent",
]
