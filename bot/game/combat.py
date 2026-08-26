"""Боевой движок: раунд = одновременный размен ударами.

Каждый боец выбирает зону атаки и зоны блока. Выбор может быть неполным —
что боец успел нажать, то и работает: не выбрал зону удара, значит не бьёт;
закрыл одну зону из двух, значит вторая осталась открытой. Кто не нажал
ничего, пропускает ход целиком, а три пропуска подряд означают техническое
поражение.

Обе атаки считаются от состояния на начало раунда и применяются
одновременно, поэтому взаимный нокаут возможен и засчитывается как ничья.
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
# Столько пропусков подряд, и судья засчитывает техническое поражение.
MAX_MISSED_TURNS = 3


class Outcome(str, Enum):
    SKIP = "skip"  # боец не выбрал зону удара
    BLOCK = "block"  # защитник закрыл зону
    DODGE = "dodge"  # ушёл с линии удара
    HIT = "hit"
    CRIT = "crit"


class DuelEnd(str, Enum):
    KO = "ko"  # кто-то упал
    DOUBLE_KO = "double_ko"  # упали оба, ничья
    JUDGE = "judge"  # лимит раундов, решение судьи
    TECHNICAL = "technical"  # пропустил слишком много ходов подряд


@dataclass(frozen=True)
class Action:
    """Выбор бойца на раунд. Может быть неполным или пустым."""

    attack: Zone | None = None
    blocks: tuple[Zone, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Боец не нажал вообще ничего — пропуск хода."""
        return self.attack is None and not self.blocks

    def is_complete(self, fclass: FighterClass) -> bool:
        return self.attack is not None and len(set(self.blocks)) == fclass.block_zones


@dataclass
class Fighter:
    """Боец внутри дуэли."""

    user_id: int
    name: str
    fclass: FighterClass
    stats: Stats
    level: int = 1
    hp: int = 0
    missed_turns: int = 0  # пропусков подряд
    damage_dealt: int = 0  # всего нанесено за бой — от этого считается опыт
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

    @property
    def gave_up(self) -> bool:
        """Боец молчит столько ходов, что судья вправе остановить бой."""
        return self.missed_turns >= MAX_MISSED_TURNS

    @classmethod
    def from_player(cls, player) -> "Fighter":
        """Собрать бойца из записи игрока: здоровье — то, что успело затянуться."""
        return cls(
            user_id=player.user_id,
            name=player.nickname,
            fclass=get_class(player.class_code),
            stats=player.stats,
            level=player.level,
            hp=player.current_hp(),
        )


@dataclass
class Strike:
    """Результат одного удара."""

    attacker_id: int
    defender_id: int
    zone: Zone | None
    outcome: Outcome
    damage: int = 0
    counter_damage: int = 0
    missed_turn: bool = False  # боец не нажал вообще ничего


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
    """Полный случайный выбор — для симуляций и тестов."""
    rng = rng or random
    zones = list(ALL_ZONES)
    return Action(
        attack=rng.choice(zones),
        blocks=tuple(rng.sample(zones, k=min(fclass.block_zones, len(zones)))),
    )


def validate_action(action: Action, fclass: FighterClass) -> None:
    """Неполный выбор допустим, лишние зоны блока — нет."""
    if len(set(action.blocks)) > fclass.block_zones:
        raise ValueError(
            f"{fclass.title} закрывает не больше {fclass.block_zones} зон, "
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
    if action.attack is None:
        return Strike(
            attacker_id=attacker.user_id,
            defender_id=defender.user_id,
            zone=None,
            outcome=Outcome.SKIP,
            missed_turn=action.is_empty,
        )

    zone = action.attack
    strike = Strike(
        attacker_id=attacker.user_id,
        defender_id=defender.user_id,
        zone=zone,
        outcome=Outcome.BLOCK,
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
    """Посчитать раунд и применить урон. Меняет hp и счётчики пропусков."""
    rng = rng or random

    for fighter, action in ((first, first_action), (second, second_action)):
        if action.is_empty:
            fighter.missed_turns += 1
        else:
            fighter.missed_turns = 0

    strike_a = _resolve_strike(first, second, first_action, second_action, round_number, rng)
    strike_b = _resolve_strike(second, first, second_action, first_action, round_number, rng)

    # Урон обоих ударов считается от состояния на начало раунда
    damage_to_second = strike_a.damage + strike_b.counter_damage
    damage_to_first = strike_b.damage + strike_a.counter_damage

    first.hp = max(0, first.hp - damage_to_first)
    second.hp = max(0, second.hp - damage_to_second)
    first.damage_dealt += damage_to_second
    second.damage_dealt += damage_to_first

    result = RoundResult(
        number=round_number,
        strikes=[strike_a, strike_b],
        hp_after={first.user_id: first.hp, second.user_id: second.hp},
    )
    _apply_ending(result, first, second)
    return result


def _apply_ending(result: RoundResult, first: Fighter, second: Fighter) -> None:
    """Проставить исход боя, если раунд оказался последним."""
    if not first.alive and not second.alive:
        result.finished = True
        result.end_reason = DuelEnd.DOUBLE_KO  # добили друг друга — ничья
    elif not first.alive:
        result.finished = True
        result.winner_id = second.user_id
        result.end_reason = DuelEnd.KO
    elif not second.alive:
        result.finished = True
        result.winner_id = first.user_id
        result.end_reason = DuelEnd.KO
    elif first.gave_up or second.gave_up:
        result.finished = True
        result.end_reason = DuelEnd.TECHNICAL
        if first.gave_up and not second.gave_up:
            result.winner_id = second.user_id
        elif second.gave_up and not first.gave_up:
            result.winner_id = first.user_id
    elif result.number >= MAX_ROUNDS:
        result.finished = True
        result.end_reason = DuelEnd.JUDGE
        result.winner_id = judge_decision(first, second)


def judge_decision(first: Fighter, second: Fighter) -> int | None:
    """Решение судьи по остатку здоровья, если раунды кончились."""
    if first.hp_percent > second.hp_percent:
        return first.user_id
    if second.hp_percent > first.hp_percent:
        return second.user_id
    return None
