"""Рынок: обмен из рук в руки.

Обмен — единственное место в клубе, где вещи и деньги переходят между
игроками без прилавка, и потому здесь проверяется не столько арифметика,
сколько запреты. Их три, и каждый стоит на сервере:

1. **Позвать можно только того, кто на рынке.** Спрятанная кнопка
   обходится запросом мимо интерфейса.
2. **Править можно только свою половину стола.** Ручки, которой можно
   тронуть чужую, нет вовсе.
3. **Согласие относится к тому, что лежало на столе.** Любая правка
   сбрасывает оба «готов» — иначе обмен превращается в подмену.

И четвёртое, без которого первые три ничего не стоят: всё, что
проверялось при выкладывании, проверяется заново в миг обмена.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.game.locations import Service, get_location, where_to
from bot.game.trade import INVITE_SECONDS, MAX_ITEMS, TRADE_FEE
from bot.models import Player
from bot.trade_service import GEAR, POTION, Offer, TradeError, TradeService
from bot.webapp.server import TRADE_KEY, create_app
from bot.webapp.trade import build_trade
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

MARKET = "market"


def headers(user_id: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(
    user_id: int, nickname: str, credits: int = 0, location: str = MARKET
) -> Player:
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code="warrior",
        credits=credits,
        location=location,
        # «В сети» считается по последнему шагу: без него боец числится
        # ни разу не заходившим и уезжает в нижний список
        seen_at=10 ** 9,
    )


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


async def with_gear(db: Database, player: Player, code: str, wear: int = 0):
    owned = await db.add_gear(player.user_id, code, wear=wear)
    player.gear.append(owned)
    return owned


@pytest.fixture
async def pair(db):
    """Двое на рынке и служба обмена над ними."""
    first = await stand(db, make_player(1, "Тайлер", credits=500))
    second = await stand(db, make_player(2, "Марла", credits=100))
    return TradeService(db), first, second


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- правила ----------


def test_the_market_is_the_house_where_fighters_swap():
    """Услуга обмена живёт на рынке, и ровно в одном доме."""
    place = where_to(Service.TRADE)

    assert place is not None
    assert place.code == MARKET
    assert get_location(MARKET).allows(Service.TRADE)
    # Дом заработал: записки «скоро здесь появится» у него больше нет
    assert not get_location(MARKET).soon


def test_the_club_takes_nothing_from_a_swap():
    """Комиссия — у комиссионки. Обмен из рук в руки идёт без неё."""
    assert TRADE_FEE == 0


def test_an_invitation_lives_a_minute_and_the_table_holds_four():
    assert INVITE_SECONDS == 60
    assert MAX_ITEMS == 4


# ---------- приглашение ----------


async def test_the_invitation_reaches_the_one_it_was_sent_to(pair):
    trades, first, second = pair

    invite = await trades.invite(first, second.user_id)

    assert invite.from_id == first.user_id
    assert invite.from_name == "Тайлер"
    assert trades.invite_to(second.user_id) is invite
    # Позвавший видит своё приглашение, но не чужое: их два разных вопроса
    assert trades.invite_from(first.user_id) is invite
    assert trades.invite_to(first.user_id) is None


async def test_nobody_can_call_himself(pair):
    trades, first, _ = pair

    with pytest.raises(TradeError, match="Сам с собой"):
        await trades.invite(first, first.user_id)


async def test_a_fighter_off_the_market_cannot_be_called(db):
    """Позвать можно только того, кто стоит на рынке."""
    trades = TradeService(db)
    first = await stand(db, make_player(1, "Тайлер"))
    away = await stand(db, make_player(2, "Марла", location="pawnshop"))

    with pytest.raises(TradeError, match="не на рынке"):
        await trades.invite(first, away.user_id)


async def test_the_invitation_burns_out_after_a_minute(pair, monkeypatch):
    trades, first, second = pair
    await trades.invite(first, second.user_id)

    # Минута прошла: приглашения нет ни у кого
    later = [1_000_000.0]
    monkeypatch.setattr("bot.trade_service.time.monotonic", lambda: later[0])
    later[0] += 0

    assert trades.invite_to(second.user_id) is None
    with pytest.raises(TradeError, match="сгорело"):
        await trades.accept(second)


async def test_accepting_opens_one_table_for_both(pair):
    trades, first, second = pair
    await trades.invite(first, second.user_id)

    trade = await trades.accept(second)

    # Стол один и тот же объект: две копии разошлись бы в первую же правку
    assert trades.trade_of(first.user_id) is trade
    assert trades.trade_of(second.user_id) is trade
    assert trades.invite_to(second.user_id) is None
    assert {side.nickname for side in trade.sides.values()} == {"Тайлер", "Марла"}


async def test_declining_leaves_no_trace(pair):
    trades, first, second = pair
    await trades.invite(first, second.user_id)

    trades.decline(second.user_id)

    assert trades.invite_to(second.user_id) is None
    assert trades.trade_of(first.user_id) is None
    with pytest.raises(TradeError, match="уже нет"):
        trades.decline(second.user_id)


async def test_the_caller_can_take_his_invitation_back(pair):
    trades, first, second = pair
    await trades.invite(first, second.user_id)

    trades.withdraw(first.user_id)

    assert trades.invite_to(second.user_id) is None
    with pytest.raises(TradeError, match="никого не звал"):
        trades.withdraw(first.user_id)


async def test_calling_the_one_who_already_called_you_says_so(pair):
    """Спорить, кто первый, незачем: приглашение уже лежит."""
    trades, first, second = pair
    await trades.invite(first, second.user_id)

    with pytest.raises(TradeError, match="уже позвал тебя"):
        await trades.invite(second, first.user_id)


async def test_a_fighter_at_the_table_cannot_be_called_again(pair, db):
    trades, first, second = pair
    await trades.invite(first, second.user_id)
    await trades.accept(second)
    third = await stand(db, make_player(3, "Боб"))

    with pytest.raises(TradeError, match="меняется с другим"):
        await trades.invite(third, first.user_id)


async def test_a_fighter_in_a_fight_is_not_called_to_the_table(pair):
    """Стол ждёт, пока добьют: обмен посреди боя — не обмен."""
    trades, first, second = pair

    class Ring:
        def is_busy(self, user_id: int) -> bool:
            return user_id == second.user_id

    trades.watch(Ring())

    with pytest.raises(TradeError, match="занят боем"):
        await trades.invite(first, second.user_id)


# ---------- стол ----------


@pytest.fixture
async def table(pair):
    trades, first, second = pair
    await trades.invite(first, second.user_id)
    trade = await trades.accept(second)
    return trades, trade, first, second


async def test_credits_go_on_the_table_up_to_what_is_on_the_account(table):
    trades, trade, first, _ = table

    trades.put_credits(first, 500)

    assert trade.side(first.user_id).credits == 500
    with pytest.raises(TradeError, match="больше положить нечего"):
        trades.put_credits(first, 501)
    with pytest.raises(TradeError, match="Отрицательных"):
        trades.put_credits(first, -1)


async def test_gear_goes_on_the_table_only_from_the_backpack(table, db):
    trades, trade, first, _ = table
    knife = await with_gear(db, first, "knife")

    trades.put_item(first, GEAR, str(knife.id))

    assert trade.side(first.user_id).offers == [Offer(GEAR, str(knife.id), 1)]
    # Повторное нажатие не удваивает вещь: на столе она одна
    trades.put_item(first, GEAR, str(knife.id))
    assert trade.side(first.user_id).offers == [Offer(GEAR, str(knife.id), 1)]


async def test_worn_gear_is_taken_off_before_it_is_swapped(table, db):
    from bot.game.equipment import Slot

    trades, _, first, _ = table
    knife = await with_gear(db, first, "knife")
    knife.slot = Slot.WEAPON

    with pytest.raises(TradeError, match="Сними её сначала"):
        trades.put_item(first, GEAR, str(knife.id))


async def test_somebody_elses_gear_does_not_go_on_your_half(table, db):
    trades, _, first, second = table
    hers = await with_gear(db, second, "knife")

    with pytest.raises(TradeError, match="Такой вещи у тебя нет"):
        trades.put_item(first, GEAR, str(hers.id))


async def test_the_table_holds_four_things_and_not_a_fifth(table, db):
    trades, trade, first, _ = table
    ids = [(await with_gear(db, first, "knife")).id for _ in range(MAX_ITEMS + 1)]
    for item_id in ids[:MAX_ITEMS]:
        trades.put_item(first, GEAR, str(item_id))

    with pytest.raises(TradeError, match="не больше 4"):
        trades.put_item(first, GEAR, str(ids[MAX_ITEMS]))
    assert len(trade.side(first.user_id).offers) == MAX_ITEMS


async def test_potions_go_by_the_count_and_a_stack_takes_one_place(table, db):
    """«Установить сумму для передачи»: склянки передают числом."""
    trades, trade, first, _ = table
    await db.add_potion(first.user_id, "heal_small", 5)
    first.potions = await db.list_potions(first.user_id)

    trades.put_item(first, POTION, "heal_small", 3)

    assert trade.side(first.user_id).offers == [Offer(POTION, "heal_small", 3)]
    # Переставили число — всё та же одна выкладка, а не вторая
    trades.put_item(first, POTION, "heal_small", 2)
    assert trade.side(first.user_id).offers == [Offer(POTION, "heal_small", 2)]


async def test_more_potions_than_there_are_do_not_go_on_the_table(table, db):
    trades, _, first, _ = table
    await db.add_potion(first.user_id, "heal_small", 2)
    first.potions = await db.list_potions(first.user_id)

    with pytest.raises(TradeError, match="всего 2 шт"):
        trades.put_item(first, POTION, "heal_small", 3)


async def test_zero_takes_a_thing_off_the_table(table, db):
    trades, trade, first, _ = table
    knife = await with_gear(db, first, "knife")
    trades.put_item(first, GEAR, str(knife.id))

    trades.take_item(first, GEAR, str(knife.id))

    assert trade.side(first.user_id).offers == []
    with pytest.raises(TradeError, match="Этого на столе нет"):
        trades.take_item(first, GEAR, str(knife.id))


# ---------- согласие ----------


async def test_any_edit_drops_both_confirmations(table, db):
    """Главное правило стола: нажатие относится к тому, что лежало."""
    trades, trade, first, second = table
    trades.put_credits(first, 300)
    trades.confirm(first)
    trades.confirm(second)
    assert all(side.ready for side in trade.sides.values())

    knife = await with_gear(db, second, "knife")
    trades.put_item(second, GEAR, str(knife.id))

    assert not any(side.ready for side in trade.sides.values())


async def test_an_edit_that_changes_nothing_leaves_the_confirmations(table, db):
    """Повторное нажатие тем же числом — не правка."""
    trades, trade, first, second = table
    await db.add_potion(first.user_id, "heal_small", 5)
    first.potions = await db.list_potions(first.user_id)
    trades.put_item(first, POTION, "heal_small", 2)
    trades.confirm(first)
    trades.confirm(second)
    version = trade.version

    trades.put_item(first, POTION, "heal_small", 2)

    assert all(side.ready for side in trade.sides.values())
    assert trade.version == version


async def test_one_confirmation_is_not_enough(table):
    trades, trade, first, _ = table
    trades.put_credits(first, 100)

    _, done = await trades.ready(first)

    assert done is False
    assert trade.side(first.user_id).ready is True
    assert trades.trade_of(first.user_id) is trade


async def test_changing_your_mind_does_not_close_the_table(table):
    trades, trade, first, _ = table
    trades.confirm(first)

    trades.unconfirm(first)

    assert trade.side(first.user_id).ready is False
    assert trades.trade_of(first.user_id) is trade


async def test_one_refusal_closes_the_table_for_both(table):
    trades, trade, first, second = table

    trades.cancel(second)

    assert trades.trade_of(first.user_id) is None
    assert trades.trade_of(second.user_id) is None
    # Ушедшему со стола говорят, почему он исчез, — и каждому по-своему
    assert trades.take_done(first.user_id) == "Марла отказался от обмена."
    assert trades.take_done(second.user_id) == "Ты отказался от обмена."
    # Сказали один раз: второй взгляд на экран записку не повторяет
    assert trades.take_done(first.user_id) == ""


async def test_an_abandoned_table_closes_itself(table, monkeypatch):
    trades, _, first, second = table

    monkeypatch.setattr("bot.trade_service.time.monotonic", lambda: 1_000_000.0)

    assert trades.trade_of(first.user_id) is None
    assert trades.take_done(second.user_id) == "Стол бросили — обмен закрылся сам."


# ---------- сам обмен ----------


async def test_both_confirmations_move_the_money_and_the_things(table, db):
    trades, _, first, second = table
    knife = await with_gear(db, first, "knife")
    await db.add_potion(second.user_id, "heal_small", 4)
    second.potions = await db.list_potions(second.user_id)

    trades.put_credits(first, 300)
    trades.put_item(first, GEAR, str(knife.id))
    trades.put_item(second, POTION, "heal_small", 3)
    trades.confirm(first)
    _, done = await trades.ready(second)

    assert done is True
    mine = await db.get_player(first.user_id)
    hers = await db.get_player(second.user_id)
    assert mine.credits == 200
    assert hers.credits == 400
    # Вещь та же самая: тот же номер и тот же износ, сменился хозяин
    assert [owned.id for owned in hers.gear] == [knife.id]
    assert mine.gear == []
    assert mine.potions == {"heal_small": 3}
    assert hers.potions == {"heal_small": 1}
    # Стол закрыт, и оба видят, чем кончилось
    assert trades.trade_of(first.user_id) is None
    assert trades.take_done(first.user_id) == "Обмен прошёл."


async def test_a_swap_takes_no_commission(table, db):
    trades, _, first, second = table
    trades.put_credits(first, 400)
    trades.confirm(first)

    await trades.ready(second)

    mine = await db.get_player(first.user_id)
    hers = await db.get_player(second.user_id)
    # Было 500 и 100, стало 100 и 500: клуб не взял ничего
    assert (mine.credits, hers.credits) == (100, 500)


async def test_the_swapped_thing_lands_in_the_backpack_not_on_the_shoulders(table, db):
    """Чужая вещь приходит в рюкзак: надевают её сами."""
    trades, _, first, second = table
    knife = await with_gear(db, first, "knife")
    trades.put_item(first, GEAR, str(knife.id))
    trades.confirm(first)

    await trades.ready(second)

    hers = await db.get_player(second.user_id)
    assert [owned.id for owned in hers.backpack] == [knife.id]
    assert hers.equipped == []


async def test_money_spent_between_the_two_clicks_stops_the_swap(table, db):
    """Всё, что проверяли при выкладывании, проверяется заново."""
    trades, _, first, second = table
    trades.put_credits(first, 500)
    trades.confirm(first)
    # Пока второй думал, первый потратился в другом окне
    first.credits = 10
    await db.save_player(first)

    with pytest.raises(TradeError, match="уже нет столько кредитов"):
        await trades.ready(second)

    hers = await db.get_player(second.user_id)
    assert hers.credits == 100


async def test_a_thing_put_on_between_the_two_clicks_stops_the_swap(table, db):
    from bot.game.equipment import Slot

    trades, _, first, second = table
    knife = await with_gear(db, first, "knife")
    trades.put_item(first, GEAR, str(knife.id))
    trades.confirm(first)
    # Успел надеть: вещь больше не в рюкзаке
    knife.slot = Slot.WEAPON
    await db.save_gear(knife)

    with pytest.raises(TradeError, match="Сними её сначала"):
        await trades.ready(second)

    hers = await db.get_player(second.user_id)
    assert hers.gear == []


async def test_leaving_the_market_between_the_two_clicks_stops_the_swap(table, db):
    trades, _, first, second = table
    trades.put_credits(first, 100)
    trades.confirm(first)
    first.location = "pawnshop"
    await db.save_player(first)

    with pytest.raises(TradeError, match="не на рынке"):
        await trades.ready(second)


async def test_a_fighter_who_walked_off_closes_the_table(table, db):
    """Дорогу обмен не запирает, но и ждать ушедшего незачем."""
    trades, trade, first, second = table
    first.location = "pawnshop"
    await db.save_player(first)

    assert await trades.alive(trade) is None
    assert trades.trade_of(second.user_id) is None
    assert "ушёл с рынка" in trades.take_done(second.user_id)


# ---------- список рынка ----------


async def test_the_market_shows_who_stands_there_and_not_yourself(pair, db):
    trades, first, second = pair
    await stand(db, make_player(3, "Боб", location="pawnshop"))

    here = await trades.crowd(first)

    assert [one.nickname for one in here] == ["Марла"]


async def test_those_in_the_club_stand_above_those_who_left(db):
    """«Онлайн сверху и офлайн, если не в клубе»."""
    from bot.game.health import now_ts

    trades = TradeService(db)
    moment = now_ts()
    me = await stand(db, make_player(1, "Тайлер"))
    fresh = make_player(2, "Марла")
    fresh.seen_at = moment
    stale = make_player(3, "Боб")
    stale.seen_at = moment - 3600
    await stand(db, fresh)
    await stand(db, stale)

    body = await build_trade(me, trades, moment)

    assert [one["nickname"] for one in body["crowd"]] == ["Марла", "Боб"]
    assert [one["online"] for one in body["crowd"]] == [True, False]
    assert "Не был в клубе" in body["crowd"][1]["presence"]


async def test_a_fighter_already_swapping_cannot_be_called(db):
    trades = TradeService(db)
    me = await stand(db, make_player(1, "Тайлер"))
    second = await stand(db, make_player(2, "Марла"))
    third = await stand(db, make_player(3, "Боб"))
    await trades.invite(second, third.user_id)
    await trades.accept(third)

    body = await build_trade(me, trades)

    assert [one["callable"] for one in body["crowd"]] == [False, False]
    assert [one["trading"] for one in body["crowd"]] == [True, True]


# ---------- то, что видит страница ----------


async def test_the_page_gets_its_own_half_apart_from_the_other(table, db):
    """Своя половина приходит ключом, а не порядком."""
    trades, _, first, second = table
    knife = await with_gear(db, first, "knife")
    trades.put_item(first, GEAR, str(knife.id))
    trades.put_credits(second, 100)

    body = await build_trade(first, trades)

    table_body = body["trade"]
    assert table_body["mine"]["user_id"] == first.user_id
    assert table_body["his"]["nickname"] == "Марла"
    assert [one["title"] for one in table_body["mine"]["items"]] == [knife.title]
    assert table_body["his"]["credits"] == 100
    assert table_body["max_credits"] == 500


async def test_the_other_half_never_carries_his_backpack(table, db):
    """Соперник видит выложенное, а не то, что у тебя есть."""
    trades, _, first, second = table
    await with_gear(db, second, "knife")

    body = await build_trade(first, trades)

    assert [row["title"] for row in body["trade"]["basket"]] == []
    assert "basket" not in body["trade"]["his"]


async def test_the_basket_says_how_many_of_a_stack_already_lie_there(table, db):
    trades, _, first, _ = table
    await db.add_potion(first.user_id, "heal_small", 5)
    first.potions = await db.list_potions(first.user_id)
    trades.put_item(first, POTION, "heal_small", 2)

    body = await build_trade(first, trades)

    stack = next(row for row in body["trade"]["basket"] if row["kind"] == POTION)
    assert (stack["count"], stack["max_count"], stack["on_table"]) == (2, 5, True)
    assert stack["stack"] is True


async def test_the_version_grows_with_every_edit(table):
    """По нему страница и понимает, что стол поменялся."""
    trades, trade, first, _ = table
    was = trade.version

    trades.put_credits(first, 1)
    trades.put_credits(first, 2)

    assert trade.version == was + 2


# ---------- дверь ----------


async def test_the_market_screen_is_closed_outside_the_market(client, db):
    await stand(db, make_player(1, "Тайлер", location="pawnshop"))

    response = await client.get("/api/trade", headers=headers(1))

    assert response.status == 409
    assert "Рынок" in (await response.json())["error"]


async def test_the_invite_button_is_checked_on_the_server(client, db):
    """Позвать из комиссионки нельзя, даже запросом мимо интерфейса."""
    await stand(db, make_player(1, "Тайлер", location="pawnshop"))
    await stand(db, make_player(2, "Марла"))

    response = await client.post(
        "/api/trade", json={"action": "invite", "user_id": 2}, headers=headers(1)
    )

    assert response.status == 409


async def test_the_whole_swap_goes_through_the_page(client, db):
    """Позвал, согласился, выложил, оба нажали — и вещи переехали."""
    first = await stand(db, make_player(1, "Тайлер", credits=500))
    await stand(db, make_player(2, "Марла", credits=0))
    knife = await db.add_gear(first.user_id, "knife")

    async def act(who: int, **payload):
        response = await client.post("/api/trade", json=payload, headers=headers(who))
        assert response.status == 200, await response.text()
        return await response.json()

    await act(1, action="invite", user_id=2)
    body = await act(2, action="accept")
    assert body["trade"]["his"]["nickname"] == "Тайлер"

    await act(1, action="item", kind=GEAR, key=str(knife.id), count=1)
    await act(1, action="credits", credits=250)
    await act(1, action="confirm")
    body = await act(2, action="confirm")

    assert body["done"] == "Обмен прошёл."
    assert body["trade"] == {}
    hers = await db.get_player(2)
    assert hers.credits == 250
    assert [owned.id for owned in hers.gear] == [knife.id]


async def test_the_service_lives_in_the_app(client):
    assert isinstance(client.app[TRADE_KEY], TradeService)


async def test_an_unknown_action_is_refused(client, db):
    await stand(db, make_player(1, "Тайлер"))

    response = await client.post(
        "/api/trade", json={"action": "танцевать"}, headers=headers(1)
    )

    assert response.status == 400


async def test_a_fighter_pulled_into_a_fight_gets_a_refusal_not_a_five_hundred(
    client, db
):
    """Бой начинают из группы, и застать он может прямо на рынке.

    Ручки, которые работают с вещами, ловят «ты на ринге» сами; те, что
    только показывают прилавок, — нет, и до общей сети на бойце в бою
    страница получала пятисотку вместо отказа.
    """
    from bot.webapp.server import DUELS_KEY

    await stand(db, make_player(1, "Тайлер"))

    class Ring:
        def duel_of_user(self, user_id: int) -> object:
            return object()

    client.app[DUELS_KEY] = Ring()
    response = await client.get("/api/trade", headers=headers(1))

    assert response.status == 409
    assert "на ринге" in (await response.json())["error"]


async def test_a_fighter_who_stepped_out_of_the_door_is_not_in_the_list(db):
    """Вышел за дверь — в списке его нет, хотя локация ещё рынок."""
    from bot.game.health import now_ts

    trades = TradeService(db)
    moment = now_ts()
    me = await stand(db, make_player(1, "Тайлер"))
    leaving = make_player(2, "Марла")
    leaving.set_out("pawnshop", 20, moment)
    await stand(db, leaving)

    assert await trades.crowd(me, moment) == []
    with pytest.raises(TradeError, match="не на рынке"):
        await trades.invite(me, leaving.user_id)
