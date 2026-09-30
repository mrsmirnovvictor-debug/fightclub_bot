"""Vegas Банк: счёт, карта, банкомат, переводы и то, чем платят в городе.

Порядок проверок — по тому, что дороже сломать:

1. **Деньги не появляются и не пропадают.** Всякое движение обязано
   сходиться: сколько ушло с одной стороны, столько пришло на другую.
2. **Двери.** Счёт один, карта одна, чужой счёт по номеру, рынок только
   наличными. Каждая заперта на сервере, а не прятанием кнопки.
3. **Скидка.** Она живёт на карте и зависит от места, а показанная цена
   обязана совпадать с тем, что уходит с кошелька.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.bank_service import (
    BankError,
    choose_purse,
    deposit,
    issue_card,
    open_account,
    send,
    settle_card,
    withdraw,
)
from bot.config import Config
from bot.database import Database
from bot.game.bank import (
    CARD,
    CARD_PRICE,
    CARD_YEAR_PRICE,
    CASH,
    DISCOUNTS,
    YEAR_SECONDS,
    card_works,
    discount_of,
    make_number,
    price_for,
    saved_by_card,
    tidy_number,
    with_discount,
)
from bot.game.classes import Stats
from bot.game.locations import Service, get_location, where_to
from bot.models import Player
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

BANK = "bank"
NOW = 1_800_000_000


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(
    user_id: int = 42,
    nickname: str = "Тайлер",
    location: str = BANK,
    credits: int = 1_000,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=8,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


async def with_account(db: Database, player: Player) -> Player:
    await stand(db, player)
    await open_account(db, player)
    return player


async def with_card(db: Database, player: Player, now: int = NOW) -> Player:
    await with_account(db, player)
    if player.credits < CARD_PRICE:
        player.credits = CARD_PRICE
        await db.save_player(player)
    await issue_card(db, player, now)
    return player


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- дом на карте ----------


def test_the_bank_is_a_house_that_finally_works():
    place = where_to(Service.BANK)

    assert place is not None and place.code == BANK
    assert get_location(BANK).allows(Service.BANK)
    # Раньше банк стоял с надписью «скоро»: теперь за ним услуга
    assert not get_location(BANK).soon


# ---------- номер счёта ----------


def test_the_account_number_reads_like_a_bank_number():
    import random

    number = make_number(random.Random(7))

    assert number.startswith("VB-")
    groups = number.split("-")
    assert len(groups) == 4
    assert all(len(one) == 4 and one.isdigit() for one in groups[1:])


def test_two_numbers_in_a_row_are_not_the_same():
    numbers = {make_number() for _ in range(200)}

    assert len(numbers) > 190, "номера повторяются слишком часто"


@pytest.mark.parametrize(
    "said",
    ["VB-1234-5678-9012", "vb-1234-5678-9012", " VB 1234 5678 9012 ",
     "VB123456789012", "VB—1234—5678—9012"],
)
def test_a_number_written_by_hand_is_understood(said):
    """Пробел и нижний регистр — обычный способ переписать номер из чата."""
    assert tidy_number(said) == "VB-1234-5678-9012"


@pytest.mark.parametrize("said", ["", "VB-1234", "не номер", "VB-1234-5678-901"])
def test_what_is_not_a_number_is_refused(said):
    assert tidy_number(said) == ""


# ---------- скидка ----------


def test_the_card_discounts_are_the_ones_the_bank_promised():
    assert discount_of(Service.CLOTHES) == 10
    assert discount_of(Service.FAN) == 10
    assert discount_of(Service.WEAPONS) == 5
    assert discount_of(Service.POTIONS) == 15
    assert discount_of(Service.HEAL) == 10
    assert discount_of(Service.INSURANCE) == 10
    assert discount_of(Service.TRAIN) == 10


def test_where_the_bank_promised_nothing_there_is_no_discount():
    """Мастерская, комиссионка и лавка мага — по полной цене."""
    assert discount_of(Service.REPAIR) == 0
    assert discount_of(Service.MARKET) == 0
    assert discount_of(Service.PREMIUM) == 0
    assert discount_of(None) == 0


def test_the_discount_rounds_in_the_fighters_favour():
    assert with_discount(100, 10) == 90
    assert with_discount(100, 15) == 85
    # 55 со скидкой в 15% — это 46.75, и копейка достаётся бойцу
    assert with_discount(55, 15) == 46
    assert with_discount(0, 10) == 0
    # По скидке ничто не становится бесплатным
    assert with_discount(1, 15) == 1


def test_cash_never_gets_a_discount():
    assert price_for(100, CASH, Service.POTIONS) == 100
    assert price_for(100, CARD, Service.POTIONS) == 85
    assert saved_by_card(100, Service.POTIONS) == 15
    assert saved_by_card(100, Service.REPAIR) == 0


def test_the_card_does_not_walk_the_market():
    """На рынке из рук в руки передают наличные, и только их."""
    assert card_works(Service.TRADE) is False
    assert card_works(Service.CLOTHES) is True
    assert card_works(Service.MARKET) is True


def test_every_discount_is_a_place_the_card_walks():
    """Скидка там, где картой нельзя заплатить, была бы обещанием впустую."""
    for service in DISCOUNTS:
        assert card_works(service), f"{service} не принимает карту"


# ---------- счёт ----------


async def test_the_account_opens_at_once_and_for_free(db):
    player = await stand(db, make_player(credits=500))

    number = await open_account(db, player)

    assert number.startswith("VB-")
    assert player.has_account and player.account_number == number
    # Бесплатно: ни кредита не ушло
    assert player.credits == 500
    assert player.account_balance == 0
    fresh = await db.get_player(player.user_id)
    assert fresh.account_number == number


async def test_a_second_account_is_refused(db):
    player = await with_account(db, make_player())

    with pytest.raises(BankError, match="уже есть"):
        await open_account(db, player)


async def test_the_number_survives_a_reread(db):
    player = await with_account(db, make_player())
    await deposit(db, player, 300)

    fresh = await db.get_player(player.user_id)

    assert fresh.account_number == player.account_number
    assert fresh.account_balance == 300


# ---------- банкомат ----------


async def test_cash_goes_to_the_account_and_comes_back(db):
    player = await with_account(db, make_player(credits=500))

    await deposit(db, player, 200)
    assert (player.credits, player.account_balance) == (300, 200)

    await withdraw(db, player, 50)
    assert (player.credits, player.account_balance) == (350, 150)
    # И ни кредита не пропало по дороге: банкомат без комиссии
    assert player.credits + player.account_balance == 500


async def test_the_atm_takes_no_commission(db):
    player = await with_account(db, make_player(credits=1_000))

    for _ in range(10):
        await deposit(db, player, 100)
        await withdraw(db, player, 100)

    assert player.credits == 1_000 and player.account_balance == 0


async def test_more_than_there_is_cannot_be_moved(db):
    player = await with_account(db, make_player(credits=100))

    with pytest.raises(BankError, match="больше положить нечего"):
        await deposit(db, player, 101)
    await deposit(db, player, 100)
    with pytest.raises(BankError, match="больше снять нечего"):
        await withdraw(db, player, 101)


@pytest.mark.parametrize("amount", [0, -1, -500])
async def test_a_sum_below_a_credit_is_refused(db, amount):
    player = await with_account(db, make_player())

    with pytest.raises(BankError, match="больше нуля"):
        await deposit(db, player, amount)
    with pytest.raises(BankError, match="больше нуля"):
        await withdraw(db, player, amount)


async def test_without_an_account_there_is_nothing_to_put_money_into(db):
    player = await stand(db, make_player())

    with pytest.raises(BankError, match="открой счёт"):
        await deposit(db, player, 100)


# ---------- перевод ----------


async def test_money_goes_to_another_account_by_its_number(db):
    first = await with_account(db, make_player(credits=1_000))
    second = await with_account(db, make_player(2, "Марла", credits=0))
    await deposit(db, first, 500)

    move, name = await send(db, first, second.account_number, 200)

    assert (move.amount, name) == (200, "Марла")
    assert first.account_balance == 300
    taker = await db.get_player(second.user_id)
    assert taker.account_balance == 200
    # Наличные ни у кого не шелохнулись: перевод идёт со счёта на счёт
    assert taker.credits == 0


async def test_a_number_nobody_owns_takes_nothing(db):
    player = await with_account(db, make_player())
    await deposit(db, player, 500)

    with pytest.raises(BankError, match="нет"):
        await send(db, player, "VB-0000-0000-0000", 100)

    assert player.account_balance == 500
    fresh = await db.get_player(player.user_id)
    assert fresh.account_balance == 500, "деньги ушли в никуда"


async def test_more_than_the_account_holds_is_refused(db):
    first = await with_account(db, make_player())
    second = await with_account(db, make_player(2, "Марла"))
    await deposit(db, first, 100)

    with pytest.raises(BankError, match="больше перевести нечего"):
        await send(db, first, second.account_number, 101)

    assert (await db.get_player(2)).account_balance == 0


async def test_a_transfer_to_yourself_is_refused(db):
    player = await with_account(db, make_player())
    await deposit(db, player, 500)

    with pytest.raises(BankError, match="твой собственный"):
        await send(db, player, player.account_number, 100)


async def test_a_number_written_by_hand_still_finds_the_account(db):
    first = await with_account(db, make_player(credits=500))
    second = await with_account(db, make_player(2, "Марла"))
    await deposit(db, first, 300)

    await send(db, first, second.account_number.lower().replace("-", " "), 100)

    assert (await db.get_player(2)).account_balance == 100


# ---------- карта ----------


async def test_the_card_costs_a_hundred_and_the_first_year_is_inside(db):
    player = await with_account(db, make_player(credits=500))

    price = await issue_card(db, player, NOW)

    assert price == CARD_PRICE
    assert player.has_card and player.card_at == NOW
    assert player.card_paid_until == NOW + YEAR_SECONDS
    assert player.credits == 400
    # Через год без дня она ещё работает
    assert player.card_works(NOW + YEAR_SECONDS - 1)


async def test_the_card_is_paid_from_the_account_when_the_money_is_there(db):
    player = await with_account(db, make_player(credits=500))
    await deposit(db, player, 300)

    await issue_card(db, player, NOW)

    assert player.account_balance == 200
    assert player.credits == 200, "за карту взяли дважды"


async def test_a_second_card_is_refused(db):
    player = await with_card(db, make_player(credits=500))

    with pytest.raises(BankError, match="уже есть"):
        await issue_card(db, player, NOW)

    assert player.credits == 400, "за вторую карту взяли деньги"


async def test_a_card_without_an_account_is_refused(db):
    player = await stand(db, make_player(credits=500))

    with pytest.raises(BankError, match="открой счёт"):
        await issue_card(db, player, NOW)


async def test_without_the_hundred_there_is_no_card(db):
    player = await with_account(db, make_player(credits=99))

    with pytest.raises(BankError, match="Не хватает"):
        await issue_card(db, player, NOW)

    assert not player.has_card


# ---------- обслуживание ----------


async def test_the_year_is_taken_from_the_account_when_it_comes(db):
    player = await with_card(db, make_player(credits=1_000))
    await deposit(db, player, 500)
    later = NOW + YEAR_SECONDS

    said = await settle_card(db, player, later)

    assert "Обслуживание" in said
    assert player.account_balance == 500 - CARD_YEAR_PRICE
    assert player.card_paid_until == later + YEAR_SECONDS
    assert player.card_works(later)


async def test_the_year_is_taken_once_and_not_for_every_year_missed(db):
    """Тот же закон, что у полиса: за время без услуги денег не берут."""
    player = await with_card(db, make_player(credits=1_000))
    await deposit(db, player, 500)
    long_after = NOW + 3 * YEAR_SECONDS

    await settle_card(db, player, long_after)

    assert player.account_balance == 400, "списали за каждый пропущенный год"
    assert player.card_paid_until == long_after + YEAR_SECONDS


async def test_an_unpaid_year_locks_the_card_but_does_not_close_it(db):
    player = await with_card(db, make_player(credits=500))
    later = NOW + YEAR_SECONDS

    said = await settle_card(db, player, later)

    assert "не обслуживается" in said
    assert player.has_card, "карту закрыли за сто кредитов"
    assert not player.card_works(later)
    assert player.purse_for(later, Service.CLOTHES) == CASH


async def test_a_topped_up_account_revives_the_card(db):
    player = await with_card(db, make_player(credits=500))
    later = NOW + YEAR_SECONDS
    await settle_card(db, player, later)
    assert not player.card_works(later)

    await deposit(db, player, 200)
    await settle_card(db, player, later)

    assert player.card_works(later)
    assert player.account_balance == 100


async def test_settling_twice_takes_nothing_the_second_time(db):
    player = await with_card(db, make_player(credits=1_000))
    await deposit(db, player, 500)
    later = NOW + YEAR_SECONDS

    await settle_card(db, player, later)
    said = await settle_card(db, player, later)

    assert said == ""
    assert player.account_balance == 400


# ---------- чем платят ----------


async def test_the_card_is_the_default_when_it_is_open(db):
    player = await with_card(db, make_player(credits=500))

    assert player.pay_from == CARD
    assert player.purse_for(NOW, Service.CLOTHES) == CARD


async def test_without_a_card_everything_is_paid_from_the_pouch(db):
    player = await with_account(db, make_player())

    assert player.purse_for(NOW, Service.CLOTHES) == CASH


async def test_the_pouch_can_be_chosen_and_it_sticks(db):
    player = await with_card(db, make_player(credits=500))

    await choose_purse(db, player, CASH)

    assert player.purse_for(NOW, Service.CLOTHES) == CASH
    fresh = await db.get_player(player.user_id)
    assert fresh.pay_from == CASH


async def test_a_card_that_is_not_there_cannot_be_chosen(db):
    player = await with_account(db, make_player())

    with pytest.raises(BankError, match="Карты"):
        await choose_purse(db, player, CARD)


async def test_the_market_is_paid_in_cash_even_with_a_card(db):
    player = await with_card(db, make_player(credits=500))
    await deposit(db, player, 300)

    assert player.purse_for(NOW, Service.TRADE) == CASH


async def test_paying_takes_the_money_from_the_purse_that_was_chosen(db):
    player = await with_card(db, make_player(credits=500))
    await deposit(db, player, 300)

    where = player.pay(100, NOW, Service.REPAIR)

    assert where == CARD
    assert player.account_balance == 200
    assert player.credits == 100, "взяли из мешочка вместо счёта"


async def test_the_price_shown_is_the_price_taken(db):
    """Скидку считают там, где показывают цену, — и списывают ровно её."""
    player = await with_card(db, make_player(credits=500))
    await deposit(db, player, 400)

    price = player.price_here(100, NOW, Service.POTIONS)
    player.pay(price, NOW, Service.POTIONS)

    assert price == 85
    assert player.account_balance == 315


async def test_what_the_card_cannot_reach_is_not_discounted(db):
    player = await with_card(db, make_player(credits=500))
    await deposit(db, player, 300)

    # На рынке карта не ходит — значит, и скидки там нет
    assert player.price_here(100, NOW, Service.TRADE) == 100


# ---------- банк через ручку ----------


async def test_the_bank_opens_an_account_and_issues_a_card_by_the_handle(client, db):
    await stand(db, make_player(credits=1_000))

    body = await (await client.get("/api/bank", headers=headers())).json()
    assert body["account"]["open"] is False
    assert [tab["code"] for tab in body["tabs"]] == ["new", "accounts", "atm"]

    body = await (
        await client.post("/api/bank", json={"action": "account"}, headers=headers())
    ).json()
    assert body["account"]["open"] is True
    assert body["account"]["number"].startswith("VB-")

    body = await (
        await client.post("/api/bank", json={"action": "card"}, headers=headers())
    ).json()
    assert body["bank_card"]["open"] is True
    assert body["bank_card"]["works"] is True
    # Карта привязана к счёту: номер у них один
    assert body["bank_card"]["number"] == body["account"]["number"]
    assert body["credits"] == 900


async def test_the_atm_moves_money_both_ways_by_the_handle(client, db):
    player = await with_account(db, make_player(credits=1_000))

    body = await (
        await client.post(
            "/api/bank", json={"action": "deposit", "amount": 400}, headers=headers()
        )
    ).json()
    assert (body["credits"], body["account"]["balance"]) == (600, 400)

    body = await (
        await client.post(
            "/api/bank", json={"action": "withdraw", "amount": 150}, headers=headers()
        )
    ).json()
    assert (body["credits"], body["account"]["balance"]) == (750, 250)
    assert player.user_id == 42


async def test_the_bank_is_shut_to_those_who_are_elsewhere(client, db):
    await stand(db, make_player(location="pharmacy"))

    answer = await client.get("/api/bank", headers=headers())

    assert answer.status == 409
    assert "банк" in (await answer.json())["error"].lower()


async def test_the_purse_is_chosen_by_the_handle(client, db):
    await with_card(db, make_player(credits=1_000))

    body = await (
        await client.post("/api/purse", json={"purse": CASH}, headers=headers())
    ).json()

    assert body["purse"] == CASH
    assert (await db.get_player(42)).pay_from == CASH


async def test_the_purse_is_chosen_from_any_counter_not_only_the_bank(client, db):
    """Кошелёк выбирают там, где стоит цена, а не сходив в банк."""
    player = await with_card(db, make_player(credits=1_000, location="pharmacy"))
    await db.save_player(player)

    answer = await client.post("/api/purse", json={"purse": CASH}, headers=headers())

    assert answer.status == 200
    assert (await db.get_player(42)).pay_from == CASH


# ---------- скидка доходит до кассы ----------


async def test_the_pharmacy_shows_and_takes_the_card_price(client, db):
    """Пятнадцать процентов видно на витрине и ровно столько уходит."""
    player = await with_card(db, make_player(credits=1_000, location="pharmacy"))
    await deposit(db, player, 500)

    shop = await (await client.get("/api/shop", headers=headers())).json()
    row = next(
        one
        for section in shop["sections"]
        for one in section["items"]
        if one["code"] == "heal_small"
    )
    assert shop["purse"]["purse"] == CARD
    assert shop["purse"]["discount"] == 15
    assert row["price"] == row["full_price"] * 85 // 100
    assert row["off"] == row["full_price"] - row["price"]

    was = (await db.get_player(42)).account_balance
    answer = await client.post(
        "/api/buy", json={"code": "heal_small"}, headers=headers()
    )
    assert answer.status == 200, await answer.json()
    fresh = await db.get_player(42)
    # Списано со счёта ровно то, что стояло на витрине
    assert was - fresh.account_balance == row["price"]
    assert fresh.credits == 400, "деньги взяли из мешочка вместо счёта"


async def test_the_pouch_pays_the_full_price_at_the_same_counter(client, db):
    player = await with_card(db, make_player(credits=1_000, location="pharmacy"))
    await choose_purse(db, player, CASH)

    shop = await (await client.get("/api/shop", headers=headers())).json()
    row = next(
        one
        for section in shop["sections"]
        for one in section["items"]
        if one["code"] == "heal_small"
    )

    assert shop["purse"]["purse"] == CASH
    assert shop["purse"]["now_off"] == 0
    assert row["price"] == row["full_price"] and row["off"] == 0


async def test_the_weapon_shop_takes_five_and_the_clothes_shop_ten(client, db):
    from bot.game.bank import with_discount
    from bot.game.equipment import get_item

    player = await with_card(db, make_player(credits=5_000))
    await db.move_to_account(player.user_id, 3_000)

    for place, service, percent in (
        ("weapon_shop", Service.WEAPONS, 5),
        ("clothes_shop", Service.CLOTHES, 10),
    ):
        fresh = await db.get_player(42)
        fresh.location = place
        await db.save_player(fresh)
        shop = await (await client.get("/api/shop", headers=headers())).json()
        assert shop["purse"]["discount"] == percent, place
        row = shop["sections"][0]["items"][0]
        item = get_item(row["code"])
        assert row["price"] == with_discount(item.price, percent), place


async def test_the_market_counter_takes_the_card_but_gives_no_discount(db):
    """Комиссионка — прилавок, а не стол: картой платить можно."""
    player = await with_card(db, make_player(credits=500))
    await deposit(db, player, 300)

    assert player.purse_for(NOW, Service.MARKET) == CARD
    assert player.price_here(200, NOW, Service.MARKET) == 200


async def test_the_trade_table_never_sees_the_account(db):
    """На рынке из рук в руки передают наличные, и только их."""
    from bot.trade_service import TradeError, TradeService

    player = await with_card(db, make_player(credits=200, location="market"))
    await deposit(db, player, 100)
    assert player.credits == 0 and player.account_balance == 100

    trades = TradeService(db)
    second = await stand(db, make_player(2, "Марла", location="market"))
    await trades.invite(player, second.user_id)
    await trades.accept(second)

    # Сто на счету на стол не положишь: стол видит только мешочек
    with pytest.raises(TradeError, match="больше положить нечего"):
        trades.put_credits(player, 100)
