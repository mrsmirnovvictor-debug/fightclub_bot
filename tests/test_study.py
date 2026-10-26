"""Учёба: запись на курс, занятия по будням и выпускной экзамен.

Порядок проверок — по тому, что дороже сломать:

1. **Деньги и место.** Платят один раз и в своём доме; на курс не
   записывают дважды и не берут второй, пока идёт первый.
2. **Занятие.** Два часа, одно в сутки, только по будням и только в
   приёмные часы. И только там, где курс читают: у кадетов это полигон,
   а не корпус.
3. **Экзамен не проходится чтением мини-аппа.** Верный ответ наружу не
   уезжает вовсе, а билет на пересдаче другой.
4. **Диплом.** Выдают с восьми верных из десяти, кладут в документы и
   больше на тот же курс не записывают.
5. **Замок на дороге.** С занятия не уходят, и это держит сервер.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.content.exams import bank_of, ticket
from bot.database import Database
from bot.game.classes import Stats
from bot.game.clock import MOSCOW
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.study import (
    COURSES,
    EXAM_SIZE,
    LESSON_SECONDS,
    LESSONS_PER_WEEK,
    PASS_MARK,
    RETRY_SECONDS,
    courses_at,
    get_course,
)
from bot.models import Player
from bot.study_service import (
    StudyError,
    attend,
    enrol,
    exam_ticket,
    leave,
    pass_exam,
    start_exam,
)
from bot.travel_service import LockedError, Travel
from bot.webapp.documents import build_documents
from bot.webapp.server import create_app
from bot.webapp.study import build_study
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

CORPS = "cadet_corps"
RANGE = "indoor_training_ground"
COLLEGE = "medical_college"
ACADEMY = "police_school"
UNIVERSITY = "university"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(
    user_id: int = 42, nickname: str = "Тайлер", location: str = CORPS,
    credits: int = 5_000,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=8,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


def moment_at(year=2026, month=10, day=7, hour=12, minute=0) -> int:
    """Миг по московским часам. 7 октября 2026 — среда."""
    return int(datetime(year, month, day, hour, minute, tzinfo=MOSCOW).timestamp())


DAY = 24 * 60 * 60


async def learn_it_all(
    db: Database, player: Player, code: str, start: int | None = None
) -> int:
    """Отстоять весь курс. Возвращает час последнего занятия.

    Занятие одно в сутки и только по будням, поэтому ходим по дням и
    перешагиваем выходные: курс в две недели так и отучивается за две.
    """
    course = get_course(code)
    moment = moment_at(hour=course.open_hour + 1) if start is None else start
    player.location = course.place
    for _ in range(course.lessons):
        while not course.open_now(moment):
            moment += DAY
        await attend(db, player, moment)
        # Занятие кончилось — и сутки тоже: следующее не раньше завтра
        moment += DAY
    return moment


def right_answers(player: Player, code: str) -> list[int]:
    course = get_course(code)
    return [one.answer for one in exam_ticket(player, course)]


def wrong_answers(player: Player, code: str) -> list[int]:
    course = get_course(code)
    return [0 if one.answer != 0 else 1 for one in exam_ticket(player, course)]


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- дома на карте ----------


def test_five_houses_open_for_the_study():
    """Корпус, колледж, академия, университет — и полигон для кадетов."""
    for code in (CORPS, COLLEGE, ACADEMY, UNIVERSITY, RANGE):
        assert get_location(code).allows(Service.STUDY), code

    # Четыре приёмные читают курсы, полигон — нет: туда только ходят
    assert len(courses_at(UNIVERSITY)) == 5
    assert len(courses_at(CORPS)) == 1
    assert courses_at(RANGE) == ()

    # «Скоро» у четырёх школ снято: обещанное они сдержали. У полигона
    # оно осталось — стрельба и спарринги ещё впереди
    for code in (CORPS, COLLEGE, ACADEMY, UNIVERSITY):
        assert not get_location(code).soon, code
    assert get_location(RANGE).soon


def test_the_courses_say_what_the_city_promised():
    """Семь курсов: цена, срок, часы и диплом — ровно как обещано."""
    prices = {one.code: one.price for one in COURSES}
    assert prices == {
        "cadet": 1_000, "medic": 1_000, "police": 1_000,
        "banking": 2_000, "it": 2_000, "management": 1_800,
        "law": 1_800, "gov": 2_500,
    }
    weeks = {one.code: one.weeks for one in COURSES}
    assert weeks["cadet"] == weeks["medic"] == weeks["police"] == 1
    assert all(
        weeks[code] == 2
        for code in ("banking", "it", "management", "law", "gov")
    )

    # Неделя — это пять будней, две недели — десять занятий
    assert get_course("cadet").lessons == LESSONS_PER_WEEK
    assert get_course("it").lessons == 2 * LESSONS_PER_WEEK

    # Часы приёма: у корпуса и академии с десяти, у колледжа и
    # университета с восьми
    hours = {one.code: (one.open_hour, one.close_hour) for one in COURSES}
    assert hours["cadet"] == (10, 18) and hours["police"] == (10, 18)
    assert hours["medic"] == (8, 16) and hours["gov"] == (8, 16)

    # Кадеты учатся не там, где записываются
    assert get_course("cadet").house == CORPS
    assert get_course("cadet").place == RANGE
    for code in ("medic", "police", "banking"):
        assert get_course(code).house == get_course(code).place


def test_every_course_has_an_exam_bank_of_its_own():
    """У каждой специальности свои вопросы, и их больше, чем в билете."""
    for one in COURSES:
        bank = bank_of(one.quiz)
        assert len(bank) > EXAM_SIZE, one.code
        for question in bank:
            assert 0 <= question.answer < len(question.options)
    assert PASS_MARK == 8, "восемьдесят процентов от десяти"


# ---------- запись ----------


async def test_the_enrolment_takes_the_money_and_opens_the_count(db):
    """Записали — деньги списали, счёт занятий открыли."""
    player = await stand(db, make_player())
    said = await enrol(db, player, "cadet", moment_at())

    assert player.credits == 4_000
    assert player.study_code == "cadet" and player.study_lessons == 0
    assert "Кадетский корпус" in said
    fresh = await db.get_player(player.user_id)
    assert fresh.study_code == "cadet"
    # И в выписку это попало бы как учёба, а не как «оплата в городе»
    assert player.card_moves == [] or player.card_moves[0].about.startswith("Курс")


async def test_the_enrolment_asks_for_the_right_house(db):
    """Записывают на месте: в университете на кадета не берут."""
    player = await stand(db, make_player(location=UNIVERSITY))
    with pytest.raises(StudyError, match="Записывают на месте"):
        await enrol(db, player, "cadet", moment_at())
    # А свои четыре — пожалуйста
    await enrol(db, player, "it", moment_at())
    assert player.study_code == "it"


async def test_two_courses_at_once_are_not_taken(db):
    """Курс один на бойца, как и работа."""
    player = await stand(db, make_player(location=UNIVERSITY))
    await enrol(db, player, "it", moment_at())
    with pytest.raises(StudyError, match="уже учишься"):
        await enrol(db, player, "gov", moment_at())


async def test_without_the_money_there_is_no_course(db):
    player = await stand(db, make_player(location=UNIVERSITY, credits=100))
    with pytest.raises(StudyError, match="Не хватает кредитов"):
        await enrol(db, player, "gov", moment_at())
    assert not player.study_code


async def test_the_dropped_course_takes_the_money_with_it(db):
    """Бросил — отчислили, деньги не вернули: за учёбу платят вперёд."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    said = await leave(db, player, moment_at())

    assert not player.study_code and player.credits == 4_000
    assert "не возвращают" in said


# ---------- занятие ----------


async def test_the_lesson_is_two_hours_at_the_right_place(db):
    """Занятие — два часа, и кадет стоит их на полигоне, а не в корпусе."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())

    # В корпусе занятий нет: там только записывают
    with pytest.raises(StudyError, match="Занятия идут не здесь"):
        await attend(db, player, moment_at(hour=11))

    player.location = RANGE
    until = await attend(db, player, moment_at(hour=11))
    assert until == moment_at(hour=11) + LESSON_SECONDS
    assert player.study_lessons == 1
    assert player.on_lesson(moment_at(hour=12))
    assert not player.on_lesson(moment_at(hour=13, minute=1))


async def test_the_second_lesson_waits_for_tomorrow(db):
    """Одно занятие в сутки: вторым курс за день не закроешь."""
    player = await stand(db, make_player(location=RANGE))
    player.location = CORPS
    await enrol(db, player, "cadet", moment_at())
    player.location = RANGE
    await attend(db, player, moment_at(hour=11))

    with pytest.raises(StudyError, match="На сегодня занятие отстояно"):
        await attend(db, player, moment_at(hour=15))
    # А назавтра — пожалуйста
    await attend(db, player, moment_at(day=8, hour=11))
    assert player.study_lessons == 2


async def test_the_lesson_keeps_the_hours_and_the_weekdays(db):
    """Выходные и нерабочие часы учебными не бывают."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    player.location = RANGE

    # 10 октября 2026 — суббота
    with pytest.raises(StudyError, match="не учебное время"):
        await attend(db, player, moment_at(day=10, hour=12))
    # Будний день, но до открытия
    with pytest.raises(StudyError, match="не учебное время"):
        await attend(db, player, moment_at(hour=9))
    # Будний день, но до закрытия меньше двух часов
    with pytest.raises(StudyError, match="два часа"):
        await attend(db, player, moment_at(hour=17))
    assert player.study_lessons == 0


async def test_the_road_is_locked_while_the_lesson_runs(db):
    """С занятия не уходят: замок держит сервер, а не вежливость."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    player.location = RANGE
    moment = now_ts()
    while not get_course("cadet").open_now(moment):
        moment += 60 * 60
    await attend(db, player, moment)

    travel = Travel(db)
    with pytest.raises(LockedError, match="на занятии"):
        await travel.go(player, CORPS, moment)


# ---------- экзамен ----------


async def test_the_exam_waits_for_every_lesson(db):
    """Недоучившегося к билету не пускают."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    player.location = RANGE
    await attend(db, player, moment_at(hour=11))

    player.location = CORPS
    with pytest.raises(StudyError, match="Сперва занятия"):
        await start_exam(db, player, moment_at(hour=14))


async def test_the_exam_is_ten_questions_and_eight_right(db):
    """Десять вопросов, восемь верных — и диплом в документах."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    last = await learn_it_all(db, player, "cadet")

    player.location = CORPS
    course, questions = await start_exam(db, player, last)
    assert course.code == "cadet" and len(questions) == EXAM_SIZE

    done, right, won = await pass_exam(
        db, player, right_answers(player, "cadet"), last
    )
    assert won and right == EXAM_SIZE
    assert player.has_diploma("cadet")
    # Курс закрыт: боец снова свободен и может пойти учиться дальше
    assert not player.study_code
    fresh = await db.get_player(player.user_id)
    assert fresh.diplomas == ["cadet"]
    assert done.diploma_title == "Диплом кадетского училища"


async def test_seven_right_are_not_enough(db):
    """Семь из десяти — это семьдесят процентов, а нужно восемьдесят."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    last = await learn_it_all(db, player, "cadet")
    player.location = CORPS

    answers = right_answers(player, "cadet")
    course = get_course("cadet")
    questions = exam_ticket(player, course)
    # Портим три ответа: верных остаётся семь
    for index in range(3):
        answers[index] = 0 if questions[index].answer != 0 else 1

    _, right, won = await pass_exam(db, player, answers, last)
    assert right == 7 and not won
    assert not player.has_diploma("cadet")
    # Курс остался на бойце: отучено всё, не сдан только билет
    assert player.study_code == "cadet" and player.study_lessons == 5


async def test_the_retake_waits_a_week_and_changes_the_ticket(db):
    """Пересдача раз в неделю, и билет на ней другой."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    last = await learn_it_all(db, player, "cadet")
    player.location = CORPS

    first = [one.text for one in exam_ticket(player, get_course("cadet"))]
    await pass_exam(db, player, wrong_answers(player, "cadet"), last)

    with pytest.raises(StudyError, match="Пересдача раз в неделю"):
        await start_exam(db, player, last + DAY)

    # Неделя прошла — пускают, и вопросы уже не те
    moment = last + RETRY_SECONDS
    _, questions = await start_exam(db, player, moment)
    assert [one.text for one in questions] != first

    _, right, won = await pass_exam(
        db, player, right_answers(player, "cadet"), moment
    )
    assert won and player.has_diploma("cadet")


async def test_the_same_diploma_is_not_studied_twice(db):
    """Диплом есть — на тот же курс больше не записывают."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    last = await learn_it_all(db, player, "cadet")
    player.location = CORPS
    await pass_exam(db, player, right_answers(player, "cadet"), last)

    with pytest.raises(StudyError, match="уже есть"):
        await enrol(db, player, "cadet", last)


async def test_the_long_course_takes_ten_lessons(db):
    """Две недели — это десять занятий, и девяти мало."""
    player = await stand(db, make_player(location=UNIVERSITY))
    await enrol(db, player, "gov", moment_at(hour=9))
    assert player.credits == 2_500

    course = get_course("gov")
    moment = moment_at(hour=9)
    for _ in range(course.lessons - 1):
        while not course.open_now(moment):
            moment += DAY
        await attend(db, player, moment)
        moment += DAY
    assert player.study_lessons == 9
    with pytest.raises(StudyError, match="Сперва занятия"):
        await start_exam(db, player, moment)


# ---------- диплом в документах ----------


async def test_the_diploma_lies_in_the_papers(db):
    """Диплом — бланк на всю жизнь, и лежит он в документах."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    last = await learn_it_all(db, player, "cadet")
    player.location = CORPS
    await pass_exam(db, player, right_answers(player, "cadet"), last)

    papers = build_documents(player, last)["documents"]
    diploma = next(one for one in papers if one["kind"] == "diploma")
    assert diploma["title"] == "Диплом кадетского училища"
    assert diploma["issuer"] == "Кадетский корпус"
    assert diploma["holder"] == "Тайлер"
    assert diploma["period"] == "Бессрочно"
    assert diploma["active"] is True


# ---------- экран ----------


async def test_the_reception_shows_what_the_house_reads(db):
    """Университет показывает пять специальностей, полигон — ни одной."""
    player = await stand(db, make_player(location=UNIVERSITY))
    body = build_study(player, moment_at())

    assert body["title"] == "Университет"
    assert len(body["courses"]) == 5
    assert {one["code"] for one in body["courses"]} == {
        "banking", "it", "management", "law", "gov"
    }
    assert all(one["open"] for one in body["courses"])
    assert body["study"] == {}

    player.location = RANGE
    empty = build_study(player, moment_at())
    assert empty["courses"] == []
    assert "приходят на занятия" in empty["note"]


async def test_the_screen_counts_the_lessons_and_the_window(db):
    """Свой курс на экране: сколько отстояно, где и когда следующее."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    player.location = RANGE
    await attend(db, player, moment_at(hour=11))

    body = build_study(player, moment_at(hour=14))
    mine = body["study"]
    assert mine["lessons_done"] == 1 and mine["left"] == 4
    assert mine["percent"] == 20
    assert mine["place_title"] == "Крытый полигон" and mine["elsewhere"] is True
    assert mine["today"] is True
    # Сегодня уже отстояли — второго занятия не дают
    assert mine["can_attend"] is False
    assert body["lesson"] == {}


async def test_the_exam_payload_keeps_the_answers_to_itself(db):
    """Наружу едут вопрос и варианты. Ответа в билете нет вовсе."""
    from bot.webapp.study import exam_payload

    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    last = await learn_it_all(db, player, "cadet")
    player.location = CORPS
    course, questions = await start_exam(db, player, last)

    body = exam_payload(questions, course)
    assert body["total"] == EXAM_SIZE and body["pass_mark"] == PASS_MARK
    assert len(body["questions"]) == EXAM_SIZE
    for one in body["questions"]:
        assert set(one) == {"text", "options"}, "в билете только вопрос и варианты"


def test_the_ticket_is_the_same_until_the_try_is_spent():
    """Один и тот же билет на одну попытку — и другой на следующую."""
    first = ticket("it", 42, 0, EXAM_SIZE)
    assert [one.text for one in first] == [
        one.text for one in ticket("it", 42, 0, EXAM_SIZE)
    ]
    assert [one.text for one in first] != [
        one.text for one in ticket("it", 42, 1, EXAM_SIZE)
    ]
    # И у другого бойца свой
    assert [one.text for one in first] != [
        one.text for one in ticket("it", 43, 0, EXAM_SIZE)
    ]


# ---------- ручки ----------


async def test_the_study_handler_enrols_and_teaches(db, client):
    """`/api/study`: записаться и встать на занятие — через место."""
    await stand(db, make_player())
    answer = await client.get("/api/study", headers=headers())
    assert answer.status == 200
    body = await answer.json()
    assert body["house"] == CORPS and len(body["courses"]) == 1

    answer = await client.post(
        "/api/study", json={"action": "enrol", "course": "cadet"},
        headers=headers(),
    )
    assert answer.status == 200
    body = await answer.json()
    assert body["study"]["code"] == "cadet"
    assert "зачислен" in body["said"]

    # Занятие в корпусе не ставят: кадеты учатся на полигоне
    answer = await client.post(
        "/api/study", json={"action": "attend"}, headers=headers()
    )
    assert answer.status == 409
    assert "не здесь" in (await answer.json())["error"]


async def test_the_study_handler_is_shut_where_no_one_teaches(db, client):
    """В баре учиться негде, и ручка это говорит, а не рисует пустое."""
    await stand(db, make_player(location="bar"))
    answer = await client.get("/api/study", headers=headers())
    assert answer.status == 409


async def test_the_exam_handler_hands_the_diploma(db, client):
    """Билет через ручку, ответы через ручку, диплом в карточке."""
    player = await stand(db, make_player())
    await enrol(db, player, "cadet", moment_at())
    await learn_it_all(db, player, "cadet")
    player.location = CORPS
    await db.save_player(player)

    answer = await client.post(
        "/api/study", json={"action": "exam"}, headers=headers()
    )
    assert answer.status == 200
    body = await answer.json()
    assert body["exam"]["total"] == EXAM_SIZE

    fresh = await db.get_player(player.user_id)
    answer = await client.post(
        "/api/study",
        json={"action": "pass", "answers": right_answers(fresh, "cadet")},
        headers=headers(),
    )
    assert answer.status == 200
    body = await answer.json()
    assert body["attempt"]["passed"] is True
    assert body["attempt"]["right"] == EXAM_SIZE
    assert "Диплом кадетского училища" in body["said"]
    assert body["diplomas"] == ["Диплом кадетского училища"]
    # И документ приехал вместе с карточкой
    kinds = [one["kind"] for one in body["card"]["documents"]]
    assert "diploma" in kinds
