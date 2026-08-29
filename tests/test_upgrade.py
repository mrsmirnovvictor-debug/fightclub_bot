"""Раздача свободных очков: правила одни на лавку в личке и на мини-апп."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats
from bot.game.equipment import CATALOGUE, OwnedItem, Slot
from bot.game.health import now_ts
from bot.game.potions import ActiveEffect
from bot.models import Player
from bot.upgrade_service import UpgradeError, parse_points, spend_points
from bot.webapp.server import create_app
from tests.test_webapp import FakeBot, TOKEN, make_init_data


def make_player(user_id: int = 42, free_points: int = 4, **kwargs) -> Player:
    stats = Stats(strength=12, agility=8, intuition=3, endurance=12)
    return Player(
        user_id=user_id,
        nickname="Тайлер",
        class_code="warrior",
        level=5,
        free_points=free_points,
        **stats.as_dict(),
        **kwargs,
    )


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    async with TestClient(TestServer(create_app(FakeBot(), db, config))) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


# ---------- разбор заявки ----------


def test_points_are_read_as_numbers_and_never_as_a_way_back():
    assert parse_points({"strength": 2, "intuition": 1}).as_dict() == {
        "strength": 2,
        "agility": 0,
        "intuition": 1,
        "endurance": 0,
    }
    with pytest.raises(UpgradeError, match="только добавлять"):
        parse_points({"strength": -3})
    with pytest.raises(UpgradeError, match="Непонятно"):
        parse_points({"strength": "много"})


# ---------- сама раздача ----------


async def test_points_land_in_the_stats_the_fighter_grew_himself(db):
    player = make_player()
    await db.save_player(player)

    await spend_points(db, player, {"strength": 2, "endurance": 1})

    fresh = await db.get_player(42)
    assert (fresh.strength, fresh.endurance) == (14, 13)
    assert fresh.free_points == 1


async def test_gear_and_potions_never_leak_into_the_base(db):
    """Меч даёт +5 интуиции, эликсир +10 силы — в базу они попасть не должны.

    Иначе прибавку можно «сдать» в характеристики, снять вещь и оставить её
    себе навсегда, а на следующем уровне повторить.
    """
    player = make_player()
    await db.save_player(player)
    saber = await db.add_gear(42, "lightsaber")
    saber.slot = Slot.WEAPON
    await db.save_gear(saber)
    player.gear = [saber]
    player.effects = [ActiveEffect(code="boost_strength", until=now_ts() + 3600)]
    assert (player.stats.strength, player.stats.intuition) == (22, 8)

    await spend_points(db, player, {"strength": 1})

    fresh = await db.get_player(42)
    assert (fresh.strength, fresh.intuition) == (13, 3)  # своё плюс одно очко
    assert fresh.stats.intuition == 8  # меч по-прежнему добавляет свои пять


async def test_you_cannot_spend_points_you_do_not_have(db):
    player = make_player(free_points=2)
    await db.save_player(player)

    with pytest.raises(UpgradeError, match="Столько очков нет"):
        await spend_points(db, player, {"strength": 2, "agility": 1})

    fresh = await db.get_player(42)
    assert fresh.strength == 12 and fresh.free_points == 2


async def test_an_empty_hand_is_refused(db):
    player = make_player()
    await db.save_player(player)

    with pytest.raises(UpgradeError, match="ни одного очка"):
        await spend_points(db, player, {})
    with pytest.raises(UpgradeError, match="ни одного очка"):
        await spend_points(db, player, {"strength": 0})


async def test_spending_everything_leaves_nothing(db):
    player = make_player(free_points=3)
    await db.save_player(player)

    await spend_points(db, player, {"agility": 1, "intuition": 1, "endurance": 1})

    fresh = await db.get_player(42)
    assert (fresh.agility, fresh.intuition, fresh.endurance) == (9, 4, 13)
    assert fresh.free_points == 0


# ---------- мини-апп ----------


async def test_the_mini_app_hands_out_points_and_returns_a_fresh_card(client, db):
    await db.save_player(make_player())

    response = await client.post(
        "/api/upgrade", json={"strength": 2, "intuition": 1}, headers=headers()
    )
    body = await response.json()

    assert response.status == 200
    assert body["spent"] == {
        "strength": 2,
        "agility": 0,
        "intuition": 1,
        "endurance": 0,
    }
    assert body["left"] == 1
    stats = {row["code"]: row["base"] for row in body["card"]["stats"]}
    assert stats["strength"] == 14 and stats["intuition"] == 4
    assert body["card"]["progress"]["free_points"] == 1


async def test_the_mini_app_refuses_more_than_there_is(client, db):
    await db.save_player(make_player(free_points=1))

    response = await client.post(
        "/api/upgrade", json={"strength": 5}, headers=headers()
    )

    assert response.status == 409
    assert "Столько очков нет" in (await response.json())["error"]
    assert (await db.get_player(42)).strength == 12


async def test_the_mini_app_refuses_negative_points(client, db):
    """Минусом характеристики не вынимают: это был бы бесплатный респек."""
    await db.save_player(make_player())

    response = await client.post(
        "/api/upgrade", json={"strength": 5, "agility": -5}, headers=headers()
    )

    assert response.status == 409
    assert (await db.get_player(42)).free_points == 4


async def test_nobody_redistributes_stats_in_the_middle_of_a_fight(db):
    """Та же дверь, что и у переодевания: на ринге не до характеристик."""
    from tests.test_duel_flow import FakeBot as DuelBot, make_service

    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    duels = make_service(DuelBot(), db)
    for user_id, nickname in ((42, "Тайлер"), (43, "Марла")):
        player = make_player(user_id=user_id)
        player.nickname = nickname
        await db.save_player(player)
    await duels.start_duel(
        -100, 7, await db.get_player(42), await db.get_player(43)
    )

    app = create_app(FakeBot(), db, config, duels=duels)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/upgrade", json={"strength": 1}, headers=headers()
        )
        # тело читаем до закрытия клиента, иначе соединение уже оборвано
        body = await response.json()

    assert response.status == 409
    assert "ринге" in body["error"]
    assert (await db.get_player(42)).strength == 12
    await duels.shutdown()


def test_a_stranger_card_carries_no_points_to_spend():
    """Панель раздачи рисуется по своей карточке: у чужой очков нет."""
    from bot.webapp.card import build_card

    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["lightsaber"], id=1, slot=Slot.WEAPON)]

    mine = build_card(player, TOKEN, viewer_id=42)
    theirs = build_card(player, TOKEN, viewer_id=999)

    assert mine["is_self"] and mine["progress"]["free_points"] == 4
    assert not theirs["is_self"]
