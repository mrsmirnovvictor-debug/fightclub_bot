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

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date

from bot.game.clock import MOSCOW, club_day, next_midnight


@dataclass(frozen=True)
class Reward:
    """Что дают за этот день: кредиты, склянка или заточка.

    Три поля, а не одно, потому что в один день может сойтись сразу
    несколько подарков: последний день февраля — это и двадцать восьмой
    вход, и последний день месяца.
    """

    day: int  # какой по счёту вход в месяце
    title: str
    icon: str = "🎁"
    credits: int = 0
    potion: str = ""  # код эликсира или пропуска
    mod: str = ""  # код заточки или модификатора
    note: str = ""
    big: bool = False  # веха: в сетке её видно крупнее, чем будни

    @property
    def empty(self) -> bool:
        return not (self.credits or self.potion or self.mod)


def merge(first: Reward, second: Reward) -> Reward:
    """Два подарка, сошедшихся в один день, — одной наградой.

    Такое бывает раз в год: в феврале двадцать восьмой вход и последний
    день месяца — это один и тот же день. Отдавать что-то одно значит
    отнять у февраля либо веху, либо заточку.

    Первый — заглавный: его значок и его подпись достаются дню целиком.
    Звать поимённо оба подарка некуда, а клетка в календаре одна.
    """
    if first.empty:
        return replace(second, day=first.day or second.day)
    if second.empty:
        return first
    return Reward(
        day=first.day or second.day,
        title=f"{first.title} и {second.title.lower()}",
        icon=first.icon,
        credits=first.credits + second.credits,
        potion=first.potion or second.potion,
        mod=first.mod or second.mod,
        note=first.note or second.note,
        big=True,  # день, в котором сошлось двое, будней крупнее
    )


def days_in_month(month: str) -> int:
    """Сколько дней в этом месяце: столько же клеток и в календаре."""
    year, number = month.split("-")
    return monthrange(int(year), int(number))[1]


def month_key(day: date) -> str:
    """Месяц, к которому день относится: по нему и обнуляется счёт."""
    return f"{day.year:04d}-{day.month:02d}"


def month_index(key: str) -> int:
    """Сквозной номер месяца — по нему выбирают, что менять каждый месяц."""
    year, month = key.split("-")
    return int(year) * 12 + int(month) - 1


def next_reset(moment: float) -> float:
    """Когда обновится счётчик входа: ближайшая полночь по Москве."""
    return next_midnight(moment)


__all__ = [
    "MOSCOW",
    "Reward",
    "club_day",
    "days_in_month",
    "merge",
    "month_index",
    "month_key",
    "next_reset",
]
