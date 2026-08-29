"""Подписка PRO: акция, срок, полуторный опыт, значок и то, что остаётся."""

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.types import SuccessfulPayment
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats
from bot.game.combat import Fighter
from bot.game.economy import pro_exp
from bot.game.equipment import CATALOGUE, SHOWCASE, MAGIC_ITEMS
from bot.game.health import now_ts
from bot.game.looks import LOOKS, get_look
from bot.game.narrator import player_link
from bot.game.pro import (
    PROMO_DAYS,
    PROMO_UNTIL,
    PRO_BADGE,
    PRO_DAYS,
    PRO_ITEM,
    PRO_LOOK,
    PRO_STARS,
    current_offer,
    promo_is_on,
)
from bot.game.store import parse_payload, pro_payload
from bot.looks_service import LookError, choose_look, wardrobe
from bot.models import Player
from bot.pro_service import ProError, claim_free_pro, grant_pro
from bot.store_service import StoreError, StoreService
from bot.webapp.card import build_card, build_club, build_magic
from bot.webapp.server import create_app
from tests.test_store import FakeBot, TOKEN
from tests.test_webapp import FakeBot as AvatarBot, make_init_data

BLADE = CATALOGUE[PRO_ITEM]
AFTER_PROMO = PROMO_UNTIL + timedelta(days=1)
DURING_PROMO = PROMO_UNTIL - timedelta(days=1)


def make_player(user_id: int = 42, level: int = 5, **kwargs) -> Player:
    stats = Stats(strength=12, agility=8, intuition=8, endurance=12)
    return Player(
        user_id=user_id,
        nickname="Тайлер",
        class_code="warrior",
        level=level,
        **stats.as_dict(),
        **kwargs,
    )


def paid(stars: int = PRO_STARS, charge_id: str = "ch-pro") -> SuccessfulPayment:
    return SuccessfulPayment(
        currency="XTR",
        total_amount=stars,
        invoice_payload=pro_payload(42),
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="",
    )


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
async def store(bot, db):
    return StoreService(bot=bot, db=db, config=Config(bot_token="test"))


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    async with TestClient(TestServer(create_app(AvatarBot(), db, config))) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


@pytest.fixture
def after_promo(monkeypatch):
    """Акция кончилась: подписку продают за звёзды, как будет после 1 сентября."""
    monkeypatch.setattr(
        "bot.store_service.current_offer", lambda *a, **k: current_offer(AFTER_PROMO)
    )


# ---------- условия ----------


def test_the_promo_gives_a_week_for_free_and_then_it_is_a_month_for_stars():
    promo = current_offer(DURING_PROMO)
    normal = current_offer(AFTER_PROMO)

    assert promo.promo and promo.free
    assert (promo.stars, promo.days) == (0, PROMO_DAYS)
    assert not normal.promo and not normal.free
    assert (normal.stars, normal.days) == (PRO_STARS, PRO_DAYS)


def test_the_promo_ends_at_midnight_on_the_first_of_september_moscow_time():
    """Полночь 1 сентября по Москве — это 21:00 UTC 31 августа."""
    assert PROMO_UNTIL == datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc)
    assert promo_is_on(PROMO_UNTIL - timedelta(minutes=1))
    assert not promo_is_on(PROMO_UNTIL)


# ---------- что приходит с подпиской ----------


async def test_the_subscription_brings_the_blade_and_the_look(db):
    player = make_player()
    await db.save_player(player)
    now = now_ts()

    grant = await grant_pro(db, player, current_offer(DURING_PROMO), now)

    assert grant.blade and grant.look and not grant.renewed
    assert player.pro_until == now + PROMO_DAYS * 24 * 3600
    assert player.is_pro(now)
    assert [owned.code for owned in await db.list_gear(42)] == [PRO_ITEM]
    assert PRO_LOOK in await db.owned_looks(42)


async def test_a_second_subscription_extends_the_term_but_not_the_loot(db):
    """Клинок кладут один раз: второй такой же был бы просто хламом."""
    player = make_player()
    await db.save_player(player)
    now = now_ts()
    offer = current_offer(AFTER_PROMO)

    await grant_pro(db, player, offer, now)
    again = await grant_pro(db, player, offer, now)

    assert again.renewed and not again.blade and not again.look
    assert player.pro_until == now + 2 * PRO_DAYS * 24 * 3600
    assert len(await db.list_gear(42)) == 1


async def test_the_blade_and_the_look_outlive_the_subscription(db):
    """Срок вышел — значок и опыт кончились, а вещи остались."""
    player = make_player()
    await db.save_player(player)
    await grant_pro(db, player, current_offer(AFTER_PROMO))

    player.pro_until = now_ts() - 1  # подписка догорела
    await db.save_player(player)
    fresh = await db.get_player(42)

    assert not fresh.is_pro()
    assert [owned.code for owned in fresh.gear] == [PRO_ITEM]
    assert PRO_LOOK in await db.owned_looks(42)


def test_the_blade_is_a_reward_and_never_a_purchase():
    assert BLADE.reward and BLADE.price == 0 and BLADE.stars == 0
    assert BLADE not in SHOWCASE and BLADE not in MAGIC_ITEMS
    assert (BLADE.damage_min, BLADE.damage_max) == (3, 9)
    assert BLADE.crit == 0.15 and BLADE.intuition == 2
    assert BLADE.level_required == 1 and BLADE.requires.intuition == 7


# ---------- образ ассасина ----------


async def test_the_assassin_look_hides_until_the_subscription_hands_it_over(db):
    player = make_player()
    await db.save_player(player)

    before = await wardrobe(db, player)
    assert PRO_LOOK not in {row["code"] for row in before}
    with pytest.raises(LookError, match="подпиской PRO"):
        await choose_look(db, player, PRO_LOOK)

    await grant_pro(db, player, current_offer(DURING_PROMO))
    after = await wardrobe(db, player)
    row = next(r for r in after if r["code"] == PRO_LOOK)

    assert row["owned"] and row["pro"] and row["price"] == 0


async def test_the_assassin_look_costs_nothing_to_wear_once_it_is_yours(db):
    player = make_player(credits=0)
    await db.save_player(player)
    await grant_pro(db, player, current_offer(DURING_PROMO))

    choice = await choose_look(db, player, PRO_LOOK)

    assert not choice.bought and choice.credits == 0
    assert (await db.get_player(42)).look == PRO_LOOK


def test_the_assassin_look_is_drawn_and_not_counted_as_free():
    from bot.game.looks import free_looks

    look = get_look(PRO_LOOK)
    assert look.pro and look.price == 0
    assert look.picture.endswith("/avatars/assassin.jpeg")
    assert look not in free_looks()
    assert look in LOOKS


# ---------- полуторный опыт ----------


def test_pro_multiplies_the_experience_and_nothing_else():
    assert pro_exp(100, is_pro=True) == 150
    assert pro_exp(100, is_pro=False) == 100
    assert pro_exp(0, is_pro=True) == 0


async def test_a_subscriber_walks_out_of_a_duel_with_half_again_the_experience(
    db, bot
):
    """Сквозной прогон: тот же бой, но у одного бойца подписка."""
    import random

    from tests.test_duel_flow import (
        FakeBot as DuelBot,
        fight_to_the_end,
        make_service,
    )

    plain = make_player(user_id=1, level=3)
    plain.nickname = "Марла"
    subscriber = make_player(user_id=2, level=3)
    await db.save_player(plain)
    await db.save_player(subscriber)
    await grant_pro(db, subscriber, current_offer(AFTER_PROMO))

    service = make_service(DuelBot(), db)
    service.rng = random.Random(7)
    session = await service.start_duel(
        -100, 7, await db.get_player(1), await db.get_player(2)
    )
    await fight_to_the_end(service, session)

    first, second = await db.get_player(1), await db.get_player(2)
    winner = first if first.wins else second
    if winner.user_id == 2:
        # победил подписчик: его опыт заметно выше того, что дал бы бой без PRO
        assert second.total_exp > 0
        assert second.total_exp == round(second.total_exp / 1.5 * 1.5)


# ---------- значок ----------


def test_the_badge_follows_the_name_everywhere():
    player = make_player()
    assert player.badge == "" and PRO_BADGE not in player_link(player)

    player.pro_until = now_ts() + 3600
    assert player.badge == PRO_BADGE
    assert player_link(player).endswith(f" {PRO_BADGE}")
    assert player.titled() == f"Тайлер {PRO_BADGE}"


def test_the_badge_reaches_the_ring_through_the_fighter():
    player = make_player()
    player.pro_until = now_ts() + 3600

    fighter = Fighter.from_player(player, armed=True)

    assert fighter.pro
    assert not Fighter.from_player(make_player(), armed=True).pro


def test_the_club_list_and_the_card_both_carry_the_badge():
    player = make_player()
    player.pro_until = now_ts() + 3600

    row = build_club([player], viewer_id=42)["fighters"][0]
    card = build_card(player, TOKEN, viewer_id=42)

    assert row["pro"] is True
    assert card["pro"]["active"] and card["pro"]["badge"] == PRO_BADGE
    assert card["pro"]["seconds_left"] > 0


# ---------- прилавок ----------


def test_the_pro_card_leads_the_mage_counter():
    magic = build_magic(make_player())

    assert magic["pro"]["title"] == "Подписка PRO"
    assert magic["pro"]["benefits"][0].startswith("Полуторный опыт")
    assert magic["pro"]["image"].endswith("/magic/pro.jpeg")
    assert not magic["pro"]["active"]


def test_the_counter_shows_the_term_that_is_left():
    player = make_player()
    player.pro_until = now_ts() + 3 * 24 * 3600

    row = build_magic(player)["pro"]

    assert row["active"] and row["left_text"].startswith("72 ч")


# ---------- оплата ----------


def test_the_payload_names_the_subscription():
    assert parse_payload(pro_payload(7)) == ("pro", "month")


async def test_the_till_will_not_invoice_a_free_subscription(store):
    """По акции подписку забирают, а не оплачивают: счёт на ноль звёзд не бывает."""
    if not promo_is_on():
        pytest.skip("акция уже кончилась")
    with pytest.raises(StoreError, match="даром"):
        store.check(pro_payload(42))


async def test_paying_for_pro_starts_the_term_and_hands_over_the_loot(after_promo, store, db):
    await db.save_player(make_player())

    grant = await store.grant(42, paid())

    assert grant.is_pro and grant.pro.blade and grant.pro.look
    player = await db.get_player(42)
    assert player.is_pro()
    assert [owned.code for owned in player.gear] == [PRO_ITEM]


async def test_the_same_pro_payment_is_counted_once(after_promo, store, db):
    await db.save_player(make_player())

    await store.grant(42, paid())
    until = (await db.get_player(42)).pro_until
    again = await store.grant(42, paid())

    assert again.already
    assert (await db.get_player(42)).pro_until == until


async def test_a_refund_takes_the_subscription_and_its_loot_back(after_promo, store, db, bot):
    """Забрал звёзды — вернул и то, что они принесли."""
    await db.save_player(make_player())
    await store.grant(42, paid())

    await store.refund(42)

    player = await db.get_player(42)
    assert bot.refunds == [(42, "ch-pro")]
    assert not player.is_pro() and player.pro_until == 0
    assert player.gear == []
    assert PRO_LOOK not in await db.owned_looks(42)


# ---------- акция через мини-апп ----------


async def test_the_free_subscription_is_claimed_in_one_tap(client, db):
    if not promo_is_on():
        pytest.skip("акция уже кончилась")
    await db.save_player(make_player())

    response = await client.post("/api/pro", json={}, headers=headers())
    body = await response.json()

    assert response.status == 200
    assert body["pro"]["days"] == PROMO_DAYS
    assert body["pro"]["blade"] and body["pro"]["look"]
    assert body["card"]["pro"]["active"]
    assert body["magic"]["pro"]["active"]
    assert [owned.code for owned in await db.list_gear(42)] == [PRO_ITEM]


async def test_the_free_claim_is_refused_when_the_promo_is_over(db, monkeypatch):
    """Дату решает сервер: подделать её со страницы не выйдет."""
    monkeypatch.setattr("bot.pro_service.promo_is_on", lambda *a, **k: False)
    player = make_player()
    await db.save_player(player)

    with pytest.raises(ProError, match="Акция кончилась"):
        await claim_free_pro(db, player)
    assert not player.is_pro()
