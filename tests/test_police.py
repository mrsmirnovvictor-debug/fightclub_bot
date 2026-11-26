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
from bot.game.health import now_ts
from bot.game.police import (
    ARREST_FEE,
    COURT_TERM,
    JAIL_TERMS,
    POLICE_TERM,
    PRISON,
    QUOTA_BONUS,
    jail_hours,
    jail_left,
    jail_seconds,
    quota_done,
    quota_target,
    shares,
    term_head,
    term_hours,
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


def test_the_term_is_read_off_the_sitting_and_not_off_the_ladder():
    """Часы берём из самой отсидки, а не считаем обратно по лестнице.

    Лестница участка описывает только то, что даёт полиция. Суд даёт
    свои часы и счётчик задержаний не трогает — и у осуждённого, которого
    полиция не брала ни разу, по лестнице выходил ноль часов при живом
    сроке. Так и было на экране тюрьмы: «отсидка 0-я, срок 0 ч».
    """
    assert term_hours(NOW + 2 * 3600, NOW, 0) == 2, "судебные два часа"
    assert term_hours(NOW + 6 * 3600, NOW, 2) == 6
    # Час с минутами — это два часа решётки, а не один: срок на экране
    # не должен выглядеть короче, чем он есть
    assert term_hours(NOW + 3600 + 60, NOW, 0) == 2
    # У строк, заведённых до того, как база стала помнить начало
    # отсидки, считаем по-старому — иначе срок вовсе пропал бы
    assert term_hours(NOW + 6 * 3600, 0, 2) == 6
    assert term_hours(NOW + 6 * 3600, NOW + 9 * 3600, 2) == 6


def test_the_sitting_says_who_put_the_man_inside():
    """Номер отсидки — про приводы, и к приговору он не относится."""
    assert term_head(1, POLICE_TERM) == "Отсидка 1-я"
    assert term_head(3, POLICE_TERM) == "Отсидка 3-я"
    # Полиция не брала ни разу — значит, посадил суд, и «отсидка 0-я»
    # тут не номер, а пустое место
    assert term_head(0, POLICE_TERM) == "По приговору суда"
    assert term_head(2, COURT_TERM) == "По приговору суда"


async def test_the_jail_row_remembers_when_the_sitting_began(db):
    """Посадка пишет в камеру начало срока и того, кто посадил."""
    one = make_player(50, "Мафиози", where=BAR)
    await db.save_player(one)

    await jail(db, one.user_id, NOW)
    sitting = await db.jail_sitting(one.user_id)
    assert sitting["since"] == NOW
    assert sitting["reason"] == POLICE_TERM
    assert sitting["until"] - sitting["since"] == jail_seconds(1)

    term = await term_of(db, one.user_id, NOW)
    assert term["head"] == "Отсидка 1-я"


async def test_the_court_term_shows_its_own_hours_and_not_a_zero(db):
    """Осуждённый видит свои часы, а не ноль от чужой лестницы.

    Полиция его не брала ни разу, счётчик задержаний нулевой — и экран
    тюрьмы писал «Отсидка 0-я, срок 0 ч.» при двух живых часах срока.
    """
    from bot.court_service import lock_up
    from bot.webapp.prison import build_prison

    one = make_player(50, "Угонщик", where=BAR)
    await db.save_player(one)

    until = await lock_up(db, one, 2, NOW)
    assert until == NOW + 2 * 3600

    body = await build_prison(db, one, NOW)
    mine = body["mine"]
    assert mine is not None, "сидящий себя в списке не нашёл"
    assert mine["hours"] == 2, "срок взят из лестницы участка, а не из дела"
    assert mine["times"] == 0, "приговор не считается приводом"
    assert mine["head"] == "По приговору суда"
    assert "сидишь 0-й раз" not in body["note"]
    assert "посадил суд" in body["note"]


async def test_a_sentence_on_top_of_a_sitting_extends_it_whole(db):
    """Приговор поверх начатой отсидки продлевает её, а не начинает новую.

    Срок на экране — вся отсидка целиком, от первого часа до выхода, а
    не последняя её добавка.
    """
    from bot.court_service import lock_up
    from bot.webapp.prison import build_prison

    one = make_player(50, "Мафиози", where=BAR)
    await db.save_player(one)

    await jail(db, one.user_id, NOW)  # три часа от участка
    fresh = await db.get_player(one.user_id)
    await lock_up(db, fresh, 2, NOW + 600)  # и два сверху от суда

    sitting = await db.jail_sitting(one.user_id)
    assert sitting["since"] == NOW, "начало отсидки сдвинулось"
    assert sitting["until"] == NOW + (3 + 2) * 3600

    body = await build_prison(db, fresh, NOW + 600)
    assert body["mine"]["hours"] == 5, "показана только добавка суда"


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


async def test_the_station_hires_its_own_and_the_agency_does_not(db, client):
    """В полицию приходят в полицию: агентство этих мест не показывает."""
    one = make_player(50, "Кадет", where=VCPD)
    one.diplomas = ["police"]
    await db.save_player(one)

    body = await (await client.get(
        "/api/police", headers=headers(one.user_id)
    )).json()
    ladder = {row["code"]: row for row in body["jobs"]}
    assert set(ladder) == {"police_chief", "police_officer", "police_patrol"}
    assert ladder["police_patrol"]["can_take"] is True
    assert ladder["police_patrol"]["limited"] is False
    # На кресло начальника нужен стаж, и об этом сказано словами
    assert ladder["police_chief"]["can_take"] is False
    assert "опыт" in ladder["police_chief"]["lacks"]

    answer = await client.post(
        "/api/police",
        json={"action": "hire", "job": "police_patrol"},
        headers=headers(one.user_id),
    )
    assert answer.status == 200
    assert "Патрульный" in (await answer.json())["said"]
    assert (await db.get_player(one.user_id)).job_code == "police_patrol"

    # А на доске агентства полицейских мест нет вовсе
    await stand(db, [one.user_id], "hr_agency")
    hr = await (await client.get(
        "/api/hr", headers=headers(one.user_id)
    )).json()
    assert hr["vacancies"], "доска агентства не пуста"
    assert not [
        row for row in hr["vacancies"] if row["code"].startswith("police_")
    ]


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

    # Задержали — и деньги ждут в участке. Час задержания настоящий, а
    # не стендовый: заявление ручка приняла по часам города, и у ордера
    # теперь есть срок давности — закрытие делом из будущего прошло бы
    # мимо живого дела
    await db.close_cases(1, 80, now_ts())
    await db.add_to_service_vault("police", 400)
    body = await (await client.post(
        "/api/police", json={"action": "collect"},
        headers=headers(victim.user_id),
    )).json()
    assert "400" in body["said"]
    assert (await db.get_player(victim.user_id)).credits == 400


# ---------- тюрьма: свой срок и соседи по камере ----------


async def test_the_prison_shows_the_term_and_the_inmates(db, client):
    """Экран тюрьмы: свой срок часами и все, кто сидит сейчас.

    До сих пор сидящий узнавал, сколько ему осталось, только попытавшись
    выйти: дорога отвечала отказом и называла срок. Теперь он стоит
    часами, а рядом — список тех, кто сидит, и час выхода каждого.
    """
    from bot.police_service import jail

    first = make_player(60, "Марла", where=PRISON)
    second = make_player(61, "Тайлер", where=PRISON)
    free = make_player(62, "Боб", where=PRISON)
    for one in (first, second, free):
        await db.save_player(one)
    await jail(db, 60, NOW)
    await jail(db, 61, NOW)
    await jail(db, 61, NOW)  # второй раз: срок длиннее

    body = await (await client.get(
        "/api/prison", headers=headers(60)
    )).json()
    rows = {one["name"]: one for one in body["inmates"]}
    assert body["total"] == 2 and set(rows) == {"Марла", "Тайлер"}
    assert body["mine"]["name"] == "Марла" and body["mine"]["mine"] is True
    assert rows["Марла"]["hours"] == JAIL_TERMS[0]
    assert rows["Тайлер"]["hours"] == JAIL_TERMS[1], "вторая отсидка длиннее"
    assert rows["Тайлер"]["left"] > rows["Марла"]["left"]
    assert body["terms"] == list(JAIL_TERMS)

    # Вольный видит тот же список, но своей строки в нём нет
    body = await (await client.get(
        "/api/prison", headers=headers(62)
    )).json()
    assert body["mine"] is None and body["total"] == 2
    assert all(not one["mine"] for one in body["inmates"])


async def test_the_prison_is_looked_at_from_inside(db, client):
    """В тюрьму заходят ногами: с улицы список не смотрят."""
    away = make_player(63, "Гуляка", where=VCPD)
    await db.save_player(away)

    answer = await client.get("/api/prison", headers=headers(63))
    assert answer.status == 409


async def test_the_served_term_leaves_the_list(db, client):
    """Отсидевший уходит из списка сам: его отпускает время.

    Часы тут настоящие: ручка смотрит на сегодняшний час, а `NOW`
    лежит в будущем — посаженный «вчера» по нему сидел бы до весны.
    """
    from bot.game.health import now_ts
    from bot.police_service import jail

    one = make_player(64, "Отсидевший", where=PRISON)
    await db.save_player(one)
    await jail(db, 64, now_ts() - 2 * JAIL_TERMS[0] * 3600)

    body = await (await client.get(
        "/api/prison", headers=headers(64)
    )).json()
    assert body["inmates"] == [] and body["mine"] is None
    assert "Камеры пусты" in body["note"]


# ---------- срок давности ордера ----------
#
# Ордер держится две недели. Это не милость, а признание того, что
# город не всесилен: не нашли человека за полмесяца — дело остаётся
# историей, и жить под вечным розыском никто не обязан.
#
# Прошлое при этом не прощается: лестница отсидок считает приводы, а не
# дела, и следующий ордер приходит к человеку с прежним счётом.


async def test_a_warrant_goes_stale_in_two_weeks(db):
    """Две недели не нашли — задерживать по этому делу больше нельзя."""
    from bot.game.police import WARRANT_TTL

    victim = make_player(50, "Прохожий", where=VCPD)
    await db.save_player(victim)
    case_id = await a_case(db, victim.user_id, 1)
    await file_report(db, victim, case_id, now=NOW)

    assert await has_warrant(db, 1, NOW) is True
    assert await has_warrant(db, 1, NOW + WARRANT_TTL - 1) is True
    assert await has_warrant(db, 1, NOW + WARRANT_TTL) is False


async def test_a_stale_case_is_not_paid_back_by_a_later_arrest(db):
    """Просроченное дело задержание не закрывает и денег не возвращает.

    Иначе срок давности был бы наполовину ненастоящим: взяли по свежему
    делу, а человек заплатил бы за все прежние, по которым его искать
    уже перестали.
    """
    from bot.game.police import WARRANT_TTL

    victim = make_player(50, "Прохожий", where=VCPD)
    await db.save_player(victim)
    old_case = await a_case(db, victim.user_id, 1, loot=400)
    await file_report(db, victim, old_case, now=NOW)

    # Полмесяца спустя — новое дело и новое заявление
    later = NOW + WARRANT_TTL + 3_600
    fresh = await db.open_case(victim.user_id, 1, BAR, 150, 1, later)
    assert await db.file_case(fresh, victim.user_id, later)

    recovered = await db.close_cases(1, 80, later)
    assert recovered == 150, "вернули только по живому делу"
    rows = {int(one["id"]): one for one in await db.cases_of_victim(victim.user_id)}
    assert int(rows[fresh]["arrested_at"]) == later
    assert int(rows[old_case]["arrested_at"]) == 0, "просроченное дело не трогали"


async def test_the_jail_ladder_remembers_the_past_after_a_stale_warrant(db):
    """Срок давности прощает дело, а не прошлое: лестница помнит приводы."""
    from bot.game.police import WARRANT_TTL, jail_hours

    don = make_player(1, "Дон", where=BAR)
    await db.save_player(don)
    first = await jail(db, don.user_id, NOW)
    assert (first - NOW) // 3_600 == jail_hours(1)

    # Вышел, год не попадался, ордер по тому делу просрочен — и всё же
    # следующая отсидка вторая, а не первая
    later = NOW + WARRANT_TTL * 30
    second = await jail(db, don.user_id, later)
    assert (second - later) // 3_600 == jail_hours(2)


# ---------- городские патрули ----------
#
# Живой полиции в городе может не быть вовсе, а ордера выписываются
# каждый день. Эти четыре — то, чем город исполняет ордер сам: две
# пары ходят по домам, и разыскиваемого берут там, где встретили.
#
# Кнопки у патруля нет и быть не может: он не игрок. Поэтому драка
# начинается сама — в тот миг, когда человек вошёл в дом, где стоит
# пара, и город на это посмотрел.


async def patrol_fight_out(service, raid, rounds: int = 200) -> None:
    """Добить задержание до развязки, нажимая только за человека.

    У патруля кнопок нет: его ход считается сам в начале раунда, и
    нажать за него нельзя — служба ответит «ход уже принят».
    """
    who = raid.victim
    for _ in range(rounds):
        if raid.finished:
            return
        if not raid.fighters[who].alive or who not in raid.paired:
            # Человека вынесли: дальше раунды идут сами, и ждать нечего
            return
        await service.choose(raid.id, who, "attack", "head")
        if raid.finished:
            return
        await service.choose(raid.id, who, "block", "belly")
    raise AssertionError("задержание не кончилось")


def test_two_pairs_walk_two_routes_by_the_clock():
    """Четверо, по двое, и где они стоят — считается от времени."""
    from bot.game.patrol import (
        OFFICERS,
        PAIRS,
        PAIR_SIZE,
        SHIFT_SECONDS,
        patrols_now,
        route_of,
        where_now,
    )

    assert (PAIRS, PAIR_SIZE, OFFICERS) == (2, 2, 4)
    # Маршруты не пересекаются: в одном доме пара всегда одна
    assert not set(route_of(0)) & set(route_of(1))
    assert len(patrols_now(NOW)) == PAIRS

    # Смена кончилась — пара на следующем доме маршрута, и это тот же
    # ответ у всякого, кто посмотрел
    first = where_now(0, NOW)
    assert where_now(0, NOW) == first, "ответ не зависит от спрашивающего"
    assert where_now(0, NOW + SHIFT_SECONDS) != first
    route = route_of(0)
    assert where_now(0, NOW + SHIFT_SECONDS * len(route)) == first


def test_the_patrol_comes_five_levels_above_the_man_it_takes():
    """Патруль выходит на пять уровней старше: ордер не украшение.

    Городской потолок им не предел: это служба, а не игрок, и
    лестница опыта на неё не распространяется. Иначе прокачанный
    мафиози бил бы городовых как мишени и жил под ордером спокойно.
    """
    from bot.game.patrol import LEVELS_ABOVE, officers

    assert LEVELS_ABOVE == 5
    pair = officers(9)
    assert len(pair) == 2
    assert [one.level for one in pair] == [14, 14]
    assert [one.fclass.code for one in pair] == ["tank", "warrior"]
    # Номера свои и отрицательные: аккаунта у них нет
    assert all(one.user_id < 0 for one in pair)
    assert len({one.user_id for one in pair}) == 2
    assert [one.level for one in officers(1)] == [6, 6]


def test_the_four_officers_are_four_different_people():
    """Четверо, и у каждого своё лицо и своё имя."""
    from bot.game.patrol import OFFICERS, officers

    first = officers(10, pair=0)
    second = officers(10, pair=1)
    names = [one.name for one in first + second]
    faces = [one.picture for one in first + second]
    assert len(set(names)) == OFFICERS == 4, names
    assert len(set(faces)) == OFFICERS, faces
    assert all(one.startswith("https://") for one in faces)
    # Пара всегда собирается одинаково: патрульный и сержант
    assert [one.fclass.code for one in second] == ["tank", "warrior"]


def test_the_patrol_wears_the_vcpd_uniform():
    """Форма VCPD на них настоящая: щит держит один, дубинка у другого.

    Поясов в комплекте два, а слот под пояс один: наручники носит тот,
    кто держит, кобуру — тот, кто бьёт.
    """
    from bot.game.equipment import CATALOGUE
    from bot.game.gear import Slot
    from bot.game.patrol import officers

    cop, sergeant = officers(10)
    worn = {
        one.name: {slot: item.item.code for slot, item in one.equipment.items.items()}
        for one in (cop, sergeant)
    }
    his = worn[cop.name]
    hers = worn[sergeant.name]
    assert his[Slot.OFFHAND] == "vcpd_riot_shield"
    assert his[Slot.WEAPON] == "vcpd_stun_device"
    assert his[Slot.BELT] == "vcpd_cuff_belt"
    assert hers[Slot.WEAPON] == "vcpd_baton"
    assert hers[Slot.BELT] == "vcpd_holster_belt"
    assert Slot.OFFHAND not in hers, "щит у сержанта не положен"
    # Общая форма на обоих
    for slot in (Slot.HEAD, Slot.JACKET, Slot.GLOVES, Slot.PANTS, Slot.BOOTS):
        assert his[slot] == hers[slot]
        assert his[slot].startswith("vcpd_")
    # Форму не купить и не выдать: в каталоге она ради картинок и чисел
    for code in set(his.values()) | set(hers.values()):
        item = CATALOGUE[code]
        assert item.reward and not item.price and not item.stars, code
        assert item.picture.endswith(f"items/{code}.jpeg")


def test_the_patrol_is_stronger_than_the_man_it_comes_for():
    """Двое городских сильнее того, за кем пришли, — в этом вся угроза.

    Мерка та же, что у рейдовых противников: живучесть на урон в ход
    (`scripts/foe_power.py`). Сравниваем с эталонным бойцом — тем, кто
    купил всё лучшее с прилавка к своему уровню.
    """
    from bot.game.classes import get_class
    from bot.game.combat import Fighter
    from bot.game.equipment import Equipment, OwnedItem
    from bot.game.patrol import officers
    from bot.game.reference import best_kit, developed_stats

    def might(one: Fighter) -> float:
        from scripts.foe_power import damage_per_turn, toughness

        return toughness(one) * damage_per_turn(one)

    def hero(code: str, level: int) -> Fighter:
        fclass = get_class(code)
        kit = {
            slot: OwnedItem(item=item, slot=slot)
            for slot, item in best_kit(fclass, level).items()
        }
        return Fighter(
            1, "Игрок", fclass, developed_stats(fclass, level),
            level=level, equipment=Equipment(items=kit),
        )

    for level in (4, 7, 10):
        pair = sum(might(one) for one in officers(level))
        for code in ("tank", "warrior", "assassin", "rogue"):
            mine = might(hero(code, level))
            assert pair >= mine, (
                f"ур{level} {code}: пара {pair:.0f} против игрока {mine:.0f}"
            )


async def test_the_patrol_takes_the_wanted_man_where_it_stands(db):
    """Разыскиваемый вошёл в дом с патрулём — его берут двое, сразу."""
    from bot.patrol_service import catch, patrol_at

    service = RobberyService(db, rng=random.Random(5))
    suspect = make_player(50, "Мафиози", where=BAR)
    await db.save_player(suspect)
    await a_case(db, 99, suspect.user_id, loot=300, filed=True)
    assert patrol_at(BAR, NOW) == 0, "в этот миг пара стоит в баре"

    raid = await catch(db, service, suspect, NOW)
    assert raid is not None
    assert raid.kind == "arrest"
    assert len(raid.fighters) == 3, "двое патрульных против одного"
    officers = [one for one in raid.fighters if one < 0]
    assert all(raid.sides[one] == RED for one in officers)
    assert all(one in raid.badges for one in officers)
    assert raid.sides[suspect.user_id] == BLUE
    # Тот из пары, кому досталась пара, уже отработал: кнопок у них
    # нет. Второй пропускает раунд, как всякий без пары, — и в этом
    # сила двоих: бьёт по одному, а здоровья у них на двоих
    assert {one for one in raid.acted} == {
        one for one in raid.paired if one < 0
    }
    assert any(one in raid.acted for one in officers)
    await service.close()


async def test_the_pair_that_stood_there_is_the_pair_that_takes_him(db):
    """Берёт та пара, что стоит в доме, — своими номерами и именами.

    Прежде номера приходили со стороны, и задержание всегда выходило
    под первой парой: в доме стоял Нил Барроу, а в журнале дрался Пит
    Валдес. Четверо городовых — четыре человека, и подменять одного
    другим нельзя.
    """
    from bot.game.patrol import officer_id, where_now
    from bot.patrol_service import catch, patrol_at

    service = RobberyService(db, rng=random.Random(5))
    here = where_now(1, NOW)
    suspect = make_player(50, "Мафиози", where=here)
    await db.save_player(suspect)
    await a_case(db, 99, suspect.user_id, loot=300, filed=True)
    assert patrol_at(here, NOW) == 1, "в этот миг здесь стоит вторая пара"

    raid = await catch(db, service, suspect, NOW)

    assert raid is not None
    took = sorted(one for one in raid.fighters if one < 0)
    assert took == sorted(officer_id(1, number) for number in (0, 1))
    assert {raid.names[one] for one in took} == {
        "Патрульный Нил Барроу", "Сержант Макс Ортис"
    }
    await service.close()


async def test_the_patrol_walks_past_the_clean_man(db):
    """Без ордера патруль не трогает никого: он не досматривает."""
    from bot.patrol_service import catch

    service = RobberyService(db)
    clean = make_player(50, "Прохожий", where=BAR)
    await db.save_player(clean)

    assert await catch(db, service, clean, NOW) is None
    assert service.of_user(clean.user_id) is None
    await service.close()


async def test_the_patrol_takes_nobody_in_a_house_it_left(db):
    """В доме без патруля задержания нет, даже если ордер есть."""
    from bot.patrol_service import catch

    service = RobberyService(db)
    suspect = make_player(50, "Мафиози", where=VCPD)
    await db.save_player(suspect)
    await a_case(db, 99, suspect.user_id, filed=True)

    assert await catch(db, service, suspect, NOW) is None
    await service.close()


async def test_the_patrol_waits_out_the_offline_the_walking_and_the_beaten(db):
    """Берут того, кто может ответить: остальных патруль пропускает.

    Драка без второй стороны — не задержание, а наказание за то, что
    человек закрыл приложение. То же правило и у живого сотрудника.
    """
    from bot.patrol_service import catch, takeable

    service = RobberyService(db)
    suspect = make_player(50, "Мафиози", where=BAR)
    await db.save_player(suspect)
    await a_case(db, 99, suspect.user_id, filed=True)
    assert await takeable(db, suspect, NOW) is True, "этого бы взяли"

    # Спрашиваем само правило, а не только исход: ту же проверку делает
    # и служба драки, и по одному отказу не видно, которая из двух
    # сработала
    gone = make_player(50, "Мафиози", where=BAR)
    gone.seen_at = NOW - 10 * 24 * 3_600
    await db.save_player(gone)
    assert await takeable(db, gone, NOW) is False
    assert await catch(db, service, gone, NOW) is None

    walking = make_player(50, "Мафиози", where=BAR)
    walking.travel_to = VCPD
    walking.arrives_at = NOW + 600
    await db.save_player(walking)
    assert await takeable(db, walking, NOW) is False
    assert await catch(db, service, walking, NOW) is None

    beaten = make_player(50, "Мафиози", where=BAR)
    beaten.set_hp(1, NOW)
    await db.save_player(beaten)
    assert not beaten.can_fight(NOW)
    assert await takeable(db, beaten, NOW) is False
    assert await catch(db, service, beaten, NOW) is None
    await service.close()


async def test_a_stale_warrant_is_not_executed_by_the_patrol(db):
    """Просроченный ордер патруль не исполняет: две недели прошли."""
    from bot.game.police import WARRANT_TTL
    from bot.patrol_service import catch
    from bot.game.patrol import SHIFT_SECONDS, route_of

    service = RobberyService(db)
    suspect = make_player(50, "Мафиози", where=BAR)
    await db.save_player(suspect)
    await a_case(db, 99, suspect.user_id, filed=True)

    # Полный круг маршрута спустя пара снова в баре, а ордеру уже
    # больше двух недель
    later = NOW + WARRANT_TTL
    later += (SHIFT_SECONDS * len(route_of(0))) - (
        later % (SHIFT_SECONDS * len(route_of(0)))
    ) + (NOW % (SHIFT_SECONDS * len(route_of(0))))
    suspect.seen_at = later
    await db.save_player(suspect)

    assert await catch(db, service, suspect, later) is None
    await service.close()


async def test_the_patrol_fight_goes_without_buttons_from_the_patrol(db):
    """Патруль дерётся сам: человек жмёт только за себя."""
    from bot.patrol_service import catch

    service = RobberyService(db, rng=random.Random(5))
    suspect = make_player(50, "Мафиози", where=BAR)
    await db.save_player(suspect)
    suspect = await weaken(db, suspect.user_id)
    await a_case(db, 99, suspect.user_id, loot=100, filed=True)

    raid = await catch(db, service, suspect, NOW)
    await patrol_fight_out(service, raid)

    assert raid.finished
    # И они били, а не стояли: иначе человек ушёл бы целым
    hit = sum(raid.fighters[one].damage_dealt for one in raid.fighters if one < 0)
    assert hit > 0
    await service.close()


async def test_the_patrol_jails_the_lone_wolf_too(db):
    """Сажает ордер, а не семья: одиночка отвечает за своё нападение.

    Прежде посадка смотрела на клан, и задержанный одиночка выходил из
    участка свободным — при живом заявлении жертвы и закрытом деле.
    """
    from bot.patrol_service import catch

    service = RobberyService(db, rng=random.Random(5))
    wolf = make_player(50, "Одиночка", where=BAR)
    await db.save_player(wolf)
    assert wolf.clan is None
    wolf = await weaken(db, wolf.user_id)
    await a_case(db, 99, wolf.user_id, loot=300, filed=True)

    raid = await catch(db, service, wolf, NOW)
    await patrol_fight_out(service, raid)

    assert raid.won, "слабого двое не упустят"
    assert [one["user_id"] for one in raid.jailed] == [wolf.user_id]
    assert (await db.get_player(wolf.user_id)).location == PRISON
    await service.close()


async def test_an_empty_handed_robber_still_goes_to_jail(db):
    """Дело без добычи сажает так же: взяли — значит ответил.

    Прежде ордер спрашивали после закрытия дел: закрыли дело с нулевой
    добычей, возвращать нечего, ордера уже нет — и задержанный уходил
    домой из-под рук патруля.
    """
    from bot.patrol_service import catch

    service = RobberyService(db, rng=random.Random(5))
    wolf = make_player(50, "Неудачник", where=BAR)
    await db.save_player(wolf)
    wolf = await weaken(db, wolf.user_id)
    await a_case(db, 99, wolf.user_id, loot=0, filed=True)

    raid = await catch(db, service, wolf, NOW)
    await patrol_fight_out(service, raid)

    assert raid.won and raid.recovered == 0
    assert [one["user_id"] for one in raid.jailed] == [wolf.user_id]
    # И работа участка засчитана: премия города падает за задержание,
    # а не за возвращённые деньги
    assert await db.service_vault("police") == ARREST_FEE
    await service.close()


async def test_a_beaten_patrol_leaves_the_warrant_in_force(db):
    """Отбился от патруля — ордер остаётся, и пара придёт снова."""
    from bot.patrol_service import catch

    service = RobberyService(db, rng=random.Random(5))
    strong = make_player(50, "Мафиози", where=BAR, level=10)
    await db.save_player(strong)
    await a_case(db, 99, strong.user_id, loot=300, filed=True)

    raid = await catch(db, service, strong, NOW)
    # Валим патруль руками: бросок кости — не правило, которое проверяем
    for one in list(raid.fighters):
        if one < 0:
            raid.fighters[one].hp = 0
    await patrol_fight_out(service, raid)

    assert raid.finished and not raid.won
    assert await has_warrant(db, strong.user_id, NOW) is True
    assert raid.jailed == []
    assert (await db.get_player(strong.user_id)).location == BAR
    await service.close()


async def test_the_house_screen_shows_the_patrol_and_starts_the_arrest(db, client):
    """Дом сам берёт разыскиваемого: кнопки у патруля нет.

    Экран дома — то место, где город на человека смотрит. Стоит он
    перед патрулём, и на нём ордер — драка начинается в этот самый
    запрос, без нажатий.
    """
    from bot.game.patrol import where_now
    from bot.game.health import now_ts as clock

    moment = clock()
    here = where_now(0, moment)
    suspect = make_player(50, "Мафиози", where=here)
    suspect.seen_at = moment
    await db.save_player(suspect)
    suspect = await weaken(db, suspect.user_id)
    suspect.location = here
    suspect.seen_at = moment
    await db.save_player(suspect)
    await a_case(db, 99, suspect.user_id, loot=300, filed=True)

    body = await (await client.get(
        "/api/crowd", headers=headers(suspect.user_id)
    )).json()

    assert body["patrol"] == 0, "патруль виден в доме"
    assert body["fight"], "и задержание уже идёт"
    assert body["fight"]["kind"] == "arrest"


async def test_the_card_of_a_patrolman_is_the_one_who_comes_for_you(db, client):
    """Карточка городового открывается по значку ℹ️ и считается от смотрящего.

    Записи в базе у патрульного нет вовсе: уровень у него не свой, а от
    того, за кем он придёт. Поэтому карточка собирается в тот миг, когда
    её попросили, и показывает того патрульного, который выйдет против
    открывшего. Тем же правилом живёт карточка Тренера.
    """
    from bot.game.patrol import officer_id, officer_name

    one = make_player(50, "Прохожий", level=7)
    await db.save_player(one)

    cop = await (await client.get(
        f"/api/card?user_id={officer_id(1, 0)}", headers=headers(one.user_id)
    )).json()

    assert cop["name"] == officer_name(1, 0) == "Патрульный Нил Барроу"
    assert cop["level"] == 12, "семь плюс пять"
    assert cop["patrol"] is True
    assert cop["fclass"]["code"] == "tank"
    # Форма на нём надетая: щит в карточке видно по слотам
    worn = {
        cell["slot"]: (cell["item"] or {}).get("code")
        for side in ("left", "right")
        for cell in cop["slots"][side]
    }
    assert worn["offhand"] == "vcpd_riot_shield"
    # Ни рейтинга, ни апгрейдов: он не игрок и в лестницах не стоит
    assert cop["record"]["rating"] is None
    assert cop["progress"]["ups"] == []
    assert cop["progress"]["ups_matter"] is False
    # Лицо своё, не классовое
    assert "vcpd_patrolman_02" in cop["avatar"]["url"]
    # И стоит он там, где стоит его пара, а не в клубе по умолчанию
    from bot.game.patrol import where_now
    from bot.game.health import now_ts as clock

    assert cop["place"]["code"] == where_now(1, clock())


async def test_a_number_that_is_not_a_patrolman_still_has_no_card(db, client):
    """Чужой отрицательный номер карточки не выдаёт: патруль — это четверо."""
    one = make_player(50, "Прохожий")
    await db.save_player(one)

    answer = await client.get(
        "/api/card?user_id=-999", headers=headers(one.user_id)
    )

    assert answer.status == 404
    assert (await answer.json())["error"] == "no_character"


async def test_a_clean_man_sees_the_patrol_and_walks_on(db, client):
    """Чистому патруль виден, но не страшен: его не трогают."""
    from bot.game.patrol import where_now
    from bot.game.health import now_ts as clock

    moment = clock()
    here = where_now(0, moment)
    clean = make_player(50, "Прохожий", where=here)
    clean.seen_at = moment
    await db.save_player(clean)

    body = await (await client.get(
        "/api/crowd", headers=headers(clean.user_id)
    )).json()

    assert body["patrol"] == 0
    assert not body["fight"]


async def test_the_map_shows_where_the_patrols_stand(db, client):
    """Патрули видно на карте: разыскиваемый вправе знать, куда не ходить."""
    from bot.game.patrol import PAIRS, patrols_now
    from bot.game.health import now_ts as clock

    one = make_player(50, "Прохожий", where=VCPD)
    one.seen_at = clock()
    await db.save_player(one)

    body = await (await client.get(
        "/api/map", headers=headers(one.user_id)
    )).json()

    assert set(body["patrols"]) == set(patrols_now(clock()))
    assert len(body["patrols"]) == PAIRS


async def test_the_house_lists_the_patrol_among_the_neighbours(db, client):
    """Патрульные стоят в списке дома: имя, звание и уровень.

    Кнопок у строки нет никаких: задерживают они сами, а напасть на
    городскую службу нельзя. Уровень показан тот, каким они выйдут
    против смотрящего — пять сверху от его собственного.

    Лица в строке нет: в доме стоят и соседи, и городовые, и строка у
    них одна на всех. Портрет у одних и ник у других делил список
    надвое по тому, кто чей, а не по тому, что с человеком можно
    сделать. Лицо осталось в карточке под ℹ️.
    """
    from bot.game.patrol import where_now
    from bot.game.health import now_ts as clock

    moment = clock()
    here = where_now(0, moment)
    one = make_player(50, "Прохожий", where=here, level=7)
    one.seen_at = moment
    await db.save_player(one)

    body = await (await client.get(
        "/api/crowd", headers=headers(one.user_id)
    )).json()

    patrol = [row for row in body["crowd"] if row.get("patrol")]
    assert [row["name"] for row in patrol] == [
        "Патрульный Пит Валдес", "Сержант Рэй Долан"
    ]
    assert [row["level"] for row in patrol] == [12, 12], "семь плюс пять"
    assert not any("picture" in row for row in patrol), "строка дома без лица"
    assert len({row["name"] for row in patrol}) == 2, "узнают их по имени"
    assert all(row["clan"] == "VCPD" for row in patrol)
    # Ни взять за карман, ни задержать: это пост, а не сосед
    assert not any(row["can_rob"] or row["can_arrest"] for row in patrol)
    assert all("по ордеру" in row["why"] for row in patrol)
    # И стоят они первыми: с ними в этом доме приходится считаться
    assert body["crowd"][0].get("patrol") is True


async def test_the_other_pair_has_its_own_names(db, client):
    """Во втором доме стоит вторая пара — со своими именами."""
    from bot.game.patrol import where_now
    from bot.game.health import now_ts as clock

    moment = clock()
    one = make_player(50, "Прохожий", where=where_now(1, moment))
    one.seen_at = moment
    await db.save_player(one)

    body = await (await client.get(
        "/api/crowd", headers=headers(one.user_id)
    )).json()

    patrol = [row for row in body["crowd"] if row.get("patrol")]
    assert [row["name"] for row in patrol] == [
        "Патрульный Нил Барроу", "Сержант Макс Ортис"
    ]
    assert body["patrol"] == 1
