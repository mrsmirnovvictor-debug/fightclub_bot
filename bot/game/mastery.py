"""Владение оружием: четыре вида, три навыка боя и два пула очков.

Класс бойца говорит, чем ему сподручнее драться, но ничего не
запрещает. Прежде оружие держалось на одном уровне и характеристиках:
любой боец мог взять что угодно, лишь бы хватило силы, и выбор оружия
не был выбором. Владение делает его выбором — и выбором игрока, а не
класса: рекомендации у класса есть, запретов нет.

**Вид — это тип урона.** Ножи и кастеты колют, топоры и секиры рубят,
дубины и молоты дробят, мечи режут; у каждого вида свой класс, которому
он сподручнее. Тип урона пока только называет вид и стоит подписью в
карточке вещи: на числа боя он не влияет, и заводить его влияние — это
отдельная задача с отдельным прогоном баланса, а не строчка здесь.

**Два пула, и переносить между ними нельзя.** За уровень боец получает
очко вида и очко навыка. Вид — чем он бьёт, навык боя — как он это
держит (второе оружие, двуручное, щит). На экране это два столбца,
«Владение оружием» и «Навыки боя», и подписаны они так же. Один пул
сделал бы выбор ложным: взял щит — остался без оружия своего уровня, и
все ходили бы одинаково.

**Вес вещи решает хват, а не боец.** Лёгкое берут любой рукой: его
можно взять вторым оружием или оставить руку под щит. Тяжёлое — секиры,
тяжёлые дубины с молотами, тяжёлые мечи — всегда двуручное, и рядом с
ним не встаёт ни щит, ни второе оружие. У колющих тяжёлых не бывает
вовсе: нож остаётся ножом, и двуручного ножа в клубе нет.

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
    """Вид оружия: чем боец бьёт и каким уроном.

    Код вида — сам тип урона: вид и тип урона это одно и то же, и два
    названия для одного развелись бы при первой же правке. Прежние коды
    (`knives_knuckles`, `hammers_axes`) звались по вещам, а вещи
    переехали: топоры ушли воину, молоты остались танку, — и код,
    который перечисляет вещи, такую правку не переживает.
    """

    PIERCING = "piercing"
    CHOPPING = "chopping"
    CRUSHING = "crushing"
    SLASHING = "slashing"

    @property
    def title(self) -> str:
        return FAMILY_TITLES[self]

    @property
    def damage(self) -> str:
        """Каким уроном бьёт это оружие: колющим, рубящим, дробящим, режущим."""
        return FAMILY_DAMAGE[self]

    @property
    def emoji(self) -> str:
        return FAMILY_EMOJI[self]

    @property
    def heavy_allowed(self) -> bool:
        """Бывает ли в этом виде тяжёлое — то, что берут обеими руками.

        У колющих не бывает: нож остаётся ножом в любой руке, а
        двуручный нож это уже не нож. Отдача колющего — парный хват, и
        за него платят очками навыка «Второе оружие».
        """
        return self is not WeaponFamily.PIERCING

    @property
    def advised(self) -> tuple[str, ...]:
        """Каким классам этот вид рекомендован. Это совет, а не запрет."""
        return FAMILY_ADVICE[self]


class HoldType(str, Enum):
    """Навык боя: как боец держит оружие. На экране — «Навыки боя»."""

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
        """Каким классам этот навык рекомендован. Это совет, а не запрет."""
        return HOLD_ADVICE[self]


# Колющее зовётся «Ножи и кастеты», а не «Ножи и кинжалы»: кинжал — тот
# же нож, и второе слово ничего не добавляло, а кастеты и трости в виде
# есть и в название не попадали. Четыре кастета и две трости из двадцати
# шести вещей вида — достаточно, чтобы стоять в имени
FAMILY_TITLES: dict[WeaponFamily, str] = {
    WeaponFamily.PIERCING: "Ножи и кастеты",
    WeaponFamily.CHOPPING: "Топоры и секиры",
    WeaponFamily.CRUSHING: "Дубины и молоты",
    WeaponFamily.SLASHING: "Мечи",
}

# Тип урона. Он же смысл вида: кастет и трость колют, секира рубит,
# кувалда дробит, сабля режет. Пока это подпись в карточке вещи и
# ничего больше — на числа боя тип урона не влияет
FAMILY_DAMAGE: dict[WeaponFamily, str] = {
    WeaponFamily.PIERCING: "колющий",
    WeaponFamily.CHOPPING: "рубящий",
    WeaponFamily.CRUSHING: "дробящий",
    WeaponFamily.SLASHING: "режущий",
}

FAMILY_EMOJI: dict[WeaponFamily, str] = {
    WeaponFamily.PIERCING: "🔪",
    WeaponFamily.CHOPPING: "🪓",
    WeaponFamily.CRUSHING: "🔨",
    WeaponFamily.SLASHING: "⚔️",
}

# Кому что сподручнее. Рекомендация идёт от того, чем класс дерётся в
# игре: трикстер живёт на скорости и коротком клинке, воин — на
# замахе топора, танк — на весе удара, ассасин — на длинном клинке.
# Прежде воину советовали дубины, а танку топоры; теперь наоборот —
# дробящее тяжелее и ближе танку, рубящее быстрее и ближе воину
FAMILY_ADVICE: dict[WeaponFamily, tuple[str, ...]] = {
    WeaponFamily.PIERCING: ("rogue",),
    WeaponFamily.CHOPPING: ("warrior",),
    WeaponFamily.CRUSHING: ("tank",),
    WeaponFamily.SLASHING: ("assassin",),
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

# Второй рукой работают быстрые классы и воин, двуручным — те, в чьём
# виде есть тяжёлое, щитом — те, кому есть что держать. Танку щит
# положен по устройству класса, трикстеру — потому что блок у него
# узкий и широкий блок щита закрывает его слабое место.
#
# Двуручное трикстеру не советуют и советовать нечего: колющего
# тяжёлого не бывает (`heavy_allowed`), и его отдача — парный хват. У
# остальных трёх видов тяжёлое есть, поэтому и совет есть
HOLD_ADVICE: dict[HoldType, tuple[str, ...]] = {
    HoldType.OFFHAND: ("rogue", "assassin", "warrior", "tank"),
    HoldType.TWO_HANDED: ("warrior", "tank", "assassin"),
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


def pack(points: dict[str, int]) -> str:
    """Вложенное одной строкой для базы: «clubs_maces:2,swords:7».

    Колонка на каждый вид и хват стоила бы семи колонок и миграции на
    каждое новое направление, а спрашивают их всегда целиком и только у
    своего бойца. Пустые вложения в строку не идут: ноль — это
    отсутствие записи, и писать его значило бы хранить пустоту.

    Порядок в строке — по коду: одна и та же раскладка обязана
    сохраняться одинаково, иначе строки в базе расходятся на ровном месте.
    """
    return ",".join(
        f"{code}:{value}"
        for code, value in sorted(points.items())
        if value > 0
    )


def unpack(text: str, codes: tuple[str, ...]) -> dict[str, int]:
    """Разобрать строку базы обратно в раскладку.

    Чужие и ломаные куски молча отбрасываем: `codes` — это весь список
    направлений, какие бывают, и строка из будущей версии не должна
    ронять карточку бойца. Отрицательных вложений не бывает.
    """
    out: dict[str, int] = {}
    for chunk in str(text or "").split(","):
        code, _, value = chunk.partition(":")
        if code not in codes or not value.strip().lstrip("-").isdigit():
            continue
        if int(value) > 0:
            out[code] = int(value)
    return out


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


FAMILY_CODES: tuple[str, ...] = tuple(one.value for one in ALL_FAMILIES)
HOLD_CODES: tuple[str, ...] = tuple(one.value for one in ALL_HOLDS)


__all__ = [
    "ALL_FAMILIES",
    "FAMILY_DAMAGE",
    "FAMILY_TITLES",
    "ALL_HOLDS",
    "FAMILY_ADVICE",
    "HOLD_ADVICE",
    "HoldType",
    "POINTS_PER_LEVEL",
    "START_POINTS",
    "WeaponFamily",
    "FAMILY_CODES",
    "HOLD_CODES",
    "advice_for",
    "free_points",
    "enough",
    "may_spend",
    "pack",
    "points_at",
    "required_for",
    "spent",
    "unpack",
]
