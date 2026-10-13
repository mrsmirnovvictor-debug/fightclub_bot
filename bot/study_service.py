"""Учёба: запись на курс, занятие в два часа и выпускной экзамен.

Правила держит `bot.game.study`, вопросы — `bot.content.exams`, отказы —
этот модуль. Дверей четыре: записаться, встать на занятие, посмотреть
билет и сдать его.

**Занятие платит временем, а не деньгами.** Деньги берут один раз, при
записи; дальше боец платит часами — два часа за занятие, одно в сутки,
и только по будням в приёмные часы. Час записывается в тот миг, когда
занятие начали: уйти с него нельзя, боец заперт в учебном доме, и
досчитывать по факту нечего.

**Записываются в одном доме, учатся иногда в другом.** У кадетов так:
запись в корпусе, занятия на полигоне. Поэтому место проверяется дважды
и по-разному — `house` при записи, `place` при занятии.

**Экзамен сверяет сервер.** Наружу уезжают вопрос и варианты, ответ
остаётся здесь. Иначе диплом выдавался бы за умение открыть отладчик, а
не за десять занятий.

**Билет считается, а не хранится.** Зерно — боец и номер попытки:
сколько раз ни спроси, билет тот же, а на пересдаче другой. Поэтому
между «показали» и «сдал» ничего не разъедется, пока номер попытки не
вырос, — а растёт он ровно в тот миг, когда попытку засчитали.
"""

from __future__ import annotations

import logging

from bot.content.exams import Question, grade, ticket
from bot.database import Database
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.game.study import (
    EXAM_SIZE,
    LESSON_SECONDS,
    PASS_MARK,
    RETRY_SECONDS,
    Course,
    courses_at,
    get_course,
    lesson_day,
    retry_at,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class StudyError(Exception):
    """Отказ по учёбе, который показывают игроку как есть."""


def my_course(player: Player) -> Course | None:
    """На каком курсе боец. None — ни на каком."""
    return get_course(player.study_code) if player.study_code else None


def lessons_left(player: Player, course: Course) -> int:
    return max(0, course.lessons - player.study_lessons)


def learned(player: Player, course: Course) -> bool:
    """Отстояны ли все занятия: без этого на экзамен не пускают."""
    return player.study_lessons >= course.lessons


def studied_today(player: Player, moment: int) -> bool:
    """Было ли занятие в эти сутки. Второго в тот же день не бывает."""
    return player.study_day == lesson_day(moment)


# ---------- записаться ----------


async def enrol(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Записаться на курс: заплатить и открыть счёт занятий."""
    moment = now_ts() if now is None else now
    course = get_course(code)
    if course is None:
        raise StudyError("Такого курса в городе не читают.")
    if player.where(moment) != course.house:
        raise StudyError(f"Записывают на месте: в «{course.title}» и приходи.")
    if player.has_diploma(course.diploma):
        raise StudyError(f"{course.diploma_title} у тебя уже есть.")
    if player.studies:
        current = my_course(player)
        name = current.title if current else "курс"
        raise StudyError(f"Ты уже учишься: «{name}». Доучись сперва.")
    price = player.price_here(course.price, moment, Service.STUDY)
    if not player.can_afford(price, moment, Service.STUDY):
        raise StudyError(
            f"Не хватает кредитов: учёба стоит {price} 💰, "
            f"а {player.purse_note(moment, Service.STUDY)}."
        )

    player.pay(price, moment, Service.STUDY, about=f"Курс: {course.title}")
    player.study_code = course.code
    player.study_since = moment
    player.study_lessons = 0
    player.study_day = ""
    player.study_exam_at = 0
    player.study_tries = 0
    await db.save_player(player)
    logger.info("Боец %s записался на курс %s", player.user_id, course.code)
    place = course.place
    where = "" if place == course.house else " Занятия идут на полигоне."
    return (
        f"Ты зачислен: «{course.title}». Занятий — {course.lessons}, "
        f"по будням {course.window}.{where}"
    )


async def leave(db: Database, player: Player, now: int | None = None) -> str:
    """Бросить курс. Деньги не возвращают: за учёбу платят вперёд."""
    moment = now_ts() if now is None else now
    course = my_course(player)
    if course is None:
        raise StudyError("Ты нигде не учишься.")
    if player.on_lesson(moment):
        raise StudyError("Сперва досиди занятие.")
    player.study_code = ""
    player.study_since = 0
    player.study_lessons = 0
    player.study_day = ""
    player.study_exam_at = 0
    player.study_tries = 0
    await db.save_player(player)
    logger.info("Боец %s бросил курс %s", player.user_id, course.code)
    return f"Ты отчислен: «{course.title}». Деньги за учёбу не возвращают."


# ---------- занятие ----------


async def attend(db: Database, player: Player, now: int | None = None) -> int:
    """Встать на занятие. Возвращает, до какого часа оно идёт."""
    moment = now_ts() if now is None else now
    course = my_course(player)
    if course is None:
        raise StudyError("Ты нигде не учишься. За учёбой — в приёмную.")
    if player.where(moment) != course.place:
        raise StudyError(f"Занятия идут не здесь: твоё место — «{course.place_title}».")
    if player.on_lesson(moment):
        raise StudyError("Занятие уже идёт.")
    if player.on_shift(moment):
        raise StudyError("Ты на смене: сперва работа, потом учёба.")
    if learned(player, course):
        raise StudyError("Курс отучен целиком. Осталось сдать экзамен.")
    if not course.open_now(moment):
        raise StudyError(
            f"Сейчас не учебное время. Занятия идут по будням {course.window}."
        )
    if not course.fits_today(moment):
        raise StudyError(
            "Занятие идёт два часа, а до конца приёма меньше. Приходи завтра."
        )
    if studied_today(player, moment):
        raise StudyError("На сегодня занятие отстояно. Больше одного в день не ставят.")

    # Занятие засчитываем сразу: уйти с него нельзя — боец заперт, — и
    # досчитывать по окончании нечего, как и на смене
    player.lesson_until = moment + LESSON_SECONDS
    player.study_lessons += 1
    player.study_day = lesson_day(moment)
    await db.save_player(player)
    logger.info(
        "Боец %s отстоял занятие %s: %s из %s",
        player.user_id, course.code, player.study_lessons, course.lessons,
    )
    return player.lesson_until


# ---------- экзамен ----------


def exam_ready(player: Player, course: Course, moment: int) -> bool:
    """Пустят ли на экзамен прямо сейчас."""
    if not learned(player, course):
        return False
    return not player.study_exam_at or retry_at(player.study_exam_at) <= moment


def exam_ticket(player: Player, course: Course) -> tuple[Question, ...]:
    """Билет этой попытки. Тот же, пока попытку не засчитали."""
    return ticket(course.quiz, player.user_id, player.study_tries, EXAM_SIZE)


async def start_exam(
    db: Database, player: Player, now: int | None = None
) -> tuple[Course, tuple[Question, ...]]:
    """Взять билет. Проверяет всё, кроме ответов: их ещё нет."""
    moment = now_ts() if now is None else now
    course = my_course(player)
    if course is None:
        raise StudyError("Ты нигде не учишься.")
    if player.where(moment) != course.house:
        raise StudyError(f"Экзамен принимают в «{course.title}».")
    if not learned(player, course):
        left = lessons_left(player, course)
        raise StudyError(
            f"Сперва занятия: осталось {left} из {course.lessons}."
        )
    if not exam_ready(player, course, moment):
        raise StudyError(_retry_note(player.study_exam_at, moment))
    return course, exam_ticket(player, course)


async def pass_exam(
    db: Database, player: Player, chosen: list[int], now: int | None = None
) -> tuple[Course, int, bool]:
    """Сдать билет. Возвращает (курс, сколько верных, сдал ли).

    Попытку засчитываем в любом исходе: пересдача раз в неделю — это и
    есть цена ошибки, и «не сдал, попробую ещё раз сейчас» её отменяло
    бы.
    """
    moment = now_ts() if now is None else now
    course, questions = await start_exam(db, player, moment)
    right = grade(questions, chosen)
    won = right >= PASS_MARK

    player.study_exam_at = moment
    player.study_tries += 1
    if won:
        player.diplomas = [*player.diplomas, course.diploma]
        player.study_code = ""
        player.study_since = 0
        player.study_lessons = 0
        player.study_day = ""
        player.study_exam_at = 0
        player.study_tries = 0
    await db.save_player(player)
    logger.info(
        "Боец %s сдавал %s: %s из %s, %s",
        player.user_id, course.code, right, len(questions),
        "сдал" if won else "не сдал",
    )
    return course, right, won


def _retry_note(exam_at: int, moment: int) -> str:
    """Когда пустят на пересдачу — словами."""
    from bot.game.clock import club_moment

    return (
        "Пересдача раз в неделю. Следующая попытка "
        f"{club_moment(retry_at(exam_at))}."
    )


def retry_left(player: Player, moment: int) -> int:
    """Сколько секунд до пересдачи. Ноль — можно идти."""
    if not player.study_exam_at:
        return 0
    return max(0, retry_at(player.study_exam_at) - moment)


__all__ = [
    "RETRY_SECONDS",
    "StudyError",
    "attend",
    "courses_at",
    "enrol",
    "exam_ready",
    "exam_ticket",
    "learned",
    "leave",
    "lessons_left",
    "my_course",
    "pass_exam",
    "retry_left",
    "start_exam",
    "studied_today",
]
