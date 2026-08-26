"""Уровни, опыт, кредиты, рейтинг и хранилище."""

import pytest


from bot.game.classes import get_class
from bot.game.economy import (
    LEVEL_CREDITS,
    MAX_LEVEL,
    MICRO_UPS_PER_LEVEL,
    POINTS_PER_UP,
    UP_CREDITS,
    exp_to_next_level,
)
from bot.models import Player


def make_player(user_id=1, **kwargs):
    data = dict(
        user_id=user_id,
        nickname="Тайлер",
        class_code="warrior",
        strength=4,
        agility=3,
        intuition=3,
        endurance=4,
    )
    data.update(kwargs)
    return Player(**data)


def test_exp_curve_grows():
    assert exp_to_next_level(1) < exp_to_next_level(2) < exp_to_next_level(3)


def test_quarter_of_a_level_gives_an_up():
    player = make_player()
    report = player.grant_exp(exp_to_next_level(1) // MICRO_UPS_PER_LEVEL)
    assert report.ups == 1
    assert report.levels == 0
    assert player.free_points == POINTS_PER_UP
    assert player.credits == UP_CREDITS
    assert player.micro_ups == 1


def test_level_up_grants_four_ups_and_level_credits():
    player = make_player()
    report = player.grant_exp(exp_to_next_level(1))
    assert report.levels == 1
    assert player.level == 2
    assert player.micro_ups == 0
    # четыре апа на уровень: три промежуточных плюс сам уровень
    assert player.free_points == MICRO_UPS_PER_LEVEL * POINTS_PER_UP
    assert player.credits == MICRO_UPS_PER_LEVEL * UP_CREDITS + LEVEL_CREDITS


def test_big_win_does_not_swallow_ups():
    """Перескок через четверть и уровень разом не должен съедать апы."""
    fast = make_player(user_id=1)
    slow = make_player(user_id=2)
    fast.grant_exp(exp_to_next_level(1) * 2)
    for _ in range(8):
        slow.grant_exp(exp_to_next_level(1) // 4)
    assert fast.level == slow.level
    assert fast.free_points == slow.free_points
    assert fast.credits == slow.credits


def test_multiple_level_ups_at_once():
    player = make_player()
    report = player.grant_exp(exp_to_next_level(1) + exp_to_next_level(2) + 10)
    assert report.levels == 2
    assert player.level == 3
    assert player.exp == 10
    assert player.free_points == 2 * MICRO_UPS_PER_LEVEL * POINTS_PER_UP


def test_progress_is_identical_regardless_of_exp_chunk_size():
    results = set()
    for chunk in (30, 63, 70, 100, 500):
        player = make_player()
        while not player.at_max_level:
            player.grant_exp(chunk)
        results.add((player.free_points, player.credits))
    assert len(results) == 1
    points, credits = results.pop()
    assert points == (MAX_LEVEL - 1) * MICRO_UPS_PER_LEVEL
    assert credits == (MAX_LEVEL - 1) * (
        MICRO_UPS_PER_LEVEL * UP_CREDITS + LEVEL_CREDITS
    )


def test_exp_keeps_piling_up_after_the_cap():
    player = make_player(level=MAX_LEVEL)
    before_points = player.free_points
    report = player.grant_exp(500)
    assert report.capped
    assert report.levels == 0 and report.ups == 0
    assert player.level == MAX_LEVEL
    assert player.total_exp == 500
    assert player.free_points == before_points


def test_respec_returns_every_spent_point():
    player = make_player(strength=9, agility=5, intuition=3, endurance=6)
    base = player.fclass.base_stats
    spent = player.spent_points
    returned = player.reset_stats()
    assert returned == spent
    assert player.stats == base
    assert player.free_points == spent


def test_switch_class_rebases_stats_and_returns_points():
    player = make_player(strength=9, agility=5, intuition=3, endurance=6)
    spent = player.spent_points
    player.switch_class("tank")
    assert player.class_code == "tank"
    assert player.stats == get_class("tank").base_stats
    assert player.free_points == spent


def test_paying_more_than_you_have_is_refused():
    player = make_player(credits=10)
    assert not player.can_afford(60)
    with pytest.raises(ValueError):
        player.pay(60)
    player.grant_credits(50)
    player.pay(60)
    assert player.credits == 0


def test_rating_never_goes_below_zero():
    player = make_player(rating=10)
    player.apply_rating(-40)
    assert player.rating == 0


def test_stats_and_class_are_derived_from_row():
    player = make_player(agility=7)
    assert player.stats.agility == 7
    assert player.fclass.code == "warrior"


async def test_player_roundtrip(db):
    player = make_player(nickname="Марла", avatar="🥷", wins=2)
    await db.save_player(player)
    loaded = await db.get_player(player.user_id)
    assert loaded is not None
    assert loaded.nickname == "Марла"
    assert loaded.avatar == "🥷"
    assert loaded.wins == 2
    assert loaded.stats == player.stats


async def test_save_player_updates_existing_row(db):
    player = make_player()
    await db.save_player(player)
    player.wins += 1
    player.level = 5
    await db.save_player(player)
    loaded = await db.get_player(player.user_id)
    assert (loaded.wins, loaded.level) == (1, 5)


async def test_top_players_sorted_by_rating(db):
    await db.save_player(make_player(user_id=1, nickname="Первый", rating=1100, wins=9))
    await db.save_player(make_player(user_id=2, nickname="Второй", rating=1200, wins=2))
    top = await db.top_players(10)
    assert [p.nickname for p in top] == ["Второй", "Первый"]


async def test_repeat_fights_of_a_pair_are_counted(db):
    for _ in range(3):
        await db.add_duel(-100, None, 1, 2, 1, 5, "ko")
    await db.add_duel(-100, None, 1, 3, 1, 5, "ko")
    # порядок бойцов в паре не важен
    assert await db.count_recent_duels_between(1, 2) == 3
    assert await db.count_recent_duels_between(2, 1) == 3
    assert await db.count_recent_duels_between(1, 3) == 1
    assert await db.count_recent_duels_between(2, 3) == 0


async def test_economy_fields_survive_a_roundtrip(db):
    player = make_player(credits=125, rating=1180, total_exp=900, micro_ups=2)
    player.exp = 40
    await db.save_player(player)
    loaded = await db.get_player(player.user_id)
    assert (loaded.credits, loaded.rating) == (125, 1180)
    assert (loaded.total_exp, loaded.micro_ups, loaded.exp) == (900, 2, 40)


async def test_arena_binding(db):
    await db.set_arena(-100, 12, "Ринг")
    arena = await db.get_arena(-100)
    assert arena.thread_id == 12
    await db.set_arena(-100, 15, "Новый ринг")
    arena = await db.get_arena(-100)
    assert (arena.thread_id, arena.title) == (15, "Новый ринг")


async def test_delete_player(db):
    await db.save_player(make_player())
    await db.delete_player(1)
    assert await db.get_player(1) is None
