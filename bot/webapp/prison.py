"""Тюрьма глазами мини-аппа: свой срок и те, кто сидит рядом.

Правила — в `bot.game.police`, посадка — в `bot.police_service`. Здесь
перевод в json.

**Срок идёт на экране, а не в голове.** До сих пор сидящий узнавал,
сколько ему осталось, только попытавшись выйти: дорога отвечала
отказом и называла срок. Теперь он стоит часами, как у смены и у
рейда, — и по тем же часам, что у всех: час освобождения приезжает с
сервера, а секунды отсчитывает страница.

**Список сидящих видят все, кто вошёл.** Тюрьма — дом города, в него
заходят и снаружи: по списку видно, кого взяли и когда он выйдет. Это
не секрет полиции, а городская сводка — её же рассказывают в баре.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.police import JAIL_TERMS, jail_hours, jail_left
from bot.models import Player


def term_row(
    user_id: int, name: str, until: int, times: int, moment: int,
    mine: bool = False,
) -> dict[str, Any]:
    """Строка сидящего: кто, в который раз и сколько ему осталось."""
    return {
        "user_id": user_id,
        "name": name,
        "times": times,
        "until": until,
        "left": jail_left(until, moment),
        "out_text": club_moment(until, "%d.%m %H:%M"),
        "hours": jail_hours(times),
        "mine": mine,
    }


async def build_prison(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Экран тюрьмы: свой срок, если он есть, и все, кто сидит."""
    moment = now_ts() if now is None else now
    rows = []
    mine = None
    for user_id, until, times in await db.inmates(moment):
        who = await db.get_player(user_id)
        row = term_row(
            user_id,
            who.nickname if who else "боец",
            until,
            times,
            moment,
            mine=user_id == player.user_id,
        )
        rows.append(row)
        if row["mine"]:
            mine = row
    return {
        "title": "Тюрьма",
        # Свой срок — первым делом: сидящему всё остальное пока не важно
        "mine": mine,
        "inmates": rows,
        "total": len(rows),
        # Лестница сроков: по ней видно, чем кончится следующая отсидка
        "terms": list(JAIL_TERMS),
        "note": _note(mine, len(rows)),
    }


def _note(mine: dict[str, Any] | None, total: int) -> str:
    """Строка сверху: про свой срок, а без него — про чужие."""
    if mine:
        return (
            f"Ты сидишь {mine['times']}-й раз. Из тюрьмы не выходят — "
            "ждут. Срок идёт даже в сети."
        )
    if not total:
        return "Камеры пусты: сегодня в городе никого не взяли."
    return f"За решёткой {total}. Сроки идут, выйдут сами."


__all__ = ["build_prison", "term_row"]
