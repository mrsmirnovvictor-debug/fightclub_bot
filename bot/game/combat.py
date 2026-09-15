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
from bot.game.abilities import (
    MAX_ENERGY,
    MAX_PER_TURN,
    Ability,
    Charge,
    Effect,
    Loadout,
    Source,
    energy_gain,
)
from bot.game.equipment import BARE_HANDS, BARE_HANDS_ICON, Equipment
from bot.game.stats import (
    NO_LIMITS,
    BLOCK_BREAK_CHANCE,
    BLOCK_BREAK_DAMAGE_SHARE,
    COUNTER_DAMAGE_MULT,
    MAX_ACCURACY_TOTAL,
    MAX_ANTICRIT_TOTAL,
    MAX_BLOCK_HOLD,
    MAX_COUNTER_CHANCE,
    MAX_CRIT_TOTAL,
    MAX_DODGE_TOTAL,
    MIN_BLOCK_BREAK,
    DerivedStats,
    derive,
)

# С трети боя бойцы начинают уставать и бьют всё больнее — чтобы дуэль не
# превращалась в бесконечное перетягивание блоков. Кривая не абсолютная, а
# растянутая на длину боя: к финальному гонгу удар тяжелее ровно вот на
# столько, а где именно начнётся разгон, считается от лимита ходов. Иначе
# длинный бой к последнему ходу выбивал бы втрое — ту же беду мы уже ловили
# в рейде, когда усталость считали по ударам отряда, а не по волнам.
FATIGUE_TOP = 1.44
# Доля боя, которую бойцы держатся ровно
FATIGUE_CALM_SHARE = 3

# Бой идёт по-боксёрски: ходы собраны в раунды, между раундами перерыв.
# Три хода на раунд — это и есть те самые три минуты, за которые в боксе
# успевают размяться и устать, а заодно ровно столько сообщений, сколько
# Telegram разрешает сказать в группу без пауз посреди боя.
TURNS_PER_ROUND = 3
# Кулачный бой короткий: он идёт в ветке группы, где Telegram считает каждое
# сообщение, и панель у всех одинаковая — растягивать там нечего.
MATCH_ROUNDS = 6
# Бой с оружием, групповой и рейд идут в карточке: сообщений там нет, зато
# есть снаряжение и способности, которым нужно время, чтобы себя показать.
LONG_ROUNDS = 9
# Жёсткий лимит кулачного боя: восемнадцать ходов, дальше решение судьи.
MAX_TURNS = TURNS_PER_ROUND * MATCH_ROUNDS
LONG_TURNS = TURNS_PER_ROUND * LONG_ROUNDS
# Прежнее имя того же числа — на него смотрит справка и групповой бой
MAX_ROUNDS = MAX_TURNS
# Столько пропусков подряд, и судья засчитывает техническое поражение.
MAX_MISSED_TURNS = 3
# Уворот нельзя сбить точностью в ноль: сколько бы ни было точности,
# у защищающегося остаётся эта надежда уйти с линии удара.
MIN_DODGE_CHANCE = 0.02
# И потолок сверху, чтобы бой не превращался в танцы вокруг трикстера.
# Снимается он тем же выключателем, что и остальные, — NO_LIMITS.
MAX_DODGE_CHANCE = 1.0 if NO_LIMITS else 0.7
# Броня не может съесть больше этой доли удара: иначе комплект брони делает
# лёгкие классы безвредными, а бой — бесконечным.
MAX_ARMOR_SHARE = 0.6


class Outcome(str, Enum):
    SKIP = "skip"  # боец не выбрал зону удара
    BLOCK = "block"  # защитник закрыл зону
    BREAK = "break"  # крит проломил блок
    DODGE = "dodge"  # ушёл с линии удара
    COUNTER = "counter"  # ушёл и ответил
    HIT = "hit"
    CRIT = "crit"


# Какой приём сильнее внутри своей ветки. Нажатых за ход может быть
# несколько, и тогда решает сильнейший — слабые не пропадают даром, они
# просто уступают место
_CRIT_RANK: dict[Effect, int] = {Effect.CRIT: 0, Effect.BREAK: 1, Effect.DOUBLE: 2}


def _DODGE_RANK(ability: Ability) -> int:
    """Уворот с критическим ответом сильнее ответа, ответ — простого ухода."""
    return (1 if ability.counter else 0) + (1 if ability.crit_counter else 0)


class DuelEnd(str, Enum):
    KO = "ko"  # кто-то упал
    DOUBLE_KO = "double_ko"  # упали оба, ничья
    JUDGE = "judge"  # лимит раундов, решение судьи
    TECHNICAL = "technical"  # пропустил слишком много ходов подряд


@dataclass(frozen=True)
class Action:
    """Выбор бойца на ход: куда бьёт каждой рукой и чем закрылся.

    Ударов столько, сколько рук с оружием: одна — один, второе оружие во
    второй руке — два. Блок один на все руки, шириной в две зоны, а со
    щитом — в три.
    """

    attacks: tuple[Zone | None, ...] = ()
    block: tuple[Zone, ...] = ()

    def __post_init__(self) -> None:
        # Одиночный удар пишут и кортежем, и голой зоной: приводим к кортежу
        if isinstance(self.attacks, Zone) or self.attacks is None:
            object.__setattr__(self, "attacks", (self.attacks,))

    @property
    def attack(self) -> Zone | None:
        """Удар основной руки — тот, что был единственным до второй руки."""
        return self.attacks[0] if self.attacks else None

    @property
    def is_empty(self) -> bool:
        """Боец не нажал вообще ничего — пропуск хода."""
        return not any(self.attacks) and not self.block

    def is_complete(self, weapons: int = 1) -> bool:
        chosen = [zone for zone in self.attacks[:weapons] if zone is not None]
        return len(chosen) == weapons and bool(self.block)


# ---------- итоговые доли: своё плюс вещи ----------
#
# Потолки здесь свои, не те, что внутри derive(): там прижимается то, что
# боец набрал характеристиками, а тут — вместе с надетым. Считать это должны
# и карточка, и профиль, и сам ринг, поэтому арифметика лежит в одном месте:
# стоит её продублировать, и карточка начинает обещать одно, а бой — другое.


def total_accuracy(base: float, gear: float) -> float:
    return min(MAX_ACCURACY_TOTAL, base + gear)


def total_anticrit(base: float, gear: float) -> float:
    return min(MAX_ANTICRIT_TOTAL, base + gear)


def total_dodge(base: float, gear: float) -> float:
    return min(MAX_DODGE_TOTAL, base + gear)


def total_crit(base: float, gear: float) -> float:
    return min(MAX_CRIT_TOTAL, base + gear)


def total_counter(base: float, gear: float) -> float:
    return min(MAX_COUNTER_CHANCE, base + gear)


def total_block_hold(base: float, gear: float = 0.0) -> float:
    return min(MAX_BLOCK_HOLD, base + gear)


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
    # Запас здоровья сверх своего: от вещей и от выпитых эликсиров
    extra_hp: int = 0
    # Подписчик: значок у имени судья ставит по этому полю
    pro: bool = False
    # Приёмы: что выучено, сколько энергии накоплено и что уже нажато.
    # Энергия живёт только внутри боя — в базу она не уходит
    loadout: Loadout = field(default_factory=Loadout)
    # Шкала одна, а кормится тем, в чём силён класс — см. ENERGY_SOURCES
    energy: int = 0
    charges: list[Charge] = field(default_factory=list)
    # Сколько приёмов пущено в дело в этом ходу: больше трёх не дают
    pressed: int = 0
    derived: DerivedStats = field(init=False)

    def __post_init__(self) -> None:
        self.derived = derive(
            self.fclass, self.stats, self.level, self.equipment.hp_bonus + self.extra_hp
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
    def accuracy(self) -> float:
        """Точность: ловкость плюс проценты с вещей. Сбивает чужой уворот."""
        return total_accuracy(self.derived.accuracy, self.equipment.accuracy)

    @property
    def anticrit(self) -> float:
        """Антикрит: интуиция плюс проценты с вещей. Сбивает чужой крит."""
        return total_anticrit(self.derived.anticrit, self.equipment.anticrit)

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
        return total_dodge(self.derived.dodge_chance, self.equipment.dodge)

    @property
    def crit(self) -> float:
        """Шанс крита: интуиция плюс проценты с вещей."""
        return total_crit(self.derived.crit_chance, self.equipment.crit)

    @property
    def counter(self) -> float:
        """Шанс контрудара: ловкость плюс проценты с вещей."""
        return total_counter(self.derived.counter_chance, self.equipment.counter)

    @property
    def block_hold(self) -> float:
        """Насколько крепко держится блок под критом. Это свойство класса."""
        return total_block_hold(self.derived.block_hold)

    def block_break_against(self, attacker: "Fighter") -> float:
        """Шанс, что крит этого соперника проломит мой блок."""
        return max(MIN_BLOCK_BREAK, BLOCK_BREAK_CHANCE - self.block_hold)

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
    def has_shield(self) -> bool:
        return self.equipment.has_shield

    @property
    def weapons(self) -> tuple[str, ...]:
        """Чем бьёт: по названию на каждую руку с оружием."""
        return self.equipment.weapon_names or (BARE_HANDS,)

    @property
    def attacks_per_round(self) -> int:
        return len(self.weapons)

    @property
    def block_width(self) -> int:
        """Сколько смежных зон закрывает блок.

        Своими руками — две, и класс на это не влияет: разница между
        классами живёт в характеристиках. Третью зону даёт только щит.
        """
        return SHIELD_BLOCK_WIDTH if self.has_shield else BLOCK_WIDTH

    def block_options(self) -> tuple[tuple[Zone, ...], ...]:
        return block_combos(self.block_width)

    @property
    def weapon(self) -> str:
        """Чем бьёт основной рукой. Без оружия — кулаком."""
        return self.equipment.weapon_name or BARE_HANDS

    @property
    def weapon_icon(self) -> str:
        return self.equipment.weapon_icon or BARE_HANDS_ICON

    @property
    def weapon_icons(self) -> tuple[str, ...]:
        """Значки рук — по столбцу ударов на каждую."""
        return self.equipment.weapon_icons or (BARE_HANDS_ICON,)

    # ---------- приёмы ----------

    def gain_energy(self, amount: int) -> None:
        """Накопить энергию. Выше потолка шкала не растёт."""
        self.energy = min(MAX_ENERGY, self.energy + amount)

    def earn(self, source: Source) -> None:
        """Начислить за событие по рецепту своего класса."""
        self.gain_energy(energy_gain(self.fclass.code, source, self.has_shield))

    @property
    def out_of_turns(self) -> bool:
        """Норма приёмов на ход выбрана."""
        return self.pressed >= MAX_PER_TURN

    def can_use(self, code: str) -> bool:
        """Хватает ли энергии и выучен ли приём. Мёртвый не может ничего."""
        if not self.alive or code not in self.loadout or self.out_of_turns:
            return False
        if any(charge.ability.code == code for charge in self.charges):
            return False  # эта заготовка уже лежит и ждёт своего момента
        return self.energy >= self.loadout.cost_of(code)

    def use(self, code: str) -> Charge:
        """Нажать приём: списать энергию и положить заготовку.

        Мгновенные приёмы (лечение) заготовкой тоже становятся — их
        разбирает тот, кто знает про союзников: в дуэли это раунд, в
        групповом бою служба боя.
        """
        if code not in self.loadout:
            raise ValueError(f"Приём {code} не выучен")
        # Порядок проверок — от общего к частному: норма на ход и смерть
        # отменяют приём целиком, и говорить про энергию тогда незачем
        if not self.alive:
            raise ValueError("Мёртвый боец приёмов не применяет")
        if self.out_of_turns:
            raise ValueError(f"За ход пускают в дело не больше {MAX_PER_TURN} приёмов")
        if any(charge.ability.code == code for charge in self.charges):
            raise ValueError("Этот приём уже наготове")
        cost = self.loadout.cost_of(code)
        if self.energy < cost:
            raise ValueError("Не хватает энергии")
        from bot.content.abilities import CATALOGUE

        self.energy -= cost
        self.pressed += 1
        charge = Charge(ability=CATALOGUE[code], tier=self.loadout.tier_of(code))
        self.charges.append(charge)
        return charge

    def charged(self, *effects: Effect) -> Charge | None:
        """Есть ли нажатая заготовка с одним из этих эффектов."""
        for charge in self.charges:
            if charge.ability.effect in effects:
                return charge
        return None

    def charged_all(self, *effects: Effect) -> list[Charge]:
        """Все заготовки этих видов — их эффекты складываются."""
        return [
            charge for charge in self.charges if charge.ability.effect in effects
        ]

    def spend_charge(self, charge: Charge) -> Ability:
        """Снять заготовку: она сработала и больше не ждёт."""
        self.charges.remove(charge)
        return charge.ability

    def heal_by(self, share: float) -> int:
        """Вернуть себе долю запаса. Отдаёт, на сколько поднялось здоровье."""
        if not self.alive:
            return 0
        before = self.hp
        self.hp = min(self.max_hp, self.hp + int(round(self.max_hp * share)))
        return self.hp - before

    @classmethod
    def from_player(cls, player, armed: bool = True) -> "Fighter":
        """Собрать бойца из записи игрока: здоровье — то, что успело затянуться.

        В кулачном бою вещи остаются в раздевалке: ни оружия, ни брони, ни
        прибавок — спорят голые характеристики. Здоровье при этом урезается
        по новому потолку, иначе боец вышел бы на ринг с чужим запасом.

        Выпитое — другое дело: эликсир в раздевалке не оставишь, поэтому его
        прибавка идёт с бойцом на ринг в любом режиме.
        """
        equipment = player.equipment if armed else Equipment()
        effect_stats = getattr(player, "effect_stats", None) or Stats()
        stats = player.stats if armed else player.base_stats.merge(effect_stats)
        fighter = cls(
            user_id=player.user_id,
            name=player.nickname,
            fclass=get_class(player.class_code),
            stats=stats,
            level=player.level,
            equipment=equipment,
            extra_hp=getattr(player, "effect_hp", 0),
            pro=bool(getattr(player, "is_pro", bool)()),
        )
        fighter.hp = max(1, min(player.current_hp(), fighter.max_hp))
        # Приёмы боец приносит с собой, а энергию — нет: она копится с нуля
        # в каждом бою. Слоты копируем, чтобы бой не менял запись игрока
        learned = getattr(player, "loadout", None)
        if learned is not None:
            fighter.loadout = Loadout(slots=dict(learned.slots))
        return fighter


@dataclass
class Strike:
    """Результат одного удара."""

    attacker_id: int
    defender_id: int
    zone: Zone | None
    outcome: Outcome
    # Что защищающийся закрывал в этот ход. Нужно не бою, а разбору: по
    # логу видно, куда били, но не видно, чего ждали, — а привычки бойца
    # читаются именно по блокам
    block: tuple[Zone, ...] = ()
    weapon: str = BARE_HANDS
    damage: int = 0
    counter_damage: int = 0
    armor: int = 0  # сколько сняла броня зоны
    absorbed: int = 0  # сколько всего съели выносливость и броня
    missed_turn: bool = False  # боец не нажал вообще ничего
    # Приёмы, сработавшие на этом ударе: свой у атакующего, чужой у
    # защищающегося. По ним судья и рассказывает, что произошло
    ability: str = ""  # приём атакующего
    defence_ability: str = ""  # приём защищающегося
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


def boxing_round(turn: int) -> int:
    """В каком раунде идёт этот ход. Ходы считаются с единицы."""
    return (turn - 1) // TURNS_PER_ROUND + 1


def turn_in_round(turn: int) -> int:
    """Который это удар внутри своего раунда: первый, второй или третий."""
    return (turn - 1) % TURNS_PER_ROUND + 1


def round_is_over(turn: int) -> bool:
    """Последний ход раунда — после него бойцов разводят по углам."""
    return turn % TURNS_PER_ROUND == 0


def fatigue_calm(limit: int = MAX_TURNS) -> int:
    """До какого хода бойцы держатся ровно — треть отпущенного боя."""
    return max(1, limit // FATIGUE_CALM_SHARE)


def fatigue_multiplier(turn: int, limit: int = MAX_TURNS) -> float:
    """Множитель урона на этом ходу: под конец боя бойцы «раскрываются».

    Разгон растянут на длину боя, поэтому и короткая дуэль, и длинный бой с
    оружием приходят к финальному гонгу с одинаково тяжёлым ударом.
    """
    calm = fatigue_calm(limit)
    step = FATIGUE_TOP / max(1, limit - calm)
    return 1.0 + max(0, turn - calm) * step


def random_action(fighter: Fighter, rng: random.Random | None = None) -> Action:
    """Полный случайный выбор — для симуляций и тестов."""
    rng = rng or random
    return Action(
        attacks=tuple(rng.choice(ALL_ZONES) for _ in fighter.weapons),
        block=rng.choice(fighter.block_options()),
    )


def validate_action(action: Action, fighter: Fighter) -> None:
    """Неполный выбор допустим, чужие блоки — нет."""
    if action.block and action.block not in fighter.block_options():
        raise ValueError(
            f"Блок должен закрывать {fighter.block_width} смежные зоны"
        )


def strikes_of(
    attacker: Fighter,
    defender: Fighter,
    action: Action,
    defender_action: Action,
    round_number: int,
    rng: random.Random,
    limit: int = MAX_TURNS,
) -> list[Strike]:
    """Все удары одного бойца за ход — по одному на руку с оружием."""
    weapons = attacker.weapons
    zones = list(action.attacks) + [None] * (len(weapons) - len(action.attacks))
    strikes = [
        strike_of(
            attacker,
            defender,
            zones[index],
            weapon,
            index,
            defender_action,
            round_number,
            rng,
            missed_turn=action.is_empty and index == 0,
            limit=limit,
        )
        for index, weapon in enumerate(weapons)
    ]
    # Пропуск хода судья отмечает одной строкой, а не по разу на каждую руку
    return strikes[:1] if action.is_empty else strikes


def strike_of(
    attacker: Fighter,
    defender: Fighter,
    zone: Zone | None,
    weapon: str,
    hand: int,
    defender_action: Action,
    round_number: int,
    rng: random.Random,
    missed_turn: bool = False,
    limit: int = MAX_TURNS,
) -> Strike:
    """Один удар одной рукой."""
    strike = Strike(
        attacker_id=attacker.user_id,
        defender_id=defender.user_id,
        zone=zone,
        outcome=Outcome.SKIP,
        block=tuple(defender_action.block),
        weapon=weapon,
        missed_turn=missed_turn,
    )
    if zone is None:
        return strike

    # Порядок, в котором приёмы вмешиваются в удар, важен, и потому он
    # здесь один на всех и написан явно:
    #
    #   1. блок — «Пролом» ломает его наверняка;
    #   2. уворот — «Проворность» уводит с линии удара наверняка;
    #   3. крит — «Критический удар» и родня делают его без броска;
    #   4. урон — «Сильный удар» и родня добавляют своё;
    #   5. «Парирование» обнуляет то, что всё-таки дошло.
    #
    # Заготовка, чей момент не настал, остаётся висеть: ушедший в блок удар
    # не тратит «Проворность», а непрошедший — «Сильный удар».
    # За ход можно пустить в дело до трёх приёмов, и работают они вместе:
    # прибавки к урону складываются, а из приёмов одной ветки срабатывает
    # сильнейший — и снимаются все, за них уже заплачено
    breaker = attacker.charged(Effect.BREAK)

    if zone in defender_action.block:
        strike.outcome = Outcome.BLOCK
        # Классический порядок: сначала блок, потом крит, потом пробитие.
        # Удар, который должен был стать критическим, упирается в блок не
        # насмерть — с какой-то вероятностью он этот блок проламывает.
        crit = rng.random() < attacker.crit_against(defender)
        broke = crit and rng.random() < defender.block_break_against(attacker)
        if breaker is not None:
            # «Пролом» на то и придуман, чтобы не спрашивать бросок
            strike.ability = attacker.spend_charge(breaker).code
            broke = True
        if broke:
            strike.outcome = Outcome.BREAK
            # Проходит ровно половина потолка — и всё. Ни выносливость, ни
            # броня зоны её больше не режут: свою долю защита уже отработала
            # тем, что блок вообще был. Иначе «половина максимального урона»
            # доходила бы до тела слабее обычного попадания.
            broken = (
                _max_damage(attacker, round_number, hand, limit)
                * BLOCK_BREAK_DAMAGE_SHARE
            )
            strike.damage = max(1, int(round(broken)))
            _apply_parry(strike, defender)
        return strike

    # Уворот: та же логика, что у крита. «Коварство» сильнее «Хитрости»,
    # «Хитрость» сильнее «Проворности» — работает лучшее, снимается всё
    evasions = defender.charged_all(Effect.DODGE, Effect.COUNTER)
    evasion = (
        max(evasions, key=lambda one: _DODGE_RANK(one.ability)) if evasions else None
    )
    dodged = rng.random() < defender.dodge_against(attacker)
    if evasion is not None:
        for charge in evasions:
            defender.spend_charge(charge)
        strike.defence_ability = evasion.ability.code
        dodged = True
    if dodged:
        strike.outcome = Outcome.DODGE
        # Контрудар: своим броском или обещанный приёмом
        promised = evasion.ability if evasion is not None else None
        answers = (promised is not None and promised.counter) or (
            rng.random() < defender.counter
        )
        if answers:
            counter = (
                _roll_damage(defender, round_number, rng, hand=0, limit=limit)
                * COUNTER_DAMAGE_MULT
            )
            if promised is not None and promised.crit_counter:
                counter *= defender.derived.crit_power
            # Контрудар прилетает не в выбранную зону, поэтому броню не трогает —
            # только сопротивление от выносливости.
            counter *= 1.0 - attacker.resist_against(defender)
            strike.counter_damage = max(1, int(round(counter)))
            strike.outcome = Outcome.COUNTER
        return strike

    damage = _roll_damage(attacker, round_number, rng, hand, limit)

    # Крит: свой бросок или обещанный приёмом. «Пролом» дожил сюда, если
    # соперник эту зону не закрывал, — тогда он работает как крит.
    # Нажатых приёмов этой ветки может быть несколько: снимаем все, а
    # работает сильнейший — удвоение сильнее пролома, пролом сильнее крита
    sharp = attacker.charged_all(Effect.CRIT, Effect.DOUBLE)
    if breaker is not None and breaker not in sharp:
        sharp.append(breaker)
    if sharp:
        best = max(sharp, key=lambda one: _CRIT_RANK[one.ability.effect])
        for charge in sharp:
            attacker.spend_charge(charge)
        strike.ability = best.ability.code
        strike.outcome = Outcome.CRIT
        damage *= attacker.derived.crit_power
        if best.ability.effect is Effect.DOUBLE:
            damage *= 2
    elif rng.random() < attacker.crit_against(defender):
        strike.outcome = Outcome.CRIT
        damage *= attacker.derived.crit_power
    else:
        strike.outcome = Outcome.HIT

    # Прибавки складываются: три удара по нарастающей дадут сумму, а не
    # самый крупный из них. Ложатся они до брони и выносливости — как
    # обычный урон, а не поверх защиты: иначе «плюс пятнадцать» доходил бы
    # до тела целее, чем сам удар
    bonuses = attacker.charged_all(Effect.DAMAGE)
    for charge in bonuses:
        damage += attacker.spend_charge(charge).damage
    if bonuses:
        # Приём в строке удара один, и крит её уже занял: тогда называем
        # самую крупную прибавку — она заметнее
        strike.ability = strike.ability or max(
            bonuses, key=lambda one: one.ability.damage
        ).ability.code

    _land_damage(strike, damage, attacker, defender, zone, rng)
    _apply_parry(strike, defender)
    return strike


def _apply_parry(strike: Strike, defender: Fighter) -> None:
    """«Парирование»: дошедший удар не наносит урона вовсе.

    Стоит последним и после брони не случайно: приём обещает ноль урона
    независимо от того, крит это или пробитый блок, — значит и снимать он
    должен уже посчитанное, а не влезать в середину расчёта.
    """
    if not strike.damage:
        return
    shield = defender.charged(Effect.PARRY)
    if shield is None:
        return
    strike.defence_ability = defender.spend_charge(shield).code
    strike.absorbed += strike.damage
    strike.damage = 0


def _land_damage(
    strike: Strike,
    damage: float,
    attacker: Fighter,
    defender: Fighter,
    zone: Zone,
    rng: random.Random,
) -> None:
    """Довести удар до тела: выносливость и броня той зоны, куда пришёлся.

    Пробитый блок идёт этой же дорогой: он проломил защиту рук, но не
    доспех на зоне и не выносливость соперника.
    """
    before = damage
    damage *= 1.0 - defender.resist_against(attacker)
    armor = min(defender.equipment.roll_armor(zone, rng), damage * MAX_ARMOR_SHARE)
    strike.armor = int(round(armor))
    damage -= armor
    strike.damage = max(1, int(round(damage)))
    strike.absorbed = max(0, int(round(before)) - strike.damage)


def _roll_damage(
    fighter: Fighter,
    round_number: int,
    rng: random.Random,
    hand: int = 0,
    limit: int = MAX_TURNS,
) -> float:
    """Урон от силы плюс урон оружия, всё вместе растёт от усталости.

    Класс влияет и на оружие: одну и ту же биту воин проворачивает лучше,
    чем танк. Иначе плоский урон оружия стирал бы разницу между классами —
    у того, кто бьёт слабо, прибавка весит вдвое больше.
    """
    raw = rng.randint(fighter.derived.damage_min, fighter.derived.damage_max)
    weapon = fighter.equipment.roll_weapon_damage(hand, rng)
    raw += weapon * fighter.fclass.damage_mult
    return raw * fatigue_multiplier(round_number, limit)


def _max_damage(
    fighter: Fighter, round_number: int, hand: int = 0, limit: int = MAX_TURNS
) -> float:
    """Самое большое, что этот боец может выбить этим оружием в этом раунде.

    От него берётся половина, когда крит проламывает блок: пробитие не
    бросок, а фиксированная доля потолка — иначе редкое событие ещё и
    рулеткой решало бы, стоило ли оно того.
    """
    weapon = fighter.equipment.weapon_damage_max(hand)
    raw = fighter.derived.damage_max + weapon * fighter.fclass.damage_mult
    return raw * fatigue_multiplier(round_number, limit)


def resolve_round(
    first: Fighter,
    first_action: Action,
    second: Fighter,
    second_action: Action,
    round_number: int,
    rng: random.Random | None = None,
    limit: int = MAX_TURNS,
) -> RoundResult:
    """Посчитать раунд и применить урон. Меняет hp и счётчики пропусков.

    `limit` — сколько ходов отпущено этому бою: после него судья считает
    очки. Кулачный короче боя с оружием, и от длины зависит ещё и разгон
    усталости, поэтому число идёт сюда, а не берётся из модуля.
    """
    rng = rng or random

    for fighter, action in ((first, first_action), (second, second_action)):
        if action.is_empty:
            fighter.missed_turns += 1
        else:
            fighter.missed_turns = 0

    strikes = strikes_of(
        first, second, first_action, second_action, round_number, rng, limit
    ) + strikes_of(
        second, first, second_action, first_action, round_number, rng, limit
    )

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
    _fill_energy(strikes, fighters)
    # Норма приёмов на ход выбирается заново каждый раунд. Заготовки при
    # этом остаются: они ждут своего момента, а не конца раунда
    first.pressed = second.pressed = 0

    result = RoundResult(
        number=round_number,
        strikes=strikes,
        hp_after={first.user_id: first.hp, second.user_id: second.hp},
    )
    _apply_ending(result, first, second, limit)
    return result


@dataclass
class GroupEcho:
    """Вторая половина приёма десятой ступени: то, что летит мимо пары.

    Приём этой ступени бьёт не только по сопернику: «Массовый удар»
    достаёт остальных противников, «Бог обмана» и «Призыв к крови» кладут
    заготовку союзникам, «Мастер жизни» их лечит. Сам движок про состав
    отряда не знает — стороны ему приносит служба боя, а он отвечает, кого
    и на сколько задело.
    """

    owner_id: int
    ability: Ability
    splashed: dict[int, int] = field(default_factory=dict)  # id → урон
    healed: dict[int, int] = field(default_factory=dict)  # id → сколько вернулось
    blessed: tuple[int, ...] = ()  # кому легла заготовка
    fallen: tuple[int, ...] = ()  # кто от этого упал

    @property
    def empty(self) -> bool:
        return not (self.splashed or self.healed or self.blessed)


def lay_charge(fighter: Fighter, code: str) -> bool:
    """Положить бойцу чужую заготовку даром. False — она у него уже есть.

    Даром — потому что платит за неё тот, кто нажал приём. Дважды одна и
    та же заготовка не кладётся: две «Проворности» уводят от одного удара
    ровно так же, как одна, и вторая просто сгорела бы.
    """
    from bot.content.abilities import CATALOGUE

    ability = CATALOGUE.get(code)
    if ability is None or not fighter.alive:
        return False
    if any(charge.ability.code == code for charge in fighter.charges):
        return False
    fighter.charges.append(Charge(ability=ability, tier=ability.tier))
    return True


def spread_heal(
    ability: Ability, owner: Fighter, allies: list[Fighter]
) -> dict[int, int]:
    """Лечение союзников — та часть приёма, что срабатывает сразу.

    Мёртвого не поднимают: приём лечит, а не воскрешает.
    """
    if not ability.ally_heal:
        return {}
    healed: dict[int, int] = {}
    for mate in allies:
        if mate.user_id == owner.user_id or not mate.alive:
            continue
        gained = mate.heal_by(ability.ally_heal)
        if gained:
            healed[mate.user_id] = gained
    return healed


def group_aftermath(
    strikes: list[Strike],
    fighters: dict[int, Fighter],
    sides: dict[int, int],
) -> list[GroupEcho]:
    """Разнести по отряду то, что приёмы этого раунда сделали сверх удара.

    `sides` — кто с кем: одинаковое число значит союзники. Босс рейда
    стоит своей стороной, в мясорубке каждый сам себе сторона, и тогда
    союзников нет ни у кого — приём срабатывает своей первой половиной и
    молчит второй.

    Считается после раунда, а не внутри удара, и намеренно: удар знает
    только двоих, а эта половина приёма — про всех остальных.
    """
    echoes: list[GroupEcho] = []
    for strike in strikes:
        for owner_id, code in (
            (strike.attacker_id, strike.ability),
            (strike.defender_id, strike.defence_ability),
        ):
            if not code:
                continue
            from bot.content.abilities import CATALOGUE

            ability = CATALOGUE.get(code)
            if ability is None or not ability.group:
                continue
            owner = fighters.get(owner_id)
            if owner is None:
                continue
            echo = GroupEcho(owner_id=owner_id, ability=ability)
            mine = sides.get(owner_id)

            if ability.splash:
                # «Остальные противники» — все чужие, кроме того, кто уже
                # получил этим ударом: ему досталось и так
                struck = strike.defender_id if owner_id == strike.attacker_id else None
                fallen = []
                for other_id, other in fighters.items():
                    if (
                        other_id in (owner_id, struck)
                        or not other.alive
                        or sides.get(other_id) == mine
                    ):
                        continue
                    other.hp = max(0, other.hp - ability.splash)
                    owner.damage_dealt += ability.splash
                    echo.splashed[other_id] = ability.splash
                    if not other.alive:
                        fallen.append(other_id)
                echo.fallen = tuple(fallen)

            allies = [
                one
                for other_id, one in fighters.items()
                if other_id != owner_id and sides.get(other_id) == mine
            ]
            if ability.aura:
                echo.blessed = tuple(
                    one.user_id for one in allies if lay_charge(one, ability.aura)
                )
            if ability.ally_heal:
                echo.healed = spread_heal(ability, owner, allies)

            if not echo.empty:
                echoes.append(echo)
    return echoes


def _fill_energy(strikes: list[Strike], fighters: dict[int, Fighter]) -> None:
    """Начислить энергию за раунд: за точные удары и удержанные блоки.

    Считается по каждому удару отдельно, поэтому боец с двумя оружиями
    копит вдвое быстрее, а щит вдвое ускоряет шкалу блоков — та же плата
    за слот второй руки, что и везде.

    Шкала одна, а цена события — своя у каждого класса: воин копит ударами,
    танк блоками, ассасин критами, трикстер уворотами. Событие, которое
    классу не свойственно, приносит ему ноль, и это не забывчивость, а
    рычаг — см. `ENERGY_SOURCES`.

    Пробитый блок (`BREAK`) атакующему засчитывается как удар, защитнику —
    никак: он не удержался.

    **Приём себя не кормит.** Исход, устроенный приёмом, энергии не
    приносит — ни уворот от «Проворности», ни крит от «Пролома». Без
    этого правила система идёт вразнос: гарантированный уворот начисляет
    за уворот, этого хватает на следующую «Проворность», и трикстер
    уворачивается вечно. Ровно так круг и переворачивался, и никакая цена
    события этого не лечила — петлю не закрыть, её можно только разорвать.
    """
    for strike in strikes:
        if strike.outcome in (Outcome.HIT, Outcome.CRIT, Outcome.BREAK):
            if strike.ability:
                continue  # удар устроил приём — платить за него не за что
            attacker = fighters[strike.attacker_id]
            attacker.earn(Source.HIT)
            if strike.outcome is Outcome.CRIT:
                attacker.earn(Source.CRIT)
        elif strike.outcome is Outcome.BLOCK:
            fighters[strike.defender_id].earn(Source.BLOCK)
        elif strike.outcome in (Outcome.DODGE, Outcome.COUNTER):
            if strike.defence_ability:
                continue  # ушёл не сам, а приёмом
            fighters[strike.defender_id].earn(Source.DODGE)


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


def _apply_ending(
    result: RoundResult, first: Fighter, second: Fighter, limit: int = MAX_TURNS
) -> None:
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
    elif result.number >= limit:
        result.finished = True
        result.end_reason = DuelEnd.JUDGE
        result.winner_id = judge_decision(first, second)


def judge_decision(first: Fighter, second: Fighter) -> int | None:
    """Решение судьи, когда шесть раундов отбоксировали без нокаута.

    Считаем по нанесённому урону, а не по остатку здоровья: победа должна
    достаться тому, кто дрался, а не тому, у кого запас больше. У танка
    здоровья изначально вдвое против ассасина — по остатку он выигрывал бы
    судейские решения, ни разу толком не попав.

    Урон вровень — смотрим, кто меньше пропустил. Вровень и это — ничья, но
    случается такое примерно никогда: ничья в клубе бывает, когда бойцы
    роняют друг друга одним разменом.
    """
    if first.damage_dealt != second.damage_dealt:
        return (
            first.user_id
            if first.damage_dealt > second.damage_dealt
            else second.user_id
        )
    if first.hp_percent != second.hp_percent:
        return first.user_id if first.hp_percent > second.hp_percent else second.user_id
    return None


__all__ = [
    "LONG_ROUNDS",
    "LONG_TURNS",
    "MATCH_ROUNDS",
    "MAX_MISSED_TURNS",
    "MAX_ROUNDS",
    "MAX_TURNS",
    "TURNS_PER_ROUND",
    "Action",
    "DuelEnd",
    "Fighter",
    "Outcome",
    "RoundResult",
    "Strike",
    "block_combo",
    "boxing_round",
    "fatigue_multiplier",
    "judge_decision",
    "random_action",
    "round_is_over",
    "resolve_round",
    "turn_in_round",
    "validate_action",
]
