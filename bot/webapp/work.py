"""Работа глазами мини-аппа: доска агентства и смена на месте.

Правил тут нет — их держат `bot.game.work` и `bot.work_service`. Здесь
перевод в json и одна забота сверх перевода: **верный ответ теста наружу
не уезжает**. На доске вакансий вопросов нет вовсе, а по заявке приходят
только текст и варианты. Тест, ответ к которому лежит в ответе сервера,
проверяет не знания, а умение открыть отладчик.

Экранов два, и они про разное. Агентство — витрина: пять мест, что
каждое даёт и что с ним у этого бойца. Рабочее место — часы: смена,
сколько сегодня отработано и сколько набежало за неделю.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.locations import get_location
from bot.game.work import (
    COOLDOWN_DAYS,
    DAY_HOURS,
    PASS_SHARE,
    SHIFT_HOURS,
    VACANCIES,
    Vacancy,
    day_is_over,
    hours_of,
    payday_after,
    shift_fits,
    vacancy_at,
    work_day,
)
from bot.models import Player


def vacancy_payload(one: Vacancy) -> dict[str, Any]:
    """Строка доски: место, деньги, норма и где работать."""
    place = get_location(one.place)
    return {
        "code": one.code,
        "title": one.title,
        "emoji": one.emoji,
        "salary": one.salary,
        "hours": one.hours,
        "shifts": one.shifts,
        "per_hour": one.per_hour,
        "place": one.place,
        "place_title": place.title if place else one.place,
        "education": one.education,
        "note": one.note,
        "keep_hours": one.keep_hours(),
    }


def board_payload(rows: list[dict], moment: int) -> list[dict[str, Any]]:
    """Доска целиком: пять мест и что с каждым у этого бойца."""
    out = []
    for row in rows:
        one: Vacancy = row["vacancy"]
        blocked = row["blocked_until"]
        body = vacancy_payload(one)
        body.update(
            {
                "taken_by": row["taken_by"],
                "taken": bool(row["taken_by"]),
                "mine": row["mine"],
                "blocked_until": blocked,
                "blocked": bool(blocked),
                "block_days": _days_left(blocked, moment),
                "block_reason": row["block_reason"],
                # Подать заявку можно на свободное и не закрытое место
                "open": not row["taken_by"] and not blocked,
            }
        )
        out.append(body)
    return out


def _days_left(until: int, moment: int) -> int:
    if until <= moment:
        return 0
    return max(1, (until - moment + 24 * 60 * 60 - 1) // (24 * 60 * 60))


def job_payload(player: Player, moment: int) -> dict[str, Any]:
    """Работа бойца: что, сколько отработано и когда платят."""
    from bot.work_service import my_vacancy

    one = my_vacancy(player)
    if one is None:
        return {}
    worked = hours_of(player.job_minutes)
    today = (
        player.shift_minutes if player.shift_day == work_day(moment) else 0
    )
    body = vacancy_payload(one)
    body.update(
        {
            "since": club_moment(player.job_since) if player.job_since else "",
            "worked": worked,
            "left": max(0, one.hours - worked),
            # Доля нормы — ею и подписана полоса
            "percent": min(100, round(worked / one.hours * 100)) if one.hours else 0,
            "today": hours_of(today),
            "day_hours": DAY_HOURS,
            "day_full": not shift_fits(today),
            "payday": club_moment(payday_after(moment)),
            # Сколько заплатят, если неделя кончится прямо сейчас
            "payout": one.payout(worked),
            "safe": not one.fires(worked),
        }
    )
    return body


def shift_payload(player: Player, moment: int) -> dict[str, Any]:
    """Идущая смена. Пусто — боец не на смене."""
    if not player.on_shift(moment):
        return {}
    return {
        "until": player.shift_until,
        "seconds_left": player.shift_left(moment),
        "hours": SHIFT_HOURS,
    }


def build_hr(player: Player, rows: list[dict], now: int | None = None) -> dict[str, Any]:
    """Агентство целиком: доска, своя работа и правила приёма."""
    moment = now_ts() if now is None else now
    return {
        "title": "HR-агентство",
        "vacancies": board_payload(rows, moment),
        "job": job_payload(player, moment),
        "questions": len(VACANCIES) and 5,
        "pass_percent": round(PASS_SHARE * 100),
        "cooldown_days": COOLDOWN_DAYS,
        "shift_hours": SHIFT_HOURS,
        "day_hours": DAY_HOURS,
        "note": (
            "Работа одна на бойца, и место — одно на город. "
            "При приёме открывают счёт в банке: жалованью надо куда-то "
            "приходить. Карту при этом не выпускают."
        ),
        "said": "",
    }


def build_work(player: Player, now: int | None = None) -> dict[str, Any]:
    """Рабочее место: смена, часы за сутки и за неделю."""
    moment = now_ts() if now is None else now
    here = get_location(player.where(moment))
    at_work = vacancy_at(player.where(moment))
    job = job_payload(player, moment)
    mine = bool(job) and job["place"] == player.where(moment)
    shift = shift_payload(player, moment)
    return {
        "place": here.title if here else "",
        "job": job if mine else {},
        "shift": shift,
        "vacancy": vacancy_payload(at_work) if at_work else {},
        # Встать на смену можно на своём месте, не на смене и если сутки
        # ещё не отработаны
        "can_start": bool(mine) and not shift and not job.get("day_full", False),
        "shift_hours": SHIFT_HOURS,
        "note": _work_note(mine, bool(shift), job, at_work, here, moment),
        "said": "",
    }


def _work_note(
    mine: bool,
    on_shift: bool,
    job: dict,
    at_work: Vacancy | None,
    here,
    moment: int,
) -> str:
    """Строка сверху экрана: что тут сейчас можно."""
    if on_shift:
        return "Смена идёт. Из дома не выйти, пока она не кончится."
    if mine:
        if job.get("day_full"):
            return day_is_over(moment, DAY_HOURS)
        return "Можно встать на смену — два часа."
    if at_work is not None:
        where = at_work.title
        soon = here.soon if here is not None and here.soon else ""
        said = (
            f"Здесь работает {where.lower()}, и это место занимают через "
            "HR-агентство."
        )
        if soon:
            said += f" А ещё сюда обещают: {soon}."
        return said
    return "Здесь не работают."


__all__ = [
    "board_payload",
    "build_hr",
    "build_work",
    "job_payload",
    "shift_payload",
    "vacancy_payload",
]
