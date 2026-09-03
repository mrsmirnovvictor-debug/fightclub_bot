"""Бои в мини-аппе: тот же сервис, второй пульт.

Правил здесь нет ни одной строчки — ходы считает `DuelService`. Тесты
следят за тем, что апп видит то же состояние, что и ветка, и что бой без
чата ведёт себя так же, как бой в группе.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats
from bot.game.modes import FightMode
from bot.models import Player
from bot.webapp.server import create_app
from tests.test_duel_flow import FakeBot as DuelBot, make_service
from tests.test_webapp import FakeBot, TOKEN, make_init_data

CHAT_ID = -1001
THREAD_ID = 7


def make_player(user_id: int, nickname: str, class_code: str = "warrior") -> Player:
    stats = Stats(strength=10, agility=8, intuition=6, endurance=10)
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code=class_code,
        level=3,
        **stats.as_dict(),
    )


def headers(user_id: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


@pytest.fixture
async def arena(db):
    """Мини-апп и сервис боёв, поднятые на одной базе."""
    duels = make_service(DuelBot(), db)
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    for user_id, nickname in ((42, "Тайлер"), (43, "Марла")):
        await db.save_player(make_player(user_id, nickname))
    app = create_app(FakeBot(), db, config, duels=duels)
    async with TestClient(TestServer(app)) as client:
        yield client, duels, db
    await duels.shutdown()


async def state(client, user_id: int) -> dict:
    response = await client.get("/api/fights", headers=headers(user_id))
    assert response.status == 200
    return await response.json()


async def act(client, user_id: int, **payload) -> tuple[int, dict]:
    response = await client.post("/api/fight", json=payload, headers=headers(user_id))
    return response.status, await response.json()


# ---------- вызов и приём ----------


async def test_a_fight_is_opened_and_joined_without_leaving_the_app(arena):
    client, duels, _ = arena

    status, mine = await act(client, 42, action="open", mode="armed")
    assert status == 200
    assert mine["challenge"]["mine"]
    assert mine["challenge"]["mode"]["code"] == "armed"
    assert mine["duel"] is None

    # соперник видит вызов в общем списке
    theirs = await state(client, 43)
    assert [row["id"] for row in theirs["challenges"]] == [mine["challenge"]["id"]]
    assert theirs["challenges"][0]["challenger"]["name"] == "Тайлер"

    status, joined = await act(
        client, 43, action="join", challenge_id=mine["challenge"]["id"]
    )
    assert status == 200
    duel = joined["duel"]
    assert duel is not None and duel["in_app"] and duel["started"]
    assert {row["name"] for row in duel["fighters"]} == {"Тайлер", "Марла"}
    assert duel["round"] == 1 and duel["turn"] == 1


async def test_your_own_challenge_is_not_offered_back_to_you(arena):
    client, _, _ = arena
    await act(client, 42, action="open", mode="fist")

    mine = await state(client, 42)

    assert mine["challenges"] == []  # принимать свой вызов нечего
    assert mine["challenge"]["mine"]


async def test_a_challenge_can_be_withdrawn(arena):
    client, duels, _ = arena
    await act(client, 42, action="open", mode="fist")

    status, after = await act(client, 42, action="cancel")

    assert status == 200
    assert after["challenge"] is None
    assert duels.challenge_of_user(42) is None


async def test_a_fight_in_the_app_never_touches_a_chat(arena):
    """У боя из аппа нет ветки: судья не говорит вслух, потому что некому."""
    client, duels, _ = arena
    bot = duels.bot

    await act(client, 42, action="open", mode="fist")
    joined = await act(client, 43, action="join", challenge_id=duels.open_challenges()[0].id)

    assert joined[0] == 200
    duel = duels.duel_of_user(42)
    assert duel.in_app and duel.chat_id is None
    assert bot.sent == [] and bot.edits == []


# ---------- сам бой ----------


async def fight_pair(client, duels) -> dict:
    await act(client, 42, action="open", mode="fist")
    _, joined = await act(
        client, 43, action="join", challenge_id=duels.open_challenges()[0].id
    )
    return joined["duel"]


async def test_a_turn_resolves_as_soon_as_both_have_chosen(arena):
    client, duels, _ = arena
    await fight_pair(client, duels)

    await act(client, 42, action="attack", zone="head")
    _, half = await act(client, 42, action="block", zone="legs")
    assert half["duel"]["round"] == 1 and half["duel"]["turn"] == 1
    assert [row["ready"] for row in half["duel"]["fighters"]] == [True, False]

    await act(client, 43, action="attack", zone="belly")
    _, done = await act(client, 43, action="block", zone="chest")

    # ход посчитан сразу: ни таймера, ни паузы между раундами в аппе нет
    assert done["duel"]["turn"] == 2
    assert len(done["duel"]["log"]) == 1
    assert done["duel"]["resting"] is False


async def test_the_app_shows_what_you_have_already_pressed(arena):
    client, duels, _ = arena
    await fight_pair(client, duels)

    await act(client, 42, action="attack", zone="belt")
    mine = await state(client, 42)

    assert mine["duel"]["chosen"] == {"attack": "belt", "block": None}
    # соперник своего выбора не видит — только то, что боец готов
    theirs = await state(client, 43)
    assert theirs["duel"]["chosen"] == {"attack": None, "block": None}


async def test_the_log_says_who_hit_where(arena):
    """Ради этого лог и нужен: видно, куда бил каждый в свой ход."""
    client, duels, _ = arena
    await fight_pair(client, duels)

    for user_id, zone, block in ((42, "head", "legs"), (43, "belly", "chest")):
        await act(client, user_id, action="attack", zone=zone)
        await act(client, user_id, action="block", zone=block)

    turn = (await state(client, 42))["duel"]["log"][0]

    assert (turn["round"], turn["turn"], turn["number"]) == (1, 1, 1)
    mine = next(s for s in turn["strikes"] if s["attacker_id"] == 42)
    assert (mine["zone"], mine["zone_title"]) == ("head", "Голова")
    assert mine["emoji"] and mine["title"]
    assert next(s for s in turn["strikes"] if s["attacker_id"] == 43)["zone"] == "belly"


async def test_the_buttons_are_the_same_five_zones_as_in_the_chat(arena):
    client, _, _ = arena

    body = await state(client, 42)

    assert [row["title"] for row in body["attacks"]] == [
        "Голова", "Корпус", "Живот", "Пояс", "Ноги"
    ]
    assert [row["title"] for row in body["blocks"]] == [
        "Голова + Корпус",
        "Корпус + Живот",
        "Живот + Пояс",
        "Пояс + Ноги",
        "Ноги + Голова",
    ]
    assert [row["code"] for row in body["modes"]] == [m.value for m in FightMode]


# ---------- апп как второй пульт ----------


async def test_a_fight_started_in_the_group_shows_up_in_the_app(arena):
    """Тот же бой: начали в ветке, продолжили в аппе."""
    client, duels, db = arena
    session = await duels.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(42), await db.get_player(43)
    )

    body = await state(client, 42)

    assert body["duel"]["id"] == session.id
    assert body["duel"]["in_app"] is False
    assert body["duel"]["yours"] is True

    # и нажатие из аппа доходит до того же боя
    await act(client, 42, action="attack", zone="chest")
    assert session.choice_of(42).attack.value == "chest"


async def test_a_stranger_sees_the_fight_but_cannot_press(arena):
    client, duels, db = arena
    await db.save_player(make_player(44, "Зевака"))
    await duels.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(42), await db.get_player(43)
    )

    body = await state(client, 44)
    assert body["duel"] is None  # чужой бой на вкладке не показывается

    status, error = await act(client, 44, action="attack", zone="head")
    assert status == 409
    assert "не на ринге" in error["error"]


async def test_a_busy_fighter_cannot_open_a_second_challenge(arena):
    client, duels, db = arena
    await duels.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(42), await db.get_player(43)
    )

    status, error = await act(client, 42, action="open", mode="fist")

    assert status == 409
    assert error["error"]


async def test_a_character_is_required_to_open_the_tab(arena):
    client, _, _ = arena

    response = await client.get("/api/fights", headers=headers(999))

    assert response.status == 404
