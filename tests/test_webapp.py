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
    assert card["birthday"] == "27.10.13 01:31"  # метка в UTC, показ московский
    assert card["record"] == {
        "wins": 136,
        "losses": 54,
        "draws": 11,
        # Подвал живёт своим счётом и в победы над людьми не мешается
        "raid_wins": player.raid_wins,
        "raid_fights": player.raid_fights,
        "rating": player.rating,
        # Чужой кошелёк не наше дело: эта карточка открыта соседом
        "credits": 0,
        "account": 0,
        "has_account": False,
        "has_card": False,
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
    # В базе метка в UTC, а показываем московское время: 22:31 UTC — это
    # уже полвторого следующего дня
    assert format_birthday("2013-10-26 22:31:00") == "27.10.13 01:31"
    assert format_birthday("2013-10-26 09:31:00") == "26.10.13 12:31"
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


async def test_the_loading_picture_reaches_the_page_by_its_real_address(client):
    """Заставка нужна раньше первого ответа, значит её адрес — в странице.

    Подставляет его сервер, а не вёрстка: все адреса картинок в игре
    живут в одном месте. Если имя места в странице переименуют, браузер
    получит само `{{loading}}` вместо картинки, и увидит это только
    игрок — поэтому проверяем обе половины: подстановка сработала и
    следа от неё не осталось.
    """
    from bot.game.art import LOADING
    from bot.webapp.server import LOADING_SLOT

    body = await (await client.get("/")).text()

    assert LOADING in body
    assert LOADING_SLOT not in body


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
    # всё остальное — аватар, слоты, счёт — приезжает из /api/card по кнопке «i».
    # Клан и служба — исключение: их значки стоят в самой строке, рядом
    # с ником, и носить их разом нельзя
    assert set(me) == {
        "user_id", "nickname", "level", "rating", "place", "pro", "badge",
        "is_self", "fclass", "clan", "service",
    }
    # Место считает сервер от смещения страницы, а не страница от своего
    # списка: на второй странице первая строка — пятьдесят первое место
    assert [row["place"] for row in body["fighters"]] == [1, 2, 3]
    assert me["place"] == 2 and me["rating"] == 1000
    assert me["clan"] == {}, "боец без клана знаков не носит"
    assert me["service"] == {}, "боец без службы значка не носит"


async def a_crowd(db, many: int, start: int = 100) -> None:
    """Клуб из `many` бойцов: рейтинг убывает с номером, как и места."""
    from bot.models import Player

    for number in range(many):
        player = Player(
            user_id=start + number,
            nickname=f"Боец {number + 1}",
            class_code="warrior",
            level=1 + number % 9,
        )
        player.rating = 2000 - number
        await db.save_player(player)


async def test_the_rating_comes_in_pages_of_fifty(client, db):
    """Рейтинг листается страницами: номер со страницы, смещение с сервера.

    Место считает сервер и от смещения: на второй странице первая
    строка — пятьдесят первое место, и страница, считающая места сама,
    написала бы там единицу.
    """
    await a_crowd(db, 137)
    head = {"X-Telegram-Init-Data": make_init_data(100)}

    first = await (await client.get("/api/club", headers=head)).json()
    assert first["total"] == 137
    assert first["page"] == 1 and first["pages"] == 3
    assert first["offset"] == 0 and first["page_size"] == 50
    assert len(first["fighters"]) == 50
    assert first["fighters"][0]["place"] == 1
    assert first["fighters"][-1]["place"] == 50

    second = await (await client.get("/api/club?page=2", headers=head)).json()
    assert second["page"] == 2 and second["offset"] == 50
    assert second["fighters"][0]["place"] == 51
    assert second["fighters"][0]["rating"] == 2000 - 50

    last = await (await client.get("/api/club?page=3", headers=head)).json()
    assert last["page"] == 3 and len(last["fighters"]) == 37
    assert last["fighters"][-1]["place"] == 137

    # Ни один боец не попал на две страницы и ни один не потерялся
    seen = [
        row["user_id"]
        for page in (first, second, last)
        for row in page["fighters"]
    ]
    assert len(seen) == len(set(seen)) == 137


async def test_a_page_past_the_end_lands_on_the_last_one(client, db):
    """Просили двадцатую, а их три — отдаём третью, а не отказ.

    Доска почёта не та вещь, на запрос которой отвечают ошибкой: номер
    страницы приходит со страницы, и мусор в нём — не повод закрыться.
    """
    await a_crowd(db, 60)
    head = {"X-Telegram-Init-Data": make_init_data(100)}

    far = await (await client.get("/api/club?page=20", headers=head)).json()
    assert far["page"] == 2 and far["fighters"][0]["place"] == 51

    for typed in ("0", "-5", "каша", ""):
        answer = await client.get(f"/api/club?page={typed}", headers=head)
        assert answer.status == 200, typed
        assert (await answer.json())["page"] == 1, typed


async def test_the_search_finds_fighters_by_a_piece_of_the_nickname(client, db):
    """Поиск по куску ника: начинающиеся с него — первыми."""
    from bot.models import Player

    for user_id, nickname in (
        (42, "Марла"),
        (43, "Команданте Марио"),
        (44, "Тайлер"),
    ):
        await db.save_player(
            Player(user_id=user_id, nickname=nickname, class_code="warrior")
        )
    head = {"X-Telegram-Init-Data": make_init_data(42)}

    found = await (await client.get("/api/players?q=мар", headers=head)).json()
    names = [row["nickname"] for row in found["fighters"]]
    assert names == ["Марла", "Команданте Марио"], (
        "набравший «Мар» ищет Марлу, а не того, у кого это в середине"
    )
    assert found["total"] == 2
    # Мест в поиске нет: это не рейтинг
    assert all(row["place"] == 0 for row in found["fighters"])
    assert found["fighters"][0]["is_self"] is True

    # Регистр не важен: прозвище — не пароль
    assert len(
        (await (await client.get("/api/players?q=МАРЛ", headers=head)).json())[
            "fighters"
        ]
    ) == 1


async def test_an_empty_search_shows_nobody_rather_than_everybody(client, db):
    """Поиск без слова — это не картотека: он отдаёт пусто."""
    from bot.models import Player

    await db.save_player(
        Player(user_id=42, nickname="Тайлер", class_code="warrior")
    )
    head = {"X-Telegram-Init-Data": make_init_data(42)}

    for query in ("", "   ", "%"):
        found = await (
            await client.get(f"/api/players?q={query}", headers=head)
        ).json()
        assert found["fighters"] == [], query


async def test_the_search_needs_a_signature(client):
    assert (await client.get("/api/players?q=мар")).status == 401


async def test_the_club_list_needs_a_signature(client):
    assert (await client.get("/api/club")).status == 401


# ---------- падение клиента доезжает до журнала ----------


async def test_a_broken_mini_app_reports_itself_to_the_log(client, caplog):
    """Мини-апп упал — сервер об этом узнаёт, а не гадает по молчанию.

    Со стороны сервера сломанная карточка выглядит идеально: все запросы
    двухсотые, ошибок нет. Исключение живёт в консоли вебвью, которую на
    телефоне не открыть, поэтому клиент приносит своё падение сам.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="bot.webapp.server"):
        response = await client.post(
            "/api/oops",
            json={
                "message": "TypeError: cannot read properties of undefined",
                "stack": "at render (card.js:3529)",
                "screen": "hero",
                "agent": "Telegram Desktop",
            },
            headers={"X-Telegram-Init-Data": make_init_data(42)},
        )

    assert response.status == 200
    written = caplog.text
    assert "42" in written, "не видно, у кого упало"
    assert "hero" in written, "не видно, на каком экране"
    assert "TypeError" in written and "card.js:3529" in written


async def test_a_crash_report_is_taken_even_without_a_signature(client, caplog):
    """Карточка могла развалиться до входа — тогда подписи в отчёте нет."""
    import logging

    with caplog.at_level(logging.WARNING, logger="bot.webapp.server"):
        response = await client.post("/api/oops", json={"message": "boom"})

    assert response.status == 200
    assert "неизвестный" in caplog.text and "boom" in caplog.text


async def test_a_crash_report_cannot_flood_the_log(client, caplog):
    """Длинный стек обрезается: журнал не свалка."""
    import logging

    with caplog.at_level(logging.WARNING, logger="bot.webapp.server"):
        await client.post(
            "/api/oops",
            json={"message": "я" * 5000, "stack": "ю" * 5000},
            headers={"X-Telegram-Init-Data": make_init_data(42)},
        )

    assert "я" * 5000 not in caplog.text
    assert len(caplog.text) < 2000


async def test_a_crash_report_survives_junk_instead_of_json(client):
    """Отчёт присылают из упавшего скрипта — там может прийти что угодно."""
    assert (await client.post("/api/oops", data=b"not json")).status == 200
    assert (await client.post("/api/oops", json=["не", "объект"])).status == 200


# Два имени на странице заняты дважды, и это не одобрение, а запись
# долга. `plural` — одно правило, написанное двумя способами: что из них
# победит, неважно. `clockText` — настоящий конфликт: тела разные, и
# второе молча перебивает первое, так что часы, писавшиеся под «время
# вышло» и «45 сек», показывают «0:00» и «0:45». Чинить это — менять вид
# экранов, и решать такое надо отдельно.
TWICE_DEFINED = {"clockText", "plural"}


def test_no_two_functions_on_the_page_share_a_name():
    """Второе объявление молча съедает первое, и заметить это нечем.

    Страница — один большой файл с плоским списком функций, без модулей
    и без сборщика. Объявил функцию с занятым именем — браузер оставит
    последнюю, а вызовы первой уедут в неё же. Ошибка тихая: файл
    разбирается, тесты соседних экранов проходят, а ломается тот, чьё
    имя увели.

    Так и случилось с авторынком: он завёл `marketAction`, которым уже
    звалась комиссионка, и три экрана торговли перестали отправлять
    запросы. Проверка руками это пропустила — она искала `function` и не
    видела `async function`.
    """
    import re

    from bot.webapp.server import STATIC_DIR

    page = (STATIC_DIR / "card.js").read_text(encoding="utf-8")
    names = re.findall(r"^(?:async )?function (\w+)\(", page, re.M)

    seen, twice = set(), set()
    for name in names:
        (twice if name in seen else seen).add(name)

    assert twice <= TWICE_DEFINED, f"имя занято дважды: {twice - TWICE_DEFINED}"


def test_the_page_knows_every_shop_and_every_house_in_town():
    """Новый магазин на карте — новая строка на странице, и обе рядом.

    Дом на карте открывает экран по своей услуге, а прилавок подписан по
    ней же. Заводится услуга в правилах, а не на странице, и забыть там
    строку легко: дом тогда открывается молча ничем, а прилавок — «Лавкой»
    без имени. Поэтому карты услуг страницы сверяем с городом.
    """
    import re

    from bot.game.locations import LOCATIONS, SHOP_SERVICES
    from bot.webapp.server import STATIC_DIR

    page = (STATIC_DIR / "card.js").read_text(encoding="utf-8")

    def keys(name: str) -> set[str]:
        body = page.split(f"{name} = {{", 1)[1].split("\n};", 1)[0]
        return set(re.findall(r"^  (\w+):", body, re.M))

    titles, screens = keys("const SHOP_TITLES"), keys("const HOUSE_SCREENS")
    # Дом открывается по первой своей услуге: у каждой работающей двери в
    # городе должна быть строка, иначе дверь откроет молча ничего
    doors = {place.services[0].value for place in LOCATIONS if place.works}
    assert doors <= screens, doors - screens
    # Прилавок подписан по услуге. Элитный магазин сюда не входит: он
    # открывает не прилавок, а вкладку товара за звёзды, и зовётся там сам
    counters = set(
        re.findall(r"^  (\w+): \(\) => openShop\(\),", page, re.M)
    )
    assert {one.value for one in SHOP_SERVICES} & counters <= titles
    assert counters <= titles, counters - titles
    assert "jewel" in counters, "ювелира страница за магазин не считает"


async def test_the_card_carries_the_set_line_everywhere_the_thing_is_shown():
    """Строка комплекта едет с вещью в куклу, в рюкзак и на прилавок.

    Считает её сервер, а не страница: число вещей набора известно только
    ему — а значит, и во сколько работает оружие. На прилавке чужая
    вещь приходит с нулём, но с лестницей: покупатель должен понимать,
    за что платит.
    """
    from bot.game.locations import Service
    from bot.webapp.card import build_shop

    player = make_player(level=10, class_code="tank", strength=25, endurance=25)
    worn = {
        Slot.WEAPON: "set_bouncer_weapon",
        Slot.HEAD: "set_bouncer_head",
        Slot.JACKET: "set_bouncer_jacket",
        Slot.BOOTS: "set_bouncer_boots",
    }
    for number, (slot, code) in enumerate(worn.items(), start=1):
        player.gear.append(
            OwnedItem(item=CATALOGUE[code], id=number, slot=slot)
        )
    # И одна вещь набора в рюкзаке: она тоже знает про комплект
    player.gear.append(OwnedItem(item=CATALOGUE["set_bouncer_belt"], id=9))

    card = build_card(player, TOKEN, viewer_id=player.user_id)
    cells = {
        row["slot"]: row for row in card["slots"]["left"] + card["slots"]["right"]
    }
    hand = cells["weapon"]["item"]["kit"]
    assert hand["worn"] == 4 and hand["power"] == 120
    assert hand["next"] == {"need": 7, "power": 135}
    assert "Вышибала" in hand["text"]
    # Шлем — не оружие, и строка у него про оружие набора
    assert cells["head"]["item"]["kit"]["power"] == 120
    assert cells["head"]["item"]["kit"]["weapon"] is False

    bag = {row["code"]: row for row in card["inventory"]}
    assert bag["set_bouncer_belt"]["kit"]["worn"] == 4

    # На прилавке — та же строка: у своего набора с числом, у чужого с нулём
    shelf = build_shop(player, Service.WEAPONS)
    goods = {
        row["code"]: row
        for section in shelf["sections"]
        for row in section["items"]
    }
    assert goods["set_bouncer_weapon"]["kit"]["worn"] == 4
    assert goods["set_army_weapon"]["kit"]["worn"] == 0
    assert goods["set_army_weapon"]["kit"]["power"] == 100
    assert goods["viking_axe"]["kit"] == {}


# ---------- казино ----------


async def test_the_casino_door_asks_where_you_stand(client, db):
    """Играть можно в казино, а не из любого места города.

    Проверяем на сервере, а не прятанием кнопки: спрятанная обходится
    запросом мимо интерфейса.
    """
    player = make_player(location="fight_club")
    await db.save_player(player)
    headers = {"X-Telegram-Init-Data": make_init_data()}
    response = await client.get("/api/casino", headers=headers)
    assert response.status == 409, await response.text()

    player.location = "casino"
    await db.save_player(player)
    response = await client.get("/api/casino", headers=headers)
    assert response.status == 200
    body = await response.json()
    assert len(body["lobby"]["games"]) == 4
    assert body["wallet"] == {"chips": 0, "reserved": 0}
    # Порядок карманов приходит с сервера: клиент его не выдумывает
    assert len(body["wheel"]) == 37 and body["wheel"][0] == 0


async def test_the_casino_changes_money_over_http(client, db):
    """Касса меняет кредиты на фишки и обратно одной ручкой."""
    player = make_player(location="casino", credits=300)
    await db.save_player(player)
    headers = {"X-Telegram-Init-Data": make_init_data()}

    response = await client.post(
        "/api/casino", headers=headers,
        json={"action": "exchange", "kind": "buy", "amount": 200},
    )
    assert response.status == 200, await response.text()
    body = await response.json()
    assert body["wallet"]["chips"] == 200
    assert (await db.get_player(42)).credits == 100

    response = await client.post(
        "/api/casino", headers=headers,
        json={"action": "exchange", "kind": "sell", "amount": 500},
    )
    assert response.status == 409, "больше, чем есть, не сдать"
    assert (await db.get_player(42)).credits == 100


async def test_the_casino_seats_and_bets_over_http(client, db):
    """Сесть, поставить и встать — всё теми же двумя ручками."""
    player = make_player(location="casino", credits=300)
    await db.save_player(player)
    headers = {"X-Telegram-Init-Data": make_init_data()}
    await client.post(
        "/api/casino", headers=headers,
        json={"action": "exchange", "kind": "buy", "amount": 300},
    )
    lobby = await (await client.get("/api/casino", headers=headers)).json()
    dice = next(
        one for one in lobby["lobby"]["games"] if one["code"] == "dice"
    )
    table_id = dice["tables"][0]["id"]

    response = await client.post(
        "/api/casino", headers=headers,
        json={"action": "sit", "table": table_id, "seat": 1},
    )
    assert response.status == 200, await response.text()
    body = await response.json()
    assert body["table"]["id"] == table_id
    assert body["table"]["mine"]

    response = await client.post(
        "/api/casino", headers=headers,
        json={"action": "bet", "table": table_id, "amount": 40,
              "pick": "seven", "command": "once"},
    )
    assert response.status == 200, await response.text()
    body = await response.json()
    assert body["wallet"] == {"chips": 260, "reserved": 40}

    # Тот же ключ команды — та же ставка, а не вторая
    await client.post(
        "/api/casino", headers=headers,
        json={"action": "bet", "table": table_id, "amount": 40,
              "pick": "seven", "command": "once"},
    )
    assert (await db.casino_wallet(42)) == {"chips": 260, "reserved": 40}

    # Встали до закрытия — ставка вернулась
    response = await client.post(
        "/api/casino", headers=headers, json={"action": "leave"},
    )
    assert response.status == 200
    assert (await db.casino_wallet(42)) == {"chips": 300, "reserved": 0}


# ---------- шкала опыта ----------


def test_the_bar_gets_the_very_thresholds_the_ups_are_paid_at():
    """Засечки шкалы считает лестница, а не страница.

    Второе место, где живут пороги апгрейдов, однажды разошлось бы с
    первым — и шкала рисовала бы засечку там, где очко не дают.
    """
    from bot.game.economy import exp_at_up
    from bot.webapp.card import ups_payload

    player = make_player(level=10, exp=6_000, micro_ups=2)

    marks = ups_payload(player)

    assert [one["number"] for one in marks] == [1, 2, 3]
    assert [one["at"] for one in marks] == [2_100, 4_500, 7_200]
    assert [one["at"] for one in marks] == [exp_at_up(10, n) for n in (1, 2, 3)]
    assert [one["taken"] for one in marks] == [True, True, False]


def test_the_fourth_up_is_not_a_notch_but_the_level_itself():
    """Четвёртого апгрейда на дорожке нет: он и есть новый уровень."""
    from bot.game.economy import MICRO_UPS_PER_LEVEL
    from bot.webapp.card import ups_payload

    marks = ups_payload(make_player(level=10, exp=6_000, micro_ups=2))

    assert len(marks) == MICRO_UPS_PER_LEVEL - 1
    assert all(one["at"] < 10_200 for one in marks)


def test_at_the_ceiling_the_bar_has_no_notches_at_all():
    """На потолке уровня впереди нет, а значит, нет и долей до него."""
    from bot.webapp.card import ups_payload

    assert ups_payload(make_player(level=15, exp=0)) == []


def test_the_card_carries_the_whole_bar():
    """Всё, что рисует шкала, приезжает одним куском."""
    card = build_card(make_player(level=10, exp=6_000, micro_ups=2), TOKEN, 42)

    bar = card["progress"]
    assert bar["level"] == 10 and bar["next_level"] == 11
    assert bar["exp"] == 6_000 and bar["exp_needed"] == 10_200
    assert bar["exp_to_next_up"] == 1_200
    assert len(bar["ups"]) == 3


# ---------- список игроков: отбор по уровню и алфавит ----------


async def _club_of(db, *rows):
    """Клуб из нескольких бойцов: (номер, ник, ELO, уровень)."""
    from bot.models import Player

    for user_id, nickname, rating, level in rows:
        player = Player(
            user_id=user_id, nickname=nickname, class_code="warrior", level=level
        )
        player.rating = rating
        await db.save_player(player)


async def test_the_player_list_can_be_sorted_by_nickname(client, db):
    """Алфавитная сортировка по нику — и она не путается в регистре.

    Кириллица тут важна: `lower()` у SQLite знает только латиницу, и
    без своей замены «Ангел» и «ангел» встали бы в разные концы списка.
    """
    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(
        db,
        (42, "Тайлер", 1000, 4),
        (43, "марла", 1400, 7),
        (44, "Ангел", 900, 2),
        (45, "ёжик", 800, 2),
    )

    body = await (await client.get("/api/club?sort=name", headers=head)).json()
    assert [row["nickname"] for row in body["fighters"]] == [
        "Ангел", "ёжик", "марла", "Тайлер"
    ]
    assert body["sort"] == "name"
    # ELO в строке остаётся — оно свойство бойца, а не порядка строк
    assert [row["rating"] for row in body["fighters"]] == [900, 800, 1400, 1000]


async def test_the_alphabetical_list_shows_no_places(client, db):
    """В переставленном списке мест нет: «пятый» означал бы не то.

    Место бойцу даёт ELO по всему клубу. Номер строки алфавитного
    списка читался бы как место в клубе — и врал бы.
    """
    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4), (43, "Ангел", 1400, 7))

    by_elo = await (await client.get("/api/club", headers=head)).json()
    assert [row["place"] for row in by_elo["fighters"]] == [1, 2]

    by_name = await (await client.get("/api/club?sort=name", headers=head)).json()
    assert [row["place"] for row in by_name["fighters"]] == [0, 0]


async def test_the_player_list_can_be_cut_down_to_one_level(client, db):
    """Отбор по уровню: тот же список, только без чужих уровней."""
    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(
        db,
        (42, "Тайлер", 1000, 4),
        (43, "Марла", 1400, 7),
        (44, "Ангел", 900, 4),
    )

    body = await (await client.get("/api/club?level=4", headers=head)).json()
    assert body["level"] == 4
    assert body["total"] == 2, "число страниц считается по тому же отбору"
    assert [row["nickname"] for row in body["fighters"]] == ["Тайлер", "Ангел"]
    # И мест в урезанном списке тоже нет: «второй» среди четвёртого
    # уровня — это не второе место в клубе
    assert [row["place"] for row in body["fighters"]] == [0, 0]

    # Уровни на выбор — только те, на которых кто-то есть
    assert body["levels"] == [4, 7]


async def test_an_empty_filter_is_an_empty_list_and_not_an_error(client, db):
    """Пустой результат отбора отличается от ошибки загрузки.

    Ответ двести с пустым списком — это «никого такого нет». Отказ
    читался бы как «рейтинг не открылся», и игрок жал бы «ещё раз».
    """
    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4))

    answer = await client.get("/api/club?level=9", headers=head)
    assert answer.status == 200
    body = await answer.json()
    assert body["fighters"] == [] and body["total"] == 0
    assert body["level"] == 9


async def test_junk_in_the_filter_does_not_break_the_list(client, db):
    """Мусор в отборе — список целиком, а не отказ."""
    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4), (43, "Марла", 1400, 7))

    for typed in ("", "ноль", "-5", "4.5", "'; DROP TABLE players; --"):
        answer = await client.get(f"/api/club?level={typed}", headers=head)
        assert answer.status == 200, typed
        body = await answer.json()
        assert body["total"] == 2, typed
        assert body["level"] == 0, typed


# ---------- рейтинг недели ----------


async def _fought(db, user_id, count, at=None, outcome="draw", delta=0):
    """Положить бойцу несколько зачётных боёв в журнал ELO."""
    from bot.game.health import now_ts

    from uuid import uuid4

    moment = now_ts() if at is None else at
    for _ in range(count):
        player = await db.get_player(user_id)
        was = player.rating
        player.apply_rating(delta)
        await db.save_player(player)
        # Ключ боя случайный: одинаковый отбросил бы уникальный индекс
        # журнала, и второй вызов этой заготовки не добавил бы ни боя
        await db.log_rating(
            user_id, "duel", uuid4().hex,
            outcome, player.rating - was, player.rating, moment,
        )


async def test_the_weekly_rating_shows_only_those_who_fought_ten_times(client, db):
    """Порог виден и на экране: с девятью боями бойца в рейтинге нет."""
    from bot.game.ladder import QUALIFY

    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4), (43, "Марла", 1400, 7))
    await _fought(db, 42, QUALIFY)
    await _fought(db, 43, QUALIFY - 1)

    body = await (await client.get("/api/ladder", headers=head)).json()

    assert [row["nickname"] for row in body["fighters"]] == ["Тайлер"]
    assert body["total"] == 1
    assert body["qualify"] == QUALIFY
    row = body["fighters"][0]
    assert row["place"] == 1 and row["fights"] == QUALIFY
    assert row["prize"] == 400, "предварительная награда за первое место"
    assert row["is_self"]


async def test_the_weekly_rating_says_the_prize_is_preliminary(client, db):
    """Подпись о предварительности и границы недели — в самом ответе.

    Это часть правил, а не оформления: страница, придумавшая подпись
    сама, однажды разошлась бы с тем, как считает сервер.
    """
    from bot.game.ladder import QUALIFY

    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4))
    await _fought(db, 42, QUALIFY)

    body = await (await client.get("/api/ladder", headers=head)).json()

    assert "воскресенье" in body["note"] and "12:00" in body["note"]
    assert "Москв" in body["window"] and "до" in body["window"]
    assert body["settled"] is False
    assert body["period"]
    # Призовая таблица приезжает оттуда же, где её считают
    assert body["prizes"][0] == {"from": 1, "to": 1, "place": "1", "prize": 400}
    assert body["prizes"][-1]["place"] == "20–50"


async def test_a_fighter_below_the_threshold_sees_his_own_progress(client, db):
    """До квалификации вместо места — личный прогресс «7 из 10»."""
    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4))
    await _fought(db, 42, 7)

    body = await (await client.get("/api/ladder", headers=head)).json()

    assert body["fighters"] == []
    assert body["mine"]["fights"] == 7
    assert body["mine"]["left"] == 3
    assert body["mine"]["qualified"] is False
    assert body["mine"]["place"] == 0 and body["mine"]["prize"] == 0
    assert body["mine"]["progress"] == "7 из 10 PvP-боёв в Бойцовском клубе"


async def test_the_tenth_fight_puts_a_fighter_on_the_board(client, db):
    """После десятого боя тот же экран показывает место, а не прогресс."""
    from bot.game.ladder import QUALIFY

    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4))
    await _fought(db, 42, QUALIFY - 1)

    before = await (await client.get("/api/ladder", headers=head)).json()
    assert before["mine"]["place"] == 0

    await _fought(db, 42, 1, outcome="win")

    after = await (await client.get("/api/ladder", headers=head)).json()
    assert after["mine"]["qualified"] is True
    assert after["mine"]["place"] == 1
    assert after["mine"]["prize"] == 400
    assert [row["user_id"] for row in after["fighters"]] == [42]


async def test_an_empty_weekly_rating_says_so_in_words(client, db):
    """Пустой рейтинг подписан словами, а не пустотой."""
    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4))

    answer = await client.get("/api/ladder", headers=head)
    assert answer.status == 200, "пустой рейтинг — не ошибка"
    body = await answer.json()
    assert body["fighters"] == [] and body["total"] == 0
    assert body["empty"] == "Пока никто не провёл 10 PvP-боёв за эту неделю."


async def test_the_weekly_rating_goes_in_pages_of_fifty(client, db):
    """Страницы по пятьдесят, а места считаются по всему рейтингу.

    На второй странице первая строка — пятьдесят первое место, а не
    первое: иначе призовая таблица платила бы за место на странице.
    """
    from bot.game.ladder import QUALIFY

    head = {"X-Telegram-Init-Data": make_init_data(42)}
    for number in range(1, 56):
        await _club_of(db, (number, f"Боец{number}", 5000 - number, 4))
        await _fought(db, number, QUALIFY)

    first = await (await client.get("/api/ladder", headers=head)).json()
    assert first["pages"] == 2 and first["page"] == 1
    assert len(first["fighters"]) == 50
    assert first["fighters"][0]["place"] == 1

    second = await (await client.get("/api/ladder?page=2", headers=head)).json()
    assert second["page"] == 2 and second["offset"] == 50
    assert [row["place"] for row in second["fighters"]] == list(range(51, 56))
    # И награды на второй странице уже нулевые: платят до пятидесятого
    assert [row["prize"] for row in second["fighters"]] == [0] * 5

    # Мусор в номере страницы — первая, а не отказ
    for typed in ("", "ноль", "-1", "99"):
        answer = await client.get(f"/api/ladder?page={typed}", headers=head)
        assert answer.status == 200, typed


async def test_the_weekly_rating_needs_a_signature(client, db):
    assert (await client.get("/api/ladder")).status == 401


async def test_opening_the_rating_settles_the_week_that_ended(client, db):
    """Взгляд на рейтинг закрывает кончившуюся неделю и платит за неё.

    Часов, которые будили бы бота в полдень воскресенья, в клубе нет:
    период закрывает тот, кто на него смотрит. И закрывает целиком —
    платят всем, а не только тому, кто смотрел.
    """
    from bot.game.clock import WEEK_SECONDS, week_key, week_opens
    from bot.game.health import now_ts
    from bot.game.ladder import QUALIFY

    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4), (43, "Марла", 1400, 7))
    # Оба дрались на прошлой неделе, смотрит только первый
    last_week = week_opens(now_ts()) - WEEK_SECONDS + 3600
    await _fought(db, 42, QUALIFY, at=last_week)
    await _fought(db, 43, QUALIFY, at=last_week)
    closed = week_key(last_week)

    body = await (await client.get("/api/ladder", headers=head)).json()

    assert await db.settled_week(closed) is not None, "неделя не закрыта"
    # Марла на экран не заходила, а деньги получила
    assert (await db.get_player(43)).credits == 400
    assert (await db.get_player(42)).credits == 375
    # И тот, кто смотрел, видит, чем кончилась прошлая неделя
    assert body["last"]["period"] == closed
    assert body["last"]["place"] == 2 and body["last"]["prize"] == 375
    assert body["last"]["paid"] is True
    # Идущая неделя при этом пустая: бои той недели в неё не затекли
    assert body["fighters"] == [] and body["mine"]["fights"] == 0


async def test_a_broken_payout_does_not_close_the_rating_screen(client, db, monkeypatch):
    """Сорвалась выплата — экран всё равно открывается.

    Рейтинг показать важнее, чем доплатить именно сейчас: доплатит
    следующий взгляд, а отказ оставил бы игрока без экрана вовсе.
    """
    import bot.webapp.server as server
    from bot.game.ladder import QUALIFY

    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4))
    await _fought(db, 42, QUALIFY)

    async def breaks(*args, **kwargs):
        raise RuntimeError("касса закрыта")

    monkeypatch.setattr(server, "settle_due", breaks)
    answer = await client.get("/api/ladder", headers=head)

    assert answer.status == 200
    body = await answer.json()
    assert [row["user_id"] for row in body["fighters"]] == [42]


async def test_a_fighter_who_left_the_club_keeps_his_place_in_the_week(client, db):
    """Удалённый боец не обрушивает список мест.

    Место ему посчитал журнал, а прозвище лежало у бойца — и бойца
    могли удалить между подсчётом и запросом. Набор полей в строке тот
    же, что у живой: страница рисует её тем же кодом, и из-за одного
    выбывшего пропали бы все остальные места.
    """
    from bot.game.ladder import QUALIFY

    head = {"X-Telegram-Init-Data": make_init_data(42)}
    await _club_of(db, (42, "Тайлер", 1000, 4), (43, "Марла", 1400, 7))
    await _fought(db, 42, QUALIFY)
    await _fought(db, 43, QUALIFY)
    await db.delete_player(43)

    body = await (await client.get("/api/ladder", headers=head)).json()

    assert [row["place"] for row in body["fighters"]] == [1, 2]
    gone = body["fighters"][0]
    assert gone["user_id"] == 43 and gone["nickname"] == "боец выбыл"
    assert gone["rating"] == 1400, "ELO знает журнал, а не карточка"
    assert gone["prize"] == 400
    # Набор полей тот же, что у живого бойца
    alive = body["fighters"][1]
    assert set(gone) == set(alive)
