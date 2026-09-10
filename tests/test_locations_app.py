"""Карта в мини-аппе: где боец, куда ходит и чего ему нельзя издалека.

Проверяем именно сервер. Спрятанная кнопка обходится запросом мимо
интерфейса, и если сервер её не подстрахует, вся карта — украшение.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import get_class
from bot.game.locations import FIGHT_CLUB, STEP_BETWEEN
from bot.models import Player
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(user_id: int = 42, location: str = FIGHT_CLUB) -> Player:
    fclass = get_class("warrior")
    return Player(
        user_id=user_id, nickname="Тайлер", class_code="warrior", level=8,
        credits=1000, location=location, **fclass.base_stats.as_dict(),
    )


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- карта ----------


async def test_the_map_shows_the_city_and_where_you_stand(client, db):
    """Карта приходит целиком: шесть районов, дома и зоны нажатия."""
    await db.save_player(make_player())

    body = await (await client.get("/api/map", headers=headers())).json()

    assert body["here"] == FIGHT_CLUB
    assert len(body["districts"]) == 6
    centre = next(one for one in body["districts"] if one["code"] == "main_hub")
    assert centre["here"] is True
    assert centre["image"].endswith("locations/main_hub.jpeg")
    club = next(one for one in centre["places"] if one["code"] == FIGHT_CLUB)
    assert club["here"] is True and club["works"] is True
    assert set(club["zone"]) == {"x", "y", "w", "h"}


async def test_houses_without_a_trade_say_so(client, db):
    """Банк и почта на карте есть, но услуги за ними пока нет."""
    await db.save_player(make_player())

    body = await (await client.get("/api/map", headers=headers())).json()
    houses = {
        place["code"]: place
        for district in body["districts"]
        for place in district["places"]
    }

    assert len(houses) == 14
    assert houses["bank"]["works"] is False
    assert houses["bank"]["soon"] and houses["bank"]["services"] == []
    assert houses["workshop"]["services"] == ["repair"]


# ---------- дорога ----------


async def test_walking_takes_time_and_the_card_says_how_much(client, db):
    """Пошёл — значит идёшь: карточка показывает, куда и сколько осталось."""
    await db.save_player(make_player())

    body = await (await client.post(
        "/api/travel", json={"to": "pharmacy"}, headers=headers()
    )).json()

    assert body["map"]["road"]["going"] is True
    assert body["map"]["road"]["to"] == "pharmacy"
    assert 0 < body["map"]["road"]["seconds_left"] <= STEP_BETWEEN
    assert body["card"]["place"]["going_to"] == "Аптека"
    # пока идёт — он ещё в клубе, и это честно
    assert body["card"]["place"]["code"] == FIGHT_CLUB


async def test_a_fighter_on_the_road_cannot_trade(client, db):
    """В дороге не торгуют: боец ещё не дошёл."""
    player = make_player(location="weapon_shop")
    player.set_out("pharmacy", STEP_BETWEEN)
    await db.save_player(player)

    response = await client.get("/api/shop", headers=headers())

    assert response.status == 409
    assert "дорог" in (await response.json())["error"]


async def test_the_road_ends_by_itself(client, db):
    """Срок вышел — боец на месте, и никакой таймер для этого не нужен."""
    player = make_player()
    player.set_out("pharmacy", STEP_BETWEEN, now=1000)
    player.arrives_at = 1  # как будто дорога кончилась давным-давно
    await db.save_player(player)

    body = await (await client.get("/api/map", headers=headers())).json()

    assert body["here"] == "pharmacy"
    # и это записано в базу, а не только показано
    assert (await db.get_player(42)).location == "pharmacy"


# ---------- у каждого дела свой адрес ----------


@pytest.mark.parametrize(
    "where,path,method,payload",
    [
        # в каждой строке боец стоит там, где этого как раз нельзя
        ("weapon_shop", "/api/repair", "post", {"item_id": 1}),
        ("workshop", "/api/market", "get", None),
        ("pawnshop", "/api/magic", "get", None),
        ("pharmacy", "/api/raid", "post", {"action": "open"}),
        ("clothes_shop", "/api/fight", "post", {"action": "open"}),
    ],
)
async def test_the_wrong_place_is_refused_by_the_server(
    client, db, where, path, method, payload
):
    """Не та локация — отказ с сервера, а не спрятанная кнопка."""
    await db.save_player(make_player(location=where))

    call = getattr(client, method)
    response = await (
        call(path, headers=headers()) if payload is None
        else call(path, json=payload, headers=headers())
    )

    assert response.status == 409, path
    assert "Здесь этого не делают" in (await response.json())["error"]


async def test_the_refusal_names_the_house_to_go_to(client, db):
    """Отказ бесполезен, если не сказать, куда идти."""
    await db.save_player(make_player())

    body = await (await client.post(
        "/api/repair", json={"item_id": 1}, headers=headers()
    )).json()

    assert "Мастерская" in body["error"]


async def test_weapons_are_sold_by_the_gunsmith_and_shirts_by_the_tailor(client, db):
    """Прилавок зависит от того, в чьей лавке боец стоит."""
    await db.save_player(make_player(location="weapon_shop"))
    gunsmith = await (await client.get("/api/shop", headers=headers())).json()
    assert [row["slot"] for row in gunsmith["sections"]] == ["weapon", "offhand"]

    await db.save_player(make_player(location="clothes_shop"))
    tailor = await (await client.get("/api/shop", headers=headers())).json()
    assert "weapon" not in [row["slot"] for row in tailor["sections"]]
    assert "shirt" in [row["slot"] for row in tailor["sections"]]


async def test_a_weapon_is_bought_where_weapons_are_sold(client, db):
    """Купить биту у одёжника нельзя, даже если попросить напрямую."""
    await db.save_player(make_player(location="clothes_shop"))

    response = await client.post(
        "/api/buy", json={"code": "bat"}, headers=headers()
    )

    assert response.status == 409
    assert "Оружейный магазин" in (await response.json())["error"]


async def test_the_club_is_where_fights_live(client, db):
    """Список боёв виден и издалека, но пометкой, что драться надо в клубе."""
    await db.save_player(make_player(location="bar"))

    body = await (await client.get("/api/fights", headers=headers())).json()

    assert body["at_club"] is False
    assert body["club_title"] == "Бойцовский клуб VEGAS"
