"""Здоровье между боями: сохранение, восстановление и допуск на ринг."""

import pytest

from bot.game.health import (
    FULL_REGEN_SECONDS,
    HURT_THRESHOLD,
    READY_THRESHOLD,
    HealthState,
    format_duration,
    health_state,
    regenerated_hp,
    seconds_until_full,
    seconds_until_ready,
)
from bot.models import Player

NOW = 1_700_000_000


def make_player(**kwargs) -> Player:
    data = dict(
        user_id=1,
        nickname="Тайлер",
        class_code="warrior",
        strength=4,
        agility=3,
        intuition=3,
        endurance=4,
    )
    data.update(kwargs)
    return Player(**data)


def test_fresh_player_is_healthy():
    player = make_player()
    assert player.hp is None
    assert player.current_hp(NOW) == player.max_hp
    assert player.can_fight(NOW)


def test_full_regeneration_takes_exactly_the_declared_time():
    player = make_player()
    player.set_hp(0, now=NOW)
    assert player.current_hp(NOW) == 0
    assert player.current_hp(NOW + FULL_REGEN_SECONDS) == player.max_hp
    # на полпути — примерно половина запаса
    half = player.current_hp(NOW + FULL_REGEN_SECONDS // 2)
    assert player.max_hp // 2 - 1 <= half <= player.max_hp // 2 + 1


def test_regeneration_never_overshoots():
    player = make_player()
    player.set_hp(10, now=NOW)
    assert player.current_hp(NOW + FULL_REGEN_SECONDS * 10) == player.max_hp
    assert regenerated_hp(50, 60, -100) == 50  # время назад не отматывается


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (0.0, HealthState.HURT),
        (0.19, HealthState.HURT),
        (0.2, HealthState.RECOVERING),
        (0.79, HealthState.RECOVERING),
        (0.8, HealthState.READY),
        (1.0, HealthState.READY),
    ],
)
def test_health_zones(percent, expected):
    assert health_state(int(100 * percent), 100) is expected


def test_only_the_green_zone_may_fight():
    assert HealthState.READY.can_fight
    assert not HealthState.RECOVERING.can_fight
    assert not HealthState.HURT.can_fight


def test_player_cannot_fight_until_the_green_zone():
    player = make_player()
    player.set_hp(0, now=NOW)
    assert not player.can_fight(NOW)

    wait = player.seconds_until_ready(NOW)
    assert not player.can_fight(NOW + wait - 1)
    assert player.can_fight(NOW + wait)
    # с нуля до зелёной зоны — восемь минут из десяти, ни секундой меньше
    assert FULL_REGEN_SECONDS * READY_THRESHOLD <= wait <= FULL_REGEN_SECONDS * 0.85


def test_countdown_never_promises_a_fight_too_early():
    """Обратный отсчёт должен совпадать с моментом реального допуска."""
    for endurance in range(2, 12):
        player = make_player(endurance=endurance)
        for stored in (0, 5, 17, 40):
            player.set_hp(stored, now=NOW)
            wait = player.seconds_until_ready(NOW)
            if wait == 0:
                assert player.can_fight(NOW)
                continue
            assert not player.can_fight(NOW + wait - 1), (endurance, stored)
            assert player.can_fight(NOW + wait), (endurance, stored)


def test_a_scratched_fighter_may_enter_the_ring():
    """Зелёная зона пускает на ринг даже с неполным здоровьем."""
    player = make_player()
    player.set_hp(int(player.max_hp * 0.85), now=NOW)
    assert player.can_fight(NOW)
    assert player.current_hp(NOW) < player.max_hp
    assert player.seconds_until_full(NOW) > 0


def test_stored_hp_is_clamped_to_the_current_maximum():
    """Респек в слабую выносливость не должен оставлять здоровья больше запаса."""
    player = make_player(endurance=10)
    player.set_hp(player.max_hp, now=NOW)
    stored = player.hp
    player.endurance = 2  # запас упал
    assert stored > player.max_hp
    assert player.current_hp(NOW) == player.max_hp


def test_seconds_to_full_and_ready_are_zero_when_healthy():
    player = make_player()
    assert player.seconds_until_ready(NOW) == 0
    assert player.seconds_until_full(NOW) == 0
    assert seconds_until_ready(80, 100) == 0
    assert seconds_until_full(100, 100) == 0


def test_hurt_threshold_is_the_red_zone_border():
    max_hp = 100
    assert health_state(int(HURT_THRESHOLD * max_hp) - 1, max_hp) is HealthState.HURT
    assert health_state(int(HURT_THRESHOLD * max_hp), max_hp) is HealthState.RECOVERING


def test_duration_reads_like_a_human_wrote_it():
    assert format_duration(45) == "45 сек"
    assert format_duration(120) == "2 мин"
    assert format_duration(135) == "2 мин 15 сек"
    assert format_duration(-5) == "0 сек"


async def test_health_survives_a_database_roundtrip(db):
    player = make_player()
    player.set_hp(12, now=NOW)
    await db.save_player(player)

    loaded = await db.get_player(player.user_id)
    assert loaded.hp == 12
    assert loaded.hp_at == NOW
    assert loaded.current_hp(NOW + 60) > 12


async def test_old_rows_without_health_are_treated_as_healthy(db):
    player = make_player()
    await db.save_player(player)
    loaded = await db.get_player(player.user_id)
    assert loaded.hp is None
    assert loaded.can_fight(NOW)
