"""Награды за вход: сколько дней боец заглядывал в клуб за месяц.

Правила здесь, сама лестница наград — в `bot/content/daily.py`, как у
вещей и приёмов: её будут дополнять, и трогать для этого правила не нужно.

Считается не подряд, а всего: сколько разных дней за календарный месяц
боец открывал карточку. Пропустил вторник — счёт не сгорел, просто встал
на месте. Для MVP это честнее: серия, которая рушится от одного занятого
дня, заставляет заходить через силу, а не по желанию.

Сутки кончаются в полночь по Москве — там же, где кончаются сутки рейда,
и той же функцией. Месяц кончается вместе с календарным: первого числа
счёт обнуляется, и лестница начинается заново.

Награда не сгорает. Дошёл до третьего дня и не забрал — заберёшь на
пятый: невзятое копится и отдаётся разом. Иначе награда наказывала бы за
то, что человек зашёл и отвлёкся.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# Сутки клуба кончаются в полночь по Москве
MOSCOW = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class Reward:
    """Что дают за этот день: кредиты или склянка."""

    day: int  # какой по счёту вход в месяце
    title: str
    icon: str = "🎁"
    credits: int = 0
    potion: str = ""  # код эликсира
    note: str = ""

    @property
    def empty(self) -> bool:
        return not (self.credits or self.potion)


def club_day(moment: float) -> date:
    """Какой сегодня день по часам клуба."""
    return datetime.fromtimestamp(moment, MOSCOW).date()


def month_key(day: date) -> str:
    """Месяц, к которому день относится: по нему и обнуляется счёт."""
    return f"{day.year:04d}-{day.month:02d}"


def month_index(key: str) -> int:
    """Сквозной номер месяца — по нему выбирают, что менять каждый месяц."""
    year, month = key.split("-")
    return int(year) * 12 + int(month) - 1


def next_reset(moment: float) -> float:
    """Когда обновится счётчик входа: ближайшая полночь по Москве."""
    here = datetime.fromtimestamp(moment, MOSCOW)
    midnight = (here + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight.timestamp()


__all__ = [
    "MOSCOW",
    "Reward",
    "club_day",
    "month_index",
    "month_key",
    "next_reset",
]
