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
    """Без фото в рамке стоит образ: своей картинки у него пока нет."""
    plain = build_card(make_player(), TOKEN)["avatar"]
    assert not plain["url"] and not plain["photo"]
    assert plain["look"] == "rookie" and plain["emoji"] == "🥊"

    with_photo = build_card(make_player(avatar_file_id="file-123"), TOKEN)["avatar"]
    assert with_photo["url"].startswith("avatar/42?expires=")
    assert with_photo["photo"]


def test_the_chosen_look_shows_up_in_the_frame():
    picked = build_card(make_player(look="queen"), TOKEN)["avatar"]
    assert (picked["look"], picked["emoji"]) == ("queen", "👑")
    assert picked["look_title"] == "Королева ринга"

    # загруженное фото важнее образа — боец поставил своё лицо осознанно
    both = build_card(make_player(look="queen", avatar_file_id="file-1"), TOKEN)
    assert both["avatar"]["url"].startswith("avatar/42?expires=")
    assert both["avatar"]["photo"]


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


async def test_the_page_carries_a_version_and_is_never_cached(client):
    """Иначе Telegram показывает вчерашнюю вёрстку из кеша вебвью."""
    from bot.webapp.server import asset_stamp

    page = await client.get("/")
    body = await page.text()
    stamp = asset_stamp()

    assert f"static/card.css?v={stamp}" in body
    assert f"static/card.js?v={stamp}" in body
    assert "no-store" in page.headers["Cache-Control"]
    # файл со штампом в адресе отдаётся как обычно
    assert (await client.get(f"/static/card.css?v={stamp}")).status == 200


def test_the_version_changes_with_the_files(tmp_path, monkeypatch):
    """Метка считается по содержимому: правка стиля обязана её сдвинуть."""
    import bot.webapp.server as server

    for name in ("card.html", "card.css", "card.js"):
        (tmp_path / name).write_text("было", encoding="utf-8")
    monkeypatch.setattr(server, "STATIC_DIR", tmp_path)

    before = server.asset_stamp()
    (tmp_path / "card.css").write_text("стало", encoding="utf-8")
    assert server.asset_stamp() != before


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


def test_the_short_name_survives_a_pasted_link():
    """В MINIAPP_NAME часто кладут не имя, а всю ссылку из BotFather.

    Ссылка с такой начинкой выглядит рабочей, а Telegram по ней молча
    открывает чат с ботом вместо карточки — ровно тот случай, когда «имя в
    чате не открывает карточку».
    """
    from bot.game.links import CardLinks, short_name

    assert short_name("https://t.me/vegasfightclub_bot/card") == "card"
    assert short_name("t.me/bot/card?startapp=1") == "card"
    assert short_name("@card") == "card"
    assert short_name("/card/") == "card"
    assert short_name("   ") == ""

    links = CardLinks()
    links.configure("@bot", "https://t.me/bot/card")
    assert links.card_url(7) == "https://t.me/bot/card?startapp=7"


def test_the_main_mini_app_opens_without_a_short_name():
    """Мини-апп можно включить главным — тогда короткое имя не нужно."""
    from bot.game.links import CardLinks

    main = CardLinks(bot_username="bot", main_app=True)
    assert main.card_url(7) == "https://t.me/bot?startapp=7"
    assert main.enabled

    # именованное приложение важнее: оно точнее адресует
    both = CardLinks(bot_username="bot", miniapp_name="card", main_app=True)
    assert both.card_url(7) == "https://t.me/bot/card?startapp=7"


def test_without_a_mini_app_the_name_leads_to_the_bot_not_into_nowhere():
    """Запасной путь: диплинк в личку, где бот сам пришлёт карточку."""
    from bot.game.links import CardLinks, card_target

    plain = CardLinks(bot_username="bot")
    assert plain.card_url(9) is None
    assert not plain.enabled
    assert plain.href(9) == "https://t.me/bot?start=card_9"

    # бот понимает, чью карточку у него просят
    assert card_target("card_9") == 9
    assert card_target("card_") is None
    assert card_target("shop") is None

    # без имени бота остаётся только профиль в Telegram
    assert CardLinks().href(9) == "tg://user?id=9"


def test_a_short_name_written_with_a_slash_still_works():
    """MINIAPP_NAME иногда вписывают как «/card» — ссылка не должна ломаться."""
    from bot.game.links import CardLinks

    sloppy = CardLinks()
    sloppy.configure("@fightclub_bot", " /card ")
    assert sloppy.card_url(1) == "https://t.me/fightclub_bot/card?startapp=1"


def test_stranger_card_hides_the_wallet_and_the_backpack():
    """Карточку соседа открывают из чата боя — кошелёк и рюкзак не показываем."""
    from bot.game.equipment import CATALOGUE, OwnedItem

    player = make_player(credits=777)
    player.gear = [OwnedItem(item=CATALOGUE["knuckles"], id=1)]

    stranger = build_card(player, TOKEN, viewer_id=999)
    assert stranger["is_self"] is False
    assert stranger["record"]["credits"] == 0
    assert stranger["inventory"] == []
    # характеристики и боевые показатели при этом на месте
    assert stranger["stats"] and stranger["combat"]["damage_max"] > 0

    mine = build_card(player, TOKEN, viewer_id=player.user_id)
    assert mine["record"]["credits"] == 777
    assert len(mine["inventory"]) == 1


# ---------- бойцовский клуб ----------


async def test_club_lists_every_fighter(client, db):
    """Список клуба: сильные сверху, в строке только то, что в ней видно."""
    from bot.models import Player

    for user_id, nickname, rating, level in (
        (42, "Тайлер", 1000, 4),
        (43, "Марла", 1400, 7),
        (44, "Ангел", 900, 2),
    ):
        player = Player(
            user_id=user_id, nickname=nickname, class_code="warrior", level=level
        )
        player.rating = rating
        await db.save_player(player)

    response = await client.get(
        "/api/club", headers={"X-Telegram-Init-Data": make_init_data(42)}
    )
    body = await response.json()

    assert response.status == 200
    assert body["total"] == 3
    assert [row["nickname"] for row in body["fighters"]] == ["Марла", "Тайлер", "Ангел"]

    me = next(row for row in body["fighters"] if row["user_id"] == 42)
    assert me["is_self"] and me["level"] == 4
    assert me["fclass"]["title"] == "Воин"
    # всё остальное — аватар, слоты, счёт — приезжает из /api/card по кнопке «i»
    assert set(me) == {"user_id", "nickname", "level", "pro", "is_self", "fclass"}


async def test_the_club_list_needs_a_signature(client):
    assert (await client.get("/api/club")).status == 401
