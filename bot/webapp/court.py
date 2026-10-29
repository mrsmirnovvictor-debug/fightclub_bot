"""Суд глазами мини-аппа: дела об угоне и приговоры по ним.

Экран один, а читают его двое. **Судья** видит список дел: кого судить,
за что, который это угон по счёту и какая по лестнице выходит мера.
**Всякий остальной** видит только своё: что ему вынесли и что с него
за это возьмут.

**Приговор не торгуется.** Кнопка одна, и мера на ней написана заранее:
лестница считается по делу, а не по настроению судьи. Судья решает, быть
приговору или нет, а не сколько дать, — иначе одно и то же дело стоило бы
разного у разных кресел.

**Заочно — это нормально.** Угонщика в зале не ждут: приговор выносят и
без него, а исполняется он в тот миг, когда тот появится в городе.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.court import EXTRA_PER, FINES, JAIL_HOURS, sentence_text
from bot.game.health import now_ts
from bot.game.locations import get_location
from bot.models import Player


def mine_row(case: dict) -> dict[str, Any]:
    """Своё дело: что вынесли и исполнено ли."""
    return {
        "id": int(case["id"]),
        "plate": str(case["plate"]),
        "fine": int(case["fine"]),
        "jail_hours": int(case["jail_hours"]),
        "sentence": sentence_text(int(case["fine"]), int(case["jail_hours"])),
        "judged": club_moment(int(case["judged_at"]))
        if case["judged_at"] else "",
        "served": bool(case["served_at"]),
    }


async def build_court(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Экран суда: список дел для судьи и свои приговоры для всех."""
    from bot.court_service import docket, judges

    moment = now_ts() if now is None else now
    place = get_location(player.where(moment))
    robe = judges(player)
    mine = [
        mine_row(one)
        for one in await db.theft_cases(judged=True, thief_id=player.user_id)
    ]
    body: dict[str, Any] = {
        "title": place.title if place else "Суд",
        "robe": robe,
        "cases": await docket(db, moment) if robe else [],
        "mine": mine,
        "debt": player.account_debt,
        "ladder": [
            {"times": number + 1, "fine": fine, "jail_hours": hours}
            for number, (fine, hours) in enumerate(zip(FINES, JAIL_HOURS))
        ],
        "extra_per": EXTRA_PER,
        "said": "",
    }
    body["note"] = _note(body)
    return body


def _note(body: dict[str, Any]) -> str:
    if body["debt"]:
        return (
            f"Долг по штрафу — {body['debt']} 💰. Пока он есть, счёт "
            "заблокирован: пополни его, и долг спишется."
        )
    if body["robe"]:
        waiting = len(body["cases"])
        return (
            f"Дел к рассмотрению: {waiting}. Мера считается по лестнице — "
            "судья решает, быть приговору или нет."
            if waiting
            else "Дел к рассмотрению нет. Город пока никого не угонял."
        )
    if body["mine"]:
        return "Здесь судят за угон. Твои дела — ниже."
    return (
        "Здесь судят за угон. Приговор выносят и заочно, а исполняется "
        "он, когда осуждённый появится в городе."
    )


__all__ = ["build_court", "mine_row"]
