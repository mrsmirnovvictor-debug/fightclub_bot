"""Почта: посылки между бойцами, наложенный платёж и срок хранения.

Проверяем пять вещей, в таком порядке важности:

1. **Вещь не пропадает.** Она либо в рюкзаке, либо в посылке, либо у
   получателя, либо вернулась отправителю — третьего не дано.
2. **Деньги.** Пять кредитов за отправку не возвращаются никогда,
   наложенный платёж уходит отправителю целиком и только при выдаче.
3. **Срок.** Пять дней, тридцать или до востребования; вышел — посылка
   едет обратно и ждёт отправителя уже без срока и без платежа.
4. **Границы.** Себе не отправляют, надетое не отправляют, десять
   посылок на бойца — потолок.
5. **Место.** И отправляют, и получают на почте: спрятанная кнопка
   обходится запросом мимо интерфейса.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.game.classes import Stats
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.post import (
    DAY,
    MAX_WAITING,
    SEND_PRICE,
    TERMS,
    get_term,
    price_problem,
)
from bot.models import Player
from bot.post_service import PostError, collect, refuse, send, sweep
from bot.webapp.post import build_post
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

POST = "post_office"
CLUB = "fight_club"


def make_player(
    user_id: int = 42,
    nickname: str = "Тайлер",
    location: str = POST,
    credits: int = 500,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def enlist(
    db: Database, user_id: int, nickname: str, location: str = POST,
    credits: int = 500,
) -> Player:
    player = make_player(user_id, nickname, location, credits)
    await db.save_player(player)
    return player


async def stock(db: Database, player: Player, code: str = "bandana") -> int:
    """Положить бойцу вещь в рюкзак и вернуть её номер."""
    owned = await db.add_gear(player.user_id, code)
    player.gear = await db.list_gear(player.user_id)
    return owned.id


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


# ---------- правила ----------


def test_the_post_office_takes_parcels():
    """Почта работает: услуга на доме, и она же первая в списке."""
    place = get_location(POST)
    assert place is not None and place.allows(Service.POST)
    assert place.services[0] is Service.POST, "дверь должна открывать почту"
    assert not place.soon


def test_three_terms_and_one_of_them_is_forever():
    """Сроков три: пять дней, тридцать и до востребования."""
    assert [one.days for one in TERMS] == [5, 30, 0]
    moment = now_ts()
    assert get_term("short").until(moment) == moment + 5 * DAY
    assert get_term("demand").until(moment) == 0, "до востребования — без срока"
    assert get_term("почтой голубя") is None


def test_the_cash_on_delivery_knows_its_bounds():
    """Наложенный платёж не бывает отрицательным и запредельным."""
    assert price_problem(0) == "" and price_problem(500) == ""
    assert "отрицательным" in price_problem(-1)
    assert "не больше" in price_problem(10_000_000)


# ---------- отправка ----------


async def test_a_parcel_leaves_the_backpack_and_waits_for_its_owner(db):
    """Отправил — вещь ушла из рюкзака и легла на почту."""
    sender = await enlist(db, 42, "Тайлер")
    guest = await enlist(db, 43, "Марла")
    item_id = await stock(db, sender)
    moment = now_ts()

    said = await send(db, sender, item_id, "Марла", 0, "long", now=moment)
    assert "Бандана" in said and "Марла" in said and "30 дней" in said
    assert sender.credits == 500 - SEND_PRICE
    assert sender.gear == [] and await db.list_gear(42) == []

    waiting = await db.parcels_for(guest.user_id)
    assert [one.code for one in waiting] == ["bandana"]
    assert waiting[0].from_name == "Тайлер" and waiting[0].to_name == "Марла"
    assert waiting[0].price == 0 and waiting[0].keep_until == moment + 30 * DAY
    # У отправителя она же видна на своей полке — чтобы знать, где вещь
    assert [one.id for one in await db.parcels_from(42)] == [waiting[0].id]


async def test_the_post_office_refuses_the_impossible(db):
    """Себе, надетое, незнакомцу и без денег — отказ."""
    sender = await enlist(db, 42, "Тайлер", credits=SEND_PRICE)
    await enlist(db, 43, "Марла")
    item_id = await stock(db, sender)

    with pytest.raises(PostError, match="Себе посылки"):
        await send(db, sender, item_id, "Тайлер", 0, "long")
    with pytest.raises(PostError, match="в клубе нет"):
        await send(db, sender, item_id, "Никого", 0, "long")
    with pytest.raises(PostError, match="Напиши ник"):
        await send(db, sender, item_id, "  ", 0, "long")
    with pytest.raises(PostError, match="срока хранения"):
        await send(db, sender, item_id, "Марла", 0, "навсегда")
    with pytest.raises(PostError, match="отрицательным"):
        await send(db, sender, item_id, "Марла", -5, "long")
    with pytest.raises(PostError, match="Такой вещи"):
        await send(db, sender, item_id + 100, "Марла", 0, "long")

    owned = sender.gear[0]
    owned.slot = owned.item.slot
    await db.save_gear(owned)
    sender.gear = await db.list_gear(42)
    with pytest.raises(PostError, match="сними вещь"):
        await send(db, sender, item_id, "Марла", 0, "long")
    assert await db.parcels_for(43) == []

    # А без пяти кредитов не отправить вовсе
    owned.slot = None
    await db.save_gear(owned)
    sender.gear = await db.list_gear(42)
    sender.credits = 1
    with pytest.raises(PostError, match="наличными 1"):
        await send(db, sender, item_id, "Марла", 0, "long")
    assert [one.id for one in sender.gear] == [item_id], "вещь осталась у бойца"


async def test_ten_parcels_is_as_much_as_one_fighter_waits_for(db):
    """Одиннадцатую посылку бойцу не шлют: почта не свалка."""
    sender = await enlist(db, 42, "Тайлер", credits=1000)
    await enlist(db, 43, "Марла")
    for _ in range(MAX_WAITING):
        await send(db, sender, await stock(db, sender), "Марла", 0, "long")
    extra = await stock(db, sender)

    with pytest.raises(PostError, match=f"уже {MAX_WAITING} посылок"):
        await send(db, sender, extra, "Марла", 0, "long")
    assert len(await db.parcels_for(43)) == MAX_WAITING
    assert [one.id for one in sender.gear] == [extra]


async def test_sending_happens_at_the_post_office(db):
    """Отправляют и получают на почте, а не с ринга."""
    sender = await enlist(db, 42, "Тайлер", location=CLUB)
    await enlist(db, 43, "Марла")
    item_id = await stock(db, sender)

    with pytest.raises(PostError, match="на почте"):
        await send(db, sender, item_id, "Марла", 0, "long")
    with pytest.raises(PostError, match="на почте"):
        await collect(db, sender, 1)
    with pytest.raises(PostError, match="на почте"):
        await refuse(db, sender, 1)


# ---------- получение ----------


async def test_the_gift_is_handed_over_for_free(db):
    """Подарок выдают даром: наложенного платежа на нём нет."""
    sender = await enlist(db, 42, "Тайлер")
    guest = await enlist(db, 43, "Марла")
    await send(db, sender, await stock(db, sender), "Марла", 0, "long")
    parcel = (await db.parcels_for(43))[0]

    said = await collect(db, guest, parcel.id)
    assert "Бандана" in said and "получена" in said
    assert guest.credits == 500, "за подарок не платят"
    assert [one.code for one in guest.gear] == ["bandana"]
    assert await db.parcels_for(43) == [] and await db.parcels_from(42) == []
    with pytest.raises(PostError, match="Такой посылки"):
        await collect(db, guest, parcel.id)


async def test_the_cash_on_delivery_goes_to_the_sender(db):
    """Наложенный платёж: получатель платит, отправитель получает."""
    sender = await enlist(db, 42, "Тайлер")
    guest = await enlist(db, 43, "Марла", credits=100)
    await send(db, sender, await stock(db, sender), "Марла", 150, "long")
    parcel = (await db.parcels_for(43))[0]

    # Денег не хватает — посылка лежит дальше
    with pytest.raises(PostError, match="просят 150"):
        await collect(db, guest, parcel.id)
    assert len(await db.parcels_for(43)) == 1

    guest.credits = 200
    await db.save_player(guest)
    said = await collect(db, guest, parcel.id)
    assert "150" in said
    assert guest.credits == 50
    assert (await db.get_player(42)).credits == 500 - SEND_PRICE + 150
    assert [one.code for one in guest.gear] == ["bandana"]


async def test_a_refusal_sends_the_parcel_back(db):
    """Отказался — посылка едет обратно, и уже даром."""
    sender = await enlist(db, 42, "Тайлер")
    guest = await enlist(db, 43, "Марла")
    await send(db, sender, await stock(db, sender), "Марла", 150, "long")
    parcel = (await db.parcels_for(43))[0]

    said = await refuse(db, guest, parcel.id)
    assert "обратно" in said
    assert await db.parcels_for(43) == []
    back = await db.parcels_for(42)
    assert len(back) == 1 and back[0].back and back[0].price == 0
    assert back[0].keep_until == 0, "возврат лежит до востребования"
    with pytest.raises(PostError, match="только забрать"):
        await refuse(db, sender, back[0].id)

    said = await collect(db, sender, back[0].id)
    assert "вернулась" in said
    assert [one.code for one in sender.gear] == ["bandana"]
    assert sender.credits == 500 - SEND_PRICE, "отправка не возвращается"


# ---------- срок хранения ----------


async def test_the_term_runs_out_and_the_parcel_comes_back(db):
    """Не забрал за пять дней — посылка возвращается отправителю."""
    sender = await enlist(db, 42, "Тайлер")
    guest = await enlist(db, 43, "Марла", credits=1000)
    moment = now_ts()
    await send(db, sender, await stock(db, sender), "Марла", 150, "short", now=moment)

    # За час до срока она ещё у получателя
    assert await sweep(db, moment + 5 * DAY - 3600) == []
    assert len(await db.parcels_for(43)) == 1

    assert len(await sweep(db, moment + 5 * DAY + 1)) == 1
    assert await db.parcels_for(43) == []
    back = await db.parcels_for(42)
    assert len(back) == 1 and back[0].back and back[0].due == 0
    # И получателю её уже не выдают
    with pytest.raises(PostError, match="Такой посылки"):
        await collect(db, guest, back[0].id)
    await collect(db, sender, back[0].id)
    assert [one.code for one in sender.gear] == ["bandana"]


async def test_the_parcel_on_demand_waits_forever(db):
    """До востребования — значит без срока: такую не разворачивают."""
    sender = await enlist(db, 42, "Тайлер")
    await enlist(db, 43, "Марла")
    moment = now_ts()
    await send(db, sender, await stock(db, sender), "Марла", 0, "demand", now=moment)

    assert await sweep(db, moment + 365 * DAY) == []
    waiting = await db.parcels_for(43)
    assert len(waiting) == 1 and not waiting[0].back
    assert waiting[0].keep_until == 0


# ---------- экран и ручки ----------


async def test_the_screen_shows_both_shelves(db):
    """Экран почты: рюкзак на отправку, свои посылки и чужие."""
    sender = await enlist(db, 42, "Тайлер")
    guest = await enlist(db, 43, "Марла")
    await send(db, sender, await stock(db, sender), "Марла", 150, "short")
    await send(db, guest, await stock(db, guest, "pipe"), "Тайлер", 0, "demand")

    body = build_post(
        sender, await db.parcels_for(42), await db.parcels_from(42)
    )
    assert body["price"] == SEND_PRICE and body["waiting"] == 1
    assert [one["code"] for one in body["inbox"]] == ["pipe"]
    assert body["inbox"][0]["from"] == "Марла" and body["inbox"][0]["gift"] is True
    assert body["inbox"][0]["forever"] is True and body["inbox"][0]["left"] == ""
    assert [one["code"] for one in body["outbox"]] == ["bandana"]
    assert body["outbox"][0]["to"] == "Марла" and body["outbox"][0]["due"] == 150
    assert body["outbox"][0]["left"] == "5 дней"
    assert [one["title"] for one in body["terms"]] == [
        "5 дней", "30 дней", "До востребования"
    ]
    assert body["bag"] == [], "вещь уехала посылкой"


async def test_the_post_office_answers_only_at_the_post_office(client, db):
    """Ручка почты проверяет место сама."""
    player = make_player(location=CLUB)
    await db.save_player(player)
    assert (await client.get("/api/post", headers=headers())).status == 409

    player.location = POST
    await db.save_player(player)
    answer = await client.get("/api/post", headers=headers())
    assert answer.status == 200
    body = await answer.json()
    assert body["price"] == SEND_PRICE and body["inbox"] == []


async def test_the_whole_parcel_walks_through_the_app(client, db):
    """Отправить, получить и вернуть — через мини-апп."""
    sender = await enlist(db, 42, "Тайлер")
    await enlist(db, 43, "Марла", credits=1000)
    item_id = await stock(db, sender)

    answer = await client.post(
        "/api/post",
        headers=headers(),
        json={
            "action": "send", "item": item_id, "nickname": "Марла",
            "price": 150, "term": "short",
        },
    )
    assert answer.status == 200, await answer.text()
    body = await answer.json()
    assert "отправлена" in body["said"] and body["credits"] == 500 - SEND_PRICE
    assert [one["to"] for one in body["outbox"]] == ["Марла"]

    # Получатель видит её у себя и забирает за назначенную цену
    body = await (await client.get("/api/post", headers=headers(43))).json()
    assert len(body["inbox"]) == 1 and body["inbox"][0]["due"] == 150
    parcel = body["inbox"][0]["parcel"]

    answer = await client.post(
        "/api/post", headers=headers(43),
        json={"action": "take", "parcel": parcel},
    )
    assert answer.status == 200, await answer.text()
    body = await answer.json()
    assert body["inbox"] == [] and body["credits"] == 850
    assert [one["title"] for one in body["bag"]] == ["Бандана"]
    assert (await db.get_player(42)).credits == 500 - SEND_PRICE + 150

    # Чужую посылку чужими руками не взять
    answer = await client.post(
        "/api/post", headers=headers(), json={"action": "take", "parcel": parcel}
    )
    assert answer.status == 409
    assert "Такой посылки" in (await answer.json())["error"]

    answer = await client.post(
        "/api/post", headers=headers(), json={"action": "потерять"}
    )
    assert answer.status == 400
