"""Комиссионка: выставил, снял, купил — и что при этом с деньгами и износом."""

import pytest

from bot.database import Database
from bot.game.equipment import CATALOGUE, MAX_WEAR, OwnedItem, Slot
from bot.game.market import (
    MAX_SHARE,
    MIN_SHARE,
    fee_of,
    payout,
    price_is_sane,
    price_range,
)
from bot.market_service import MarketError, buy_lot, price_hint, sell_lot, withdraw_lot
from bot.models import Player


def make_player(user_id: int, nickname: str, credits: int = 0) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", credits=credits
    )


async def with_gear(db: Database, player: Player, code: str, wear: int = 0):
    await db.save_player(player)
    owned = await db.add_gear(player.user_id, code, wear=wear)
    player.gear.append(owned)
    return owned


# ---------- цена и комиссия ----------


def test_the_price_stays_between_half_and_three_prices():
    knife = CATALOGUE["knife"]

    low, high = price_range(knife)

    assert (low, high) == (round(knife.price * MIN_SHARE), round(knife.price * MAX_SHARE))
    assert price_is_sane(knife, low) and price_is_sane(knife, high)
    assert not price_is_sane(knife, low - 1)
    assert not price_is_sane(knife, high + 1)


def test_what_the_shop_never_sold_costs_whatever_you_ask():
    """Сравнить не с чем: у наград и товара мага цены на прилавке нет."""
    saber = CATALOGUE["lightsaber"]  # только за звёзды
    blade = CATALOGUE["hidden_blade"]  # награда за подписку

    assert price_range(saber) is None and price_range(blade) is None
    assert price_is_sane(saber, 5) and price_is_sane(blade, 100000)
    assert not price_is_sane(saber, 0)  # даром не отдают
    assert "цена любая" in price_hint(saber)


def test_the_club_takes_five_percent_and_rounds_them_up():
    """Округляем в пользу клуба: полкредита комиссии — это кредит."""
    assert (fee_of(100), payout(100)) == (5, 95)
    assert (fee_of(101), payout(101)) == (6, 95)
    assert fee_of(20) == 1
    assert payout(1) == 0  # с рубля клуб забирает рубль


# ---------- выставить и снять ----------


async def test_an_item_leaves_the_backpack_when_it_goes_on_sale(db):
    seller = make_player(1, "Victor")
    knife = await with_gear(db, seller, "knife", wear=4)

    lot_id = await sell_lot(db, seller, knife.id, 120)

    assert await db.list_gear(1) == []
    assert seller.gear == []
    lot = await db.market_lot(lot_id)
    assert (lot["code"], lot["wear"], lot["price"]) == ("knife", 4, 120)
    assert lot["seller"] == "Victor"  # видно, кто выставил


async def test_worn_gear_cannot_be_sold_off_your_own_back(db):
    seller = make_player(1, "Victor")
    knife = await with_gear(db, seller, "knife")
    knife.slot = Slot.WEAPON

    with pytest.raises(MarketError, match="надета"):
        await sell_lot(db, seller, knife.id, 120)

    assert await db.market_lots() == []


async def test_a_price_outside_the_range_is_refused(db):
    seller = make_player(1, "Victor")
    knife = await with_gear(db, seller, "knife")

    with pytest.raises(MarketError, match="от 55 до 330"):
        await sell_lot(db, seller, knife.id, 20)

    assert len(await db.list_gear(1)) == 1  # вещь осталась при хозяине


async def test_someone_elses_gear_is_not_yours_to_sell(db):
    seller = make_player(1, "Victor")
    await db.save_player(seller)

    with pytest.raises(MarketError, match="Этой вещи у тебя нет"):
        await sell_lot(db, seller, 999, 120)


async def test_a_lot_can_be_taken_back_with_its_wear(db):
    seller = make_player(1, "Victor")
    knife = await with_gear(db, seller, "knife", wear=7)
    lot_id = await sell_lot(db, seller, knife.id, 120)

    title = await withdraw_lot(db, seller, lot_id)

    assert title == "Нож"
    back = await db.list_gear(1)
    assert [(row.code, row.wear) for row in back] == [("knife", 7)]
    assert await db.market_lots() == []


async def test_only_the_seller_takes_the_lot_back(db):
    seller = make_player(1, "Victor")
    stranger = make_player(2, "x RED x")
    await db.save_player(stranger)
    knife = await with_gear(db, seller, "knife")
    lot_id = await sell_lot(db, seller, knife.id, 120)

    with pytest.raises(MarketError, match="только тот, кто выставил"):
        await withdraw_lot(db, stranger, lot_id)


# ---------- покупка ----------


async def test_a_purchase_moves_the_item_and_splits_the_money(db):
    seller = make_player(1, "Victor")
    buyer = make_player(2, "x RED x", credits=500)
    await db.save_player(buyer)
    knife = await with_gear(db, seller, "knife", wear=6)
    lot_id = await sell_lot(db, seller, knife.id, 200)

    deal = await buy_lot(db, buyer, lot_id)

    assert deal["title"] == "Нож" and deal["seller"] == "Victor"
    assert (deal["price"], deal["fee"], deal["earned"]) == (200, 10, 190)
    # вещь у покупателя, с тем же износом
    assert [(row.code, row.wear) for row in await db.list_gear(2)] == [("knife", 6)]
    assert await db.list_gear(1) == []
    # деньги: покупатель заплатил всё, продавец получил за вычетом комиссии
    assert (await db.get_player(2)).credits == 300
    assert (await db.get_player(1)).credits == 190
    assert await db.market_lots() == []


async def test_you_cannot_buy_your_own_lot(db):
    seller = make_player(1, "Victor", credits=500)
    knife = await with_gear(db, seller, "knife")
    lot_id = await sell_lot(db, seller, knife.id, 120)

    with pytest.raises(MarketError, match="твой же лот"):
        await buy_lot(db, seller, lot_id)


async def test_an_empty_purse_buys_nothing(db):
    seller = make_player(1, "Victor")
    buyer = make_player(2, "x RED x", credits=50)
    await db.save_player(buyer)
    knife = await with_gear(db, seller, "knife")
    lot_id = await sell_lot(db, seller, knife.id, 200)

    with pytest.raises(MarketError, match="Не хватает кредитов"):
        await buy_lot(db, buyer, lot_id)

    assert (await db.get_player(2)).credits == 50
    assert len(await db.market_lots()) == 1  # лот остался на полке


async def test_a_lot_is_sold_once_even_if_two_press_together(db):
    """Лот забирает тот, чей DELETE прошёл первым: второму — отказ."""
    seller = make_player(1, "Victor")
    first = make_player(2, "x RED x", credits=500)
    second = make_player(3, "Марла", credits=500)
    await db.save_player(first)
    await db.save_player(second)
    knife = await with_gear(db, seller, "knife")
    lot_id = await sell_lot(db, seller, knife.id, 200)

    await buy_lot(db, first, lot_id)
    with pytest.raises(MarketError, match="разобрали"):
        await buy_lot(db, second, lot_id)

    assert (await db.get_player(3)).credits == 500
    assert len(await db.list_gear(2)) == 1 and await db.list_gear(3) == []


async def test_the_wear_survives_the_whole_journey(db):
    """Износ едет с вещью: продали пожившую — пожившую и купили."""
    seller = make_player(1, "Victor")
    buyer = make_player(2, "x RED x", credits=500)
    await db.save_player(buyer)
    worn = OwnedItem(item=CATALOGUE["knife"], wear=MAX_WEAR - 1, max_wear=MAX_WEAR)
    stored = await db.add_gear(1, worn.code, wear=worn.wear)
    seller.gear.append(stored)
    await db.save_player(seller)

    lot_id = await sell_lot(db, seller, stored.id, 60)
    await buy_lot(db, buyer, lot_id)

    bought = (await db.list_gear(2))[0]
    assert bought.wear == MAX_WEAR - 1
    assert bought.max_wear == MAX_WEAR


# ---------- ручка мини-аппа ----------


async def market_client(db):
    """Мини-апп с комиссионкой на этой базе."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot.config import Config
    from bot.webapp.server import create_app
    from tests.test_webapp import FakeBot, TOKEN

    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    return TestClient(TestServer(app))


def headers(user_id: int) -> dict:
    from tests.test_webapp import make_init_data

    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


async def test_the_market_endpoint_shows_shelves_and_your_own_gear(db):
    seller = make_player(1, "Victor")
    buyer = make_player(2, "x RED x", credits=500)
    await db.save_player(buyer)
    await db.add_gear(2, "bandana")
    knife = await with_gear(db, seller, "knife", wear=5)
    await sell_lot(db, seller, knife.id, 150)

    async with await market_client(db) as client:
        response = await client.get("/api/market", headers=headers(2))
        body = await response.json()

    assert response.status == 200
    assert body["fee"] == 5
    weapon = next(row for row in body["sections"] if row["slot"] == "weapon")
    lot = weapon["items"][0]
    assert (lot["title"], lot["seller"], lot["price"]) == ("Нож", "Victor", 150)
    assert lot["mine"] is False and lot["wear"] == 5
    # своё из рюкзака можно выставить, и видно, в каких рамках
    assert [row["title"] for row in body["sellable"]] == ["Бандана"]
    assert body["sellable"][0]["hint"] == "От 20 до 120 💰"


async def test_the_market_endpoint_sells_withdraws_and_buys(db):
    seller = make_player(1, "Victor")
    buyer = make_player(2, "x RED x", credits=500)
    await db.save_player(buyer)
    knife = await with_gear(db, seller, "knife")

    async with await market_client(db) as client:
        sold = await client.post(
            "/api/market",
            json={"action": "sell", "item_id": knife.id, "price": 200},
            headers=headers(1),
        )
        body = await sold.json()
        assert sold.status == 200
        lot_id = body["sections"][0]["items"][0]["id"]
        assert body["sections"][0]["items"][0]["mine"] is True

        bought = await client.post(
            "/api/market",
            json={"action": "buy", "lot_id": lot_id},
            headers=headers(2),
        )
        assert bought.status == 200
        assert (await bought.json())["sections"] == []

        # покупать нечего — и снимать тоже
        gone = await client.post(
            "/api/market",
            json={"action": "withdraw", "lot_id": lot_id},
            headers=headers(1),
        )
        assert gone.status == 409
        assert "уже нет" in (await gone.json())["error"]

    assert (await db.get_player(1)).credits == 190
    assert (await db.get_player(2)).credits == 300


async def test_the_market_endpoint_refuses_a_wrong_price(db):
    seller = make_player(1, "Victor")
    knife = await with_gear(db, seller, "knife")

    async with await market_client(db) as client:
        response = await client.post(
            "/api/market",
            json={"action": "sell", "item_id": knife.id, "price": 5},
            headers=headers(1),
        )
        body = await response.json()

    assert response.status == 409
    assert "от 55 до 330" in body["error"]
