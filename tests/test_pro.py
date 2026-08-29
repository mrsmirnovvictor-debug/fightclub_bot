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
from bot.pro_service import (
    ProError,
    claim_free_pro,
    grant_pro,
    promo_claim_id,
    promo_taken,
)
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
        nickname=kwargs.pop("nickname", "Тайлер"),
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


def test_stars_always_buy_the_normal_month_even_during_the_promo(store):
    """Акция — разовый бесплатный вход, а не скидка на продление."""
    offer = store.check(pro_payload(42))

    assert (offer.stars, offer.days) == (PRO_STARS, PRO_DAYS)
    assert not offer.promo


async def test_paying_for_pro_starts_the_term_and_hands_over_the_loot(store, db):
    await db.save_player(make_player())

    grant = await store.grant(42, paid())

    assert grant.is_pro and grant.pro.blade and grant.pro.look
    player = await db.get_player(42)
    assert player.is_pro()
    assert [owned.code for owned in player.gear] == [PRO_ITEM]


async def test_the_same_pro_payment_is_counted_once(store, db):
    await db.save_player(make_player())

    await store.grant(42, paid())
    until = (await db.get_player(42)).pro_until
    again = await store.grant(42, paid())

    assert again.already
    assert (await db.get_player(42)).pro_until == until


async def test_a_refund_takes_the_subscription_and_its_loot_back(store, db, bot):
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


# ---------- бесплатная неделя даётся один раз ----------


async def test_the_free_week_is_handed_out_once_and_never_again(db):
    """Кнопку «забрать даром» можно было тыкать без счёта — по неделе за раз."""
    player = make_player()
    await db.save_player(player)
    now = now_ts()

    first = await claim_free_pro(db, player, now)

    assert first.offer.days == PROMO_DAYS
    assert player.pro_until == now + PROMO_DAYS * 24 * 3600

    for _ in range(3):
        with pytest.raises(ProError, match="уже забирал"):
            await claim_free_pro(db, player, now)

    # срок не сдвинулся ни на секунду
    assert player.pro_until == now + PROMO_DAYS * 24 * 3600
    assert (await db.get_player(42)).pro_until == player.pro_until


async def test_the_free_week_stays_spent_even_after_it_burns_out(db):
    """Неделя кончилась — второй раз даром её всё равно не дают."""
    player = make_player()
    await db.save_player(player)
    await claim_free_pro(db, player)
    player.pro_until = now_ts() - 1
    await db.save_player(player)

    with pytest.raises(ProError, match="уже забирал"):
        await claim_free_pro(db, player)
    assert await promo_taken(db, 42)


async def test_the_free_claim_leaves_no_trace_in_the_purchase_history(db, store):
    """Подарок — не покупка: ни в истории, ни в возвратах его нет."""
    player = make_player()
    await db.save_player(player)
    await claim_free_pro(db, player)

    assert await db.purchases_of(42) == []
    with pytest.raises(StoreError, match="подарок"):
        await store.refund(42, promo_claim_id(42))


async def test_the_counter_offers_stars_once_the_free_week_is_taken(db):
    player = make_player()
    await db.save_player(player)

    before = build_magic(player, promo_claimed=False)["pro"]
    await claim_free_pro(db, player)
    after = build_magic(player, promo_claimed=True)["pro"]

    assert before["free"] and before["stars"] == 0 and before["days"] == PROMO_DAYS
    assert not after["free"]
    assert after["stars"] == PRO_STARS and after["days"] == PRO_DAYS
    assert after["promo_claimed"]
    assert "уже забрал" in after["promo_note"]


async def test_the_mini_app_refuses_the_second_free_claim(client, db):
    if not promo_is_on():
        pytest.skip("акция уже кончилась")
    await db.save_player(make_player())

    first = await client.post("/api/pro", json={}, headers=headers())
    second = await client.post("/api/pro", json={}, headers=headers())
    body = await second.json()

    assert first.status == 200
    assert second.status == 409
    assert "уже забирал" in body["error"]
    # и прилавок сразу показывает цену продления
    magic = await (await client.get("/api/magic", headers=headers())).json()
    assert not magic["pro"]["free"] and magic["pro"]["stars"] == PRO_STARS


# ---------- разовая правка сроков ----------


async def test_the_overrun_subscription_is_cut_back_to_one_week(db):
    """Разовая правка: у бойца набежал месяц от повторных нажатий."""
    from bot.game.pro import DAY
    from bot.seed import TEST_FIGHTER, fix_promo_overrun

    player = make_player(user_id=7, nickname=TEST_FIGHTER)
    player.pro_until = now_ts() + 28 * DAY  # четыре нажатия по неделе
    await db.save_player(player)

    assert await fix_promo_overrun(db) is True

    fixed = await db.get_player(7)
    assert fixed.pro_left() <= PROMO_DAYS * DAY
    assert fixed.pro_left() > (PROMO_DAYS - 1) * DAY
    # акция теперь числится забранной: второй раз даром не дадут
    assert await promo_taken(db, 7)
    with pytest.raises(ProError, match="уже забирал"):
        await claim_free_pro(db, fixed)


async def test_the_fix_runs_once_and_never_touches_an_honest_term(db):
    from bot.game.pro import DAY
    from bot.seed import TEST_FIGHTER, fix_promo_overrun

    player = make_player(user_id=7, nickname=TEST_FIGHTER)
    player.pro_until = now_ts() + 28 * DAY
    await db.save_player(player)
    await fix_promo_overrun(db)
    after_fix = (await db.get_player(7)).pro_until

    # второй запуск ничего не трогает — даже если бойцу докупили месяц
    player = await db.get_player(7)
    player.pro_until += 30 * DAY
    await db.save_player(player)
    assert await fix_promo_overrun(db) is False
    assert (await db.get_player(7)).pro_until == after_fix + 30 * DAY


async def test_the_fix_leaves_a_short_term_alone(db):
    """Срок короче обещанного не растягиваем: правка только урезает."""
    from bot.game.pro import DAY
    from bot.seed import TEST_FIGHTER, fix_promo_overrun

    player = make_player(user_id=7, nickname=TEST_FIGHTER)
    player.pro_until = now_ts() + 2 * DAY
    await db.save_player(player)

    assert await fix_promo_overrun(db) is False
    assert (await db.get_player(7)).pro_left() <= 2 * DAY + 5
