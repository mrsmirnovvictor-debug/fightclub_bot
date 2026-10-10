"""Экраны автошколы и участка: курс, билет и выдача прав.

Здесь только то, что видно на странице. Правила живут в
`bot.game.driving`, вопросы — в `bot.content.pdd`, а действия — в
`bot.driving_service`.

Главное правило этого файла: наружу не уезжает ни верный ответ, ни
пояснение к нему. Страница получает вопрос, картинку и варианты — и
ничего, по чему экзамен можно было бы сдать, не читая вопроса.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.driving import (
    COURSE_PRICE,
    EXAM_MINUTES,
    LICENCE_CODE,
    LICENCE_EMOJI,
    LICENCE_GIVES,
    LICENCE_ISSUER,
    LICENCE_NOTE,
    LICENCE_TITLE,
    MISTAKES_ALLOWED,
    STUDY_DAYS,
    TICKET_SIZE,
    exam_text,
    exam_window,
    licence_number,
    next_exam,
)
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.models import Player

# Какой шаг пути боец прошёл. По нему страница и решает, что показывать:
# цену курса, обратный отсчёт учёбы, кнопку «на экзамен» или сам билет
NEW = "new"  # не записан
STUDY = "study"  # учится, три дня ещё не прошли
READY = "ready"  # отучился, ждёт дня приёма
EXAM = "exam"  # билет идёт прямо сейчас
PASSED = "passed"  # сдал, права ещё не получил
DONE = "done"  # права на руках


def school_stage(player: Player, moment: int) -> str:
    """На каком шаге боец. Один ответ на весь экран."""
    school = player.school
    if school.has_licence:
        return DONE
    if school.passed:
        return PASSED
    if school.sitting(moment):
        return EXAM
    if not school.enrolled:
        return NEW
    return READY if school.studied(moment) else STUDY


def build_school(player: Player, now: int | None = None) -> dict[str, Any]:
    """Экран автошколы: курс, обратный отсчёт и билет."""
    from bot.driving_service import current_question

    moment = now_ts() if now is None else now
    school = player.school
    window = exam_window(moment)
    stage = school_stage(player, moment)
    coming = next_exam(moment)
    return {
        "title": "Автошкола",
        "stage": stage,
        "price": player.price_here(COURSE_PRICE, moment, Service.SCHOOL),
        "full_price": COURSE_PRICE,
        "credits": player.credits,
        "purse": purse_line(player, moment),
        "study_days": STUDY_DAYS,
        "study_left": school.study_left(moment) if school.enrolled else 0,
        "study_until": (
            club_moment(school.course_at + STUDY_DAYS * 24 * 60 * 60)
            if school.enrolled
            else ""
        ),
        "schedule": exam_text(moment),
        "open": window is not None,
        "window": window.title if window else "",
        "closes_in": window.seconds_left(moment) if window else 0,
        "next_exam": club_moment(coming.start),
        "minutes": EXAM_MINUTES,
        "questions": TICKET_SIZE,
        "mistakes": MISTAKES_ALLOWED,
        # Билет: вопрос, на котором стоит попытка, и сколько её осталось
        "question": current_question(player, moment),
        "seconds_left": school.time_left(moment),
        "wrong": school.wrong if school.sitting(moment) else 0,
        "passed_at": club_moment(school.passed_at) if school.passed else "",
        "note": school_note(stage, school, moment, window is not None),
        "said": "",
    }


def school_note(stage: str, school, moment: int, open_now: bool) -> str:
    """Строка сверху экрана: что тут сейчас делают."""
    if stage == NEW:
        return (
            f"Курс — {COURSE_PRICE} 💰 и {STUDY_DAYS} дня на самостоятельное "
            "изучение ПДД. После них можно садиться за билет."
        )
    if stage == STUDY:
        return (
            "Идёт курс: учи правила. На экзамен записывают, когда пройдут "
            f"{STUDY_DAYS} полных дня с записи."
        )
    if stage == READY:
        return (
            f"Курс отучен. Экзамен принимают {exam_text(moment)} — "
            + ("можно садиться за билет." if open_now else "приходи в этот час.")
        )
    if stage == EXAM:
        return (
            f"{TICKET_SIZE} вопросов и {EXAM_MINUTES} минут. Одна ошибка "
            "прощается, на второй экзамен кончается."
        )
    if stage == PASSED:
        return "Экзамен сдан. За правами — в полицейский участок."
    return "Права получены и лежат в документах."


def purse_line(player: Player, moment: int) -> str:
    """Чем боец здесь платит — одной строкой, как в лавке."""
    return player.purse_note(moment, Service.SCHOOL)


def build_police(player: Player, now: int | None = None) -> dict[str, Any]:
    """Экран участка: выдача прав и ничего больше.

    Дежурная часть с розыском тут когда-нибудь будут, а пока участок
    умеет одно — выписать бланк тому, кто сдал экзамен.
    """
    moment = now_ts() if now is None else now
    school = player.school
    ready = school.passed and not school.has_licence
    return {
        "title": "Полицейский участок",
        "can_take": ready,
        "has_licence": school.has_licence,
        "passed": school.passed,
        "licence": licence_document(player, moment),
        "note": (
            "Бланк готов: распишись и забирай."
            if ready
            else (
                "Права у тебя на руках — смотри в документах."
                if school.has_licence
                else (
                    "Водительское удостоверение выдают здесь, но сначала его "
                    "надо сдать: курс и экзамен — в автошколе."
                )
            )
        ),
        "said": "",
    }


def licence_document(player: Player, moment: int) -> dict[str, Any]:
    """Права как документ. Пустой словарь — их не выдавали.

    Бланк тот же, что у полиса и карты, но срока у него нет вовсе:
    удостоверение бессрочно, и в поле срока так и написано.
    """
    school = player.school
    if not school.has_licence:
        return {}
    return {
        "code": LICENCE_CODE,
        "kind": "licence",
        "emoji": LICENCE_EMOJI,
        "title": LICENCE_TITLE,
        "issuer": LICENCE_ISSUER,
        "number": licence_number(player.user_id, school.licence_at),
        "holder": player.nickname,
        "holder_title": "Выдано",
        "issued": club_moment(school.licence_at),
        "until": "",
        "period": "Бессрочно",
        "period_title": "Срок действия",
        "active": True,
        "seconds_left": 0,
        "covers": "Управление транспортным средством",
        "gives": list(LICENCE_GIVES),
        "auto_renew": False,
        "switchable": False,
        "note": LICENCE_NOTE,
        "state": "Действует",
        "ground": "Сдан экзамен",
    }


__all__ = [
    "DONE",
    "EXAM",
    "NEW",
    "PASSED",
    "READY",
    "STUDY",
    "build_police",
    "build_school",
    "licence_document",
    "school_stage",
]
