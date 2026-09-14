"""Товар мастерской: заточки и модификаторы, по записи на каждый.

Здесь только содержимое — названия, полосы и цены. Правила, по которым
модификатор ложится на вещь, живут в `bot.game.gear`: там же, где правила
самих вещей.

Сетка ровная, поэтому и собирается перебором, а не тридцатью пятью
записями руками: пять ступеней × три вида, и у модификации предмета ещё и
своя доля на каждую характеристику. Руками здесь только числа — полосы,
цены и названия ступеней, — и именно их правят, когда меняют баланс.

Картинка у каждого модификатора своя и берётся от кода (`items/<code>.jpeg`),
как и у вещей. Не доехал файл — на его месте останется значок вида.

Тринадцать файлов приехали в бакет перепутанными между собой: на картинке
профессиональной заточки щита нарисована элитная, и так далее по цепочке.
Переименовать их в бакете дороже, чем развести адреса здесь, — для того у
модификатора и есть поле `image`. Разбирается это ниже, в `MIXED_UP`.
"""

from __future__ import annotations

from bot.game.art import item as item_art
from bot.game.gear import MOD_STAT_TITLES, ModKind, Modifier

# Ступени: номер, название, цена. Цена одна на все три вида — платят за
# ступень, а не за то, что точишь
LEVELS: tuple[tuple[int, str, int], ...] = (
    (1, "Простая", 500),
    (2, "Улучшенная", 1000),
    (3, "Профессиональная", 2000),
    (4, "Мастерская", 3000),
    (5, "Элитная", 5000),
)

# Полосы заточки: на столько вырастут обе границы урона или брони
SHARPEN: dict[int, tuple[int, int]] = {
    1: (1, 5),
    2: (3, 6),
    3: (5, 10),
    4: (10, 15),
    5: (15, 20),
}

# Полосы модификации предмета, в целых процентах
SHARES: dict[int, tuple[int, int]] = {
    1: (5, 7),
    2: (7, 10),
    3: (10, 15),
    4: (15, 25),
    5: (25, 50),
}

# Значок ступени: по нему модификатор узнают в рюкзаке, и та же звёздочка
# потом горит на вещи. Серая, жёлтая, оранжевая, фиолетовая, красная
STARS: dict[int, str] = {1: "⚪", 2: "🟡", 3: "🟠", 4: "🟣", 5: "🔴"}

# Названия ступеней склоняются под вид: «Простая заточка», но «Простой
# модификатор». Род разный, и подставлять одно слово в оба нельзя
FEMININE = {"Простая": "Простая", "Улучшенная": "Улучшенная",
            "Профессиональная": "Профессиональная", "Мастерская": "Мастерская",
            "Элитная": "Элитная"}
MASCULINE = {"Простая": "Простой", "Улучшенная": "Улучшенный",
             "Профессиональная": "Профессиональный", "Мастерская": "Мастерский",
             "Элитная": "Элитный"}

# Значок характеристики — тот же, что в карточке бойца
SHARE_ICONS: dict[str, str] = {
    "accuracy": "🎯",
    "dodge": "🌀",
    "crit": "💥",
    "anticrit": "🩻",
    "counter": "🔄",
}


# Код модификатора → файл, в котором на самом деле лежит его картинка.
# Это замкнутая перестановка: каждый файл из списка используется ровно
# один раз, и тест это проверяет — иначе два модификатора поделили бы одну
# картинку, а третий остался бы без своей.
MIXED_UP: dict[str, str] = {
    # заточка щита: 3→4→5→3
    "sharpen_shield_3": "sharpen_shield_4",
    "sharpen_shield_4": "sharpen_shield_5",
    "sharpen_shield_5": "sharpen_shield_3",
    # точность: мастерская с элитной поменялись местами
    "mod_accuracy_4": "mod_accuracy_5",
    "mod_accuracy_5": "mod_accuracy_4",
    # элитный крит уехал к улучшенному антикриту, и наоборот
    "mod_crit_5": "mod_anticrit_2",
    "mod_anticrit_2": "mod_crit_5",
    # длинная цепочка: контрудар и верх антикрита сдвинуты по кругу
    "mod_counter_1": "mod_anticrit_5",
    "mod_counter_2": "mod_counter_1",
    "mod_counter_4": "mod_counter_2",
    "mod_counter_5": "mod_counter_4",
    "mod_anticrit_4": "mod_counter_5",
    "mod_anticrit_5": "mod_anticrit_4",
}


def _art(code: str) -> str:
    """Адрес картинки: пусто у тех, чей файл лежит под своим кодом."""
    file = MIXED_UP.get(code)
    return item_art(file) if file else ""


def _sharpen(kind: ModKind, what: str, icon: str) -> list[Modifier]:
    """Пять ступеней заточки для оружия или щита."""
    return [
        Modifier(
            code=f"sharpen_{kind.value}_{level}",
            title=f"{FEMININE[name]} заточка {what}",
            kind=kind,
            level=level,
            low=SHARPEN[level][0],
            high=SHARPEN[level][1],
            price=price,
            icon=icon,
            image=_art(f"sharpen_{kind.value}_{level}"),
        )
        for level, name, price in LEVELS
    ]


def _shares() -> list[Modifier]:
    """По пять ступеней на каждую характеристику, которую можно поднять."""
    rows: list[Modifier] = []
    for stat, title in MOD_STAT_TITLES.items():
        for level, name, price in LEVELS:
            rows.append(
                Modifier(
                    code=f"mod_{stat}_{level}",
                    title=f"{MASCULINE[name]} модификатор: {title}",
                    kind=ModKind.GEAR,
                    level=level,
                    low=SHARES[level][0],
                    high=SHARES[level][1],
                    price=price,
                    stat=stat,
                    icon=SHARE_ICONS.get(stat, "✨"),
                    image=_art(f"mod_{stat}_{level}"),
                )
            )
    return rows


MODS: tuple[Modifier, ...] = tuple(
    _sharpen(ModKind.WEAPON, "оружия", "🗡")
    + _sharpen(ModKind.SHIELD, "щита", "🛡")
    + _shares()
)

CATALOGUE: dict[str, Modifier] = {mod.code: mod for mod in MODS}


def get_mod(code: str) -> Modifier | None:
    return CATALOGUE.get(code)


def mods_of_kind(kind: ModKind) -> tuple[Modifier, ...]:
    """Прилавок одного вида — в порядке ступеней."""
    return tuple(
        sorted(
            (mod for mod in MODS if mod.kind is kind),
            key=lambda mod: (mod.stat, mod.level),
        )
    )


def star_of(level: int) -> str:
    return STARS.get(level, "")


__all__ = [
    "CATALOGUE",
    "MIXED_UP",
    "LEVELS",
    "MODS",
    "SHARES",
    "SHARPEN",
    "STARS",
    "get_mod",
    "mods_of_kind",
    "star_of",
]
