"""Мастерская: заточки, модификаторы и то, что они делают с вещью.

Три правила проверяются строже прочих: модификатор ложится только на своё
(заточка оружия — на оружие), вещь модифицируется ровно один раз, а число
внутри полосы выпадает броском и остаётся с вещью — в бою работает именно
оно, а не то, что лежало на прилавке.
"""

import random

import pytest

from bot.content.mods import LEVELS, MODS, SHARES, SHARPEN, get_mod, star_of
from bot.game.equipment import Equipment, OwnedItem, Slot, get_item
from bot.game.gear import ModKind, modified
from bot.mods_service import ModError, apply_mod, buy_mod
from tests.test_inventory import make_player


# ---------- прилавок ----------


def test_the_counter_holds_three_kinds_and_five_steps():
    """Три вида товара, по пять ступеней, и у модификации — своя доля."""
    assert len(MODS) == 35  # 5 заточек оружия, 5 щита и 25 модификаторов

    for kind, count in ((ModKind.WEAPON, 5), (ModKind.SHIELD, 5), (ModKind.GEAR, 25)):
        rows = [mod for mod in MODS if mod.kind is kind]
        assert len(rows) == count, kind
        assert {mod.level for mod in rows} == {1, 2, 3, 4, 5}

    # одна доля на модификатор, и в магазине есть каждая
    shares = {mod.stat for mod in MODS if mod.kind is ModKind.GEAR}
    assert shares == {"accuracy", "dodge", "crit", "anticrit", "counter"}


@pytest.mark.parametrize("level,name,price", LEVELS)
def test_every_step_costs_what_it_should(level, name, price):
    """Цена зависит от ступени, а не от того, что точишь."""
    rows = [mod for mod in MODS if mod.level == level]
    assert {mod.price for mod in rows} == {price}
    assert all(name.lower()[:5] in mod.title.lower() for mod in rows), rows[0].title


def test_the_bands_are_the_ones_the_owner_set():
    """Полосы заточки и модификации — ровно те, что заказаны."""
    for level, (low, high) in SHARPEN.items():
        assert (get_mod(f"sharpen_weapon_{level}").low,
                get_mod(f"sharpen_weapon_{level}").high) == (low, high)
        assert (get_mod(f"sharpen_shield_{level}").low,
                get_mod(f"sharpen_shield_{level}").high) == (low, high)
    for level, (low, high) in SHARES.items():
        mod = get_mod(f"mod_dodge_{level}")
        assert (mod.low, mod.high) == (low, high)
        assert mod.span.endswith("%"), "доля пишется процентами"


def test_the_star_says_the_step():
    """Звёздочка на вещи — это её ступень: серая простая, красная элитная."""
    assert [star_of(level) for level in (1, 2, 3, 4, 5)] == ["⚪", "🟡", "🟠", "🟣", "🔴"]


# ---------- что модификатор делает с вещью ----------


def test_sharpening_lifts_both_edges_of_the_damage():
    """Заточка поднимает и минимальный урон, и максимальный."""
    bat = get_item("bat")
    sharp = get_mod("sharpen_weapon_3")

    better = modified(bat, sharp, 7)

    assert (better.damage_min, better.damage_max) == (
        bat.damage_min + 7, bat.damage_max + 7
    )
    # всё остальное осталось прежним: заточка не трогает ни доли, ни цену
    assert better.accuracy == bat.accuracy and better.price == bat.price


def test_a_shield_is_sharpened_into_armour():
    shield = get_item("riot_shield")
    sharp = get_mod("sharpen_shield_5")

    better = modified(shield, sharp, 18)

    assert (better.armor_min, better.armor_max) == (
        shield.armor_min + 18, shield.armor_max + 18
    )


def test_a_modifier_can_give_a_share_the_item_never_had():
    """Кроссовки начинают уворачиваться — в этом и смысл модификации."""
    boots = get_item("army_boots")
    assert boots.dodge == 0

    better = modified(boots, get_mod("mod_dodge_4"), 22)

    assert better.dodge == pytest.approx(0.22)


def test_the_roll_stays_inside_the_band():
    """Выпасть может любое число полосы — и только оно."""
    mod = get_mod("sharpen_weapon_1")
    rng = random.Random(7)

    rolls = {mod.roll(rng) for _ in range(200)}

    assert rolls == {1, 2, 3, 4, 5}, "полоса 1–5 выпадает целиком и без хвостов"


def test_the_modified_item_is_the_one_that_fights():
    """В бой идёт модифицированная вещь, а не та, что лежала на прилавке."""
    owned = OwnedItem(item=get_item("bat"), id=1, slot=Slot.WEAPON)
    equipment = Equipment(items={Slot.WEAPON: owned})
    before = equipment.weapon_damage

    owned.modify("sharpen_weapon_2", 5)

    assert equipment.weapon_damage == (before[0] + 5, before[1] + 5)
    assert owned.is_modified and owned.modifier.level == 2


def test_a_modifier_fits_only_its_own_kind():
    """Заточка оружия на кроссовки не ложится, и наоборот."""
    bat, boots = get_item("bat"), get_item("army_boots")
    shield = get_item("riot_shield")

    assert get_mod("sharpen_weapon_1").fits(bat)
    assert not get_mod("sharpen_weapon_1").fits(boots)
    assert get_mod("sharpen_shield_1").fits(shield)
    assert not get_mod("sharpen_shield_1").fits(bat)
    assert get_mod("mod_dodge_1").fits(boots)
    assert not get_mod("mod_dodge_1").fits(bat)


# ---------- что видно в карточке ----------


def test_the_card_says_how_much_of_the_number_the_master_gave():
    """Рядом с итогом стоит прибавка: «урон 12–16 (+5)».

    Числа в карточке уже посчитаны с модификацией, и без подписи не
    отличить заточенную биту от той, что такой и лежала на прилавке.
    """
    from bot.webapp.card import bonuses_payload

    bat = OwnedItem(item=get_item("bat"), id=1, slot=Slot.WEAPON)
    bat.modify("sharpen_weapon_2", 5)

    rows = {row["code"]: row for row in bonuses_payload(bat.real, None, bat)}

    assert rows["damage"]["text"] == bat.real.describe_damage()
    assert rows["damage"]["plus"] == "+5"
    # подписана ровно одна строка — та, которую поднимал мастер
    assert [code for code, row in rows.items() if row.get("plus")] == ["damage"]


def test_a_share_is_signed_in_percents():
    """Доля подписывается процентами: «уворот 22% (+22%)»."""
    from bot.webapp.card import bonuses_payload

    boots = OwnedItem(item=get_item("army_boots"), id=2, slot=Slot.BOOTS)
    boots.modify("mod_dodge_4", 22)

    rows = {row["code"]: row for row in bonuses_payload(boots.real, None, boots)}

    assert rows["dodge"]["text"] == "22%" and rows["dodge"]["plus"] == "+22%"


def test_a_shield_signs_its_armour():
    from bot.webapp.card import bonuses_payload

    shield = OwnedItem(item=get_item("riot_shield"), id=3, slot=Slot.OFFHAND)
    shield.modify("sharpen_shield_5", 18)

    rows = {row["code"]: row for row in bonuses_payload(shield.real, None, shield)}

    assert rows["armor"]["plus"] == "+18"


def test_an_untouched_thing_is_not_signed():
    """На витрине и на нетронутой вещи прибавки нет вовсе."""
    from bot.webapp.card import bonuses_payload

    bat = OwnedItem(item=get_item("bat"), id=4, slot=Slot.WEAPON)

    assert not any("plus" in row for row in bonuses_payload(bat.real, None, bat))
    assert not any("plus" in row for row in bonuses_payload(get_item("bat")))


# ---------- мастерская ----------


@pytest.fixture
async def fighter(db):
    player = make_player()
    player.credits = 10000
    await db.save_player(player)
    return player


async def test_a_modifier_is_bought_and_lands_in_the_bag(db, fighter):
    mod = await buy_mod(db, fighter, "sharpen_weapon_1")

    assert mod.price == 500
    assert fighter.credits == 9500
    assert await db.list_mods(fighter.user_id) == {"sharpen_weapon_1": 1}


async def test_an_empty_purse_buys_nothing(db, fighter):
    fighter.credits = 100

    with pytest.raises(ModError, match="Не хватает кредитов"):
        await buy_mod(db, fighter, "sharpen_weapon_5")

    assert fighter.credits == 100
    assert await db.list_mods(fighter.user_id) == {}


async def test_the_master_rolls_and_keeps_the_number(db, fighter):
    """Бросок делается у мастера, и число остаётся с вещью навсегда."""
    bat = await db.add_gear(fighter.user_id, "bat")
    fighter.gear.append(bat)
    await buy_mod(db, fighter, "sharpen_weapon_1")

    result = await apply_mod(db, fighter, bat.id, "sharpen_weapon_1", random.Random(3))

    assert 1 <= result.value <= 5
    assert result.star == "⚪" and "урон" in result.gain
    # модификатор ушёл из рюкзака, а число легло в базу
    assert await db.list_mods(fighter.user_id) == {}
    saved = next(one for one in await db.list_gear(fighter.user_id) if one.id == bat.id)
    assert saved.mod == "sharpen_weapon_1" and saved.mod_value == result.value
    assert saved.real.damage_min == get_item("bat").damage_min + result.value


async def test_a_thing_is_modified_only_once(db, fighter):
    """Вторая звёздочка на вещь не ставится — ни та же, ни другая."""
    bat = await db.add_gear(fighter.user_id, "bat")
    fighter.gear.append(bat)
    await buy_mod(db, fighter, "sharpen_weapon_1")
    await buy_mod(db, fighter, "sharpen_weapon_5")
    await apply_mod(db, fighter, bat.id, "sharpen_weapon_1")

    with pytest.raises(ModError, match="уже модифицирована"):
        await apply_mod(db, fighter, bat.id, "sharpen_weapon_5")

    # дорогой модификатор при этом остался в рюкзаке: за отказ не платят
    assert await db.list_mods(fighter.user_id) == {"sharpen_weapon_5": 1}


async def test_the_master_refuses_the_wrong_pair(db, fighter):
    boots = await db.add_gear(fighter.user_id, "army_boots")
    fighter.gear.append(boots)
    await buy_mod(db, fighter, "sharpen_weapon_1")

    with pytest.raises(ModError, match="не ложится"):
        await apply_mod(db, fighter, boots.id, "sharpen_weapon_1")

    assert await db.list_mods(fighter.user_id) == {"sharpen_weapon_1": 1}


async def test_without_the_modifier_nothing_happens(db, fighter):
    """Модификатора нет в рюкзаке — вещь остаётся нетронутой."""
    bat = await db.add_gear(fighter.user_id, "bat")
    fighter.gear.append(bat)

    with pytest.raises(ModError, match="нет в рюкзаке"):
        await apply_mod(db, fighter, bat.id, "sharpen_weapon_1")

    assert not bat.is_modified
