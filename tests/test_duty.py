"""Служебная форма: её выдают должностью и ею же забирают.

Договор тут один и простой: форма числится за креслом. Её нельзя
купить, нельзя продать, нельзя переслать или спрятать — и она уходит
вместе с должностью, в ту же секунду, без всяких напоминаний.
"""

from __future__ import annotations

import pytest

from bot.content.items import CATALOGUE
from bot.duty_service import (
    DutyError,
    collect_the_stray,
    hand_back,
    kit_for,
    rack_rows,
    take,
)
from bot.game.classes import Stats
from bot.game.gear import Slot
from tests.test_police import NOW, VCPD, cop, make_player

CHIEF = "police_chief"
KIT = tuple(one.code for one in kit_for(CHIEF))


async def patrolman(db, user_id: int = 81, nickname: str = "Патрульный"):
    """Сотрудник при значке, но не в кресле: `cop` сажает в начальники."""
    one = make_player(user_id, nickname, where=VCPD)
    one.job_code = "police_patrol"
    one.job_since = NOW
    await db.save_player(one)
    return await db.get_player(user_id)


async def chief(db, user_id: int = 90, nickname: str = "Ragnar"):
    """Боец в кресле начальника полиции."""
    one = make_player(user_id, nickname, where=VCPD)
    one.job_code = CHIEF
    one.job_since = NOW
    one.diplomas = ["police"]
    await db.save_player(one)
    return await db.get_player(user_id)


def test_the_kit_fills_every_slot_and_asks_for_nothing():
    """Тринадцать вещей — по одной на каждую клетку бойца.

    Ни уровня, ни характеристик под надевание: начальником становятся
    по диплому и стажу, а форма, которую носить некому, — не награда за
    должность, а насмешка.
    """
    kit = kit_for(CHIEF)
    assert len(kit) == 13
    assert all(one.level_required == 1 for one in kit)
    assert all(one.requires == Stats() for one in kit)
    assert all(not one.for_classes for one in kit), "кресло одно на все классы"
    assert all(one.is_duty and one.duty == CHIEF for one in kit)
    # Купить и продать нельзя: цены у формы нет вовсе
    assert all(not one.on_sale and not one.price for one in kit)
    # Слоты не повторяются: это комплект, а не склад одинаковых вещей
    assert len({one.slot for one in kit}) == 13
    # И закрывают они все клетки, включая три кольца
    assert {one.slot for one in kit} == set(Slot)


def test_the_kit_is_a_fifth_above_the_fan_counter():
    """Форма сильнее лучшего, что покупают за деньги, но не вдвое.

    Эталон — «воин» фанатского прилавка: десятый уровень, полторы
    тысячи за вещь. Держим зазор в 15–20 процентов: меньше — должность
    ничего не даёт, больше — кроме неё ничего не нужно.
    """
    pairs = (
        ("chief_baton", "fan_warrior_bat"),
        ("chief_tshirt", "fan_warrior_tee"),
        ("chief_jacket", "fan_warrior_harrington"),
        ("chief_pants", "fan_warrior_track_pants"),
        ("chief_boots", "fan_warrior_sneakers"),
        ("chief_helmet", "fan_warrior_cap"),
        ("chief_belt", "fan_warrior_belt"),
        ("chief_gloves", "fan_warrior_gloves"),
        ("chief_necklace", "fan_warrior_necklace"),
        ("chief_shield", "fan_boss_shield"),
    )
    for ours, theirs in pairs:
        mine, base = CATALOGUE[ours], CATALOGUE[theirs]
        for field in ("hp", "damage_min", "damage_max", "armor_min",
                      "armor_max", "strength", "agility", "intuition"):
            was = getattr(base, field)
            now = getattr(mine, field)
            assert now >= was, f"{ours}: {field} ниже фанатского"
            # Мелочь вроде двух очков ловкости округляется в ноль, и
            # требовать от неё роста значит требовать выдумки. Зазор
            # сверяем там, где числа крупные
            if was >= 10:
                assert now > was, f"{ours}: {field} не вырос"
                assert now <= round(was * 1.3), f"{ours}: {field} слишком высоко"


def test_the_kit_wears_its_own_pictures():
    """Картинки — те, что нарисовали: своя папка на службу и кресло."""
    for one in kit_for(CHIEF):
        assert "/items_new/police/chief/" in one.picture
        assert one.picture.endswith(".png")


async def test_the_chief_takes_the_kit_and_a_stranger_does_not(db):
    """Форму берёт тот, кто в кресле. Остальным — отказ."""
    boss = await chief(db)
    said = await take(db, boss, "chief_baton")
    assert "Длинная дубинка" in said
    assert [one.code for one in boss.gear] == ["chief_baton"]

    # Второй раз ту же вещь не выдают
    with pytest.raises(DutyError, match="уже на руках"):
        await take(db, boss, "chief_baton")

    patrol = await patrolman(db, 81, "Патрульный")
    with pytest.raises(DutyError, match="под должность"):
        await take(db, patrol, "chief_baton")

    plain = make_player(82, "Прохожий", where=VCPD)
    await db.save_player(plain)
    with pytest.raises(DutyError, match="под должность"):
        await take(db, await db.get_player(82), "chief_helmet")


async def test_the_rack_says_where_each_piece_is(db):
    """Стойка: вещь либо на складе, либо на руках, либо надета."""
    from bot.inventory_service import equip

    boss = await chief(db)
    rows = {one["code"]: one for one in rack_rows(boss, CHIEF)}
    assert len(rows) == 13
    assert all(one["where"] == "на складе" for one in rows.values())

    await take(db, boss, "chief_helmet")
    taken = {one["code"]: one for one in rack_rows(boss, CHIEF)}
    assert taken["chief_helmet"]["where"] == "на руках"

    await equip(db, boss, boss.gear[0].id, Slot.HEAD)
    worn = {one["code"]: one for one in rack_rows(boss, CHIEF)}
    assert worn["chief_helmet"]["where"] == "надета"
    assert worn["chief_helmet"]["worn"] is True


async def test_the_kit_goes_back_with_the_post(db):
    """Ушёл с должности — форма вернулась на склад сама.

    Снимать надетое при этом не просим: она уходит со слота вместе со
    строкой в базе. Держать форму на том, у кого должности больше нет,
    было бы страннее.
    """
    from bot.inventory_service import equip
    from bot.work_service import quit_job

    boss = await chief(db)
    await take(db, boss, "chief_helmet")
    await take(db, boss, "chief_baton")
    await equip(db, boss, boss.gear[0].id, Slot.HEAD)
    assert len(boss.gear) == 2

    await quit_job(db, boss, NOW)

    assert boss.job_code == ""
    assert await db.list_gear(boss.user_id) == []


async def test_the_stray_kit_is_collected_even_without_a_farewell(db):
    """Форма не по должности возвращается на склад при первом же взгляде."""
    boss = await chief(db)
    await take(db, boss, "chief_ring")
    # Кресло отняли мимо увольнения — так бывает при правках руками
    boss.job_code = ""
    await db.save_player(boss)

    assert await collect_the_stray(db, boss) == 1
    assert await db.list_gear(boss.user_id) == []
    assert await collect_the_stray(db, boss) == 0, "второй раз брать нечего"


async def test_the_kit_is_not_for_sale_and_not_for_the_post(db):
    """Служебную вещь не продают, не пересылают и не прячут."""
    from bot.inventory_service import InventoryError, hand_in
    from bot.market_service import MarketError, sell_lot
    from bot.post_service import PostError, send

    boss = await chief(db)
    await take(db, boss, "chief_shield")
    item_id = boss.gear[0].id

    with pytest.raises(MarketError, match="служебная форма"):
        await sell_lot(db, boss, item_id, 1_000)
    with pytest.raises(InventoryError, match="не принимает"):
        await hand_in(db, boss, item_id)

    mate = make_player(83, "Сосед", where=VCPD)
    await db.save_player(mate)
    boss.location = "post_office"
    with pytest.raises(PostError, match="служебная форма"):
        await send(db, boss, item_id, "Сосед", term="short", now=NOW)

    # Кладовая и склад семьи спрашивают то же самое, но до них надо
    # сперва снять квартиру и вступить в клан — а мафиози в полицию не
    # берут вовсе. Отказ там стоит на всякий случай, и проверять его
    # через две чужие двери значило бы проверять те двери

    # И после всех отказов вещь на месте
    assert [one.code for one in await db.list_gear(boss.user_id)] == ["chief_shield"]


async def test_the_full_kit_lifts_the_baton_above_the_ladder(db):
    """Собранная форма поднимает дубинку: четыре вещи, семь, девять, все.

    Это и есть награда за должность целиком, а не за одну снятую с
    полки вещь: тринадцать клеток на бойце, и двенадцать вещей набора —
    это он весь.
    """
    from bot.game.equipment import Equipment, OwnedItem
    from bot.game.sets import FULL_KIT, set_power

    assert FULL_KIT == 12, "клеток тринадцать, больше надеть некуда"
    assert set_power(3) == 1.0
    assert set_power(4) == 1.2
    assert set_power(9) == 1.5
    assert set_power(FULL_KIT) == 1.65

    kit = kit_for(CHIEF)
    worn = Equipment(
        items={
            one.slot: OwnedItem(item=one, id=number + 1, slot=one.slot)
            for number, one in enumerate(kit)
        }
    )
    assert worn.kit_worn("chief") == 13
    baton = worn.items[Slot.WEAPON]
    grown = worn.grown(baton)
    assert grown.damage_max > baton.item.damage_max
    assert grown.damage_max == round(baton.item.damage_max * 1.65)


async def test_the_office_store_takes_things_and_gives_them_back(db):
    """Склад кабинета — те же полки, что у семьи: положил и забрал."""
    from bot.duty_service import RACK_SLOTS, stash, store_rows, take_stored

    boss = await chief(db)
    owned = await db.add_gear(boss.user_id, "wraps")
    boss.gear = await db.list_gear(boss.user_id)

    said = await stash(db, boss, owned.id, NOW)
    assert "на складе кабинета" in said
    assert await db.list_gear(boss.user_id) == []
    rows = await store_rows(db, boss)
    assert len(rows) == 1 and rows[0]["title"] == "Бинты"
    assert rows[0]["by"] == "Ragnar", "видно, кто положил"

    back = await take_stored(db, boss, rows[0]["id"])
    assert "Бинты" in back
    assert [one.code for one in await db.list_gear(boss.user_id)] == ["wraps"]
    assert await store_rows(db, boss) == []

    # Полок пятнадцать, и шестнадцатую вещь класть некуда
    for number in range(RACK_SLOTS):
        extra = await db.add_gear(boss.user_id, "wraps")
        boss.gear = await db.list_gear(boss.user_id)
        await stash(db, boss, extra.id, NOW)
    last = await db.add_gear(boss.user_id, "wraps")
    boss.gear = await db.list_gear(boss.user_id)
    with pytest.raises(DutyError, match="все заняты"):
        await stash(db, boss, last.id, NOW)


async def test_the_office_store_belongs_to_the_post(db):
    """Склад кабинета — не общая полка: чужой до неё не дотянется."""
    from bot.duty_service import stash, store_rows, take_stored

    boss = await chief(db)
    owned = await db.add_gear(boss.user_id, "wraps")
    boss.gear = await db.list_gear(boss.user_id)
    await stash(db, boss, owned.id, NOW)
    stored = (await store_rows(db, boss))[0]

    patrol = await patrolman(db)
    his = await db.add_gear(patrol.user_id, "wraps")
    patrol.gear = await db.list_gear(patrol.user_id)
    with pytest.raises(DutyError, match="дело должности"):
        await stash(db, patrol, his.id, NOW)
    with pytest.raises(DutyError, match="дело должности"):
        await take_stored(db, patrol, stored["id"])
    assert await store_rows(db, patrol) == []

    # И форму на полку не кладут: она и так числится за кабинетом
    await take(db, boss, "chief_ring")
    ring = next(one for one in boss.gear if one.code == "chief_ring")
    with pytest.raises(DutyError, match="числится за кабинетом"):
        await stash(db, boss, ring.id, NOW)


async def test_the_office_opens_for_the_chief_alone(db):
    """Кабинет открывает должность, а не значок."""
    from bot.webapp.police import build_police_board

    boss = await chief(db)
    body = await build_police_board(db, boss, NOW)
    office = body["office"]
    assert office["post"] == "Начальник полиции"
    assert len(office["rack"]) == 13
    assert office["garage"]["slots"] == 3

    patrol = await patrolman(db, 84, "Патрульный")
    plain = await build_police_board(db, patrol, NOW)
    assert plain["office"] == {}, "дежурная часть — всем, кабинет — одному"
