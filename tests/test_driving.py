"""Автошкола: курс, экзамен по билету и водительское удостоверение.

Проверяем пять вещей и в таком порядке важности:

1. **Ответы не уезжают на страницу.** Ни верный вариант, ни пояснение к
   нему: иначе экзамен сдаётся чтением ответа мини-аппа.
2. **Срок учёбы.** Три полных дня после записи, и ни часом раньше.
3. **Часы приёма.** По средам и субботам с 12 до 15, и только там, где
   учат, — в автошколе.
4. **Счёт ошибок.** Одна прощается, вторая кончает экзамен на месте.
5. **Две двери.** Учатся в автошколе, права получают в участке, и ни одно
   из этих мест не заменяет другое.
"""

from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.content.pdd import TICKET, ask, question
from bot.database import Database
from bot.driving_service import (
    SchoolError,
    answer,
    current_question,
    enroll,
    start_exam,
    take_licence,
)
from bot.game.classes import Stats
from bot.game.clock import MOSCOW
from bot.game.driving import (
    COURSE_PRICE,
    EXAM_MINUTES,
    EXAM_SECONDS,
    MISTAKES_ALLOWED,
    STUDY_DAYS,
    STUDY_SECONDS,
    TICKET_SIZE,
    School,
    exam_window,
    licence_number,
    next_exam,
)
from bot.game.locations import Service, get_location, where_to
from bot.models import Player
from bot.webapp.documents import build_documents
from bot.webapp.driving import build_police, build_school, licence_document
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

SCHOOL = "driving_school"
POLICE = "vcpd"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(location: str = SCHOOL, credits: int = 1000) -> Player:
    return Player(
        user_id=42, nickname="Тайлер", class_code="warrior", level=5,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


def moment_at(day: int = 14, hour: int = 13, minute: int = 0) -> int:
    """Миг по московским часам. 14 октября 2026 — среда, день приёма."""
    return int(datetime(2026, 10, day, hour, minute, tzinfo=MOSCOW).timestamp())


async def enrolled(db: Database, player: Player, when: int) -> Player:
    """Боец, записанный на курс в названный час."""
    await db.save_player(player)
    await enroll(db, player, when)
    return player


async def sitting(db: Database, player: Player, when: int = 0) -> int:
    """Боец за билетом: записан неделю назад, экзамен идёт. Отдаём час."""
    moment = when or moment_at()
    await enrolled(db, player, moment - STUDY_SECONDS - 60)
    await start_exam(db, player, moment)
    return moment


def right_answer(number: int) -> int:
    return TICKET[number].answer


def wrong_answer(number: int) -> int:
    """Любой вариант мимо верного."""
    one = TICKET[number]
    return 0 if one.answer != 0 else 1


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- билет ----------


def test_the_ticket_holds_sixteen_questions_with_pictures():
    """Билет приехал целиком: шестнадцать вопросов, у каждого своя картинка."""
    from bot.game.art import PDD

    assert len(TICKET) == TICKET_SIZE == 16
    pictures = set()
    for number, one in enumerate(TICKET):
        assert one.text and one.comment, number
        assert len(one.options) == 4, one.text
        assert 0 <= one.answer < len(one.options), one.text
        assert one.image.startswith(f"{PDD}/"), one.text
        pictures.add(one.image)
    assert len(pictures) == len(TICKET), "две картинки на один билет"


def test_the_page_never_sees_the_right_answer():
    """Наружу уезжают вопрос, картинка и варианты — и больше ничего.

    Верный номер и пояснение к нему остаются на сервере: уехав однажды,
    они превратили бы экзамен в чтение ответа мини-аппа.
    """
    for number, one in enumerate(TICKET):
        shown = ask(number)
        assert set(shown) == {"number", "total", "text", "image", "options"}
        assert shown["number"] == number + 1 and shown["total"] == 16
        assert shown["options"] == list(one.options)
        assert one.comment not in str(shown)


def test_a_question_outside_the_ticket_is_nothing():
    assert question(0) is TICKET[0]
    assert question(15) is TICKET[15]
    assert question(16) is None and question(-1) is None


# ---------- правила ----------


def test_the_school_and_the_police_each_hold_their_own_door():
    """Учатся в автошколе, права получают в участке."""
    assert where_to(Service.SCHOOL).code == SCHOOL
    assert where_to(Service.POLICE).code == POLICE
    assert get_location(SCHOOL).allows(Service.SCHOOL)
    assert get_location(POLICE).allows(Service.POLICE)
    # Автошкола заработала: записки «скоро здесь появится» у неё больше нет
    assert not get_location(SCHOOL).soon
    # А участку есть что обещать сверх прав: дежурная часть и розыск
    assert get_location(POLICE).soon


def test_the_course_costs_three_hundred_and_takes_three_days():
    assert COURSE_PRICE == 300
    assert STUDY_DAYS == 3
    assert STUDY_SECONDS == 3 * 24 * 3600


def test_the_exam_is_held_on_wednesdays_and_saturdays_from_noon():
    """По средам и субботам с 12:00 до 15:00 мск, и ни часом больше."""
    # 14 октября 2026 — среда, 17-е — суббота
    assert exam_window(moment_at(14, 11)) is None
    assert exam_window(moment_at(14, 12)) is not None
    assert exam_window(moment_at(14, 14, 59)) is not None
    assert exam_window(moment_at(14, 15)) is None
    assert exam_window(moment_at(15, 13)) is None, "в четверг не принимают"
    assert exam_window(moment_at(17, 13)) is not None, "суббота — день приёма"

    # После среды ближайший экзамен — суббота того же часа
    when = datetime.fromtimestamp(next_exam(moment_at(14, 15, 30)).start, MOSCOW)
    assert (when.weekday(), when.hour) == (5, 12)


def test_the_ticket_gives_a_quarter_of_an_hour_and_forgives_one_mistake():
    assert EXAM_MINUTES == 15 and EXAM_SECONDS == 900
    assert MISTAKES_ALLOWED == 1


def test_the_study_runs_three_full_days_not_three_calendar_ones():
    """Записался в среду вечером — к субботнему вечеру отучился."""
    school = School(course_at=moment_at(14, 20))

    assert not school.studied(moment_at(17, 19, 59)), "три дня ещё не полные"
    assert school.studied(moment_at(17, 20))
    assert school.study_left(moment_at(15, 20)) == 2 * 24 * 3600


def test_the_licence_number_is_the_same_every_time_it_is_asked():
    """Номер считается от бойца и часа выдачи, а не хранится."""
    issued = moment_at()

    assert licence_number(42, issued) == licence_number(42, issued)
    assert licence_number(42, issued) != licence_number(43, issued)
    series, region = licence_number(42, issued).split(" ", 1)
    assert series.isdigit() and len(region.split(" ")[1]) == 6


# ---------- запись на курс ----------


async def test_the_course_is_paid_for_and_starts_the_clock(db):
    player = make_player()
    await db.save_player(player)

    await enroll(db, player, moment_at())

    assert player.credits == 1000 - COURSE_PRICE
    assert player.school.enrolled and player.school.course_at == moment_at()
    # И записалось это в базу, а не только в память
    again = await db.get_player(player.user_id)
    assert again.school.course_at == moment_at()


async def test_the_course_is_signed_for_in_the_school_and_only_there(db):
    player = make_player(location=POLICE)
    await db.save_player(player)

    with pytest.raises(SchoolError, match="автошколе"):
        await enroll(db, player, moment_at())
    assert player.credits == 1000


async def test_there_is_no_second_course_for_the_same_fighter(db):
    player = await enrolled(db, make_player(), moment_at())

    with pytest.raises(SchoolError, match="уже записан"):
        await enroll(db, player, moment_at(14, 14))
    assert player.credits == 1000 - COURSE_PRICE


async def test_an_empty_purse_leaves_the_course_unsigned(db):
    player = make_player(credits=COURSE_PRICE - 1)
    player.pay_from = "cash"
    await db.save_player(player)

    with pytest.raises(SchoolError, match="стоит"):
        await enroll(db, player, moment_at())
    assert not player.school.enrolled


# ---------- допуск к экзамену ----------


async def test_the_exam_waits_for_the_three_days_to_pass(db):
    player = await enrolled(db, make_player(), moment_at(14, 13))

    with pytest.raises(SchoolError, match="Курс идёт"):
        await start_exam(db, player, moment_at(17, 12, 59))


async def test_the_exam_is_only_open_in_its_hours(db):
    player = make_player()
    await enrolled(db, player, moment_at(10, 10))  # суббота, 10 октября

    with pytest.raises(SchoolError, match="принимают"):
        await start_exam(db, player, moment_at(14, 11))  # среда, но до полудня
    # А в полдень — садись
    await start_exam(db, player, moment_at(14, 12))
    assert player.school.sitting(moment_at(14, 12))


async def test_the_exam_is_sat_in_the_school_and_not_in_the_station(db):
    player = make_player()
    await enrolled(db, player, moment_at(10, 10))
    player.location = POLICE

    with pytest.raises(SchoolError, match="автошколе"):
        await start_exam(db, player, moment_at())


async def test_the_exam_needs_the_course_first(db):
    player = make_player()
    await db.save_player(player)

    with pytest.raises(SchoolError, match="запишись"):
        await start_exam(db, player, moment_at())


# ---------- сам экзамен ----------


async def test_the_questions_come_one_by_one(db):
    player = make_player()
    moment = await sitting(db, player)

    first = current_question(player, moment)
    assert first["number"] == 1 and first["total"] == 16

    await answer(db, player, right_answer(0), moment)
    second = current_question(player, moment)
    assert second["number"] == 2
    assert second["text"] == TICKET[1].text


async def test_one_mistake_is_forgiven_and_the_second_ends_it(db):
    player = make_player()
    moment = await sitting(db, player)

    first = await answer(db, player, wrong_answer(0), moment)
    assert (first.right, first.done, first.wrong) == (False, False, 1)
    assert player.school.sitting(moment), "одна ошибка экзамен не кончает"

    second = await answer(db, player, wrong_answer(1), moment)
    assert (second.right, second.done, second.passed) == (False, True, False)
    assert not player.school.sitting(moment), "попытка осталась открытой"
    assert not player.school.passed


async def test_sixteen_answers_with_one_slip_still_pass(db):
    """Одна ошибка — это сдано: экзамен прощает ровно одну."""
    player = make_player()
    moment = await sitting(db, player)

    done = await answer(db, player, wrong_answer(0), moment)
    for number in range(1, TICKET_SIZE):
        done = await answer(db, player, right_answer(number), moment)

    assert done.done and done.passed and done.wrong == 1
    assert player.school.passed and player.school.passed_at == moment
    # Попытка закрыта: второй раз тот же билет не идёт
    assert not player.school.sitting(moment)


async def test_a_clean_ticket_passes_too(db):
    player = make_player()
    moment = await sitting(db, player)

    for number in range(TICKET_SIZE):
        done = await answer(db, player, right_answer(number), moment)

    assert done.passed and done.wrong == 0


async def test_the_quarter_of_an_hour_burns_the_attempt(db):
    player = make_player()
    moment = await sitting(db, player)

    with pytest.raises(SchoolError, match="Время вышло"):
        await answer(db, player, right_answer(0), moment + EXAM_SECONDS + 1)
    # Сгоревшая попытка снимается с бойца: следующий заход начинается с
    # первого вопроса, а не с того, где время кончилось
    assert not player.school.sitting(moment + EXAM_SECONDS + 1)
    assert player.school.step == 0


async def test_an_option_outside_the_question_is_refused(db):
    player = make_player()
    moment = await sitting(db, player)

    for chosen in (-1, 4, 99):
        with pytest.raises(SchoolError, match="варианта"):
            await answer(db, player, chosen, moment)
    assert player.school.step == 0


async def test_a_failed_exam_can_be_retaken_for_free(db):
    """Платят за курс, а не за попытку: пересдача ничего не стоит."""
    player = make_player()
    moment = await sitting(db, player)
    spent = player.credits

    await answer(db, player, wrong_answer(0), moment)
    await answer(db, player, wrong_answer(1), moment)

    # Тот же день приёма, следующая попытка
    await start_exam(db, player, moment + 60)
    assert player.school.sitting(moment + 60)
    assert player.credits == spent, "за пересдачу взяли деньги"


# ---------- права ----------


async def test_the_licence_is_issued_in_the_station_after_the_exam(db):
    player = make_player()
    moment = await sitting(db, player)
    for number in range(TICKET_SIZE):
        await answer(db, player, right_answer(number), moment)

    # В школе прав не выдают
    with pytest.raises(SchoolError, match="участке"):
        await take_licence(db, player, moment)

    player.location = POLICE
    await take_licence(db, player, moment + 600)

    assert player.school.has_licence
    assert player.school.licence_at == moment + 600
    again = await db.get_player(player.user_id)
    assert again.school.has_licence


async def test_there_is_no_licence_without_the_exam(db):
    player = make_player(location=POLICE)
    await db.save_player(player)

    with pytest.raises(SchoolError, match="сдай экзамен"):
        await take_licence(db, player, moment_at())


async def test_the_licence_is_issued_once(db):
    player = make_player(location=POLICE)
    player.school = School(passed_at=moment_at(), licence_at=moment_at())
    await db.save_player(player)
    await db.set_school(player.user_id, player.school)

    with pytest.raises(SchoolError, match="на руках"):
        await take_licence(db, player, moment_at(14, 14))


def test_the_licence_is_a_paper_without_an_end_date():
    """Бланк бессрочный, и в поле срока так и написано."""
    player = make_player()
    player.school = School(passed_at=moment_at(), licence_at=moment_at())

    paper = licence_document(player, moment_at(14, 14))

    assert paper["title"] == "Водительское удостоверение"
    assert paper["holder"] == player.nickname
    assert paper["period"] == "Бессрочно" and not paper["until"]
    assert paper["number"] == licence_number(player.user_id, moment_at())
    assert paper["active"] and paper["state"] == "Действует"
    assert any("таксопарк" in one for one in paper["gives"])


def test_the_licence_lies_with_the_other_papers():
    player = make_player()
    assert not licence_document(player, moment_at())
    assert not build_documents(player, moment_at())["documents"]

    player.school = School(passed_at=moment_at(), licence_at=moment_at())
    papers = build_documents(player, moment_at())

    assert [one["code"] for one in papers["documents"]] == ["licence"]
    assert papers["total"] == 1
    # А пустой раздел говорит, где права берут
    assert "автошколе" in build_documents(make_player(), moment_at())["empty_note"]


# ---------- экран ----------


def test_the_school_screen_walks_the_whole_road():
    """Шаг решает сервер: страница рисует тот, который ей назвали."""
    player = make_player()
    assert build_school(player, moment_at())["stage"] == "new"

    player.school = School(course_at=moment_at(14, 13))
    body = build_school(player, moment_at(15, 13))
    assert body["stage"] == "study" and body["study_left"] == 2 * 24 * 3600

    player.school = School(course_at=moment_at(10, 13))
    ready = build_school(player, moment_at(14, 13))
    assert ready["stage"] == "ready" and ready["open"] is True
    assert ready["schedule"] == "по средам и субботам с 12:00 до 15:00 мск"
    assert not ready["question"], "билета на этом шаге нет"

    player.school = School(passed_at=moment_at())
    assert build_school(player, moment_at())["stage"] == "passed"

    player.school = School(passed_at=moment_at(), licence_at=moment_at())
    assert build_school(player, moment_at())["stage"] == "done"


def test_the_school_screen_shows_the_question_without_its_answer():
    player = make_player()
    player.school = School(
        course_at=moment_at(10, 13), exam_until=moment_at() + EXAM_SECONDS
    )

    body = build_school(player, moment_at())

    assert body["stage"] == "exam"
    assert body["question"]["text"] == TICKET[0].text
    assert body["seconds_left"] == EXAM_SECONDS
    # Ни верного номера, ни пояснения: в билете на странице их нет вовсе
    assert not {"answer", "correct", "comment"} & set(body["question"])
    assert TICKET[0].comment not in str(body)


def test_the_station_opens_the_desk_only_to_the_one_who_passed():
    player = make_player(location=POLICE)
    assert build_police(player, moment_at())["can_take"] is False
    assert "автошколе" in build_police(player, moment_at())["note"]

    player.school = School(passed_at=moment_at())
    ready = build_police(player, moment_at())
    assert ready["can_take"] is True and not ready["licence"]

    player.school = School(passed_at=moment_at(), licence_at=moment_at())
    done = build_police(player, moment_at())
    assert done["can_take"] is False and done["licence"]["number"]


# ---------- двери ----------


async def test_the_school_screen_is_closed_from_the_street(client, db):
    await db.save_player(make_player(location=POLICE))

    refused = await client.get("/api/school", headers=headers())

    assert refused.status == 409
    assert "Автошкола" in (await refused.json())["error"]


async def test_the_station_screen_is_closed_from_the_street(client, db):
    await db.save_player(make_player(location=SCHOOL))

    refused = await client.get("/api/police", headers=headers())

    assert refused.status == 409
    assert "участок" in (await refused.json())["error"].lower()


async def test_the_whole_road_walks_through_the_app(client, db):
    """Запись, экзамен и права — тем же путём, каким идёт игрок."""
    player = make_player()
    await db.save_player(player)

    # Записались
    body = await (await client.post(
        "/api/school", json={"action": "enroll"}, headers=headers()
    )).json()
    assert body["stage"] == "study"

    # Отмотали курс назад: три дня учёбы позади, и дело за днём приёма.
    # Час здесь настоящий — прогон идёт в том же времени, что и сервер
    from bot.game.health import now_ts

    await db.set_school(42, School(course_at=now_ts() - STUDY_SECONDS - 60))

    opened = await client.post(
        "/api/school", json={"action": "exam"}, headers=headers()
    )
    # Экзамен принимают по расписанию, и прогон идёт в настоящем времени:
    # либо сели за билет, либо услышали, когда приходить
    if opened.status == 200:
        seated = await opened.json()
        assert seated["stage"] == "exam"
        assert seated["question"]["number"] == 1
        said = await (await client.post(
            "/api/school",
            json={"action": "answer", "option": right_answer(0)},
            headers=headers(),
        )).json()
        assert said["attempt"]["right"] is True
        assert said["question"]["number"] == 2
    else:
        assert opened.status == 409
        assert "принимают" in (await opened.json())["error"]


async def test_the_station_hands_the_licence_over_the_counter(client, db):
    player = make_player(location=POLICE)
    await db.save_player(player)
    await db.set_school(42, School(passed_at=moment_at()))

    body = await (await client.post("/api/licence", headers=headers())).json()

    assert body["has_licence"] is True
    assert body["licence"]["title"] == "Водительское удостоверение"
    assert "выданы" in body["said"].lower()
    # И второй раз бланк не выписывают
    refused = await client.post("/api/licence", headers=headers())
    assert refused.status == 409
