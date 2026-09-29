"""Тренажёрный зал глазами мини-аппа.

Правил тут нет — их держат `bot.game.gym` и `bot.gym_service`. Здесь
перевод в json и две заботы сверх перевода.

**Идущий слот отделён от расписания.** Присоединиться можно только к
нему, и кнопка живёт при нём, а не в строке табло: кнопка на каждой из
сорока двух клеток означала бы сорок один отказ.

**Расписание идёт днями, а не сплошным списком.** Сорок две строки
подряд читаются как простыня; день с шестью строками — как день.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from bot.game.classes import Stat
from bot.game.gym import (
    MAX_UPGRADES,
    PASSES,
    SCHEDULE_DAYS,
    TRAINING_MINUTES,
    UPGRADE_GAIN,
    UPGRADE_STEPS,
    Slot,
    moscow_day,
    next_slot,
    schedule_from,
    slot_now,
    total_for,
    training_for,
)
from bot.game.health import now_ts
from bot.gym_service import Progress, Visit, has_pass
from bot.models import Player

WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def day_title(day: date, today: date) -> str:
    """«Сегодня», «завтра» или день недели с числом."""
    if day == today:
        return "Сегодня"
    if (day - today).days == 1:
        return "Завтра"
    return f"{WEEKDAYS[day.weekday()].capitalize()}, {day.strftime('%d.%m')}"


def slot_payload(slot: Slot, now: int, done: bool) -> dict[str, Any]:
    """Клетка табло: время, что тренируют и в каком она состоянии."""
    if done:
        state = "done"
    elif slot.ends <= now:
        state = "past"
    elif slot.is_open(now):
        state = "open" if slot.takes_joiners(now) else "late"
    else:
        state = "ahead"
    return {
        "id": slot.id,
        "clock": slot.clock,
        "code": slot.training.code,
        "title": slot.training.title,
        "emoji": slot.training.emoji,
        "stat": slot.training.stat.value,
        "gains": slot.training.gains,
        "starts": slot.starts,
        "ends": slot.ends,
        # «open» — идёт и пускает, «late» — идёт, но записываться поздно,
        # «done» — боец в нём уже отработал, «past» — прошёл, «ahead» — впереди
        "state": state,
    }


def schedule_payload(
    now: int, done: set[str], days: int = SCHEDULE_DAYS
) -> list[dict[str, Any]]:
    """Расписание днями: заголовок дня и шесть его клеток."""
    today = moscow_day(now)
    board: list[dict[str, Any]] = []
    for slot in schedule_from(now, days):
        if not board or board[-1]["day"] != slot.day.isoformat():
            board.append(
                {
                    "day": slot.day.isoformat(),
                    "title": day_title(slot.day, today),
                    "today": slot.day == today,
                    "slots": [],
                }
            )
        board[-1]["slots"].append(slot_payload(slot, now, slot.id in done))
    return board


def progress_payload(row: Progress) -> dict[str, Any]:
    """Строка прогресса: сколько натренировано и что дальше."""
    training = training_for(row.stat)
    return {
        "stat": row.stat.value,
        "title": row.stat.title.capitalize(),
        "emoji": row.stat.emoji,
        # Чем эту характеристику качают — название стоит рядом с полосой,
        # чтобы из зала было видно, какой слот ловить
        "training": training.title if training else "",
        "training_emoji": training.emoji if training else "",
        "points": row.points,
        "ups": row.ups,
        "max_ups": MAX_UPGRADES,
        # 0 — улучшений больше нет; тогда и полосу рисовать не по чему
        "price": row.price,
        "ready": row.ready,
        "maxed": row.maxed,
        "gain": UPGRADE_GAIN,
        # Сколько осталось до следующего улучшения — им и подписана полоса
        "left": max(0, row.price - row.points) if row.price else 0,
        "percent": round(min(1.0, row.points / row.price) * 100) if row.price else 100,
    }


def visit_payload(visit: Visit | None, now: int) -> dict[str, Any]:
    if visit is None:
        return {}
    training = training_for(visit.stat)
    return {
        "stat": visit.stat.value,
        "title": training.title if training else "Тренировка",
        "emoji": training.emoji if training else "🏋️",
        "seconds_left": visit.seconds_left(now),
        "until": visit.until,
        "over": visit.is_over(now),
    }


def pass_payload(player: Player, until: int, now: int) -> dict[str, Any]:
    from bot.game.clock import club_moment

    live = has_pass(until, now)
    return {
        "active": live,
        "until": club_moment(until) if until else "",
        "days_left": max(0, (until - now) // (24 * 60 * 60)) if live else 0,
        "tickets": [
            {
                "code": ticket.code,
                "title": ticket.title,
                "days": ticket.days,
                "price": ticket.price,
                "note": ticket.note,
                "affordable": player.can_afford(ticket.price),
                # Во сколько обходится день: по нему и видно, что год выгоднее
                "per_day": round(ticket.price / ticket.days, 1),
            }
            for ticket in PASSES
        ],
    }


def build_gym(
    player: Player,
    until: int,
    rows: dict[Stat, Progress],
    visit: Visit | None,
    done: set[str],
    now: int | None = None,
) -> dict[str, Any]:
    """Зал целиком: абонемент, идущий слот, прогресс и расписание."""
    moment = now_ts() if now is None else now
    live = slot_now(moment)
    coming = next_slot(moment)
    return {
        "credits": player.credits,
        "minutes": TRAINING_MINUTES,
        "steps": list(UPGRADE_STEPS),
        "total": total_for(MAX_UPGRADES),
        "pass": pass_payload(player, until, moment),
        # Слот, к которому можно присоединиться прямо сейчас. Пусто — зал
        # закрыт, и тогда страница называет ближайший
        "now": slot_payload(live, moment, live.id in done) if live else {},
        "next": slot_payload(coming, moment, False) if coming else {},
        "visit": visit_payload(visit, moment),
        "progress": [progress_payload(rows[stat]) for stat in rows],
        "schedule": schedule_payload(moment, done),
        "said": "",
    }


__all__ = [
    "build_gym",
    "day_title",
    "pass_payload",
    "progress_payload",
    "schedule_payload",
    "slot_payload",
    "visit_payload",
]
