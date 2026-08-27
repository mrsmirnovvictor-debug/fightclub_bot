"""Боевой движок: раунд = одновременный размен ударами.

Каждый боец выбирает зону удара для каждого своего оружия и один блок.
Блок закрывает только смежные зоны: две обычно, три со щитом.

Выбор может быть неполным — что боец успел нажать, то и работает: не выбрал
зону удара, значит не бьёт; не выбрал блок, значит стоит открытым. Кто не
нажал ничего, пропускает ход целиком, а три пропуска подряд означают
техническое поражение.

Обе стороны считаются от состояния на начало раунда и применяются
одновременно, поэтому взаимный нокаут возможен и засчитывается как ничья.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from bot.game.classes import (
    ALL_ZONES,
    BLOCK_WIDTH,
    SHIELD_BLOCK_WIDTH,
    FighterClass,
    Stats,
    Zone,
    block_combo,
    block_combos,
    get_class,
)
from bot.game.equipment import BARE_HANDS, BARE_HANDS_ICON, Equipment
from bot.game.stats import (
    COUNTER_DAMAGE_MULT,
    MAX_ACCURACY,
    MAX_ANTICRIT,
    MAX_CRIT_CHANCE,
    DerivedStats,
    derive,
)

# После этого раунда бойцы начинают уставать и бьют всё больнее —
# чтобы дуэль не превращалась в бесконечное перетягивание блоков.
FATIGUE_FROM_ROUND = 6
FATIGUE_STEP = 0.12
# Жёсткий лимит: дальше судья останавливает бой и считает по здоровью.
MAX_ROUNDS = 20
# Столько пропусков подряд, и судья засчитывает техническое поражение.
MAX_MISSED_TURNS = 3
# Уворот нельзя сбить точностью в ноль: сколько бы ни было точности,
# у защищающегося остаётся эта надежда уйти с линии удара.
MIN_DODGE_CHANCE = 0.02
# И потолок сверху, чтобы бой не превращался в танцы вокруг трикстера
MAX_DODGE_CHANCE = 0.6
# Броня не может съесть больше половины удара: иначе комплект брони делает
# лёгкие классы безвредными, а бой — бесконечным.
MAX_ARMOR_SHARE = 0.5


class Outcome(str, Enum):
    SKIP = "skip"  # боец не выбрал зону удара
    BLOCK = "block"  # защитник закрыл зону
    DODGE = "dodge"  # ушёл с линии удара
    COUNTER = "counter"  # ушёл и ответил
    HIT = "hit"
    CRIT = "crit"


class DuelEnd(str, Enum):
    KO = "ko"  # кто-то упал
    DOUBLE_KO = "double_ko"  # упали оба, ничья
    JUDGE = "judge"  # лимит раундов, решение судьи
    TECHNICAL = "technical"  # пропустил слишком много ходов подряд


@dataclass(frozen=True)
class Action:
    """Выбор бойца на раунд: куда бьёт каждым оружием и чем закрылся."""

    attacks: tuple[Zone | None, ...] = (None,)
    block: tuple[Zone, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Боец не нажал вообще ничего — пропуск хода."""
        return not any(self.attacks) and not self.block

    def is_complete(self, weapons: int = 1) -> bool:
        chosen = [zone for zone in self.attacks[:weapons] if zone is not None]
        return len(chosen) == weapons and bool(self.block)


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
    equipment: Equipment = field(default_factory=Equipment)
    derived: DerivedStats = field(init=False)

    def __post_init__(self) -> None:
        self.derived = derive(
            self.fclass, self.stats, self.level, self.equipment.hp_bonus
        )
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

    @property
    def has_shield(self) -> bool:
        return self.equipment.has_shield

    @property
    def accuracy(self) -> float:
        """Точность: ловкость плюс проценты с вещей. Сбивает чужой уворот."""
        return min(MAX_ACCURACY, self.derived.accuracy + self.equipment.accuracy)

    @property
    def anticrit(self) -> float:
        """Антикрит: интуиция плюс проценты с вещей. Сбивает чужой крит."""
        return min(MAX_ANTICRIT, self.derived.anticrit + self.equipment.anticrit)

    @property
    def resist(self) -> float:
        """Сопротивление урону от выносливости: доля, которую снимает с удара."""
        return self.derived.resist

    @property
    def penetration(self) -> float:
        """Пробивание от ловкости: срезает чужое сопротивление."""
        return self.derived.penetration

    def resist_against(self, attacker: "Fighter") -> float:
        """Сколько удастся снять с удара этого соперника: резист минус пробой."""
        return max(0.0, self.resist - attacker.penetration)

    @property
    def dodge(self) -> float:
        """Уворот: ловкость плюс проценты с вещей."""
        return min(MAX_DODGE_CHANCE, self.derived.dodge_chance + self.equipment.dodge)

    @property
    def crit(self) -> float:
        """Шанс крита: интуиция плюс проценты с вещей."""
        return min(MAX_CRIT_CHANCE, self.derived.crit_chance + self.equipment.crit)

    def dodge_against(self, attacker: "Fighter") -> float:
        """Шанс увернуться от этого соперника: свой уворот минус его точность."""
        return min(
            MAX_DODGE_CHANCE, max(MIN_DODGE_CHANCE, self.dodge - attacker.accuracy)
        )

    def crit_against(self, defender: "Fighter") -> float:
        """Шанс крита по этому сопернику: свой крит минус его антикрит."""
        return max(0.0, self.crit - defender.anticrit)

    def armor_range(self, zone: Zone) -> tuple[int, int]:
        return self.equipment.armor_range(zone)

    @property
    def weapons(self) -> tuple[str, ...]:
        """Чем бьёт: одно название на каждое оружие, без оружия — кулаком."""
        return self.equipment.weapon_names or (BARE_HANDS,)

    @property
    def weapon_icons(self) -> tuple[str, ...]:
        return self.equipment.weapon_icons or (BARE_HANDS_ICON,)

    @property
    def attacks_per_round(self) -> int:
        return len(self.weapons)

    @property
    def block_width(self) -> int:
        """Сколько смежных зон закрывает блок.

        В кулачном бою у всех одинаково — две. Класс на это не влияет,
        разница между классами живёт в характеристиках. Расширить блок
        может только щит.
        """
        return SHIELD_BLOCK_WIDTH if self.has_shield else BLOCK_WIDTH

    def block_options(self) -> tuple[tuple[Zone, ...], ...]:
        return block_combos(self.block_width)

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
            equipment=player.equipment,
        )


@dataclass
class Strike:
    """Результат одного удара."""

    attacker_id: int
    defender_id: int
    zone: Zone | None
    outcome: Outcome
    weapon: str = BARE_HANDS
    damage: int = 0
    counter_damage: int = 0
    by_shield: bool = False  # блок принят щитом
    armor: int = 0  # сколько сняла броня зоны
    absorbed: int = 0  # сколько всего съели выносливость и броня
    missed_turn: bool = False  # боец не нажал вообще ничего
    defender_hp_after: int = 0
    attacker_hp_after: int = 0


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


def random_action(fighter: Fighter, rng: random.Random | None = None) -> Action:
    """Полный случайный выбор — для симуляций и тестов."""
    rng = rng or random
    attacks = tuple(
        rng.choice(ALL_ZONES) for _ in range(fighter.attacks_per_round)
    )
    return Action(attacks=attacks, block=rng.choice(fighter.block_options()))


def validate_action(action: Action, fighter: Fighter) -> None:
    """Неполный выбор допустим, лишние удары и чужие блоки — нет."""
    if len(action.attacks) > fighter.attacks_per_round:
        raise ValueError(
            f"У бойца {fighter.attacks_per_round} удара за раунд, "
            f"а выбрано {len(action.attacks)}"
        )
    if action.block and action.block not in fighter.block_options():
        raise ValueError(
            f"Блок должен закрывать {fighter.block_width} смежные зоны"
        )


def _resolve_strike(
    attacker: Fighter,
    defender: Fighter,
    zone: Zone | None,
    weapon: str,
    weapon_index: int,
    defender_action: Action,
    round_number: int,
    rng: random.Random,
    missed_turn: bool = False,
) -> Strike:
    strike = Strike(
        attacker_id=attacker.user_id,
        defender_id=defender.user_id,
        zone=zone,
        outcome=Outcome.SKIP,
        weapon=weapon,
        missed_turn=missed_turn,
    )
    if zone is None:
        return strike

    if zone in defender_action.block:
        strike.outcome = Outcome.BLOCK
        strike.by_shield = defender.has_shield
        return strike

    if rng.random() < defender.dodge_against(attacker):
        strike.outcome = Outcome.DODGE
        if rng.random() < defender.derived.counter_chance:
            counter = _roll_damage(defender, 0, round_number, rng) * COUNTER_DAMAGE_MULT
            # Контрудар прилетает не в выбранную зону, поэтому броню не трогает —
            # только сопротивление от выносливости.
            counter *= 1.0 - attacker.resist_against(defender)
            strike.counter_damage = max(1, int(round(counter)))
            strike.outcome = Outcome.COUNTER
        return strike

    damage = _roll_damage(attacker, weapon_index, round_number, rng)
    if rng.random() < attacker.crit_against(defender):
        strike.outcome = Outcome.CRIT
        damage *= attacker.derived.crit_power
    else:
        strike.outcome = Outcome.HIT

    # Удар доходит через выносливость и броню той зоны, куда пришёлся
    before = damage
    damage *= 1.0 - defender.resist_against(attacker)
    armor = min(defender.equipment.roll_armor(zone, rng), damage * MAX_ARMOR_SHARE)
    strike.armor = int(round(armor))
    damage -= armor
    strike.damage = max(1, int(round(damage)))
    strike.absorbed = max(0, int(round(before)) - strike.damage)
    return strike


def _roll_damage(
    fighter: Fighter, weapon_index: int, round_number: int, rng: random.Random
) -> float:
    """Урон от силы плюс урон оружия, всё вместе растёт от усталости.

    Класс влияет и на оружие: одну и ту же биту воин проворачивает лучше,
    чем танк. Иначе плоский урон оружия стирал бы разницу между классами —
    у того, кто бьёт слабо, прибавка весит вдвое больше.
    """
    raw = rng.randint(fighter.derived.damage_min, fighter.derived.damage_max)
    weapon = fighter.equipment.roll_weapon_damage(weapon_index, rng)
    raw += weapon * fighter.fclass.damage_mult
    return raw * fatigue_multiplier(round_number)


def _strikes_of(
    attacker: Fighter,
    defender: Fighter,
    action: Action,
    defender_action: Action,
    round_number: int,
    rng: random.Random,
) -> list[Strike]:
    """Все удары одного бойца за раунд — по одному на оружие."""
    strikes: list[Strike] = []
    weapons = attacker.weapons
    zones = list(action.attacks) + [None] * (len(weapons) - len(action.attacks))
    for index, weapon in enumerate(weapons):
        strikes.append(
            _resolve_strike(
                attacker,
                defender,
                zones[index],
                weapon,
                index,
                defender_action,
                round_number,
                rng,
                missed_turn=action.is_empty and index == 0,
            )
        )
    # Пропуск хода показываем одной строкой, а не по разу на каждое оружие
    if action.is_empty:
        return strikes[:1]
    return strikes


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

    strikes = _strikes_of(first, second, first_action, second_action, round_number, rng)
    strikes += _strikes_of(second, first, second_action, first_action, round_number, rng)

    # Урон всех ударов считается от состояния на начало раунда
    damage_taken = {first.user_id: 0, second.user_id: 0}
    for strike in strikes:
        damage_taken[strike.defender_id] += strike.damage
        damage_taken[strike.attacker_id] += strike.counter_damage

    fighters = {first.user_id: first, second.user_id: second}
    for user_id, damage in damage_taken.items():
        fighter = fighters[user_id]
        fighter.hp = max(0, fighter.hp - damage)
    first.damage_dealt += damage_taken[second.user_id]
    second.damage_dealt += damage_taken[first.user_id]

    _fill_running_hp(strikes, fighters)

    result = RoundResult(
        number=round_number,
        strikes=strikes,
        hp_after={first.user_id: first.hp, second.user_id: second.hp},
    )
    _apply_ending(result, first, second)
    return result


def _fill_running_hp(strikes: list[Strike], fighters: dict[int, Fighter]) -> None:
    """Проставить остаток здоровья на момент каждого удара — для рассказа судьи."""
    running = {
        user_id: fighter.hp + sum(
            strike.damage
            for strike in strikes
            if strike.defender_id == user_id
        ) + sum(
            strike.counter_damage
            for strike in strikes
            if strike.attacker_id == user_id
        )
        for user_id, fighter in fighters.items()
    }
    for strike in strikes:
        running[strike.defender_id] = max(0, running[strike.defender_id] - strike.damage)
        running[strike.attacker_id] = max(
            0, running[strike.attacker_id] - strike.counter_damage
        )
        strike.defender_hp_after = running[strike.defender_id]
        strike.attacker_hp_after = running[strike.attacker_id]


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


__all__ = [
    "MAX_MISSED_TURNS",
    "MAX_ROUNDS",
    "Action",
    "DuelEnd",
    "Fighter",
    "Outcome",
    "RoundResult",
    "Strike",
    "block_combo",
    "fatigue_multiplier",
    "judge_decision",
    "random_action",
    "resolve_round",
    "validate_action",
]
