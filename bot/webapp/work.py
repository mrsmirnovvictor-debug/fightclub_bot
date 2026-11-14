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

from bot.content.work_quiz import quiz_for
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
    OVERTIME_RATE,
    day_is_over,
    hours_of,
    payday_after,
    shift_fits,
    vacancies_at,
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
        "salary_max": one.salary_max,
        "pay_text": one.pay_text,
        "hours": one.hours,
        "shifts": one.shifts,
        "per_hour": one.per_hour,
        "place": one.place,
        "place_title": place.title if place else one.place,
        "education": one.education,
        "needs": one.needs,
        "months": one.months,
        "months_text": one.months_text,
        "seats": one.seats,
        "note": one.note,
        "keep_hours": one.keep_hours(),
        # Есть ли тест — да или нет, и ни одного вопроса сверх этого.
        # Там, где дверь закрыта дипломом, экзамен уже сдан в училище, и
        # страница по этому полю решает, показать вопросы или сразу
        # кнопку «Устроиться»
        "has_quiz": bool(quiz_for(one.code)),
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
                "taken": row["free_seats"] <= 0,
                "taken_seats": row["taken_seats"],
                "free_seats": row["free_seats"],
                "holders": row["holders"],
                "lacks": row["lacks"],
                "mine": row["mine"],
                "blocked_until": blocked,
                "blocked": bool(blocked),
                "block_days": _days_left(blocked, moment),
                "block_reason": row["block_reason"],
                # Подать заявку можно на свободное, не закрытое место,
                # к которому у бойца есть бумаги
                "open": (
                    row["free_seats"] > 0 and not blocked and not row["lacks"]
                ),
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
            # Сколько заплатят, если неделя кончится прямо сейчас, и
            # сколько из этого придёт за переработку
            "payout": one.payout(worked),
            "overtime": one.overtime(worked),
            "overtime_pay": one.overtime_pay(worked),
            "hour_rate": round(one.hour_rate * OVERTIME_RATE, 1),
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
            "приходить. Карту при этом не выпускают. Час сверх недельной "
            "нормы оплачивают вдвойне."
        ),
        "said": "",
    }


async def build_work(db, player: Player, now: int | None = None) -> dict[str, Any]:
    """Рабочее место: смена, часы за сутки и за неделю.

    Здесь же и приём на те места, что занимают в самом доме: в банк, в
    больницу, в часть и в суд приходят в банк, в больницу, в часть и в
    суд. Прежде такие места экран только перечислял и отсылал в
    агентство, которого они и не показывают.
    """
    from bot.work_service import city_appoints

    moment = now_ts() if now is None else now
    # Пустые кресла городской мэр занимает сам, и случается это при
    # каждом взгляде на работу: кнопок у него нет, а очередь по
    # заявкам могла собраться, пока сюда никто не заходил
    await city_appoints(db, moment)
    here = get_location(player.where(moment))
    at_work = vacancies_at(player.where(moment))
    job = job_payload(player, moment)
    mine = bool(job) and job["place"] == player.where(moment)
    shift = shift_payload(player, moment)
    return {
        "place": here.title if here else "",
        "job": job if mine else {},
        "shift": shift,
        # Что тут вообще работают. В банке таких мест три, и экран
        # перечисляет их все: за одними идут в агентство, другие
        # занимают прямо тут
        "vacancies": await house_board(db, player, at_work, moment),
        # Встать на смену можно на своём месте, не на смене и если сутки
        # ещё не отработаны
        "can_start": bool(mine) and not shift and not job.get("day_full", False),
        "shift_hours": SHIFT_HOURS,
        "note": _work_note(mine, bool(shift), job, at_work, here, moment),
        "said": "",
    }


async def house_board(
    db, player: Player, at_work: tuple[Vacancy, ...], moment: int
) -> list[dict[str, Any]]:
    """Места этого дома: что каждое даёт и что с ним у этого бойца."""
    from bot.work_service import vacancies as work_rows

    if not at_work:
        return []
    rows = await work_rows(db, player, moment, board=at_work)
    mine = await db.bids_of(player.user_id)
    out = []
    for row in rows:
        one: Vacancy = row["vacancy"]
        body = vacancy_payload(one)
        free = not row["limited"] or row["free_seats"] > 0
        body.update(
            {
                "at_house": one.at_house,
                "taken_by": row["taken_by"],
                "taken_seats": row["taken_seats"],
                "free_seats": row["free_seats"],
                "limited": row["limited"],
                "mine": row["mine"],
                "lacks": row["lacks"],
                # Кресло начальника не занимают — на него подают заявку,
                # а сажает мэр
                "appointed": one.appointed,
                "bid": one.code in mine,
                "can_take": bool(
                    one.at_house and not one.appointed and not row["lacks"]
                    and not player.works and free
                ),
                "can_bid": bool(
                    one.appointed and not row["lacks"] and not row["mine"]
                ),
            }
        )
        out.append(body)
    return out


def _work_note(
    mine: bool,
    on_shift: bool,
    job: dict,
    at_work: tuple[Vacancy, ...],
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
    if at_work:
        # Перечисляем все здешние места: в банке их три, и сказать про
        # одно значило бы спрятать два
        # Названия как написаны: «Банк «Vegas»» в нижнем регистре
        # превращается в «банк «vegas»», а это имя, а не слово
        names = ", ".join(one.title for one in at_work)
        soon = here.soon if here is not None and here.soon else ""
        where = (
            "Сюда берут на месте."
            if all(one.at_house for one in at_work)
            else "Эти места занимают через HR-агентство."
            if len(at_work) > 1
            else "Это место занимают через HR-агентство."
        )
        said = f"Здесь работают: {names}. {where}"
        if soon:
            said += f" А ещё сюда обещают: {soon}."
        return said
    return "Здесь не работают."


__all__ = [
    "board_payload",
    "build_hr",
    "build_work",
    "house_board",
    "job_payload",
    "shift_payload",
    "vacancy_payload",
]
