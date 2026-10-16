"""Территории глазами мини-аппа: карта точек и что с каждой.

Правил тут нет — их держат `bot.game.turf` и `bot.turf_service`. Здесь
перевод в json.

**Чужой владелец виден целиком.** Кто держит точку, сколько она приносит
и когда за неё дрались — это и есть предмет войны, и прятать его не от
кого: территории на то и территории, чтобы про них знали все.

**Кнопка штурма считается на сервере.** Можно ли идти, решает служба:
своя точка, отдых после боя, потолок в четыре точки и то, воюет ли этот
боец вообще. Страница только рисует ответ.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import format_duration, now_ts
from bot.game.turf import (
    CALM_HOURS,
    MAX_GARRISON,
    MAX_SPOTS_PER_OWNER,
    TOTAL_SPOTS,
    Owner,
    calm_left,
)
from bot.models import Player


async def spot_row(
    db, holding, mine: Owner, moment: int
) -> dict[str, Any]:
    """Строка точки: дом, доход, владелец и можно ли идти на штурм."""
    from bot.turf_service import owner_title

    spot = holding.spot
    owner = holding.owner
    rest = calm_left(holding.fought_at, moment)
    ours = owner.same(mine) and not mine.nobody
    return {
        "code": spot.code,
        "title": spot.title,
        "income": spot.income,
        "note": spot.note,
        "owner": {
            "kind": owner.kind,
            "key": owner.key,
            "title": await owner_title(db, owner),
            "nobody": owner.nobody,
        },
        "mine": ours,
        "taken_at": club_moment(holding.taken_at) if holding.taken_at else "",
        # Отдых после боя: точку не отбивают в ту же минуту
        "calm": rest,
        "calm_text": format_duration(rest) if rest else "",
        "can_storm": bool(not ours and not rest and not mine.nobody),
    }


async def build_turf(
    db, player: Player, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Карта территорий целиком: все точки и что с ними у этого бойца."""
    from bot.turf_service import owner_of, owner_title, survey, vault_of

    moment = now_ts() if now is None else now
    mine = owner_of(player)
    rows = await survey(db, moment)
    spots = [await spot_row(db, one, mine, moment) for one in rows]
    held = [one for one in spots if one["mine"]]
    return {
        "title": "Территории",
        "spots": spots,
        "total": TOTAL_SPOTS,
        # За кого воюет этот боец. Пусто — ни за кого: одиночкам войны
        # за территории не положено
        "side": {
            "kind": mine.kind,
            "key": mine.key,
            "title": await owner_title(db, mine) if not mine.nobody else "",
            "nobody": mine.nobody,
        },
        "held": len(held),
        "income": sum(one["income"] for one in held),
        "vault": await vault_of(db, mine),
        "ceiling": MAX_SPOTS_PER_OWNER,
        "squad": MAX_GARRISON,
        "calm_hours": CALM_HOURS,
        "said": said,
        "note": _note(mine, len(held)),
    }


def _note(mine: Owner, held: int) -> str:
    if mine.nobody:
        return (
            "За территории воюют семьи и службы. Вступи в клан или иди на "
            "службу — тогда и поговорим."
        )
    if not held:
        return (
            "Своих точек пока нет. Ничьи тоже под охраной: брать их "
            "придётся боем."
        )
    return f"Точек у вас {held} из {MAX_SPOTS_PER_OWNER}. Доход капает раз в сутки."


__all__ = ["build_turf", "spot_row"]
