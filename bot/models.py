"""Модели данных, которые ходят между БД и игровой логикой."""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.classes import FighterClass, Stats, get_class
from bot.game.economy import (
    LEVEL_CREDITS,
    MAX_LEVEL,
    MICRO_UPS_PER_LEVEL,
    POINTS_PER_UP,
    RATING_START,
    UP_CREDITS,
    exp_per_up,
    exp_to_next_level,
    ups_earned,
)


@dataclass
class ProgressReport:
    """Что боец получил после боя: опыт, апы, уровни, кредиты."""

    exp: int = 0
    ups: int = 0
    levels: int = 0
    credits: int = 0
    points: int = 0
    rating_delta: int = 0
    capped: bool = False  # опыт пришёл на потолке уровня

    @property
    def is_empty(self) -> bool:
        return not (self.exp or self.credits or self.rating_delta)


@dataclass
class Player:
    user_id: int
    nickname: str
    class_code: str
    avatar: str = "🥊"
    avatar_file_id: str | None = None
    strength: int = 0
    agility: int = 0
    intuition: int = 0
    endurance: int = 0
    free_points: int = 0
    level: int = 1
    exp: int = 0  # опыт внутри текущего уровня
    total_exp: int = 0  # накоплено за всё время, растёт и после потолка
    micro_ups: int = 0  # промежуточных апов взято на текущем уровне
    credits: int = 0
    rating: int = RATING_START
    wins: int = 0
    losses: int = 0
    draws: int = 0

    @property
    def stats(self) -> Stats:
        return Stats(
            strength=self.strength,
            agility=self.agility,
            intuition=self.intuition,
            endurance=self.endurance,
        )

    @property
    def fclass(self) -> FighterClass:
        return get_class(self.class_code)

    @property
    def fights(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def exp_needed(self) -> int:
        return exp_to_next_level(self.level)

    @property
    def at_max_level(self) -> bool:
        return self.level >= MAX_LEVEL

    @property
    def exp_to_next_up(self) -> int:
        """Сколько опыта осталось до ближайшего апа (0 на потолке уровня)."""
        if self.at_max_level:
            return 0
        step = exp_per_up(self.level)
        target = min(self.exp_needed, step * (self.micro_ups + 1))
        return max(0, target - self.exp)

    @property
    def spent_points(self) -> int:
        """Сколько очков боец вложил сверх базы своего класса."""
        return self.stats.total() - self.fclass.base_stats.total()

    def apply_stats(self, stats: Stats) -> None:
        self.strength = stats.strength
        self.agility = stats.agility
        self.intuition = stats.intuition
        self.endurance = stats.endurance

    def reset_stats(self) -> int:
        """Снести распределённые очки обратно в свободные. Вернуть их число."""
        returned = max(0, self.spent_points)
        self.apply_stats(self.fclass.base_stats)
        self.free_points += returned
        return returned

    def switch_class(self, class_code: str) -> int:
        """Сменить класс: база нового класса, все вложенные очки возвращаются."""
        returned = max(0, self.spent_points)
        self.class_code = class_code
        self.apply_stats(get_class(class_code).base_stats)
        self.free_points += returned
        return returned

    def grant_exp(self, amount: int) -> ProgressReport:
        """Начислить опыт и выдать всё, что за ним следует.

        Апы приходят на каждой четверти уровня; четвёртый совпадает с самим
        уровнем. На потолке уровня опыт продолжает копиться в total_exp —
        под уровни, которые появятся позже.
        """
        report = ProgressReport(exp=max(0, amount))
        if report.exp == 0:
            return report
        self.total_exp += report.exp

        if self.at_max_level:
            report.capped = True
            return report

        self.exp += report.exp
        while not self.at_max_level and self.exp >= self.exp_needed:
            # Крупная победа может перескочить сразу и четверть, и уровень —
            # добираем апы, недополученные на уходящем уровне.
            while self.micro_ups < MICRO_UPS_PER_LEVEL - 1:
                self.micro_ups += 1
                self._take_up(report)
            self.exp -= self.exp_needed
            self.level += 1
            self.micro_ups = 0
            report.levels += 1
            self.credits += LEVEL_CREDITS
            report.credits += LEVEL_CREDITS
            self._take_up(report)  # четвёртый ап — сам уровень

        if self.at_max_level:
            self.exp = 0
            report.capped = True
            return report

        while self.micro_ups < ups_earned(self.exp, self.level):
            self.micro_ups += 1
            self._take_up(report)
        return report

    def _take_up(self, report: ProgressReport) -> None:
        self.free_points += POINTS_PER_UP
        self.credits += UP_CREDITS
        report.ups += 1
        report.points += POINTS_PER_UP
        report.credits += UP_CREDITS

    def grant_credits(self, amount: int) -> int:
        self.credits = max(0, self.credits + amount)
        return self.credits

    def can_afford(self, price: int) -> bool:
        return self.credits >= price

    def pay(self, price: int) -> None:
        if not self.can_afford(price):
            raise ValueError("Недостаточно кредитов")
        self.credits -= price

    def apply_rating(self, delta: int) -> int:
        self.rating = max(0, self.rating + delta)
        return self.rating


@dataclass
class Arena:
    """Ветка группы, в которой клуб проводит бои."""

    chat_id: int
    thread_id: int | None
    title: str = ""
