"""Недельный бонусный пулл: три дела у семьи и три у города.

Точки платят за то, что их держат, — это доход, и он идёт сам собой.
Бонус платит за другое: за то, сколько на неделе работали. Нападал,
брал точки, держал их — к понедельнику приходит общий счёт.

**Три дела, четыре ступени.** У каждого дела своя лестница: сделал
мало — сто, сделал много — полторы тысячи. Ступени считаются по нижней
границе: ровно десять нападений — это уже вторая ступень, а не первая.
Иначе граница между «1-10» и «10-20» означала бы, что на десятом
нападении премия куда-то девается.

**Три дела складываются.** Это пулл, а не выбор: закрыл все три по
верхней ступени — четыре с половиной тысячи за неделю. Бонус ничего не
отнимает у дохода с точек: его считают отдельно и платят отдельно.

**Неделя та же, что у работы и у жалованья.** От понедельника, девяти
утра по Москве, до следующего понедельника; в девять утра и платят.
Часов, которые ходили бы по базе в этот миг, в клубе нет — неделю
закрывает тот, кто пришёл посмотреть на счёт.

**Семье платят в сейф, городу — в карманы.** У семьи общий сейф, и
бонус за общее дело кладут туда. У города сейфа на раздачу нет:
начальник заранее говорит, как делить — поровну или по должностям, — и
в понедельник каждый получает свою часть сам.

Счёт ведёт `bot.bonus_service`, хранение — `bot.database`, экраны —
`bot.webapp.bonus`.
"""

from __future__ import annotations

from dataclasses import dataclass

HOUR = 60 * 60

# Мерки, по которым идёт счёт. Семейные и городские лежат рядом: это
# один журнал на две стороны, и разводить их по таблицам значило бы
# писать дважды одно и то же
RAIDS = "raids"  # нападений за неделю
SEIZED = "seized"  # захваченных точек
HELD = "held"  # секунд удержания точек
ARRESTS = "arrests"  # задержаний за нападения
FREED = "freed"  # точек, освобождённых от мафии
GUARDED = "guarded"  # секунд охраны точек

# Время копим в секундах, а показываем в часах: час — крупная мерка, и
# складывать по ней значило бы терять по полчаса на каждом взгляде
SECONDS = (HELD, GUARDED)


@dataclass(frozen=True)
class Step:
    """Ступень лестницы: от скольких и сколько за это."""

    need: int
    award: int


@dataclass(frozen=True)
class Bonus:
    """Дело недели: что считают, в чём и по какой лестнице платят."""

    code: str
    title: str
    note: str
    unit: str  # как называть сделанное: «нападений», «часов»
    steps: tuple[Step, ...]

    @property
    def top(self) -> int:
        """Сколько даёт верхняя ступень."""
        return self.steps[-1].award


# Лестница у всех одна: сто, пятьсот, тысяча, полторы. Разными делают
# не деньги, а то, сколько за них надо сделать
AWARDS = (100, 500, 1000, 1500)


def ladder(bounds: tuple[int, int, int, int]) -> tuple[Step, ...]:
    """Четыре ступени по четырём нижним границам."""
    return tuple(Step(need, award) for need, award in zip(bounds, AWARDS))


MAFIA_BONUSES: tuple[Bonus, ...] = (
    Bonus(
        RAIDS,
        "Нападения",
        "Ограбления, которые семья довела до драки.",
        "нападений",
        ladder((1, 10, 20, 30)),
    ),
    Bonus(
        SEIZED,
        "Захваты",
        "Точки, взятые боем или вошедшие в пустой дом.",
        "захватов",
        ladder((14, 28, 56, 112)),
    ),
    Bonus(
        HELD,
        "Удержание",
        "Часы, которые точки простояли за семьёй.",
        "часов",
        ladder((100, 200, 300, 600)),
    ),
)

POLICE_BONUSES: tuple[Bonus, ...] = (
    Bonus(
        ARRESTS,
        "Задержания",
        "Взятые преступники по делам о нападениях.",
        "задержаний",
        ladder((1, 5, 10, 15)),
    ),
    Bonus(
        FREED,
        "Освобождения",
        "Точки, снятые у мафии боем или без боя.",
        "освобождений",
        ladder((28, 56, 112, 224)),
    ),
    Bonus(
        GUARDED,
        "Охрана",
        "Часы, которые точки простояли за городом.",
        "часов",
        ladder((50, 100, 150, 300)),
    ),
)

BY_CODE: dict[str, Bonus] = {
    one.code: one for one in MAFIA_BONUSES + POLICE_BONUSES
}

# Сколько всего можно взять за неделю одной стороне
MAFIA_TOP = sum(one.top for one in MAFIA_BONUSES)
POLICE_TOP = sum(one.top for one in POLICE_BONUSES)


def get_bonus(code: str) -> Bonus | None:
    return BY_CODE.get(code)


def shown(bonus: Bonus, value: int) -> int:
    """Сделанное в том виде, в каком его читают: секунды — часами."""
    return value // HOUR if bonus.code in SECONDS else value


def step_of(bonus: Bonus, value: int) -> int:
    """Какая ступень закрыта: от нуля до четырёх."""
    done = shown(bonus, value)
    return sum(1 for one in bonus.steps if done >= one.need)


def award_for(bonus: Bonus, value: int) -> int:
    """Сколько эта сторона взяла по этому делу."""
    step = step_of(bonus, value)
    return bonus.steps[step - 1].award if step else 0


def next_step(bonus: Bonus, value: int) -> Step | None:
    """Следующая ступень. Пусто — взята верхняя."""
    step = step_of(bonus, value)
    return bonus.steps[step] if step < len(bonus.steps) else None


def left_to_next(bonus: Bonus, value: int) -> int:
    """Сколько осталось до следующей ступени. Ноль — выше некуда."""
    ahead = next_step(bonus, value)
    return max(0, ahead.need - shown(bonus, value)) if ahead else 0


def pool_for(bonuses: tuple[Bonus, ...], tally: dict[str, int]) -> int:
    """Весь пулл стороны за неделю: три дела складываются."""
    return sum(award_for(one, tally.get(one.code, 0)) for one in bonuses)


# ---------- как город делит свой пулл ----------
#
# Семье делить нечего: у неё общий сейф. У города сейфа на раздачу нет,
# и пулл расходится по людям — так, как заранее сказал начальник.
EVEN = "even"  # поровну на всех
BY_RANK = "rank"  # по должностям

SPLITS: tuple[str, ...] = (EVEN, BY_RANK)
SPLIT_TITLES: dict[str, str] = {
    EVEN: "Поровну",
    BY_RANK: "По должностям",
}
SPLIT_NOTES: dict[str, str] = {
    EVEN: "Каждому сотруднику одинаково, от начальника до патрульного.",
    BY_RANK: "Начальнику три доли, офицеру две, патрульному одна.",
}

# Доли должностей. Те же три ступени, что и у чинов семьи, и считаются
# они долями, а не деньгами: пулл разный от недели к неделе
RANK_WEIGHT: dict[str, int] = {
    "police_chief": 3,
    "police_officer": 2,
    "police_patrol": 1,
}


def weight_of(job_code: str) -> int:
    """Сколько долей у этой должности. Чужая работа — ни одной."""
    return RANK_WEIGHT.get(job_code, 0)


def split_pool(pot: int, weights: list[int]) -> list[int]:
    """Разложить пулл по долям. Остаток остаётся в казне участка.

    Делим вниз: лишние кредиты, которые не делятся на доли, оставляем
    службе, а не дарим первому в списке. Случайный получатель остатка
    был бы несправедливостью, которой никто не просил.
    """
    total = sum(weights)
    if pot <= 0 or total <= 0:
        return [0] * len(weights)
    return [pot * one // total for one in weights]


__all__ = [
    "ARRESTS",
    "AWARDS",
    "BY_RANK",
    "EVEN",
    "FREED",
    "GUARDED",
    "HELD",
    "HOUR",
    "MAFIA_BONUSES",
    "MAFIA_TOP",
    "POLICE_BONUSES",
    "POLICE_TOP",
    "RAIDS",
    "RANK_WEIGHT",
    "SECONDS",
    "SEIZED",
    "SPLITS",
    "SPLIT_NOTES",
    "SPLIT_TITLES",
    "Bonus",
    "Step",
    "award_for",
    "get_bonus",
    "ladder",
    "left_to_next",
    "next_step",
    "pool_for",
    "shown",
    "split_pool",
    "step_of",
    "weight_of",
]
