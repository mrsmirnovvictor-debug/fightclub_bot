"""Бонусный пулл глазами мини-аппа: три дела и лестница у каждого.

Правил тут нет — их держат `bot.game.bonuses` и `bot.bonus_service`.
Здесь перевод в json.

**Счёт видят все свои.** Пулл общий: его зарабатывают вместе, и сколько
сделано, должен видеть каждый — мафиози в клане, патрульный в участке.
Решать, кому показывать, по чину значило бы спрятать от человека то,
что он сам и наработал.

**Ступень называется словом, а не процентом.** «Вторая из четырёх, до
следующей шесть нападений» — это ответ, а полоска на три четверти —
загадка.
"""

from __future__ import annotations

from typing import Any

from bot.game.bonuses import (
    SPLITS,
    SPLIT_NOTES,
    SPLIT_TITLES,
    Bonus,
    award_for,
    left_to_next,
    next_step,
    pool_for,
    shown,
    step_of,
)
from bot.game.clock import club_moment
from bot.game.health import format_term, now_ts
from bot.game.turf import Owner


def bonus_row(bonus: Bonus, value: int) -> dict[str, Any]:
    """Одно дело недели: сделано, ступень и что дальше."""
    ahead = next_step(bonus, value)
    return {
        "code": bonus.code,
        "title": bonus.title,
        "note": bonus.note,
        "unit": bonus.unit,
        "done": shown(bonus, value),
        "step": step_of(bonus, value),
        "steps": [
            {"need": one.need, "award": one.award} for one in bonus.steps
        ],
        "award": award_for(bonus, value),
        # Следующая ступень: сколько до неё и сколько она даст. Пусто —
        # взята верхняя, и дальше лестницы нет
        "need": ahead.need if ahead else 0,
        "next_award": ahead.award if ahead else 0,
        "left": left_to_next(bonus, value),
        "top": bonus.top,
    }


async def build_bonus(
    db, owner: Owner, now: int | None = None, can_split: bool = False
) -> dict[str, Any]:
    """Бонусный пулл этой стороны: счёт недели и прошлая выдача.

    Сводит недели заодно: экран бонусов — такое же место, где на счёт
    смотрят, как карта точек, и закрывать неделю при взгляде на неё
    честнее, чем ждать, пока кто-нибудь откроет карту.
    """
    from bot.bonus_service import bonuses_for, scoreboard, sweep

    moment = now_ts() if now is None else now
    bonuses = bonuses_for(owner)
    if owner.nobody or not bonuses:
        return {}
    await sweep(db, moment)
    board = await scoreboard(db, owner, moment)
    tally = board["tally"]
    rows = [bonus_row(one, tally.get(one.code, 0)) for one in bonuses]
    last = board["last"]
    body = {
        "title": "Бонусы недели",
        "rows": rows,
        # Сколько набежало к этому часу и сколько дают за всё разом
        "pool": pool_for(bonuses, tally),
        "top": sum(one.top for one in bonuses),
        "ends_at": board["ends_at"],
        "ends_text": club_moment(board["ends_at"], "%d.%m %H:%M"),
        "left_text": format_term(max(0, board["ends_at"] - moment)),
        # Прошлая неделя: сколько пришло в понедельник. Пусто — ещё не
        # приходило, и врать про ноль не надо
        "last": {
            "amount": last["amount"],
            "paid_text": club_moment(last["paid_at"], "%d.%m"),
        } if last else None,
        "to_vault": owner.kind == "clan",
    }
    if owner.kind == "service":
        body["split"] = await split_payload(db, owner.service, can_split)
    return body


async def split_payload(
    db, code: str, can_split: bool = False
) -> dict[str, Any]:
    """Как служба делит пулл и может ли смотрящий это менять."""
    mode = await db.service_split(code)
    return {
        "mode": mode,
        "title": SPLIT_TITLES.get(mode, mode),
        "note": SPLIT_NOTES.get(mode, ""),
        "can_set": can_split,
        "options": [
            {
                "code": one,
                "title": SPLIT_TITLES[one],
                "note": SPLIT_NOTES[one],
                "mine": one == mode,
            }
            for one in SPLITS
        ],
    }


__all__ = ["bonus_row", "build_bonus", "split_payload"]
