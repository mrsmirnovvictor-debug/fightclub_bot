"""Миграция владения: живому бойцу вкладывают минимум под надетое.

Владение пришло в клуб, когда город уже дрался. Очки обоих пулов у
живых бойцов появились сами — они считаются от уровня, — но лежали бы
нераспределёнными, а оружие в руках их требует: первый же взгляд на
рюкзак говорил бы «нужно владение» про меч, которым боец дерётся третий
месяц.

Работа разовая и живёт среди таких же (`bot/chores.py`). Опасностей у
неё две, и обе проверяются здесь: раздеть бойца и повториться при
перезапуске.
"""

from __future__ import annotations

import logging

from bot import chores
from bot.game.equipment import Slot
from bot.game.health import now_ts
from bot.game.mastery import HoldType, WeaponFamily
from bot.mastery_service import needed_for, plan_for, settle_worn
from tests.test_robbery import make_player

SWORDS = WeaponFamily.SWORDS.value
KNIVES = WeaponFamily.KNIVES.value
HAMMERS = WeaponFamily.HAMMERS.value
OFFHAND = HoldType.OFFHAND.value
SHIELD = HoldType.SHIELD.value


async def armed(db, user_id: int, level: int, **hands: str):
    """Боец с вещами в руках: клетка — ключ, код вещи — значение."""
    player = make_player(user_id, f"Боец-{user_id}", level=level)
    await db.save_player(player)
    for cell, code in hands.items():
        owned = await db.add_gear(user_id, code)
        owned.slot = Slot(cell)
        await db.save_gear(owned)
    return player


# ---------- правила ----------


def test_the_cell_decides_whether_a_hold_is_needed():
    """Нож в основной руке требует вида, во второй — ещё и хвата."""
    assert needed_for([("knife", "weapon")]) == ({KNIVES: 4}, {})
    assert needed_for([("knife", "offhand")]) == ({KNIVES: 4}, {OFFHAND: 4})
    # Щит — хват без вида, где бы он ни лежал
    assert needed_for([("riot_shield", "offhand")]) == ({}, {SHIELD: 8})
    # Одежда и огнестрел владения не требуют
    assert needed_for([("club_tee", "body")]) == ({}, {})


def test_the_same_direction_asks_for_the_maximum_and_not_the_sum():
    """Два меча — это не два требования: очки лежат в направлении.

    Владение восьмого уровня держит и катану восьмого, и вакидзаси
    пониже; складывать пороги значило бы требовать за одно направление
    дважды.
    """
    families, _ = needed_for(
        [("katana", "weapon"), ("machete", "offhand")]
    )

    assert families == {SWORDS: 8}, "катана восьмого, мачете шестого"


def test_only_whole_thresholds_are_invested():
    """Половина порога не держит ничего, и очки в неё не кладут.

    Положенное уже не переложить: лучше оставить очко свободным, чем
    закопать его в направление, которое всё равно не открылось.
    """
    # На девятый порог девяти очков хватает, на второе направление нет
    assert plan_for(9, {}, {HAMMERS: 9, KNIVES: 4}) == {HAMMERS: 9}
    # Самое дорогое не влезло — меньшее за ним всё равно кладём
    assert plan_for(5, {}, {HAMMERS: 9, KNIVES: 4}) == {KNIVES: 4}
    # Вложенное зачитывается: доложить нужно только недостающее
    assert plan_for(9, {SWORDS: 6}, {SWORDS: 8}) == {SWORDS: 2}
    # И второй раз докладывать нечего
    assert plan_for(9, {SWORDS: 8}, {SWORDS: 8}) == {}


# ---------- работа по живой базе ----------


async def test_the_minimum_for_what_is_worn_is_invested(db):
    """Что надето, то и оплачено: остаток остаётся бойцу.

    Катана с ножом стоит двенадцати очков вида — восемь и четыре, — и
    бойцу двенадцатого уровня они впору ровно в край: пара разных видов
    и есть то самое распыление, за которое платят верхними ступенями.
    """
    await armed(db, 71, 12, weapon="katana", offhand="knife")

    done = await chores.run(db, now_ts())

    assert any("владение по надетому" in line for line in done), done
    saved = await db.get_player(71)
    assert saved.mastery == {SWORDS: 8, KNIVES: 4}
    assert saved.holds == {OFFHAND: 4}
    # Остаток считается от уровня: в видах не осталось ничего, в хватах
    # ушли четыре очка из двенадцати
    assert saved.mastery_free == 0 and saved.holds_free == 8


async def test_a_shield_asks_only_for_its_hold(db):
    """Щиту вид не назначают: он требует хвата и ничего больше."""
    await armed(db, 72, 10, weapon="katana", offhand="riot_shield")

    await chores.run(db, now_ts())

    saved = await db.get_player(72)
    assert saved.mastery == {SWORDS: 8}
    assert saved.holds == {SHIELD: 8}


async def test_the_migration_undresses_nobody(db):
    """Ни одна вещь не снята и не пропала: правила спрашивают при надевании."""
    await armed(db, 73, 10, weapon="katana", offhand="knife")

    await chores.run(db, now_ts())

    worn = {
        owned.slot: owned.code
        for owned in await db.list_gear(73)
        if owned.slot is not None
    }
    assert worn == {Slot.WEAPON: "katana", Slot.OFFHAND: "knife"}


async def test_a_second_run_invests_nothing_more(db):
    """Бот перезапускается часто, и второй заход ничего не добавляет."""
    await armed(db, 74, 10, weapon="katana")
    await chores.run(db, now_ts())
    before = await db.get_player(74)

    assert await chores.run(db, now_ts()) == []

    # И само правило идемпотентно, без отметки о работе: считает заново
    again, short = await settle_worn(db, before, [("katana", "weapon")])
    assert again == {} and short == []
    assert (await db.get_player(74)).mastery == {SWORDS: 8}


async def test_what_does_not_fit_stays_short_and_goes_to_the_log(db, caplog):
    """Очков может не хватить по-настоящему — и это случай для журнала.

    В пуле столько очков, сколько уровней, а надетого в руках бывает
    два: секира девятого уровня с ножом четвёртого — это тринадцать
    очков вида на девять имеющихся. Сборка остаётся на бойце, а очки он
    раскладывает заново к следующей смене оружия.
    """
    await armed(db, 75, 9, weapon="viking_axe", offhand="knife")

    with caplog.at_level(logging.WARNING, logger="bot.chores"):
        done = await chores.run(db, now_ts())

    saved = await db.get_player(75)
    # Самое дорогое закрыто, на второе направление очков не осталось
    assert saved.mastery == {HAMMERS: 9}
    assert saved.mastery_free == 0
    assert any("не хватило у 1" in line for line in done), done
    assert "не хватает очков владения" in caplog.text
    assert "Ножи и кастеты" in caplog.text
    # Нож при этом в руке и остался
    assert any(one.code == "knife" and one.slot for one in await db.list_gear(75))


async def test_a_townsman_is_left_alone(db):
    """Городским очков не вкладывают: их оружие приходит вместе с ними."""
    npc = make_player(-77, "Охрана", level=9)
    await db.save_player(npc)
    owned = await db.add_gear(-77, "katana")
    owned.slot = Slot.WEAPON
    await db.save_gear(owned)

    await chores.run(db, now_ts())

    assert (await db.get_player(-77)).mastery == {}


async def test_the_backpack_is_not_paid_for(db):
    """За лежащее в рюкзаке очков не вкладывают: владения требуют руки.

    База и отдаёт только руки: рюкзаки всего города ради владений
    перебирать незачем, и лишняя секира в мешке не должна становиться
    девятью очками, которых боец не просил.
    """
    await armed(db, 76, 10, weapon="katana")
    spare = await db.add_gear(76, "viking_axe")
    assert spare.slot is None

    assert await db.worn_in_hands() == {76: [("katana", "weapon")]}

    await chores.run(db, now_ts())

    assert (await db.get_player(76)).mastery == {SWORDS: 8}
