"""Стенд сцены боя: ход собирается движком, а не выдумывается.

Смысл стенда в том, что он гоняет настоящий конвейер. Если бы ход
рисовал браузер, стенд проверял бы сцену против представления о
сервере, а не против сервера, — и разошлись бы они молча. Поэтому
проверки здесь следят ровно за одним: форма хода та же, что в бою, и
справочники те же, что у движка.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import ALL_ZONES, FIGHTER_CLASSES
from bot.game.combat import Outcome
from bot.webapp.server import create_app
from tests.test_webapp import FakeBot, TOKEN, make_init_data


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


def head():
    return {"X-Telegram-Init-Data": make_init_data()}


async def post(client, body):
    answer = await client.post("/api/scene", json=body, headers=head())
    return answer.status, await answer.text()


async def played(client, body):
    answer = await client.post("/api/scene", json=body, headers=head())
    assert answer.status == 200, await answer.text()
    return await answer.json()


# ---------- страница ----------


async def test_the_bench_page_is_served_with_its_own_scripts(client):
    """Стенд — отдельная страница, но сцена в ней та же, что в бою."""
    answer = await client.get("/scene")
    assert answer.status == 200
    page = await answer.text()

    for name in ("fightassets.js", "fightscene.js", "fightrender.js", "scene.js"):
        assert f"static/{name}?v=" in page, name
    # Своей копии сцены у стенда нет: карточка и стенд делят одни файлы
    assert "card.js" not in page
    # Адрес графики подставлен сервером
    assert '{{combat}}' not in page
    assert 'data-combat="https://' in page
    # А в самой вёрстке бакета нет: все адреса картинок живут в
    # `bot.game.art`, и вторым местом страница быть не должна
    from bot.webapp.server import STATIC_DIR

    source = (STATIC_DIR / 'scene.html').read_text(encoding='utf-8')
    assert 'r2.dev' not in source


# ---------- ход собирает движок ----------


async def test_the_turn_has_the_very_shape_the_fight_sends(client):
    """Форма хода — та же, что у боя: один сериализатор на двоих."""
    data = await played(client, {
        "left": "warrior", "right": "tank",
        "strikes": [{"by": "left", "zone": "chest", "outcome": "hit"}],
    })
    turn = data["turn"]
    for key in (
        "number", "round", "turn", "strikes", "hp_after", "hp_before",
        "resolution", "finished", "winner_id", "lines",
    ):
        assert key in turn, key
    strike = turn["strikes"][0]
    for key in (
        "attacker_id", "defender_id", "zone", "zone_title", "zone_where",
        "block", "outcome", "emoji", "title", "weapon", "damage", "counter",
        "armor", "hp_after", "counter_hp_after", "ability", "defence_ability",
        "missed_turn",
    ):
        assert key in strike, key


async def test_the_bench_offers_only_what_the_engine_knows(client):
    """Зоны, классы и исходы берутся у движка, а не выписаны руками."""
    data = await played(client, {"left": "warrior", "right": "tank", "strikes": []})

    assert [one["code"] for one in data["zones"]] == [
        zone.value for zone in ALL_ZONES
    ]
    assert {one["code"] for one in data["classes"]} == set(FIGHTER_CLASSES)
    assert {one["code"] for one in data["outcomes"]} == {
        outcome.value for outcome in Outcome
    }


async def test_the_fighters_carry_their_art_folder(client):
    """Сцене нужна папка образов — её же присылает и бой."""
    data = await played(client, {"left": "rogue", "right": "assassin", "strikes": []})
    left, right = data["fighters"]
    assert left["art_line"] == "trickster", left
    assert right["art_line"] == "assassin"
    assert left["max_hp"] > 0 and left["hp"] == left["max_hp"]


# ---------- выбранный исход доезжает как есть ----------


@pytest.mark.parametrize(
    "outcome", [one.value for one in Outcome]
)
async def test_every_outcome_can_be_staged(client, outcome):
    """Любой исход, который знает движок, стенд умеет показать."""
    data = await played(client, {
        "left": "warrior", "right": "tank",
        "strikes": [{"by": "left", "zone": "head", "outcome": outcome}],
    })
    strike = data["turn"]["strikes"][0]
    assert strike["outcome"] == outcome
    # У пропуска зоны нет вовсе — это не «голова», а её отсутствие
    assert (strike["zone"] is None) == (outcome == "skip")


async def test_a_defensive_outcome_takes_no_health(client):
    """Блок, уворот и пропуск здоровья не снимают — как и в бою."""
    for outcome in ("block", "dodge", "skip"):
        data = await played(client, {
            "left": "warrior", "right": "tank",
            "strikes": [{"by": "left", "zone": "chest", "outcome": outcome,
                         "damage": 40}],
        })
        strike = data["turn"]["strikes"][0]
        assert strike["damage"] == 0, outcome
        assert strike["hp_after"] == data["turn"]["hp_before"][
            str(strike["defender_id"])
        ], outcome


async def test_a_counter_takes_health_from_the_attacker(client):
    """Контрудар бьёт того, кто начал: остаток снимается с него."""
    data = await played(client, {
        "left": "warrior", "right": "tank",
        "strikes": [{"by": "left", "zone": "head", "outcome": "counter"}],
    })
    strike = data["turn"]["strikes"][0]
    assert strike["counter"] > 0
    assert strike["counter_hp_after"] < data["turn"]["hp_before"][
        str(strike["attacker_id"])
    ]
    # А защищавшийся цел
    assert strike["hp_after"] == data["turn"]["hp_before"][
        str(strike["defender_id"])
    ]


async def test_the_bench_can_knock_a_fighter_out(client):
    """Добили — ход помечен законченным, и назван победитель."""
    data = await played(client, {
        "left": "warrior", "right": "tank",
        "strikes": [{"by": "left", "zone": "head", "outcome": "crit",
                     "damage": 999}],
    })
    turn = data["turn"]
    assert turn["finished"] is True
    assert turn["winner_id"] == data["fighters"][0]["user_id"]
    assert turn["hp_after"][str(data["fighters"][1]["user_id"])] == 0


async def test_the_judge_speaks_about_the_staged_turn(client):
    """Слова судьи приходят те же, что в бою: его и зовём."""
    data = await played(client, {
        "left": "warrior", "right": "tank",
        "strikes": [{"by": "left", "zone": "belly", "outcome": "hit",
                     "damage": 17}],
    })
    lines = data["turn"]["lines"]
    assert lines, "судья промолчал"
    assert any("17" in line for line in lines), lines


async def test_the_order_of_strikes_is_kept(client):
    """Порядок ударов в ходу — тот, что задали: его решает не стенд."""
    data = await played(client, {
        "left": "warrior", "right": "tank",
        "strikes": [
            {"by": "right", "zone": "head", "outcome": "hit"},
            {"by": "left", "zone": "legs", "outcome": "hit"},
        ],
    })
    first, second = data["turn"]["strikes"]
    assert first["attacker_id"] == data["fighters"][1]["user_id"]
    assert second["attacker_id"] == data["fighters"][0]["user_id"]


async def test_simultaneous_is_passed_through(client):
    """«Разом» решает не сцена: стенд обязан уметь задать и это."""
    data = await played(client, {
        "left": "warrior", "right": "tank", "resolution": "simultaneous",
        "strikes": [{"by": "left", "zone": "head", "outcome": "hit"}],
    })
    assert data["turn"]["resolution"] == "simultaneous"


# ---------- отказы ----------


async def test_a_class_the_game_does_not_have_is_refused(client):
    """Выдуманный класс стенд не берёт: образов для него нет."""
    status, text = await post(client, {
        "left": "ниндзя", "right": "tank", "strikes": [],
    })
    assert status == 400, text


async def test_a_zone_the_engine_does_not_know_is_refused(client):
    """И зона тоже: движок знает пять, шестой нет."""
    status, text = await post(client, {
        "left": "warrior", "right": "tank",
        "strikes": [{"by": "left", "zone": "ухо", "outcome": "hit"}],
    })
    assert status == 400, text


async def test_the_bench_needs_the_same_door_as_the_rest(client):
    """Стенд не заводит отдельного входа без проверки."""
    answer = await client.post(
        "/api/scene", json={"left": "warrior", "right": "tank", "strikes": []}
    )
    assert answer.status == 401
