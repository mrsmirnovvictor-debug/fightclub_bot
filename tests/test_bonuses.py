"""Недельный бонусный пулл: лестницы, счёт, часы и понедельник.

Порядок проверок — по тому, что дороже сломать:

1. **Лестница.** Ступень берётся по нижней границе, и ровно на границе
   она уже взята: десять нападений — это вторая ступень, а не первая.
2. **Счёт.** Сделанное пишут туда, где оно случилось: нападение, захват,
   освобождение, задержание.
3. **Часы.** Время владения копится само, по разнице, и делится по
   неделям, если между взглядами прошёл понедельник.
4. **Выдача.** Пулл выдают один раз: семье в сейф, городу по людям — и
   так, как велел начальник.
"""

from __future__ import annotations

import pytest

from bot.bonus_service import (
    accrue,
    note_arrest,
    note_raid,
    note_spot,
    settle,
    sweep,
)
from bot.database import Database
from bot.game.bonuses import (
    ARRESTS,
    FREED,
    GUARDED,
    HELD,
    MAFIA_BONUSES,
    POLICE_BONUSES,
    RAIDS,
    SEIZED,
    EVEN,
    BY_RANK,
    award_for,
    get_bonus,
    left_to_next,
    pool_for,
    shown,
    split_pool,
    step_of,
)
from bot.game.turf import owner_of_clan, owner_of_service
from bot.game.work import WEEK_SECONDS, payday_after, week_of
from bot.police_service import PoliceError, choose_split
from bot.webapp.bonus import build_bonus
from tests.test_police import cop
from tests.test_turf import NOW, family, stand

HOUR = 60 * 60
POLICE = owner_of_service("police")


# ---------- лестница ----------


@pytest.mark.parametrize(
    "value, step, award",
    [
        (0, 0, 0),
        (1, 1, 100),
        (9, 1, 100),
        # Ровно на границе ступень уже взята: иначе на десятом
        # нападении премия куда-то девалась бы
        (10, 2, 500),
        (19, 2, 500),
        (20, 3, 1000),
        (30, 4, 1500),
        (300, 4, 1500),
    ],
)
def test_the_ladder_counts_from_the_lower_bound(value, step, award):
    raids = get_bonus(RAIDS)
    assert step_of(raids, value) == step
    assert award_for(raids, value) == award


def test_the_hours_are_counted_in_seconds_and_shown_in_hours():
    """Время копим секундами, показываем часами.

    Складывать по часам значило бы терять по полчаса на каждом взгляде:
    обзор точек заглядывает часто.
    """
    held = get_bonus(HELD)
    assert shown(held, 150 * HOUR) == 150
    assert step_of(held, 150 * HOUR) == 1, "сто часов — первая ступень"
    assert step_of(held, 99 * HOUR + 3599) == 0, "неполный час не считается"
    assert left_to_next(held, 150 * HOUR) == 50
    assert left_to_next(held, 600 * HOUR) == 0, "выше некуда"


def test_three_deeds_add_up_to_one_pool():
    """Пулл — это сумма трёх дел, а не выбор лучшего из них."""
    tally = {RAIDS: 30, SEIZED: 112, HELD: 600 * HOUR}
    assert pool_for(MAFIA_BONUSES, tally) == 4500
    assert pool_for(MAFIA_BONUSES, {RAIDS: 5}) == 100
    assert pool_for(MAFIA_BONUSES, {}) == 0
    assert pool_for(POLICE_BONUSES, {ARRESTS: 15, FREED: 224,
                                     GUARDED: 300 * HOUR}) == 4500


def test_the_pool_splits_by_shares_and_keeps_the_change():
    """Делим вниз, остаток остаётся службе.

    Случайный получатель остатка был бы несправедливостью, которой
    никто не просил.
    """
    assert split_pool(900, [1, 1, 1]) == [300, 300, 300]
    assert split_pool(2000, [3, 2, 1]) == [1000, 666, 333]
    assert sum(split_pool(2000, [3, 2, 1])) == 1999, "один остался в казне"
    assert split_pool(100, []) == []
    assert split_pool(0, [1, 1]) == [0, 0]


async def _held(db, clan_id: int, since: int, upto: int) -> int:
    """Часы семьи за все недели, которых коснулся этот отрезок."""
    total = 0
    for one in {week_of(since), week_of(upto)}:
        total += (await db.bonus_week("clan", str(clan_id), one)).get(HELD, 0)
    return total


# ---------- счёт ----------


async def test_the_deeds_land_in_the_journal(db):
    """Нападение, захват, освобождение и задержание — по единице."""
    don, squad, clan = await family(db)
    mine = owner_of_clan(clan.id)
    week = week_of(NOW)

    await note_raid(db, clan.id, NOW)
    await note_raid(db, clan.id, NOW)
    await note_spot(db, mine, NOW)
    await note_arrest(db, "police", NOW)
    await note_spot(db, POLICE, NOW)

    assert await db.bonus_week("clan", str(clan.id), week) == {
        RAIDS: 2, SEIZED: 1,
    }
    assert await db.bonus_week("service", "police", week) == {
        ARRESTS: 1, FREED: 1,
    }


async def test_the_robbery_counts_the_raid_for_the_family(db):
    """Нападение считает та же развязка, что заводит дело."""
    from bot.robbery_service import Robbery, RobberyService

    don, squad, clan = await family(db)
    service = RobberyService(db)
    raid = Robbery(
        id=1, place="bar", place_title="Бар", kind="rob",
        attacker=don.user_id, victim=squad[1], fighters={}, sides={},
        names={}, clans={don.user_id: clan.id},
    )
    try:
        await service._count_for_clan(raid, clan.id, NOW)
    finally:
        await service.close()

    tally = await db.bonus_week("clan", str(clan.id), week_of(NOW))
    assert tally.get(RAIDS) == 1


async def test_the_arrest_counts_for_the_city(db):
    """Задержание считает тот же расчёт, что кладёт деньги в участок."""
    from bot.police_service import reward_arrest

    await cop(db, 80, "Ragnar")
    await reward_arrest(db, 80, 300, NOW)
    tally = await db.bonus_week("service", "police", week_of(NOW))
    assert tally.get(ARRESTS) == 1


async def test_freeing_a_house_counts_for_the_city(db):
    """Освобождение без боя — такое же освобождение, как с боем."""
    from bot.turf_service import release

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW - 3 * 24 * HOUR)
    officer = await cop(db, 81, "Ragnar")
    officer.location = "bar"
    await db.save_player(officer)

    await release(db, await db.get_player(81), "bar", now=NOW)
    tally = await db.bonus_week("service", "police", week_of(NOW))
    assert tally.get(FREED) == 1


# ---------- часы ----------


async def test_the_hours_grow_with_every_look_at_the_map(db):
    """Часы владения копятся сами: по разнице, а не по нажатию."""
    don, squad, clan = await family(db)
    week = week_of(NOW)
    await db.take_turf("bar", "clan", str(clan.id), week + HOUR)

    await accrue(db, week + 5 * HOUR)
    tally = await db.bonus_week("clan", str(clan.id), week)
    assert tally[HELD] == 4 * HOUR

    # Второй взгляд дописывает, а не пересчитывает заново
    await accrue(db, week + 9 * HOUR)
    tally = await db.bonus_week("clan", str(clan.id), week)
    assert tally[HELD] == 8 * HOUR

    # А между взглядами меньше минуты — и в базу не ходим вовсе
    await accrue(db, week + 9 * HOUR + 30)
    tally = await db.bonus_week("clan", str(clan.id), week)
    assert tally[HELD] == 8 * HOUR


async def test_the_hours_split_at_the_monday(db):
    """Между взглядами прошёл понедельник — часы делятся по неделям.

    Сложить их все в текущую значило бы заплатить за воскресенье по
    понедельничному счёту.
    """
    don, squad, clan = await family(db)
    week = week_of(NOW)
    await db.take_turf("bar", "clan", str(clan.id), payday_after(NOW) - 3 * HOUR)

    # Смотрят только через пять часов после понедельника
    await accrue(db, payday_after(NOW) + 5 * HOUR)
    assert (await db.bonus_week("clan", str(clan.id), week))[HELD] == 3 * HOUR
    fresh = await db.bonus_week("clan", str(clan.id), week + WEEK_SECONDS)
    assert fresh[HELD] == 5 * HOUR


async def test_the_new_owner_starts_the_clock_from_zero(db):
    """Часы прежнего хозяина не достаются новому."""
    from bot.turf_war import TurfWar

    from bot.game.health import now_ts

    don, squad, clan = await family(db)
    rival, rivals, theirs = await family(
        db, first=20, name="Псы", sign="silver_wolves"
    )
    # Часы тут настоящие: развязку штурма сводит служба, а она смотрит
    # на сегодняшний час, а не на `NOW`
    base = now_ts()
    await db.take_turf("bar", "clan", str(clan.id), base - 10 * HOUR)
    await accrue(db, base)

    await stand(db, rivals, "bar")
    war = TurfWar(db)
    try:
        muster = await war.open_muster(
            await db.get_player(rival.user_id), "bar", now=base
        )
        await war.march(muster.id, rival.user_id, now=base)
    finally:
        await war.close()

    await accrue(db, now_ts() + 2 * HOUR)
    ours = await _held(db, clan.id, base - 10 * HOUR, base)
    took = await _held(db, theirs.id, base, base + 2 * HOUR)
    assert 10 * HOUR <= ours < 10 * HOUR + 60, "свои часы остались при семье"
    assert 2 * HOUR <= took < 2 * HOUR + 60, "чужие ей не достались"
    assert (await db.bonus_week("clan", str(theirs.id),
                                week_of(base)))[SEIZED] == 1


# ---------- понедельник ----------


async def test_the_family_gets_its_pool_into_the_vault_once(db):
    """Пулл семьи уходит в сейф и ровно один раз."""
    don, squad, clan = await family(db)
    week = week_of(NOW)
    for _ in range(12):
        await note_raid(db, clan.id, NOW)
    await db.add_bonus("clan", str(clan.id), week, HELD, 250 * HOUR)
    before = (await db.clan_by_id(clan.id)).vault

    monday = payday_after(NOW) + 60
    await settle(db, monday)
    after = (await db.clan_by_id(clan.id)).vault
    assert after - before == 1000, "500 за нападения и 500 за удержание"
    assert (await db.bonus_payout("clan", str(clan.id), week))["amount"] == 1000

    # Второй взгляд в тот же понедельник ничего не добавляет
    await settle(db, monday + HOUR)
    assert (await db.clan_by_id(clan.id)).vault == after


async def test_the_city_pool_goes_to_the_officers(db):
    """Городской пулл расходится по людям — по слову начальника."""
    chief = await cop(db, 90, "Ragnar")
    officer = await cop(db, 91, "Офицер")
    one = await db.get_player(91)
    one.job_code = "police_officer"
    await db.save_player(one)
    patrol = await cop(db, 92, "Патрульный")
    one = await db.get_player(92)
    one.job_code = "police_patrol"
    await db.save_player(one)
    week = week_of(NOW)
    for _ in range(7):
        await note_arrest(db, "police", NOW)

    purse = {
        number: (await db.get_player(number)).credits for number in (90, 91, 92)
    }
    await settle(db, payday_after(NOW) + 60)
    after = {
        number: (await db.get_player(number)).credits for number in (90, 91, 92)
    }
    # Поровну: пулл пятьсот на троих
    assert [after[one] - purse[one] for one in (90, 91, 92)] == [166, 166, 166]
    assert await db.service_vault("police") == 2, "остаток остался службе"


async def test_the_chief_says_how_the_pool_is_split(db):
    """По должностям: начальнику три доли, офицеру две, патрульному одна."""
    chief = await cop(db, 93, "Ragnar")
    officer = await cop(db, 94, "Офицер")
    one = await db.get_player(94)
    one.job_code = "police_officer"
    await db.save_player(one)
    patrol = await cop(db, 95, "Патрульный")
    one = await db.get_player(95)
    one.job_code = "police_patrol"
    await db.save_player(one)

    said = await choose_split(db, await db.get_player(93), BY_RANK)
    assert "по должностям" in said
    assert await db.service_split("police") == BY_RANK

    week = week_of(NOW)
    await db.add_bonus("service", "police", week, ARRESTS, 15)
    purse = {
        number: (await db.get_player(number)).credits for number in (93, 94, 95)
    }
    await settle(db, payday_after(NOW) + 60)
    after = {
        number: (await db.get_player(number)).credits for number in (93, 94, 95)
    }
    assert [after[one] - purse[one] for one in (93, 94, 95)] == [750, 500, 250]


async def test_the_split_is_not_everyones_to_set(db):
    """Дележ премии — дело начальника, и слово у него одно из двух."""
    patrol = await cop(db, 96, "Патрульный")
    one = await db.get_player(96)
    one.job_code = "police_patrol"
    await db.save_player(one)
    with pytest.raises(PoliceError, match="дело начальника"):
        await choose_split(db, await db.get_player(96), BY_RANK)

    chief = await cop(db, 97, "Ragnar")
    with pytest.raises(PoliceError, match="не делят"):
        await choose_split(db, await db.get_player(97), "по-братски")
    assert await db.service_split("police") == EVEN, "осталось как было"


async def test_a_quiet_week_pays_nothing_but_still_closes(db):
    """Тихая неделя закрывается молча: ноль — тоже ответ."""
    don, squad, clan = await family(db)
    week = week_of(NOW)
    await db.add_bonus("clan", str(clan.id), week, RAIDS, 0)
    before = (await db.clan_by_id(clan.id)).vault

    await settle(db, payday_after(NOW) + 60)
    assert (await db.clan_by_id(clan.id)).vault == before
    assert (await db.bonus_payout("clan", str(clan.id), week))["amount"] == 0


async def test_the_pool_is_paid_on_top_of_the_turf_income(db):
    """Бонус ничего не отнимает у дохода с точек: это разные деньги."""
    from bot.hire_service import hire, post
    from bot.turf_service import survey

    don, squad, clan = await family(db)
    week = week_of(NOW)
    await db.take_turf("bar", "clan", str(clan.id), week)
    boss = await db.get_player(don.user_id)
    boss.credits = 20_000
    boss.location = "bar"
    await db.save_player(boss)
    await hire(db, await db.get_player(don.user_id), "merc_5", now=week)
    mine = (await db.hirelings_of(don.user_id))[0]
    await post(db, await db.get_player(don.user_id), int(mine["id"]), "bar",
               now=week)
    for _ in range(30):
        await note_raid(db, clan.id, NOW)

    monday = payday_after(NOW) + 60
    await survey(db, monday)
    vault = (await db.clan_by_id(clan.id)).vault
    # Доход точки за семь суток и пулл за нападения с удержанием:
    # неделя — это сто шестьдесят восемь часов, первая ступень
    assert vault == 7 * 90 + 1500 + 100


# ---------- экран ----------


async def test_the_board_shows_the_ladder_and_the_last_payout(db):
    """Экран называет ступень словом и помнит прошлый понедельник."""
    don, squad, clan = await family(db)
    mine = owner_of_clan(clan.id)
    week = week_of(NOW)
    for _ in range(12):
        await note_raid(db, clan.id, NOW)

    body = await build_bonus(db, mine, NOW)
    rows = {one["code"]: one for one in body["rows"]}
    assert [one["code"] for one in body["rows"]] == [RAIDS, SEIZED, HELD]
    assert rows[RAIDS]["done"] == 12 and rows[RAIDS]["step"] == 2
    assert rows[RAIDS]["award"] == 500 and rows[RAIDS]["left"] == 8
    assert rows[RAIDS]["need"] == 20 and rows[RAIDS]["next_award"] == 1000
    assert body["pool"] == 500 and body["top"] == 4500
    assert body["to_vault"] is True
    assert body["last"] is None, "первая неделя — платить ещё не за что"

    # Прошёл понедельник: пулл выдан, и экран о нём помнит
    monday = payday_after(NOW) + 60
    body = await build_bonus(db, mine, monday)
    assert body["rows"][0]["done"] == 0, "новая неделя считается с нуля"
    assert body["last"]["amount"] == 500


async def test_the_station_shows_the_split_to_everyone_and_the_switch_to_the_chief(
    db,
):
    """Счёт видит каждый сотрудник, а дележ меняет только кресло."""
    chief = await cop(db, 98, "Ragnar")
    patrol = await cop(db, 99, "Патрульный")
    one = await db.get_player(99)
    one.job_code = "police_patrol"
    await db.save_player(one)

    body = await build_bonus(db, POLICE, NOW, can_split=False)
    assert body["split"]["mode"] == EVEN
    assert body["split"]["can_set"] is False
    assert [one["code"] for one in body["split"]["options"]] == [EVEN, BY_RANK]
    assert body["to_vault"] is False, "город делит по людям, сейфа у него нет"

    body = await build_bonus(db, POLICE, NOW, can_split=True)
    assert body["split"]["can_set"] is True


async def test_a_loner_has_no_board_at_all(db):
    """Одиночке бонусов не положено: пулл зарабатывают сторонами."""
    from bot.game.turf import Owner

    assert await build_bonus(db, Owner(), NOW) == {}
