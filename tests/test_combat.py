"""Тесты боевого движка."""

import random
from dataclasses import replace

import pytest

from bot.game.classes import (
    ALL_ZONES,
    ASSASSIN,
    BLOCK_WIDTH,
    FIGHTER_CLASSES,
    ROGUE,
    TANK,
    WARRIOR,
    Stats,
    Zone,
    block_combo,
    block_combos,
    block_title,
)
from bot.game.combat import (
    MATCH_ROUNDS,
    MAX_MISSED_TURNS,
    MAX_ROUNDS,
    MAX_TURNS,
    MIN_DODGE_CHANCE,
    TURNS_PER_ROUND,
    _max_damage,
    boxing_round,
    round_is_over,
    turn_in_round,
    Action,
    DuelEnd,
    Fighter,
    Outcome,
    fatigue_multiplier,
    judge_decision,
    random_action,
    resolve_round,
    validate_action,
)
from bot.game.equipment import CATALOGUE, Equipment, ItemKind, Slot
from bot.game.reference import developed_stats, reference_equipment
from bot.game.stats import (
    BLOCK_BREAK_CHANCE,
    BLOCK_BREAK_DAMAGE_SHARE,
    MIN_BLOCK_BREAK,
    derive,
)

def make(fclass=WARRIOR, user_id=1, name="Боец", **kwargs):
    return Fighter(
        user_id=user_id, name=name, fclass=fclass, stats=fclass.base_stats, **kwargs
    )


def guard(zone: Zone, width: int = BLOCK_WIDTH) -> tuple[Zone, ...]:
    return block_combo(zone, width)


def strike_at(zone: Zone, block: tuple[Zone, ...] = ()) -> Action:
    return Action(attack=zone, block=block)


# ---------- зоны и блоки ----------


def test_zones_form_a_ring():
    combos = block_combos(BLOCK_WIDTH)
    assert len(combos) == len(ALL_ZONES)
    assert combos[0] == (Zone.HEAD, Zone.CHEST)
    # последний блок замыкает кольцо: ноги и снова голова
    assert combos[-1] == (Zone.LEGS, Zone.HEAD)
    assert all(len(combo) == BLOCK_WIDTH for combo in combos)


def test_the_block_never_covers_more_than_two_zones():
    """Три зоны разом не закрывает никто: щитов в клубе нет."""
    from bot.game.classes import block_button

    assert all(len(combo) == 2 for combo in block_combos())
    assert [block_button(combo) for combo in block_combos()] == [
        "🛡 Голова + Корпус",
        "🛡 Корпус + Живот",
        "🛡 Живот + Пояс",
        "🛡 Пояс + Ноги",
        "🛡 Ноги + Голова",
    ]


def test_block_title_reads_like_a_button():
    assert block_title((Zone.HEAD, Zone.CHEST)) == "Голова + корпус"


# ---------- снаряжение бойца ----------


def test_bare_fighter_punches_with_fists():
    fighter = make()
    assert fighter.weapon == "кулаком"
    assert fighter.block_width == BLOCK_WIDTH


def test_weapon_changes_what_the_judge_calls_it():
    fighter = make(equipment=Equipment.from_codes({"weapon": "knuckles"}))
    assert fighter.weapon == "кастетом"


def test_nobody_gets_a_second_strike_or_a_wider_block():
    """Рука одна: двух ударов за ход не сделать никаким снаряжением.

    Раньше во вторую руку брали второе оружие или щит — от этого зависели
    и число ударов, и ширина блока. Ни того, ни другого больше нет.
    """
    from bot.game.equipment import ALL_SLOTS

    assert not hasattr(Fighter, "attacks_per_round")
    assert not hasattr(Equipment, "second_weapon")
    assert not hasattr(Equipment, "has_shield")
    # щита нет ни как слота, ни как вида предмета
    assert "shield" not in {slot.value for slot in ALL_SLOTS}
    assert {kind.value for kind in ItemKind} == {"gear", "weapon"}
    # у оружия ровно одно место на карточке
    assert CATALOGUE["knuckles"].slots == (Slot.WEAPON,)

    for fclass in FIGHTER_CLASSES.values():
        assert make(fclass=fclass).block_width == BLOCK_WIDTH


# ---------- размен ударами ----------


def test_fighter_starts_with_full_hp():
    fighter = make()
    assert fighter.hp == fighter.max_hp == derive(WARRIOR, WARRIOR.base_stats).max_hp
    assert fighter.alive


def test_block_absorbs_damage_completely():
    attacker, defender = make(user_id=1), make(user_id=2)
    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        strike_at(Zone.BELLY, guard(Zone.HEAD)),
        round_number=1,
        rng=random.Random(1),
    )
    strike = result.strikes[0]
    assert strike.outcome is Outcome.BLOCK
    assert strike.damage == 0
    assert result.strikes[1].outcome is not Outcome.BLOCK


# ---------- пробитие блока критом ----------


class Loaded(random.Random):
    """Кости с заранее заданными бросками: сначала список, потом как обычно."""

    def __init__(self, rolls, seed=0):
        super().__init__(seed)
        self.rolls = list(rolls)

    def random(self):
        return self.rolls.pop(0) if self.rolls else super().random()


def test_a_crit_that_hits_a_block_can_break_through():
    """Классический порядок: блок, потом крит, потом бросок на пробитие."""
    attacker = make(ASSASSIN, user_id=1)
    defender = make(ROGUE, user_id=2)
    # первый бросок — крит есть, второй — пробитие прошло
    rng = Loaded([0.0, 0.0])

    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.HEAD)),
        round_number=1,
        rng=rng,
    )

    strike = result.strikes[0]
    assert strike.outcome is Outcome.BREAK
    assert strike.damage > 0


def test_a_block_that_holds_costs_the_attacker_nothing():
    """Крит был, а пробитие не прошло — обычный блок, урона нет."""
    attacker = make(ASSASSIN, user_id=1)
    defender = make(TANK, user_id=2)
    rng = Loaded([0.0, 0.99])  # крит есть, бросок на пробитие провален

    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.HEAD)),
        round_number=1,
        rng=rng,
    )

    assert result.strikes[0].outcome is Outcome.BLOCK
    assert result.strikes[0].damage == 0
    assert defender.hp == defender.max_hp


def test_an_ordinary_blocked_hit_never_breaks_a_block():
    """Не крит — не пробитие: обычный удар вязнет в блоке при любом броске."""
    attacker = make(WARRIOR, user_id=1)
    defender = make(ROGUE, user_id=2)
    rng = Loaded([0.99, 0.0])  # крита нет, а бросок на пробитие был бы удачным

    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.HEAD)),
        round_number=1,
        rng=rng,
    )

    assert result.strikes[0].outcome is Outcome.BLOCK


def test_a_broken_block_lets_through_half_of_the_maximum():
    """Проходит ровно половина потолка — ни брони, ни сопротивления сверху."""
    attacker = make(ASSASSIN, user_id=1)
    # на защитнике полный доспех: он не должен срезать пробитие
    armour = reference_equipment(ROGUE, 5)
    defender = Fighter(
        2, "Марла", ROGUE, ROGUE.base_stats.merge(armour.bonus), equipment=armour
    )
    assert defender.equipment.armor_range(Zone.HEAD)[1] > 0
    rng = Loaded([0.0, 0.0])

    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.HEAD)),
        round_number=1,
        rng=rng,
    )

    strike = result.strikes[0]
    expected = round(_max_damage(attacker, 1) * BLOCK_BREAK_DAMAGE_SHARE)
    assert strike.outcome is Outcome.BREAK
    assert strike.damage == expected
    assert strike.armor == 0


def test_the_classes_hold_a_block_in_the_right_order():
    """Танк держит крепче всех, за ним воин, потом трикстер, потом ассасин."""
    holds = [make(fclass).block_hold for fclass in (TANK, WARRIOR, ROGUE, ASSASSIN)]
    assert holds == sorted(holds, reverse=True)
    assert len(set(holds)) == 4  # ступеньки, а не общая полка

    # и то же самое с обратной стороны: у ассасина блок ломают чаще всех
    breaks = [
        make(fclass, user_id=2).block_break_against(make(ASSASSIN))
        for fclass in (TANK, WARRIOR, ROGUE, ASSASSIN)
    ]
    assert breaks == sorted(breaks)
    assert breaks[0] == pytest.approx(BLOCK_BREAK_CHANCE - TANK.block_hold)
    assert all(chance >= MIN_BLOCK_BREAK for chance in breaks)


def test_nobody_closes_from_a_break_completely():
    """Сколько ни держи, щёлочка остаётся: наглухо блок не запирается."""
    stubborn = make(TANK, user_id=2)
    stubborn.derived = replace(stubborn.derived, block_hold=0.99)

    assert stubborn.block_break_against(make(ASSASSIN)) == MIN_BLOCK_BREAK


def test_the_judge_marks_a_broken_block_with_a_bleeding_shield():
    """У пробития свой значок: щит с кровью, и урон в строке виден."""
    from bot.game.narrator import OUTCOME_EMOJI, describe_strike

    attacker = make(ASSASSIN, user_id=1, name="Тайлер")
    defender = make(ROGUE, user_id=2, name="Марла")
    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.HEAD)),
        round_number=1,
        rng=Loaded([0.0, 0.0]),
    )
    strike = result.strikes[0]

    line = describe_strike(strike, attacker, defender, random.Random(1))
    assert line.startswith(OUTCOME_EMOJI[Outcome.BREAK])
    assert OUTCOME_EMOJI[Outcome.BREAK] == "🛡🩸"
    assert f"−{strike.damage}" in line
    assert f"[{strike.defender_hp_after}/{defender.max_hp}]" in line


def test_unblocked_hit_deals_damage():
    attacker, defender = make(user_id=1), make(user_id=2)
    start_hp = defender.hp
    resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        strike_at(Zone.HEAD, guard(Zone.CHEST)),
        round_number=1,
        rng=random.Random(3),
    )
    assert defender.hp < start_hp


def test_a_turn_is_exactly_one_strike_from_each_side():
    """За ход ровно два удара на двоих — по одному с каждой стороны."""
    attacker = make(user_id=1, equipment=Equipment.from_codes({"weapon": "knuckles"}))
    defender = make(user_id=2)
    result = resolve_round(
        attacker,
        Action(attack=Zone.HEAD, block=guard(Zone.LEGS)),
        defender,
        strike_at(Zone.CHEST, guard(Zone.HEAD)),
        round_number=1,
        rng=random.Random(4),
    )
    assert len(result.strikes) == 2
    mine = [s for s in result.strikes if s.attacker_id == 1]
    assert len(mine) == 1
    assert mine[0].weapon == "кастетом"


def test_damage_is_simultaneous():
    """Оба удара считаются от состояния на начало раунда."""
    attacker, defender = make(user_id=1), make(user_id=2)
    attacker.hp = defender.hp = 1
    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        strike_at(Zone.CHEST, guard(Zone.BELLY)),
        round_number=1,
        rng=random.Random(0),
    )
    assert result.finished
    # добили друг друга за один ход — ничья, без тай-брейка
    assert result.end_reason is DuelEnd.DOUBLE_KO
    assert result.winner_id is None


def test_strike_carries_remaining_hp_for_the_story():
    attacker, defender = make(user_id=1), make(user_id=2)
    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.CHEST)),  # только защищается
        round_number=1,
        rng=random.Random(5),
    )
    hit = result.strikes[0]
    if hit.damage:
        assert hit.defender_hp_after == defender.hp
        assert hit.defender_hp_after == result.hp_after[2]


def test_ko_finishes_duel():
    attacker, defender = make(user_id=1), make(user_id=2)
    defender.hp = 1
    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.HEAD)),
        defender,
        strike_at(Zone.LEGS, guard(Zone.BELLY)),
        round_number=1,
        rng=random.Random(5),
    )
    assert result.finished
    assert result.end_reason is DuelEnd.KO
    assert result.winner_id == 1


# ---------- боксёрские раунды ----------


def test_eighteen_turns_make_six_rounds_of_three():
    assert (TURNS_PER_ROUND, MATCH_ROUNDS, MAX_TURNS) == (3, 6, 18)
    assert [boxing_round(turn) for turn in range(1, 10)] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert [turn_in_round(turn) for turn in range(1, 7)] == [1, 2, 3, 1, 2, 3]
    # перерыв — после каждого третьего удара, и после последнего тоже
    assert [turn for turn in range(1, MAX_TURNS + 1) if round_is_over(turn)] == [
        3, 6, 9, 12, 15, 18
    ]


def test_the_judge_counts_damage_dealt_not_health_left():
    """Победа тому, кто дрался, а не тому, у кого запас больше.

    У танка здоровья вдвое против ассасина: считай судья по остатку, танк
    выигрывал бы решения, ни разу толком не попав.
    """
    puncher, wall = make(ASSASSIN, user_id=1), make(TANK, user_id=2)
    puncher.hp, wall.hp = 10, 90
    puncher.damage_dealt, wall.damage_dealt = 80, 30

    assert judge_decision(puncher, wall) == 1


def test_equal_damage_falls_back_to_who_took_less():
    first, second = make(user_id=1), make(user_id=2)
    first.damage_dealt = second.damage_dealt = 40
    first.hp, second.hp = 50, 20

    assert judge_decision(first, second) == 1


def test_a_judged_draw_needs_both_the_damage_and_the_health_to_match():
    """Ничья по решению судьи — редкость: всё должно совпасть до цифры."""
    first, second = make(user_id=1), make(user_id=2)
    first.damage_dealt = second.damage_dealt = 40
    first.hp = second.hp = 50

    assert judge_decision(first, second) is None


def test_round_limit_ends_with_judge_call():
    first, second = make(user_id=1), make(user_id=2)
    first.hp = second.hp = 200
    result = resolve_round(
        first,
        strike_at(Zone.HEAD, guard(Zone.HEAD)),
        second,
        strike_at(Zone.HEAD, guard(Zone.HEAD)),
        round_number=MAX_TURNS,
        rng=random.Random(7),
    )
    assert result.finished
    assert result.end_reason is DuelEnd.JUDGE


def test_fatigue_only_after_threshold():
    assert fatigue_multiplier(1) == 1.0
    assert fatigue_multiplier(6) == 1.0
    assert fatigue_multiplier(10) > fatigue_multiplier(7) > 1.0


# ---------- неполный выбор и пропуски ----------


def test_partial_choice_is_allowed_but_junk_is_not():
    fighter = make()
    validate_action(strike_at(Zone.HEAD), fighter)  # без блока
    validate_action(Action(), fighter)  # вообще ничего
    validate_action(strike_at(Zone.HEAD, guard(Zone.HEAD)), fighter)
    with pytest.raises(ValueError):  # блок не по смежным зонам
        validate_action(Action(attack=Zone.HEAD, block=(Zone.HEAD, Zone.LEGS)), fighter)



def test_fighter_without_attack_zone_does_not_strike():
    attacker, defender = make(user_id=1), make(user_id=2)
    start_hp = defender.hp
    result = resolve_round(
        attacker,
        Action(block=guard(Zone.HEAD)),  # только защита
        defender,
        strike_at(Zone.LEGS, guard(Zone.BELLY)),
        round_number=1,
        rng=random.Random(4),
    )
    assert result.strikes[0].outcome is Outcome.SKIP
    assert result.strikes[0].missed_turn is False  # блок-то он выбрал
    assert defender.hp == start_hp
    assert attacker.missed_turns == 0


def test_unchosen_block_leaves_every_zone_open():
    attacker, defender = make(user_id=1), make(user_id=2)
    result = resolve_round(
        attacker,
        strike_at(Zone.BELT, guard(Zone.HEAD)),
        defender,
        strike_at(Zone.HEAD),  # блок не выбран
        round_number=1,
        rng=random.Random(11),
    )
    assert result.strikes[0].outcome is not Outcome.BLOCK


def test_empty_action_counts_as_missed_turn():
    attacker, defender = make(user_id=1), make(user_id=2)
    result = resolve_round(
        attacker,
        Action(),
        defender,
        strike_at(Zone.HEAD, guard(Zone.HEAD)),
        round_number=1,
        rng=random.Random(6),
    )
    assert attacker.missed_turns == 1
    assert result.strikes[0].missed_turn is True
    assert not result.finished


def test_three_missed_turns_end_the_duel():
    absent, active = make(user_id=1), make(user_id=2)
    action = strike_at(Zone.BELT, guard(Zone.HEAD))
    rng = random.Random(9)
    for round_number in range(1, MAX_MISSED_TURNS + 1):
        result = resolve_round(absent, Action(), active, action, round_number, rng)
    assert absent.missed_turns == MAX_MISSED_TURNS
    assert result.finished
    assert result.end_reason is DuelEnd.TECHNICAL
    assert result.winner_id == 2


def test_any_press_resets_the_missed_turn_counter():
    fighter, opponent = make(user_id=1), make(user_id=2)
    action = strike_at(Zone.BELT, guard(Zone.HEAD))
    rng = random.Random(13)
    resolve_round(fighter, Action(), opponent, action, 1, rng)
    resolve_round(fighter, Action(), opponent, action, 2, rng)
    assert fighter.missed_turns == 2
    resolve_round(fighter, Action(block=guard(Zone.HEAD)), opponent, action, 3, rng)
    assert fighter.missed_turns == 0


def test_both_silent_fighters_get_a_draw():
    first, second = make(user_id=1), make(user_id=2)
    rng = random.Random(3)
    for round_number in range(1, MAX_MISSED_TURNS + 1):
        result = resolve_round(first, Action(), second, Action(), round_number, rng)
    assert result.finished
    assert result.end_reason is DuelEnd.TECHNICAL
    assert result.winner_id is None


@pytest.mark.parametrize("code", sorted(FIGHTER_CLASSES))
def test_duel_always_terminates(code):
    """Любая пара классов доходит до результата и не зависает."""
    fclass = FIGHTER_CLASSES[code]
    rng = random.Random(42)
    for opponent_code, opponent in FIGHTER_CLASSES.items():
        first = make(fclass=fclass, user_id=1)
        second = make(fclass=opponent, user_id=2, name="Соперник")
        for round_number in range(1, MAX_ROUNDS + 1):
            result = resolve_round(
                first,
                random_action(first, rng),
                second,
                random_action(second, rng),
                round_number,
                rng,
            )
            if result.finished:
                break
        else:  # pragma: no cover
            pytest.fail(f"{code} vs {opponent_code}: бой не закончился")
        assert result.end_reason is not None


# ---------- точность, антикрит, сопротивление, броня ----------


class Rigged:
    """Кости с заранее расписанным исходом: сначала броски, потом числа."""

    def __init__(self, rolls: list[float] | None = None, numbers: list[int] | None = None):
        self.rolls = list(rolls or [])
        self.numbers = list(numbers or [])

    def random(self) -> float:
        return self.rolls.pop(0) if self.rolls else 1.0

    def randint(self, low: int, high: int) -> int:
        return self.numbers.pop(0) if self.numbers else high

    def choice(self, seq):  # pragma: no cover - в этих тестах не нужен
        return seq[0]


def dressed(*codes: str, user_id: int = 1, fclass=WARRIOR, **kwargs) -> Fighter:
    equipment = Equipment.from_codes(
        {CATALOGUE[code].slots[0].value: code for code in codes}
    )
    return Fighter(
        user_id=user_id,
        name="Боец",
        fclass=fclass,
        stats=fclass.base_stats.merge(equipment.bonus),
        equipment=equipment,
        **kwargs,
    )


def test_each_pair_is_split_between_two_stats():
    """Круг: ловкость бьёт выносливость, выносливость — интуицию, интуиция — ловкость."""
    nimble = derive(WARRIOR, Stats(strength=5, agility=30, intuition=5, endurance=5))
    sharp = derive(WARRIOR, Stats(strength=5, agility=5, intuition=30, endurance=5))
    tough = derive(WARRIOR, Stats(strength=5, agility=5, intuition=5, endurance=30))

    # ловкость — уворот и пробивание, но не точность и не антикрит
    assert nimble.dodge_chance > sharp.dodge_chance
    assert nimble.penetration > sharp.penetration
    assert nimble.accuracy == pytest.approx(sharp.accuracy / 6, abs=0.01)
    # интуиция — крит и точность
    assert sharp.crit_chance > tough.crit_chance
    assert sharp.accuracy > nimble.accuracy
    # выносливость — сопротивление и антикрит
    assert tough.resist > nimble.resist
    assert tough.anticrit > sharp.anticrit


def gain_per_point(fclass, stat: str, read, low: int = 5, high: int = 15) -> float:
    """Сколько приносит классу одно очко этой характеристики."""
    base = Stats(strength=5, agility=5, intuition=5, endurance=5)
    weak = derive(fclass, Stats(**{**base.as_dict(), stat: low}))
    strong = derive(fclass, Stats(**{**base.as_dict(), stat: high}))
    return (read(strong) - read(weak)) / (high - low)


def test_class_gets_more_out_of_its_own_stat():
    """Профильное очко весит для своего класса больше, чем для чужого."""
    checks = (
        (WARRIOR, "strength", lambda d: d.damage_max),
        (ROGUE, "agility", lambda d: d.dodge_chance),
        (ASSASSIN, "intuition", lambda d: d.crit_chance),
        (TANK, "endurance", lambda d: d.resist),
    )
    for owner, stat, read in checks:
        mine = gain_per_point(owner, stat, read)
        for other in (WARRIOR, ROGUE, ASSASSIN, TANK):
            if other is owner:
                continue
            assert mine > gain_per_point(other, stat, read), (
                f"{stat}: {owner.title} должен получать больше, чем {other.title}"
            )


def test_penetration_shaves_the_resistance():
    striker = make(user_id=1)
    defender = make(user_id=2)
    object.__setattr__(defender.derived, "resist", 0.20)
    object.__setattr__(striker.derived, "penetration", 0.08)

    assert defender.resist_against(striker) == pytest.approx(0.12)
    object.__setattr__(striker.derived, "penetration", 0.50)
    assert defender.resist_against(striker) == 0.0


def test_accuracy_eats_the_dodge_point_for_point():
    """Трикстер с 40% уворота против точности в 10% уворачивается на 30%."""
    dodger = make(user_id=1)
    striker = make(user_id=2)
    object.__setattr__(dodger.derived, "dodge_chance", 0.40)
    object.__setattr__(striker.derived, "accuracy", 0.10)

    assert dodger.dodge_against(striker) == pytest.approx(0.30)


def test_dodge_never_falls_to_zero():
    dodger = make(user_id=1)
    striker = make(user_id=2)
    object.__setattr__(dodger.derived, "dodge_chance", 0.10)
    object.__setattr__(striker.derived, "accuracy", 0.90)

    assert dodger.dodge_against(striker) == MIN_DODGE_CHANCE


def test_anticrit_eats_the_crit_point_for_point():
    striker = make(user_id=1, fclass=ASSASSIN)
    defender = make(user_id=2)
    object.__setattr__(striker.derived, "crit_chance", 0.40)
    object.__setattr__(defender.derived, "anticrit", 0.15)

    assert striker.crit_against(defender) == pytest.approx(0.25)
    object.__setattr__(defender.derived, "anticrit", 0.90)
    assert striker.crit_against(defender) == 0.0


def test_weapon_damage_lands_on_top_of_the_strength_damage():
    """Сила выбила 11, кастет добавил свои 4 — соперник получает 15."""
    attacker = dressed("knuckles", user_id=1)
    defender = make(user_id=2)
    object.__setattr__(defender.derived, "resist", 0.0)
    object.__setattr__(attacker.derived, "penetration", 0.0)

    # промах по увороту, без крита, урон силой 11, урон кастетом 4
    rng = Rigged(rolls=[0.99, 0.99], numbers=[11, 4])
    weapon_share = round(4 * attacker.fclass.damage_mult)
    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.CHEST)),
        round_number=1,
        rng=rng,
    )
    assert result.strikes[0].damage == 11 + weapon_share


def test_resistance_shaves_a_share_off_every_hit():
    """Урон 30 при сопротивлении 10% доходит как 27."""
    attacker = make(user_id=1)
    defender = make(user_id=2)
    object.__setattr__(defender.derived, "resist", 0.10)
    object.__setattr__(attacker.derived, "penetration", 0.0)

    rng = Rigged(rolls=[0.99, 0.99], numbers=[30])
    result = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.LEGS)),
        defender,
        Action(block=guard(Zone.CHEST)),
        round_number=1,
        rng=rng,
    )
    assert result.strikes[0].damage == 27


def test_armor_holds_the_zone_it_covers():
    """Шлем принимает удар в голову и не помогает, когда бьют по ногам."""
    attacker = make(user_id=1)
    helmet = CATALOGUE["moto_helmet"]
    defender = dressed("moto_helmet", user_id=2)
    object.__setattr__(defender.derived, "resist", 0.0)
    object.__setattr__(attacker.derived, "penetration", 0.0)

    assert defender.armor_range(Zone.HEAD) == (helmet.armor_min, helmet.armor_max)
    assert defender.armor_range(Zone.LEGS) == (0, 0)

    # удар 30 в голову: броня снимает свои 3
    rng = Rigged(rolls=[0.99, 0.99], numbers=[30, 3])
    head = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.BELT)),
        defender,
        Action(block=guard(Zone.CHEST)),
        round_number=1,
        rng=rng,
    ).strikes[0]
    assert (head.damage, head.armor) == (27, 3)

    # тот же удар по ногам проходит целиком
    rng = Rigged(rolls=[0.99, 0.99], numbers=[30])
    legs = resolve_round(
        attacker,
        strike_at(Zone.LEGS, guard(Zone.BELT)),
        defender,
        Action(block=guard(Zone.CHEST)),
        round_number=1,
        rng=rng,
    ).strikes[0]
    assert (legs.damage, legs.armor) == (30, 0)


def test_a_shirt_and_a_jacket_stack_on_the_same_zones():
    """Футболка и верхняя одежда лежат слоями: их броня складывается."""
    from bot.game.equipment import SLOT_ZONES

    assert SLOT_ZONES[Slot.SHIRT] == SLOT_ZONES[Slot.JACKET] == (Zone.CHEST, Zone.BELLY)
    assert Slot.SHIRT.section == "футболки"
    assert Slot.JACKET.section == "верхняя одежда"


def test_armor_never_eats_more_than_half_of_a_hit():
    """Иначе комплект брони делает лёгкие классы безвредными."""
    attacker = make(user_id=1)
    defender = dressed("moto_helmet", user_id=2)
    object.__setattr__(defender.derived, "resist", 0.0)
    object.__setattr__(attacker.derived, "penetration", 0.0)

    rng = Rigged(rolls=[0.99, 0.99], numbers=[6, 5, 2])  # урон 6, брони на 7
    strike = resolve_round(
        attacker,
        strike_at(Zone.HEAD, guard(Zone.BELT)),
        defender,
        Action(block=guard(Zone.CHEST)),
        round_number=1,
        rng=rng,
    ).strikes[0]
    assert strike.damage == 3
    assert strike.armor == 3


# ---------- камень-ножницы-бумага ----------


def duel_share(first: str, second: str, level: int, runs: int, seed: int) -> float:
    """Доля побед первого класса в серии боёв на этом уровне, в комплектах."""
    rng = random.Random(seed)
    wins = 0.0
    for _ in range(runs):
        fighters = []
        for index, code in enumerate((first, second), start=1):
            fclass = FIGHTER_CLASSES[code]
            equipment = reference_equipment(fclass, level)
            stats = developed_stats(fclass, level).merge(equipment.bonus)
            fighters.append(
                Fighter(index, fclass.title, fclass, stats, level, equipment=equipment)
            )
        a, b = fighters
        number = 1
        while True:
            result = resolve_round(
                a, random_action(a, rng), b, random_action(b, rng), number, rng
            )
            if result.finished:
                break
            number += 1
        if result.winner_id == 1:
            wins += 1
        elif result.winner_id is None:
            wins += 0.5
    return wins / runs


@pytest.mark.parametrize(
    "winner,loser,why",
    [
        ("rogue", "tank", "ловкость пробивает сопротивление"),
        ("tank", "assassin", "выносливость гасит крит антикритом"),
        ("assassin", "rogue", "интуиция ловит уворот точностью"),
    ],
)
@pytest.mark.parametrize("level", [1, 8])
def test_the_circle_holds_on_every_level(winner, loser, why, level):
    """Трикстер бьёт танка, танк — ассасина, ассасин — трикстера.

    Считаем по трём сидам: перевес в круге около шести очков, и на сотне
    боёв с одного сида он тонет в разбросе. Такой тест ловил бы не правку
    баланса, а любое смещение потока случайных чисел — например лишний
    бросок на пробитие блока.
    """
    share = sum(
        duel_share(winner, loser, level=level, runs=200, seed=seed + level)
        for seed in (2024, 4048, 6072)
    ) / 3
    assert share > 0.5, (
        f"{FIGHTER_CLASSES[winner].title} должен бить "
        f"{FIGHTER_CLASSES[loser].title} ({why}), а взял {share:.0%} на {level} уровне"
    )


def test_the_warrior_stays_out_of_the_circle():
    """Воин ровен со всеми: его дело урон, а не круг.

    Считаем по трём сидам, как и круг: с одного сида доля гуляет на пять-семь
    очков, и такой тест ловил бы не баланс, а смещение потока случайных чисел —
    хоть лишний бросок брони от новой вещи.
    """
    for rival in ("rogue", "assassin", "tank"):
        share = sum(
            duel_share("warrior", rival, level=8, runs=200, seed=seed)
            for seed in (2032, 4056, 6080)
        ) / 3
        assert 0.40 < share < 0.62, f"воин против {rival}: {share:.0%}"
