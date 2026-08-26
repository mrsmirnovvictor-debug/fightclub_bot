"""Модели данных, которые ходят между БД и игровой логикой."""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.classes import POINTS_PER_LEVEL, FighterClass, Stats, get_class


def exp_to_next_level(level: int) -> int:
    """Сколько опыта нужно, чтобы уйти с текущего уровня на следующий."""
    return 100 + (level - 1) * 50


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
    exp: int = 0
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

    def grant_exp(self, amount: int) -> int:
        """Начислить опыт и вернуть количество полученных уровней."""
        self.exp += amount
        gained = 0
        while self.exp >= self.exp_needed:
            self.exp -= self.exp_needed
            self.level += 1
            self.free_points += POINTS_PER_LEVEL
            gained += 1
        return gained


@dataclass
class Arena:
    """Ветка группы, в которой клуб проводит бои."""

    chat_id: int
    thread_id: int | None
    title: str = ""
