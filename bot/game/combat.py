"""Боевой движок: раунд = одновременный размен ударами.

Каждый боец выбирает одну зону атаки и несколько зон блока. Обе атаки
считаются от состояния на начало раунда и применяются одновременно, поэтому
взаимный нокаут возможен и считается ничьей.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from bot.game.classes import ALL_ZONES, FighterClass, Stats, Zone, get_class
from bot.game.stats import COUNTER_DAMAGE_MULT, DerivedStats, derive

# После этого раунда бойцы начинают уставать и бьют всё больнее —
# чтобы дуэль не превращалась в бесконечное перетягивание блоков.
FATIGUE_FROM_ROUND = 6
FATIGUE_STEP = 0.12
# Жёсткий лимит: дальше судья останавливает бой и считает по здоровью.
MAX_ROUNDS = 20


class Outcome(str, Enum):
    BLOCK = "block"  # защитник закрыл зону
    DODGE = "dodge"  # ушёл с линии удара
    HIT = "hit"
    CRIT = "crit"


class DuelEnd(str, Enum):
    KO = "ko"  # кто-то упал
    DOUBLE_KO = "double_ko"  # упали оба
    JUDGE = "judge"  # лимит раундов, решение судьи
    GIVE_UP = "give_up"  # сдался


@dataclass(frozen=True)
class Action:
    """Выбор бойца на раунд."""

    attack: Zone
    blocks: tuple[Zone, ...]
    auto: bool = False  # выбрано ботом по таймауту


@dataclass
class Fighter:
    """Боец внутри дуэли."""

    user_id: int
    name: str
    fclass: FighterClass
    stats: Stats
    level: int = 1
    hp: int = 0
    derived: DerivedStats = field(init=False)

    def __post_init__(self) -> None:
        self.derived = derive(self.fclass, self.stats, self.level)
        if self.hp <= 0:
            self.hp = self.derived.max_hp

    @property
    def max_hp(self) -> int:
        return self.derived.max_hp

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def hp_percent(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0.0

    @classmethod
    def from_player(cls, player) -> "Fighter":
        """Собрать бойца из записи игрока в БД."""
        return cls(
            user_id=player.user_id,
            name=player.nickname,
            fclass=get_class(player.class_code),
            stats=player.stats,
            level=player.level,
        )


@dataclass
class Strike:
    """Результат одного удара."""

    attacker_id: int
    defender_id: int
    zone: Zone
    outcome: Outcome
    damage: int = 0
    counter_damage: int = 0
    auto: bool = False


@dataclass
class RoundResult:
    number: int
    strikes: list[Strike]
    hp_after: dict[int, int]
    finished: bool = False
    winner_id: int | None = None
    end_reason: DuelEnd | None = None


def fatigue_multiplier(round_number: int) -> float:
    """Множитель урона за раунд: с какого-то момента бойцы «раскрываются»."""
    extra = max(0, round_number - FATIGUE_FROM_ROUND)
    return 1.0 + extra * FATIGUE_STEP


def random_action(fclass: FighterClass, rng: random.Random | None = None) -> Action:
    """Случайный выбор — за того, кто не успел определиться."""
    rng = rng or random
    zones = list(ALL_ZONES)
    attack = rng.choice(zones)
    blocks = tuple(rng.sample(zones, k=min(fclass.block_zones, len(zones))))
    return Action(attack=attack, blocks=blocks, auto=True)


def validate_action(action: Action, fclass: FighterClass) -> None:
    if len(set(action.blocks)) != fclass.block_zones:
        raise ValueError(
            f"{fclass.title} закрывает ровно {fclass.block_zones} зоны, "
            f"а выбрано {len(set(action.blocks))}"
        )


def _resolve_strike(
    attacker: Fighter,
    defender: Fighter,
    action: Action,
    defender_action: Action,
    round_number: int,
    rng: random.Random,
) -> Strike:
    zone = action.attack
    strike = Strike(
        attacker_id=attacker.user_id,
        defender_id=defender.user_id,
        zone=zone,
        outcome=Outcome.BLOCK,
        auto=action.auto,
    )

    if zone in defender_action.blocks:
        return strike

    dodge_chance = min(
        0.6, max(0.01, defender.derived.dodge_chance - attacker.derived.accuracy)
    )
    if rng.random() < dodge_chance:
        strike.outcome = Outcome.DODGE
        if rng.random() < defender.derived.counter_chance:
            counter = _roll_damage(defender, round_number, rng) * COUNTER_DAMAGE_MULT
            strike.counter_damage = max(1, int(round(counter)))
        return strike

    damage = _roll_damage(attacker, round_number, rng)
    if rng.random() < attacker.derived.crit_chance:
        strike.outcome = Outcome.CRIT
        damage *= attacker.derived.crit_power
    else:
        strike.outcome = Outcome.HIT
    strike.damage = max(1, int(round(damage)))
    return strike


def _roll_damage(fighter: Fighter, round_number: int, rng: random.Random) -> float:
    raw = rng.randint(fighter.derived.damage_min, fighter.derived.damage_max)
    return raw * fatigue_multiplier(round_number)


def resolve_round(
    first: Fighter,
    first_action: Action,
    second: Fighter,
    second_action: Action,
    round_number: int,
    rng: random.Random | None = None,
) -> RoundResult:
    """Посчитать раунд и применить урон. Меняет hp бойцов."""
    rng = rng or random

    strike_a = _resolve_strike(first, second, first_action, second_action, round_number, rng)
    strike_b = _resolve_strike(second, first, second_action, first_action, round_number, rng)

    # Урон обоих ударов считается от состояния на начало раунда
    damage_to_second = strike_a.damage + strike_b.counter_damage
    damage_to_first = strike_b.damage + strike_a.counter_damage

    first.hp = max(0, first.hp - damage_to_first)
    second.hp = max(0, second.hp - damage_to_second)

    result = RoundResult(
        number=round_number,
        strikes=[strike_a, strike_b],
        hp_after={first.user_id: first.hp, second.user_id: second.hp},
    )

    if not first.alive and not second.alive:
        result.finished = True
        result.end_reason = DuelEnd.DOUBLE_KO
        result.winner_id = initiative_winner(first, second, rng)
    elif not first.alive:
        result.finished = True
        result.winner_id = second.user_id
        result.end_reason = DuelEnd.KO
    elif not second.alive:
        result.finished = True
        result.winner_id = first.user_id
        result.end_reason = DuelEnd.KO
    elif round_number >= MAX_ROUNDS:
        result.finished = True
        result.end_reason = DuelEnd.JUDGE
        result.winner_id = judge_decision(first, second)

    return result


def initiative_winner(first: Fighter, second: Fighter, rng: random.Random) -> int:
    """Если рухнули оба — победил тот, чей удар прошёл первым.

    Скорость бойца — ловкость плюс интуиция; при равенстве решает жребий.
    """
    speed_first = first.stats.agility + first.stats.intuition
    speed_second = second.stats.agility + second.stats.intuition
    if speed_first > speed_second:
        return first.user_id
    if speed_second > speed_first:
        return second.user_id
    return rng.choice([first.user_id, second.user_id])


def judge_decision(first: Fighter, second: Fighter) -> int | None:
    """Решение судьи по остатку здоровья, если раунды кончились."""
    if first.hp_percent > second.hp_percent:
        return first.user_id
    if second.hp_percent > first.hp_percent:
        return second.user_id
    return None
