"""Патрули города: две пары полицейских, которые ходят по домам.

Живой полиции в городе может не быть вовсе — кресла в участке стоят
пустыми, — а ордера выписываются каждый день. Без второй половины
заявление жертвы было бы бумажкой: написать его можно, исполнить
некому. Эти четыре — то, чем город исполняет ордер сам.

**Их четверо, и ходят они по двое.** Один патрульный против
разыскиваемого — это дуэль, в которой город проигрывает чаще, чем
побеждает; четверо разом — это облава, от которой нет смысла бежать.
Двое — ровно та сила, при которой задержание возможно, но не
бесплатно.

**Часов у патруля нет.** Где пара стоит сейчас — считается от самого
времени: сутки нарезаны на смены, смена делится на дома маршрута.
Никакого фонового обхода, никаких таймеров: любой, кто посмотрел,
получает тот же ответ, и перезапуск бота ничего не сдвигает. Это тот
же ленивый расчёт, которым в клубе сводятся недели и доход с точек.

**Маршруты открытые.** Пару видно и в доме, и на карте города — у
разыскиваемого есть выбор, куда не ходить. Прятаться от патруля
законно: это и есть игра, которую ему оставляют. Невидимый патруль,
берущий человека без предупреждения, был бы не игрой, а штрафом за
вход в город.

**В дома семьи и к больнице они не заходят.** Особняк — имущество
семьи, больница и тюрьма — места, где человека и так держат. Патруль
ходит по общественным домам: бар, рынок, казино, клуб.

Действия — в `bot.patrol_service`, правила ордера и отсидки — в
`bot.game.police`.
"""

from __future__ import annotations

from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.combat import Fighter
from bot.game.economy import MAX_LEVEL
from bot.game.equipment import CATALOGUE, Equipment, OwnedItem, Slot

# Сколько патрульных в паре и сколько пар в городе. Четверо — это две
# пары на две части города: центр и окраина
PAIR_SIZE = 2
PAIRS = 2
OFFICERS = PAIR_SIZE * PAIRS

# Номера патрульных в бою. Отрицательные, как у всякого, у кого нет
# аккаунта, и своим блоком: у Тренера −1, у городской охраны −1 и
# ниже, у бойцов отряда −100 и ниже, у отряда на штурме −1000 и ниже.
# Столкнуться они не могут и так — патрульных нет в базе, и в одной
# драке с охраной или отрядом они не стоят, — но блок свой, чтобы это
# было видно по номеру
FIRST_ID = -201

# Четверо городских: двое патрульных и двое сержантов, у каждого своё
# лицо. Пара собирается всегда одинаково — патрульный и сержант:
# щит и кулак. Два танка стояли бы насмерть и не брали никого, два
# бойца брали бы каждого с первого раза.
#
# Классы разводят и экипировку: у патрульного щит и шокер, у сержанта
# дубинка. Поясов в комплекте два, а слот под пояс один — наручники
# носит тот, кто держит, кобуру тот, кто бьёт.
PAIR_CLASSES: tuple[str, ...] = ("tank", "warrior")

# Кто в каком карауле: по человеку на место в паре, и у каждой пары
# свои двое. Имя с номером — чтобы в журнале боя было видно, который
# из патрульных работает, а не «Патрульный против Патрульного»
OFFICER_NAMES: tuple[tuple[str, ...], ...] = (
    ("Патрульный 01", "Сержант 01"),
    ("Патрульный 02", "Сержант 02"),
)

# Лица: по файлу на каждого из четверых. Лежат рядом с прочими
# аватарами города, не в папке нанятых: городовых рисовали своим
# заходом, в форме VCPD
OFFICER_FACES: tuple[tuple[str, ...], ...] = (
    ("vcpd_patrolman_01", "vcpd_sergeant_01"),
    ("vcpd_patrolman_02", "vcpd_sergeant_02"),
)

# На сколько уровней патруль старше того, за кем пришёл. Пять — это
# разница, при которой ордер не шутка: прокачанный мафиози знает, что
# за ним придут всерьёз, а не постоят рядом
LEVELS_ABOVE = 5

# Что на них надето. Форма одна на всех (фуражка, китель, перчатки,
# штаны, ботинки), а руки и пояс разные: держит один, бьёт другой
COMMON_GEAR: tuple[str, ...] = (
    "vcpd_peaked_cap",
    "vcpd_tunic",
    "vcpd_gloves",
    "vcpd_uniform_trousers",
    "vcpd_duty_boots",
)
CLASS_GEAR: dict[str, tuple[str, ...]] = {
    # Патрульный держит: щит, шокер и наручники
    "tank": ("vcpd_riot_shield", "vcpd_stun_device", "vcpd_cuff_belt"),
    # Сержант работает: дубинка и кобура
    "warrior": ("vcpd_baton", "vcpd_holster_belt"),
}

# Сколько патруль стоит в одном доме. Двадцать минут — это и обозримое
# ожидание для того, кто хочет его переждать, и полный круг маршрута за
# два часа
SHIFT_SECONDS = 20 * 60

# Маршруты пар: общественные дома, где людно. Особняк мафии, больница,
# тюрьма, участок и жилые дома в них не входят — туда патруль не ходит
# ни по делу, ни мимо
ROUTES: tuple[tuple[str, ...], ...] = (
    # Центр: где гуляют деньги
    ("bar", "casino", "market", "fight_club", "bank", "restaurant"),
    # Окраина: где стоят машины и собирается шум
    (
        "gas_station",
        "car_wash",
        "used_car_market",
        "stadium",
        "pawnshop",
        "post_office",
    ),
)


def route_of(pair: int) -> tuple[str, ...]:
    """Маршрут этой пары. Пар две, и маршруты у них не пересекаются."""
    return ROUTES[pair % len(ROUTES)]


def where_now(pair: int, now: int) -> str:
    """Дом, в котором пара стоит в этот миг.

    Считается от времени, а не от обхода: сутки нарезаны сменами,
    смена — шаг по маршруту. Поэтому ответ один у всех, кто спросил, и
    перезапуск бота патруль не сдвигает.
    """
    route = route_of(pair)
    return route[(max(0, now) // SHIFT_SECONDS) % len(route)]


def patrols_now(now: int) -> dict[str, int]:
    """Где какая пара стоит сейчас: дом → номер пары.

    Маршруты не пересекаются, поэтому в одном доме пара всегда одна.
    """
    return {where_now(pair, now): pair for pair in range(PAIRS)}


def shift_left(now: int) -> int:
    """Сколько секунд пара ещё стоит в этом доме.

    По этому числу разыскиваемый решает, переждать или уходить: смена
    видна на экране часами, как смена на работе и сбор в рейд.
    """
    return SHIFT_SECONDS - (max(0, now) % SHIFT_SECONDS)


def officer_level(suspect_level: int) -> int:
    """Уровень патрульного: на пять выше того, за кем пришли.

    Патруль обязан быть сильнее разыскиваемого, иначе ордер становится
    украшением карточки: десятый уровень жил бы под ним безнаказанно и
    бил бы городовых как мишени. Пять уровней сверху — и городской
    потолок ему не предел: это служба, а не игрок, и лестница опыта на
    неё не распространяется.
    """
    return max(1 + LEVELS_ABOVE, suspect_level + LEVELS_ABOVE)


def officer_face(pair: int, number: int) -> str:
    """Лицо этого городового: у каждого из четверых своё."""
    from bot.game.art import patrol_avatar

    faces = OFFICER_FACES[pair % len(OFFICER_FACES)]
    return patrol_avatar(faces[number % len(faces)])


def officer_name(pair: int, number: int) -> str:
    """Как этого городового зовут в журнале боя и в списке дома."""
    names = OFFICER_NAMES[pair % len(OFFICER_NAMES)]
    return names[number % len(names)]


def _gear_for(fclass: str) -> dict[Slot, OwnedItem]:
    """Форма VCPD на этом патрульном: своя по классу плюс общая.

    Числа у вещей постоянные: комплект нарисован и посчитан под
    задержание, и подбирать его по уровню, как охране точки, не нужно —
    у городского патруля своя форма, а не лучшее с прилавка.
    """
    worn: dict[Slot, OwnedItem] = {}
    for code in CLASS_GEAR.get(fclass, ()) + COMMON_GEAR:
        item = CATALOGUE.get(code)
        if item is None:  # pragma: no cover - вещь убрали из каталога
            continue
        worn.setdefault(item.slot, OwnedItem(item=item, slot=item.slot))
    return worn


def _stats_for(level: int) -> Stats:
    """Характеристики патрульного: ровные, по уровню и без изысков."""
    return Stats(
        strength=4 + level,
        agility=2 + level // 2,
        intuition=2 + level // 2,
        endurance=6 + level,
    )


def officers(level: int, start: int = FIRST_ID, pair: int = 0) -> list[Fighter]:
    """Пара патрульных как бойцы боя — по уровню того, за кем пришли."""
    rank = officer_level(level)
    out: list[Fighter] = []
    for number in range(PAIR_SIZE):
        fclass = PAIR_CLASSES[number % len(PAIR_CLASSES)]
        out.append(
            Fighter(
                user_id=start - number,
                name=officer_name(pair, number),
                fclass=FIGHTER_CLASSES[fclass],
                stats=_stats_for(rank),
                level=rank,
                equipment=Equipment(items=_gear_for(fclass)),
                picture=officer_face(pair, number),
            )
        )
    return out


__all__ = [
    "CLASS_GEAR",
    "COMMON_GEAR",
    "FIRST_ID",
    "LEVELS_ABOVE",
    "OFFICERS",
    "OFFICER_FACES",
    "OFFICER_NAMES",
    "PAIRS",
    "PAIR_CLASSES",
    "PAIR_SIZE",
    "ROUTES",
    "SHIFT_SECONDS",
    "officer_face",
    "officer_level",
    "officer_name",
    "officers",
    "patrols_now",
    "route_of",
    "shift_left",
    "where_now",
]
