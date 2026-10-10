"""Автошкола: запись на курс, экзамен по билету и выдача прав.

Правила держит `bot.game.driving`, вопросы — `bot.content.pdd`, а здесь
то, что меняет состояние: списать за курс, начать попытку, принять ответ
и выписать бланк.

Каждое действие сначала проверяет место: учатся и сдают в автошколе,
права получают в участке. Проверка тут, а не на странице: спрятанная
кнопка обходится запросом мимо интерфейса.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from bot.content.pdd import TICKET, question
from bot.database import Database
from bot.game.driving import (
    COURSE_PRICE,
    MISTAKES_ALLOWED,
    POLICE_HOUSE,
    SCHOOL_HOUSE,
    STUDY_DAYS,
    STUDY_SECONDS,
    TICKET_SIZE,
    School,
    exam_text,
    exam_window,
    next_exam,
)
from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.models import Player

logger = logging.getLogger(__name__)


class SchoolError(Exception):
    """Отказ автошколы, который можно показать игроку как есть."""


@dataclass(frozen=True)
class Answered:
    """Чем кончился ответ на вопрос билета."""

    right: bool
    # Экзамен кончился этим ответом: сдан или провален
    done: bool
    passed: bool
    wrong: int
    step: int  # сколько вопросов пройдено


async def _save(db: Database, player: Player, school: School) -> School:
    player.school = school
    await db.set_school(player.user_id, school)
    return school


async def enroll(db: Database, player: Player, now: int | None = None) -> School:
    """Записаться на курс: триста кредитов и три дня самостоятельной учёбы."""
    moment = now_ts() if now is None else now
    if player.where(moment) != SCHOOL_HOUSE:
        raise SchoolError("Записаться можно в автошколе, а не отсюда.")
    school = player.school
    if school.has_licence:
        raise SchoolError("Права у тебя уже есть — второй раз их не выдают.")
    if school.passed:
        raise SchoolError("Экзамен сдан. Права ждут в полицейском участке.")
    if school.enrolled:
        raise SchoolError("Ты уже записан на курс.")
    price = player.price_here(COURSE_PRICE, moment, Service.SCHOOL)
    if not player.can_afford(price, moment, Service.SCHOOL):
        raise SchoolError(
            f"Курс стоит {price} 💰, а {player.purse_note(moment, Service.SCHOOL)}."
        )
    player.pay(price, moment, Service.SCHOOL)
    await db.save_player(player)
    logger.info("Боец %s записался в автошколу", player.user_id)
    return await _save(db, player, replace(school, course_at=moment))


async def start_exam(db: Database, player: Player, now: int | None = None) -> School:
    """Сесть за билет: пятнадцать минут и шестнадцать вопросов."""
    moment = now_ts() if now is None else now
    if player.where(moment) != SCHOOL_HOUSE:
        raise SchoolError("Экзамен сдают в автошколе: приходи туда.")
    school = player.school
    if school.has_licence or school.passed:
        raise SchoolError("Экзамен уже сдан.")
    if not school.enrolled:
        raise SchoolError("Сначала запишись на курс.")
    if not school.studied(moment):
        raise SchoolError(
            f"Курс идёт: на изучение ПДД даётся {STUDY_DAYS} дня. "
            "Садиться за билет можно с "
            f"{club_moment(school.course_at + STUDY_SECONDS)}."
        )
    if school.sitting(moment):
        raise SchoolError("Экзамен уже идёт.")
    if exam_window(moment) is None:
        when = next_exam(moment)
        raise SchoolError(
            f"Экзамен принимают {exam_text(moment)}. "
            f"Ближайший — {club_moment(when.start)}."
        )
    logger.info("Боец %s сел за билет", player.user_id)
    return await _save(db, player, school.begin(moment))


async def answer(
    db: Database, player: Player, chosen: int, now: int | None = None
) -> Answered:
    """Подтвердить ответ на текущий вопрос билета.

    Вторая ошибка кончает экзамен на месте: доучивай и приходи в
    следующий раз. Шестнадцатый верный ответ — экзамен сдан.
    """
    moment = now_ts() if now is None else now
    school = player.school
    if not school.sitting(moment):
        if school.burnt(moment):
            await _save(db, player, school.closed())
            raise SchoolError(
                "Время вышло: на билет даётся четверть часа. Нужна пересдача."
            )
        raise SchoolError("Экзамен не идёт.")
    one = question(school.step)
    if one is None:  # pragma: no cover - билет кончается раньше по `step`
        raise SchoolError("Вопросы кончились.")
    if not 0 <= chosen < len(one.options):
        raise SchoolError("Такого варианта в вопросе нет.")

    right = one.correct(chosen)
    wrong = school.wrong + (0 if right else 1)
    step = school.step + 1
    if wrong > MISTAKES_ALLOWED:
        await _save(db, player, school.closed())
        logger.info("Боец %s провалил экзамен ПДД", player.user_id)
        return Answered(right=right, done=True, passed=False, wrong=wrong, step=step)
    if step >= TICKET_SIZE:
        await _save(
            db, player, replace(school.closed(), passed_at=moment)
        )
        logger.info("Боец %s сдал экзамен ПДД с %s ошибками", player.user_id, wrong)
        return Answered(right=right, done=True, passed=True, wrong=wrong, step=step)
    await _save(db, player, replace(school, step=step, wrong=wrong))
    return Answered(right=right, done=False, passed=False, wrong=wrong, step=step)


async def take_licence(
    db: Database, player: Player, now: int | None = None
) -> School:
    """Получить права в полицейском участке. Выдают один раз и навсегда."""
    moment = now_ts() if now is None else now
    if player.where(moment) != POLICE_HOUSE:
        raise SchoolError("Права выдают в полицейском участке.")
    school = player.school
    if school.has_licence:
        raise SchoolError("Права у тебя на руках, в документах.")
    if not school.passed:
        raise SchoolError("Сначала сдай экзамен в автошколе.")
    logger.info("Бойцу %s выданы права", player.user_id)
    return await _save(db, player, replace(school, licence_at=moment))


def current_question(player: Player, moment: int) -> dict:
    """Вопрос, на котором стоит попытка. Пусто — попытка не идёт."""
    from bot.content.pdd import ask

    school = player.school
    if not school.sitting(moment) or school.step >= len(TICKET):
        return {}
    return ask(school.step)


__all__ = [
    "Answered",
    "SchoolError",
    "answer",
    "current_question",
    "enroll",
    "start_exam",
    "take_licence",
]
