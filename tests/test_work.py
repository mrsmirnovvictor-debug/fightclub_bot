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
from bot.game.health import now_ts
from bot.game.locations import Service, get_location, where_to
from bot.game.work import (
    agency_board,
    COOLDOWN_SECONDS,
    DAY_HOURS,
    PASS_SHARE,
    SHIFT_HOURS,
    SHIFT_SECONDS,
    STAFF_HOURS,
    VACANCIES,
    WEEK_SECONDS,
    get_vacancy,
    payday_after,
    payday_before,
    vacancies_at,
    week_is_over,
)
from bot.models import Player
from bot.travel_service import LockedError, Travel
from bot.webapp.server import create_app
from bot.webapp.work import build_work
from bot.work_service import (
    WorkError,
    apply,
    hire_at_house,
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
    """Работать негде — значит, вакансии нет: проверяем все до одной."""
    for one in VACANCIES:
        place = get_location(one.place)
        assert place is not None, one.code
        assert place.allows(Service.WORK), f"{one.place} не принимает работников"
        assert one in vacancies_at(one.place)


def test_the_work_does_not_steal_the_house_from_its_own_screen():
    """Зал остаётся залом, клуб клубом: работа стоит второй услугой.

    Экран дома выбирается по первой услуге. Встань работа первой — зал
    открывался бы сменой вместо тренировок.
    """
    for code in ("strength_gym", "fight_club", "casino"):
        place = get_location(code)
        assert place.services[0] is not Service.WORK, code
        assert Service.WORK in place.services, code


def test_the_street_vacancies_are_the_ones_the_city_promised():
    """Девять мест с улицы: без диплома, на одном тесте при приёме.

    Пять первой очереди и четыре из восточного столбца. Диплома не
    просят нигде, но ремесло спрашивают стажем: под ножницы в городе
    диплома нет, а брать на них прямо с улицы — всё равно что сажать за
    стойку банка первого встречного.
    """
    rows = {one.code: one for one in VACANCIES}
    street = [one for one in VACANCIES if not one.needs]

    assert len(street) == 9
    # Норма на улице такая же, как в конторе: десять часов у всех, и у
    # почты тоже — прежде она одна стояла на восьми
    for one in street:
        assert one.hours == STAFF_HOURS, one.code
    assert (rows["bartender"].salary, rows["bartender"].seats) == (250, 5)
    assert (rows["postman"].salary, rows["postman"].seats) == (250, 3)
    assert (rows["trainer"].salary, rows["trainer"].seats) == (300, 3)
    assert (rows["fight_host"].salary, rows["fight_host"].seats) == (300, 3)
    assert (rows["croupier"].salary, rows["croupier"].seats) == (300, 2)
    # Восточный столбец: мойщик и официант — улица в чистом виде,
    # барбер и мастер салона — ремесло, и с них спрашивают месяц стажа.
    # Ремесло и платит лучше: триста пятьдесят против трёхсот
    assert (rows["car_washer"].salary, rows["car_washer"].seats) == (250, 2)
    assert (rows["waiter"].salary, rows["waiter"].seats) == (250, 3)
    assert (rows["barber"].salary, rows["barber"].seats) == (350, 2)
    assert (rows["beauty_master"].salary, rows["beauty_master"].seats) == (350, 2)
    assert rows["barber"].months == 1 and rows["beauty_master"].months == 1

    # Диплома не просят ни на одном
    for one in street:
        assert one.education == "не требуется"
    # Стаж спрашивают только у двух ремесленных
    assert {one.code for one in street if one.months} == {
        "barber", "beauty_master",
    }
    # Мест на улице везде несколько: одно место на всех означало бы,
    # что устроиться успевает ровно один боец за всю жизнь города, а
    # остальным на доске написано «занято»
    for one in street:
        assert 2 <= one.seats <= 5, f"{one.code}: мест {one.seats}"


# ---------- тест при приёме ----------


def test_every_street_vacancy_has_a_quiz_of_five():
    """Тест — там, где нет диплома: он и есть вступительный экзамен.

    На местах под диплом экзамен уже сдан — в училище, и спрашивать
    второй раз то же самое значило бы не верить собственному бланку.
    """
    for one in VACANCIES:
        questions = quiz_for(one.code)
        if one.needs:
            assert questions == (), f"{one.code}: диплом и тест разом"
            continue
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


# ---------- мест столько, сколько их в городе ----------


async def test_a_taken_vacancy_is_gone_from_the_board(db):
    """Места кончились — доска говорит, кто их занял, и не берёт больше."""
    now = moment_at()
    for number in range(3):  # на почте три места
        one = await stand(db, make_player(300 + number, f"Почтальон-{number}"))
        await apply(db, one, "postman", right_answers("postman"), now)

    late = await stand(db, make_player(2, "Марла"))
    board = await vacancies(db, late, now)
    row = next(one for one in board if one["vacancy"].code == "postman")

    assert row["taken_seats"] == 3 and row["free_seats"] == 0
    assert "Почтальон-0" in row["taken_by"]
    with pytest.raises(WorkError, match="заняты все 3"):
        await apply(db, late, "postman", right_answers("postman"), now)


async def test_the_bar_hires_five_and_turns_away_the_sixth(db):
    """За стойкой пять мест: занявший не закрывает бар остальным."""
    now = moment_at()
    for number in range(5):
        one = await stand(db, make_player(400 + number, f"Бармен-{number}"))
        await apply(db, one, "bartender", right_answers("bartender"), now)

    board = await vacancies(db, await stand(db, make_player(500, "Марла")), now)
    row = next(one for one in board if one["vacancy"].code == "bartender")
    assert row["taken_seats"] == 5 and row["free_seats"] == 0

    late = await stand(db, make_player(501, "Опоздавший"))
    with pytest.raises(WorkError, match="заняты все 5") as refusal:
        await apply(db, late, "bartender", right_answers("bartender"), now)
    # Все имена в отказ не лезут: называем четверых и считаем остальных
    assert "и ещё 1" in str(refusal.value)


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
    assert [one["nickname"] for one in taken["bartender"]] == ["Тайлер"]


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
    assert [one["nickname"] for one in taken["bartender"]] == ["Тайлер"]


async def test_the_query_counts_the_seats_before_it_writes(db):
    """Мест бывает четыре, и считает их тот же запрос, что записывает.

    Раньше «одно место на город» держал уникальный индекс. Мест стало
    несколько, индекс пришлось снять — и счёт переехал в `WHERE` того же
    оператора: между проверкой и записью щели по-прежнему нет.
    """
    now = moment_at()
    fighters = []
    for number in range(4):
        one = await stand(db, make_player(100 + number, f"Боец-{number}"))
        fighters.append(one)

    # Два места: первые двое заняли, третьему отказали
    assert await db.take_job(fighters[0].user_id, "bank_cashier", now, now, 2)
    assert await db.take_job(fighters[1].user_id, "bank_cashier", now, now, 2)
    assert not await db.take_job(fighters[2].user_id, "bank_cashier", now, now, 2)

    taken = await db.taken_jobs()
    assert [one["nickname"] for one in taken["bank_cashier"]] == [
        "Боец-0", "Боец-1"
    ]
    # А на место с одним креслом второго не пускают и подавно
    assert await db.take_job(fighters[2].user_id, "bank_head", now, now, 1)
    assert not await db.take_job(fighters[3].user_id, "bank_head", now, now, 1)


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


def test_the_norm_pays_in_full_and_overtime_pays_double():
    """Норма — весь оклад, а каждый час сверх неё стоит две ставки.

    Пример владельца: норма десять часов, оклад триста. Ставка часа —
    тридцать, отработано двенадцать, значит триста оклада плюс два часа
    по шестьдесят.
    """
    bartender = get_vacancy("bartender")  # 250 за 10 часов

    assert bartender.payout(10) == 250
    assert bartender.overtime(10) == 0 and bartender.overtime_pay(10) == 0
    # Ставка 25 в час, сверхурочная — 50
    assert bartender.overtime(12) == 2
    assert bartender.overtime_pay(12) == 100
    assert bartender.payout(12) == 350

    trainer = get_vacancy("trainer")  # 300 за 10 часов — пример владельца
    assert trainer.hour_rate == 30
    assert trainer.overtime_pay(12) == 120
    assert trainer.payout(12) == 420
    # И дальше растёт ровно так же: восемь смен за неделю — шесть часов сверх
    assert trainer.payout(16) == 300 + 6 * 60

    # На почте норма та же десятка при двухстах пятидесяти: ставка
    # двадцать пять, и переработка считается с неё же
    postman = get_vacancy("postman")
    assert postman.hour_rate == 25 and postman.payout(10) == 250
    assert postman.payout(12) == 250 + 2 * 50


def test_the_overtime_rate_is_the_exact_one_not_the_one_on_the_board():
    """Сверхурочные считают по точной ставке, а не по витринной.

    `per_hour` округлена до десятых — ею сравнивают вакансии на доске.
    Если считать деньги по ней, расчёт разойдётся с обещанным: у места
    с нормой в три часа и жалованьем в двести ставка 66.(6), витрина
    показывает 66.7, и на шести часах переработки лишний кредит берётся
    из воздуха.
    """
    from bot.game.work import Vacancy

    odd = Vacancy("odd", "Странное место", "🙃", salary=200, hours=3, place="bar")

    assert odd.per_hour == 66.7, "витрина округляет до десятых"
    assert odd.hour_rate == pytest.approx(200 / 3)
    # Три часа сверх нормы по двойной ставке — ровно четыреста
    assert odd.overtime_pay(6) == 400
    assert odd.payout(6) == 600


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


async def test_overtime_is_paid_double_on_monday(db):
    """Переработка приходит в понедельник отдельной строкой расчёта.

    Шесть смен за неделю — двенадцать часов при норме в десять. Платят
    оклад и два часа по двойной ставке; в расчёте это сказано словами,
    чтобы боец видел, за что ему пришло больше обычного.
    """
    player = await hired(db, make_player(location=BAR), now=moment_at(day=5))
    # Шесть смен по два часа: пять дней подряд и ночная смена понедельника
    for day in range(5, 11):
        await start_shift(db, player, moment_at(day=day, hour=12))
    assert player.job_minutes == 720

    said = await settle(db, player, moment_at(day=12, hour=9))

    # 250 оклада и 2 часа по 50 — ставка часа 25, сверхурочная вдвое
    assert player.account_balance == 350
    assert "350" in said and "сверхурочные 100" in said
    assert "2 ч сверх нормы" in said
    assert player.job_code == "bartender"


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


async def test_the_friday_hire_is_not_sacked_for_a_week_he_had_no_time_for(db):
    """Устроился в пятницу — до понедельника норму не закрыть.

    Две смены по два часа — всё, что остаётся на субботу и воскресенье,
    а половина нормы бармена это пять часов. Увольнять за то, чего не
    дали сделать, нечестно.
    """
    player = await hired(db, make_player(location=BAR), now=moment_at(day=9))
    await start_shift(db, player, moment_at(day=10, hour=12))  # два часа

    said = await settle(db, player, moment_at(day=12, hour=9))

    assert player.job_code == "bartender", "уволен за короткую неделю"
    assert player.account_balance == 50, "за отработанное всё равно платят"
    assert "уволили" not in said
    assert "Неделя была короткой" in said
    assert await db.taken_jobs(), "место осталось за ним"


async def test_the_thursday_hire_is_judged_like_everyone(db):
    """Четверг — ещё не короткая неделя: три дня по две смены это шесть
    часов, и половину нормы закрыть можно."""
    player = await hired(db, make_player(location=BAR), now=moment_at(day=8))
    await start_shift(db, player, moment_at(day=8, hour=12))  # два часа

    said = await settle(db, player, moment_at(day=12, hour=9))

    assert "уволили" in said
    assert player.job_code == ""


async def test_the_short_week_is_forgiven_once_and_not_twice(db):
    """Поблажка — только той неделе, в которую приняли.

    Дальше недели у него полные, и спрос обычный: иначе устроившийся в
    пятницу держал бы место, не работая вовсе.
    """
    player = await hired(db, make_player(location=BAR), now=moment_at(day=9))
    assert "уволили" not in await settle(db, player, moment_at(day=12, hour=9))
    assert player.job_code == "bartender"

    # Следующая неделя целая, и за пустую уже увольняют
    said = await settle(db, player, moment_at(day=19, hour=9))

    assert "уволили" in said
    assert player.job_code == ""


def test_the_days_to_payday_count_what_is_left_whole():
    """Сколько дней у принятого остаётся до расчёта: день приёма не в счёт."""
    from bot.game.work import days_to_payday, hours_before_payday, week_of

    week = week_of(moment_at(day=5, hour=12))
    left = {
        moment_at(day=day, hour=14): days_to_payday(
            moment_at(day=day, hour=14), week
        )
        for day in range(5, 12)
    }
    assert list(left.values()) == [6, 5, 4, 3, 2, 1, 0]
    # Пятница, суббота, воскресенье — меньше пяти часов, норму бармена
    # в такую неделю не закрыть
    for day in (9, 10, 11):
        assert hours_before_payday(moment_at(day=day, hour=14), week) < 5


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


async def test_the_agency_shows_every_vacancy_by_the_handle(client, db):
    await stand(db, make_player())

    body = await (await client.get("/api/hr", headers=headers())).json()

    # Полицейских мест на доске агентства нет: в полицию приходят в
    # полицию, и отдельная дверь у неё не для красоты
    assert len(body["vacancies"]) == len(agency_board())
    assert not any(
        one["code"].startswith("police_") for one in body["vacancies"]
    )
    row = body["vacancies"][0]
    assert {"code", "title", "salary", "hours", "place_title"} <= set(row)
    # Вопросов на доске нет: их выдают по заявке. Есть только признак,
    # что тест у места вообще бывает
    assert "quiz" not in row
    assert row["has_quiz"] is True
    assert not any("text" in str(value) for value in row.values())


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
    # Нанимаем этим часом, а не октябрьским: ручка читает настоящие
    # часы, и боец, нанятый в прошлую неделю, к первому же запросу
    # оказался бы уволен расчётом за невыполненную норму
    player = make_player(location=BAR)
    await hired(db, player, now=now_ts())

    body = await (await client.get("/api/work", headers=headers())).json()
    assert body["job"]["code"] == "bartender"
    assert body["can_start"] is True

    body = await (
        await client.post("/api/work", json={"action": "start"}, headers=headers())
    ).json()

    assert body["shift"]["seconds_left"] > 0
    assert body["can_start"] is False
    assert (await db.get_player(42)).shift_until > 0


async def test_the_screen_counts_the_overtime_before_payday(client, db):
    """Переработку видно на экране работы, а не только в расчёте.

    Боец должен понимать, за что ему придёт больше обычного, ещё до
    понедельника: часы сверх нормы, ставка такого часа и сама прибавка.
    """
    player = make_player(location=BAR)
    await hired(db, player, now=moment_at(day=5))
    for day in range(5, 11):  # шесть смен — двенадцать часов при норме в десять
        await start_shift(db, player, moment_at(day=day, hour=12))

    # Экран собираем тем же часом той же недели, а не настоящим: иначе
    # к этой строке неделя успевала бы кончиться, расчёт — пройти, а
    # бойца с невыполненной нормой — уволить
    end = moment_at(day=11, hour=12)
    job = (await build_work(db, await db.get_player(42), end))["job"]
    assert job["worked"] == 12 and job["overtime"] == 2
    assert job["hour_rate"] == 50, "час сверх нормы стоит две ставки"
    assert job["overtime_pay"] == 100 and job["payout"] == 350

    # А пока норма не перейдена, переработки нет вовсе
    player.job_minutes = 600
    await db.save_player(player)
    job = (await build_work(db, await db.get_player(42), end))["job"]
    assert job["overtime"] == 0 and job["overtime_pay"] == 0


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
    assert [one["nickname"] for one in taken["bartender"]] == ["Марла"]
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


# ---------- места под диплом ----------


def test_the_diploma_jobs_are_the_ones_the_city_promised():
    """Двадцать семь мест под диплом: дом, кол-во, требование, стаж, деньги."""
    rows = {one.code: one for one in VACANCIES if one.needs}
    assert len(rows) == 27

    def check(code, place, seats, needs, months, salary):
        one = rows[code]
        assert one.place == place, code
        assert one.seats == seats, code
        assert one.needs == needs, code
        assert one.months == months, code
        assert one.salary == salary, code

    check("bank_head", "bank", 1, "banking", 2, 1_500)
    check("bank_cashier", "bank", 6, "banking", 0, 600)
    check("bank_clerk", "bank", 4, "banking", 1, 1_000)
    check("mayor_adviser", "city_administration", 2, "gov", 1, 700)
    check("clerk", "city_administration", 4, "gov", 0, 500)
    check("police_chief", "vcpd", 1, "police", 2, 1_500)
    # Полицейская лестница: начальник один, офицеров и патрульных город
    # не считает — в патруль берут всех, кто кончил академию. Так же
    # устроены больница, часть и суд
    check("police_officer", "vcpd", 0, "police", 1, 700)
    check("police_patrol", "vcpd", 0, "police", 0, 500)
    check("head_doctor", "hospital", 1, "medic", 2, 1_500)
    check("district_doctor", "hospital", 0, "medic", 1, 700)
    check("orderly", "hospital", 0, "medic", 0, 500)
    check("army_head", "military_base", 1, "cadet", 2, 1_500)
    check("senior_officer", "military_base", 0, "cadet", 1, 700)
    check("junior_officer", "military_base", 0, "cadet", 0, 500)
    check("chief_judge", "courthouse", 1, "law", 2, 1_500)
    check("judge_aide", "courthouse", 0, "law", 1, 700)
    check("court_clerk", "courthouse", 0, "law", 0, 500)
    check("taxi_driver", "taxi_depot", 4, "licence", 0, 500)
    # Авторынок — те же продажи, что в салоне и страховой, и спрос тот
    # же: диплом управления, те же шестьсот и те же три места
    check("car_dealer", "used_car_market", 3, "management", 0, 600)
    # Разработка: один диплом на три ступени, и разделяет их стаж
    check("dev_senior", "office_building", 2, "it", 2, 1_500)
    check("dev_middle", "office_building", 4, "it", 1, 1_000)
    check("dev_junior", "office_building", 6, "it", 0, 600)
    # Диплом управления просят в шести домах. Пять из них — менеджеры
    # по продажам: одно место, пять дверей, всё одинаково. Шестой —
    # дилер авторынка: деньги и стаж те же, а мест два, потому что
    # рынок не контора
    managers = [one for one in rows.values() if one.needs == "management"]
    assert len(managers) == 6
    assert {one.place for one in managers} == {
        "car_dealership", "insurance_office", "driving_school",
        "strength_gym", "northern_wall_shop", "used_car_market",
    }
    # Шесть домов, одно место, всё одинаково — и зовётся оно везде
    # одинаково тоже: менеджер по продажам
    for one in managers:
        assert (one.seats, one.months, one.salary) == (3, 0, 600)
        assert one.title == "Менеджер по продажам"


def test_the_requirement_is_named_by_the_diploma_itself():
    """Доска зовёт документ так же, как зовёт его училище."""
    from bot.game.study import get_course
    from bot.game.work import get_vacancy

    assert get_vacancy("bank_head").education == (
        get_course("banking").diploma_title
    )
    assert get_vacancy("army_head").education == (
        get_course("cadet").diploma_title
    )
    # У таксиста не диплом, а права
    assert get_vacancy("taxi_driver").education == "Водительское удостоверение"
    assert get_vacancy("bartender").education == "не требуется"


def test_the_months_are_counted_down_not_up(db):
    """Полтора месяца — это один месяц опыта, а не два."""
    from bot.game.work import MONTH_SECONDS, months_count, months_of, plural_months

    # Падежи у числа разные: после «от» родительный, при счёте счётный.
    # «От 2 месяца» и «у тебя 1 месяца» — обе фразы одинаково кривые
    assert plural_months(1) == "месяца" and months_count(1) == "месяц"
    assert plural_months(2) == "месяцев" and months_count(2) == "месяца"
    assert plural_months(5) == "месяцев" and months_count(5) == "месяцев"

    assert months_of(0) == 0
    assert months_of(MONTH_SECONDS - 1) == 0
    assert months_of(MONTH_SECONDS) == 1
    assert months_of(MONTH_SECONDS * 3 // 2) == 1
    assert months_of(MONTH_SECONDS * 2) == 2


async def test_without_the_diploma_the_door_is_shut(db):
    """Диплома нет — не берут, и говорят, какого именно."""
    player = await stand(db, make_player(location="bank"))

    with pytest.raises(WorkError, match="Нужен документ"):
        await hire_at_house(db, player, "bank_cashier", moment_at())
    assert not player.works

    # С дипломом — берут, и без всякого теста: его сдали в училище
    player.diplomas = ["banking"]
    await db.save_player(player)
    place = await hire_at_house(db, player, "bank_cashier", moment_at())
    assert place.code == "bank_cashier"
    assert player.job_code == "bank_cashier"


async def test_the_taxi_asks_for_the_licence_not_a_diploma(db):
    """У таксопарка требование своё: права, а не диплом."""
    from bot.game.driving import School

    player = await stand(db, make_player())
    player.diplomas = ["banking", "gov", "cadet"]
    with pytest.raises(WorkError, match="Водительское удостоверение"):
        await apply(db, player, "taxi_driver", [], moment_at())

    player.school = School(licence_at=moment_at())
    attempt = await apply(db, player, "taxi_driver", [], moment_at())
    assert attempt.hired is True


async def test_the_top_seat_waits_for_the_experience(db):
    """Диплом есть, стажа нет — на управляющего не берут."""
    from bot.game.work import MONTH_SECONDS

    from bot.work_service import bid_for_post

    player = await stand(db, make_player(location="bank"))
    player.diplomas = ["banking"]

    with pytest.raises(
        WorkError, match="Нужен опыт от 2 месяцев, а у тебя 0 месяцев"
    ):
        await bid_for_post(db, player, "bank_head", moment_at())

    # Два месяца стажа — и заявку принимают. Дальше решает мэр: в
    # кресло начальника не устраиваются, в него назначают
    player.job_experience = 2 * MONTH_SECONDS
    await db.save_player(player)
    seat = await bid_for_post(db, player, "bank_head", moment_at())
    assert seat.appointed and await db.bids_of(player.user_id) == ["bank_head"]


async def test_the_experience_counts_the_current_job_too(db):
    """Опыт идёт, пока боец работает, — а не только после ухода."""
    from bot.game.work import MONTH_SECONDS

    player = await hired(db, make_player())
    hour = moment_at()
    assert player.experience_months(hour) == 0
    assert player.experience_months(hour + MONTH_SECONDS) == 1
    assert player.experience_months(hour + 3 * MONTH_SECONDS) == 3


async def test_the_experience_stays_when_the_job_is_gone(db):
    """Стаж — единственное, что боец уносит с работы."""
    from bot.game.work import MONTH_SECONDS

    player = await hired(db, make_player(location=BAR))
    later = moment_at() + 2 * MONTH_SECONDS

    await quit_job(db, player, later)

    assert not player.works
    assert player.job_experience >= 2 * MONTH_SECONDS
    assert player.experience_months(later) == 2
    fresh = await db.get_player(player.user_id)
    assert fresh.experience_months(later) == 2


async def test_the_patrol_takes_everyone_and_the_chief_is_one(db):
    """Патрульных город не считает, а в кресло начальника назначают."""
    from bot.game.work import MONTH_SECONDS
    from bot.work_service import hire_at_house

    now = moment_at()
    for number in range(6):
        one = await stand(db, make_player(200 + number, f"Боец-{number}"))
        one.diplomas = ["police"]
        one.location = "vcpd"
        await hire_at_house(db, one, "police_patrol", now)

    taken = await db.taken_jobs()
    assert len(taken["police_patrol"]) == 6, "шестому тоже нашлась форма"

    # А в кресло начальника в участке не устраиваются вовсе: оно одно
    # на город, и сажает в него мэр
    chief = await stand(db, make_player(250, "Первый"))
    chief.diplomas = ["police"]
    chief.location = "vcpd"
    chief.job_experience = 3 * MONTH_SECONDS
    with pytest.raises(WorkError, match="назначает мэр"):
        await hire_at_house(db, chief, "police_chief", now)


async def test_the_board_counts_the_free_seats(db):
    """Доска показывает, сколько кресел занято и кем."""
    now = moment_at()
    taken = await stand(db, make_player(300, "Марла"))
    taken.diplomas = ["banking"]
    taken.location = "bank"
    await db.save_player(taken)
    await hire_at_house(db, taken, "bank_cashier", now)

    seeker = await stand(db, make_player(301, "Тайлер"))
    rows = {
        one["vacancy"].code: one
        for one in await vacancies(db, seeker, now, board=VACANCIES)
    }

    row = rows["bank_cashier"]
    assert row["taken_seats"] == 1 and row["free_seats"] == 5
    assert row["holders"] == ["Марла"]
    # У соискателя без диплома место закрыто, и сказано чем
    assert "Нужен документ" in row["lacks"]


# ---------- служба и семья ----------


def test_the_light_side_covers_the_badge_the_gown_and_the_coat():
    """Светлая сторона — участок, часть, больница и суд. И только они.

    Пометка стоит на месте, а не на доме: в клане не состоят ни
    патрульный, ни санитар, ни секретарь суда, — а секретарь
    администрации состоит сколько угодно.
    """
    lawful = {one.code for one in VACANCIES if one.lawful}
    assert lawful == {
        "police_chief", "police_officer", "police_patrol",
        "army_head", "senior_officer", "junior_officer",
        "head_doctor", "district_doctor", "orderly",
        "chief_judge", "judge_aide", "court_clerk",
    }
    # Администрация и банк работают со всеми: бумагу и кредит мафиози
    # выдают, как и всякому
    for code in ("clerk", "mayor_adviser", "bank_head", "bank_cashier"):
        assert not get_vacancy(code).lawful, code


def test_the_service_and_the_vacancy_agree_on_the_light_side():
    """Светлая сторона описана дважды — и обе записи обязаны сойтись."""
    from bot.game.services import SERVICES, service_of_job

    for one in VACANCIES:
        service = service_of_job(one.code)
        if service is None:
            assert not one.lawful, f"{one.code}: место светлое, а службы нет"
            continue
        assert one.lawful == service.lawful, (
            f"{one.code}: место {one.lawful}, а служба {service.code} "
            f"{service.lawful}"
        )
    # И у каждой светлой службы есть хоть одно место — кроме тех, чьи
    # дома ещё не открыты
    opened = [one for one in SERVICES if one.jobs]
    assert {one.code for one in opened} == {
        "police", "army", "medical", "court", "administration"
    }
    # Право силы — не то же самое, что светлая сторона: на точки города
    # выходят трое, а в семью не берут пятерых
    from bot.game.services import FORCE, LAWFUL

    assert FORCE == {"police", "army", "fbi"}
    assert FORCE < LAWFUL


async def test_the_mafia_is_not_taken_into_the_service(db):
    """Состоишь в семье — в полицию и в армию не возьмут."""
    from bot.game.clans import Clan, Rank, Standing

    player = await stand(db, make_player())
    player.diplomas = ["police", "cadet"]
    player.job_experience = 0
    player.clan = Standing(
        clan=Clan(id=1, name="Коза Ностра", don=42, created_at=moment_at(),
                  sign="black_ravens", members=1),
        rank=Rank.DON,
    )

    player.diplomas = ["police", "cadet", "medic", "law"]
    for code, house in (
        ("police_patrol", "vcpd"),
        ("junior_officer", "military_base"),
        ("orderly", "hospital"),
        ("court_clerk", "courthouse"),
    ):
        player.location = house
        await db.save_player(player)
        with pytest.raises(WorkError, match="не берут тех, кто в семье"):
            await hire_at_house(db, player, code, moment_at())
    assert not player.works
    player.location = HR
    await db.save_player(player)

    # А барменом — пожалуйста: за стойкой светлой стороны нет
    assert (
        await apply(db, player, "bartender", right_answers("bartender"), moment_at())
    ).hired


async def test_the_board_says_why_the_service_is_shut(db):
    """Доска не прячет кнопку, а говорит, что мешает."""
    from bot.game.clans import Clan, Rank, Standing
    from bot.work_service import lacking

    player = await stand(db, make_player())
    player.diplomas = ["police"]
    player.clan = Standing(
        clan=Clan(id=1, name="Коза Ностра", don=42, created_at=moment_at(),
                  sign="black_ravens", members=1),
        rank=Rank.SOLDIER,
    )
    said = lacking(player, get_vacancy("police_patrol"), moment_at())
    assert "не берут тех, кто в семье" in said and "Коза Ностра" in said


async def test_the_office_building_hires_three_steps_of_developers(db):
    """Бизнес-центр: три ступени разработки на один IT-диплом.

    Доска агентства, а не дом: в центр приходят по вакансии, как в банк
    и в больницу. Диплом один на всех, разделяет их стаж.
    """
    from bot.game.work import agency_board

    devs = [one for one in agency_board() if one.place == "office_building"]

    assert [one.code for one in devs] == [
        "dev_senior", "dev_middle", "dev_junior"
    ]
    assert all(one.needs == "it" for one in devs)
    assert [one.months for one in devs] == [2, 1, 0]
    assert [one.salary for one in devs] == [1_500, 1_000, 600]
    # Каждому диплом назван так же, как его зовёт университет
    from bot.game.study import get_course

    assert devs[0].education == get_course("it").diploma_title


async def test_a_developer_is_hired_with_the_it_diploma(db):
    """Без диплома в разработку не берут, с диплом́ом — берут младшим."""
    from bot.game.study import get_course
    from bot.work_service import apply, lacking

    moment = moment_at(day=5, hour=10)
    seeker = await stand(db, make_player(location="hr_agency"))
    junior = get_vacancy("dev_junior")

    assert "Диплом об IT-специальности" in lacking(seeker, junior, moment)
    # Теста у места под диплом нет: бумага и есть его тест
    with pytest.raises(WorkError, match="Диплом"):
        await apply(db, seeker, "dev_junior", [], now=moment)

    seeker.diplomas.append(get_course("it").diploma)
    await db.save_player(seeker)
    attempt = await apply(db, seeker, "dev_junior", [], now=moment)

    assert attempt.hired and attempt.vacancy.title == "Младший разработчик"
    assert (await db.get_player(seeker.user_id)).job_code == "dev_junior"
    # А на старшего не берут и с дипломом: нужен стаж
    senior = get_vacancy("dev_senior")
    assert "от 2 месяцев" in lacking(seeker, senior, moment)


# ---------- кресла начальников: заявка и назначение ----------


async def seat_mayor(db: Database, player: Player) -> Player:
    """Посадить бойца в кресло мэра — назначать может только он."""
    await db.seat_mayor(player.user_id, moment_at(), "2026-10-04")
    return player


def with_diploma(user_id: int, name: str, diploma: str, **over) -> Player:
    one = make_player(user_id, name, location=over.pop("location", "bank"))
    one.diplomas = [diploma]
    # Стаж начальнику нужен: два месяца отложенного опыта
    one.job_experience = over.pop("experience", 3 * MONTH)
    return one


MONTH = 30 * 24 * 60 * 60


async def test_the_chief_chairs_are_appointed_not_taken():
    """Пять кресел в городе назначает мэр, и у каждого своя вилка."""
    appointed = [one for one in VACANCIES if one.appointed]

    assert {one.code for one in appointed} == {
        "bank_head", "police_chief", "head_doctor", "army_head", "chief_judge",
    }
    for one in appointed:
        assert (one.salary, one.salary_max) == (1_500, 2_300), one.code
        assert one.seats == 1, one.code
        # Назначают в доме: в агентстве таких мест нет вовсе
        assert one.at_house, one.code
        assert one.fits_pay(1_500) and one.fits_pay(2_300)
        assert not one.fits_pay(1_499) and not one.fits_pay(2_301)
    assert get_vacancy("bartender").appointed is False
    assert get_vacancy("bartender").fits_pay(250)


async def test_the_agency_does_not_hire_where_the_house_hires(db):
    """Место дома в агентстве не занимают — даже запросом мимо страницы."""
    seeker = await stand(db, with_diploma(60, "Кассир", "banking"))

    with pytest.raises(WorkError, match="приходи в банк"):
        await apply(db, seeker, "bank_cashier", [], moment_at())
    # А в самом банке — пожалуйста
    place = await hire_at_house(db, seeker, "bank_cashier", moment_at())
    assert place.code == "bank_cashier"
    assert (await db.get_player(60)).job_code == "bank_cashier"


async def test_a_bid_is_not_a_job(db):
    """Заявка ничего не занимает: кресло пустует, боец работает дальше."""
    from bot.work_service import bid_for_post

    one = await stand(db, with_diploma(61, "Соискатель", "banking"))
    await hire_at_house(db, one, "bank_cashier", moment_at())

    await bid_for_post(db, one, "bank_head", moment_at())

    assert await db.bids_of(61) == ["bank_head"]
    fresh = await db.get_player(61)
    assert fresh.job_code == "bank_cashier", "заявка не снимает с места"
    assert not (await db.taken_jobs()).get("bank_head")


async def test_a_bid_without_the_diploma_is_refused(db):
    """На кресло подают с бумагой и стажем, а не с одним желанием."""
    from bot.work_service import bid_for_post

    green = await stand(db, make_player(62, "Новичок", location="bank"))
    with pytest.raises(WorkError, match="Нужен документ"):
        await bid_for_post(db, green, "bank_head", moment_at())

    fresh = await stand(
        db, with_diploma(63, "Выпускник", "banking", experience=0)
    )
    with pytest.raises(WorkError, match="опыт"):
        await bid_for_post(db, fresh, "bank_head", moment_at())


async def test_the_mayor_seats_the_chief_and_names_the_pay(db):
    """Мэр сажает подавшего и называет жалованье из вилки."""
    from bot.work_service import (
        appoint, bid_for_post, candidates_for, my_vacancy,
    )

    mayor = await seat_mayor(db, await stand(db, make_player(70, "Мэр")))
    one = await stand(db, with_diploma(71, "Банкир", "banking"))
    await bid_for_post(db, one, "bank_head", moment_at())

    people = await candidates_for(db, "bank_head", moment_at())
    assert [row["user_id"] for row in people] == [71]
    assert people[0]["asked"] is True and not people[0]["lacks"]

    seat, chosen = await appoint(db, mayor, "bank_head", 71, 2_000, moment_at())

    assert seat.salary == 2_000, "вилка названа мэром"
    assert chosen.job_code == "bank_head" and chosen.job_salary == 2_000
    assert await db.bids_of(71) == [], "заявки сняты назначением"
    # И платят ему по назначенному, а не по нижнему краю вилки
    assert seat.payout(seat.hours) == 2_000
    assert my_vacancy(await db.get_player(71)).salary == 2_000


# ---------- мэр от города: назначает сам, по заявкам и стажу ----------


async def seat_city(db: Database) -> None:
    """Кресло мэра у города: живого человека в нём нет."""
    from bot.game.city import MAYOR_ID

    await db.seat_mayor(MAYOR_ID, moment_at(), "2026-10-04")


async def test_the_city_seats_the_longest_served_of_those_who_asked(db):
    """Городской мэр сажает подавшего с самым долгим стажем."""
    from bot.work_service import bid_for_post, city_appoints

    await seat_city(db)
    green = await stand(
        db, with_diploma(80, "Молодой", "banking", experience=2 * MONTH)
    )
    old_hand = await stand(
        db, with_diploma(81, "Старый", "banking", experience=5 * MONTH)
    )
    # Первая заявка уже зовёт город: пустое кресло он занимает сразу
    await bid_for_post(db, green, "bank_head", moment_at())
    assert (await db.get_player(80)).job_code == "bank_head"

    # А пришедший со бо́льшим стажем сменяет сидящего: решает стаж, а
    # не то, кто первый нажал
    await bid_for_post(db, old_hand, "bank_head", moment_at())

    assert (await db.get_player(81)).job_code == "bank_head"
    assert (await db.get_player(80)).job_code == "", "сидевшего сменили"
    assert await city_appoints(db, moment_at()) == [], "менять больше некого"
    assert await db.bids_of(81) == [], "заявки сняты назначением"


async def test_the_city_keeps_the_chief_against_a_shorter_record(db):
    """Сидящего в кресле меньшим стажем не сменить."""
    from bot.work_service import bid_for_post, city_appoints

    await seat_city(db)
    chief = await stand(
        db, with_diploma(87, "Начальник", "banking", experience=6 * MONTH)
    )
    await bid_for_post(db, chief, "bank_head", moment_at())
    assert (await db.get_player(87)).job_code == "bank_head"

    short = await stand(
        db, with_diploma(88, "Соискатель", "banking", experience=2 * MONTH)
    )
    await bid_for_post(db, short, "bank_head", moment_at())

    assert (await db.get_player(87)).job_code == "bank_head"
    assert (await db.get_player(88)).job_code == ""
    # Заявка не пропала: сядет он, когда стажа станет больше
    assert await db.bids_of(88) == ["bank_head"]
    assert await city_appoints(db, moment_at()) == []


async def test_the_city_pays_by_the_years_served(db):
    """Жалованье от города — по стажу: за месяц сверх четверть вилки."""
    from bot.work_service import bid_for_post, city_pay

    head = get_vacancy("bank_head")
    assert city_pay(head, 2) == 1_500, "в самый край вилки"
    assert city_pay(head, 3) == 1_700
    assert city_pay(head, 6) == 2_300, "верх вилки, и выше не бывает"
    assert city_pay(head, 60) == 2_300
    assert city_pay(get_vacancy("bartender"), 9) == get_vacancy(
        "bartender"
    ).salary, "твёрдый оклад стажем не двигают"

    await seat_city(db)
    one = await stand(
        db, with_diploma(82, "Банкир", "banking", experience=4 * MONTH)
    )
    await bid_for_post(db, one, "bank_head", moment_at())

    assert (await db.get_player(82)).job_salary == 1_900


async def test_the_city_takes_only_those_who_asked(db):
    """Город не призывает: не подал заявку — не назначат, хоть с дипломом."""
    from bot.work_service import candidates_for, city_appoints

    await seat_city(db)
    await stand(db, with_diploma(83, "Банкир", "banking"))

    # Живому мэру такого показывают: ему можно сажать любого с бумагой
    people = await candidates_for(db, "bank_head", moment_at())
    assert [row["user_id"] for row in people] == [83]
    assert people[0]["asked"] is False

    assert await city_appoints(db, moment_at()) == []
    assert (await db.get_player(83)).job_code == "", "кресло пустует"


async def test_the_live_mayor_appoints_for_himself(db):
    """Пока в кресле мэра человек, город в назначения не лезет."""
    from bot.work_service import bid_for_post, city_appoints

    mayor = await seat_mayor(db, await stand(db, make_player(84, "Мэр")))
    one = await stand(db, with_diploma(85, "Банкир", "banking"))
    await bid_for_post(db, one, "bank_head", moment_at())

    assert await city_appoints(db, moment_at()) == []
    assert (await db.get_player(85)).job_code == ""
    assert await db.bids_of(85) == ["bank_head"], "заявка ждёт мэра"
    del mayor


async def test_the_city_fills_the_chair_the_work_screen_looks_at(db):
    """Экран работы — тот же ленивый сбор: взглянул, и кресло занято."""
    from bot.work_service import city_appoints

    await seat_city(db)
    one = await stand(db, with_diploma(86, "Банкир", "banking"))
    await db.place_bid(86, "bank_head", moment_at())
    assert (await db.get_player(86)).job_code == "", "заявка легла молча"

    await build_work(db, await db.get_player(86), moment_at())

    assert (await db.get_player(86)).job_code == "bank_head"
    assert await city_appoints(db, moment_at()) == []


async def test_the_pay_must_lie_inside_the_fork(db):
    """Ни ниже вилки, ни выше: кресло стоит столько, сколько стоит."""
    from bot.work_service import appoint, bid_for_post

    mayor = await seat_mayor(db, await stand(db, make_player(72, "Мэр")))
    one = await stand(db, with_diploma(73, "Банкир", "banking"))
    await bid_for_post(db, one, "bank_head", moment_at())

    for wrong in (1_000, 2_400):
        with pytest.raises(WorkError, match="от 1500 до 2300"):
            await appoint(db, mayor, "bank_head", 73, wrong, moment_at())


async def test_nobody_but_the_mayor_appoints(db):
    """Назначает мэр. Не мэр — не назначает, как бы ни просил."""
    from bot.work_service import appoint

    await seat_mayor(db, await stand(db, make_player(74, "Мэр")))
    stranger = await stand(db, make_player(75, "Прохожий"))
    await stand(db, with_diploma(76, "Банкир", "banking"))

    with pytest.raises(WorkError, match="Назначает начальников мэр"):
        await appoint(db, stranger, "bank_head", 76, 1_500, moment_at())


async def test_without_bids_the_mayor_picks_from_everyone_with_the_paper(db):
    """Не подал никто — мэр смотрит на всех с дипломом. Нет и таких — пусто."""
    from bot.work_service import candidates_for

    await stand(db, make_player(77, "Без бумаги"))
    assert await candidates_for(db, "chief_judge", moment_at()) == []

    lawyer = with_diploma(78, "Юрист", "law", location="courthouse")
    await stand(db, lawyer)
    people = await candidates_for(db, "chief_judge", moment_at())

    assert [row["user_id"] for row in people] == [78]
    assert people[0]["asked"] is False, "заявки не было — город показал всех"


async def test_the_new_chief_replaces_the_old_one_with_a_payslip(db):
    """Сменили — значит рассчитали: прежний уходит с деньгами за недоделю."""
    from bot.work_service import appoint

    mayor = await seat_mayor(db, await stand(db, make_player(80, "Мэр")))
    first = await stand(db, with_diploma(81, "Первый", "banking"))
    second = await stand(db, with_diploma(82, "Второй", "banking"))

    await appoint(db, mayor, "bank_head", 81, 1_500, moment_at())
    await appoint(db, mayor, "bank_head", 82, 2_300, moment_at())

    before = await db.get_player(81)
    after = await db.get_player(82)
    assert not before.works and before.job_salary == 0
    assert after.job_code == "bank_head" and after.job_salary == 2_300
    assert len((await db.taken_jobs())["bank_head"]) == 1


# ---------- суд ----------


def test_the_courthouse_opened_with_three_benches():
    """Суд открылся: три места под диплом юриста и светлая сторона."""
    from bot.game.locations import Service as Door, get_location
    from bot.game.study import get_course

    court = get_location("courthouse")
    assert court.works and court.allows(Door.WORK)

    rows = [one for one in VACANCIES if one.place == "courthouse"]
    assert [one.code for one in rows] == [
        "chief_judge", "judge_aide", "court_clerk"
    ]
    for one in rows:
        assert one.needs == "law", one.code
        assert one.lawful, f"{one.code}: мантия и семья несовместимы"
        assert one.at_house, f"{one.code}: в суд приходят в суд"
        assert one.hours == STAFF_HOURS

    # Диплом выдаёт университет, и зовётся он так же, как на доске
    law = get_course("law")
    assert (law.price, law.weeks, law.place) == (1_800, 2, "university")
    assert get_vacancy("court_clerk").education == law.diploma_title


async def test_the_court_hires_in_the_court(db):
    """Секретарём суда берут в суде, а не в агентстве."""
    clerk = await stand(db, with_diploma(90, "Секретарь", "law", location="courthouse", experience=0))

    with pytest.raises(WorkError, match="приходи в суд"):
        await apply(db, clerk, "court_clerk", [], moment_at())

    place = await hire_at_house(db, clerk, "court_clerk", moment_at())
    assert place.code == "court_clerk"
    assert (await db.get_player(90)).job_code == "court_clerk"


# ---------- экраны ----------


async def test_the_house_screen_offers_the_job_and_the_bid(db):
    """Экран банка: кассира берут кнопкой, на управляющего подают заявку."""
    from bot.webapp.work import build_work

    seeker = await stand(db, with_diploma(91, "Соискатель", "banking"))
    body = await build_work(db, seeker, moment_at())

    rows = {one["code"]: one for one in body["vacancies"]}
    assert set(rows) == {"bank_head", "bank_cashier", "bank_clerk"}

    cashier = rows["bank_cashier"]
    assert cashier["at_house"] and cashier["can_take"] and not cashier["appointed"]
    assert cashier["pay_text"] == "600 💰"

    chief = rows["bank_head"]
    assert chief["appointed"] and chief["can_bid"] and not chief["can_take"]
    assert chief["pay_text"] == "1500–2300 💰"
    assert chief["bid"] is False


async def test_only_the_mayor_sees_the_chairs(db):
    """Кресла с кандидатами уезжают на экран мэра и ничей больше."""
    from bot.webapp.elections import seats_payload
    from bot.work_service import bid_for_post

    mayor = await seat_mayor(db, await stand(db, make_player(92, "Мэр")))
    one = await stand(db, with_diploma(93, "Банкир", "banking"))
    await bid_for_post(db, one, "bank_head", moment_at())

    seats = {
        row["code"]: row for row in await seats_payload(db, mayor, moment_at())
    }
    assert len(seats) == 5
    chair = seats["bank_head"]
    assert chair["pay_min"] == 1_500 and chair["pay_max"] == 2_300
    assert chair["holder"] == {}
    assert [one["user_id"] for one in chair["candidates"]] == [93]
