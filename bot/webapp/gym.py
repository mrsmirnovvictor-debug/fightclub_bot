"""Тренажёрный зал глазами мини-аппа.

Правил тут нет — их держат `bot.game.gym` и `bot.gym_service`. Здесь
перевод в json и две заботы сверх перевода.

**Идущий слот отделён от расписания.** Присоединиться можно только к
нему, и кнопка живёт при нём, а не в строке табло: кнопка на каждой из
сорока двух клеток означала бы сорок один отказ.

**Расписание идёт днями, а не сплошным списком.** Сорок две строки
подряд читаются как простыня; день с шестью строками — как день.

**Идущее занятие и отработанное различаются.** На табло это часы против
галочки: боец должен видеть, что стоит на занятии прямо сейчас, а не
только что оно у него было.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from bot.game.classes import Stat
from bot.game.gym import (
    DAY_PRICES,
    MAX_UPGRADES,
    PASSES,
    SCHEDULE_DAYS,
    SLOT_LIMIT,
    TRAINING_MINUTES,
    UPGRADE_GAIN,
    UPGRADE_STEPS,
    VISITS_PER_DAY,
    Slot,
    day_is_full,
    moscow_day,
    next_slot,
    price_of_visit,
    schedule_from,
    slot_is_full,
    slot_now,
    total_for,
    training_for,
)
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.gym_service import Progress, Visit, has_pass
from bot.models import Player
from bot.webapp.bank import purse_payload

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


def slot_payload(
    slot: Slot, now: int, mine: bool | None = None, taken: int = 0
) -> dict[str, Any]:
    """Клетка табло: время, что тренируют, сколько народу и что с ней.

    `mine` — None, если боец в этом занятии не стоял; True — отработал,
    False — стоит прямо сейчас.
    """
    full = slot_is_full(taken)
    if mine is True:
        state = "done"
    elif mine is False:
        state = "training"
    elif slot.ends <= now:
        state = "past"
    elif slot.is_open(now):
        state = "full" if full else ("open" if slot.takes_joiners(now) else "late")
    else:
        state = "full" if full else "ahead"
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
        # Сколько мест занято из скольких — видно до того, как нажмёшь
        "taken": taken,
        "limit": SLOT_LIMIT,
        "full": full,
        # «open» — идёт и пускает, «late» — идёт, но записываться поздно,
        # «full» — мест нет, «training» — боец стоит в нём прямо сейчас,
        # «done» — отработал, «past» — прошёл, «ahead» — впереди
        "state": state,
    }


def schedule_payload(
    now: int,
    mine: dict[str, bool],
    crowd: dict[str, int] | None = None,
    days: int = SCHEDULE_DAYS,
) -> list[dict[str, Any]]:
    """Расписание днями: заголовок дня и шесть его клеток."""
    today = moscow_day(now)
    seats = crowd or {}
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
        board[-1]["slots"].append(
            slot_payload(slot, now, mine.get(slot.id), seats.get(slot.id, 0))
        )
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
        "tickets": [_ticket_payload(player, ticket, now) for ticket in PASSES],
    }


def _ticket_payload(player: Player, ticket, now: int) -> dict[str, Any]:
    """Абонемент на прилавке. Цена — под выбранный кошелёк."""
    price = player.price_here(ticket.price, now, Service.TRAIN)
    return {
        "code": ticket.code,
        "title": ticket.title,
        "days": ticket.days,
        "price": price,
        "full_price": ticket.price,
        "off": ticket.price - price,
        "note": ticket.note,
        "affordable": player.can_afford(price, now, Service.TRAIN),
        # Во сколько обходится день: по нему и видно, что год выгоднее
        "per_day": round(price / ticket.days, 1),
    }


def day_payload(taken: int, player: Player | None = None, now: int = 0) -> dict[str, Any]:
    """Сколько занятий сегодня взято, почём следующее и весь прайс.

    Прайс показывается целиком, а не одной ценой: по лесенке видно, во
    что обойдётся сегодняшний день, если ходить до вечера.
    """
    full = day_is_full(taken)

    def here(price: int) -> int:
        """Цена занятия под тем кошельком, которым боец здесь платит."""
        if player is None or not price:
            return price
        return player.price_here(price, now, Service.TRAIN)

    return {
        "taken": taken,
        "limit": VISITS_PER_DAY,
        "full": full,
        # Ноль — следующее занятие по абонементу, то есть даром
        "price": 0 if full else here(price_of_visit(taken)),
        "free_left": max(0, 1 - taken),
        "prices": [
            {"number": number, "price": here(price), "free": price == 0}
            for number, price in enumerate(DAY_PRICES, start=1)
        ],
    }


def build_gym(
    player: Player,
    until: int,
    rows: dict[Stat, Progress],
    visit: Visit | None,
    mine: dict[str, bool],
    crowd: dict[str, int] | None = None,
    taken_today: int = 0,
    now: int | None = None,
) -> dict[str, Any]:
    """Зал целиком: абонемент, идущий слот, прогресс и расписание."""
    moment = now_ts() if now is None else now
    seats = crowd or {}
    live = slot_now(moment)
    coming = next_slot(moment)
    return {
        "credits": player.credits,
        "minutes": TRAINING_MINUTES,
        "steps": list(UPGRADE_STEPS),
        "total": total_for(MAX_UPGRADES),
        "pass": pass_payload(player, until, moment),
        "day": day_payload(taken_today, player, moment),
        "purse": purse_payload(player, moment, Service.TRAIN),
        # Слот, к которому можно присоединиться прямо сейчас. Пусто — зал
        # закрыт, и тогда страница называет ближайший
        "now": slot_payload(live, moment, mine.get(live.id), seats.get(live.id, 0))
        if live
        else {},
        "next": slot_payload(
            coming, moment, mine.get(coming.id), seats.get(coming.id, 0)
        )
        if coming
        else {},
        "visit": visit_payload(visit, moment),
        "progress": [progress_payload(rows[stat]) for stat in rows],
        "schedule": schedule_payload(moment, mine, seats),
        "said": "",
    }


__all__ = [
    "build_gym",
    "day_payload",
    "day_title",
    "pass_payload",
    "progress_payload",
    "schedule_payload",
    "slot_payload",
    "visit_payload",
]
