"""Травмы: за что дают, что отнимают, как лечат.

Травма — единственное в клубе, что уводит характеристику в минус, и
поэтому проверяется с двух сторон разом: и как правило боя, и как
состояние бойца, из-за которого слетает экипировка и удваивается дорога.
"""

import random
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import ASSASSIN, TANK, Stat, Stats, Zone, block_combo
from bot.game.combat import Action, Fighter, Outcome, resolve_round
from bot.game.equipment import Equipment, Slot
from bot.game.injuries import (
    ActiveInjury,
    HURT_ODDS,
    INJURIES,
    INJURY_CHANCE,
    Hurt,
    ZONE_STATS,
    injury_for,
    long_duration,
)
from bot.game.locations import travel_seconds
from bot.injury_service import InjuryError, heal_injury, hurt_player, worse
from bot.models import Player
from bot.webapp.card import build_card
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data


def make_player(location: str = "hospital", credits: int = 500, **stats) -> Player:
    numbers = {"strength": 30, "agility": 30, "intuition": 30, "endurance": 30}
    numbers.update(stats)
    return Player(
        user_id=42, nickname="Тайлер", class_code="warrior", level=8,
        credits=credits, location=location, **Stats(**numbers).as_dict(),
    )


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- справочник ----------


def test_nine_injuries_three_of_each_weight():
    """Девять травм: по три на тяжесть и по три на характеристику."""
    assert len(INJURIES) == 9
    for hurt in Hurt:
        mine = [one for one in INJURIES if one.hurt is hurt]
        assert len(mine) == 3
        assert {one.stat for one in mine} == {
            Stat.STRENGTH, Stat.AGILITY, Stat.INTUITION
        }


def test_the_weight_decides_the_price_the_time_and_the_loss():
    """Срок, потеря и цена лечения зависят только от тяжести."""
    assert [one.hours for one in Hurt] == [2, 6, 12]
    assert [one.penalty for one in Hurt] == [10, 15, 20]
    assert [one.price for one in Hurt] == [100, 200, 300]
    assert [one.cure_seconds // 60 for one in Hurt] == [10, 15, 20]
    assert round(sum(share for _, share in HURT_ODDS), 6) == 1.0
    # Числа названы прямо: статистический тест ниже сверяется с этой же
    # константой и сам по себе её подмену не заметит
    assert INJURY_CHANCE == 0.25
    assert dict(HURT_ODDS) == {Hurt.LIGHT: 0.60, Hurt.MEDIUM: 0.30, Hurt.HEAVY: 0.10}


def test_where_it_hurts_is_where_it_broke():
    """Куда ударили, то и сломалось: голова — интуиция, ноги — ловкость."""
    assert injury_for(Zone.HEAD, Hurt.HEAVY).title == "травма позвоночника"
    assert injury_for(Zone.CHEST, Hurt.HEAVY).title == "перелом руки"
    assert injury_for(Zone.LEGS, Hurt.LIGHT).title == "ушиб ноги"
    assert injury_for(Zone.BELT, Hurt.MEDIUM).title == "перелом копчика"
    # Живот отнесён к корпусу: рёбра и пресс — это про силу
    assert ZONE_STATS[Zone.BELLY] is Stat.STRENGTH
    # Зона есть у каждого удара, но бывает и пустая — тогда голова
    assert injury_for(None, Hurt.LIGHT).stat is Stat.INTUITION


def test_the_time_left_is_written_in_words():
    assert long_duration(36900) == "10 часов 15 минут"
    assert long_duration(7200) == "2 часа"
    assert long_duration(3660) == "1 час 1 минута"
    assert long_duration(120) == "2 минуты"
    assert long_duration(30) == "меньше минуты"


# ---------- за что дают ----------


def fight(hp: int, zone: Zone, seed: int, rng=None):
    """Один размен: ассасин бьёт в зону, у соперника здоровья на волосок."""
    attacker = Fighter(
        user_id=1, name="Бьющий", fclass=ASSASSIN, stats=ASSASSIN.base_stats,
        equipment=Equipment.from_codes({"weapon": "knuckles"}),
    )
    victim = Fighter(user_id=2, name="Падающий", fclass=TANK, stats=TANK.base_stats)
    victim.hp = hp
    result = resolve_round(
        attacker,
        Action(attacks=(zone,)),
        victim,
        # Блок держит живот и пояс — голова и ноги открыты
        Action(block=block_combo(Zone.BELLY, 2)),
        round_number=1,
        rng=rng or random.Random(seed),
    )
    return result, victim


def test_a_standing_fighter_is_never_injured():
    """Пока боец на ногах, травмы нет — сколько бы критов он ни поймал."""
    for seed in range(40):
        result, victim = fight(hp=400, zone=Zone.HEAD, seed=seed)
        assert victim.alive and not result.injuries


def test_only_a_finishing_crit_breaks_something():
    """Упал не от крита — встанет целым."""
    plain = 0
    for seed in range(200):
        result, victim = fight(hp=2, zone=Zone.HEAD, seed=seed)
        if victim.alive:
            continue
        last = [one for one in result.strikes if one.defender_id == 2 and one.damage]
        if last[-1].outcome not in (Outcome.CRIT, Outcome.BREAK):
            plain += 1
            assert not result.injuries, "травма без добивающего крита"
    assert plain, "обычных добиваний не нашлось — проверять было нечего"


def test_a_finishing_crit_hurts_about_a_quarter_of_the_time():
    """Добивающий крит калечит примерно в четверти случаев."""
    rng = random.Random(11)
    crits = hurt = 0
    for _ in range(4000):
        result, victim = fight(hp=2, zone=Zone.HEAD, seed=0, rng=rng)
        if victim.alive:
            continue
        last = [one for one in result.strikes if one.defender_id == 2 and one.damage]
        if last[-1].outcome in (Outcome.CRIT, Outcome.BREAK):
            crits += 1
            hurt += bool(result.injuries)
    assert crits > 30, "добивающих критов не набралось"
    share = hurt / crits
    assert abs(share - INJURY_CHANCE) < 0.12, share


def test_the_injury_lands_where_the_last_blow_did():
    """Добили в ноги — сломана нога, а не рука."""
    rng = random.Random(3)
    seen = set()
    for _ in range(4000):
        result, victim = fight(hp=2, zone=Zone.LEGS, seed=0, rng=rng)
        if not victim.alive and result.injuries:
            seen.add(result.injuries[2].stat)
    assert seen == {Stat.AGILITY}, seen


# ---------- что она делает с бойцом ----------


def test_an_injury_takes_the_stat_down_and_can_send_it_below_zero():
    now = int(time.time())
    player = make_player(agility=5)
    player.injury = ActiveInjury("broken_leg", now + 3600)

    assert player.stats.agility == -15
    assert player.worn_stats.agility == -15, "вещи мерятся той же меркой"
    assert player.crippled and not player.can_fight()
    assert player.limping


def test_a_light_injury_does_not_bench_a_strong_fighter():
    """Минус десять к силе у крепкого бойца — не приговор, а помеха."""
    player = make_player(strength=30)
    player.injury = ActiveInjury("shoulder", int(time.time()) + 3600)

    assert player.stats.strength == 20
    assert not player.crippled and player.can_fight()
    # Но по городу он всё равно ковыляет
    assert player.limping


def test_a_healed_injury_stops_counting():
    player = make_player()
    player.injury = ActiveInjury("spine", int(time.time()) - 1)

    assert player.injury_loss == Stats()
    assert not player.limping and not player.crippled


def test_the_road_takes_twice_as_long_with_an_injury():
    whole = travel_seconds("fight_club", "pharmacy")
    assert travel_seconds("fight_club", "pharmacy", limping=True) == whole * 2
    # Шаг на месте остаётся шагом на месте
    assert travel_seconds("fight_club", "fight_club", limping=True) == 0


# ---------- как записывается ----------


async def test_an_injury_reaches_the_database_and_drops_the_gear(db):
    """Сломанная рука не держит биту: вещь уходит в рюкзак."""
    player = make_player(strength=14)
    await db.save_player(player)
    owned = await db.add_gear(42, "pipe")  # требует 12 силы
    owned.slot = Slot.WEAPON
    await db.save_gear(owned)
    player.gear = [owned]

    active = await hurt_player(db, player, injury_for(Zone.CHEST, Hurt.LIGHT))

    assert active is not None and active.code == "shoulder"
    assert [one.title for one in player.dropped_gear] == ["Деревянная бита"]
    saved = await db.get_player(42)
    assert saved.injury is not None and saved.injury.code == "shoulder"
    assert saved.equipped == [], "вещь осталась надетой без нужной силы"


async def test_a_light_injury_does_not_cancel_a_heavy_one(db):
    """Фингал не лечит перелом руки: тяжёлая травма остаётся."""
    player = make_player()
    await db.save_player(player)
    heavy = await hurt_player(db, player, injury_for(Zone.CHEST, Hurt.HEAVY))

    again = await hurt_player(db, player, injury_for(Zone.HEAD, Hurt.LIGHT))

    assert again is None
    assert player.injury == heavy
    # А равная по тяжести ложится заново: время пошло сначала
    assert worse(injury_for(Zone.HEAD, Hurt.HEAVY), heavy, int(time.time()))


async def test_an_expired_injury_is_forgotten_by_the_base(db):
    player = make_player()
    await db.save_player(player)
    await db.set_injury(42, "spine", int(time.time()) - 1)

    assert await db.injury_of(42) is None
    assert (await db.get_player(42)).injury is None


# ---------- лечение ----------


async def test_the_hospital_turns_hours_into_minutes(db):
    player = make_player(credits=500)
    await db.save_player(player)
    await hurt_player(db, player, injury_for(Zone.CHEST, Hurt.HEAVY))

    healed = await heal_injury(db, player)

    assert player.credits == 500 - Hurt.HEAVY.price
    assert healed.seconds_left() <= Hurt.HEAVY.cure_seconds
    assert healed.seconds_left() > 0, "лечат не мгновенно"
    # И это доехало до базы
    saved = await db.get_player(42)
    assert saved.injury is not None
    assert saved.injury.seconds_left() <= Hurt.HEAVY.cure_seconds


async def test_a_whole_fighter_is_not_treated(db):
    player = make_player()
    await db.save_player(player)

    with pytest.raises(InjuryError, match="цел"):
        await heal_injury(db, player)

    assert player.credits == 500


async def test_without_credits_the_injury_stays(db):
    player = make_player(credits=10)
    await db.save_player(player)
    await hurt_player(db, player, injury_for(Zone.HEAD, Hurt.MEDIUM))

    with pytest.raises(InjuryError, match="хватает кредитов"):
        await heal_injury(db, player)

    assert player.credits == 10
    assert player.injury is not None
    assert player.injury.seconds_left() > Hurt.MEDIUM.cure_seconds


async def test_treating_an_injury_is_a_hospital_matter(client, db):
    """Лечиться от травмы можно только в больнице, как и от ран."""
    player = make_player(location="fight_club")
    await db.save_player(player)
    await hurt_player(db, player, injury_for(Zone.HEAD, Hurt.LIGHT))

    answer = await client.post("/api/injury", json={}, headers=headers())

    assert answer.status == 409
    assert (await db.get_player(42)).credits == 500


async def test_the_hospital_answers_with_the_price_of_the_injury(client, db):
    player = make_player()
    await db.save_player(player)
    await hurt_player(db, player, injury_for(Zone.LEGS, Hurt.MEDIUM))

    body = await (await client.get("/api/hospital", headers=headers())).json()

    assert body["injury"]["title"] == "перелом копчика"
    assert body["injury"]["price"] == Hurt.MEDIUM.price
    assert body["injury"]["cure_minutes"] == 15
    assert body["injury"]["affordable"] is True


async def test_healing_answers_with_a_fresh_card(client, db):
    player = make_player()
    await db.save_player(player)
    await hurt_player(db, player, injury_for(Zone.HEAD, Hurt.LIGHT))

    body = await (
        await client.post("/api/injury", json={}, headers=headers())
    ).json()

    assert body["done"]["title"] == "фингал под глазом"
    assert body["done"]["minutes"] <= 10
    assert body["card"]["record"]["credits"] == 500 - Hurt.LIGHT.price
    assert body["hospital"]["injury"]["code"] == "black_eye"


# ---------- карточка ----------


def test_the_card_tells_what_is_broken_and_for_how_long():
    player = make_player(agility=5)
    player.injury = ActiveInjury("broken_leg", int(time.time()) + 36900)

    card = build_card(player, "x" * 20, viewer_id=42)

    assert card["injury"]["text"] == (
        "Тяжёлая травма: перелом ноги. Ещё 10 часов 15 минут."
    )
    assert card["injury"]["crippled"] is True
    agility = next(row for row in card["stats"] if row["code"] == "agility")
    assert agility["loss"] == -20
    assert agility["total"] == -15, "итог обязан уходить в минус"
    # Целые характеристики о травме не знают
    strength = next(row for row in card["stats"] if row["code"] == "strength")
    assert strength["loss"] == 0


def test_a_whole_fighter_has_no_injury_in_the_card():
    card = build_card(make_player(), "x" * 20, viewer_id=42)
    assert card["injury"] == {}
    assert all(row["loss"] == 0 for row in card["stats"])
