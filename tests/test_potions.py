"""Эликсиры: покупка, глоток, срок действия и раздел «Прочее» на витрине."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats, get_class
from bot.game.health import now_ts
from bot.game.potions import (
    EFFECT_SECONDS,
    POTIONS,
    ActiveEffect,
    PotionKind,
    get_potion,
    spell_duration,
)
from bot.game.stats import derive
from bot.models import Player
from bot.potions_service import PotionError, buy_potion, use_potion
from bot.webapp.card import build_card, build_shop
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot, headers
from tests.test_webapp import TOKEN

HEAL = get_potion("heal_small")
BIG_HEAL = get_potion("heal_big")
STRENGTH = get_potion("boost_strength")
LIFE = get_potion("boost_hp")


def make_player(user_id: int = 1, credits: int = 1000, level: int = 5) -> Player:
    fclass = get_class("warrior")
    stats = Stats(strength=12, agility=8, intuition=8, endurance=12)
    return Player(
        user_id=user_id,
        nickname="Тайлер",
        class_code=fclass.code,
        level=level,
        credits=credits,
        **stats.as_dict(),
    )


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    async with TestClient(TestServer(create_app(FakeBot(), db, config))) as client:
        yield client


# ---------- каталог ----------


def test_every_potion_has_a_price_and_does_something():
    for potion in POTIONS:
        assert potion.price > 0, potion.code
        assert potion.describe(), potion.code
        if potion.kind is PotionKind.HEAL:
            assert potion.heal > 0 and not potion.seconds
        else:
            assert potion.seconds == EFFECT_SECONDS
            assert potion.bonus.total() or potion.hp


def test_the_shelf_holds_exactly_what_was_asked_for():
    """Два эликсира восстановления и четыре временных — больше ничего."""
    heals = [p for p in POTIONS if p.kind is PotionKind.HEAL]
    boosts = [p for p in POTIONS if p.kind is PotionKind.BOOST]
    assert sorted(p.heal for p in heals) == [30, 60]
    assert sorted(p.bonus.total() + p.hp for p in boosts) == [10, 10, 10, 60]


def test_duration_reads_like_a_sentence():
    assert spell_duration(EFFECT_SECONDS) == "2 ч"
    assert spell_duration(3600 + 47 * 60) == "1 ч 47 мин"
    assert spell_duration(12 * 60) == "12 мин"
    assert spell_duration(9) == "9 сек"


# ---------- покупка ----------


async def test_buying_a_potion_takes_the_money_and_stacks_it(db):
    player = make_player(credits=100)
    await db.save_player(player)

    await buy_potion(db, player, "heal_small")
    await buy_potion(db, player, "heal_small")

    assert player.credits == 100 - 2 * HEAL.price
    assert player.potion_count("heal_small") == 2
    assert await db.list_potions(player.user_id) == {"heal_small": 2}


async def test_a_potion_locked_by_level_stays_on_the_shelf(db):
    player = make_player(level=4)
    await db.save_player(player)

    with pytest.raises(PotionError, match="открывается на 5 уровне"):
        await buy_potion(db, player, "boost_strength")
    assert player.potion_count("boost_strength") == 0


async def test_an_empty_purse_buys_nothing(db):
    player = make_player(credits=10)
    await db.save_player(player)

    with pytest.raises(PotionError, match="Не хватает кредитов"):
        await buy_potion(db, player, "boost_strength")
    assert player.credits == 10


# ---------- восстановление ----------


async def test_healing_tops_the_fighter_up_but_never_above_the_ceiling(db):
    player = make_player()
    await db.save_player(player)
    await buy_potion(db, player, "heal_big")
    # избит: до потолка не хватает меньше, чем даёт склянка
    player.set_hp(player.max_hp - 20)
    await db.save_player(player)

    result = await use_potion(db, player, "heal_big")

    assert result.healed == 20  # лишнее не наливается
    assert player.current_hp() == player.max_hp
    assert player.potion_count("heal_big") == 0


async def test_a_full_fighter_keeps_the_bottle_corked(db):
    player = make_player()
    await db.save_player(player)
    await buy_potion(db, player, "heal_small")

    with pytest.raises(PotionError, match="полном порядке"):
        await use_potion(db, player, "heal_small")
    assert player.potion_count("heal_small") == 1  # склянка цела


async def test_drinking_what_you_do_not_have_is_refused(db):
    player = make_player()
    await db.save_player(player)

    with pytest.raises(PotionError, match="в рюкзаке нет"):
        await use_potion(db, player, "heal_small")


# ---------- временные эффекты ----------


async def test_the_strength_potion_lifts_the_stat_for_two_hours(db):
    player = make_player()
    await db.save_player(player)
    await buy_potion(db, player, "boost_strength")
    before = player.stats.strength
    now = now_ts()

    result = await use_potion(db, player, "boost_strength", now=now)

    assert result.until == now + EFFECT_SECONDS
    assert player.stats.strength == before + 10
    assert [effect.code for effect in player.active_effects(now)] == ["boost_strength"]
    # …и через два часа с минутой всё как было
    assert player.active_effects(now + EFFECT_SECONDS + 60) == []


def test_an_expired_effect_stops_counting():
    player = make_player()
    now = now_ts()
    player.effects = [ActiveEffect(code="boost_strength", until=now - 1)]

    assert player.stats.strength == 12
    assert player.active_effects() == []


async def test_the_life_potion_raises_the_ceiling_and_gives_it_back_later(db):
    player = make_player()
    await db.save_player(player)
    await buy_potion(db, player, "boost_hp")
    ceiling = player.max_hp

    await use_potion(db, player, "boost_hp")
    assert player.max_hp == ceiling + LIFE.hp

    # эффект догорел — потолок вернулся на своё место
    player.effects = []
    assert player.max_hp == ceiling


async def test_a_second_bottle_extends_the_clock_and_not_the_bonus(db):
    player = make_player(credits=1000)
    await db.save_player(player)
    await buy_potion(db, player, "boost_strength")
    await buy_potion(db, player, "boost_strength")
    now = now_ts()

    first = await use_potion(db, player, "boost_strength", now=now)
    second = await use_potion(db, player, "boost_strength", now=now)

    assert second.extended and not first.extended
    assert second.until == now + 2 * EFFECT_SECONDS
    # прибавка та же: +10, а не +20
    assert player.stats.strength == 22
    assert len(player.active_effects(now)) == 1


async def test_the_effect_survives_a_restart(db):
    """Срок лежит в базе, поэтому боец не теряет выпитое при перезапуске."""
    player = make_player()
    await db.save_player(player)
    await buy_potion(db, player, "boost_agility")
    await use_potion(db, player, "boost_agility")

    fresh = await db.get_player(player.user_id)

    assert [effect.code for effect in fresh.effects] == ["boost_agility"]
    assert fresh.stats.agility == 18


async def test_the_database_forgets_effects_whose_time_ran_out(db):
    player = make_player()
    await db.save_player(player)
    await db.set_effect(player.user_id, "boost_strength", now_ts() - 5)

    assert await db.list_effects(player.user_id) == []


# ---------- бой ----------


def test_a_drunk_elixir_goes_into_the_fist_fight_too():
    """Вещи остаются в раздевалке, а выпитое — нет: его не снимешь."""
    from bot.game.combat import Fighter

    player = make_player()
    player.effects = [
        ActiveEffect(code="boost_strength", until=now_ts() + EFFECT_SECONDS),
        ActiveEffect(code="boost_hp", until=now_ts() + EFFECT_SECONDS),
    ]
    bare = derive(player.fclass, player.base_stats, player.level)

    fighter = Fighter.from_player(player, armed=False)

    assert fighter.stats.strength == 22
    assert fighter.max_hp == bare.max_hp + LIFE.hp


# ---------- витрина и карточка ----------


def test_the_counter_has_a_shelf_for_everything_you_drink():
    player = make_player()
    shop = build_shop(player)
    misc = shop["sections"][-1]

    assert misc["slot"] == "misc"
    assert misc["title"] == "Прочее"
    assert [row["code"] for row in misc["items"]] == [p.code for p in POTIONS]
    assert all(row["consumable"] for row in misc["items"])
    assert all(row["suits"] == [] for row in misc["items"])


def test_a_locked_potion_is_shown_but_marked():
    rookie = make_player(level=1)
    misc = build_shop(rookie)["sections"][-1]
    rows = {row["code"]: row for row in misc["items"]}

    assert rows["heal_small"]["unlocked"]
    assert not rows["boost_strength"]["unlocked"]
    assert misc["open"] == 1


def test_the_card_carries_the_bag_of_bottles_and_what_is_running():
    player = make_player()
    player.potions = {"heal_small": 3}
    player.effects = [
        ActiveEffect(code="boost_strength", until=now_ts() + 3600 + 47 * 60)
    ]

    card = build_card(player, TOKEN, viewer_id=player.user_id)

    assert [row["code"] for row in card["potions"]] == ["heal_small"]
    assert card["potions"][0]["owned"] == 3
    assert card["effects"][0]["title"] == STRENGTH.title
    assert card["effects"][0]["left_text"] == "1 ч 47 мин"


def test_a_stranger_sees_the_effects_but_not_the_bag():
    """Эффект уже сидит в характеристиках — прятать его нечестно."""
    player = make_player()
    player.potions = {"heal_small": 3}
    player.effects = [
        ActiveEffect(code="boost_strength", until=now_ts() + EFFECT_SECONDS)
    ]

    card = build_card(player, TOKEN, viewer_id=999)

    assert card["potions"] == []
    assert len(card["effects"]) == 1


# ---------- мини-апп ----------


async def test_the_mini_app_sells_a_potion_and_pours_it(client, db):
    player = make_player(user_id=42, credits=200)
    player.set_hp(10)
    await db.save_player(player)

    response = await client.post(
        "/api/buy", json={"code": "heal_small"}, headers=headers(42)
    )
    body = await response.json()
    assert response.status == 200
    assert body["bought"]["consumable"] is True
    assert [row["code"] for row in body["card"]["potions"]] == ["heal_small"]

    response = await client.post(
        "/api/use", json={"code": "heal_small"}, headers=headers(42)
    )
    body = await response.json()

    assert response.status == 200
    assert body["used"]["healed"] == HEAL.heal
    assert body["used"]["left"] == 0
    assert body["card"]["potions"] == []
    assert body["card"]["hp"]["current"] == 10 + HEAL.heal


async def test_the_mini_app_starts_the_effect_and_shows_the_clock(client, db):
    player = make_player(user_id=42, credits=500)
    await db.save_player(player)
    await client.post("/api/buy", json={"code": "boost_hp"}, headers=headers(42))

    response = await client.post(
        "/api/use", json={"code": "boost_hp"}, headers=headers(42)
    )
    body = await response.json()

    assert body["used"]["healed"] == 0
    assert body["used"]["seconds_left"] > EFFECT_SECONDS - 5
    assert [effect["code"] for effect in body["card"]["effects"]] == ["boost_hp"]
    assert body["card"]["effects"][0]["left_text"] == "2 ч"


async def test_the_mini_app_refuses_a_bottle_that_is_not_in_the_bag(client, db):
    player = make_player(user_id=42)
    await db.save_player(player)

    response = await client.post(
        "/api/use", json={"code": "heal_small"}, headers=headers(42)
    )

    assert response.status == 409
    assert "в рюкзаке нет" in (await response.json())["error"]


async def test_an_unknown_bottle_is_not_poured(client, db):
    player = make_player(user_id=42)
    await db.save_player(player)

    response = await client.post(
        "/api/use", json={"code": "moonshine"}, headers=headers(42)
    )

    assert response.status == 409
    assert "нет" in (await response.json())["error"]


async def test_a_reset_takes_the_bottles_and_the_buffs_with_it(db):
    """Новый персонаж начинает с пустыми руками — и с трезвой головой."""
    player = make_player(user_id=7)
    await db.save_player(player)
    await buy_potion(db, player, "heal_small")
    await db.set_effect(player.user_id, "boost_strength", now_ts() + EFFECT_SECONDS)

    await db.delete_player(player.user_id)

    assert await db.list_potions(player.user_id) == {}
    assert await db.list_effects(player.user_id) == []


def test_every_potion_is_drawn_under_its_own_code():
    """Файл склянки лежит в /potions под кодом эликсира — как образы и слоты."""
    from bot.game import art

    for potion in POTIONS:
        assert potion.picture == f"{art.POTIONS}/{potion.code}.png"
    assert len({potion.picture for potion in POTIONS}) == len(POTIONS)


def test_the_shop_row_carries_the_potion_picture():
    from bot.webapp.card import build_shop

    misc = build_shop(make_player())["sections"][-1]
    rows = {row["code"]: row for row in misc["items"]}

    assert rows["heal_small"]["image"] == get_potion("heal_small").picture
    assert all(row["image"] for row in misc["items"])
