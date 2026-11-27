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

# Чем вещь была до перехода на семейства. Картинка в бакете осталась
# прежней, и на ней — прежнее оружие: пока арт не перерисован, лист
# должен говорить, что именно разошлось с названием. Список закрытый и
# правится руками: это память об одной правке каталога, а не правило
WAS_CALLED: dict[str, str] = {
    "set_plut_weapon": "Булава с маской шута",
    "set_mercenary_weapon": "Кукри с волком",
    "set_boxer_weapon": "Бронзовый кастет",
    "set_biker_weapon": "Топор с крылатым черепом",
    "set_croupier_weapon": "Трость с рулеткой",
    "set_strangler_weapon": "Цепь с рукоятями",
    "set_fighter_weapon": "Стальной кастет",
    "set_camouflage_weapon": "Нож со змеёй",
    "set_bouncer_weapon": "Дубинка с бульдогом",
    "set_killer_weapon": "Рубиновый кинжал",
}


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
        "was": WAS_CALLED.get(item.code, ""),
    }


def arsenal_payload() -> dict:
    """Весь арсенал: сперва наборы с переименованным, потом остальное.

    Порядок — рабочий, под сверку арта: вещи, у которых картинка
    разошлась с названием, стоят первыми, дальше остальные наборы, и
    только потом всё, что вне наборов. Внутри — по уровню.
    """
    rows = [item_row(item) for item in CATALOGUE.values() if item.kind in IN_HANDS]
    rows.sort(
        key=lambda row: (
            row["kind"] != "Оружие",
            not row["was"],
            row["set"] == "",
            row["set"],
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
            "renamed": sum(1 for row in rows if row["was"]),
        },
    }


__all__ = ["IN_HANDS", "WAS_CALLED", "arsenal_payload", "item_row"]
