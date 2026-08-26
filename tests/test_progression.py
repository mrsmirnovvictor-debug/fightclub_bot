"""Уровни, опыт и хранилище."""


from bot.game.classes import POINTS_PER_LEVEL
from bot.models import Player, exp_to_next_level


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


def test_level_up_grants_points():
    player = make_player()
    gained = player.grant_exp(exp_to_next_level(1))
    assert gained == 1
    assert player.level == 2
    assert player.free_points == POINTS_PER_LEVEL
    assert player.exp == 0


def test_multiple_level_ups_at_once():
    player = make_player()
    gained = player.grant_exp(exp_to_next_level(1) + exp_to_next_level(2) + 10)
    assert gained == 2
    assert player.level == 3
    assert player.exp == 10
    assert player.free_points == 2 * POINTS_PER_LEVEL


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


async def test_top_players_sorted_by_wins(db):
    await db.save_player(make_player(user_id=1, nickname="Первый", wins=5))
    await db.save_player(make_player(user_id=2, nickname="Второй", wins=9))
    top = await db.top_players(10)
    assert [p.nickname for p in top] == ["Второй", "Первый"]


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
