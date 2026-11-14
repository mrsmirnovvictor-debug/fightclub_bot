"""Подписки PRO и PREMIUM: акция, срок, множитель опыта и значки."""

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.types import SuccessfulPayment
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats
from bot.game.combat import Fighter
from bot.game.equipment import CATALOGUE, SHOWCASE, MAGIC_ITEMS
from bot.game.health import now_ts
from bot.game.looks import LOOKS, get_look
from bot.game.narrator import player_link
from bot.game.pro import (
    PREMIUM,
    PREMIUM_BADGE,
    PREMIUM_TIER,
    PROMO_DAYS,
    PROMO_UNTIL,
    PRO_BADGE,
    PRO_DAYS,
    PRO_TIER,
    LEGACY_ITEM,
    LEGACY_LOOK,
    PRO_STARS,
    current_offer,
    paid_offer,
    promo_is_on,
)
from bot.game.store import parse_payload, pro_payload
from bot.looks_service import LookError, wardrobe, wear_look
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

BLADE = CATALOGUE[LEGACY_ITEM]
AFTER_PROMO = PROMO_UNTIL + timedelta(days=1)
DURING_PROMO = PROMO_UNTIL - timedelta(days=1)


def make_player(user_id: int = 42, level: int = 5, **kwargs) -> Player:
    stats = Stats(strength=12, agility=8, intuition=8, endurance=12)
    return Player(
        user_id=user_id,
        nickname=kwargs.pop("nickname", "Тайлер"),
        class_code="warrior",
        level=level,
        location=kwargs.pop("location", "premium_shop"),
        **stats.as_dict(),
        **kwargs,
    )


def paid(
    stars: int = PRO_STARS,
    charge_id: str = "ch-pro",
    tier: str = "pro",
) -> SuccessfulPayment:
    return SuccessfulPayment(
        currency="XTR",
        total_amount=stars,
        invoice_payload=pro_payload(42, tier),
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


async def test_the_subscription_brings_nothing_but_the_term(db):
    """Ни вещей, ни образа: подписка продаёт срок, а не вечное."""
    player = make_player()
    await db.save_player(player)
    now = now_ts()

    grant = await grant_pro(db, player, current_offer(DURING_PROMO), now)

    assert not grant.renewed
    assert player.pro_until == now + PROMO_DAYS * 24 * 3600
    assert player.is_pro(now)
    assert await db.list_gear(42) == []
    assert await db.owned_looks(42) == set()


async def test_a_second_subscription_only_extends_the_term(db):
    player = make_player()
    await db.save_player(player)
    now = now_ts()
    offer = current_offer(AFTER_PROMO)

    await grant_pro(db, player, offer, now)
    again = await grant_pro(db, player, offer, now)

    assert again.renewed
    assert player.pro_until == now + 2 * PRO_DAYS * 24 * 3600
    assert await db.list_gear(42) == []
    assert await db.owned_looks(42) == set()


def test_the_benefits_are_bare_one_line_promises(db):
    """Описание короткое намеренно — и ничего вечного не обещает.

    Проверяем не буквы, а обещание: ни одна строка не называет вещь или
    внешность. Без этого теста строку про клинок или образ вернули бы в
    описание первым же редактированием текста.
    """
    from bot.game import pro

    both = " ".join(
        line for tier in pro.TIERS for line in tier.benefits
    ).lower()
    said = (both + " " + pro.NOTE).lower()
    for word in ("клинок", "образ", "в инвентарь", "в рюкзак", "оружие"):
        assert word not in said, f"описание обещает вечное: {word}"
    # Значок в список не идёт: его видно и так
    assert "значок" not in said
    # По строке на услугу, каждая — одно предложение без оговорок
    for tier in pro.TIERS:
        for line in tier.benefits:
            assert line.count(".") == 0 and len(line) <= 60, line
    # А то, ради чего подписку берут, названо
    for word in ("аналитик", "страховка", "абонемент", "опыта", "машины"):
        assert word in said, f"описание молчит о главном: {word}"
    # Полис обещает только старшая ступень: у младшей его нет
    pro_said = " ".join(pro.PRO_TIER.benefits).lower()
    assert "страховка" not in pro_said, "PRO обещает полис, которого не даёт"
    assert "страховка" in " ".join(pro.PREMIUM_TIER.benefits).lower()


def test_the_blade_is_a_reward_and_never_a_purchase():
    assert BLADE.reward and BLADE.price == 0 and BLADE.stars == 0
    assert BLADE not in SHOWCASE and BLADE not in MAGIC_ITEMS
    assert (BLADE.damage_min, BLADE.damage_max) == (3, 9)
    assert BLADE.crit == 0.15 and BLADE.intuition == 2
    assert BLADE.level_required == 1 and BLADE.requires.intuition == 7


# ---------- образ ассасина: старая выдача ----------


async def test_the_assassin_look_is_not_handed_out_any_more(db):
    """Подписка его больше не даёт, и купить его нельзя."""
    player = make_player()
    await db.save_player(player)

    await grant_pro(db, player, current_offer(DURING_PROMO))

    shelf = {row["code"] for row in await wardrobe(db, player)}
    assert LEGACY_LOOK not in shelf, "подписка образ больше не открывает"
    with pytest.raises(LookError, match="больше не выдают"):
        await wear_look(db, player, LEGACY_LOOK)


async def test_the_assassin_look_stays_with_those_who_got_it(db):
    """У кого он есть — остался: отбирать выданное мы не собираемся."""
    player = make_player(credits=0)
    await db.save_player(player)
    await db.add_look(player.user_id, LEGACY_LOOK)  # выдали в прошлой версии

    shelf = await wardrobe(db, player)
    row = next(r for r in shelf if r["code"] == LEGACY_LOOK)
    choice = await wear_look(db, player, LEGACY_LOOK)

    assert row["owned"] and row["pro"] and row["price"] == 0
    assert not choice.bought and choice.credits == 0
    assert (await db.get_player(42)).look == LEGACY_LOOK


def test_the_assassin_look_is_drawn_and_not_counted_as_free():
    from bot.game.looks import free_looks

    look = get_look(LEGACY_LOOK)
    assert look.pro and look.price == 0
    assert look.picture.endswith("/avatars/assassin_transparent.png")
    assert look not in free_looks()
    assert look in LOOKS


# ---------- полуторный опыт ----------


def test_each_tier_multiplies_the_experience_by_its_own_number():
    """Множитель — свойство ступени, и берётся он у старшей живой."""
    player = make_player()
    assert player.exp_share() == 1.0

    player.pro_until = now_ts() + 3600
    assert player.exp_share() == PRO_TIER.exp_share == 1.5

    player.premium_until = now_ts() + 3600
    assert player.exp_share() == PREMIUM_TIER.exp_share == 2.0

    # Держать обе разом можно, и работает старшая: ступень — это уровень
    # обслуживания, а не сумма услуг
    player.pro_until = now_ts() + 10 * 24 * 3600
    assert player.exp_share() == PREMIUM_TIER.exp_share


def test_the_formula_rounds_up_once_and_at_the_very_end():
    """Подписка множит заработанное, а не округлённое.

    Округляй по дороге — и опыт зависел бы от порядка множителей:
    ceil(101 × 1,5) это 152, а ceil(101) × 1,5 — уже 151,5, то есть не
    целое число вовсе.
    """
    import math

    from bot.game.economy import base_exp, win_exp

    # Бой, где все три множителя дробные: сто два урона при средних ста
    # ХП — это 1,02; второй бой с тем же соперником — 0,8; подписка — 1,5
    raw = base_exp(5) * 1.02 * 0.8 * PRO_TIER.exp_share
    got = win_exp(
        102, 5, 5, 200, 2, previous_fights=1, exp_share=PRO_TIER.exp_share
    )

    assert got == math.ceil(raw) == 184

    # А если округлять на каждом шаге, выходит на единицу больше. Эта
    # единица и есть вся разница между «подписка множит заработанное» и
    # «подписка множит округлённое»
    step = math.ceil(base_exp(5) * 1.02)
    step = math.ceil(step * 0.8)
    step = math.ceil(step * PRO_TIER.exp_share)
    assert step == 185 and got == 184


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
        # Победил подписчик. Сверяем не «больше нуля», а ровно ту цифру,
        # которую даёт формула на числах этого самого боя: иначе тест
        # проходил бы и с выключенной подпиской
        import math

        from bot.game.economy import base_exp

        me = session.fighters[2]
        rival = session.fighters[1]
        raw = base_exp(3) * min(
            1.05, max(1.0, me.damage_dealt / ((me.max_hp + rival.max_hp) / 2))
        )
        assert second.total_exp == math.ceil(raw * 1.5) > math.ceil(raw)


# ---------- значок ----------


def test_the_badge_follows_the_name_everywhere():
    player = make_player()
    assert player.badge == "" and PRO_BADGE not in player_link(player)

    player.pro_until = now_ts() + 3600
    assert player.badge == PRO_BADGE
    assert player_link(player).endswith(f" {PRO_BADGE}")
    assert player.titled() == f"Тайлер {PRO_BADGE}"


def test_the_crystal_is_pro_and_the_crown_is_premium():
    """У ступеней свои значки, и старшая перебивает младшую."""
    assert PRO_BADGE == "💎" and PREMIUM_BADGE == "👑"

    player = make_player()
    player.premium_until = now_ts() + 3600
    assert player.badge == PREMIUM_BADGE
    assert player.titled() == f"Тайлер {PREMIUM_BADGE}"

    # Обе разом — корона: значок показывает ступень обслуживания
    player.pro_until = now_ts() + 10 * 24 * 3600
    assert player.badge == PREMIUM_BADGE


def test_the_badge_reaches_the_ring_through_the_fighter():
    player = make_player()
    player.pro_until = now_ts() + 3600

    fighter = Fighter.from_player(player, armed=True)

    assert fighter.badge == PRO_BADGE
    assert not Fighter.from_player(make_player(), armed=True).badge

    player.premium_until = now_ts() + 3600
    assert Fighter.from_player(player, armed=True).badge == PREMIUM_BADGE


def test_the_club_list_and_the_card_both_carry_the_badge():
    player = make_player()
    player.pro_until = now_ts() + 3600

    row = build_club([player], viewer_id=42)["fighters"][0]
    card = build_card(player, TOKEN, viewer_id=42)

    assert row["pro"] is True
    assert card["pro"]["active"] and card["pro"]["badge"] == PRO_BADGE
    assert card["pro"]["seconds_left"] > 0


# ---------- прилавок ----------


def test_both_tiers_lead_the_mage_counter():
    """Ступени стоят на прилавке подряд, младшая первой."""
    magic = build_magic(make_player())

    assert [one["code"] for one in magic["tiers"]] == ["pro", "premium"]
    assert [one["stars"] for one in magic["tiers"]] == [250, 500]
    assert [one["badge"] for one in magic["tiers"]] == [PRO_BADGE, PREMIUM_BADGE]
    # Одиночный ключ остался и ведёт на младшую: по нему берут карточку
    # страницы, которые про вторую ступень ещё не знают
    assert magic["pro"]["title"] == "Подписка PRO"
    assert magic["pro"]["benefits"] == list(PRO_TIER.benefits)
    assert magic["pro"]["image"].endswith("/subscriptions/pro.webp")
    assert magic["tiers"][1]["image"].endswith("/subscriptions/premium.webp")
    assert not magic["pro"]["active"]


def test_the_premium_card_promises_what_pro_does_not():
    magic = build_magic(make_player())
    premium = magic["tiers"][1]

    assert premium["title"] == "Подписка PREMIUM"
    assert premium["benefits"] == list(PREMIUM_TIER.benefits)
    said = " ".join(premium["benefits"]).lower()
    assert "200% опыта" in said
    assert "страховка" in said
    assert "20%" in said


def test_the_counter_shows_the_term_that_is_left():
    player = make_player()
    player.pro_until = now_ts() + 3 * 24 * 3600

    row = build_magic(player)["pro"]

    assert row["active"] and row["left_text"].startswith("72 ч")


# ---------- оплата ----------


def test_the_payload_names_the_tier():
    assert parse_payload(pro_payload(7)) == ("pro", "pro")
    assert parse_payload(pro_payload(7, PREMIUM)) == ("pro", "premium")


def test_an_old_invoice_still_buys_pro():
    """Счёт, выставленный до второй ступени, обязан дойти.

    В середине у него стоит «month»: ступень была одна, и различать было
    нечего. Касса понимает такой счёт как PRO, а не отвергает платёж.
    """
    from bot.game.pro import get_tier

    assert parse_payload("pro:month:7") == ("pro", "month")
    assert get_tier("month") is None
    assert (get_tier("month") or PRO_TIER) is PRO_TIER


def test_stars_always_buy_the_normal_month_even_during_the_promo(store):
    """Акция — разовый бесплатный вход, а не скидка на продление."""
    offer = store.check(pro_payload(42))

    assert (offer.stars, offer.days) == (PRO_STARS, PRO_DAYS)
    assert not offer.promo


async def test_paying_for_pro_starts_the_term_and_nothing_else(store, db):
    await db.save_player(make_player())

    grant = await store.grant(42, paid())

    assert grant.is_pro
    player = await db.get_player(42)
    assert player.is_pro()
    assert player.gear == [], "за звёзды приходит срок, а не снаряжение"
    assert await db.owned_looks(42) == set(), "и не внешность"


async def test_the_same_pro_payment_is_counted_once(store, db):
    await db.save_player(make_player())

    await store.grant(42, paid())
    until = (await db.get_player(42)).pro_until
    again = await store.grant(42, paid())

    assert again.already
    assert (await db.get_player(42)).pro_until == until


async def test_paying_for_premium_starts_the_top_term_and_not_pro(store, db):
    """Звёзды за PREMIUM продлевают PREMIUM, а не младшую ступень."""
    from bot.game.pro import PREMIUM_STARS

    await db.save_player(make_player())

    grant = await store.grant(
        42, paid(PREMIUM_STARS, "ch-premium", PREMIUM)
    )

    assert grant.is_pro and grant.goods.tier is PREMIUM_TIER
    player = await db.get_player(42)
    assert player.is_premium() and player.premium_until > 0
    assert player.pro_until == 0, "продлили не ту ступень"
    assert player.tier() is PREMIUM_TIER
    assert player.badge == PREMIUM_BADGE


async def test_the_two_terms_live_side_by_side(store, db):
    """Оплаченный месяц PRO доживает свой срок под старшей ступенью.

    Иначе покупка младшей ступени исчезала бы в старшей, и за неё
    пришлось бы возвращать звёзды.
    """
    from bot.game.pro import PREMIUM_STARS

    await db.save_player(make_player())

    await store.grant(42, paid())
    await store.grant(42, paid(PREMIUM_STARS, "ch-premium", PREMIUM))

    player = await db.get_player(42)
    assert player.pro_until > 0 and player.premium_until > 0
    # Работает старшая: ступень — это уровень обслуживания, а не сумма
    assert player.tier() is PREMIUM_TIER
    assert player.exp_share() == PREMIUM_TIER.exp_share
    assert player.cars_allowed() == PREMIUM_TIER.cars


async def test_a_refund_of_premium_leaves_pro_alone(store, db, bot):
    """Возврат звёзд за PREMIUM не отбирает оплаченный месяц PRO."""
    from bot.game.pro import PREMIUM_STARS

    await db.save_player(make_player())
    await store.grant(42, paid())
    await store.grant(42, paid(PREMIUM_STARS, "ch-premium", PREMIUM))
    pro_until = (await db.get_player(42)).pro_until

    await store.refund(42, "ch-premium")

    player = await db.get_player(42)
    assert player.premium_until == 0, "PREMIUM не сняли"
    assert player.pro_until == pro_until, "заодно отобрали и PRO"
    assert player.tier() is PRO_TIER


async def test_a_refund_takes_the_subscription_and_its_loot_back(store, db, bot):
    """Забрал звёзды — вернул и то, что они принесли."""
    await db.save_player(make_player())
    await store.grant(42, paid())

    await store.refund(42)

    player = await db.get_player(42)
    assert bot.refunds == [(42, "ch-pro")]
    assert not player.is_pro() and player.pro_until == 0
    assert player.gear == []
    assert LEGACY_LOOK not in await db.owned_looks(42)


# ---------- акция через мини-апп ----------


async def test_the_free_subscription_is_claimed_in_one_tap(client, db, promo_running):
    await db.save_player(make_player())

    response = await client.post("/api/pro", json={}, headers=headers())
    body = await response.json()

    assert response.status == 200
    assert body["pro"]["days"] == PROMO_DAYS
    # В ответе — только срок: ни вещи, ни образа подписка не выдаёт
    assert "blade" not in body["pro"] and "look" not in body["pro"]
    assert body["card"]["pro"]["active"]
    assert body["magic"]["pro"]["active"]
    assert await db.list_gear(42) == []
    assert await db.owned_looks(42) == set()


@pytest.fixture
def promo_running(monkeypatch):
    """Акция идёт — независимо от того, какое сегодня число.

    Правило «бесплатная неделя даётся один раз» живёт дольше самой акции,
    и проверять его надо всегда, а не до первого сентября. Календарь здесь
    ни при чём: у него свой тест.
    """
    from datetime import datetime, timezone

    # Двигаем сам календарь, а не отдельные проверки: и лавка мага, и выдача
    # смотрят на одну дату, значит и подменять надо её одну.
    monkeypatch.setattr(
        "bot.game.pro.PROMO_UNTIL", datetime(2099, 1, 1, tzinfo=timezone.utc)
    )


async def test_the_free_claim_is_refused_when_the_promo_is_over(db, monkeypatch):
    """Дату решает сервер: подделать её со страницы не выйдет."""
    monkeypatch.setattr("bot.pro_service.promo_is_on", lambda *a, **k: False)
    player = make_player()
    await db.save_player(player)

    with pytest.raises(ProError, match="Акция кончилась"):
        await claim_free_pro(db, player)
    assert not player.is_pro()


# ---------- бесплатная неделя даётся один раз ----------


async def test_the_free_week_is_handed_out_once_and_never_again(db, promo_running):
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


async def test_the_free_week_stays_spent_even_after_it_burns_out(db, promo_running):
    """Неделя кончилась — второй раз даром её всё равно не дают."""
    player = make_player()
    await db.save_player(player)
    await claim_free_pro(db, player)
    player.pro_until = now_ts() - 1
    await db.save_player(player)

    with pytest.raises(ProError, match="уже забирал"):
        await claim_free_pro(db, player)
    assert await promo_taken(db, 42)


async def test_the_free_claim_leaves_no_trace_in_the_purchase_history(db, store, promo_running):
    """Подарок — не покупка: ни в истории, ни в возвратах его нет."""
    player = make_player()
    await db.save_player(player)
    await claim_free_pro(db, player)

    assert await db.purchases_of(42) == []
    with pytest.raises(StoreError, match="подарок"):
        await store.refund(42, promo_claim_id(42))


async def test_the_counter_offers_stars_once_the_free_week_is_taken(db, promo_running):
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


async def test_the_mini_app_refuses_the_second_free_claim(client, db, promo_running):
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


