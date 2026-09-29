"""Тренажёрный зал: расписание, абонемент и прокачка характеристик.

Зал — единственное место в клубе, где характеристику растят не уровнем, а
временем. Приходят по расписанию, стоят пятнадцать минут, получают очко
прогресса; накопились очки — характеристика поднимается на единицу.

**Качают три характеристики из четырёх.** Силу, ловкость и интуицию —
каждую своей тренировкой. Выносливость в зал не идёт: она держит запас
здоровья, и отдать её на тот же станок значило бы продавать живучесть за
время сильнее, чем всё остальное.

**Расписание не хранится, а выводится.** Неделя разыгрывается по своему
номеру: у всех бойцов оно одно и то же, переживает перезапуск бота и не
стоит базе ни строки. Новая неделя — новый розыгрыш.

Внутри дня разыгрываются шесть слотов, по два на каждую тренировку.
Поровну — намеренно: иначе неделя могла выпасть так, что вечерами всю
неделю одна сила, и боец, который заходит только вечером, качал бы одно.

**Слот двухчасовой, тренировка пятнадцатиминутная.** Записаться можно,
пока до конца слота больше пятнадцати минут, — иначе тренировка не
успевала бы кончиться в свой слот.

**Одна тренировка на слот.** Этого не просили, но без этого в
двухчасовой слот влезает семь подходов, и зал из расписания превращается
в кнопку: шесть слотов в день дали бы под полсотни очков в сутки вместо
шести.

**Очки тратятся.** Каждое улучшение стоит свою цену — 3, 6, 12, 24 и 48
тренировок, — а не считается от начала времён. Всего на пять улучшений
одной характеристики уходит 93 тренировки.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bot.game.classes import Stat
from bot.game.clock import MOSCOW

# Сколько боец стоит на тренировке
TRAINING_MINUTES = 15
TRAINING_SECONDS = TRAINING_MINUTES * 60

# Слоты расписания: час начала, длина два часа
SLOT_HOURS: tuple[int, ...] = (8, 10, 12, 14, 16, 18)
SLOT_LENGTH_HOURS = 2
SLOT_SECONDS = SLOT_LENGTH_HOURS * 60 * 60

# Сколько слотов каждой тренировки в дне. Шесть слотов на три тренировки
SLOTS_PER_TRAINING = len(SLOT_HOURS) // 3

# На сколько дней вперёд зал показывает расписание
SCHEDULE_DAYS = 7

# Сколько тренировок стоит каждое следующее улучшение
UPGRADE_STEPS: tuple[int, ...] = (3, 6, 12, 24, 48)
MAX_UPGRADES = len(UPGRADE_STEPS)

# На сколько растёт характеристика за одно улучшение
UPGRADE_GAIN = 1


@dataclass(frozen=True)
class Training:
    """Вид тренировки: что качает и как называется на табло."""

    code: str
    title: str
    stat: Stat
    emoji: str
    note: str

    @property
    def gains(self) -> str:
        return f"{self.stat.emoji} {self.stat.title.capitalize()}"


TRAININGS: tuple[Training, ...] = (
    Training(
        "power",
        "Силовая тренировка",
        Stat.STRENGTH,
        "🏋️",
        "Железо, подходы и счёт вслух",
    ),
    Training(
        "cardio",
        "Кардио-тренинг",
        Stat.AGILITY,
        "🏃",
        "Дорожка, скакалка и работа ног",
    ),
    Training(
        "crossfit",
        "Кросс-фит",
        Stat.INTUITION,
        "🤸",
        "Круговая работа: думать приходится на ходу",
    ),
)

BY_CODE: dict[str, Training] = {one.code: one for one in TRAININGS}
BY_STAT: dict[Stat, Training] = {one.stat: one for one in TRAININGS}

# Какие характеристики качает зал — в порядке табло
GYM_STATS: tuple[Stat, ...] = tuple(one.stat for one in TRAININGS)


def get_training(code: str) -> Training | None:
    return BY_CODE.get(code)


def training_for(stat: Stat) -> Training | None:
    return BY_STAT.get(stat)


@dataclass(frozen=True)
class Pass:
    """Абонемент: на сколько и почём."""

    code: str
    title: str
    days: int
    price: int
    note: str


PASSES: tuple[Pass, ...] = (
    Pass("month", "Месяц", 30, 500, "Попробовать и втянуться"),
    Pass("half", "Полгода", 182, 2500, "Дешевле двух месяцев в пересчёте"),
    Pass("year", "Год", 365, 4000, "Цена восьми месяцев за двенадцать"),
)

PASSES_BY_CODE: dict[str, Pass] = {one.code: one for one in PASSES}


def get_pass(code: str) -> Pass | None:
    return PASSES_BY_CODE.get(code)


# ---------- расписание ----------


def moscow_day(moment: int) -> date:
    """Какой сегодня день по часам клуба."""
    return datetime.fromtimestamp(moment, MOSCOW).date()


def slot_start(day: date, hour: int) -> int:
    """Момент начала слота как метка времени."""
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=MOSCOW).timestamp())


@dataclass(frozen=True)
class Slot:
    """Одна клетка расписания: день, час и что в ней тренируют."""

    day: date
    hour: int
    training: Training

    @property
    def id(self) -> str:
        """Ключ слота. По нему и помнят, что боец в нём уже отработал."""
        return f"{self.day.isoformat()}:{self.hour:02d}"

    @property
    def starts(self) -> int:
        return slot_start(self.day, self.hour)

    @property
    def ends(self) -> int:
        return self.starts + SLOT_SECONDS

    @property
    def clock(self) -> str:
        return f"{self.hour:02d}:00–{self.hour + SLOT_LENGTH_HOURS:02d}:00"

    def is_open(self, now: int) -> bool:
        """Идёт ли слот прямо сейчас."""
        return self.starts <= now < self.ends

    def seconds_left(self, now: int) -> int:
        return max(0, self.ends - now)

    def takes_joiners(self, now: int) -> bool:
        """Можно ли записаться: слот идёт и успеет кончиться после тренировки."""
        return self.is_open(now) and self.seconds_left(now) > TRAINING_SECONDS


def week_of(day: date) -> tuple[int, int]:
    """Год и номер недели по ISO — зерно розыгрыша."""
    year, week, _ = day.isocalendar()
    return year, week


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_schedule(day: date) -> dict[date, tuple[Slot, ...]]:
    """Расписание недели, в которую попадает этот день.

    Розыгрыш идёт по номеру недели и всегда даёт одно и то же: расписание
    не хранится нигде, и всё же одинаково у всех бойцов и после
    перезапуска. Внутри дня — по два слота на каждую тренировку.
    """
    year, week = week_of(day)
    rng = random.Random(f"gym-{year}-{week}")
    monday = monday_of(day)
    schedule: dict[date, tuple[Slot, ...]] = {}
    for shift in range(7):
        today = monday + timedelta(days=shift)
        kinds = [one for one in TRAININGS for _ in range(SLOTS_PER_TRAINING)]
        rng.shuffle(kinds)
        schedule[today] = tuple(
            Slot(day=today, hour=hour, training=training)
            for hour, training in zip(SLOT_HOURS, kinds)
        )
    return schedule


def day_schedule(day: date) -> tuple[Slot, ...]:
    """Слоты одного дня."""
    return week_schedule(day).get(day, ())


def schedule_from(now: int, days: int = SCHEDULE_DAYS) -> list[Slot]:
    """Слоты на неделю вперёд, начиная с сегодняшнего дня.

    Прошедшие сегодняшние слоты остаются в списке: по ним видно, что зал
    работал с утра, и что расписание — не выдумка на ближайший час.
    """
    today = moscow_day(now)
    slots: list[Slot] = []
    for shift in range(days):
        slots.extend(day_schedule(today + timedelta(days=shift)))
    return slots


def slot_now(now: int) -> Slot | None:
    """Слот, который идёт прямо сейчас. None — зал закрыт."""
    for slot in day_schedule(moscow_day(now)):
        if slot.is_open(now):
            return slot
    return None


def next_slot(now: int) -> Slot | None:
    """Ближайший слот, который ещё не начался."""
    for slot in schedule_from(now):
        if slot.starts > now:
            return slot
    return None  # pragma: no cover - расписание бесконечно вперёд


# ---------- прогресс ----------


def price_of_upgrade(ups: int) -> int:
    """Сколько тренировок стоит следующее улучшение. 0 — улучшений больше нет."""
    return UPGRADE_STEPS[ups] if 0 <= ups < MAX_UPGRADES else 0


def can_upgrade(points: int, ups: int) -> bool:
    price = price_of_upgrade(ups)
    return bool(price) and points >= price


def total_for(ups: int) -> int:
    """Сколько тренировок уходит на столько улучшений подряд."""
    return sum(UPGRADE_STEPS[:ups])


__all__ = [
    "BY_CODE",
    "BY_STAT",
    "GYM_STATS",
    "MAX_UPGRADES",
    "PASSES",
    "SCHEDULE_DAYS",
    "SLOTS_PER_TRAINING",
    "SLOT_HOURS",
    "SLOT_LENGTH_HOURS",
    "SLOT_SECONDS",
    "TRAINING_MINUTES",
    "TRAINING_SECONDS",
    "TRAININGS",
    "UPGRADE_GAIN",
    "UPGRADE_STEPS",
    "Pass",
    "Slot",
    "Training",
    "can_upgrade",
    "day_schedule",
    "get_pass",
    "get_training",
    "monday_of",
    "moscow_day",
    "next_slot",
    "price_of_upgrade",
    "schedule_from",
    "slot_now",
    "slot_start",
    "total_for",
    "training_for",
    "week_of",
    "week_schedule",
]
