"""Тесты боевого движка."""

import random

import pytest

from bot.game.classes import (
    ALL_ZONES,
    BLOCK_WIDTH,
    FIGHTER_CLASSES,
    SHIELD_BLOCK_WIDTH,
    TANK,
    WARRIOR,
    Zone,
    block_combo,
    block_combos,
    block_title,
)
from bot.game.combat import (
    MAX_MISSED_TURNS,
    MAX_ROUNDS,
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
from bot.game.equipment import CATALOGUE, Equipment, Item, ItemKind, Slot
from bot.game.stats import derive

SHIV = Item(
    "shiv",
    "Заточка",
    Slot.SHIELD,
    "🗡",
    kind=ItemKind.WEAPON,
    instrumental="заточкой",
    strength=1,
)


def make(fclass=WARRIOR, user_id=1, name="Боец", **kwargs):
    return Fighter(
        user_id=user_id, name=name, fclass=fclass, stats=fclass.base_stats, **kwargs
    )


def guard(zone: Zone, width: int = BLOCK_WIDTH) -> tuple[Zone, ...]:
    return block_combo(zone, width)


def strike_at(zone: Zone, block: tuple[Zone, ...] = ()) -> Action:
    return Action(attacks=(zone,), block=block)


# ---------- зоны и блоки ----------


def test_zones_form_a_ring():
    combos = block_combos(BLOCK_WIDTH)
    assert len(combos) == len(ALL_ZONES)
    assert combos[0] == (Zone.HEAD, Zone.CHEST)
    # последний блок замыкает кольцо: ноги и снова голова
    assert combos[-1] == (Zone.LEGS, Zone.HEAD)
    assert all(len(combo) == BLOCK_WIDTH for combo in combos)


def test_shield_block_covers_three_adjacent_zones():
    combos = block_combos(SHIELD_BLOCK_WIDTH)
    assert combos[0] == (Zone.HEAD, Zone.CHEST, Zone.BELLY)
    assert combos[-1] == (Zone.LEGS, Zone.HEAD, Zone.CHEST)


def test_block_title_reads_like_a_button():
    assert block_title((Zone.HEAD, Zone.CHEST)) == "Голова + грудь"
    assert block_title(guard(Zone.BELLY, 3)) == "Живот + пояс + ноги"


# ---------- снаряжение бойца ----------


def test_bare_fighter_punches_with_fists():
    fighter = make()
    assert fighter.weapons == ("кулаком",)
    assert fighter.attacks_per_round == 1
    assert fighter.block_width == BLOCK_WIDTH


def test_weapon_changes_what_the_judge_calls_it():
    fighter = make(equipment=Equipment.from_codes({"weapon": "knuckles"}))
    assert fighter.weapons == ("кастетом",)


def test_shield_widens_the_block():
    fighter = make(equipment=Equipment.from_codes({"shield": "bar_lid"}))
    assert fighter.has_shield
    assert fighter.block_width == SHIELD_BLOCK_WIDTH


def test_class_does_not_change_attacks_or_blocks():
    """В кулачном бою у всех один удар и блок на две зоны."""
    for fclass in FIGHTER_CLASSES.values():
        fighter = make(fclass=fclass)
        assert fighter.attacks_per_round == 1
        assert fighter.block_width == BLOCK_WIDTH
    assert make(fclass=TANK).block_width == make(fclass=WARRIOR).block_width


def test_second_weapon_gives_a_second_strike():
    fighter = make(
        equipment=Equipment(items={Slot.WEAPON: CATALOGUE["knuckles"], Slot.SHIELD: SHIV})
    )
    assert fighter.attacks_per_round == 2
    assert fighter.weapons == ("кастетом", "заточкой")
    assert not fighter.has_shield  # вторая рука занята оружием
    assert fighter.block_width == BLOCK_WIDTH


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


def test_shield_block_is_marked_for_the_story():
    attacker = make(user_id=1)
    defender = make(user_id=2, equipment=Equipment.from_codes({"shield": "bar_lid"}))
    result = resolve_round(
        attacker,
        strike_at(Zone.BELLY, guard(Zone.LEGS)),
        defender,
        strike_at(Zone.LEGS, guard(Zone.HEAD, SHIELD_BLOCK_WIDTH)),
        round_number=1,
        rng=random.Random(2),
    )
    assert result.strikes[0].outcome is Outcome.BLOCK
    assert result.strikes[0].by_shield is True


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


def test_two_weapons_produce_two_strikes():
    attacker = make(
        user_id=1,
        equipment=Equipment(items={Slot.WEAPON: CATALOGUE["knuckles"], Slot.SHIELD: SHIV}),
    )
    defender = make(user_id=2)
    result = resolve_round(
        attacker,
        Action(attacks=(Zone.HEAD, Zone.BELLY), block=guard(Zone.LEGS)),
        defender,
        strike_at(Zone.CHEST, guard(Zone.HEAD)),
        round_number=1,
        rng=random.Random(4),
    )
    mine = [s for s in result.strikes if s.attacker_id == 1]
    assert len(mine) == 2
    assert [s.weapon for s in mine] == ["кастетом", "заточкой"]


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


def test_judge_decision_by_remaining_hp():
    first, second = make(user_id=1), make(user_id=2)
    first.hp, second.hp = 50, 20
    assert judge_decision(first, second) == 1
    second.hp = 50
    assert judge_decision(first, second) is None


def test_round_limit_ends_with_judge_call():
    first, second = make(user_id=1), make(user_id=2)
    first.hp = second.hp = 200
    result = resolve_round(
        first,
        strike_at(Zone.HEAD, guard(Zone.HEAD)),
        second,
        strike_at(Zone.HEAD, guard(Zone.HEAD)),
        round_number=MAX_ROUNDS,
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
        validate_action(Action(attacks=(Zone.HEAD,), block=(Zone.HEAD, Zone.LEGS)), fighter)
    with pytest.raises(ValueError):  # второго оружия нет
        validate_action(Action(attacks=(Zone.HEAD, Zone.CHEST)), fighter)


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
