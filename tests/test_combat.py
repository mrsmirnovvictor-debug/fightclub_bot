"""Тесты боевого движка."""

import random

import pytest

from bot.game.classes import FIGHTER_CLASSES, TANK, WARRIOR, Zone
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
from bot.game.stats import derive


def make(fclass=WARRIOR, user_id=1, name="Боец", **kwargs):
    return Fighter(user_id=user_id, name=name, fclass=fclass, stats=fclass.base_stats, **kwargs)


def test_fighter_starts_with_full_hp():
    fighter = make()
    assert fighter.hp == fighter.max_hp == derive(WARRIOR, WARRIOR.base_stats).max_hp
    assert fighter.alive


def test_block_absorbs_damage_completely():
    attacker, defender = make(user_id=1), make(user_id=2)
    result = resolve_round(
        attacker,
        Action(attack=Zone.HEAD, blocks=(Zone.LEGS, Zone.BELT)),
        defender,
        Action(attack=Zone.BELLY, blocks=(Zone.HEAD, Zone.CHEST)),
        round_number=1,
        rng=random.Random(1),
    )
    strike = result.strikes[0]
    assert strike.outcome is Outcome.BLOCK
    assert strike.damage == 0
    # защитник бил в незакрытую зону — там урон есть
    assert result.strikes[1].outcome is not Outcome.BLOCK


def test_unblocked_hit_deals_damage():
    attacker, defender = make(user_id=1), make(user_id=2)
    start_hp = defender.hp
    resolve_round(
        attacker,
        Action(attack=Zone.HEAD, blocks=(Zone.LEGS, Zone.BELT)),
        defender,
        Action(attack=Zone.HEAD, blocks=(Zone.CHEST, Zone.BELLY)),
        round_number=1,
        rng=random.Random(3),
    )
    assert defender.hp < start_hp


def test_damage_is_simultaneous():
    """Оба удара считаются от состояния на начало раунда."""
    attacker = make(user_id=1)
    defender = make(user_id=2)
    attacker.hp = defender.hp = 1
    result = resolve_round(
        attacker,
        Action(attack=Zone.HEAD, blocks=(Zone.LEGS, Zone.BELT)),
        defender,
        Action(attack=Zone.CHEST, blocks=(Zone.BELLY, Zone.LEGS)),
        round_number=1,
        rng=random.Random(0),
    )
    assert result.finished
    # добили друг друга за один ход — ничья, без тай-брейка
    assert result.end_reason is DuelEnd.DOUBLE_KO
    assert result.winner_id is None


def test_ko_finishes_duel():
    attacker, defender = make(user_id=1), make(user_id=2)
    defender.hp = 1
    result = resolve_round(
        attacker,
        Action(attack=Zone.HEAD, blocks=(Zone.HEAD, Zone.CHEST)),
        defender,
        Action(attack=Zone.LEGS, blocks=(Zone.BELLY, Zone.LEGS)),
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
        Action(attack=Zone.HEAD, blocks=(Zone.HEAD, Zone.CHEST)),
        second,
        Action(attack=Zone.HEAD, blocks=(Zone.HEAD, Zone.CHEST)),
        round_number=MAX_ROUNDS,
        rng=random.Random(7),
    )
    assert result.finished
    assert result.end_reason is DuelEnd.JUDGE


def test_fatigue_only_after_threshold():
    assert fatigue_multiplier(1) == 1.0
    assert fatigue_multiplier(6) == 1.0
    assert fatigue_multiplier(10) > fatigue_multiplier(7) > 1.0


def test_tank_blocks_three_zones():
    action = random_action(TANK, random.Random(2))
    assert len(set(action.blocks)) == 3
    validate_action(action, TANK)


def test_partial_choice_is_allowed_but_excess_blocks_are_not():
    # неполный выбор легитимен: боец просто не успел нажать всё
    validate_action(Action(attack=Zone.HEAD, blocks=(Zone.HEAD,)), WARRIOR)
    validate_action(Action(), WARRIOR)
    with pytest.raises(ValueError):
        validate_action(
            Action(attack=Zone.HEAD, blocks=(Zone.HEAD, Zone.CHEST, Zone.LEGS)),
            WARRIOR,
        )


def test_fighter_without_attack_zone_does_not_strike():
    attacker, defender = make(user_id=1), make(user_id=2)
    start_hp = defender.hp
    result = resolve_round(
        attacker,
        Action(blocks=(Zone.HEAD, Zone.CHEST)),  # только защита
        defender,
        Action(attack=Zone.LEGS, blocks=(Zone.BELLY, Zone.BELT)),
        round_number=1,
        rng=random.Random(4),
    )
    assert result.strikes[0].outcome is Outcome.SKIP
    assert result.strikes[0].missed_turn is False  # блоки-то он выбрал
    assert defender.hp == start_hp
    assert attacker.missed_turns == 0


def test_unchosen_block_zone_stays_open():
    attacker, defender = make(user_id=1), make(user_id=2)
    result = resolve_round(
        attacker,
        Action(attack=Zone.BELT, blocks=(Zone.HEAD, Zone.CHEST)),
        defender,
        Action(attack=Zone.HEAD, blocks=(Zone.HEAD,)),  # закрыта одна зона из двух
        round_number=1,
        rng=random.Random(11),
    )
    # удар в пояс прошёл мимо блока: защитник его не закрыл
    assert result.strikes[0].outcome is not Outcome.BLOCK


def test_empty_action_counts_as_missed_turn():
    attacker, defender = make(user_id=1), make(user_id=2)
    result = resolve_round(
        attacker,
        Action(),
        defender,
        Action(attack=Zone.HEAD, blocks=(Zone.HEAD, Zone.CHEST)),
        round_number=1,
        rng=random.Random(6),
    )
    assert attacker.missed_turns == 1
    assert result.strikes[0].missed_turn is True
    assert not result.finished


def test_three_missed_turns_end_the_duel():
    absent, active = make(user_id=1), make(user_id=2)
    active_action = Action(attack=Zone.BELT, blocks=(Zone.HEAD, Zone.CHEST))
    rng = random.Random(9)
    for round_number in range(1, MAX_MISSED_TURNS + 1):
        result = resolve_round(absent, Action(), active, active_action, round_number, rng)
    assert absent.missed_turns == MAX_MISSED_TURNS
    assert result.finished
    assert result.end_reason is DuelEnd.TECHNICAL
    assert result.winner_id == 2


def test_any_press_resets_the_missed_turn_counter():
    fighter, opponent = make(user_id=1), make(user_id=2)
    opponent_action = Action(attack=Zone.BELT, blocks=(Zone.HEAD, Zone.CHEST))
    rng = random.Random(13)
    resolve_round(fighter, Action(), opponent, opponent_action, 1, rng)
    resolve_round(fighter, Action(), opponent, opponent_action, 2, rng)
    assert fighter.missed_turns == 2
    # успел нажать хотя бы блок — счётчик обнуляется
    resolve_round(fighter, Action(blocks=(Zone.HEAD,)), opponent, opponent_action, 3, rng)
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
                random_action(fclass, rng),
                second,
                random_action(opponent, rng),
                round_number,
                rng,
            )
            if result.finished:
                break
        else:  # pragma: no cover
            pytest.fail(f"{code} vs {opponent_code}: бой не закончился")
        assert result.end_reason is not None
