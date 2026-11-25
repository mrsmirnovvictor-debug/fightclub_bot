"""Трансферный рынок: бойцов продают, меняют и доплачивают.

Главная забота набора — чтобы ни один боец не достался двоим и ни один
не пропал между карманами. Поэтому почти каждая проверка здесь про то,
что сделка смотрит на мир заново в тот самый миг, когда её заключают:
между «выставил» и «купил» проходят часы.
"""

import random

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats, get_class
from bot.game.squad import SQUAD_LIMIT, npc_stats
from bot.models import Player
from bot.squad_service import SquadError, dismiss, to_hospital
from bot.transfer_service import (
    MIN_PRICE,
    TransferError,
    accept,
    buy,
    decline,
    offer,
    put_up,
    take_down,
    withdraw,
)
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

NOW = 1_800_000_000


def a_player(user_id: int = 42, level: int = 9, credits: int = 10_000) -> Player:
    return Player(
        user_id=user_id,
        nickname=f"Боец {user_id}",
        class_code="warrior",
        level=level,
        credits=credits,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def a_fighter(db, owner_id: int, level: int = 3, name: str = "Громила"):
    """Боец в отряде этого игрока."""
    npc_id = await db.next_npc_id()
    stats = npc_stats(get_class("tank"), 1, level)
    npc = Player(
        user_id=npc_id, nickname=name, class_code="tank", level=level,
        **stats.as_dict(),
    )
    await db.save_player(npc)
    await db.add_squad(npc_id, owner_id, 1, 1, NOW)
    return npc


async def two_sides(db, seller_level: int = 9, buyer_level: int = 9):
    seller = a_player(42, level=seller_level)
    buyer = a_player(43, level=buyer_level)
    await db.save_player(seller)
    await db.save_player(buyer)
    return seller, buyer


# ---------- лот ----------


async def test_a_listed_fighter_still_fights_for_his_owner(db):
    """Продаётся человек, а не место: выставленный остаётся в отряде."""
    seller, _ = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)

    await put_up(db, seller, npc.user_id, 500, NOW)
    assert len(await db.squad_of(seller.user_id)) == 1
    lot = await db.transfer_of(npc.user_id)
    assert lot["price"] == 500 and lot["owner_id"] == seller.user_id


async def test_a_busy_fighter_is_not_for_sale(db):
    """Занятого не продают: сделка увела бы его из-под обязательств."""
    seller, _ = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)

    await db.set_squad_spot(npc.user_id, "bar")
    with pytest.raises(TransferError, match="точку"):
        await put_up(db, seller, npc.user_id, 500, NOW)

    await db.set_squad_spot(npc.user_id, "")
    await to_hospital(db, npc.user_id, NOW)
    with pytest.raises(TransferError, match="больнице"):
        await put_up(db, seller, npc.user_id, 500, NOW)


async def test_the_price_has_a_floor(db):
    """Лот за единицу — это не продажа, а передача: для неё рынок не нужен."""
    seller, _ = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    with pytest.raises(TransferError, match="Цена"):
        await put_up(db, seller, npc.user_id, MIN_PRICE - 1, NOW)


async def test_only_the_owner_lists_and_unlists(db):
    """Чужого бойца не выставить и чужой лот не снять."""
    seller, buyer = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    with pytest.raises(TransferError, match="не твой"):
        await put_up(db, buyer, npc.user_id, 500, NOW)

    await put_up(db, seller, npc.user_id, 500, NOW)
    with pytest.raises(TransferError, match="не твой"):
        await take_down(db, buyer, npc.user_id)


# ---------- покупка ----------


async def test_buying_moves_the_fighter_and_the_money(db):
    """Боец уходит в чужой отряд, деньги — продавцу."""
    seller, buyer = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)
    purse = buyer.credits

    await buy(db, buyer, npc.user_id, NOW)
    assert (await db.squad_row(npc.user_id))["owner_id"] == buyer.user_id
    assert await db.squad_of(seller.user_id) == []
    assert buyer.credits == purse - 500
    assert (await db.get_player(seller.user_id)).credits == 10_000 + 500
    assert await db.transfer_of(npc.user_id) is None, "лот закрыт"


async def test_the_gear_stays_with_the_seller(db):
    """За экипировку платил продавец: продают человека, а не рюкзак.

    Так же устроено увольнение, и двум похожим случаям незачем
    расходиться.
    """
    seller, buyer = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    await db.add_gear(npc.user_id, "knuckles")
    await put_up(db, seller, npc.user_id, 500, NOW)

    await buy(db, buyer, npc.user_id, NOW)
    assert [one.item.code for one in await db.list_gear(seller.user_id)] == [
        "knuckles"
    ]
    assert await db.list_gear(npc.user_id) == []


async def test_nobody_buys_a_fighter_older_than_himself(db):
    """Купить бойца выше себя нельзя: сделка не умеет того, чего не умеет рост."""
    seller, buyer = await two_sides(db, buyer_level=3)
    npc = await a_fighter(db, seller.user_id, level=7)
    await put_up(db, seller, npc.user_id, 500, NOW)

    with pytest.raises(TransferError, match="старше хозяина"):
        await buy(db, buyer, npc.user_id, NOW)
    assert (await db.squad_row(npc.user_id))["owner_id"] == seller.user_id


async def test_a_poor_buyer_gets_nothing(db):
    """Денег нет — и боец остаётся у продавца."""
    seller, buyer = await two_sides(db)
    buyer.credits = 100
    await db.save_player(buyer)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)

    with pytest.raises(TransferError, match="стоит"):
        await buy(db, buyer, npc.user_id, NOW)
    assert (await db.squad_row(npc.user_id))["owner_id"] == seller.user_id


async def test_a_full_squad_cannot_buy(db):
    """Шесть в отряде — покупать некуда."""
    seller, buyer = await two_sides(db)
    for number in range(SQUAD_LIMIT):
        await a_fighter(db, buyer.user_id, name=f"Свой {number}")
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)

    with pytest.raises(TransferError, match="освободи место"):
        await buy(db, buyer, npc.user_id, NOW)


async def test_a_sold_fighter_is_not_sold_twice(db):
    """Второй покупатель приходит к пустому месту, а не ко второму бойцу."""
    seller, buyer = await two_sides(db)
    third = a_player(44)
    await db.save_player(third)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)

    await buy(db, buyer, npc.user_id, NOW)
    with pytest.raises(TransferError, match="лота больше нет"):
        await buy(db, third, npc.user_id, NOW)


async def test_a_dismissed_fighter_takes_his_lot_with_him(db):
    """Уволил выставленного — лот исчез вместе с ним."""
    seller, buyer = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)

    await dismiss(db, seller, npc.user_id)
    assert await db.transfer_of(npc.user_id) is None
    with pytest.raises(TransferError):
        await buy(db, buyer, npc.user_id, NOW)


# ---------- обмен ----------


async def test_a_swap_trades_fighters_and_pays_the_difference(db):
    """Боец за бойца с доплатой: оба меняются местами, деньги идут продавцу."""
    seller, buyer = await two_sides(db)
    mine = await a_fighter(db, seller.user_id, name="Мой")
    theirs = await a_fighter(db, buyer.user_id, name="Чужой")
    await put_up(db, seller, mine.user_id, 500, NOW)

    bid = await offer(db, buyer, mine.user_id, theirs.user_id, 200, NOW)
    done = await accept(db, seller, bid, NOW)

    assert done["gave"].user_id == mine.user_id
    assert done["got"].user_id == theirs.user_id
    assert (await db.squad_row(mine.user_id))["owner_id"] == buyer.user_id
    assert (await db.squad_row(theirs.user_id))["owner_id"] == seller.user_id
    assert (await db.get_player(seller.user_id)).credits == 10_000 + 200
    assert (await db.get_player(buyer.user_id)).credits == 10_000 - 200


async def test_an_offer_does_not_take_the_money_until_it_is_accepted(db):
    """Предложение может провисеть сутки — платить за ожидание не надо."""
    seller, buyer = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)

    await offer(db, buyer, npc.user_id, 0, 300, NOW)
    assert (await db.get_player(buyer.user_id)).credits == 10_000


async def test_an_empty_offer_is_refused(db):
    """Ни бойца, ни денег — это не предложение."""
    seller, buyer = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)

    with pytest.raises(TransferError, match="Пустое предложение"):
        await offer(db, buyer, npc.user_id, 0, 0, NOW)


async def test_a_swap_is_checked_again_at_the_hour_of_the_answer(db):
    """За сутки у покупателя мог кончиться и боец, и кошелёк."""
    seller, buyer = await two_sides(db)
    mine = await a_fighter(db, seller.user_id, name="Мой")
    theirs = await a_fighter(db, buyer.user_id, name="Чужой")
    await put_up(db, seller, mine.user_id, 500, NOW)
    bid = await offer(db, buyer, mine.user_id, theirs.user_id, 200, NOW)

    # Предложенного бойца тем временем уложили
    await to_hospital(db, theirs.user_id, NOW)
    with pytest.raises(TransferError, match="больнице"):
        await accept(db, seller, bid, NOW)
    assert (await db.squad_row(mine.user_id))["owner_id"] == seller.user_id


async def test_a_broke_buyer_cannot_be_accepted(db):
    """Доплаты нет — размена нет."""
    seller, buyer = await two_sides(db)
    mine = await a_fighter(db, seller.user_id, name="Мой")
    theirs = await a_fighter(db, buyer.user_id, name="Чужой")
    await put_up(db, seller, mine.user_id, 500, NOW)
    bid = await offer(db, buyer, mine.user_id, theirs.user_id, 5_000, NOW)

    buyer.credits = 100
    await db.save_player(buyer)
    with pytest.raises(TransferError, match="не хватает денег"):
        await accept(db, seller, bid, NOW)


async def test_a_full_squad_still_swaps(db):
    """При размене отдающий освобождает своё же место.

    Запрещать обмен полному отряду было бы неправдой: боец уходит, и
    место под встречного освобождается в тот же миг.
    """
    seller, buyer = await two_sides(db)
    mine = await a_fighter(db, seller.user_id, name="Мой")
    for number in range(SQUAD_LIMIT - 1):
        await a_fighter(db, seller.user_id, name=f"Запас {number}")
    theirs = await a_fighter(db, buyer.user_id, name="Чужой")
    for number in range(SQUAD_LIMIT - 1):
        await a_fighter(db, buyer.user_id, name=f"Их запас {number}")
    assert len(await db.squad_of(seller.user_id)) == SQUAD_LIMIT
    assert len(await db.squad_of(buyer.user_id)) == SQUAD_LIMIT

    await put_up(db, seller, mine.user_id, 500, NOW)
    bid = await offer(db, buyer, mine.user_id, theirs.user_id, 0, NOW)
    await accept(db, seller, bid, NOW)
    assert len(await db.squad_of(seller.user_id)) == SQUAD_LIMIT
    assert len(await db.squad_of(buyer.user_id)) == SQUAD_LIMIT


async def test_only_the_seller_answers_and_only_the_buyer_withdraws(db):
    """Чужое предложение не принять и не забрать."""
    seller, buyer = await two_sides(db)
    third = a_player(44)
    await db.save_player(third)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)
    bid = await offer(db, buyer, npc.user_id, 0, 300, NOW)

    with pytest.raises(TransferError, match="не твой"):
        await accept(db, third, bid, NOW)
    with pytest.raises(TransferError, match="не твоё"):
        await withdraw(db, third, bid)

    await decline(db, seller, bid)
    assert await db.bid_row(bid) is None


async def test_taking_the_lot_down_clears_its_offers(db):
    """Снял лот — предложения по нему уходят вместе с ним."""
    seller, buyer = await two_sides(db)
    npc = await a_fighter(db, seller.user_id)
    await put_up(db, seller, npc.user_id, 500, NOW)
    bid = await offer(db, buyer, npc.user_id, 0, 300, NOW)

    await take_down(db, seller, npc.user_id)
    assert await db.bid_row(bid) is None
    assert await db.bids_for(npc.user_id) == []


# ---------- рынок через http ----------
#
# Служба проверена выше по одной сделке; здесь — что экран школы
# показывает три полки раздельно и что кнопки на них доходят до службы.
# Семь действий в одной ручке легко перепутать местами, и такая путаница
# тихая: «принять» вместо «отклонить» выглядит как успешный ответ.


SCHOOL = "martial_arts_school"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


async def at_school(db, user_id: int, level: int = 9, credits: int = 10_000):
    """Игрок, который стоит в школе: рынок — место, а не кнопка."""
    one = a_player(user_id, level=level, credits=credits)
    one.location = SCHOOL
    one.seen_at = NOW
    await db.save_player(one)
    return one


async def test_the_market_is_only_open_inside_the_school(client, db):
    """Рынок — это место: с улицы в него не заглядывают."""
    seller = await at_school(db, 42)
    seller.location = "street"
    await db.save_player(seller)
    answer = await client.get("/api/transfers", headers=headers(42))
    assert answer.status == 409


async def test_the_market_keeps_my_lots_apart_from_the_others(client, db):
    """Три полки: чужие лоты, свои лоты и свои предложения."""
    seller = await at_school(db, 42)
    buyer = await at_school(db, 43)
    mine = await a_fighter(db, seller.user_id, name="Громила")
    theirs = await a_fighter(db, buyer.user_id, name="Тихий")
    await put_up(db, seller, mine.user_id, 500, NOW)
    await put_up(db, buyer, theirs.user_id, 400, NOW)

    body = await (await client.get("/api/transfers", headers=headers(42))).json()
    assert [one["name"] for one in body["mine"]] == ["Громила"]
    assert [one["name"] for one in body["lots"]] == ["Тихий"]
    assert body["lots"][0]["owner"] == buyer.nickname
    assert body["offers"] == []


async def test_the_market_says_why_a_lot_cannot_be_taken(client, db):
    """Отказ написан до нажатия: выше уровнем — значит не твой."""
    seller = await at_school(db, 42)
    await at_school(db, 43, level=2)
    npc = await a_fighter(db, seller.user_id, level=5)
    await put_up(db, seller, npc.user_id, 500, NOW)

    body = await (await client.get("/api/transfers", headers=headers(43))).json()
    lot = body["lots"][0]
    assert lot["can_buy"] is False
    assert lot["why"] == "Он 5 уровня, а ты 2."


async def test_listing_and_buying_go_through_the_market_window(client, db):
    """Выставил — купили: обе кнопки доходят до службы."""
    seller = await at_school(db, 42)
    await at_school(db, 43)
    npc = await a_fighter(db, seller.user_id)

    put = await client.post(
        "/api/transfers",
        json={"action": "put_up", "npc": npc.user_id, "price": 500},
        headers=headers(42),
    )
    assert put.status == 200
    assert "500" in (await put.json())["said"]

    bought = await client.post(
        "/api/transfers",
        json={"action": "buy", "npc": npc.user_id},
        headers=headers(43),
    )
    assert bought.status == 200
    row = await db.squad_row(npc.user_id)
    assert row["owner_id"] == 43
    assert (await db.get_player(42)).credits == 10_500


async def test_an_offer_reaches_the_owner_and_comes_back_answered(client, db):
    """Предложение видно обоим: покупателю — своё, хозяину — чужое."""
    seller = await at_school(db, 42)
    buyer = await at_school(db, 43)
    lot = await a_fighter(db, seller.user_id, name="Громила")
    give = await a_fighter(db, buyer.user_id, name="Тихий")
    await put_up(db, seller, lot.user_id, 500, NOW)

    made = await client.post(
        "/api/transfers",
        json={
            "action": "offer",
            "npc": lot.user_id,
            "give": give.user_id,
            "extra": 100,
        },
        headers=headers(43),
    )
    assert made.status == 200
    mine = (await made.json())["offers"]
    assert mine[0]["lot"]["name"] == "Громила"
    assert mine[0]["give"]["name"] == "Тихий"
    assert mine[0]["extra"] == 100

    seen = await (await client.get("/api/transfers", headers=headers(42))).json()
    bid = seen["mine"][0]["bids"][0]
    assert bid["buyer"] == buyer.nickname
    assert bid["give"]["name"] == "Тихий"

    taken = await client.post(
        "/api/transfers",
        json={"action": "accept", "bid": bid["id"]},
        headers=headers(42),
    )
    assert taken.status == 200
    assert (await db.squad_row(lot.user_id))["owner_id"] == 43
    assert (await db.squad_row(give.user_id))["owner_id"] == 42


async def test_a_refusal_from_the_market_comes_back_in_words(client, db):
    """Отказ службы доходит до экрана как есть, а не как поломка."""
    seller = await at_school(db, 42)
    npc = await a_fighter(db, seller.user_id)
    answer = await client.post(
        "/api/transfers",
        json={"action": "put_up", "npc": npc.user_id, "price": 1},
        headers=headers(42),
    )
    assert answer.status == 409
    assert str(MIN_PRICE) in (await answer.json())["error"]


async def test_the_market_does_not_offer_a_busy_fighter(client, db):
    """На полке «кого продать» только свободные: занятого не продают."""
    seller = await at_school(db, 42)
    free = await a_fighter(db, seller.user_id, name="Громила")
    held = await a_fighter(db, seller.user_id, name="Часовой")
    await db.set_squad_spot(held.user_id, "bar")

    body = await (await client.get("/api/transfers", headers=headers(42))).json()
    assert [one["name"] for one in body["free"]] == ["Громила"]
    assert free.user_id in [one["id"] for one in body["free"]]
