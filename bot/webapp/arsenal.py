"""Сверочный лист оружейной: всё, что боец берёт в руки, одной выдачей.

Нужен не игре, а владельцу: по нему сверяют арт с названиями и числами.
Поэтому здесь нет ни бойца, ни его денег — только каталог, как он есть
на этом выкате. Страница (`static/arsenal.html`) читает эту выдачу и
рисует карточки; адреса картинок приходят отсюда, потому что бакет в
вёрстке не пишется — все адреса картинок в игре живут в `bot.game.art`.

**Почему это отдельный лист, а не прилавок.** Прилавок показывает то,
что боец может купить здесь и сейчас: снятое с продажи, наградное и
рейдовое на него не попадает. Для сверки картинок нужно обратное — весь
каталог разом, вместе со снятым и наградным: арт у них тоже есть, и
разойтись с названием он может точно так же.
"""

from __future__ import annotations

from bot.game.classes import ALL_STATS, FIGHTER_CLASSES
from bot.game.equipment import CATALOGUE, SET_TITLES, Item, ItemKind
from bot.game.gear import HAND_TITLES, hold_required
from bot.game.mastery import HoldType, WeaponFamily

# Что берут в руки: оружие, щит и огнестрел. Остальное — одежда и
# украшения, и к владению оружием они отношения не имеют
IN_HANDS = (ItemKind.WEAPON, ItemKind.SHIELD, ItemKind.FIREARM)

KIND_TITLES: dict[ItemKind, str] = {
    ItemKind.WEAPON: "Оружие",
    ItemKind.SHIELD: "Щит",
    ItemKind.FIREARM: "Огнестрел",
}

# Что нарисовано на картинке, когда это расходится с названием вещи.
# Лист для того и сделан: пока арт не догнал каталог, он обязан
# говорить, что именно разошлось. Список закрытый и правится руками —
# это память о том, где картинка отстала, а не правило.
#
# **Сейчас он пуст, и это не упущение.** Арт-пак от 28 ноября закрыл
# всё: двенадцать вторых вещей в руки у наборов, десять достроенных
# ступеней лавки и семь переименованных комплектных, у которых картинка
# спорила с названием. Пустой список означает, что каталог и арт
# сошлись; строки сюда вписывают, когда они снова разойдутся.
DRAWN_INSTEAD: dict[str, str] = {}


# Числа, которые владелец правит прямо на листе. Поле названо так, как
# оно зовётся в каталоге (`bot/content/items.py`): правку потом
# переносят в код, и перевод названий туда-обратно — лишний шаг, на
# котором теряются вещи. Доли идут процентами: на вещи они записаны
# частями единицы, а читают и правят их процентами.
EDITABLE: tuple[tuple[str, str, str], ...] = (
    ("damage_min", "Урон от", ""),
    ("damage_max", "Урон до", ""),
    ("armor_min", "Броня от", ""),
    ("armor_max", "Броня до", ""),
    ("strength", "Сила", ""),
    ("agility", "Проворство", ""),
    ("intuition", "Интуиция", ""),
    ("hp", "Запас", ""),
    ("accuracy", "Точность", "%"),
    ("dodge", "Уворот", "%"),
    ("crit", "Крит", "%"),
    ("anticrit", "Антикрит", "%"),
    ("counter", "Контрудар", "%"),
    ("level_required", "Уровень", ""),
    ("price", "Цена", "кр."),
    ("stars", "Цена", "⭐"),
    ("shots", "Патроны", ""),
)


def edit_row(item: Item) -> list[dict]:
    """Числа этой вещи так, как их правят на листе.

    Показываются не все поля подряд, а те, что у вещи есть: броня у
    оружия, патроны у щита и звёзды у лавочной вещи — пустые графы, и
    место они занимают зря. Нулевые прибавки при этом остаются: их как
    раз и заводят, когда вещи чего-то не хватает.
    """
    rows: list[dict] = []
    for field, title, unit in EDITABLE:
        value = getattr(item, field)
        if field in ("armor_min", "armor_max") and not item.armor_max:
            continue
        if field in ("damage_min", "damage_max") and not item.damage_max:
            continue
        if field == "shots" and not item.shots:
            continue
        if field == "price" and item.stars:
            continue
        if field == "stars" and not item.stars:
            continue
        if unit == "%":
            value = round(value * 100)
        rows.append({"field": field, "title": title, "value": value, "unit": unit})
    for stat in ALL_STATS:
        rows.append(
            {
                "field": f"requires.{stat.value}",
                "title": f"Нужно: {stat.title}",
                "value": item.requires.get(stat) or 0,
                "unit": "",
            }
        )
    return rows


def item_row(item: Item) -> dict:
    """Одна вещь так, как её показывает сверочный лист."""
    hold = hold_required(item)
    return {
        "code": item.code,
        "title": item.title,
        "image": item.picture,
        "icon": item.icon,
        "kind": KIND_TITLES[item.kind],
        "family": WeaponFamily(item.family).title if item.family else "",
        # Код вида — для порядка на листе: подпись сортировать нельзя,
        # она про чтение, а не про последовательность правил
        "family_code": item.family,
        "damage_kind": WeaponFamily(item.family).damage if item.family else "",
        "heavy": bool(item.two_handed),
        # Хват — про оружие. Щит держат второй рукой всегда, и «основная
        # рука» в его строке была бы прямой неправдой
        "hand": "" if item.kind is ItemKind.SHIELD else HAND_TITLES[item.hand],
        "hold": HoldType(hold).title if hold else "",
        "level": item.level_required,
        "mastery": item.mastery_required,
        "set": SET_TITLES.get(item.kit, item.kit) if item.kit else "",
        "classes": [
            FIGHTER_CLASSES[code].title
            for code in item.for_classes
            if code in FIGHTER_CLASSES
        ],
        "requires": {
            stat.title: item.requires.get(stat)
            for stat in ALL_STATS
            if item.requires.get(stat)
        },
        "damage": [item.damage_min, item.damage_max] if item.damage_max else None,
        "armor": [item.armor_min, item.armor_max] if item.armor_max else None,
        "gains": {
            title: value
            for title, value in (
                ("Сила", item.strength),
                ("Проворство", item.agility),
                ("Интуиция", item.intuition),
                ("Здоровье", item.hp),
            )
            if value
        },
        # Доли на вещи записаны частями единицы, а читают их процентами
        "shares": {
            title: round(value * 100)
            for title, value in (
                ("Точность", item.accuracy),
                ("Уворот", item.dodge),
                ("Крит", item.crit),
                ("Антикрит", item.anticrit),
                ("Контрудар", item.counter),
            )
            if value
        },
        "price": item.price,
        "stars": item.stars,
        "reward": bool(item.reward),
        "retired": bool(item.retired),
        "shots": item.shots,
        "two_handed": bool(item.two_handed),
        "drawn": DRAWN_INSTEAD.get(item.code, ""),
        # Числа для правки — те же, что выше, но полями каталога и
        # одним списком: лист ими рисует поля ввода
        "edit": edit_row(item),
    }


# Порядок видов на листе — тот же, в каком их читают правила: колющее,
# рубящее, дробящее, режущее. Щиты и огнестрел вида не имеют и идут
# после всего оружия
FAMILY_ORDER: dict[str, int] = {
    family.value: number for number, family in enumerate(WeaponFamily)
}

# Сперва оружие, потом щиты, последним огнестрел: он в клубе один, и
# место ему в конце, а не вперемешку со щитами своей ступени
KIND_ORDER: tuple[ItemKind, ...] = (ItemKind.WEAPON, ItemKind.SHIELD, ItemKind.FIREARM)


def arsenal_payload() -> dict:
    """Весь арсенал по видам: оружие по семействам и ступеням, потом щиты.

    Порядок — каталожный, а не разборочный. Прежде первыми стояли
    вещи, у которых картинка разошлась с названием, и это было удобно,
    пока их было семь. Когда их стало тридцать три — а у двадцати
    шести картинки нет вовсе, — первые экраны листа превратились в
    стену пустых рамок, и сверять арт по нему стало нельзя: того, что
    нарисовано, не видно.

    Теперь лист идёт как каталог: всё оружие по видам (колющее,
    рубящее, дробящее, режущее), внутри вида по ступеням, дальше щиты и
    огнестрел. Спорное и ненарисованное помечено рамкой у карточки и
    собирается одним нажатием по кнопке отбора — выбор остался, а
    по умолчанию виден весь арсенал.

    Тип урона приезжает строкой у каждой вещи: вид и тип урона это одно
    и то же, и на листе сверки видно, чем вещь бьёт, а не только как
    она зовётся.
    """
    rows = [item_row(item) for item in CATALOGUE.values() if item.kind in IN_HANDS]
    ranks = {KIND_TITLES[kind]: number for number, kind in enumerate(KIND_ORDER)}
    rows.sort(
        key=lambda row: (
            ranks[row["kind"]],
            FAMILY_ORDER.get(row["family_code"], len(FAMILY_ORDER)),
            row["level"],
            row["title"],
        )
    )
    return {
        "items": rows,
        "counts": {
            "all": len(rows),
            "weapons": sum(1 for row in rows if row["kind"] == "Оружие"),
            "shields": sum(1 for row in rows if row["kind"] == "Щит"),
            "in_sets": sum(1 for row in rows if row["set"]),
            "drawn_apart": sum(1 for row in rows if row["drawn"]),
        },
    }


__all__ = [
    "DRAWN_INSTEAD",
    "EDITABLE",
    "FAMILY_ORDER",
    "IN_HANDS",
    "KIND_ORDER",
    "arsenal_payload",
    "edit_row",
    "item_row",
]
