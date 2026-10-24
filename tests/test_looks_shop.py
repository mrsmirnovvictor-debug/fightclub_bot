"""Образы продают в городе: барбершоп и салон красоты.

Прежде внешность покупали прямо в карточке, нажав на лицо. Теперь за ней
ходят ногами, как за всем остальным, и проверяем мы четыре вещи:

1. **Мужские лица режут в барбершопе, женские делают в салоне красоты.**
   Экран один, домов два, и разница в том, что лежит на полке.
2. **Смотреть может любой, купить — только своего пола.** Женщина в
   барбершопе видит полку целиком и не берёт с неё ничего.
3. **В карточке торговли больше нет.** Там переключаются между своими, и
   чужой образ оттуда не купить — ни кнопкой, ни запросом мимо неё.
4. **Купленное и надетое не отнимают.** Правило старое, и смена места
   продажи его не трогает.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.game.classes import Stats
from bot.game.locations import Service, get_location
from bot.game.looks import (
    FEMALE,
    LOOK_PRICE,
    MALE,
    gender_at,
    looks_of_gender,
    shop_of,
)
from bot.looks_service import LookError, buy_look, shelf, wardrobe, wear_look
from bot.models import Player
from bot.webapp.looks import build_barber
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

NOW = 1_800_000_000
BARBER = "barbershop"
SALON = "beauty_salon"
CLUB = "fight_club"
HIS, HERS = "male_01_boxer", "female_01_boxer"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


def make_player(
    user_id: int = 42, nickname: str = "Тайлер", gender: str = MALE,
    where: str = BARBER, credits: int = 1_000,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=where, seen_at=NOW, gender=gender,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def visitor(db: Database, **kwargs) -> Player:
    player = make_player(**kwargs)
    await db.save_player(player)
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


# ---------- дома ----------


def test_two_houses_sell_looks_and_each_sells_its_own():
    """Барбершоп режет мужские лица, салон красоты делает женские."""
    for code, gender in ((BARBER, MALE), (SALON, FEMALE)):
        place = get_location(code)
        assert place.allows(Service.LOOKS)
        assert place.services[0] is Service.LOOKS, "дверь ведёт на полку"
        assert Service.WORK in place.services, "место мастера осталось"
        assert place.works and not place.soon
        assert gender_at(code) == gender
        assert shop_of(gender) == code

    # Больше образами никто не торгует
    assert gender_at(CLUB) == ""


# ---------- полка ----------


async def test_the_shelf_holds_every_look_of_this_house(db):
    """На полке все образы своего пола: и открытые, и платные."""
    player = await visitor(db)

    rows = await shelf(db, player, BARBER, NOW)

    assert {one["gender"] for one in rows} == {MALE}
    assert {one["code"] for one in rows} == {
        one.code for one in looks_of_gender(MALE) if not one.pro
    }
    boxer = next(one for one in rows if one["code"] == HIS)
    assert boxer["price"] == LOOK_PRICE == 200
    assert boxer["owned"] is False and boxer["can_buy"] is True
    assert boxer["why"] == ""
    # Открытый всем образ покупать незачем — он и так свой
    free = next(one for one in rows if one["code"] == "rookie")
    assert free["owned"] is True and free["can_buy"] is False
    assert "и так твой" in free["why"]


async def test_a_woman_may_look_at_the_barbershop_but_not_buy(db):
    """Смотреть может любой, купить — только своего пола."""
    player = await visitor(db, gender=FEMALE)

    rows = await shelf(db, player, BARBER, NOW)

    assert len(rows) == 12, "полка видна целиком"
    assert all(not one["can_buy"] for one in rows)
    boxer = next(one for one in rows if one["code"] == HIS)
    assert "берут их мужчины" in boxer["why"]
    # И запрос мимо страницы её тоже не пустит
    with pytest.raises(LookError, match="берут их мужчины"):
        await buy_look(db, player, HIS, BARBER, NOW)
    assert (await db.get_player(42)).credits == 1_000


async def test_a_man_cannot_buy_in_the_beauty_salon(db):
    """И обратно: мужчина в салоне красоты только смотрит."""
    player = await visitor(db, where=SALON)

    rows = await shelf(db, player, SALON, NOW)

    assert {one["gender"] for one in rows} == {FEMALE}
    assert all(not one["can_buy"] for one in rows)
    with pytest.raises(LookError, match="берут их женщины"):
        await buy_look(db, player, HERS, SALON, NOW)


async def test_the_other_houses_look_is_not_on_this_shelf(db):
    """Женский образ в барбершопе не купить: в этом доме делают другие."""
    player = await visitor(db, gender=FEMALE)

    with pytest.raises(LookError, match="в этом доме делают другие"):
        await buy_look(db, player, HERS, BARBER, NOW)
    with pytest.raises(LookError, match="образами не торгуют"):
        await buy_look(db, player, HIS, CLUB, NOW)


# ---------- покупка ----------


async def test_a_bought_look_is_worn_right_away_and_stays_forever(db):
    """Из барбершопа выходят с новой стрижкой, а не с квитанцией."""
    player = await visitor(db)

    choice = await buy_look(db, player, HIS, BARBER, NOW)

    assert choice.bought is True and choice.credits == 800
    fresh = await db.get_player(42)
    assert fresh.look == HIS and fresh.credits == 800
    assert HIS in await db.owned_looks(42)
    # Второй раз его не продают
    with pytest.raises(LookError, match="и так твой"):
        await buy_look(db, fresh, HIS, BARBER, NOW)
    assert (await db.get_player(42)).credits == 800


async def test_the_shop_wants_the_money(db):
    """Нет кредитов — нет образа, и образ при этом не надевается."""
    player = await visitor(db, credits=10)

    with pytest.raises(LookError, match="Образ стоит"):
        await buy_look(db, player, HIS, BARBER, NOW)

    fresh = await db.get_player(42)
    assert fresh.look == "" and HIS not in await db.owned_looks(42)


async def test_the_old_handout_is_not_for_sale(db):
    """Образ прошлой выдачи не продают и в барбершопе."""
    player = await visitor(db)

    with pytest.raises(LookError, match="больше не выдают"):
        await buy_look(db, player, "assassin", BARBER, NOW)


# ---------- карточка: только своё ----------


async def test_the_card_holds_only_what_you_own(db):
    """В гардеробе лежат свои образы, и докупать там нечего."""
    player = await visitor(db)
    await db.add_look(player.user_id, HIS)

    rows = await wardrobe(db, player, NOW)

    assert {one["code"] for one in rows} == {
        "rookie", "worker", "racer", HIS,
    }
    assert all(one["owned"] for one in rows)


async def test_the_card_does_not_sell_anything(db):
    """Чужой образ из карточки не надеть: за ним идут в город."""
    player = await visitor(db, credits=10_000)

    with pytest.raises(LookError, match="За ним — в барбершоп"):
        await wear_look(db, player, "boss", NOW)

    assert (await db.get_player(42)).credits == 10_000
    assert (await db.get_player(42)).look == ""
    # А женский — тем более, и адрес у него свой
    with pytest.raises(LookError, match="салон красоты"):
        await wear_look(db, player, HERS, NOW)


async def test_your_own_look_goes_on_for_free(db):
    """Смена между своими бесплатна и мгновенна."""
    player = await visitor(db, credits=500)
    await db.add_look(player.user_id, HIS)

    choice = await wear_look(db, player, HIS, NOW)

    assert choice.bought is False and choice.credits == 500
    assert (await db.get_player(42)).look == HIS
    # И обратно на открытый всем
    await wear_look(db, player, "rookie", NOW)
    assert (await db.get_player(42)).look == "rookie"


# ---------- экран ----------


async def test_the_barber_screen_shows_the_shelf_and_the_purse(db):
    """Экран дома: полка, кошелёк и записка о том, что тут делают."""
    player = await visitor(db)

    body = await build_barber(db, player, NOW)

    assert body["title"] == get_location(BARBER).title
    assert body["sells"] == MALE and body["sells_title"] == "Мужские"
    assert body["mine"] is True
    assert len(body["shelf"]) == 12
    assert body["price"] == LOOK_PRICE
    assert "раз и навсегда" in body["note"]
    assert "«Персонаж»" in body["note"], "сказано, где переключаться"
    assert "Своих у тебя 3 из 12" in body["note"]


async def test_the_screen_tells_a_woman_where_her_looks_are(db):
    """Чужой дом не отказывает молча: он называет свой."""
    player = await visitor(db, gender=FEMALE)

    body = await build_barber(db, player, NOW)

    assert body["mine"] is False
    assert "смотреть можно, брать нет" in body["note"]
    assert "салон красоты" in body["note"]


async def test_the_shop_opens_only_inside_it(db, client):
    """Образы берут в доме: спрятанная кнопка обходится запросом."""
    await visitor(db)

    answer = await client.get("/api/barber", headers=headers())
    assert answer.status == 200
    assert len((await answer.json())["shelf"]) == 12

    answer = await client.post(
        "/api/barber", json={"action": "buy", "code": HIS}, headers=headers()
    )
    assert answer.status == 200
    assert "теперь твой навсегда" in (await answer.json())["said"]

    answer = await client.post(
        "/api/barber", json={"action": "стрижка"}, headers=headers()
    )
    assert answer.status == 400

    away = await db.get_player(42)
    away.location = CLUB
    await db.save_player(away)
    assert (await client.get("/api/barber", headers=headers())).status == 409
