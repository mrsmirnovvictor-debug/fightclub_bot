"""Производные боевые характеристики: здоровье, урон, шансы крита и уворота."""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.classes import FighterClass, Stats

# Базовый урон голыми кулаками. Сила прибавляет к нему понемногу: иначе
# один-единственный стат решает бой, а уворот с критом остаются украшением.
BASE_DAMAGE = 8.5
DAMAGE_PER_STRENGTH = 0.78
DAMAGE_SPREAD = 0.25  # разброс урона ±25%

BASE_CRIT_CHANCE = 0.03
CRIT_PER_INTUITION = 0.008
MAX_CRIT_CHANCE = 0.55
BASE_CRIT_POWER = 1.5
# Интуиция растит не только шанс крита, но и его силу. Шанс упирается в
# потолок, сила — нет, поэтому вкладываться в интуицию имеет смысл всегда.
CRIT_POWER_PER_INTUITION = 0.04

BASE_DODGE_CHANCE = 0.02
DODGE_PER_AGILITY = 0.007
MAX_DODGE_CHANCE = 0.55

# Точность — обратная сторона уворота, и отвечает за неё та же ловкость.
# За очко ловкости точности дают меньше, чем уворота, поэтому при равной
# ловкости уворот всегда чуть впереди: шанс уйти с линии удара есть всегда.
ACCURACY_PER_AGILITY = 0.005
MAX_ACCURACY = 0.6

# Антикрит — обратная сторона крита, и отвечает за него та же интуиция.
# За очко интуиции крита дают чуть больше, чем антикрита.
ANTICRIT_PER_INTUITION = 0.006
MAX_ANTICRIT = 0.5

# Сопротивление урону: выносливость снимает долю с каждого пропущенного удара
RESIST_PER_ENDURANCE = 0.005
MAX_RESIST = 0.25

# Контрудар — продолжение уворота, поэтому и растёт он от ловкости
BASE_COUNTER_CHANCE = 0.02
COUNTER_PER_AGILITY = 0.007
MAX_COUNTER_CHANCE = 0.5
COUNTER_DAMAGE_MULT = 0.85  # контрудар бьёт почти в полную силу

HP_PER_LEVEL = 5  # прибавка к здоровью за каждый уровень


@dataclass(frozen=True)
class DerivedStats:
    """Всё, что нужно боевому движку, посчитанное из класса, статов и уровня."""

    max_hp: int
    damage_min: int
    damage_max: int
    crit_chance: float
    crit_power: float
    anticrit: float
    dodge_chance: float
    counter_chance: float
    accuracy: float
    resist: float


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
        BASE_COUNTER_CHANCE + stats.agility * COUNTER_PER_AGILITY + fclass.counter_bonus,
    )

    return DerivedStats(
        max_hp=max_hp,
        damage_min=damage_min,
        damage_max=damage_max,
        crit_chance=round(crit_chance, 4),
        crit_power=round(
            BASE_CRIT_POWER
            + fclass.crit_power_bonus
            + stats.intuition * CRIT_POWER_PER_INTUITION,
            3,
        ),
        anticrit=round(min(MAX_ANTICRIT, stats.intuition * ANTICRIT_PER_INTUITION), 4),
        dodge_chance=round(dodge_chance, 4),
        counter_chance=round(counter_chance, 4),
        accuracy=round(min(MAX_ACCURACY, stats.agility * ACCURACY_PER_AGILITY), 4),
        resist=round(min(MAX_RESIST, stats.endurance * RESIST_PER_ENDURANCE), 4),
    )
