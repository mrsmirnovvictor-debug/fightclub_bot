"""Отряд: звёздность, витрина найма, рост бойца и увольнение.

Проверяется то, на чём стоит вся вкладка: что звёзды платят уровнями, а
не силой на старте; что витрина держится неделей и не пересобирается
перезаходом; что боец не обгоняет хозяина, но и опыта не теряет; что
уволенный возвращает вещи.
"""

import random

import pytest

from bot.content.recruits import make_name, pick_face
from bot.game.art import RECRUIT_FACES, recruit_face
from bot.game.classes import Stats, get_class
from bot.game.squad import (
    HIRE_PRICE,
    REROLL_LIMIT,
    REROLL_PRICES,
    SHOWCASE_SIZE,
    SQUAD_LIMIT,
    STAR_CHANCE,
    bonus_points,
    npc_stats,
    reroll_price,
    rerolls_left,
    roll_stars,
)
from bot.game.work import week_of
from bot.models import Player
from bot.squad_service import (
    SquadError,
    add_exp,
    discharge,
    dismiss,
    fighters,
    hire,
    reroll,
    showcase,
    to_hospital,
)

WEEK = 1_800_000_000


def a_player(level: int = 5, credits: int = 5_000, user_id: int = 42) -> Player:
    return Player(
        user_id=user_id,
        nickname="Тайлер",
        class_code="warrior",
        level=level,
        credits=credits,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def enlist(db, player: Player | None = None) -> Player:
    player = player or a_player()
    await db.save_player(player)
    return player


# ---------- звёздность ----------


def test_the_star_odds_add_up_to_a_hundred():
    """Доли витрины — это доли, а не пожелания."""
    assert sum(STAR_CHANCE.values()) == 100
    assert sorted(STAR_CHANCE) == [1, 2, 3, 4, 5]
    # Чем реже боец, тем он дороже: иначе звёзды не значили бы ничего
    prices = [HIRE_PRICE[one] for one in sorted(HIRE_PRICE)]
    assert prices == sorted(prices)


def test_a_candidate_of_any_star_looks_the_same_at_the_counter():
    """На первом уровне 1⭐ и 5⭐ неразличимы, и это честно.

    Звёздность — не сила сегодня, а скорость роста. Если бы 5⭐ выходил
    сильнее уже на витрине, цена в 500 кредитов покупала бы готового
    бойца, а не будущее.
    """
    one = npc_stats(get_class("tank"), 1, 1)
    five = npc_stats(get_class("tank"), 5, 1)
    assert one == five
    assert bonus_points(5, 1) == 0, "создание бойца само по себе бонуса не даёт"


def test_stars_pay_off_in_levels_and_by_the_table():
    """Бонус приходит ровно на тех уровнях, что названы в постановке."""
    # 2⭐ — на третьем, шестом, девятом
    assert [bonus_points(2, one) for one in (1, 2, 3, 5, 6, 9)] == [0, 0, 1, 1, 2, 3]
    # 3⭐ — на каждом втором
    assert [bonus_points(3, one) for one in (1, 2, 3, 4, 8)] == [0, 1, 1, 2, 4]
    # 4⭐ — на каждом со второго, 5⭐ — по два
    assert [bonus_points(4, one) for one in (1, 2, 5)] == [0, 1, 4]
    assert [bonus_points(5, one) for one in (1, 2, 5)] == [0, 2, 8]
    # Обычный боец не получает ничего и остаётся меркой для остальных
    assert all(bonus_points(1, one) == 0 for one in range(1, 20))


def test_the_same_three_numbers_always_give_the_same_fighter():
    """Класс, звёздность и уровень — и больше ничего.

    Характеристики не хранятся в базе, а считаются: разойтись формуле и
    записи негде, потому что записи нет. Поэтому модель обязана быть
    чистой — ни броска, ни часа внутри.
    """
    first = npc_stats(get_class("assassin"), 4, 7)
    second = npc_stats(get_class("assassin"), 4, 7)
    assert first == second
    # И бонус ложится в тот же круг, которым класс качается сам
    plain = npc_stats(get_class("assassin"), 1, 7)
    assert sum(first.as_dict().values()) - sum(plain.as_dict().values()) == bonus_points(
        4, 7
    )


def test_the_rolled_star_is_always_one_of_the_five():
    """Бросок витрины не выдаёт звёздности, которой нет в таблице."""
    dice = random.Random(1)
    seen = {roll_stars(dice) for _ in range(500)}
    assert seen <= set(STAR_CHANCE)
    assert len(seen) == 5, "за пятьсот бросков должны встретиться все"


# ---------- пересмотры ----------


def test_a_reroll_costs_more_each_time_and_then_runs_out():
    """Цена растёт парами, а девятого пересмотра нет вовсе."""
    assert [reroll_price(one) for one in range(REROLL_LIMIT)] == list(REROLL_PRICES)
    assert reroll_price(REROLL_LIMIT) == 0, "больше восьми нельзя"
    assert rerolls_left(0) == REROLL_LIMIT and rerolls_left(REROLL_LIMIT) == 0
    # К восьмому разу пересмотр стоит дороже обычного бойца: первый —
    # мелочь, последний — решение
    assert REROLL_PRICES[0] < HIRE_PRICE[1] < REROLL_PRICES[-1]


# ---------- имена и лица ----------


def test_names_and_faces_do_not_repeat_where_they_are_seen_together():
    """Девять имён на экране — девять разных имён."""
    dice = random.Random(5)
    names: set[str] = set()
    faces: set[int] = set()
    for _ in range(SQUAD_LIMIT + SHOWCASE_SIZE):
        name = make_name(names, dice)
        face = pick_face(faces, dice)
        assert name not in names and face not in faces
        names.add(name)
        faces.add(face)
    assert all(1 <= one <= RECRUIT_FACES for one in faces)


def test_every_face_in_the_pool_has_an_address():
    """Тридцать два лица — тридцать два адреса в бакете."""
    seen = {recruit_face(one) for one in range(1, RECRUIT_FACES + 1)}
    assert len(seen) == RECRUIT_FACES
    assert recruit_face(7).endswith("/npc_recruits/npc_male_07.png")


# ---------- витрина ----------


async def test_the_showcase_holds_until_the_week_turns(db):
    """Перезаход не меняет кандидатов, а новая неделя меняет.

    Иначе витрина перестала бы быть ограничением: закрыл вкладку,
    открыл — и вот тебе новая тройка даром.
    """
    player = await enlist(db)
    first = await showcase(db, player, WEEK, random.Random(1))
    again = await showcase(db, player, WEEK + 3_600, random.Random(2))
    assert [one["name"] for one in first["cards"]] == [
        one["name"] for one in again["cards"]
    ]

    later = await showcase(db, player, WEEK + 8 * 24 * 3_600, random.Random(3))
    assert later["week"] != first["week"], "неделя должна смениться"
    assert [one["name"] for one in later["cards"]] != [
        one["name"] for one in first["cards"]
    ]


async def test_the_showcase_shows_three_first_level_candidates(db):
    """Трое, все первого уровня, у каждого своя цена по звёздам."""
    player = await enlist(db)
    shop = await showcase(db, player, WEEK, random.Random(4))
    assert len(shop["cards"]) == SHOWCASE_SIZE
    for card in shop["cards"]:
        assert card["price"] == HIRE_PRICE[card["stars"]]
        assert card["stars"] in STAR_CHANCE
    assert shop["rolls_left"] == REROLL_LIMIT
    assert shop["squad"] == 0 and shop["limit"] == SQUAD_LIMIT


async def test_a_reroll_takes_money_and_brings_other_people(db):
    """Заплатил — витрина другая, и следующий пересмотр дороже."""
    player = await enlist(db)
    before = await showcase(db, player, WEEK, random.Random(1))
    purse = player.credits

    after = await reroll(db, player, WEEK, random.Random(9))
    assert player.credits == purse - REROLL_PRICES[0]
    assert [one["name"] for one in after["cards"]] != [
        one["name"] for one in before["cards"]
    ]
    assert after["rolls_done"] == 1
    assert after["roll_price"] == REROLL_PRICES[1]


async def test_the_rerolls_run_out_for_the_week(db):
    """Восемь пересмотров — и до понедельника всё."""
    player = await enlist(db, a_player(credits=100_000))
    await showcase(db, player, WEEK, random.Random(1))
    for step in range(REROLL_LIMIT):
        state = await reroll(db, player, WEEK, random.Random(step))
    assert state["rolls_left"] == 0
    with pytest.raises(SquadError):
        await reroll(db, player, WEEK, random.Random(99))


async def test_a_poor_fighter_cannot_reroll(db):
    """Без денег пересмотра нет, и витрина остаётся прежней."""
    player = await enlist(db, a_player(credits=10))
    before = await showcase(db, player, WEEK, random.Random(1))
    with pytest.raises(SquadError):
        await reroll(db, player, WEEK, random.Random(2))
    after = await showcase(db, player, WEEK, random.Random(3))
    assert [one["name"] for one in after["cards"]] == [
        one["name"] for one in before["cards"]
    ]
    assert player.credits == 10, "за отказ денег не берут"


# ---------- найм ----------


async def test_hiring_takes_the_money_and_puts_the_man_in_the_squad(db):
    """Боец появляется в отряде, деньги уходят, кандидат гаснет."""
    player = await enlist(db)
    shop = await showcase(db, player, WEEK, random.Random(1))
    card = shop["cards"][0]
    purse = player.credits

    npc = await hire(db, player, card["slot"], WEEK)
    assert npc.nickname == card["name"] and npc.level == 1
    assert player.credits == purse - card["price"]

    held = await fighters(db, player.user_id)
    assert len(held) == 1
    row, mine = held[0]
    assert row["stars"] == card["stars"] and row["face"] == card["face"]
    assert mine.user_id < 0, "боец живёт отрицательным номером, как Тренер"
    assert mine.free_points == 0, "раздавать очки ему некому"


async def test_the_same_candidate_is_not_hired_twice(db):
    """Два нажатия подряд не дают двух бойцов по одной цене."""
    player = await enlist(db)
    shop = await showcase(db, player, WEEK, random.Random(1))
    slot = shop["cards"][0]["slot"]

    await hire(db, player, slot, WEEK)
    with pytest.raises(SquadError):
        await hire(db, player, slot, WEEK)
    assert len(await db.squad_of(player.user_id)) == 1


async def test_a_squad_never_grows_past_six(db):
    """Шесть мест — шесть бойцов, седьмого не берут."""
    player = await enlist(db, a_player(credits=100_000))
    for step in range(SQUAD_LIMIT):
        shop = await showcase(db, player, WEEK + step * 7 * 24 * 3_600, random.Random(step))
        await hire(db, player, shop["cards"][0]["slot"], WEEK + step * 7 * 24 * 3_600)
    assert len(await db.squad_of(player.user_id)) == SQUAD_LIMIT

    moment = WEEK + SQUAD_LIMIT * 7 * 24 * 3_600
    shop = await showcase(db, player, moment, random.Random(77))
    with pytest.raises(SquadError):
        await hire(db, player, shop["cards"][0]["slot"], moment)


async def test_a_poor_fighter_cannot_hire(db):
    """Денег нет — и кандидат остаётся на витрине непогашенным."""
    player = await enlist(db, a_player(credits=100))
    shop = await showcase(db, player, WEEK, random.Random(1))
    with pytest.raises(SquadError):
        await hire(db, player, shop["cards"][0]["slot"], WEEK)
    again = await showcase(db, player, WEEK, random.Random(1))
    assert not any(one["taken"] for one in again["cards"])


# ---------- рост ----------


async def test_a_fighter_never_outgrows_his_owner(db):
    """Потолок — уровень хозяина, и опыт сверх него не сгорает."""
    player = await enlist(db, a_player(level=3))
    shop = await showcase(db, player, WEEK, random.Random(1))
    npc = await hire(db, player, shop["cards"][0]["slot"], WEEK)
    row = (await db.squad_of(player.user_id))[0]

    await add_exp(db, npc, row["stars"], player, 1_000_000)
    assert npc.level == player.level, "дорос ровно до хозяина"
    banked = npc.exp
    assert banked > 0, "лишний опыт должен лежать, а не пропадать"

    # Хозяин подрос — и боец догоняет на том же опыте, без новых боёв
    player.level += 2
    levels = await add_exp(db, npc, row["stars"], player, 0)
    assert levels == 0, "без опыта уровень не даётся даже под потолком"
    levels = await add_exp(db, npc, row["stars"], player, 1)
    assert levels > 0 and npc.level > 3


async def test_growing_up_recounts_the_stats_by_the_stars(db):
    """После повышения характеристики пересчитаны, а не дописаны."""
    player = await enlist(db, a_player(level=6))
    shop = await showcase(db, player, WEEK, random.Random(2))
    npc = await hire(db, player, shop["cards"][0]["slot"], WEEK)
    row = (await db.squad_of(player.user_id))[0]

    await add_exp(db, npc, row["stars"], player, 1_000_000)
    wanted = npc_stats(get_class(npc.class_code), row["stars"], npc.level)
    assert npc.strength == wanted.strength
    assert npc.agility == wanted.agility
    assert npc.intuition == wanted.intuition
    assert npc.endurance == wanted.endurance


# ---------- больница и увольнение ----------


async def test_a_beaten_fighter_waits_in_hospital_until_he_is_taken(db):
    """Вынесли с точки — лежит; забрали — на ногах и здоров."""
    player = await enlist(db)
    shop = await showcase(db, player, WEEK, random.Random(1))
    npc = await hire(db, player, shop["cards"][0]["slot"], WEEK)
    await db.set_squad_spot(npc.user_id, "casino")

    await to_hospital(db, npc.user_id, WEEK + 100)
    row = await db.squad_row(npc.user_id)
    assert row["hospital_at"] == WEEK + 100
    assert row["spot"] == "", "лежачий точку не держит"

    npc.set_hp(1)
    await db.save_player(npc)
    healed = await discharge(db, player, npc.user_id)
    assert (await db.squad_row(npc.user_id))["hospital_at"] == 0
    assert healed.hp == healed.max_hp
    with pytest.raises(SquadError):
        await discharge(db, player, npc.user_id)


async def test_dismissal_hands_the_gear_back_and_takes_the_man_away(db):
    """Вещи хозяину, боец из города, место в отряде свободно."""
    player = await enlist(db)
    shop = await showcase(db, player, WEEK, random.Random(1))
    npc = await hire(db, player, shop["cards"][0]["slot"], WEEK)
    await db.add_gear(npc.user_id, "knuckles")

    await dismiss(db, player, npc.user_id)
    assert await db.squad_of(player.user_id) == []
    assert await db.get_player(npc.user_id) is None
    mine = [one.item.code for one in await db.list_gear(player.user_id)]
    assert mine == ["knuckles"], "за экипировку платил хозяин"


async def test_another_mans_fighter_is_not_yours_to_dismiss(db):
    """Чужого бойца не уволить и из больницы не забрать."""
    owner = await enlist(db)
    stranger = await enlist(db, a_player(user_id=43))
    shop = await showcase(db, owner, WEEK, random.Random(1))
    npc = await hire(db, owner, shop["cards"][0]["slot"], WEEK)

    with pytest.raises(SquadError):
        await dismiss(db, stranger, npc.user_id)
    with pytest.raises(SquadError):
        await discharge(db, stranger, npc.user_id)
    assert len(await db.squad_of(owner.user_id)) == 1


async def test_a_fighter_is_never_counted_as_a_live_clan_member(db):
    """Боец отряда не считается человеком в семье.

    Это не правило, которое мы написали, а то, как устроен город:
    в клан вступают записью в `clan_members`, а бойца туда никто не
    вписывает. Проверяем именно это — что найм не раздувает семью и не
    съедает её мест.
    """
    player = await enlist(db)
    shop = await showcase(db, player, WEEK, random.Random(1))
    npc = await hire(db, player, shop["cards"][0]["slot"], WEEK)

    rows = await db.conn.execute_fetchall(
        "SELECT COUNT(*) FROM clan_members WHERE user_id = ?", (npc.user_id,)
    )
    assert rows[0][0] == 0
    # И в списках города его тоже нет: там считают людей
    assert await db.count_players() == 1


def test_the_week_of_the_showcase_is_the_city_week():
    """Витрина обновляется тем же понедельником, что и жалованье."""
    assert week_of(WEEK) == week_of(WEEK + 3_600)
    assert week_of(WEEK) != week_of(WEEK + 8 * 24 * 3_600)
