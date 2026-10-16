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
from bot.game.classes import BLOCK_WIDTH
from bot.game.turf import (
    CALM_HOURS,
    MAX_GARRISON,
    MAX_SPOTS_PER_OWNER,
    TOTAL_SPOTS,
    Owner,
    calm_left,
)
from bot.turf_service import MAX_WAVES
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


# ---------- живая осада ----------


def fighter_row(one, viewer_id: int = 0) -> dict[str, Any]:
    """Боец в панели: имя, запас и жив ли он ещё."""
    return {
        "user_id": one.user_id,
        "name": one.name,
        "hp": max(0, one.hp),
        "max_hp": one.max_hp,
        "percent": max(0, round(one.hp / one.max_hp * 100)) if one.max_hp else 0,
        "alive": one.alive,
        "level": one.level,
        "fclass": one.fclass.emoji,
        "is_self": one.user_id == viewer_id,
    }


def muster_payload(muster, viewer_id: int, seconds: int) -> dict[str, Any]:
    """Сбор отряда: куда идут, кто записан и сколько ждать."""
    return {
        "id": muster.id,
        "spot": muster.spot.code,
        "spot_title": muster.spot.title,
        "income": muster.spot.income,
        "leader": muster.leader,
        "mine": viewer_id in muster.members,
        "is_leader": viewer_id == muster.leader,
        "members": [
            {"user_id": one, "name": name, "is_self": one == viewer_id}
            for one, name in muster.members.items()
        ],
        "total": len(muster.members),
        "max": MAX_GARRISON,
        "seconds_left": muster.seconds_left(seconds),
    }


def siege_payload(siege, viewer_id: int) -> dict[str, Any]:
    """Панель штурма: гарнизон, отряд, что нажато и чем кончилось."""
    from bot.webapp.fight import (
        ATTACK_BUTTONS,
        abilities_payload,
        block_buttons,
        hands_payload,
    )

    mine = siege.choices.get(viewer_id)
    fighter = siege.party.get(viewer_id)
    foe = siege.foe_of(viewer_id)
    return {
        "id": siege.id,
        "spot": siege.spot.code,
        "spot_title": siege.spot.title,
        "income": siege.spot.income,
        "defender": siege.defender_title,
        "wave": siege.wave,
        "max_waves": MAX_WAVES,
        "finished": siege.finished,
        "won": siege.won,
        "summary": siege.summary,
        "hands": hands_payload(fighter),
        # Кнопки зон: сколько рук, столько столбцов удара, плюс блок
        "attacks": [dict(row) for row in ATTACK_BUTTONS],
        "blocks": block_buttons(fighter.block_width if fighter else BLOCK_WIDTH),
        # Весь гарнизон по порядку: по нему видно, сколько ещё стоит
        "wall": [fighter_row(one) for one in siege.wall.values()],
        "party": [fighter_row(one, viewer_id) for one in siege.party.values()],
        # Кого смотрящий бьёт сейчас. Пусто — некого: его защитника
        # добили, а следующий достанется со следующего размена
        "foe": fighter_row(foe) if foe is not None else None,
        "yours": viewer_id in siege.party,
        "alive": bool(fighter and fighter.alive),
        "acted": viewer_id in siege.acted,
        "seconds_left": siege.seconds_left(),
        "chosen": {
            "attacks": {
                str(hand): zone.value
                for hand, zone in (mine.attacks.items() if mine else ())
            },
            "block": mine.block[0].value if mine and mine.block else None,
        },
        # Приёмы — только свои: чужая заготовка перестаёт быть
        # неожиданностью, если её видно
        "abilities": abilities_payload(fighter),
        "log": siege.rounds,
    }


async def build_war(
    db, player: Player, war, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Карта войны вместе с тем, что у бойца идёт прямо сейчас.

    Ответ всегда полный: панель опрашивает ручку раз в пару секунд, и по
    нему видно, что рисовать — карту, сбор, волну или итог.
    """
    body = await build_turf(db, player, now, said)
    if war is None:  # pragma: no cover - бот без войны не поднимается
        body.update({"muster": None, "siege": None, "result": None})
        return body
    muster = war.muster_of_user(player.user_id)
    siege = war.siege_of_user(player.user_id)
    result = war.result_of_user(player.user_id)
    body["muster"] = (
        muster_payload(muster, player.user_id, war.lobby_seconds)
        if muster is not None
        else None
    )
    body["siege"] = siege_payload(siege, player.user_id) if siege else None
    body["result"] = siege_payload(result, player.user_id) if result else None
    # Где уже дерутся: на такую точку второй отряд не собирают
    busy = {one.spot.code for one in war.musters()} | {
        one.spot.code for one in war.sieges()
    }
    for row in body["spots"]:
        if row["code"] in busy:
            row["can_storm"] = False
            row["busy"] = True
        else:
            row["busy"] = False
    return body


__all__ = [
    "build_turf",
    "build_war",
    "fighter_row",
    "muster_payload",
    "siege_payload",
    "spot_row",
]
