"""Работа: вакансии, тест при приёме, смена и жалованье по понедельникам.

Порядок проверок — по тому, что дороже сломать:

1. **Вакансия одна на город.** Двое барменов не бывает, и проверка стоит
   в базе, а не в питоне: два нажатия в одну секунду иначе оба прошли бы.
2. **Тест не проходится чтением мини-аппа.** Верный ответ наружу не
   уезжает вовсе.
3. **Деньги.** Норма, доля от нормы, округление вверх и увольнение за
   половину — четыре правила, и каждое стоит своей проверки.
4. **Замок на дороге.** Со смены не уходят, и это держит сервер.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.content.work_quiz import QUIZZES, grade, pass_mark, quiz_for
from bot.database import Database
from bot.game.classes import Stats
from bot.game.clock import MOSCOW
from bot.game.locations import Service, get_location, where_to
from bot.game.work import (
    COOLDOWN_SECONDS,
    DAY_HOURS,
    PASS_SHARE,
    SHIFT_HOURS,
    SHIFT_SECONDS,
    VACANCIES,
    WEEK_SECONDS,
    get_vacancy,
    payday_after,
    payday_before,
    vacancy_at,
    week_is_over,
)
from bot.models import Player
from bot.travel_service import LockedError, Travel
from bot.webapp.server import create_app
from bot.work_service import (
    WorkError,
    apply,
    quit_job,
    quiz_payload,
    settle,
    start_shift,
    vacancies,
)
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

HR = "hr_agency"
BAR = "bar"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(
    user_id: int = 42, nickname: str = "Тайлер", location: str = HR
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=8,
        credits=500, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


def moment_at(year=2026, month=10, day=7, hour=12, minute=0) -> int:
    """Миг по московским часам. 7 октября 2026 — среда."""
    return int(datetime(year, month, day, hour, minute, tzinfo=MOSCOW).timestamp())


def right_answers(code: str) -> list[int]:
    return [one.answer for one in quiz_for(code)]


def wrong_answers(code: str) -> list[int]:
    """Все ответы мимо: выбираем вариант, который точно не верный."""
    return [0 if one.answer != 0 else 1 for one in quiz_for(code)]


async def hired(
    db: Database, player: Player, code: str = "bartender", now=None
) -> Player:
    """Боец, уже принятый на работу. Возвращаем его самого, а не заявку."""
    await stand(db, player)
    await apply(db, player, code, right_answers(code), now or moment_at())
    return player


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- дома на карте ----------


def test_the_hiring_moved_to_the_agency_of_its_own():
    """Наём стоит в HR-агентстве, а Бизнес-центру вернули его имя.

    Раньше наём приписали офисному зданию в «Деловом углу» — за
    неимением своего дома. Теперь такой дом есть: HR-агентство в районе
    «HR и ювелирный». Код прежнего дома при этом не трогали: у тех, кто
    уже работает, место работы считается по услуге, а не по дому.
    """
    place = get_location(HR)

    assert place.title == "HR-агентство"
    assert place.district == "hr_jewelry_district_v3"
    assert place.allows(Service.HIRE)
    assert where_to(Service.HIRE) is place

    office = get_location("office_building")
    assert office.title == "Бизнес-центр"
    assert not office.allows(Service.HIRE)
    assert office.soon, "дому без дела нужно сказать, чего в нём ждать"


def test_every_vacancy_has_a_house_that_takes_workers():
    """Работать негде — значит, вакансии нет: проверяем все пять."""
    for one in VACANCIES:
        place = get_location(one.place)
        assert place is not None, one.code
        assert place.allows(Service.WORK), f"{one.place} не принимает работников"
        assert vacancy_at(one.place) is one


def test_the_work_does_not_steal_the_house_from_its_own_screen():
    """Зал остаётся залом, клуб клубом: работа стоит второй услугой.

    Экран дома выбирается по первой услуге. Встань работа первой — зал
    открывался бы сменой вместо тренировок.
    """
    for code in ("strength_gym", "fight_club", "casino"):
        place = get_location(code)
        assert place.services[0] is not Service.WORK, code
        assert Service.WORK in place.services, code


def test_the_five_vacancies_are_the_ones_the_city_promised():
    rows = {one.code: one for one in VACANCIES}

    assert len(VACANCIES) == 5
    assert (rows["bartender"].salary, rows["bartender"].hours) == (250, 10)
    assert (rows["postman"].salary, rows["postman"].hours) == (200, 8)
    assert (rows["trainer"].salary, rows["trainer"].hours) == (300, 10)
    assert (rows["fight_host"].salary, rows["fight_host"].hours) == (300, 10)
    assert (rows["croupier"].salary, rows["croupier"].hours) == (300, 10)
    # Образования не требует ни одна: это первая очередь работы
    assert all(one.education == "не требуется" for one in VACANCIES)


# ---------- тест при приёме ----------


def test_every_vacancy_has_a_quiz_of_five():
    for one in VACANCIES:
        questions = quiz_for(one.code)
        assert len(questions) == 5, one.code
        for question in questions:
            assert len(question.options) >= 3, question.text
            assert 0 <= question.answer < len(question.options)


def test_four_right_out_of_five_is_the_pass_mark():
    """Восемьдесят процентов от пяти — ровно четыре."""
    assert PASS_SHARE == 0.8
    assert pass_mark(5, PASS_SHARE) == 4


def test_the_right_answer_never_leaves_the_server():
    """Тест, который проходится чтением ответа мини-аппа, — не тест."""
    for one in VACANCIES:
        for row in quiz_payload(one.code):
            assert set(row) == {"text", "options"}, row
            assert "answer" not in row and "correct" not in row


def test_grading_counts_only_what_was_answered():
    code = "bartender"

    assert grade(code, right_answers(code)) == (5, 5)
    assert grade(code, wrong_answers(code)) == (0, 5)
    # Недостающие ответы — неверные, а не «зачтём»
    assert grade(code, right_answers(code)[:3]) == (3, 5)
    assert grade(code, []) == (0, 5)


async def test_four_right_answers_get_the_job(db):
    player = await stand(db, make_player())
    answers = right_answers("bartender")
    answers[0] = 1 - answers[0] if answers[0] != 1 else 0  # одну мимо

    attempt = await apply(db, player, "bartender", answers, moment_at())

    assert attempt.hired is True
    assert (attempt.right, attempt.need) == (4, 4)
    assert player.job_code == "bartender"


async def test_three_right_answers_do_not(db):
    player = await stand(db, make_player())
    answers = right_answers("bartender")
    answers[0] = 0 if answers[0] != 0 else 1
    answers[1] = 0 if answers[1] != 0 else 1

    attempt = await apply(db, player, "bartender", answers, moment_at())

    assert attempt.hired is False and attempt.right == 3
    assert player.job_code == ""


async def test_a_failed_test_shuts_that_door_for_a_week(db):
    player = await stand(db, make_player())
    now = moment_at()
    await apply(db, player, "bartender", wrong_answers("bartender"), now)

    with pytest.raises(WorkError, match="через 7 дн"):
        await apply(db, player, "bartender", right_answers("bartender"), now)

    # А через неделю — можно
    later = now + COOLDOWN_SECONDS + 1
    attempt = await apply(db, player, "bartender", right_answers("bartender"), later)
    assert attempt.hired is True


async def test_a_failed_test_leaves_the_other_doors_open(db):
    """Провалить тест бармена не значит не уметь на почте."""
    player = await stand(db, make_player())
    now = moment_at()
    await apply(db, player, "bartender", wrong_answers("bartender"), now)

    attempt = await apply(db, player, "postman", right_answers("postman"), now)

    assert attempt.hired is True


# ---------- вакансия одна на город ----------


async def test_a_taken_vacancy_is_gone_from_the_board(db):
    first = await stand(db, make_player())
    second = await stand(db, make_player(2, "Марла"))
    await apply(db, first, "bartender", right_answers("bartender"), moment_at())

    board = await vacancies(db, second, moment_at())
    row = next(one for one in board if one["vacancy"].code == "bartender")

    assert row["taken_by"] == "Тайлер"
    with pytest.raises(WorkError, match="занято"):
        await apply(db, second, "bartender", right_answers("bartender"), moment_at())


async def test_the_second_barman_is_stopped_by_the_base_not_by_python(db):
    """Два нажатия в секунду — один бармен. Держат это двое, порознь.

    Условие в самом запросе не даёт занять второе место тому, у кого уже
    есть работа. Уникальный индекс не даёт двоим занять одно. Проверки
    разные, и каждая нужна: первая про бойца, вторая про место.
    """
    first = await stand(db, make_player())
    second = await stand(db, make_player(2, "Марла"))
    now = moment_at()

    assert await db.take_job(first.user_id, "bartender", now, now) is True
    assert await db.take_job(second.user_id, "bartender", now, now) is False

    taken = await db.taken_jobs()
    assert list(taken) == ["bartender"]
    assert taken["bartender"]["nickname"] == "Тайлер"


async def test_the_query_itself_refuses_a_second_job_to_one_fighter(db):
    """Условие `job_code = ''` в запросе: работа одна на бойца.

    Индекс этого не ловит — он про место, а не про человека: перейти с
    бармена на почтальона одним запросом он бы позволил, и боец потерял
    бы недельные часы вместе с местом.
    """
    player = await stand(db, make_player())
    now = moment_at()
    assert await db.take_job(player.user_id, "bartender", now, now) is True

    assert await db.take_job(player.user_id, "postman", now, now) is False

    taken = await db.taken_jobs()
    assert list(taken) == ["bartender"]
    assert taken["bartender"]["nickname"] == "Тайлер"


async def test_the_base_holds_one_holder_per_vacancy_by_its_own_index(db):
    """Индекс на вакансии: двоих с одним местом база не примет вовсе."""
    import sqlite3

    await stand(db, make_player())
    await stand(db, make_player(2, "Марла"))
    await db.conn.execute(
        "UPDATE players SET job_code = 'bartender' WHERE user_id = 42"
    )

    with pytest.raises(sqlite3.IntegrityError):
        await db.conn.execute(
            "UPDATE players SET job_code = 'bartender' WHERE user_id = 2"
        )


async def test_a_fighter_holds_one_job_at_a_time(db):
    player = await hired(db, make_player())

    with pytest.raises(WorkError, match="уже есть работа"):
        await apply(db, player, "postman", right_answers("postman"), moment_at())


# ---------- счёт при приёме ----------


async def test_hiring_opens_a_bank_account_but_not_a_card(db):
    """Жалованью надо куда-то приходить. Карта за деньги — её не навязывают."""
    player = await stand(db, make_player())

    attempt = await apply(db, player, "bartender", right_answers("bartender"), moment_at())

    assert attempt.account.startswith("VB-")
    assert player.has_account and not player.has_card
    fresh = await db.get_player(42)
    assert fresh.account_number == attempt.account
    assert fresh.card_at == 0


async def test_an_existing_account_is_not_opened_twice(db):
    from bot.bank_service import open_account

    player = await stand(db, make_player())
    number = await open_account(db, player)

    attempt = await apply(db, player, "bartender", right_answers("bartender"), moment_at())

    assert attempt.account == ""
    assert (await db.get_player(42)).account_number == number


# ---------- смена ----------


async def test_a_shift_is_two_hours_and_locks_the_road(db):
    player = await hired(db, make_player(location=BAR))
    now = moment_at()

    until = await start_shift(db, player, now)

    assert until == now + SHIFT_SECONDS
    assert player.on_shift(now) and player.shift_left(now) == SHIFT_SECONDS
    # Часы записаны сразу: уйти со смены нельзя, досчитывать нечего
    assert player.job_minutes == SHIFT_HOURS * 60


async def test_work_is_done_where_the_job_is(db):
    player = await hired(db, make_player(location=HR))

    with pytest.raises(WorkError, match="на месте"):
        await start_shift(db, player, moment_at())


async def test_only_one_shift_a_day(db):
    player = await hired(db, make_player(location=BAR))
    now = moment_at()
    await start_shift(db, player, now)

    with pytest.raises(WorkError, match="не больше"):
        await start_shift(db, player, now + SHIFT_SECONDS + 1)

    assert player.job_minutes == DAY_HOURS * 60


async def test_a_new_day_opens_the_shift_again(db):
    player = await hired(db, make_player(location=BAR))
    now = moment_at(hour=20)
    await start_shift(db, player, now)

    tomorrow = moment_at(day=8, hour=10)
    await start_shift(db, player, tomorrow)

    assert player.job_minutes == 2 * SHIFT_HOURS * 60


async def test_the_shift_cannot_start_twice(db):
    player = await hired(db, make_player(location=BAR))
    now = moment_at()
    await start_shift(db, player, now)

    with pytest.raises(WorkError, match="уже идёт"):
        await start_shift(db, player, now + 60)


async def test_nobody_leaves_the_house_during_a_shift(db):
    """Замок на дороге держит сервер, а не спрятанная кнопка."""
    player = await hired(db, make_player(location=BAR))
    now = moment_at()
    await start_shift(db, player, now)

    with pytest.raises(LockedError, match="на смене"):
        await Travel(db).go(player, "fight_club", now)

    # А как час вышел — иди куда хочешь
    await Travel(db).go(player, "fight_club", now + SHIFT_SECONDS + 1)


async def test_without_a_job_there_is_no_shift(db):
    player = await stand(db, make_player(location=BAR))

    with pytest.raises(WorkError, match="нигде не работаешь"):
        await start_shift(db, player, moment_at())


# ---------- понедельник, девять утра ----------


def test_the_week_turns_on_monday_at_nine_moscow():
    # 7 октября 2026 — среда
    wednesday = moment_at(day=7, hour=12)
    monday = payday_before(wednesday)

    when = datetime.fromtimestamp(monday, MOSCOW)
    assert when.weekday() == 0 and when.hour == 9 and when.minute == 0
    assert when.date() == datetime(2026, 10, 5, tzinfo=MOSCOW).date()
    assert payday_after(wednesday) == monday + WEEK_SECONDS


def test_monday_before_nine_still_belongs_to_the_week_before():
    early = moment_at(day=5, hour=8, minute=59)
    payday = datetime.fromtimestamp(payday_before(early), MOSCOW)

    assert payday.date() == datetime(2026, 9, 28, tzinfo=MOSCOW).date()
    # А в девять ноль-ноль неделя уже новая
    sharp = moment_at(day=5, hour=9)
    assert datetime.fromtimestamp(payday_before(sharp), MOSCOW).date() == (
        datetime(2026, 10, 5, tzinfo=MOSCOW).date()
    )


def test_a_week_is_over_only_after_its_monday():
    start = payday_before(moment_at(day=7))

    assert week_is_over(start, moment_at(day=8)) is False
    assert week_is_over(start, moment_at(day=12, hour=8)) is False  # ещё до девяти
    assert week_is_over(start, moment_at(day=12, hour=9)) is True


async def test_monday_holds_two_shifts_one_for_each_week(db):
    """В понедельник работают дважды: ночную смену и дневную.

    В девять утра понедельника закрывается неделя и приходит жалованье.
    Ночная смена до девяти идёт в зачёт прошлой неделе, дневная после
    девяти — новой, и запирать бойца на сутки после ночной значило бы
    отнимать у него первый день недели, за который ему же и платят.
    """
    player = await hired(db, make_player(location=BAR), now=moment_at(day=7))
    # Неделя почти отработана: восемь часов из десяти, и ночная смена её
    # закроет. Иначе в девять утра бойца уволят за норму, а не за часы
    player.job_minutes = 8 * 60
    night = moment_at(day=12, hour=3)  # 12 октября 2026 — понедельник

    await start_shift(db, player, night)
    assert player.job_minutes == 10 * 60, "ночная смена пошла в прошлую неделю"

    # До девяти второй раз не встать: ночь — всё тот же рабочий день
    with pytest.raises(WorkError, match="хватит"):
        await start_shift(db, player, moment_at(day=12, hour=6))

    # А в девять неделя закрылась: часы ушли в жалованье, день начался заново
    await start_shift(db, player, moment_at(day=12, hour=10))
    assert player.job_minutes == SHIFT_HOURS * 60, (
        "часы ночной смены не ушли в прошлую неделю"
    )

    # И третью смену в тот же понедельник уже не отработать
    with pytest.raises(WorkError, match="хватит"):
        await start_shift(db, player, moment_at(day=12, hour=14))


def test_the_night_before_payday_says_when_the_shift_opens_again():
    """В ночь на понедельник отказ говорит про девять утра, а не про завтра."""
    from bot.game.work import day_is_over

    night = day_is_over(moment_at(day=12, hour=3), DAY_HOURS)
    assert "хватит" in night and "9:00" in night and "жалованье" in night

    # В остальные дни — обычный отказ, без обещания смены к утру
    usual = day_is_over(moment_at(day=13, hour=3), DAY_HOURS)
    assert "хватит" in usual and "9:00" not in usual


def test_the_work_day_splits_monday_and_nothing_else():
    """Рабочий день меняется в полночь, а в понедельник ещё и в девять."""
    from bot.game.work import work_day

    # Понедельник: ночь и день — разные рабочие дни
    assert work_day(moment_at(day=12, hour=3)) != work_day(moment_at(day=12, hour=10))
    # Вторник: что три часа ночи, что три дня — день один
    assert work_day(moment_at(day=13, hour=3)) == work_day(moment_at(day=13, hour=15))
    # Полночь день меняет всегда
    assert work_day(moment_at(day=13, hour=23)) != work_day(moment_at(day=14, hour=1))
    # В девять утра вторника ничего не происходит: неделя та же
    assert work_day(moment_at(day=13, hour=8)) == work_day(moment_at(day=13, hour=9))


def test_the_norm_pays_in_full_and_overtime_adds_nothing():
    bartender = get_vacancy("bartender")

    assert bartender.payout(10) == 250
    assert bartender.payout(12) == 250, "переработка не прибавляет"


def test_less_than_the_norm_is_paid_pro_rata_rounded_up():
    bartender = get_vacancy("bartender")  # 250 за 10 часов

    assert bartender.payout(8) == 200
    # 6 часов — это 150 ровно; 7 часов — 175
    assert bartender.payout(6) == 150
    assert bartender.payout(7) == 175
    postman = get_vacancy("postman")  # 200 за 8 часов
    # 5 часов — это 125 ровно, а 3 часа — 75
    assert postman.payout(5) == 125
    assert postman.payout(3) == 75
    assert bartender.payout(0) == 0


def test_a_part_of_an_hour_is_rounded_up_not_down():
    """Округление вверх, а не вниз: последние минуты тоже оплачены.

    Часы у ставок ровные, и на целых часах вверх и вниз дают одно и то
    же — правило проверяется там, где эти два ответа расходятся. Сейчас
    сойтись им негде: смена ровно два часа, и дробных часов в игре не
    бывает. Правило всё равно живёт в коде и обязано быть верным: смена
    однажды может стать часовой или получасовой.
    """
    bartender = get_vacancy("bartender")  # 250 за 10 часов, 25 за час
    postman = get_vacancy("postman")  # 200 за 8 часов, 25 за час

    # Полчаса бармена — это 12.5: вверх 13, вниз 12
    assert bartender.payout(0.5) == 13
    assert postman.payout(0.5) == 13
    # И ни один час не стоит ноль, пока он отработан
    assert bartender.payout(0.01) == 1


def test_under_half_the_norm_means_the_sack():
    bartender = get_vacancy("bartender")  # норма 10 часов

    assert bartender.fires(4) is True
    assert bartender.fires(5) is False, "ровно половина — ещё не увольнение"
    assert bartender.keep_hours() == 5


async def test_the_week_is_settled_on_monday_and_the_money_goes_to_the_account(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    # Пять смен по два часа — норма закрыта
    for day in range(6, 11):
        await start_shift(db, player, moment_at(day=day, hour=12))
    assert player.job_minutes == 600

    said = await settle(db, player, moment_at(day=12, hour=9))

    assert "250" in said
    assert player.account_balance == 250
    assert player.credits == 500, "жалованье ушло в наличные вместо счёта"
    assert player.job_minutes == 0, "часы не обнулились"
    assert player.job_code == "bartender", "уволили при выполненной норме"


async def test_half_the_norm_keeps_the_job_and_pays_half(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    for day in range(6, 9):  # три смены — шесть часов из десяти
        await start_shift(db, player, moment_at(day=day, hour=12))

    said = await settle(db, player, moment_at(day=12, hour=9))

    assert player.account_balance == 150
    assert player.job_code == "bartender"
    assert "уволили" not in said


async def test_under_half_the_norm_is_paid_and_sacked(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    await start_shift(db, player, moment_at(day=6, hour=12))  # два часа из десяти

    said = await settle(db, player, moment_at(day=12, hour=9))

    assert player.account_balance == 50, "уволенному не заплатили за отработанное"
    assert "уволили" in said
    assert player.job_code == ""
    # Место снова в агентстве
    assert await db.taken_jobs() == {}


async def test_the_sacked_cannot_come_back_for_a_week(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    monday = moment_at(day=12, hour=9)
    await settle(db, player, monday)

    with pytest.raises(WorkError, match="уволили"):
        await apply(db, player, "bartender", right_answers("bartender"), monday)

    later = monday + COOLDOWN_SECONDS + 1
    assert (await apply(db, player, "bartender", right_answers("bartender"), later)).hired


async def test_a_month_away_costs_one_week_not_four(db):
    """Тот же закон, что у полиса: за время без услуги клуб не считает."""
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    await start_shift(db, player, moment_at(day=6, hour=12))

    said = await settle(db, player, moment_at(month=11, day=9, hour=12))

    assert said.count("Жалованье") == 1
    assert player.account_balance == 50


async def test_settling_twice_pays_once(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    await start_shift(db, player, moment_at(day=6, hour=12))
    monday = moment_at(day=12, hour=10)

    await settle(db, player, monday)
    again = await settle(db, player, monday)

    assert again == ""


async def test_nothing_is_settled_before_monday(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    await start_shift(db, player, moment_at(day=6, hour=12))

    assert await settle(db, player, moment_at(day=9)) == ""
    assert player.account_balance == 0


# ---------- уход по своей воле ----------


async def test_quitting_pays_for_what_was_worked_and_frees_the_place(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    await start_shift(db, player, moment_at(day=6, hour=12))

    await quit_job(db, player, moment_at(day=7))

    assert player.account_balance == 50
    assert player.job_code == ""
    assert await db.taken_jobs() == {}


async def test_nobody_quits_mid_shift(db):
    player = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    now = moment_at(day=6, hour=12)
    await start_shift(db, player, now)

    with pytest.raises(WorkError, match="доработай смену"):
        await quit_job(db, player, now + 60)


# ---------- через ручку ----------


async def test_the_agency_shows_five_vacancies_by_the_handle(client, db):
    await stand(db, make_player())

    body = await (await client.get("/api/hr", headers=headers())).json()

    assert len(body["vacancies"]) == 5
    row = body["vacancies"][0]
    assert {"code", "title", "salary", "hours", "place_title"} <= set(row)
    # Вопросов на доске нет: их выдают по заявке
    assert "quiz" not in row


async def test_the_agency_is_shut_to_those_who_are_elsewhere(client, db):
    await stand(db, make_player(location=BAR))

    answer = await client.get("/api/hr", headers=headers())

    assert answer.status == 409


async def test_the_quiz_comes_without_answers_by_the_handle(client, db):
    await stand(db, make_player())

    body = await (
        await client.post(
            "/api/hr", json={"action": "quiz", "code": "bartender"}, headers=headers()
        )
    ).json()

    assert len(body["quiz"]) == 5
    for row in body["quiz"]:
        assert set(row) == {"text", "options"}


async def test_hiring_through_the_handle_takes_the_vacancy(client, db):
    await stand(db, make_player())

    body = await (
        await client.post(
            "/api/hr",
            json={
                "action": "apply",
                "code": "bartender",
                "answers": right_answers("bartender"),
            },
            headers=headers(),
        )
    ).json()

    assert body["attempt"]["hired"] is True
    row = next(one for one in body["vacancies"] if one["code"] == "bartender")
    assert row["mine"] is True and row["taken_by"] == "Тайлер"
    assert (await db.get_player(42)).job_code == "bartender"


async def test_the_workplace_offers_the_shift_by_the_handle(client, db):
    player = make_player(location=BAR)
    await hired(db, player)

    body = await (await client.get("/api/work", headers=headers())).json()
    assert body["job"]["code"] == "bartender"
    assert body["can_start"] is True

    body = await (
        await client.post("/api/work", json={"action": "start"}, headers=headers())
    ).json()

    assert body["shift"]["seconds_left"] > 0
    assert body["can_start"] is False
    assert (await db.get_player(42)).shift_until > 0


async def test_the_workplace_of_a_stranger_says_where_jobs_are_taken(client, db):
    """В баре стоит тот, кто там не работает: экран есть, смены нет."""
    await stand(db, make_player(location=BAR))

    body = await (await client.get("/api/work", headers=headers())).json()

    assert body["job"] == {}
    assert body["can_start"] is False
    assert "HR-агентств" in body["note"]


def test_the_quiz_questions_are_all_answerable():
    """У каждого вопроса верный вариант лежит внутри списка вариантов."""
    for code, questions in QUIZZES.items():
        assert get_vacancy(code) is not None, code
        for question in questions:
            assert question.correct(question.answer)
            assert not question.correct((question.answer + 1) % len(question.options))


def test_the_shift_and_the_day_agree_with_each_other():
    """Две смены в сутки не влезают: день ровно в одну смену."""
    from bot.game.work import day_is_full, shift_fits

    assert SHIFT_HOURS == 2 and DAY_HOURS == 2
    assert shift_fits(0) is True
    assert shift_fits(SHIFT_HOURS * 60) is False
    assert day_is_full(DAY_HOURS * 60) is True


def test_a_week_of_shifts_covers_the_norm():
    """Норму можно закрыть: иначе увольняли бы всех подряд.

    Десять часов — это пять смен, а в неделе семь дней. Норма выполнима,
    и с запасом в два дня; восьмичасовая — с запасом в три.
    """
    for one in VACANCIES:
        assert one.shifts <= 7, f"{one.code}: норму не закрыть за неделю"
        assert one.shifts * SHIFT_HOURS >= one.hours


async def test_a_vacancy_is_freed_from_a_fighter_who_stopped_coming(db):
    """Место одно на город — и не может достаться первому навсегда.

    Своя неделя сводится, когда за работой приходят. Тот, кто перестал
    заходить, свою так и не сведёт, — и место держал бы вечно. Поэтому
    недели держателей сводит тот, кто смотрит на доску.
    """
    absent = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    await start_shift(db, absent, moment_at(day=6, hour=12))  # два часа из десяти
    seeker = await stand(db, make_player(2, "Марла"))

    # Месяц спустя на доску смотрит другой боец
    later = moment_at(month=11, day=9, hour=12)
    board = await vacancies(db, seeker, later)

    row = next(one for one in board if one["vacancy"].code == "bartender")
    assert row["taken_by"] == "", "место так и осталось за ушедшим"
    # И ушедшему при этом заплатили за отработанное, а не забыли о нём
    paid = await db.get_player(absent.user_id)
    assert paid.account_balance == 50
    assert paid.job_code == ""


async def test_the_freed_vacancy_can_be_taken_by_another(db):
    absent = await hired(db, make_player(location=BAR), now=moment_at(day=6))
    seeker = await stand(db, make_player(2, "Марла"))
    later = moment_at(month=11, day=9, hour=12)

    attempt = await apply(db, seeker, "bartender", right_answers("bartender"), later)

    assert attempt.hired is True
    taken = await db.taken_jobs()
    assert taken["bartender"]["nickname"] == "Марла"
    assert (await db.get_player(absent.user_id)).job_code == ""


async def test_a_holder_who_keeps_the_norm_is_not_touched_by_others(db):
    """Сводим чужую неделю по тем же правилам, а не отбираем место."""
    worker = await hired(db, make_player(location=BAR), now=moment_at(day=5, hour=10))
    for day in range(5, 10):
        await start_shift(db, worker, moment_at(day=day, hour=12))
    seeker = await stand(db, make_player(2, "Марла"))

    board = await vacancies(db, seeker, moment_at(day=12, hour=10))

    row = next(one for one in board if one["vacancy"].code == "bartender")
    assert row["taken_by"] == "Тайлер", "норму выполнил, а места лишился"
    kept = await db.get_player(worker.user_id)
    assert kept.job_code == "bartender"
    assert kept.account_balance == 250, "норму выполнил, а не заплатили"
