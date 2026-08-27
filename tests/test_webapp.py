"""Мини-апп: подпись Telegram, сборка карточки и HTTP-ручки."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.equipment import CATALOGUE, LEFT_SLOTS, RIGHT_SLOTS, OwnedItem, Slot
from bot.game.health import now_ts
from bot.models import Player
from bot.webapp.auth import (
    AuthError,
    check_avatar_token,
    parse_init_data,
    sign_avatar,
)
from bot.webapp.card import build_card, format_birthday
from bot.webapp.server import create_app

TOKEN = "424242:TESTTOKEN"


def make_init_data(user_id: int = 42, start_param: str = "", auth_date: int | None = None):
    payload = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(
            {"id": user_id, "first_name": "Тайлер", "username": "tyler"},
            separators=(",", ":"),
        ),
    }
    if start_param:
        payload["start_param"] = start_param
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


def make_player(user_id: int = 42, **kwargs) -> Player:
    data = dict(
        user_id=user_id,
        nickname="Растафарайчик",
        class_code="warrior",
        strength=9,
        agility=6,
        intuition=5,
        endurance=8,
        level=7,
        total_exp=23743,
        wins=136,
        losses=54,
        draws=11,
        created_at="2013-10-26 22:31:00",
    )
    data.update(kwargs)
    return Player(**data)


# ---------- подпись ----------


def test_valid_init_data_is_accepted():
    user = parse_init_data(make_init_data(start_param="777"), TOKEN)
    assert user.user_id == 42
    assert user.first_name == "Тайлер"
    assert user.start_param == "777"


def test_tampered_init_data_is_rejected():
    data = make_init_data(user_id=42)
    # подменяем id пользователя прямо в подписанной строке
    forged = data.replace("%3A42%2C", "%3A43%2C")
    assert forged != data
    with pytest.raises(AuthError):
        parse_init_data(forged, TOKEN)
    with pytest.raises(AuthError):
        parse_init_data(data.replace("query_id=AAHdF6IQ", "query_id=HACKED00"), TOKEN)
    with pytest.raises(AuthError):
        parse_init_data(data, "999:OTHERTOKEN")


def test_init_data_without_hash_is_rejected():
    with pytest.raises(AuthError):
        parse_init_data("user=%7B%22id%22%3A1%7D&auth_date=1", TOKEN)
    with pytest.raises(AuthError):
        parse_init_data("", TOKEN)


def test_stale_init_data_is_rejected():
    old = make_init_data(auth_date=int(time.time()) - 90_000)
    with pytest.raises(AuthError):
        parse_init_data(old, TOKEN)
    # с большим окном та же строка проходит
    assert parse_init_data(old, TOKEN, max_age=200_000).user_id == 42


def test_avatar_token_is_bound_to_user_and_time():
    expires = int(time.time()) + 600
    token = sign_avatar(42, TOKEN, expires)
    assert check_avatar_token(42, TOKEN, expires, token)
    assert not check_avatar_token(43, TOKEN, expires, token)
    assert not check_avatar_token(42, TOKEN, expires, "deadbeef")
    assert not check_avatar_token(42, TOKEN, expires, token, now=expires + 1)


# ---------- карточка ----------


def test_card_has_everything_the_screen_needs():
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    assert card["name"] == "Растафарайчик"
    assert card["level"] == 7
    assert card["is_self"] is True
    assert card["city"] == "Vegas City"
    assert card["birthplace"] == "Vegas City"  # по умолчанию, пока не дрался в группе
    assert card["birthday"] == "26.10.13 22:31"
    assert card["record"] == {
        "wins": 136,
        "losses": 54,
        "draws": 11,
        "rating": player.rating,
        "credits": 0,
    }
    assert card["hp"]["current"] == card["hp"]["max"] == player.max_hp
    assert card["hp"]["color"] == "green"
    assert [stat["code"] for stat in card["stats"]] == [
        "strength",
        "agility",
        "intuition",
        "endurance",
    ]


def test_card_slots_follow_the_layout():
    card = build_card(make_player(), TOKEN)
    assert [slot["slot"] for slot in card["slots"]["left"]] == [
        s.value for s in LEFT_SLOTS
    ]
    assert [slot["slot"] for slot in card["slots"]["right"]] == [
        s.value for s in RIGHT_SLOTS
    ]
    assert all(slot["item"] is None for slot in card["slots"]["left"])


def test_equipment_shows_up_in_slots_stats_and_hp():
    bare = make_player()
    dressed = make_player()
    dressed.gear = [
        OwnedItem(item=CATALOGUE["knuckles"], id=1, slot=Slot.WEAPON),
        OwnedItem(item=CATALOGUE["leather_jacket"], id=2, slot=Slot.JACKET),
    ]

    before = build_card(bare, TOKEN)
    after = build_card(dressed, TOKEN)

    weapon = next(s for s in after["slots"]["left"] if s["slot"] == "weapon")
    assert weapon["item"]["title"] == "Кастет"
    assert f"💪+{CATALOGUE['knuckles'].strength}" in weapon["item"]["bonus"]
    assert "👊" in weapon["item"]["bonus"]  # кастет добавляет свой урон

    strength = next(s for s in after["stats"] if s["code"] == "strength")
    bonus = CATALOGUE["knuckles"].strength
    assert (strength["base"], strength["bonus"], strength["total"]) == (9, bonus, 9 + bonus)
    # косуха даёт выносливость и плоские очки здоровья
    assert after["hp"]["max"] > before["hp"]["max"]


def test_card_reports_a_beaten_fighter_as_red():
    player = make_player()
    player.set_hp(1, now=now_ts())
    card = build_card(player, TOKEN)
    assert card["hp"]["color"] == "red"
    assert card["hp"]["can_fight"] is False
    assert card["hp"]["ready_in"] > 0
    assert card["hp"]["ready_in_text"]


def test_avatar_url_appears_only_for_uploaded_photos():
    assert build_card(make_player(), TOKEN)["avatar"]["url"] is None
    with_photo = build_card(make_player(avatar_file_id="file-123"), TOKEN)
    assert with_photo["avatar"]["url"].startswith("avatar/42?expires=")


def test_birthday_formats_and_survives_junk():
    assert format_birthday("2013-10-26 22:31:00") == "26.10.13 22:31"
    assert format_birthday(None) == "—"
    assert format_birthday("непонятно") == "непонятно"


# ---------- HTTP ----------


class FakeBot:
    """Заглушка вместо aiogram.Bot — отдаёт «файл» аватара."""

    class File:
        file_path = "photos/file_1.jpg"

    async def get_file(self, file_id):
        return self.File()

    async def download_file(self, path):
        import io

        return io.BytesIO(b"\xff\xd8\xff\xe0 jpeg")


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    app = create_app(FakeBot(), db, config)
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_api_needs_a_valid_signature(client):
    assert (await client.get("/api/card")).status == 401
    response = await client.get(
        "/api/card", headers={"X-Telegram-Init-Data": make_init_data() + "junk"}
    )
    assert response.status == 401


async def test_api_returns_the_card_of_the_viewer(client, db):
    await db.save_player(make_player())
    response = await client.get(
        "/api/card", headers={"X-Telegram-Init-Data": make_init_data()}
    )
    assert response.status == 200
    card = await response.json()
    assert card["name"] == "Растафарайчик"
    assert card["is_self"] is True


async def test_api_opens_someone_elses_card_by_start_param(client, db):
    await db.save_player(make_player(user_id=42, nickname="Тайлер"))
    await db.save_player(make_player(user_id=77, nickname="Марла"))
    response = await client.get(
        "/api/card",
        headers={"X-Telegram-Init-Data": make_init_data(user_id=42, start_param="77")},
    )
    card = await response.json()
    assert card["name"] == "Марла"
    assert card["is_self"] is False
    assert "credits" in card["record"]  # данные общие, скрывает их уже страница


async def test_api_says_when_there_is_no_character(client):
    response = await client.get(
        "/api/card", headers={"X-Telegram-Init-Data": make_init_data()}
    )
    assert response.status == 404
    body = await response.json()
    assert body["error"] == "no_character"


async def test_avatar_needs_a_signed_link(client, db):
    await db.save_player(make_player(avatar_file_id="file-123"))
    assert (await client.get("/avatar/42")).status == 403

    expires = int(time.time()) + 600
    token = sign_avatar(42, TOKEN, expires)
    good = await client.get(f"/avatar/42?expires={expires}&token={token}")
    assert good.status == 200
    assert good.content_type == "image/jpeg"
    assert await good.read() == b"\xff\xd8\xff\xe0 jpeg"

    stale = int(time.time()) - 10
    bad = await client.get(
        f"/avatar/42?expires={stale}&token={sign_avatar(42, TOKEN, stale)}"
    )
    assert bad.status == 403


async def test_avatar_404_when_the_fighter_has_no_photo(client, db):
    await db.save_player(make_player())
    expires = int(time.time()) + 600
    response = await client.get(
        f"/avatar/42?expires={expires}&token={sign_avatar(42, TOKEN, expires)}"
    )
    assert response.status == 404


async def test_page_and_health_are_served(client):
    page = await client.get("/")
    assert page.status == 200
    assert "Карточка бойца" in await page.text()
    assert (await client.get("/static/card.js")).status == 200
    assert (await (await client.get("/healthz")).json()) == {"status": "ok"}


# ---------- ссылки на карточку в чате ----------


def test_name_in_chat_links_to_the_card():
    from bot.game.links import links
    from bot.game.narrator import name_link

    try:
        assert name_link(42, "Тайлер") == '<a href="tg://user?id=42">Тайлер</a>'
        links.configure("@fightclub_bot", "card")
        assert name_link(42, "Тайлер") == (
            '<a href="https://t.me/fightclub_bot/card?startapp=42">Тайлер</a>'
        )
        # имя всё так же экранируется
        assert "&lt;b&gt;" in name_link(1, "<b>")
    finally:
        links.configure("", "")


def test_card_link_needs_both_username_and_app_name():
    from bot.game.links import CardLinks

    assert CardLinks().card_url(1) is None
    assert CardLinks(bot_username="bot").card_url(1) is None
    assert CardLinks(bot_username="bot", miniapp_name="card").card_url(1) == (
        "https://t.me/bot/card?startapp=1"
    )
