"""Групповой бой в мини-аппе: тот же сервис, второй пульт.

Правил здесь нет — раунды считает `BattleService`. Тесты следят за тем,
что состав можно собрать не выходя из карточки и что ход уходит одной
кнопкой: удар каждой рукой и блок разом.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.battle import BLUE, RED
from bot.webapp.server import create_app
from tests.test_battle_flow import make_service
from tests.test_duel_flow import FakeBot as DuelBot
from tests.test_fight_app import headers, make_player
from tests.test_webapp import FakeBot, TOKEN

# Бойцы клуба: командный бой берёт четверых, мясорубка — от троих
CLUB = ((42, "Тайлер"), (43, "Марла"), (44, "Боб"), (45, "Ангел"), (46, "Зевака"))
TEAM_OF_TWO = (43, RED), (44, BLUE), (45, BLUE)


@pytest.fixture
async def club(db):
    """Мини-апп и сервис групповых боёв на одной базе."""
    battles = make_service(DuelBot(), db)
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    for user_id, nickname in CLUB:
        player = make_player(user_id, nickname)
        player.level = 5
        await db.save_player(player)
    app = create_app(FakeBot(), db, config, battles=battles)
    async with TestClient(TestServer(app)) as client:
        yield client, battles, db
    await battles.shutdown()


async def state(client, user_id: int) -> dict:
    response = await client.get("/api/battle", headers=headers(user_id))
    assert response.status == 200
    return await response.json()


async def act(client, user_id: int, **payload) -> tuple[int, dict]:
    response = await client.post("/api/battle", json=payload, headers=headers(user_id))
    return response.status, await response.json()


async def start_team(client, battles) -> dict:
    """Собрать бой двое на двое целиком в аппе."""
    await act(client, 42, action="open", kind="team", size=2)
    lobby = battles.lobby_of_user(42)
    body: dict = {}
    for user_id, team in TEAM_OF_TWO:
        _, body = await act(client, user_id, action="join", lobby_id=lobby.id, team=team)
    return body


async def start_royale(client, battles, size: int = 4) -> dict:
    await act(client, 42, action="open", kind="royale", size=size)
    lobby = battles.lobby_of_user(42)
    body: dict = {}
    for user_id, _ in CLUB[1:size]:
        _, body = await act(client, user_id, action="join", lobby_id=lobby.id)
    return body


# ---------- сбор ----------


async def test_a_team_is_gathered_without_leaving_the_app(club):
    client, battles, _ = club

    status, mine = await act(client, 42, action="open", kind="team", size=2)

    assert status == 200
    assert mine["lobby"]["mine"] and mine["lobby"]["total"] == 1
    assert mine["lobby"]["kind_title"] == "Командный бой"
    assert mine["battle"] is None

    # чужой сбор виден остальным
    theirs = await state(client, 43)
    assert [row["id"] for row in theirs["lobbies"]] == [mine["lobby"]["id"]]

    lobby_id = mine["lobby"]["id"]
    for user_id, team in TEAM_OF_TWO:
        status, body = await act(
            client, user_id, action="join", lobby_id=lobby_id, team=team
        )
        assert status == 200

    assert body["battle"] is not None  # состав полон — гонг
    assert body["battle"]["round"] == 1
    assert len(body["battle"]["party"]) == 4
    assert {row["team"] for row in body["battle"]["party"]} == {RED, BLUE}


async def test_the_royale_takes_everyone_without_sides(club):
    client, battles, _ = club

    body = await start_royale(client, battles, size=3)

    battle = body["battle"]
    assert battle["kind"] == "royale"
    assert {row["team"] for row in battle["party"]} == {RED}
    assert len(battle["party"]) == 3


async def test_the_lineup_can_be_left(club):
    client, battles, _ = club
    await act(client, 42, action="open", kind="team", size=2)

    status, after = await act(client, 42, action="leave")

    assert status == 200
    assert after["lobby"] is None
    assert battles.lobby_of_user(42) is None


async def test_the_size_is_checked_by_the_server(club):
    client, _, _ = club

    status, error = await act(client, 42, action="open", kind="royale", size=99)

    assert status == 409
    assert "королевскую битву" in error["error"]


# ---------- сам бой ----------


async def test_a_turn_is_sent_as_one_move(club):
    """Кнопка «Вперёд!» шлёт удар и блок разом — как в дуэли."""
    client, battles, _ = club
    await start_team(client, battles)

    status, body = await act(client, 42, action="turn", attack="head", block="belt")

    assert status == 200
    battle = body["battle"]
    assert battle["acted"] is True
    assert battle["chosen"]["attacks"]["0"] == "head"
    assert battle["fighting"] is True
    # остальные ещё думают: раунд не закрыт
    assert sum(1 for row in battle["party"] if row["ready"]) == 1
    assert battle["round"] == 1


async def test_half_a_turn_is_refused(club):
    client, battles, _ = club
    await start_team(client, battles)

    status, error = await act(client, 42, action="turn", attack="head")

    assert status == 409
    assert "удар" in error["error"]


async def test_the_round_is_counted_when_everyone_has_pressed(club):
    client, battles, _ = club
    await start_team(client, battles)

    body: dict = {}
    for user_id, _ in CLUB[:4]:
        _, body = await act(
            client, user_id, action="turn", attack="head", block="belt"
        )

    battle = body["battle"]
    # размен посчитан: в логе есть ход, а раунд сменился или бой уже кончен
    assert battle["log"]
    assert battle["round"] >= 2 or battle["finished"]


async def test_a_stranger_cannot_hit_in_someone_elses_battle(club):
    client, battles, _ = club
    await start_team(client, battles)

    outside = await state(client, 46)

    assert outside["battle"] is None
    assert outside["lobbies"] == []  # сбор уже закрыт: идёт бой
    status, error = await act(client, 46, action="turn", attack="head", block="belt")
    assert status == 409
    assert "не в групповом бою" in error["error"]
