"""Выборы глазами мини-аппа: экран администрации и бюллетень.

Экранов тут два, и живут они в разных местах. Первый — администрация:
туда приходят ногами, чтобы выдвинуться, и там же видно весь список
кандидатов с программами. Второй — бюллетень: он не экран, а лист
поверх всего, и приходит сам, потому что голосуют не в доме, а везде.

**Бюллетень приходит ко всем и сразу.** Город выбирает мэра, а не
приходит за этим в окно. Поэтому голосование лежит в карточке — первом
запросе, который делает каждый, — а не за дверью администрации.

**Голосовавшему показываем его отметку, а не бланк.** Голос один и
необратим, и второй бланк был бы обещанием, которого никто не
выполнит.
"""

from __future__ import annotations

from typing import Any

from bot.game.elections import (
    COUNTING,
    NOMINATION,
    PHASE_TITLES,
    QUIET,
    VOTING,
)
from bot.game.health import now_ts
from bot.models import Player


def ballot_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Бюллетень для карточки: лист, отметка или ничего.

    Отдаётся всегда — и в тишине между выборами тоже. Пустой `shown`
    значит ровно одно: показывать нечего, и страница не рисует ничего.
    """
    phase = state["phase"]
    voting = phase == VOTING
    return {
        # Лист показываем только пока идёт голосование: в остальное
        # время ни бланка, ни отметки
        "shown": bool(voting and state["candidates"]),
        "phase": phase,
        "title": PHASE_TITLES.get(phase, ""),
        "day": state["day"],
        "seconds_left": state["seconds_left"],
        "mine": state["mine"],
        "candidates": [
            {"user_id": one["user_id"], "name": one["name"],
             "program": one["program"]}
            for one in state["candidates"]
        ],
        "mayor": state["mayor"],
    }


def build_elections(
    player: Player, state: dict[str, Any], now: int | None = None,
    said: str = "",
) -> dict[str, Any]:
    """Экран администрации: кандидаты, свой голос и что сейчас можно."""
    moment = now_ts() if now is None else now
    phase = state["phase"]
    return {
        "title": "Администрация города",
        "day": state["day"],
        "phase": phase,
        "phase_title": PHASE_TITLES.get(phase, ""),
        "seconds_left": state["seconds_left"],
        "candidates": state["candidates"],
        "total": len(state["candidates"]),
        "mine": state["mine"],
        "standing": state["standing"],
        "mayor": state["mayor"],
        "program_max": state["program_max"],
        "can_nominate": bool(phase == NOMINATION and not state["standing"]),
        "can_vote": bool(phase == VOTING and not state["mine"]),
        "counting": phase == COUNTING,
        "said": said,
        "note": _note(state),
    }


def _note(state: dict[str, Any]) -> str:
    phase = state["phase"]
    total = len(state["candidates"])
    seat = state["mayor"]["name"]
    chair = f"Мэр города — {seat}." if seat else "Кресло мэра пустует."
    if phase == QUIET:
        return (
            f"{chair} Выдвижение откроется в среду, в девять утра на "
            "неделе выборов."
        )
    if phase == NOMINATION:
        tail = (
            f"Уже выдвинулись: {total}."
            if total
            else "Пока не выдвинулся никто."
        )
        return f"Идёт выдвижение. {tail} Приём до субботы, до восьми вечера."
    if phase == VOTING:
        if state["mine"]:
            return f"Ты уже проголосовал. {chair}"
        return f"Идут выборы мэра. Голос один, передумать нельзя."
    if phase == COUNTING:
        return "Голосование закрыто. Новый мэр заступит в понедельник, в девять."
    return f"Список кандидатов закрыт. Голосование — в воскресенье с восьми. {chair}"


__all__ = ["ballot_payload", "build_elections"]
