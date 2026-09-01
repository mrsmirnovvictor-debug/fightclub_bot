"""Производные боевые характеристики: здоровье, урон, шансы крита и уворота."""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.classes import FighterClass, Stats

# Базовый урон голыми кулаками. Сила прибавляет к нему понемногу: иначе
# один-единственный стат решает бой, а уворот с критом остаются украшением.
BASE_DAMAGE = 10.5
DAMAGE_PER_STRENGTH = 0.62
DAMAGE_SPREAD = 0.25  # разброс урона ±25%

BASE_CRIT_CHANCE = 0.03
CRIT_PER_INTUITION = 0.0108
MAX_CRIT_CHANCE = 0.55
BASE_CRIT_POWER = 1.5
# Интуиция растит не только шанс крита, но и его силу. Шанс упирается в
# потолок, сила — нет, поэтому вкладываться в интуицию имеет смысл всегда.
CRIT_POWER_PER_INTUITION = 0.04

BASE_DODGE_CHANCE = 0.02
DODGE_PER_AGILITY = 0.01
MAX_DODGE_CHANCE = 0.55

# Три пары идут по кругу: ловкость бьёт выносливость, выносливость —
# интуицию, интуиция — ловкость. Отсюда камень-ножницы-бумага между
# трикстером, танком и ассасином, а сила остаётся вне круга — воин волен
# качаться ровно или уходить в крайности.
#
#   🤸 уворот      ← сбивает 🔮 точность
#   🔮 крит        ← сбивает 🫀 антикрит
#   🫀 сопротивление ← пробивает 🤸 ловкость

# Точность (антиуворот): интуиция угадывает, куда уйдёт соперник
ACCURACY_PER_INTUITION = 0.005
MAX_ACCURACY = 0.6

# Антикрит: выносливость терпит там, где другой сложился бы
ANTICRIT_PER_ENDURANCE = 0.011
MAX_ANTICRIT = 0.5

# Сопротивление урону: выносливость снимает долю с каждого пропущенного удара
RESIST_PER_ENDURANCE = 0.008
MAX_RESIST = 0.31

# Пробивание: ловкость находит щель в чужой обороне и срезает сопротивление
PENETRATION_PER_AGILITY = 0.008
MAX_PENETRATION = 0.5

# Контрудар — продолжение уворота, поэтому и растёт он от ловкости
BASE_COUNTER_CHANCE = 0.02
COUNTER_PER_AGILITY = 0.007
MAX_COUNTER_CHANCE = 0.5
COUNTER_DAMAGE_MULT = 0.85  # контрудар бьёт почти в полную силу

# Пробитие блока: крит, упёршийся в блок, всё равно может его проломить.
# Столько шансов у него без всякой защиты; устойчивость блока вычитается.
BLOCK_BREAK_CHANCE = 0.5
# Ниже этого шанс не опускается: наглухо от пробития не закрывается никто
MIN_BLOCK_BREAK = 0.05
# Пробитый блок гасит удар вполовину: проходит половина максимального урона
BLOCK_BREAK_DAMAGE_SHARE = 0.5
MAX_BLOCK_HOLD = 0.6

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
    penetration: float
    # Устойчивость блока под критом: доля, на которую падает шанс пробития
    block_hold: float = 0.0


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

    avg_damage = (
        BASE_DAMAGE + stats.strength * DAMAGE_PER_STRENGTH * fclass.damage_gain
    ) * fclass.damage_mult
    damage_min = max(1, round(avg_damage * (1 - DAMAGE_SPREAD)))
    damage_max = max(damage_min + 1, round(avg_damage * (1 + DAMAGE_SPREAD)))

    crit_chance = min(
        MAX_CRIT_CHANCE,
        BASE_CRIT_CHANCE
        + stats.intuition * CRIT_PER_INTUITION * fclass.crit_gain
        + fclass.crit_bonus,
    )
    dodge_chance = min(
        MAX_DODGE_CHANCE,
        BASE_DODGE_CHANCE
        + stats.agility * DODGE_PER_AGILITY * fclass.dodge_gain
        + fclass.dodge_bonus,
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
        anticrit=round(
            min(
                MAX_ANTICRIT,
                stats.endurance * ANTICRIT_PER_ENDURANCE + fclass.anticrit_bonus,
            ),
            4,
        ),
        dodge_chance=round(dodge_chance, 4),
        counter_chance=round(counter_chance, 4),
        accuracy=round(
            min(
                MAX_ACCURACY,
                stats.intuition * ACCURACY_PER_INTUITION + fclass.accuracy_bonus,
            ),
            4,
        ),
        resist=round(
            min(
                MAX_RESIST,
                stats.endurance * RESIST_PER_ENDURANCE * fclass.resist_gain,
            ),
            4,
        ),
        block_hold=round(min(MAX_BLOCK_HOLD, fclass.block_hold), 4),
        penetration=round(
            min(
                MAX_PENETRATION,
                stats.agility * PENETRATION_PER_AGILITY + fclass.penetration_bonus,
            ),
            4,
        ),
    )
