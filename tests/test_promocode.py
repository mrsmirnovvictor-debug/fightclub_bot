"""Промокод: слово, которое превращается в подписку.

Код знать надо — его называют снаружи игры. Поэтому прилавок его не
показывает, а проверки здесь делятся на три: как читается набранное,
что код выдаёт и один ли раз, и что отвечает ручка.
"""

from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats
from bot.game.health import now_ts
from bot.game.pro import PRO, PRO_DAYS, PRO_TIER
from bot.game.promocodes import CODES, PromoCode, find, known, normalize
from bot.models import Player
from bot.pro_service import ProError, code_claim_id, code_taken, redeem_code
from bot.webapp.server import create_app
from tests.test_webapp import FakeBot as AvatarBot, TOKEN, make_init_data

START = "VEGAS1334"


def make_player(user_id: int = 42, **kwargs) -> Player:
    stats = Stats(strength=12, agility=8, intuition=8, endurance=12)
    return Player(
        user_id=user_id,
        nickname=kwargs.pop("nickname", "Тайлер"),
        class_code="warrior",
        level=kwargs.pop("level", 5),
        location=kwargs.pop("location", "premium_shop"),
        **stats.as_dict(),
        **kwargs,
    )


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    async with TestClient(TestServer(create_app(AvatarBot(), db, config))) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


# ---------- как читается набранное ----------


def test_the_start_code_gives_a_month_of_pro():
    """VEGAS1334 — месяц PRO, и это записано в одном месте."""
    code = find(START)
    assert code is not None, "кода старта нет в списке"
    assert code.days == PRO_DAYS == 30
    assert code.tier == PRO
    assert code.tier_obj is PRO_TIER
    assert code.term_text == "месяц"
    # Оферта та же, что у покупки, только даром
    offer = code.offer()
    assert offer.free and offer.stars == 0 and offer.days == PRO_DAYS
    assert offer.tier is PRO_TIER


def test_the_code_is_read_however_it_was_typed():
    """Строчными, с пробелами, через дефис — всё это один код.

    Отказ «такого кода нет» в ответ на верный код, набранный иначе,
    игрок прочтёт как обман, а не как свою опечатку.
    """
    for typed in (
        START, "vegas1334", "Vegas1334", " VEGAS1334 ", "vegas 1334",
        "VEGAS-1334", "vegas_1334", "  vegas — 1334  ",
    ):
        got = find(typed)
        assert got is not None, f"не узнали код в «{typed}»"
        assert got.code == START


def test_the_cyrillic_lookalikes_are_the_same_letters():
    """Набранный с кириллическими двойниками код выглядит так же.

    Двойник есть не у всякой буквы: кириллическая «С» — это латинская
    «C», а не «S», и подменять ею «S» было бы угадыванием. Сводим только
    то, что и правда неотличимо на вид.
    """
    # «Е» и «А» здесь кириллические
    mixed = "V" + "Е" + "G" + "А" + "S" + "1334"
    assert mixed != START, "буквы в проверке не те, что нужно"
    assert normalize(mixed) == START
    assert find(mixed) is not None

    # А вот кириллическая «С» — это «C», и кодом она не станет
    assert normalize("VEGA" + "С" + "1334") == "VEGAC1334"


def test_an_unknown_code_is_not_found_and_not_known():
    assert find("CLUB0000") is None
    assert not known("CLUB0000")
    assert find("") is None and not known("")
    assert find("   ") is None


def test_a_code_past_its_day_is_known_but_dead():
    """Истёкший код отличается от выдуманного: про него есть что сказать."""
    long_ago = datetime(2020, 1, 1, tzinfo=timezone.utc)
    old = PromoCode(code="OLD2020", days=30, until=long_ago)
    assert not old.is_on()
    assert old.is_on(long_ago - timedelta(days=1))
    # Бессрочный работает всегда: дата, выставленная наугад, однажды
    # отключила бы код посреди рекламы
    assert find(START).is_on(datetime(2030, 1, 1, tzinfo=timezone.utc))


def test_the_codes_in_the_list_are_spelled_once_and_the_same_way():
    """Код в списке записан так же, как его читают: без мусора и в верхнем."""
    for one in CODES:
        assert normalize(one.code) == one.code, one.code
        assert one.days > 0 and one.tier


# ---------- что код выдаёт ----------


async def test_the_code_gives_a_month_of_pro_to_whoever_typed_it(db):
    player = make_player()
    await db.save_player(player)
    now = now_ts()

    code, grant = await redeem_code(db, player, "vegas 1334", now)

    assert code.code == START
    assert grant.offer.free, "за код звёзд не берут"
    assert not grant.renewed, "подписки не было — значит, не продление"
    assert grant.until == now + PRO_DAYS * 24 * 3600
    assert player.is_pro(now) and player.is_pro_exactly(now)
    assert not player.is_premium(now)

    fresh = await db.get_player(player.user_id)
    assert fresh.pro_until == grant.until, "срок не доехал до базы"


async def test_the_same_code_works_once_per_fighter(db):
    """Один код на бойца. Иначе это кнопка «ещё месяц» без конца."""
    player = make_player()
    await db.save_player(player)

    await redeem_code(db, player, START)
    assert await code_taken(db, START, player.user_id)

    with pytest.raises(ProError) as oops:
        await redeem_code(db, player, START)
    assert "уже применял" in str(oops.value)

    # И написание тут ничего не меняет: ключ в журнале один
    with pytest.raises(ProError):
        await redeem_code(db, player, "vegas-1334")


async def test_the_code_of_one_fighter_does_not_spend_it_for_another(db):
    """Код общий: сосед применил — твой ещё при тебе."""
    one = make_player(42)
    two = make_player(51, nickname="Марла")
    await db.save_player(one)
    await db.save_player(two)

    await redeem_code(db, one, START)
    assert await code_taken(db, START, 42)
    assert not await code_taken(db, START, 51)

    _, grant = await redeem_code(db, two, START)
    assert grant.until > 0 and two.is_pro(now_ts())


async def test_a_code_on_top_of_a_live_subscription_extends_it(db):
    """Код продлевает живую подписку, а не заменяет её."""
    now = now_ts()
    player = make_player(pro_until=now + 10 * 24 * 3600)
    await db.save_player(player)

    _, grant = await redeem_code(db, player, START, now)

    assert grant.renewed, "подписка была жива — это продление"
    assert grant.until == now + (10 + PRO_DAYS) * 24 * 3600


async def test_an_unknown_code_is_refused_and_spends_nothing(db):
    player = make_player()
    await db.save_player(player)

    with pytest.raises(ProError) as oops:
        await redeem_code(db, player, "CLUB0000")
    assert "Такого промокода нет" in str(oops.value)

    # Отказ ничего не потратил: верный код после него работает
    assert not await code_taken(db, START, player.user_id)
    _, grant = await redeem_code(db, player, START)
    assert grant.until > 0


async def test_an_empty_field_says_so_and_does_not_hunt_for_a_code(db):
    player = make_player()
    await db.save_player(player)

    for typed in ("", "   ", "---"):
        with pytest.raises(ProError) as oops:
            await redeem_code(db, player, typed)
        assert "поле пустое" in str(oops.value), typed


async def test_the_claim_key_is_the_same_whatever_the_spelling(db):
    """Ключ в журнале один на код и бойца — он и держит «один раз»."""
    assert code_claim_id("vegas 1334", 42) == code_claim_id(START, 42)
    assert code_claim_id(START, 42) != code_claim_id(START, 51)
    assert START in code_claim_id(START, 42)


# ---------- ручка ----------


async def test_the_shop_takes_the_code_and_gives_back_the_card(client, db):
    player = make_player()
    await db.save_player(player)

    answer = await client.post(
        "/api/promo", json={"code": "vegas1334"}, headers=headers()
    )
    assert answer.status == 200
    body = await answer.json()

    assert body["promo"]["code"] == START
    assert body["promo"]["term"] == "месяц"
    assert body["promo"]["days"] == PRO_DAYS
    assert body["promo"]["renewed"] is False
    assert body["promo"]["seconds_left"] > 0
    # Карточка и прилавок приезжают тем же ответом: перечитывать нечего
    assert body["card"]["pro"]["active"], "подписка не встала в карточку"
    assert body["card"]["pro"]["tier"] == PRO
    assert body["magic"]["tiers"], "прилавок не вернулся"

    fresh = await db.get_player(42)
    assert fresh.is_pro(now_ts())


async def test_the_shop_refuses_a_wrong_code_with_words_for_the_player(client, db):
    await db.save_player(make_player())

    answer = await client.post(
        "/api/promo", json={"code": "CLUB0000"}, headers=headers()
    )
    assert answer.status == 409
    assert "Такого промокода нет" in (await answer.json())["error"]

    empty = await client.post("/api/promo", json={}, headers=headers())
    assert empty.status == 409
    assert "поле пустое" in (await empty.json())["error"]


async def test_the_shop_refuses_the_second_try_of_the_same_code(client, db):
    await db.save_player(make_player())

    first = await client.post(
        "/api/promo", json={"code": START}, headers=headers()
    )
    assert first.status == 200

    second = await client.post(
        "/api/promo", json={"code": START}, headers=headers()
    )
    assert second.status == 409
    assert "уже применял" in (await second.json())["error"]


async def test_a_stranger_without_a_fighter_gets_nothing(client, db):
    """Кода без бойца не применить: некому выдавать подписку."""
    answer = await client.post(
        "/api/promo", json={"code": START}, headers=headers(999)
    )
    assert answer.status in (404, 409)
