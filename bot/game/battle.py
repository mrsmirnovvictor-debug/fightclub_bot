"""Правила боёв, где дерутся больше двух: команда на команду и все против всех.

Раунд разбивается на пары: каждый боец за ход дерётся с одним соперником, а
на следующем ходу соперник меняется. Кому пары не досталось (например, живых
с одной стороны больше), тот ход пропускает и остаётся при своём.

Сам размен ударами считает тот же движок, что и дуэли, — здесь только
раскладка бойцов по парам и подсчёт, кто ещё стоит на ногах.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.game.combat import Fighter

# Больше этого числа раундов бой не идёт: дальше судья считает по здоровью
MAX_BATTLE_ROUNDS = 25

# Сколько бойцов может собрать сторона и сколько всего — в мясорубку
MIN_TEAM_SIZE = 2
MAX_TEAM_SIZE = 5
MIN_ROYALE = 3
MAX_ROYALE = 8


class BattleKind(str, Enum):
    TEAM = "team"  # команда на команду
    ROYALE = "royale"  # каждый сам за себя

    @property
    def title(self) -> str:
        return "командный бой" if self is BattleKind.TEAM else "королевская битва"

    @property
    def emoji(self) -> str:
        return "🤝" if self is BattleKind.TEAM else "👑"

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


# В командном бою две стороны; в мясорубке каждый сам себе сторона
RED, BLUE = 0, 1
TEAM_NAMES = {RED: "🔴 Красные", BLUE: "🔵 Синие"}


def team_name(team: int) -> str:
    return TEAM_NAMES.get(team, f"боец {team}")


def alive_ids(fighters: dict[int, Fighter]) -> list[int]:
    return [user_id for user_id, fighter in fighters.items() if fighter.alive]


def pair_up(
    fighters: dict[int, Fighter],
    teams: dict[int, int],
    kind: BattleKind,
    round_number: int,
) -> tuple[list[tuple[int, int]], list[int]]:
    """Разложить живых по парам на этот раунд.

    Возвращает пары и тех, кому соперника не хватило. Со сменой раунда
    соперники сдвигаются, поэтому за бой каждый успевает подраться с разными.
    """
    alive = [user_id for user_id in fighters if fighters[user_id].alive]
    if kind is BattleKind.TEAM:
        return _pair_teams(alive, teams, round_number)
    return _pair_everyone(alive, round_number)


def _pair_teams(
    alive: list[int], teams: dict[int, int], round_number: int
) -> tuple[list[tuple[int, int]], list[int]]:
    red = [user_id for user_id in alive if teams.get(user_id) == RED]
    blue = [user_id for user_id in alive if teams.get(user_id) != RED]
    if not red or not blue:
        return [], alive

    # Меньшая сторона сдвигается каждый раунд — так соперник каждый ход новый
    # и за круг успеваешь подраться с каждым. Большая сторона сдвигается раз
    # в полный круг: тогда без пары остаётся то один, то другой. При равных
    # составах большую сторону не двигаем вовсе — иначе пары не вернутся к
    # исходным, а перетасуются заново.
    small, large = (blue, red) if len(blue) <= len(red) else (red, blue)
    cycle = len(small)
    small = _shift(small, (round_number - 1) % cycle)
    if len(large) > cycle:
        large = _shift(large, ((round_number - 1) // cycle) % len(large))

    pairs = [
        (first, second) if first in red else (second, first)
        for first, second in zip(large, small)
    ]
    paired = {user_id for pair in pairs for user_id in pair}
    return pairs, [user_id for user_id in alive if user_id not in paired]


def _shift(side: list[int], steps: int) -> list[int]:
    return side[steps:] + side[:steps]


def _pair_everyone(
    alive: list[int], round_number: int
) -> tuple[list[tuple[int, int]], list[int]]:
    if len(alive) < 2:
        return [], alive
    # На каждом раунде сдвигаем круг: пары перетасовываются, но без случайности
    order = _shift(alive, (round_number - 1) % len(alive))
    pairs = [(order[i], order[i + 1]) for i in range(0, len(order) - 1, 2)]
    paired = {user_id for pair in pairs for user_id in pair}
    return pairs, [user_id for user_id in alive if user_id not in paired]


@dataclass
class BattleOutcome:
    """Чем кончился бой."""

    finished: bool = False
    winners: tuple[int, ...] = ()  # кто победил: команда или последний живой
    winning_team: int | None = None
    draw: bool = False
    by_rounds: bool = False  # решение судьи по здоровью, а не нокаутом


def judge(
    fighters: dict[int, Fighter],
    teams: dict[int, int],
    kind: BattleKind,
    round_number: int,
) -> BattleOutcome:
    """Продолжается ли бой, а если нет — кто победил."""
    alive = alive_ids(fighters)

    if kind is BattleKind.TEAM:
        red = [user_id for user_id in alive if teams.get(user_id) == RED]
        blue = [user_id for user_id in alive if teams.get(user_id) != RED]
        if not red and not blue:
            return BattleOutcome(finished=True, draw=True)
        if not blue:
            return BattleOutcome(finished=True, winners=tuple(red), winning_team=RED)
        if not red:
            return BattleOutcome(finished=True, winners=tuple(blue), winning_team=BLUE)
        if round_number >= MAX_BATTLE_ROUNDS:
            return _judge_teams(fighters, red, blue)
        return BattleOutcome()

    if not alive:
        return BattleOutcome(finished=True, draw=True)
    if len(alive) == 1:
        return BattleOutcome(finished=True, winners=(alive[0],))
    if round_number >= MAX_BATTLE_ROUNDS:
        return _judge_royale(fighters, alive)
    return BattleOutcome()


def _health_share(fighters: dict[int, Fighter], side: list[int]) -> float:
    return sum(fighters[user_id].hp_percent for user_id in side) / max(1, len(side))


def _judge_teams(
    fighters: dict[int, Fighter], red: list[int], blue: list[int]
) -> BattleOutcome:
    red_share, blue_share = _health_share(fighters, red), _health_share(fighters, blue)
    if abs(red_share - blue_share) < 1e-9:
        return BattleOutcome(finished=True, draw=True, by_rounds=True)
    if red_share > blue_share:
        return BattleOutcome(
            finished=True, winners=tuple(red), winning_team=RED, by_rounds=True
        )
    return BattleOutcome(
        finished=True, winners=tuple(blue), winning_team=BLUE, by_rounds=True
    )


def _judge_royale(fighters: dict[int, Fighter], alive: list[int]) -> BattleOutcome:
    best = max(alive, key=lambda user_id: fighters[user_id].hp_percent)
    leaders = [
        user_id
        for user_id in alive
        if abs(fighters[user_id].hp_percent - fighters[best].hp_percent) < 1e-9
    ]
    if len(leaders) > 1:
        return BattleOutcome(finished=True, draw=True, by_rounds=True)
    return BattleOutcome(finished=True, winners=(best,), by_rounds=True)
