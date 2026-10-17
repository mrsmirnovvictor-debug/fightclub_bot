"""Полиция: дежурная часть, вмешательство, задержание и тюрьма.

Порядок проверок — по тому, что дороже сломать:

1. **Срок.** Три часа, шесть, двенадцать, сутки — и из тюрьмы не выйти,
   пока он идёт.
2. **Ордер.** Берётся из заявления жертвы, а не из кассы; без него
   задерживать некого.
3. **Деньги.** Награбленное возвращается в участок, а оттуда — жертве,
   и ровно один раз.
4. **Вмешательство.** Полицейский встаёт за того, на кого напали, и
   своих денег в драке не теряет.
5. **Норма.** Половина нападений за неделю, премия и дележ копилки.
"""

from __future__ import annotations

import random

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.game.battle import BLUE, RED
from bot.game.police import (
    ARREST_FEE,
    JAIL_TERMS,
    PRISON,
    QUOTA_BONUS,
    jail_hours,
    jail_left,
    jail_seconds,
    quota_done,
    quota_target,
    shares,
)
from bot.game.work import week_of
from bot.police_service import (
    PoliceError,
    collect,
    file_report,
    has_warrant,
    jail,
    officers,
    serves_in_police,
    settle_week,
    term_of,
)
from bot.robbery_service import RobberyError, RobberyService
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data
from tests.test_robbery import (
    NOW,
    family,
    fight_out,
    licensed,
    make_player,
    stand,
)

VCPD = "vcpd"
BAR = "bar"


def headers(user_id: int = 1) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


async def cop(
    db: Database, user_id: int = 80, nickname: str = "Патрульный",
    where: str = VCPD,
) -> object:
    """Боец при значке: занял кресло в участке — значит, полицейский."""
    from bot.game.services import get_service

    one = make_player(user_id, nickname, where=where)
    one.job_code = get_service("police").jobs[0]
    one.job_since = NOW
    await db.save_player(one)
    return await db.get_player(user_id)


async def weaken(db: Database, user_id: int) -> object:
    """Сделать бойца заведомо слабее: исход драки не должен быть броском.

    Проверкам про развязку нужен предсказуемый победитель, а не
    честный бой: про честный есть свои тесты в движке.
    """
    one = await db.get_player(user_id)
    assert one is not None
    one.level = 1
    one.strength = one.agility = one.intuition = one.endurance = 1
    one.free_points = 0
    await db.save_player(one)
    return await db.get_player(user_id)


async def a_case(
    db: Database, victim_id: int, suspect_id: int, loot: int = 200,
    place: str = BAR, filed: bool = False,
) -> int:
    """Готовое дело в дежурной части. `filed` — с заявлением."""
    case_id = await db.open_case(victim_id, suspect_id, place, loot, 1, NOW)
    if filed:
        assert await db.file_case(case_id, victim_id, NOW)
    return case_id


# ---------- правила ----------


def test_the_term_grows_with_every_sitting():
    """Три часа, шесть, двенадцать, сутки — и дальше те же сутки."""
    assert JAIL_TERMS == (3, 6, 12, 24)
    assert jail_hours(1) == 3
    assert jail_hours(2) == 6
    assert jail_hours(3) == 12
    assert jail_hours(4) == 24
    assert jail_hours(9) == 24, "пятый и дальше сидят те же сутки"
    assert jail_hours(0) == 0
    assert jail_seconds(2) == 6 * 3600


def test_the_term_counts_down_to_nothing():
    assert jail_left(NOW + 100, NOW) == 100
    assert jail_left(NOW, NOW) == 0
    assert jail_left(NOW - 100, NOW) == 0
    assert jail_left(0, NOW) == 0


def test_the_quota_is_half_the_attacks_rounded_up():
    """Норма — половина нападений, и половина задержания не считается."""
    assert quota_target(0) == 0
    assert quota_target(1) == 1, "одно нападение — одно задержание"
    assert quota_target(2) == 1
    assert quota_target(3) == 2
    assert quota_target(25) == 13
    assert quota_done(0, 0) is True, "тихая неделя — норма выполнена"
    assert quota_done(12, 25) is False
    assert quota_done(13, 25) is True


def test_the_pot_is_split_evenly_and_the_remainder_stays():
    """Делим нацело: дробных кредитов в клубе нет нигде."""
    assert shares(900, 3) == [300, 300, 300]
    assert shares(1000, 3) == [333, 333, 333]
    assert shares(2, 3) == [], "на троих по нулю — значит, не делим вовсе"
    assert shares(0, 3) == []
    assert shares(500, 0) == []


# ---------- тюрьма ----------


async def test_the_jailed_move_to_the_prison_and_stay(db):
    """Посаженный переезжает в тюрьму, и срок растёт от раза к разу."""
    one = make_player(50, "Мафиози", where=BAR)
    await db.save_player(one)

    until = await jail(db, one.user_id, NOW)
    assert until == NOW + jail_seconds(1)
    fresh = await db.get_player(one.user_id)
    assert fresh.location == PRISON and not fresh.travel_to
    term = await term_of(db, one.user_id, NOW)
    assert term["times"] == 1 and term["left"] == jail_seconds(1)

    # Второй раз — вдвое дольше
    again = await jail(db, one.user_id, NOW + 100_000)
    assert again == NOW + 100_000 + jail_seconds(2)
    assert (await term_of(db, one.user_id, NOW))["times"] == 2


async def test_the_road_out_of_prison_is_shut(db, client):
    """Из тюрьмы не выходят, пока срок идёт."""
    one = make_player(50, "Мафиози", where=BAR)
    await db.save_player(one)
    from bot.game.health import now_ts

    moment = now_ts()
    await jail(db, one.user_id, moment)

    answer = await client.post(
        "/api/travel", json={"to": BAR}, headers=headers(one.user_id)
    )
    assert answer.status == 409
    assert "тюрьмы не выходят" in (await answer.json())["error"]

    # Срок вышел — дорога открылась
    await db.put_in_jail(one.user_id, moment - 10, 1)
    answer = await client.post(
        "/api/travel", json={"to": BAR}, headers=headers(one.user_id)
    )
    assert answer.status == 200


# ---------- заявление и ордер ----------


async def test_the_report_makes_the_warrant(db):
    """Заявление жертвы — и задержать его может любой сотрудник."""
    victim = make_player(50, "Прохожий", where=VCPD)
    await db.save_player(victim)
    case_id = await a_case(db, victim.user_id, 1)
    assert await has_warrant(db, 1) is False

    said = await file_report(db, victim, case_id, now=NOW)
    assert "Ордер" in said
    assert await has_warrant(db, 1) is True

    # Второй раз то же заявление не принимают
    with pytest.raises(PoliceError, match="уже не принять"):
        await file_report(db, victim, case_id, now=NOW)


async def test_the_report_is_written_in_the_station_by_the_victim(db):
    """Заявление пишут в участке и только по своему делу."""
    victim = make_player(50, "Прохожий", where=BAR)
    await db.save_player(victim)
    case_id = await a_case(db, victim.user_id, 1)
    with pytest.raises(PoliceError, match="в участке"):
        await file_report(db, victim, case_id, now=NOW)

    stranger = make_player(51, "Посторонний", where=VCPD)
    await db.save_player(stranger)
    with pytest.raises(PoliceError, match="уже не принять"):
        await file_report(db, stranger, case_id, now=NOW)


# ---------- задержание ----------


async def test_the_arrest_needs_a_warrant_and_a_badge(db):
    """Задерживает полиция и только по ордеру."""
    service = RobberyService(db)
    don, squad, _ = await family(db)
    await stand(db, squad, VCPD)
    don = await db.get_player(don.user_id)
    officer = await cop(db)
    suspect = make_player(50, "Мафиози", where=VCPD)
    await db.save_player(suspect)

    with pytest.raises(RobberyError, match="дело полиции"):
        await service.arrest(don, suspect.user_id, now=NOW)
    with pytest.raises(RobberyError, match="нет ордера"):
        await service.arrest(officer, suspect.user_id, now=NOW)

    await a_case(db, 99, suspect.user_id, filed=True)
    raid = await service.arrest(officer, suspect.user_id, now=NOW)
    assert raid.kind == "arrest"
    assert raid.sides[officer.user_id] == RED
    assert raid.sides[suspect.user_id] == BLUE
    assert officer.user_id in raid.badges
    await service.close()


async def test_the_arrest_returns_the_loot_and_jails_the_mafia(db):
    """Взял — награбленное в участок, мафиози в тюрьму."""
    service = RobberyService(db, rng=random.Random(5))
    officer = await cop(db)
    don, squad, clan = await family(db)
    await stand(db, squad, VCPD)
    don = await weaken(db, don.user_id)
    victim_id = 99
    await a_case(db, victim_id, don.user_id, loot=300, filed=True)
    await a_case(db, victim_id, don.user_id, loot=200, filed=True)

    raid = await service.arrest(officer, don.user_id, now=NOW)
    purse = officer.credits
    await fight_out(service, raid)

    assert raid.won, "первого уровня против десятого не выстоять"
    assert raid.recovered == 500, "оба дела закрыты, и деньги в участке"
    assert await db.service_vault("police") == ARREST_FEE + 500
    term = await term_of(db, don.user_id)
    assert term["times"] == 1 and term["left"] > 0
    assert (await db.get_player(don.user_id)).location == PRISON
    assert await has_warrant(db, don.user_id) is False, "ордер закрыт"
    # Задержание засчитано в недельный журнал службы
    from bot.game.health import now_ts

    arrests, _ = await db.service_week("police", week_of(now_ts()))
    assert arrests == 1
    # Деньги сотрудника не трогают: у него и брать нечего
    assert (await db.get_player(officer.user_id)).credits == purse
    await service.close()


# ---------- вмешательство ----------


async def test_the_officer_steps_in_for_the_victim(db):
    """Полицейский встаёт за того, на кого напали, и без лицензии."""
    service = RobberyService(db)
    don, squad, _ = await family(db)
    await stand(db, squad, BAR)
    victim = make_player(50, "Прохожий", where=BAR)
    await db.save_player(victim)
    await licensed(db, [don.user_id], 1)
    officer = await cop(db, where=BAR)

    raid = await service.rob(await db.get_player(don.user_id), victim.user_id,
                             now=NOW)
    await service.step_in(raid.id, officer, now=NOW)
    assert raid.sides[officer.user_id] == BLUE, "за жертву"
    assert officer.user_id in raid.badges
    # Лицензия не тронута: защищать — это работа
    from bot.robbery_service import RobberyService as _R  # noqa: F401
    assert (await service.licences(officer.user_id, NOW))["left"] == 0
    await service.close()


async def test_a_civilian_does_not_step_in_with_a_badge(db):
    """Без значка так не вмешиваются: это полицейская дорога."""
    service = RobberyService(db)
    don, squad, _ = await family(db)
    await stand(db, squad, BAR)
    victim = make_player(50, "Прохожий", where=BAR)
    await db.save_player(victim)
    await licensed(db, [don.user_id], 1)

    raid = await service.rob(await db.get_player(don.user_id), victim.user_id,
                             now=NOW)
    mate = await db.get_player(squad[1])
    with pytest.raises(RobberyError, match="не при значке"):
        await service.step_in(raid.id, mate, now=NOW)
    await service.close()


async def test_the_beaten_robber_goes_to_jail(db):
    """Отбились при полицейском — налётчики едут в тюрьму."""
    service = RobberyService(db, rng=random.Random(11))
    don, squad, _ = await family(db)
    await stand(db, squad, BAR)
    don = await weaken(db, don.user_id)
    victim = make_player(50, "Прохожий", where=BAR)
    await db.save_player(victim)
    await licensed(db, [don.user_id], 1)
    officer = await cop(db, where=BAR)

    raid = await service.rob(await db.get_player(don.user_id), victim.user_id,
                             now=NOW)
    await service.step_in(raid.id, officer, now=NOW)
    await fight_out(service, raid)

    assert not raid.won, "первого уровня против двоих десятого не выстоять"
    assert raid.jailed and raid.jailed[0]["user_id"] == don.user_id
    assert raid.jailed[0]["hours"] == 3, "первая отсидка — три часа"
    assert (await db.get_player(don.user_id)).location == PRISON
    # Патрульный своих денег не теряет: берут только из кармана жертвы
    assert (await db.get_player(officer.user_id)).credits == officer.credits
    await service.close()


# ---------- деньги жертвы ----------


async def test_the_victim_collects_from_the_station(db):
    """Жертва приходит в участок и забирает возвращённое — один раз."""
    victim = make_player(50, "Прохожий", where=VCPD, credits=100)
    await db.save_player(victim)
    case_id = await a_case(db, victim.user_id, 1, loot=400, filed=True)
    await db.close_cases(1, 80, NOW)
    await db.add_to_service_vault("police", 400)

    said = await collect(db, victim, now=NOW)
    assert "400" in said
    assert (await db.get_player(victim.user_id)).credits == 500
    assert await db.service_vault("police") == 0

    with pytest.raises(PoliceError, match="Забирать нечего"):
        await collect(db, await db.get_player(victim.user_id), now=NOW)


async def test_the_money_waits_for_the_arrest(db):
    """Пока мафиози не взяли, забирать нечего."""
    victim = make_player(50, "Прохожий", where=VCPD)
    await db.save_player(victim)
    await a_case(db, victim.user_id, 1, loot=400, filed=True)
    with pytest.raises(PoliceError, match="Забирать нечего"):
        await collect(db, victim, now=NOW)


# ---------- норма и копилка ----------


async def test_the_week_is_settled_and_the_pot_is_shared(db):
    """Неделя кончилась: норма посчитана, премия добавлена, копилка роздана."""
    first = await cop(db, 80, "Первый")
    second = await cop(db, 81, "Второй")
    assert sorted(await officers(db)) == [80, 81]

    from bot.game.health import now_ts

    moment = now_ts()
    past = week_of(moment) - 7 * 24 * 3600
    # На прошлой неделе: два нападения и два задержания — норма с запасом
    _, _, clan = await family(db)
    await db.add_clan_attack(clan.id, past)
    await db.add_clan_attack(clan.id, past)
    await db.add_service_arrest("police", past)
    await db.add_service_arrest("police", past)
    await db.add_to_service_vault("police", 1_000)

    done = await settle_week(db, moment)
    assert done["arrests"] == 2 and done["attacks"] == 2
    assert done["bonus"] == QUOTA_BONUS
    assert done["pot"] == 1_000 + QUOTA_BONUS
    assert done["each"] == (1_000 + QUOTA_BONUS) // 2
    assert (await db.get_player(80)).credits == first.credits + done["each"]
    assert (await db.get_player(81)).credits == second.credits + done["each"]
    assert await db.service_vault("police") == 0

    # Второй заход ничего не повторяет
    again = await settle_week(db, moment)
    assert again["pot"] == 0


async def test_an_unmet_quota_pays_no_bonus(db):
    """Норму не сделали — премии нет, но копилка всё равно делится."""
    await cop(db, 80, "Первый")
    from bot.game.health import now_ts

    moment = now_ts()
    past = week_of(moment) - 7 * 24 * 3600
    _, _, clan = await family(db)
    for _ in range(10):
        await db.add_clan_attack(clan.id, past)
    await db.add_service_arrest("police", past)
    await db.add_to_service_vault("police", 700)

    done = await settle_week(db, moment)
    assert done["attacks"] == 10 and done["arrests"] == 1
    assert done["bonus"] == 0
    assert done["pot"] == 700 and done["each"] == 700


# ---------- ручки и экран ----------


async def test_the_station_shows_the_duty_board_to_officers_only(db, client):
    """Сводка по городу — сотруднику, свои дела — всем."""
    officer = await cop(db)
    victim = make_player(50, "Прохожий", where=VCPD)
    await db.save_player(victim)
    await a_case(db, victim.user_id, 1, loot=250)

    body = await (await client.get(
        "/api/police", headers=headers(officer.user_id)
    )).json()
    assert body["badge"] is True
    assert len(body["cases"]) == 1
    assert body["cases"][0]["loot"] == 250
    assert body["week"]["bonus"] == QUOTA_BONUS

    body = await (await client.get(
        "/api/police", headers=headers(victim.user_id)
    )).json()
    assert body["badge"] is False
    assert body["cases"] == [] and body["wanted"] == []
    # А своё дело он видит, и по нему можно написать заявление
    assert len(body["mine"]) == 1 and body["mine"][0]["can_file"] is True


async def test_the_handler_files_and_repays(db, client):
    """Заявление через ручку, задержание, и деньги обратно жертве."""
    victim = make_player(50, "Прохожий", where=VCPD, credits=0)
    await db.save_player(victim)
    case_id = await a_case(db, victim.user_id, 1, loot=400)

    answer = await client.post(
        "/api/police",
        json={"action": "report", "case": case_id},
        headers=headers(victim.user_id),
    )
    assert answer.status == 200
    body = await answer.json()
    assert body["mine"][0]["filed"] is True
    assert "Ордер" in body["said"]

    # Задержали — и деньги ждут в участке
    await db.close_cases(1, 80, NOW)
    await db.add_to_service_vault("police", 400)
    body = await (await client.post(
        "/api/police", json={"action": "collect"},
        headers=headers(victim.user_id),
    )).json()
    assert "400" in body["said"]
    assert (await db.get_player(victim.user_id)).credits == 400
