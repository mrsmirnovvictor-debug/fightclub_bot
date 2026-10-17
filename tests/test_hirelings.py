"""Нанятые: наёмники семьи, патрульные города и охрана точек.

Порядок проверок — по тому, что дороже сломать:

1. **Счёт на руках.** Три у рядового, четыре у старшего, пять у самого
   старшего — и ни одним больше.
2. **Деньги.** Платит человек из кармана, а не казна.
3. **Точка.** Десять мест на точке, один человек от нанимателя.
4. **Гибель.** Отбили точку — выставленные гибнут насовсем.
5. **Доход.** Город получает вдвое меньше семьи, а при перезахвате
   прежнему платят за фактическое время.
"""

from __future__ import annotations

import random

import pytest

from bot.database import Database
from bot.game.hirelings import (
    GUARD_LEVEL,
    GUARD_SIZE,
    HEAD_CAP,
    MAX_POSTED,
    MERC_GRADES,
    PATROL_GRADES,
    PLAIN_CAP,
    SENIOR_CAP,
    cap_for,
    get_grade,
    grades_for,
)
from bot.game.turf import (
    DAY_SECONDS,
    SERVICE_SHARE,
    final_payout,
    get_spot,
    owner_of_clan,
    owner_of_service,
    payout,
    share_of,
)
from bot.hire_service import HireError, guards, hire, post, roster, side_of
from bot.turf_service import garrison, hand_over, survey, vault_of
from tests.test_robbery import NOW, family, make_player, stand
from tests.test_police import cop

BAR = "bar"


async def rich(db: Database, user_id: int, credits: int = 20_000):
    one = await db.get_player(user_id)
    one.credits = credits
    await db.save_player(one)
    return await db.get_player(user_id)


# ---------- правила ----------


def test_the_grades_stand_where_the_rules_say():
    """Три ступени у каждой стороны: пятый, седьмой и девятый."""
    assert [one.level for one in MERC_GRADES] == [5, 7, 9]
    assert [one.price for one in MERC_GRADES] == [300, 600, 1_200]
    assert [one.fclass for one in MERC_GRADES] == ["rogue", "assassin", "tank"]

    assert [one.level for one in PATROL_GRADES] == [5, 7, 9]
    assert [one.price for one in PATROL_GRADES] == [300, 600, 1_200]
    assert [one.fclass for one in PATROL_GRADES] == ["tank", "rogue", "tank"]


def test_every_grade_has_a_face():
    """У каждой ступени своё лицо, и лежит оно в папке нанятых."""
    from bot.game.art import NPC_AVATARS
    from bot.game.hirelings import guard_picture

    faces = {one.code: one.picture for one in MERC_GRADES + PATROL_GRADES}
    assert len(set(faces.values())) == 6, "шесть ступеней — шесть лиц"
    for code, link in faces.items():
        assert link.startswith(NPC_AVATARS), code
        assert link.endswith(".png"), code

    # Имена файлов в бакете свои: наёмник там зовётся мафией
    assert faces["merc_9"].endswith("/mafia_9_tank.png")
    assert faces["patrol_7"].endswith("/police_7_trickster.png")
    assert guard_picture().endswith("/guard_4_tank.png")


def test_the_hired_carry_their_face_into_the_fight():
    """Лицо едет с бойцом: его рисуют в шкалах гарнизона."""
    from bot.game.hirelings import guard_picture

    one = guards()[0]
    assert one.picture == guard_picture()


def test_the_rank_says_how_many_are_held():
    """Три рядовому, четыре старшему, пять самому старшему."""
    assert cap_for("soldier") == PLAIN_CAP == 3
    assert cap_for("capo") == SENIOR_CAP == 4
    assert cap_for("don") == HEAD_CAP == 5
    # Лестница города считается той же меркой
    assert cap_for("police_patrol") == PLAIN_CAP
    assert cap_for("police_officer") == SENIOR_CAP
    assert cap_for("police_chief") == HEAD_CAP
    assert cap_for("") == PLAIN_CAP


def test_the_city_guards_are_three_tanks_of_the_fourth_level():
    """Охрана города: трое, четвёртый уровень, и с оружием из лавки."""
    wall = guards()
    assert len(wall) == GUARD_SIZE == 3
    assert all(one.level == GUARD_LEVEL == 4 for one in wall)
    assert all(one.fclass.code == "tank" for one in wall)
    assert all(one.equipment.items for one in wall), "голыми руками не выпускают"
    # Номера отрицательные: с бойцами отряда их не спутать
    assert all(one.user_id < 0 for one in wall)


def test_the_city_takes_half_of_what_a_family_takes():
    """Город держит точки не ради денег: доля у него половинная."""
    spot = get_spot("casino")
    assert share_of("clan") == 1.0
    assert share_of("service") == SERVICE_SHARE == 0.5
    assert payout(spot, 1, 1 + DAY_SECONDS)[0] == spot.income
    assert payout(spot, 1, 1 + DAY_SECONDS, 0.5)[0] == spot.income // 2


def test_the_leftover_hours_are_paid_at_the_handover():
    """Держал четырнадцать часов — получи за четырнадцать часов."""
    spot = get_spot("casino")
    assert final_payout(spot, 0, 0) == 0
    assert final_payout(spot, 1_000, 1_000) == 0
    assert final_payout(spot, 1_000, 1_000 + DAY_SECONDS) == spot.income
    half = final_payout(spot, 1_000, 1_000 + DAY_SECONDS // 2)
    assert half == spot.income // 2
    assert final_payout(spot, 1_000, 1_000 + DAY_SECONDS, 0.5) == spot.income // 2


# ---------- наём ----------


async def test_the_hired_come_out_of_a_pocket(db):
    """Платит человек, а не сейф: деньги списываются с нанимателя."""
    don, _, _ = await family(db)
    one = await rich(db, don.user_id, 5_000)

    done = await hire(db, one, "merc_5", now=NOW)
    assert done["grade"].price == 300
    assert (await db.get_player(don.user_id)).credits == 5_000 - 300
    assert len(await db.hirelings_of(don.user_id)) == 1

    poor = await rich(db, don.user_id, 100)
    with pytest.raises(HireError, match="Не хватает"):
        await hire(db, poor, "merc_9", now=NOW)


async def test_the_roster_is_capped_by_rank(db):
    """Дон держит пятерых, мафиози — троих."""
    don, squad, _ = await family(db)
    boss = await rich(db, don.user_id)
    for _ in range(HEAD_CAP):
        await hire(db, boss, "merc_5", now=NOW)
        boss = await db.get_player(don.user_id)
    with pytest.raises(HireError, match=f"Больше {HEAD_CAP}"):
        await hire(db, boss, "merc_5", now=NOW)

    soldier = await rich(db, squad[1])
    for _ in range(PLAIN_CAP):
        await hire(db, soldier, "merc_5", now=NOW)
        soldier = await db.get_player(squad[1])
    with pytest.raises(HireError, match=f"Больше {PLAIN_CAP}"):
        await hire(db, soldier, "merc_5", now=NOW)


async def test_each_side_hires_its_own(db):
    """Семья берёт наёмников, город — патрульных, одиночка — никого."""
    don, _, _ = await family(db)
    boss = await rich(db, don.user_id)
    assert side_of(boss) == "merc"
    with pytest.raises(HireError, match="к себе не берут"):
        await hire(db, boss, "patrol_5", now=NOW)

    officer = await cop(db, 90, "Сержант")
    officer.credits = 5_000
    await db.save_player(officer)
    officer = await db.get_player(90)
    assert side_of(officer) == "patrol"
    await hire(db, officer, "patrol_7", now=NOW)
    assert [one.code for one in grades_for("patrol")] == [
        "patrol_5", "patrol_7", "patrol_9"
    ]

    loner = make_player(99, "Один")
    await db.save_player(loner)
    with pytest.raises(HireError, match="сам по себе"):
        await hire(db, await db.get_player(99), "merc_5", now=NOW)


# ---------- точка ----------


async def test_the_spot_takes_ten_and_one_from_each(db):
    """Десять мест на точке и по одному от человека."""
    don, squad, clan = await family(db, size=12)
    await db.take_turf(BAR, "clan", str(clan.id), NOW)
    await stand(db, squad, BAR)

    for number in squad[:MAX_POSTED]:
        one = await rich(db, number)
        await hire(db, one, "merc_5", now=NOW)
        taken = (await db.hirelings_of(number))[0]
        await post(db, await db.get_player(number), int(taken["id"]), BAR, now=NOW)
    assert len(await db.hirelings_at(BAR)) == MAX_POSTED

    # Одиннадцатому места нет
    extra = await rich(db, squad[MAX_POSTED])
    await hire(db, extra, "merc_5", now=NOW)
    mine = (await db.hirelings_of(extra.user_id))[0]
    with pytest.raises(HireError, match=f"уже {MAX_POSTED}"):
        await post(db, await db.get_player(extra.user_id), int(mine["id"]),
                   BAR, now=NOW)


async def test_the_second_from_one_person_does_not_fit(db):
    """От тебя на точке один. Десять мест — это десять разных людей."""
    don, squad, clan = await family(db)
    await db.take_turf(BAR, "clan", str(clan.id), NOW)
    await stand(db, [don.user_id], BAR)
    boss = await rich(db, don.user_id)
    await hire(db, boss, "merc_5", now=NOW)
    await hire(db, await db.get_player(don.user_id), "merc_7", now=NOW)

    mine = await db.hirelings_of(don.user_id)
    await post(db, await db.get_player(don.user_id), int(mine[0]["id"]), BAR,
               now=NOW)
    with pytest.raises(HireError, match="уже стоит человек"):
        await post(db, await db.get_player(don.user_id), int(mine[1]["id"]),
                   BAR, now=NOW)


async def test_the_guard_is_posted_on_the_spot_and_on_your_own(db):
    """Ставят на месте и только на свою точку."""
    don, squad, clan = await family(db)
    boss = await rich(db, don.user_id)
    await hire(db, boss, "merc_5", now=NOW)
    mine = (await db.hirelings_of(don.user_id))[0]

    # Точка ещё не ваша
    await stand(db, [don.user_id], BAR)
    with pytest.raises(HireError, match="не ваша"):
        await post(db, await db.get_player(don.user_id), int(mine["id"]),
                   BAR, now=NOW)

    # Своя, но боец не на ней
    await db.take_turf(BAR, "clan", str(clan.id), NOW)
    await stand(db, [don.user_id], "casino")
    with pytest.raises(HireError, match="на месте"):
        await post(db, await db.get_player(don.user_id), int(mine["id"]),
                   BAR, now=NOW)


# ---------- гибель и перезахват ----------


async def test_the_lost_spot_loses_its_hired_for_good(db):
    """Отбили точку — выставленные на ней гибнут насовсем."""
    from bot.turf_war import TurfWar

    from bot.game.health import now_ts

    # Часы тут настоящие: бой идёт через службу, а она смотрит на
    # сегодняшний день, а не на `NOW`
    moment = now_ts()
    don, squad, clan = await family(db)
    await db.take_turf(BAR, "clan", str(clan.id), moment - 14 * 3600)
    # Драка была давно: иначе точка отдыхала бы и штурма не вышло
    await db.hold_turf(BAR, moment - 2 * DAY_SECONDS)
    await stand(db, [don.user_id], BAR)
    boss = await rich(db, don.user_id)
    await hire(db, boss, "merc_5", now=moment)
    mine = (await db.hirelings_of(don.user_id))[0]
    # Час тот же, настоящий: `post` сводит доход, и час из будущего
    # угнал бы расчёт точки вперёд на три месяца
    await post(db, await db.get_player(don.user_id), int(mine["id"]), BAR,
               now=moment)
    assert len(await db.hirelings_at(BAR)) == 1

    # Чужая семья берёт точку
    rival, rivals, other = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    await stand(db, rivals, BAR)
    war = TurfWar(db, rng=random.Random(2))
    try:
        muster = await war.open_muster(
            await db.get_player(rival.user_id), BAR, now=moment
        )
        for number in rivals[1:]:
            await war.join(muster.id, await db.get_player(number))
        siege = await war.march(muster.id, rival.user_id, now=moment)
        for _ in range(400):
            if siege.finished:
                break
            for number in list(siege.alive_ids):
                if siege.finished:
                    break
                await war.choose(siege.id, number, "attack", "head")
                if siege.finished:
                    break
                await war.choose(siege.id, number, "block", "belly")
    finally:
        await war.close()

    assert siege.won, "трое десятого уровня против одного наёмника"
    assert siege.fallen == 1
    assert await db.hirelings_at(BAR) == [], "наёмник погиб насовсем"
    assert await db.hirelings_of(don.user_id) == [], "и с рук его списали"
    # Прежним заплатили за фактическое время владения
    assert siege.handed > 0
    assert await vault_of(db, owner_of_clan(clan.id)) >= siege.handed


async def test_the_handover_pays_the_hours_actually_held(db):
    """Расчёт при смене владельца идёт за неполные сутки."""
    don, _, clan = await family(db)
    spot = get_spot(BAR)
    mine = owner_of_clan(clan.id)
    taken = NOW
    await db.take_turf(BAR, "clan", str(clan.id), taken)
    await survey(db, taken)

    row = await db.turf_row(BAR)
    half = taken + DAY_SECONDS // 2
    paid = await hand_over(db, spot, mine, row.paid_until, half)
    assert paid == spot.income // 2
    assert await vault_of(db, mine) == paid

    # Ничьей точке платить некому
    from bot.game.turf import Owner

    assert await hand_over(db, spot, Owner(), taken, half) == 0


async def test_the_city_keeps_half_of_the_income(db):
    """Точка под городом платит вдвое меньше, чем под семьёй."""
    spot = get_spot(BAR)
    await db.take_turf(BAR, "service", "police", NOW)
    await survey(db, NOW + DAY_SECONDS)
    assert await vault_of(db, owner_of_service("police")) == spot.income // 2


# ---------- экран ----------


async def test_the_roster_shows_the_counter_and_the_counter(db):
    """Экран найма: что на руках, сколько осталось и что предлагают."""
    don, _, clan = await family(db)
    boss = await rich(db, don.user_id)
    sheet = await roster(db, boss)
    assert sheet["side"] == "merc" and sheet["cap"] == HEAD_CAP
    assert sheet["hired"] == 0 and sheet["left"] == HEAD_CAP
    assert [one["code"] for one in sheet["grades"]] == [
        "merc_5", "merc_7", "merc_9"
    ]

    await hire(db, boss, "merc_9", now=NOW)
    sheet = await roster(db, await db.get_player(don.user_id))
    assert sheet["hired"] == 1 and sheet["left"] == HEAD_CAP - 1
    assert sheet["mine"][0]["title"] == get_grade("merc_9").title
    assert sheet["mine"][0]["spot"] == "", "в резерве, пока не выставлен"
