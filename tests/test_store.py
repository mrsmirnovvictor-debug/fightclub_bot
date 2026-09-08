"""Касса: пачки кредитов, оплата звёздами, повторы, возвраты и мини-апп."""

import json

import pytest
from aiogram.types import PreCheckoutQuery, SuccessfulPayment, Update
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import get_class
from bot.game.reference import kit_price
from bot.game.store import PACKS, get_pack, pack_of_payload, payload_for
from bot.keyboards import TopUpCB
from bot.models import Player
from bot.store_service import StoreError, StoreService, spent_stars
from bot.webapp.server import create_app
from tests.harness import BOT, SESSION, Client, ids, new_user
from tests.test_webapp import FakeBot as AvatarBot, make_init_data

TOKEN = "424242:TESTTOKEN"


class FakeBot:
    """Заглушка Bot API: копит выставленные счета и возвраты."""

    def __init__(self) -> None:
        self.invoices: list[dict] = []
        self.refunds: list[tuple[int, str]] = []

    async def create_invoice_link(self, **kwargs) -> str:
        self.invoices.append(kwargs)
        return "https://t.me/$invoice-" + kwargs["payload"]

    async def refund_star_payment(self, user_id: int, telegram_payment_charge_id: str):
        self.refunds.append((user_id, telegram_payment_charge_id))
        return True


def make_player(user_id: int = 42, credits: int = 0) -> Player:
    stats = get_class("warrior").base_stats
    player = Player(
        user_id=user_id, nickname="Тайлер", class_code="warrior", **stats.as_dict()
    )
    player.credits = credits
    return player


def payment(pack_code: str = "case", charge_id: str = "ch-1") -> SuccessfulPayment:
    pack = get_pack(pack_code)
    return SuccessfulPayment(
        currency="XTR",
        total_amount=pack.stars,
        invoice_payload=f"pack:{pack.code}:42",
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="",
    )


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
async def store(bot, db):
    return StoreService(bot=bot, db=db, config=Config(bot_token="test"))


# ---------- пачки ----------


def test_the_bigger_the_pack_the_cheaper_the_credit():
    prices = [pack.stars_per_hundred for pack in PACKS]
    assert prices == sorted(prices, reverse=True)
    assert len(set(prices)) == len(prices)  # у каждой пачки своя выгода


def test_pack_sizes_are_tied_to_what_gear_costs():
    """Мелочь — докинуть на вещь, кейс — одеться, и ничего сверх этого."""
    kit = kit_price(get_class("warrior"), 8)
    assert PACKS[0].total < kit / 4  # горсть комплект не покупает
    assert PACKS[-1].total > kit  # сейф покрывает его с запасом
    assert all(pack.stars > 0 and pack.total > 0 for pack in PACKS)


def test_bonus_credits_are_counted_on_top():
    case = get_pack("case")
    assert case.total == case.credits + case.bonus
    assert "сверху" in case.describe()


def test_payload_says_which_pack_and_whose():
    pack = get_pack("roll")
    assert payload_for(pack, 777) == "pack:roll:777"
    assert pack_of_payload("pack:roll:777") is pack
    assert pack_of_payload("pack:unknown:1") is None
    assert pack_of_payload("мусор") is None


# ---------- оплата ----------


async def test_paid_stars_turn_into_credits(store, db):
    await db.save_player(make_player(credits=10))

    grant = await store.grant(42, payment())

    assert grant.credits == get_pack("case").total
    assert grant.balance == 10 + grant.credits
    assert (await db.get_player(42)).credits == grant.balance
    assert not grant.already

    row = (await db.purchases_of(42))[0]
    assert (row["code"], row["stars"], row["charge_id"]) == (
        "case",
        get_pack("case").stars,
        "ch-1",
    )


async def test_the_same_payment_is_never_credited_twice(store, db):
    """Telegram может прислать апдейт повторно — кредиты капают один раз."""
    await db.save_player(make_player())
    first = await store.grant(42, payment())
    second = await store.grant(42, payment())

    assert second.already and second.credits == 0
    assert (await db.get_player(42)).credits == first.credits
    assert len(await db.purchases_of(42)) == 1


async def test_payment_without_a_character_is_not_lost(store, db):
    with pytest.raises(StoreError, match="/start"):
        await store.grant(42, payment())
    assert not await db.purchases_of(42)  # платёж не записан — вернуть можно


async def test_a_made_up_pack_is_refused(store):
    with pytest.raises(StoreError, match="кассе"):
        store.check("pack:diamonds:42")
    with pytest.raises(StoreError):
        store.check("что-то своё")


async def test_invoice_is_issued_in_stars(store, bot):
    link = await store.invoice_link(get_pack("roll"), 42)

    assert link.endswith("pack:roll:42")
    invoice = bot.invoices[-1]
    assert invoice["currency"] == "XTR"
    assert invoice["prices"][0].amount == get_pack("roll").stars
    assert "550" in invoice["description"]  # сколько кредитов придёт


# ---------- возврат ----------


async def test_refund_returns_the_stars_and_takes_the_credits_back(store, db, bot):
    await db.save_player(make_player(credits=5))
    await store.grant(42, payment())

    row = await store.refund(42)

    assert bot.refunds == [(42, "ch-1")]
    assert (await db.get_player(42)).credits == 5  # осталось только своё
    assert (await db.get_purchase("ch-1"))["refunded_at"]
    assert row["stars"] == get_pack("case").stars


async def test_spent_credits_cannot_be_refunded(store, db, bot):
    await db.save_player(make_player())
    await store.grant(42, payment())
    player = await db.get_player(42)
    player.grant_credits(-500)  # оделся
    await db.save_player(player)

    with pytest.raises(StoreError, match="уже потрачены"):
        await store.refund(42)
    assert not bot.refunds


async def test_you_cannot_refund_twice_or_refund_a_stranger(store, db):
    await db.save_player(make_player())
    await db.save_player(make_player(user_id=43))
    await store.grant(42, payment())
    await store.refund(42)

    with pytest.raises(StoreError, match="уже вернули"):
        await store.refund(42, "ch-1")
    with pytest.raises(StoreError, match="не числится"):
        await store.refund(43, "ch-1")
    with pytest.raises(StoreError, match="покупок за тобой нет"):
        await store.refund(43)


async def test_refunded_stars_do_not_count_as_income(store, db):
    await db.save_player(make_player())
    await store.grant(42, payment("handful", "ch-1"))
    await store.grant(42, payment("roll", "ch-2"))
    await store.refund(42, "ch-1")

    rows = await store.history(42)
    assert spent_stars(rows) == get_pack("roll").stars


# ---------- бот ----------


@pytest.fixture
async def player_client(dispatcher_env):
    db, _, _ = dispatcher_env
    client = Client(db)
    await db.save_player(make_player(client.user.id, credits=20))
    return client


async def feed_payment(client, pack_code: str, charge_id: str) -> None:
    """Прислать боту сообщение об удачной оплате — как это делает Telegram."""
    from tests.harness import DISPATCHER, make_message

    message = make_message(
        client.chat,
        "",
        client.user,
        successful_payment=SuccessfulPayment(
            currency="XTR",
            total_amount=get_pack(pack_code).stars,
            invoice_payload=f"pack:{pack_code}:{client.user.id}",
            telegram_payment_charge_id=charge_id,
            provider_payment_charge_id="",
        ),
    )
    await DISPATCHER.feed_update(BOT, Update(update_id=next(ids), message=message))


async def test_topup_lists_the_packs(player_client):
    await player_client.send("/topup")
    text = SESSION.texts[-1]

    assert "Касса клуба" in text
    for pack in PACKS:
        assert pack.title in text
        assert f"{pack.stars} ⭐" in text

    keyboard = SESSION.method_calls("SendMessage")[-1].reply_markup
    assert len(keyboard.inline_keyboard) == len(PACKS)


async def test_pressing_a_pack_issues_an_invoice(player_client):
    await player_client.press(TopUpCB(code="case").pack())

    invoice = SESSION.method_calls("SendInvoice")[-1]
    assert invoice.currency == "XTR"
    assert invoice.prices[0].amount == get_pack("case").stars
    assert invoice.payload == f"pack:case:{player_client.user.id}"


async def test_pre_checkout_lets_our_packs_through_and_stops_the_rest(player_client):
    from tests.harness import DISPATCHER

    async def ask(payload: str) -> None:
        query = PreCheckoutQuery(
            id=str(next(ids)),
            from_user=player_client.user,
            currency="XTR",
            total_amount=350,
            invoice_payload=payload,
        )
        await DISPATCHER.feed_update(
            BOT, Update(update_id=next(ids), pre_checkout_query=query)
        )

    await ask(f"pack:case:{player_client.user.id}")
    assert SESSION.method_calls("AnswerPreCheckoutQuery")[-1].ok

    await ask("pack:diamonds:1")
    refused = SESSION.method_calls("AnswerPreCheckoutQuery")[-1]
    assert not refused.ok and "кассе" in refused.error_message


async def test_successful_payment_tops_up_the_wallet(player_client):
    await feed_payment(player_client, "case", "charge-1")

    player = await player_client.player()
    assert player.credits == 20 + get_pack("case").total
    assert "Начислено" in SESSION.texts[-1]
    assert str(get_pack("case").total) in SESSION.texts[-1]

    # тот же платёж пришёл ещё раз — счёт не растёт
    await feed_payment(player_client, "case", "charge-1")
    assert (await player_client.player()).credits == player.credits
    assert "уже учтён" in SESSION.texts[-1]


async def test_purchases_and_refund_from_the_chat(player_client):
    await feed_payment(player_client, "handful", "charge-2")

    await player_client.send("/purchases")
    assert "charge-2" in SESSION.texts[-1]
    assert f"{get_pack('handful').stars}" in SESSION.texts[-1]

    await player_client.send("/refund")
    assert "вернулись" in SESSION.texts[-1]
    assert (await player_client.player()).credits == 20


async def test_topup_needs_a_character(dispatcher_env):
    stranger = Client(dispatcher_env[0])
    await stranger.send("/topup")
    assert "/start" in SESSION.texts[-1]


# ---------- мини-апп ----------


@pytest.fixture
async def webapp(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    bot = FakeBot()
    app = create_app(AvatarBot(), db, config, store=StoreService(bot, db, config))
    async with TestClient(TestServer(app)) as client:
        client.stars = bot
        yield client


async def test_miniapp_shows_the_cashdesk(webapp, db):
    await db.save_player(make_player(credits=77))
    response = await webapp.get(
        "/api/topup", headers={"X-Telegram-Init-Data": make_init_data(42)}
    )
    body = await response.json()

    assert response.status == 200
    assert body["credits"] == 77 and body["open"]
    assert [pack["code"] for pack in body["packs"]] == [p.code for p in PACKS]
    assert body["packs"][-1]["profit"] > 0  # большая пачка выгоднее мелочи


async def test_miniapp_cashdesk_needs_a_signature_and_a_character(webapp):
    assert (await webapp.get("/api/topup")).status == 401
    signed = {"X-Telegram-Init-Data": make_init_data(42)}
    assert (await webapp.get("/api/topup", headers=signed)).status == 404


async def test_miniapp_asks_for_an_invoice(webapp, db):
    await db.save_player(make_player())
    response = await webapp.post(
        "/api/invoice",
        data=json.dumps({"code": "roll"}),
        headers={"X-Telegram-Init-Data": make_init_data(42)},
    )
    body = await response.json()

    assert response.status == 200
    assert body["link"].endswith("pack:roll:42")
    assert body["stars"] == get_pack("roll").stars
    assert webapp.stars.invoices[-1]["currency"] == "XTR"


async def test_miniapp_refuses_an_unknown_pack(webapp, db):
    await db.save_player(make_player())
    response = await webapp.post(
        "/api/invoice",
        data=json.dumps({"code": "diamonds"}),
        headers={"X-Telegram-Init-Data": make_init_data(42)},
    )
    assert response.status == 409
    assert "кассе" in (await response.json())["error"]


async def test_without_a_cashdesk_the_miniapp_says_so(db):
    config = Config(bot_token=TOKEN)
    app = create_app(AvatarBot(), db, config)
    async with TestClient(TestServer(app)) as client:
        await db.save_player(make_player())
        response = await client.post(
            "/api/invoice",
            data=json.dumps({"code": "roll"}),
            headers={"X-Telegram-Init-Data": make_init_data(42)},
        )
        assert response.status == 503


def test_new_user_helper_is_unique():
    assert new_user().id != new_user().id
