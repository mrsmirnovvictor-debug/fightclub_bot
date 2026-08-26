"""Производные боевые характеристики: здоровье, урон, шансы крита и уворота."""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.classes import FighterClass, Stats

# Базовый урон голыми кулаками
BASE_DAMAGE = 4.0
DAMAGE_PER_STRENGTH = 1.4
DAMAGE_SPREAD = 0.25  # разброс урона ±25%

BASE_CRIT_CHANCE = 0.03
CRIT_PER_INTUITION = 0.008
MAX_CRIT_CHANCE = 0.5
BASE_CRIT_POWER = 1.5

BASE_DODGE_CHANCE = 0.02
DODGE_PER_AGILITY = 0.007
MAX_DODGE_CHANCE = 0.45
# Интуиция атакующего «сбивает» уворот защищающегося
ACCURACY_PER_INTUITION = 0.004

BASE_COUNTER_CHANCE = 0.02
COUNTER_PER_INTUITION = 0.004
MAX_COUNTER_CHANCE = 0.5
COUNTER_DAMAGE_MULT = 0.5  # контрудар бьёт вполсилы

HP_PER_LEVEL = 5  # прибавка к здоровью за каждый уровень


@dataclass(frozen=True)
class DerivedStats:
    """Всё, что нужно боевому движку, посчитанное из класса, статов и уровня."""

    max_hp: int
    damage_min: int
    damage_max: int
    crit_chance: float
    crit_power: float
    dodge_chance: float
    counter_chance: float
    accuracy: float


def derive(
    fclass: FighterClass,
    stats: Stats,
    level: int = 1,
    extra_hp: int = 0,
) -> DerivedStats:
    """Боевые показатели. stats — уже с учётом экипировки, extra_hp — от неё же."""
    max_hp = int(
        fclass.hp_base
        + stats.endurance * fclass.hp_per_endurance
        + (level - 1) * HP_PER_LEVEL
        + max(0, extra_hp)
    )

    avg_damage = (BASE_DAMAGE + stats.strength * DAMAGE_PER_STRENGTH) * fclass.damage_mult
    damage_min = max(1, round(avg_damage * (1 - DAMAGE_SPREAD)))
    damage_max = max(damage_min + 1, round(avg_damage * (1 + DAMAGE_SPREAD)))

    crit_chance = min(
        MAX_CRIT_CHANCE,
        BASE_CRIT_CHANCE + stats.intuition * CRIT_PER_INTUITION + fclass.crit_bonus,
    )
    dodge_chance = min(
        MAX_DODGE_CHANCE,
        BASE_DODGE_CHANCE + stats.agility * DODGE_PER_AGILITY + fclass.dodge_bonus,
    )
    counter_chance = min(
        MAX_COUNTER_CHANCE,
        BASE_COUNTER_CHANCE
        + stats.intuition * COUNTER_PER_INTUITION
        + fclass.counter_bonus,
    )

    return DerivedStats(
        max_hp=max_hp,
        damage_min=damage_min,
        damage_max=damage_max,
        crit_chance=round(crit_chance, 4),
        crit_power=round(BASE_CRIT_POWER + fclass.crit_power_bonus, 3),
        dodge_chance=round(dodge_chance, 4),
        counter_chance=round(counter_chance, 4),
        accuracy=round(stats.intuition * ACCURACY_PER_INTUITION, 4),
    )
