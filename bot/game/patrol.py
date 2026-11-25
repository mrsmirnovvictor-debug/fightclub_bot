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

# Классы пары: щит и кулак. Два танка стояли бы насмерть и не брали
# никого, два бойца брали бы каждого с первого раза
PAIR_CLASSES: tuple[str, ...] = ("tank", "warrior")

# Имена пары. У патрульных нет лиц и нет карточек: это пост, а не люди,
# как и городская охрана у точек
PAIR_NAMES: tuple[str, ...] = ("Патрульный", "Сержант")

PATROL_ART = "guard_4_tank"

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
    """Уровень патрульного: по уровню того, за кем пришли.

    Патруль не бывает слабее разыскиваемого — иначе десятый уровень
    жил бы под ордером безнаказанно, а ордер стал бы украшением
    карточки. И не бывает сильнее: берут двое, и этого достаточно.
    """
    return max(1, min(MAX_LEVEL, suspect_level))


def patrol_picture() -> str:
    """Лицо патруля. Одно на всех: это пост, а не человек."""
    from bot.game.art import npc_avatar

    return npc_avatar(PATROL_ART)


def _weapon_for(level: int, fclass: str) -> OwnedItem | None:
    """Оружие патрульного: лучшее с обычного прилавка по его уровню.

    Тем же способом одевается городская охрана: голыми руками на
    задержание не выходят, но и фанатской биты патрульному не выдают.
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
    """Характеристики патрульного: ровные, по уровню и без изысков."""
    return Stats(
        strength=4 + level,
        agility=2 + level // 2,
        intuition=2 + level // 2,
        endurance=6 + level,
    )


def officers(level: int, start: int = FIRST_ID) -> list[Fighter]:
    """Пара патрульных как бойцы боя — по уровню того, за кем пришли."""
    rank = officer_level(level)
    face = patrol_picture()
    out: list[Fighter] = []
    for number in range(PAIR_SIZE):
        fclass = PAIR_CLASSES[number % len(PAIR_CLASSES)]
        weapon = _weapon_for(rank, fclass)
        out.append(
            Fighter(
                user_id=start - number,
                name=PAIR_NAMES[number % len(PAIR_NAMES)],
                fclass=FIGHTER_CLASSES[fclass],
                stats=_stats_for(rank),
                level=rank,
                equipment=Equipment(
                    items={Slot.WEAPON: weapon} if weapon else {}
                ),
                picture=face,
            )
        )
    return out


__all__ = [
    "FIRST_ID",
    "OFFICERS",
    "PAIRS",
    "PAIR_CLASSES",
    "PAIR_NAMES",
    "PAIR_SIZE",
    "PATROL_ART",
    "ROUTES",
    "SHIFT_SECONDS",
    "officer_level",
    "officers",
    "patrol_picture",
    "patrols_now",
    "route_of",
    "shift_left",
    "where_now",
]
