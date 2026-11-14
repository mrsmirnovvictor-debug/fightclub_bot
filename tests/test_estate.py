"""Аренда жилья: четыре дома, пятьдесят семь квартир, сейф и кладовая.

Проверяем шесть вещей, в таком порядке важности:

1. **Планировка.** В домах №1–3 по шестнадцать квартир, в №4 девять, и
   комнатность на этаже ровно та, какую нарисовали.
2. **Деньги и срок.** Месяц вперёд по цене комнатности, продление
   кладётся сверху остатка, а не съедает его.
3. **Одна квартира на бойца** и один жилец на квартиру — и это держит
   база, а не проверка в службе.
4. **Вместимость.** Шесть мест на комнату, и тринадцатую вещь в двушку
   не положить.
5. **Просрочка.** Срок вышел — квартира заперта; неделя прошла — её
   сдают другому, а имущество возвращается хозяину целиком.
6. **Место.** Снимают, платят и открывают сейф в своём доме: спрятанная
   кнопка обходится запросом мимо интерфейса.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.estate_service import (
    EstateError,
    deposit,
    extend,
    move_out,
    rent,
    stash,
    sweep,
    take,
    withdraw,
)
from bot.game.classes import Stats
from bot.game.estate import (
    FLAT_SLOTS,
    GRACE_SECONDS,
    HOUSES,
    RENT_PRICE,
    RENT_SECONDS,
    extended,
    flat_at,
    flats_of,
    house_rooms,
)
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.models import Player
from bot.webapp.estate import build_house
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

HOUSE = "residential_apartment"
HOUSE_4 = "residential_apartment_4"
CLUB = "fight_club"


def make_player(
    user_id: int = 42,
    nickname: str = "Тайлер",
    location: str = HOUSE,
    credits: int = 1000,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def enlist(
    db: Database, user_id: int, nickname: str, location: str = HOUSE,
    credits: int = 1000,
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


# ---------- планировка ----------


def test_the_quarter_holds_fifty_seven_flats():
    """Четыре дома: три по шестнадцать квартир и один на девять."""
    assert len(HOUSES) == 4
    assert [len(flats_of(house)) for house in HOUSES] == [16, 16, 16, 9]
    assert sum(len(flats_of(house)) for house in HOUSES) == 57


def test_each_floor_is_laid_out_as_drawn():
    """Этаж домов №1–3: две однокомнатные, двушка и трёшка. №4: трёшка и две двушки."""
    first = flats_of(HOUSE)
    assert [flat.rooms for flat in first[:4]] == [1, 1, 2, 3]
    assert [flat.floor for flat in first[:5]] == [1, 1, 1, 1, 2]
    # Номера идут подряд через все этажи, как в жизни
    assert [flat.number for flat in first] == list(range(1, 17))
    assert house_rooms(HOUSE) == {1: 8, 2: 4, 3: 4}

    fourth = flats_of(HOUSE_4)
    assert [flat.rooms for flat in fourth] == [3, 2, 2] * 3
    assert house_rooms(HOUSE_4) == {2: 6, 3: 3}
    assert {flat.floor for flat in fourth} == {1, 2, 3}
    assert flat_at(HOUSE_4, 10) is None, "десятой квартиры в доме №4 нет"


def test_the_price_and_the_pantry_grow_with_the_rooms():
    """Цена — 300, 400 и 500, мест в кладовой — по шесть на комнату."""
    assert RENT_PRICE == {1: 300, 2: 400, 3: 500}
    assert FLAT_SLOTS == {1: 6, 2: 12, 3: 18}
    assert flat_at(HOUSE, 1).price == 300 and flat_at(HOUSE, 1).slots == 6
    assert flat_at(HOUSE, 3).price == 400 and flat_at(HOUSE, 3).slots == 12
    assert flat_at(HOUSE, 4).price == 500 and flat_at(HOUSE, 4).slots == 18


def test_every_house_rents_flats():
    """Все четыре дома открыты и сдают: «скоро» у них больше нет."""
    for house in HOUSES:
        place = get_location(house)
        assert place is not None and place.allows(Service.ESTATE)
        assert not place.soon, f"{house} всё ещё «скоро»"


# ---------- снять и продлить ----------


async def test_a_month_is_paid_forward(db):
    """Снял — списали месяц, и срок встал ровно на тридцать дней."""
    player = await enlist(db, 42, "Тайлер")
    moment = now_ts()

    said = await rent(db, player, 3, now=moment)
    assert "двухкомнатная" in said.lower() and "12 вещей" in said
    assert player.credits == 1000 - 400
    lease = await db.lease_of(player.user_id)
    assert lease.number == 3 and lease.rooms == 2 and lease.house == HOUSE
    assert lease.paid_until == moment + RENT_SECONDS
    assert lease.rented_at == moment and lease.vault == 0
    assert player.flat == lease


async def test_extending_lies_on_top_of_the_term(db):
    """Продление кладётся поверх остатка, а не от сегодняшнего дня."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    moment = now_ts()
    await rent(db, player, 1, now=moment)
    paid = (await db.lease_of(42)).paid_until

    # Через десять дней доплатил месяц: срок стал на два месяца от найма
    later = moment + 10 * 24 * 60 * 60
    said = await extend(db, player, now=later)
    assert "продлена" in said
    assert (await db.lease_of(42)).paid_until == paid + RENT_SECONDS
    assert player.credits == 2000 - 300 * 2

    # А просроченный съём начинается от этого часа: за дни, в которые в
    # квартиру не заходили, клуб денег не берёт и времени не возвращает
    assert extended(paid, paid + 5) == paid + 5 + RENT_SECONDS


async def test_one_flat_a_fighter_and_one_tenant_a_flat(db):
    """Вторую квартиру не снять, и занятую — тоже."""
    first = await enlist(db, 42, "Тайлер", credits=2000)
    await rent(db, first, 1)

    with pytest.raises(EstateError, match="уже снимаешь"):
        await rent(db, first, 2)

    second = await enlist(db, 43, "Марла", credits=2000)
    with pytest.raises(EstateError, match="уже занята"):
        await rent(db, second, 1)
    # А свободную — пожалуйста
    await rent(db, second, 2)
    assert (await db.lease_of(43)).number == 2


async def test_the_empty_pocket_rents_nothing(db):
    """Не хватает кредитов — отказ, и квартира остаётся свободной."""
    player = await enlist(db, 42, "Тайлер", credits=100)
    with pytest.raises(EstateError, match="наличными 100"):
        await rent(db, player, 4)
    assert await db.lease_of(42) is None
    with pytest.raises(EstateError, match="Такой квартиры"):
        await rent(db, player, 17)


async def test_renting_happens_in_the_house_itself(db):
    """Снимают, платят и открывают сейф в своём доме, а не с ринга."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    await rent(db, player, 1)

    player.location = CLUB
    # Снять и продлить можно только в жилом доме — хоть в каком
    with pytest.raises(EstateError, match="жилом доме"):
        await rent(db, player, 2)
    with pytest.raises(EstateError, match="жилом доме"):
        await extend(db, player)
    # А сейф и кладовая — только в своём: до них надо дойти
    with pytest.raises(EstateError, match="надо дойти"):
        await deposit(db, player, 10)
    with pytest.raises(EstateError, match="надо дойти"):
        await withdraw(db, player, 10)
    with pytest.raises(EstateError, match="надо дойти"):
        await stash(db, player, 1)
    with pytest.raises(EstateError, match="надо дойти"):
        await take(db, player, 1)

    # И в чужом подъезде своя квартира тоже не открывается
    player.location = "residential_apartment_2"
    with pytest.raises(EstateError, match="Жилой дом №1"):
        await deposit(db, player, 10)
    with pytest.raises(EstateError, match="своём доме"):
        await extend(db, player)


# ---------- сейф и кладовая ----------


async def test_the_safe_keeps_credits_apart_from_the_pocket(db):
    """В сейф кладут наличные, берут их оттуда наличными же."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    await rent(db, player, 1)

    said = await deposit(db, player, 700)
    assert "700" in said
    assert player.credits == 2000 - 300 - 700
    assert (await db.lease_of(42)).vault == 700

    with pytest.raises(EstateError, match="столько не взять"):
        await withdraw(db, player, 701)
    with pytest.raises(EstateError, match="Напиши число"):
        await deposit(db, player, 0)

    await withdraw(db, player, 700)
    assert player.credits == 2000 - 300
    assert (await db.lease_of(42)).vault == 0


async def test_the_pantry_holds_six_places_a_room(db):
    """В двушку влезает двенадцать вещей, тринадцатая не лезет."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    await rent(db, player, 3)  # двухкомнатная: двенадцать мест

    item_id = await stock(db, player)
    said = await stash(db, player, item_id)
    assert "Бандана" in said
    assert player.gear == [] and await db.list_gear(42) == []
    kept = await db.flat_items((await db.lease_of(42)).id)
    assert [one.code for one in kept] == ["bandana"]

    said = await take(db, player, kept[0].id)
    assert "Бандана" in said and [one.code for one in player.gear] == ["bandana"]
    assert await db.flat_items((await db.lease_of(42)).id) == []
    with pytest.raises(EstateError, match="уже нет"):
        await take(db, player, kept[0].id)

    lease = await db.lease_of(42)
    for _ in range(FLAT_SLOTS[2]):
        await stash(db, player, await stock(db, player))
    extra = await stock(db, player)
    with pytest.raises(EstateError, match="12 мест"):
        await stash(db, player, extra)
    assert len(await db.flat_items(lease.id)) == FLAT_SLOTS[2]
    # В рюкзаке осталась та вещь, которую забрали с полки, и та, которой
    # места не хватило: лишнего кладовая не берёт и своего не отдаёт
    assert extra in [one.id for one in player.gear]
    assert [one.code for one in player.gear] == ["bandana", "bandana"]


async def test_the_worn_thing_stays_on_the_fighter(db):
    """Надетое в кладовую не кладут: сперва его снимают."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    await rent(db, player, 1)
    item_id = await stock(db, player)
    owned = player.gear[0]
    owned.slot = owned.item.slot
    await db.save_gear(owned)
    player.gear = await db.list_gear(42)

    with pytest.raises(EstateError, match="сними вещь"):
        await stash(db, player, item_id)
    with pytest.raises(EstateError, match="Такой вещи"):
        await stash(db, player, item_id + 100)


async def test_a_homeless_fighter_has_no_safe(db):
    """Нет квартиры — нет ни сейфа, ни кладовой."""
    player = await enlist(db, 42, "Тайлер")
    with pytest.raises(EstateError, match="не снимаешь жилья"):
        await extend(db, player)
    with pytest.raises(EstateError, match="не снимаешь жилья"):
        await deposit(db, player, 10)
    with pytest.raises(EstateError, match="не снимаешь жилья"):
        await withdraw(db, player, 10)
    with pytest.raises(EstateError, match="не снимаешь жилья"):
        await stash(db, player, 1)
    with pytest.raises(EstateError, match="не снимаешь жилья"):
        await take(db, player, 1)
    with pytest.raises(EstateError, match="не снимаешь жилья"):
        await move_out(db, player)


# ---------- просрочка и выселение ----------


async def test_the_locked_flat_opens_only_after_paying(db):
    """Срок вышел — ни положить, ни взять, пока не продлил."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    moment = now_ts()
    await rent(db, player, 1, now=moment)
    await deposit(db, player, 200, now=moment)

    late = moment + RENT_SECONDS + 60
    with pytest.raises(EstateError, match="заперта"):
        await deposit(db, player, 100, now=late)
    with pytest.raises(EstateError, match="заперта"):
        await withdraw(db, player, 100, now=late)
    with pytest.raises(EstateError, match="заперта"):
        await stash(db, player, await stock(db, player), now=late)

    # Продлил — всё на месте, и сейф снова открыт
    await extend(db, player, now=late)
    assert (await db.lease_of(42)).vault == 200
    await withdraw(db, player, 200, now=late)
    assert player.credits == 2000 - 300 - 200 - 300 + 200


async def test_a_week_of_arrears_gives_the_flat_away(db):
    """Неделя просрочки — квартиру сдают другому, а добро возвращают."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    moment = now_ts()
    await rent(db, player, 3, now=moment)
    await deposit(db, player, 500, now=moment)
    await stash(db, player, await stock(db, player), now=moment)
    purse = player.credits

    # За час до конца недели квартира ещё его
    almost = moment + RENT_SECONDS + GRACE_SECONDS - 3600
    assert await sweep(db, player, almost) == []
    assert await db.lease_of(42) is not None

    # А после — уже нет, и всё вернулось хозяину
    after = moment + RENT_SECONDS + GRACE_SECONDS + 1
    assert len(await sweep(db, player, after)) == 1
    assert await db.lease_of(42) is None and player.flat is None
    assert player.credits == purse + 500
    assert [one.code for one in player.gear] == ["bandana"]
    assert [one.code for one in await db.list_gear(42)] == ["bandana"]

    # И квартиру можно снять заново — хоть ему же
    await rent(db, player, 3, now=after)
    assert (await db.lease_of(42)).number == 3


async def test_moving_out_returns_everything(db):
    """Выселился — вещи в рюкзак, кредиты из сейфа на руки."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    await rent(db, player, 4)
    await deposit(db, player, 300)
    await stash(db, player, await stock(db, player))
    purse = player.credits

    said = await move_out(db, player)
    assert "выселился" in said and "300" in said and "1" in said
    assert await db.lease_of(42) is None and player.flat is None
    assert player.credits == purse + 300
    assert [one.code for one in player.gear] == ["bandana"]

    # Квартира свободна: её снимает кто угодно
    other = await enlist(db, 43, "Марла", credits=2000)
    await rent(db, other, 4)
    assert (await db.lease_of(43)).number == 4


# ---------- экран и ручки ----------


async def test_the_house_screen_shows_the_whole_stairwell(db):
    """Экран дома: этажи, цены и кто за какой дверью."""
    player = await enlist(db, 42, "Тайлер", credits=2000)
    other = await enlist(db, 43, "Марла", credits=2000)
    await rent(db, other, 1)

    taken = await db.leases_in(HOUSE)
    body = build_house(player, HOUSE, taken)
    assert body["title"] == "Жилой дом №1" and body["floors"] == 4
    assert body["flats"] == 16 and body["rent_days"] == 30
    assert [one["free"] for one in body["rooms"]] == [7, 4, 4]
    first = body["levels"][0]["flats"]
    assert [one["number"] for one in first] == [1, 2, 3, 4]
    assert first[0]["taken"] and first[0]["tenant"] == "Марла"
    assert not first[0]["can_rent"] and first[1]["can_rent"]
    assert body["mine"] == {} and body["pantry"]["mine"] is False

    # А у жильца тот же экран показывает его квартиру и сейф
    mine = build_house(other, HOUSE, taken)
    assert mine["mine"]["number"] == 1 and mine["mine"]["paid"] is True
    assert mine["levels"][0]["flats"][0]["mine"] is True
    assert mine["pantry"]["mine"] is True and mine["pantry"]["slots"] == 6
    assert "Твоя однокомнатная" in mine["note"]

    # Квартира в другом доме — это только строка: снять вторую нельзя
    away = build_house(other, HOUSE_4, await db.leases_in(HOUSE_4))
    assert away["mine"] == {} and away["elsewhere"]["number"] == 1
    assert not any(
        flat["can_rent"] for level in away["levels"] for flat in level["flats"]
    )


async def test_the_house_answers_only_inside_the_house(client, db):
    """Ручка дома проверяет место сама."""
    player = make_player(location=CLUB)
    await db.save_player(player)
    answer = await client.get("/api/flats", headers=headers())
    assert answer.status == 409

    player.location = HOUSE
    await db.save_player(player)
    answer = await client.get("/api/flats", headers=headers())
    assert answer.status == 200
    body = await answer.json()
    assert body["flats"] == 16 and body["mine"] == {}


async def test_the_whole_lease_walks_through_the_app(client, db):
    """Снять, положить вещь, продлить и выселиться — через мини-апп."""
    player = await enlist(db, 42, "Тайлер", credits=3000)
    item_id = await stock(db, player)

    answer = await client.post(
        "/api/flats", headers=headers(), json={"action": "rent", "flat": 4}
    )
    assert answer.status == 200, await answer.text()
    body = await answer.json()
    assert "снята" in body["said"] and body["mine"]["rooms"] == 3
    assert body["pantry"]["slots"] == 18 and body["credits"] == 2500

    answer = await client.post(
        "/api/flats", headers=headers(),
        json={"action": "deposit", "amount": 400},
    )
    assert answer.status == 200, await answer.text()
    assert (await answer.json())["pantry"]["vault"] == 400

    answer = await client.post(
        "/api/flats", headers=headers(), json={"action": "stash", "item": item_id}
    )
    assert answer.status == 200, await answer.text()
    body = await answer.json()
    assert [one["title"] for one in body["pantry"]["items"]] == ["Бандана"]
    assert body["pantry"]["free"] == 17 and body["pantry"]["bag"] == []

    answer = await client.post(
        "/api/flats", headers=headers(), json={"action": "extend"}
    )
    assert answer.status == 200, await answer.text()
    assert "продлена" in (await answer.json())["said"]

    # Чужая квартира чужими руками не открывается
    await enlist(db, 43, "Марла", credits=2000)
    answer = await client.post(
        "/api/flats", headers=headers(43), json={"action": "rent", "flat": 4}
    )
    assert answer.status == 409
    assert "занята" in (await answer.json())["error"]

    answer = await client.post(
        "/api/flats", headers=headers(), json={"action": "leave"}
    )
    assert answer.status == 200, await answer.text()
    body = await answer.json()
    assert "выселился" in body["said"] and body["mine"] == {}
    assert [one["title"] for one in body["pantry"]["bag"]] == []
    assert body["card"]["inventory"] and body["credits"] == 2500 - 400 + 400 - 500

    answer = await client.post(
        "/api/flats", headers=headers(), json={"action": "залезть в окно"}
    )
    assert answer.status == 400
