"""Городская охрана: трое у дверей всякой точки, которой не правит семья.

Это не найм и никогда им не был: охрана ничья, бесплатная и в чей-то
счёт не идёт. Дом не бывает без сторожа, даже когда за него никто не
дерётся — и чтобы взять ничью точку, семье приходится этих троих
пройти.

За семью город не дерётся. Точка, взятая семьёй и брошенная без
охраны, стоит пустая: входи и бери. Это та же мысль, что и с доходом —
точка принадлежит тому, кто её держит, а не тому, кто однажды на ней
подрался.

**Чем это было раньше.** В городе продавали наёмников и патрульных:
ступень с готовыми числами, которую ставили на точку за деньги и
которая гибла насовсем. Их заменил отряд (`bot.game.squad`) — живые
бойцы со своими характеристиками, вещами и уровнями. От прежнего
найма остался только этот пост у дверей: он не покупался и тогда.
"""

from __future__ import annotations

from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.combat import Fighter
from bot.game.equipment import CATALOGUE, Equipment, OwnedItem, Slot

# Трое четвёртого уровня, класс танковый: такую охрану проходят, но не
# походя
GUARD_LEVEL = 4
GUARD_CLASS = "tank"
GUARD_SIZE = 3
GUARD_ART = "guard_4_tank"


def guard_picture() -> str:
    """Лицо городской охраны. Одно на всех троих: это не люди, а пост."""
    from bot.game.art import npc_avatar

    return npc_avatar(GUARD_ART)


def _weapon_for(level: int, fclass: str) -> OwnedItem | None:
    """Оружие из лавки по росту и по классу.

    Берём самое дорогое с обычного прилавка, что этому уровню
    разрешено и этому классу годится: голыми руками охрану не
    выпускают, но и фанатская бита за полторы тысячи ей не положена.
    """
    fits = [
        item
        for item in CATALOGUE.values()
        if item.slot == Slot.WEAPON
        and item.price
        and item.level_required <= level
        and (not item.for_classes or fclass in item.for_classes)
    ]
    if not fits:
        return None
    best = max(fits, key=lambda item: (item.level_required, item.price))
    return OwnedItem(item=best, slot=Slot.WEAPON)


def _stats_for(level: int) -> Stats:
    """Характеристики охранника: ровные, по уровню и без изысков."""
    return Stats(
        strength=4 + level,
        agility=2 + level // 2,
        intuition=2 + level // 2,
        endurance=6 + level,
    )


def _one(number: int, name: str, face: str) -> Fighter:
    weapon = _weapon_for(GUARD_LEVEL, GUARD_CLASS)
    return Fighter(
        user_id=number,
        name=name,
        fclass=FIGHTER_CLASSES[GUARD_CLASS],
        stats=_stats_for(GUARD_LEVEL),
        level=GUARD_LEVEL,
        equipment=Equipment(items={Slot.WEAPON: weapon} if weapon else {}),
        picture=face,
    )


def guards(start: int = -1) -> list[Fighter]:
    """Городская охрана как бойцы боя.

    Номера отрицательные и сквозные: судья ищет бойца по номеру, и два
    одинаковых в одном гарнизоне обрушили бы поиск.
    """
    face = guard_picture()
    return [
        _one(start - number, f"Охранник {number + 1}", face)
        for number in range(GUARD_SIZE)
    ]


__all__ = [
    "GUARD_ART",
    "GUARD_CLASS",
    "GUARD_LEVEL",
    "GUARD_SIZE",
    "guard_picture",
    "guards",
]
