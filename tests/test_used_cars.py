"""Авторынок: машины из рук в руки.

Комиссионка для вещей уже есть, и рынок сделан по её образцу. Поэтому
проверяем не «работает ли торговля вообще» — это проверено там, — а то,
чем машина отличается от вещи:

1. **Продают только свою насовсем.** Снятая в салоне принадлежит салону,
   служебная — гаражу, стоящая на парковке лежит в чужом гараже.
2. **Место считают у покупателя.** Машин на руках одна, с подпиской три,
   и рынок это правило не обходит.
3. **Деньги сходятся.** Сколько ушло у покупателя, столько пришло
   продавцу плюс доля рынка — и ни кредитом больше.
"""

import pytest

from bot.game.cars import cars_allowed, get_car
from bot.game.locations import Service, get_location, where_to
from bot.game.used_cars import (
    FEE,
    price_hint,
    price_is_sane,
    price_range,
    sellable,
)
from bot.used_cars_service import (
    UsedCarError,
    buy_car,
    sell_car,
    withdraw_car,
)
from tests.test_cars import HATCH, NOW, licensed, make_player

MARKET = "used_car_market"


async def trader(db, user_id: int = 42, name: str = "Тайлер", credits: int = 0):
    """Боец на рынке, с правами и деньгами."""
    player = await licensed(db, make_player(user_id, name, where=MARKET))
    player.credits = credits
    player.location = MARKET
    await db.save_player(player)
    return await db.get_player(user_id)


async def with_own_car(db, player, code: str = HATCH):
    """Выдать бойцу машину насовсем, минуя кассу салона."""
    await db.put_car(player.user_id, code, 0, NOW)
    return await db.get_player(player.user_id)


# ---------- дом и услуга ----------


def test_the_market_is_a_house_with_a_trade_of_its_own():
    """Рынок — не салон: там торгуют с городом, здесь друг с другом."""
    place = get_location(MARKET)

    assert place.allows(Service.USED_CARS)
    assert where_to(Service.USED_CARS).code == MARKET
    # Рынок стоит первым: дверь на карте открывает тот экран, что первый
    assert place.services[0] is Service.USED_CARS
    assert Service.WORK in place.services, "место дилера осталось"
    # Дом обжит: обещать ему больше нечего
    assert place.works and not place.soon


# ---------- цена ----------


def test_the_price_stays_within_reach_of_the_salon():
    """Вдвое дешевле — отдать даром, втрое дороже — печатать кредиты."""
    car = get_car(HATCH)
    low, high = price_range(car)

    assert (low, high) == (round(car.price * 0.5), round(car.price * 3.0))
    assert price_is_sane(car, low) and price_is_sane(car, high)
    assert not price_is_sane(car, low - 1)
    assert not price_is_sane(car, high + 1)
    assert not price_is_sane(car, 0) and not price_is_sane(car, -100)
    assert str(low) in price_hint(car) and str(high) in price_hint(car)


def test_a_service_car_is_not_for_sale_at_all():
    """Служебную выдал гараж, а не кошелёк: продавать нечего."""
    from bot.game.cars import PATROL_CAR, TAXI_CAR

    assert sellable(get_car(HATCH))
    assert not sellable(get_car(PATROL_CAR))
    assert not sellable(get_car(TAXI_CAR))


# ---------- выставить ----------


async def test_an_owned_car_goes_on_the_market(db):
    seller = await trader(db)
    await with_own_car(db, seller)
    car = get_car(HATCH)

    lot = await sell_car(db, seller, HATCH, car.price, NOW)

    assert lot > 0
    rows = await db.car_lots()
    assert [one["code"] for one in rows] == [HATCH]
    assert rows[0]["price"] == car.price
    # С бойца машина снята: она на рынке, а не у него
    assert await db.car_row(seller.user_id, HATCH) is None


async def test_a_rented_car_is_not_yours_to_sell(db):
    seller = await trader(db)
    await db.put_car(seller.user_id, HATCH, 24 * 60 * 60, NOW)

    with pytest.raises(UsedCarError, match="в аренде"):
        await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)

    assert await db.car_lots() == []
    assert await db.car_row(seller.user_id, HATCH) is not None


async def test_a_parked_car_is_fetched_before_it_is_sold(db):
    """Машина на парковке лежит в гараже, а не в руках у хозяина."""
    from bot.car_service import leave
    from bot.clan_service import found
    from bot.game.cars import IN_CLAN

    seller = await trader(db, credits=5_000)
    seller.location = "mafia_mansion"
    await db.save_player(seller)
    clan = await found(db, seller, "Коза Ностра", "black_ravens", now=NOW)
    seller = await db.get_player(seller.user_id)
    await with_own_car(db, seller)
    await leave(db, seller, HATCH, IN_CLAN, clan.id, NOW)
    seller.location = MARKET
    await db.save_player(seller)

    with pytest.raises(UsedCarError, match="на парковке"):
        await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)


async def test_the_car_under_you_is_not_sold_from_under_you(db):
    from bot.car_service import wheel

    seller = await trader(db)
    await with_own_car(db, seller)
    await wheel(db, seller, HATCH, NOW)

    with pytest.raises(UsedCarError, match="за рулём"):
        await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)


async def test_a_price_out_of_range_is_refused(db):
    seller = await trader(db)
    await with_own_car(db, seller)
    low, high = price_range(get_car(HATCH))

    for price in (low - 1, high + 1, 0):
        with pytest.raises(UsedCarError, match="Цена не та"):
            await sell_car(db, seller, HATCH, price, NOW)

    assert await db.car_lots() == []
    assert await db.car_row(seller.user_id, HATCH) is not None


async def test_a_car_you_do_not_have_is_not_sold(db):
    seller = await trader(db)

    with pytest.raises(UsedCarError, match="нет"):
        await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)


# ---------- снять с продажи ----------


async def test_the_seller_takes_his_car_back(db):
    seller = await trader(db)
    await with_own_car(db, seller)
    lot = await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)

    title = await withdraw_car(db, seller, lot, NOW)

    assert title == get_car(HATCH).title
    assert await db.car_lots() == []
    row = await db.car_row(seller.user_id, HATCH)
    assert row is not None and not row["until"], "вернулась своей насовсем"


async def test_only_the_one_who_listed_it_takes_it_back(db):
    seller = await trader(db)
    await with_own_car(db, seller)
    lot = await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)
    other = await trader(db, 7, "Чужак")

    with pytest.raises(UsedCarError, match="кто выставил"):
        await withdraw_car(db, other, lot, NOW)


# ---------- купить ----------


async def test_the_money_adds_up_on_both_sides(db):
    """Сколько ушло у покупателя, столько пришло продавцу и рынку."""
    car = get_car(HATCH)
    price = car.price
    seller = await trader(db)
    await with_own_car(db, seller)
    lot = await sell_car(db, seller, HATCH, price, NOW)
    buyer = await trader(db, 7, "Покупатель", credits=price + 500)

    deal = await buy_car(db, buyer, lot, NOW)

    assert deal["price"] == price
    assert deal["fee"] + deal["earned"] == price, "доля рынка и выплата сходятся"
    assert deal["fee"] == round(price * FEE)
    assert deal["seller"] == "Тайлер"

    fresh_buyer = await db.get_player(buyer.user_id)
    fresh_seller = await db.get_player(seller.user_id)
    assert fresh_buyer.credits == 500
    assert fresh_seller.credits == deal["earned"]
    # Машина переехала целиком: у покупателя своя, у продавца её нет
    assert await db.car_row(buyer.user_id, HATCH) is not None
    assert await db.car_row(seller.user_id, HATCH) is None
    assert await db.car_lots() == []


async def test_your_own_lot_is_withdrawn_and_not_bought(db):
    seller = await trader(db, credits=100_000)
    await with_own_car(db, seller)
    lot = await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)

    with pytest.raises(UsedCarError, match="твой же лот"):
        await buy_car(db, seller, lot, NOW)


async def test_a_car_you_cannot_afford_stays_on_the_market(db):
    price = get_car(HATCH).price
    seller = await trader(db)
    await with_own_car(db, seller)
    lot = await sell_car(db, seller, HATCH, price, NOW)
    buyer = await trader(db, 7, "Бедный", credits=price - 1)

    with pytest.raises(UsedCarError, match="Не хватает"):
        await buy_car(db, buyer, lot, NOW)

    assert len(await db.car_lots()) == 1
    assert (await db.get_player(buyer.user_id)).credits == price - 1


async def test_the_market_does_not_go_around_the_car_limit(db):
    """На руках одна машина: рынок это правило соблюдает, как и салон."""
    from bot.game.cars import SHOWCASE

    other = next(one for one in SHOWCASE if one.code != HATCH)
    seller = await trader(db)
    await with_own_car(db, seller, other.code)
    lot = await sell_car(db, seller, other.code, other.price, NOW)

    buyer = await trader(db, 7, "Покупатель", credits=100_000)
    await with_own_car(db, buyer)  # место занято своей машиной
    assert cars_allowed(False) == 1

    with pytest.raises(UsedCarError, match="некуда девать"):
        await buy_car(db, buyer, lot, NOW)

    assert len(await db.car_lots()) == 1


async def test_two_of_the_same_car_do_not_fit_one_fighter(db):
    """Этого не держит и сама база: у машины на бойца одна строка."""
    seller = await trader(db)
    await with_own_car(db, seller)
    lot = await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)
    buyer = await trader(db, 7, "Покупатель", credits=100_000)
    await with_own_car(db, buyer)

    with pytest.raises(UsedCarError, match="уже есть"):
        await buy_car(db, buyer, lot, NOW)


async def test_a_lot_taken_first_is_gone_for_the_second_buyer(db):
    """Защита от двойной продажи: машину забирает тот, кто успел."""
    price = get_car(HATCH).price
    seller = await trader(db)
    await with_own_car(db, seller)
    lot = await sell_car(db, seller, HATCH, price, NOW)
    first = await trader(db, 7, "Первый", credits=price)
    second = await trader(db, 8, "Второй", credits=price)

    await buy_car(db, first, lot, NOW)

    with pytest.raises(UsedCarError, match="уже забрали"):
        await buy_car(db, second, lot, NOW)
    assert (await db.get_player(second.user_id)).credits == price


# ---------- витрина ----------


async def test_the_screen_splits_mine_from_theirs(db):
    """Чужое покупают, своё снимают: кнопки разные, списки тоже."""
    from bot.webapp.cars import build_cars

    seller = await trader(db)
    await with_own_car(db, seller)
    await sell_car(db, seller, HATCH, get_car(HATCH).price, NOW)
    buyer = await trader(db, 7, "Покупатель", credits=100_000)

    his = (await build_cars(db, seller, NOW))["market"]
    theirs = (await build_cars(db, buyer, NOW))["market"]

    assert [one["code"] for one in his["mine"]] == [HATCH]
    assert his["lots"] == []
    assert [one["code"] for one in theirs["lots"]] == [HATCH]
    assert theirs["mine"] == []
    # У лота видно и продавца, и салонную цену — по ней судят, дорого ли
    assert theirs["lots"][0]["seller"] == "Тайлер"
    assert theirs["lots"][0]["salon"] == get_car(HATCH).price


async def test_the_screen_offers_only_what_can_be_listed(db):
    """В «выставить» попадает своя, не на парковке и не под собой."""
    from bot.car_service import wheel
    from bot.webapp.cars import build_cars

    seller = await trader(db)
    await with_own_car(db, seller)

    offer = (await build_cars(db, seller, NOW))["market"]["offer"]
    assert [one["code"] for one in offer] == [HATCH]
    assert offer[0]["low"], "рамки цены показаны продавцу"

    # Сел за руль — машина из списка ушла
    await wheel(db, seller, HATCH, NOW)
    offer = (await build_cars(db, seller, NOW))["market"]["offer"]
    assert offer == []


async def test_the_salon_screen_has_no_market_and_the_market_no_showcase(db):
    """Два дома — два экрана, и путать их нечем."""
    from bot.game.cars import SALON_PLACE
    from bot.webapp.cars import build_cars

    player = await trader(db)
    at_market = await build_cars(db, player, NOW)
    assert at_market["market"] is not None
    assert at_market["shelf"] == [], "витрины салона на рынке нет"

    player.location = SALON_PLACE
    await db.save_player(player)
    at_salon = await build_cars(db, await db.get_player(player.user_id), NOW)
    assert at_salon["market"] is None
    assert at_salon["shelf"], "витрина салона на месте"
