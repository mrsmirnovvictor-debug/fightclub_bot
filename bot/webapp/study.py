"""Учёба глазами мини-аппа: приёмная, занятие и выпускной билет.

Правил тут нет — их держат `bot.game.study` и `bot.study_service`. Здесь
перевод в json и одна забота сверх перевода: **верный ответ наружу не
уезжает**. В билете едут вопрос и варианты, ответ сверяет сервер.

Экран один на пять домов, и что он покажет, решает дом. В корпусе,
колледже, академии и университете — приёмная: какие курсы читают и что с
ними у этого бойца. На полигоне приёмной нет вовсе: туда кадет приходит
на занятие, и показывать ему там нечего, кроме часов.

Поэтому у экрана два блока, и каждый может быть пустым: `courses` — что
записывают в этом доме, `study` — на чём стоит сам боец. На полигоне
первый пуст, у неучащегося — второй.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.study import (
    EXAM_SIZE,
    LESSON_HOURS,
    PASS_MARK,
    RETRY_DAYS,
    Course,
    courses_at,
    diploma_of,
    get_course,
    retry_at,
)
from bot.models import Player
from bot.webapp.bank import purse_payload


def course_payload(
    one: Course, player: Player | None = None, moment: int = 0
) -> dict[str, Any]:
    """Строка приёмной: курс, цена, срок и что дают в конце.

    Цена — та, что боец заплатит на самом деле: карта даёт скидку, и
    число на витрине обязано совпадать с тем, что уйдёт с кошелька.
    """
    from bot.game.locations import Service

    price = (
        player.price_here(one.price, moment, Service.STUDY)
        if player is not None
        else one.price
    )
    return {
        "code": one.code,
        "title": one.title,
        "emoji": one.emoji,
        "price": price,
        "full_price": one.price,
        "weeks": one.weeks,
        "weeks_text": one.weeks_text,
        "lessons": one.lessons,
        "hours": one.hours,
        "lesson_hours": LESSON_HOURS,
        "window": one.window,
        "place": one.place,
        "place_title": one.place_title,
        # Где учатся, не всегда там, где записывают: кадетов строят на
        # полигоне, и об этом надо сказать до того, как боец заплатит
        "elsewhere": one.place != one.house,
        "diploma": one.diploma,
        "diploma_title": one.diploma_title,
        "subject": one.subject,
        "exam_size": EXAM_SIZE,
        "pass_mark": PASS_MARK,
        "note": one.note,
    }


def board_payload(
    player: Player, house: str, moment: int = 0
) -> list[dict[str, Any]]:
    """Что читают в этом доме и что с каждым курсом у бойца."""
    out = []
    for one in courses_at(house):
        body = course_payload(one, player, moment)
        body.update(
            {
                "done": player.has_diploma(one.diploma),
                "mine": player.study_code == one.code,
                # Записаться можно, пока не учишься и нет диплома
                "open": not player.studies and not player.has_diploma(one.diploma),
            }
        )
        out.append(body)
    return out


def study_payload(player: Player, moment: int) -> dict[str, Any]:
    """Курс, на котором стоит боец. Пусто — он нигде не учится."""
    from bot.study_service import (
        exam_ready,
        learned,
        lessons_left,
        my_course,
        retry_left,
        studied_today,
    )

    one = my_course(player)
    if one is None:
        return {}
    here = player.where(moment)
    full = learned(player, one)
    body = course_payload(one, player, moment)
    body.update(
        {
            "since": club_moment(player.study_since) if player.study_since else "",
            "lessons_done": player.study_lessons,
            "left": lessons_left(player, one),
            "percent": min(100, round(player.study_lessons / one.lessons * 100)),
            "learned": full,
            "today": studied_today(player, moment),
            "open_now": one.open_now(moment),
            "fits": one.fits_today(moment),
            # Можно ли встать на занятие прямо сейчас, одним словом: на
            # месте, в часы, не сегодня и не всё отучено
            "can_attend": (
                here == one.place
                and not full
                and one.open_now(moment)
                and one.fits_today(moment)
                and not studied_today(player, moment)
                and not player.on_lesson(moment)
                and not player.on_shift(moment)
            ),
            "at_place": here == one.place,
            "at_house": here == one.house,
            # Экзамен: пустят ли и когда пустят, если не сейчас
            "exam_open": exam_ready(player, one, moment),
            "tries": player.study_tries,
            "retry_days": RETRY_DAYS,
            "retry_left": retry_left(player, moment),
            "retry_at": (
                club_moment(retry_at(player.study_exam_at))
                if player.study_exam_at
                else ""
            ),
        }
    )
    return body


def lesson_payload(player: Player, moment: int) -> dict[str, Any]:
    """Идущее занятие. Пусто — боец не на занятии."""
    if not player.on_lesson(moment):
        return {}
    return {
        "until": player.lesson_until,
        "seconds_left": player.lesson_left(moment),
        "hours": LESSON_HOURS,
    }


def exam_payload(questions, course: Course) -> dict[str, Any]:
    """Билет наружу: вопросы и варианты, без единого ответа."""
    return {
        "course": course.code,
        "title": f"Выпускной экзамен: {course.title}",
        "subject": course.subject,
        "pass_mark": PASS_MARK,
        "total": len(questions),
        "questions": [
            {"text": one.text, "options": list(one.options)} for one in questions
        ],
    }


def diploma_document(code: str, player: Player) -> dict[str, Any]:
    """Диплом как документ. Бланк тот же, что у прав: бессрочный."""
    course = diploma_of(code)
    if course is None:  # pragma: no cover - курс убрали, а диплом остался
        return {}
    house = get_location(course.house)
    return {
        "code": f"diploma:{code}",
        "kind": "diploma",
        "emoji": course.emoji,
        "title": course.diploma_title,
        "issuer": house.title if house else course.title,
        "holder": player.nickname,
        "holder_title": "Выдан",
        "period": "Бессрочно",
        "period_title": "Срок действия",
        "active": True,
        "gives": [
            f"Подтверждает, что курс «{course.title}» отучен и сдан",
            f"Экзамен: {course.subject}, {PASS_MARK} верных из {EXAM_SIZE}",
        ],
        "state": "Действует",
        "ground": "Сдан выпускной экзамен",
        "note": (
            "Бланк бессрочный: продлевать и подтверждать его не нужно. "
            "Второй раз на тот же курс не записывают."
        ),
    }


def diploma_documents(player: Player) -> list[dict[str, Any]]:
    """Все дипломы бойца — по порядку курсов, а не по порядку сдачи."""
    from bot.game.study import COURSES

    return [
        row
        for row in (
            diploma_document(one.diploma, player)
            for one in COURSES
            if player.has_diploma(one.diploma)
        )
        if row
    ]


def build_study(
    player: Player, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Экран учёбы: приёмная этого дома, свой курс и идущее занятие."""
    moment = now_ts() if now is None else now
    here = player.where(moment)
    house = get_location(here)
    mine = study_payload(player, moment)
    return {
        "title": house.title if house else "Учёба",
        "house": here,
        "courses": board_payload(player, here, moment),
        "study": mine,
        "lesson": lesson_payload(player, moment),
        "credits": player.credits,
        # Чем платить за курс — выбирают здесь же, как в лавке. Без
        # этого выбора боец с жалованьем на счету и пустым карманом
        # читал «не хватает кредитов» и шёл в банк догадываться
        "purse": purse_payload(player, moment, Service.STUDY),
        "diplomas": [
            get_course(code).diploma_title
            for code in player.diplomas
            if get_course(code)
        ],
        "said": said,
        # Пустой дом без курсов и без своей учёбы: сюда пришли зря, и
        # сказать об этом честнее, чем показать пустой экран
        "note": _note(player, here, mine),
    }


def _note(player: Player, here: str, mine: dict[str, Any]) -> str:
    if mine and mine["at_place"] and not mine["at_house"]:
        return f"Занятия курса «{mine['title']}» идут здесь."
    if mine and mine["learned"]:
        return "Курс отучен целиком. Осталось сдать выпускной экзамен."
    if not courses_at(here):
        return "Своей приёмной тут нет: сюда приходят на занятия."
    if player.studies:
        return "Доучись то, на чём стоишь: двух курсов разом не берут."
    return "Платят за курс один раз. Дальше — ходить."


__all__ = [
    "board_payload",
    "build_study",
    "course_payload",
    "diploma_document",
    "diploma_documents",
    "exam_payload",
    "lesson_payload",
    "study_payload",
]
