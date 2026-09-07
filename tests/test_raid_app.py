"""Рейд в мини-аппе: тот же сервис, второй пульт.

Правил здесь нет — волны считает `RaidService`. Тесты следят за тем, что
апп видит то же состояние, что и ветка, и что рейд, собранный в аппе, идёт
так же, как собранный командой в группе.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.raid import BOSS_ID, MAX_PARTY
from bot.webapp.server import create_app
from tests.test_duel_flow import FakeBot as DuelBot
from tests.test_raid import make_service
from tests.test_fight_app import headers, make_player
from tests.test_webapp import FakeBot, TOKEN


@pytest.fixture
async def cellar(db):
    """Мини-апп и сервис рейдов на одной базе."""
    raids = make_service(DuelBot(), db)
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    for user_id, nickname in ((42, "Тайлер"), (43, "Марла"), (44, "Зевака")):
        await db.save_player(make_player(user_id, nickname))
    app = create_app(FakeBot(), db, config, raids=raids)
    async with TestClient(TestServer(app)) as client:
        yield client, raids, db
    await raids.shutdown()


async def state(client, user_id: int) -> dict:
    response = await client.get("/api/raid", headers=headers(user_id))
    assert response.status == 200
    return await response.json()


async def act(client, user_id: int, **payload) -> tuple[int, dict]:
    response = await client.post("/api/raid", json=payload, headers=headers(user_id))
    return response.status, await response.json()


async def start(client, raids, size: int = 2) -> dict:
    """Собрать рейд в аппе и добить его до полного отряда."""
    await act(client, 42, action="open", size=size)
    lobby = raids.lobby_of_user(42)
    _, body = await act(client, 43, action="join", lobby_id=lobby.id)
    return body


# ---------- сбор ----------


async def test_a_raid_is_gathered_without_leaving_the_app(cellar):
    client, raids, _ = cellar

    status, mine = await act(client, 42, action="open", size=2)

    assert status == 200
    assert mine["lobby"]["mine"] and mine["lobby"]["total"] == 1
    assert mine["lobby"]["boss"]["title"] == "Босс Подвала"
    assert mine["raid"] is None

    # чужой сбор виден остальным
    theirs = await state(client, 43)
    assert [row["id"] for row in theirs["lobbies"]] == [mine["lobby"]["id"]]

    status, joined = await act(
        client, 43, action="join", lobby_id=mine["lobby"]["id"]
    )

    assert status == 200
    assert joined["raid"] is not None  # отряд полон — спустились в подвал
    assert joined["raid"]["wave"] == 1
    assert {row["name"] for row in joined["raid"]["party"]} == {"Тайлер", "Марла"}


async def test_the_party_can_be_left(cellar):
    client, raids, _ = cellar
    await act(client, 42, action="open", size=3)

    status, after = await act(client, 42, action="leave")

    assert status == 200
    assert after["lobby"] is None
    assert raids.lobby_of_user(42) is None


async def test_the_party_size_is_checked_by_the_server(cellar):
    client, _, _ = cellar

    status, error = await act(client, 42, action="open", size=MAX_PARTY + 5)

    assert status == 409
    assert "от 2 до 10" in error["error"]


# ---------- сам рейд ----------


async def test_a_turn_is_sent_as_one_move(cellar):
    """Кнопка «Вперёд!» шлёт удар и блок разом — как в бою."""
    client, raids, _ = cellar
    await start(client, raids)
    before = raids.raid_of_user(42).enemy.hp

    status, body = await act(client, 42, action="turn", attack="head", block="belt")

    assert status == 200
    raid = body["raid"]
    assert raid["acted"] is True
    assert raid["boss"]["hp"] <= before
    # соперник ещё думает: волна не закрыта
    assert [row["acted"] for row in raid["party"]] == [True, False]


async def test_half_a_turn_is_refused(cellar):
    client, raids, _ = cellar
    await start(client, raids)

    status, body = await act(client, 42, action="turn", attack="head")

    assert status == 409
    assert "и удар, и блок" in body["error"]
    assert 42 not in raids.raid_of_user(42).acted


async def test_the_log_says_who_hit_where(cellar):
    client, raids, _ = cellar
    await start(client, raids)

    await act(client, 42, action="turn", attack="head", block="belt")
    turn = (await state(client, 42))["raid"]["log"][0]

    assert turn["lines"], "судья промолчал"
    mine = next(s for s in turn["strikes"] if s["attacker_id"] == 42)
    assert (mine["zone"], mine["zone_title"]) == ("head", "Голова")
    assert any(s["attacker_id"] == BOSS_ID for s in turn["strikes"])


async def test_a_stranger_sees_nothing_and_cannot_press(cellar):
    client, raids, _ = cellar
    await start(client, raids)

    body = await state(client, 44)
    assert body["raid"] is None

    status, error = await act(client, 44, action="turn", attack="head", block="belt")
    assert status == 409
    assert "не в рейде" in error["error"]


async def test_the_result_holds_the_screen_until_it_is_closed(cellar):
    client, raids, _ = cellar
    await start(client, raids)
    session = raids.raid_of_user(42)
    session.enemy.hp = 1

    await act(client, 42, action="turn", attack="head", block="belt")

    raid = (await state(client, 42))["raid"]
    assert raid["finished"] is True
    summary = "\n".join(raid["summary"])
    assert "Босс повержен" in summary and "Кто сколько набил" in summary
    assert "<b>" not in summary

    status, after = await act(client, 42, action="done")

    assert status == 200 and after["raid"] is None


async def test_the_history_of_raids_is_open_to_read(cellar):
    client, raids, db = cellar
    await start(client, raids)
    session = raids.raid_of_user(42)
    session.enemy.hp = 1
    await act(client, 42, action="turn", attack="head", block="belt")

    response = await client.get("/api/raids?user_id=42", headers=headers(43))
    body = await response.json()

    assert response.status == 200
    row = body["raids"][0]
    assert row["boss"] == "Босс Подвала"
    # В списке боёв важно не «босс повержен», а что вышло у тебя
    assert row["result"] == "win" and row["result_title"] == "Победа"
    assert row["caption"] == "Победа (с Марла) — рейд против Босса Подвала"
    assert row["verdict"] == "Босс повержен"
    assert row["damage"] > 0 and row["waves"] == 1


async def test_a_raid_lands_in_the_list_of_fights(cellar):
    """Рейд стоит в статистике рядом с дуэлями и читается так же."""
    client, raids, db = cellar
    await start(client, raids)
    raids.raid_of_user(42).enemy.hp = 1
    await act(client, 42, action="turn", attack="head", block="belt")

    response = await client.get("/api/history?user_id=42", headers=headers(42))
    body = await response.json()

    row = body["days"][0]["fights"][0]
    assert row["kind"] == "raid"
    assert row["caption"] == "Победа (с Марла) — рейд против Босса Подвала"
    assert row["waves"] == 1 and row["damage"] > 0
    assert body["total"] == 1 and body["counts"]["win"] == 1


async def test_the_boss_card_comes_with_the_section(cellar):
    """Кнопка «i» рисуется по тому, что пришло вместе с разделом."""
    client, raids, _ = cellar

    idle = (await state(client, 42))["boss"]

    assert idle["live"] is False
    assert idle["title"] == "Босс Подвала"
    assert idle["level"] > 0 and idle["max_hp"] > 0
    assert idle["weapon"] == "Кувалда"
    assert len(idle["kit"]) == 8
    assert idle["combat"]["resist"] > 0

    await start(client, raids)
    live = (await state(client, 42))["boss"]

    assert live["live"] is True
    assert live["level"] == raids.raid_of_user(42).enemy.level


async def test_the_boss_stands_in_slots_like_a_fighter(cellar):
    """Кукла босса собирается тем же payload, что и карточка бойца."""
    client, _, _ = cellar

    boss = (await state(client, 42))["boss"]

    assert boss["avatar"]["url"].endswith("bosses/cellar_boss.png")
    left = boss["slots"]["left"]
    right = boss["slots"]["right"]
    assert [row["slot"] for row in left] == ["head", "weapon", "shirt", "belt"]
    assert [row["slot"] for row in right] == ["gloves", "jacket", "pants", "boots"]
    # у босса заняты все восемь, но форма слота та же, что у пустого
    assert all(row["item"] for row in left + right)
    assert all(row["placeholder_image"] for row in left + right)
    assert next(row for row in left if row["slot"] == "weapon")["item"]["title"] == (
        "Кувалда"
    )
