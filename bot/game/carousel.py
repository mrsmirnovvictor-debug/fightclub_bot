"""«Карусель удачи»: барабан с призами и шансы, обратные цене.

Отдельная игра казино, и устроена она не как стол: мест у неё нет, а
играть в неё могут сколько угодно человек разом — каждый крутит свой
барабан за свои жетоны.

Что в барабане: оружие и щиты седьмого, восьмого и девятого уровня с
витрины оружейной, три стопки жетонов и три машины из салона. Список
не выписан руками — он считается из тех же каталогов, по которым
торгуют лавка и салон. Иначе новая вещь на прилавке в барабан не
попала бы, а снятая с продажи осталась бы призом.

**Чем дороже приз, тем реже он выпадает.** Вес обратен цене, и это не
фигура речи: вес = `TOP_PRICE / цена`. У такого правила есть следствие,
которое стоит знать до того, как его заметят: каждый приз приносит
дому одинаково. Выигрыш в среднем — не «сколько-то от цены», а
среднее гармоническое цен, и считается оно ровно.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from bot.game.cars import get_car
from bot.game.equipment import ItemKind
from bot.content.items import shelf_of

# Что крутится в барабане: уровни оружия и щитов. Ниже седьмого в
# барабане делать нечего — такие вещи боец покупает не задумываясь, а
# выше девятого в лавке и не продают
PRIZE_LEVELS = (7, 8, 9)

# Стопки жетонов. Это единственный приз, который не надо ни забирать,
# ни продавать: жетоны и так жетоны
CHIP_PRIZES = (250, 500, 750)

# Машины из салона: по одной из трёх ценовых полок
CAR_PRIZES = ("city_sedan", "shadow_sport", "iron_muscle")

# Сколько стоит крутнуть
ROLL_PRICE = 250

# Сколько длится вращение
SPIN_SECONDS = 5

# Зерно, которым перемешана лента. Порядок постоянный: лента и так
# едет, а подмена товара на ходу выглядела бы прыжком
BELT_SEED = "карусель"

# Почём казино принимает выигрыш обратно. Половина — потому что в
# городе вещи назад не принимают вовсе, и цену сдачи пришлось
# назначить. Половина цены лавки: за полную карусель печатала бы
# деньги, за четверть продавать было бы незачем
SELL_SHARE = 2

# От этой цены считаются веса. Это цена самого дорогого приза.
TOP_PRICE = 20_000

# Во сколько раз веса крупнее целого. Без этого две самые дорогие
# машины — за шестнадцать и за двадцать тысяч — округлялись в один и
# тот же вес, и правило «чем дороже, тем реже» на них переставало
# работать ровно там, где оно всего заметнее
WEIGHT_SCALE = 10


@dataclass(frozen=True)
class Prize:
    """Приз барабана: что это, почём и как часто попадается."""

    kind: str  # item | chips | car
    code: str
    title: str
    price: int
    image: str = ""

    @property
    def weight(self) -> int:
        """Чем дороже, тем реже. Вес обратен цене и не меньше единицы."""
        return max(1, round(TOP_PRICE * WEIGHT_SCALE / max(1, self.price)))

    @property
    def sells_for(self) -> int:
        """Сколько жетонов дадут за него в кассе.

        Жетоны не продают: они и так жетоны, и начисляются сразу.
        """
        if self.kind == "chips":
            return self.price
        return self.price // SELL_SHARE


def _item_prizes() -> list[Prize]:
    """Оружие и щиты нужных уровней — те же, что на витрине оружейной."""
    out = []
    for item in shelf_of(""):
        if item.kind not in (ItemKind.WEAPON, ItemKind.SHIELD):
            continue
        if item.retired or item.level_required not in PRIZE_LEVELS:
            continue
        if not item.price:
            continue
        out.append(Prize("item", item.code, item.title, item.price,
                         item.picture))
    return out


def _car_prizes() -> list[Prize]:
    out = []
    for code in CAR_PRIZES:
        car = get_car(code)
        if car is None:  # pragma: no cover - салон переименовали
            continue
        out.append(Prize("car", car.code, car.title, car.price, car.image))
    return out


def _chip_prizes() -> list[Prize]:
    return [
        Prize("chips", f"chips{one}", f"{one} жетонов", one)
        for one in CHIP_PRIZES
    ]


def prizes() -> tuple[Prize, ...]:
    """Весь барабан. Считается один раз: каталоги не меняются на ходу."""
    global _POOL
    if _POOL is None:
        _POOL = tuple(_item_prizes() + _chip_prizes() + _car_prizes())
    return _POOL


_POOL: tuple[Prize, ...] | None = None


def by_code(code: str) -> Prize | None:
    for one in prizes():
        if one.code == code:
            return one
    return None


def draw(rng: random.Random) -> Prize:
    """Вытянуть приз. Шанс обратен цене."""
    pool = prizes()
    return rng.choices(pool, weights=[one.weight for one in pool])[0]


def chance(prize: Prize) -> float:
    """Доля этого приза среди всех вращений."""
    total = sum(one.weight for one in prizes())
    return prize.weight / total


def average_win() -> float:
    """Сколько приз стоит в среднем — по ценам лавки.

    При весах, обратных цене, это среднее гармоническое: каждый приз
    вносит в выигрыш поровну, сколько бы он ни стоил.
    """
    pool = prizes()
    total = sum(one.weight for one in pool)
    return sum(one.weight * one.price for one in pool) / total


def average_sale() -> float:
    """А сколько в среднем дадут за него жетонами, если сдать сразу."""
    pool = prizes()
    total = sum(one.weight for one in pool)
    return sum(one.weight * one.sells_for for one in pool) / total


def belt() -> tuple[Prize, ...]:
    """Лента барабана: весь барабан разом и в одном и том же порядке.

    Лента не меняется и от времени не зависит. Прежде она пересчиты­
    валась каждые пять секунд, и товар на ходу подменялся — со стороны
    это выглядело не каруселью, а прыжком через каждые пять секунд.
    Карусель и так едет; ей довольно того, что она едет.

    Порядок перемешан раз и навсегда, постоянным зерном: иначе дорогое
    стояло бы в барабане кучей, и половина ленты была бы машинами. А
    так по ней видно всё, что может выпасть, — лента для этого и нужна.
    """
    global _BELT
    if _BELT is None:
        pool = list(prizes())
        random.Random(BELT_SEED).shuffle(pool)
        _BELT = tuple(pool)
    return _BELT


_BELT: tuple[Prize, ...] | None = None


__all__ = [
    "BELT_SEED",
    "CAR_PRIZES",
    "CHIP_PRIZES",
    "PRIZE_LEVELS",
    "Prize",
    "ROLL_PRICE",
    "SELL_SHARE",
    "SPIN_SECONDS",
    "TOP_PRICE",
    "WEIGHT_SCALE",
    "average_sale",
    "average_win",
    "belt",
    "by_code",
    "chance",
    "draw",
    "prizes",
]
