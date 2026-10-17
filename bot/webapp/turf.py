"""Территории глазами мини-аппа: карта точек и что с каждой.

Правил тут нет — их держат `bot.game.turf` и `bot.turf_service`. Здесь
перевод в json.

**Чужой владелец виден целиком.** Кто держит точку, сколько она приносит
и когда за неё дрались — это и есть предмет войны, и прятать его не от
кого: территории на то и территории, чтобы про них знали все.

**Кнопка штурма считается на сервере.** Можно ли идти, решает служба:
своя точка, отдых после боя, потолок в четыре точки, то, воюет ли этот
боец вообще, — и то, стоит ли он у самого дома. Страница только рисует
ответ.

**В особняке — доска, у дома — кнопка.** Список точек с владельцами и
доходом смотрят откуда угодно: это сводка, а не пульт. Созвать отряд
можно только там, где боец стоит ногами, и `here` в строке точки — это
ровно оно.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import format_term, now_ts
from bot.game.classes import BLOCK_WIDTH
from bot.game.hirelings import MAX_POSTED
from bot.game.turf import (
    CALM_HOURS,
    MAX_GARRISON,
    MAX_SPOTS_PER_OWNER,
    SERVICE,
    SERVICE_SHARE,
    TOTAL_SPOTS,
    Owner,
    calm_left,
)
from bot.turf_service import MAX_WAVES
from bot.models import Player


# Сколько разменов журнала уходит в панель. Штурм двух полных отрядов
# — это до сорока волн по пять ударов, и слать все двести в каждом
# опросе незачем: читают последние, а не первые
LOG_TAIL = 60

# Отметка владельца в списке: по ней видно, чья точка, не читая имени.
# Своя — флаг, чужая семья — шляпа, служба — щит, ничья — круг
OWN_MARK = "🚩"
CLAN_MARK = "🎩"
SERVICE_MARK = "🛡"
NOBODY_MARK = "⚪️"


def spot_mark(owner: Owner, ours: bool) -> str:
    """Чем помечена точка в списке."""
    if ours:
        return OWN_MARK
    if owner.nobody:
        return NOBODY_MARK
    return SERVICE_MARK if owner.kind == SERVICE else CLAN_MARK


async def spot_row(
    db, holding, mine: Owner, moment: int, here: str = "",
    posted: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Строка точки: дом, доход, владелец и можно ли идти на штурм."""
    from bot.turf_service import owner_title

    spot = holding.spot
    owner = holding.owner
    rest = calm_left(holding.fought_at, moment)
    ours = owner.same(mine) and not mine.nobody
    standing = spot.code == here
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
        "mark": spot_mark(owner, ours),
        "mine": ours,
        # Сколько нанятых на точке и сколько их туда ещё влезет
        "posted": (posted or {}).get(spot.code, 0),
        "max_posted": MAX_POSTED,
        # Платит ли точка. Брошенная не платит: это дом с вывеской
        "guarded": holding.guarded,
        # Боец стоит у этого дома. Без этого кнопки сбора не бывает:
        # точку берут ногами
        "here": standing,
        "taken_at": club_moment(holding.taken_at) if holding.taken_at else "",
        # Отдых после боя: точку не отбивают в ту же минуту
        "calm": rest,
        # Отдых — сутки, и `format_duration` назвал бы их «1440 мин»
        "calm_text": format_term(rest) if rest else "",
        "can_storm": bool(
            standing and not ours and not rest and not mine.nobody
        ),
    }


async def build_turf(
    db, player: Player, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Карта территорий целиком: все точки и что с ними у этого бойца."""
    from bot.turf_service import owner_of, owner_title, survey, vault_of

    moment = now_ts() if now is None else now
    mine = owner_of(player)
    here = player.where(moment)
    rows = await survey(db, moment)
    standing = await db.posted_counts()
    spots = [
        await spot_row(db, one, mine, moment, here, standing) for one in rows
    ]
    held = [one for one in spots if one["mine"]]
    return {
        "title": "Территории",
        "spots": spots,
        # Где боец стоит сейчас: по этому экран и решает, рисовать ли
        # кнопку сбора хоть у одной строки
        "here": here,
        "at_spot": any(one["here"] for one in spots),
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
        "income": sum(
            int(one["income"] * (SERVICE_SHARE if mine.kind == SERVICE else 1))
            for one in held
            if one["guarded"]
        ),
        "vault": await vault_of(db, mine),
        "ceiling": MAX_SPOTS_PER_OWNER,
        "squad": MAX_GARRISON,
        "calm_hours": CALM_HOURS,
        "said": said,
        # Нанятые: что на руках, что предлагают и кого можно выставить
        "hire": await _hire_rows(db, player, here, spots),
        "note": _note(mine, len(held), any(one["here"] for one in spots)),
    }


async def _hire_rows(
    db, player: Player, here: str, spots: list[dict[str, Any]]
) -> dict[str, Any]:
    """Наём глазами экрана: счёт, прилавок и кого ставить здесь.

    Выставить можно только на своей точке и только стоя на ней — это
    решает служба, а страница рисует ответ.
    """
    from bot.hire_service import roster

    body = await roster(db, player)
    row = next((one for one in spots if one["code"] == here), None)
    body["spot"] = here if row else ""
    body["spot_title"] = row["title"] if row else ""
    free = bool(row and row["mine"] and row["posted"] < MAX_POSTED)
    ours = [one for one in body["mine"] if one["spot"] == here]
    body["can_post"] = bool(free and not ours)
    body["why"] = ""
    if row and not body["can_post"]:
        if not row["mine"]:
            body["why"] = "Охрану ставят на своей точке."
        elif ours:
            body["why"] = "От тебя здесь уже стоит человек."
        else:
            body["why"] = f"На точке уже {MAX_POSTED} — больше не ставят."
    return body


def _note(mine: Owner, held: int, at_spot: bool) -> str:
    if mine.nobody:
        return (
            "За территории воюют семьи и службы. Вступи в клан или иди на "
            "службу — тогда и поговорим."
        )
    if not at_spot:
        # Доска, а не пульт: отсюда видно, кто чем владеет, но взять
        # точку можно только придя к ней
        tail = (
            f"Точек у вас {held} из {MAX_SPOTS_PER_OWNER}."
            if held
            else "Своих точек пока нет."
        )
        return f"{tail} Захват берут на месте: дойди до дома и созови отряд."
    if not held:
        return (
            "Ты на месте. Ничьи точки тоже под охраной: брать их придётся "
            "боем."
        )
    return (
        f"Точек у вас {held} из {MAX_SPOTS_PER_OWNER}. Доход капает раз в "
        "сутки и только с тех, на которых кто-то стоит."
    )


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
        # Лицо есть у нанятых и охраны, у игрока его нет
        "picture": one.picture,
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
        "log": siege.rounds[-LOG_TAIL:],
    }


async def build_war(
    db, player: Player, war, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Карта войны вместе с тем, что у бойца идёт прямо сейчас.

    Ответ всегда полный: панель опрашивает ручку раз в пару секунд, и по
    нему видно, что рисовать — карту, сбор, волну или итог.
    """
    body = await build_turf(db, player, now, said)
    if war is None:
        # Войны может не быть: карту точек собирают и без неё — так её
        # берут снимки и проверки, которым до кнопок дела нет
        body.update({"muster": None, "siege": None, "result": None})
        for row in body["spots"]:
            row["busy"] = False
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


# ---------- отметки войны на карте города ----------


def map_marks(body: dict[str, Any]) -> dict[str, Any]:
    """Война глазами карты: флажок под каждой точкой и плашка под ногами.

    Считается из готового ответа войны, а не заново: два списка одних и
    тех же точек однажды разошлись бы, и карта показала бы одного
    владельца, а особняк другого.
    """
    spots = {
        row["code"]: {
            "mark": row["mark"],
            "owner": row["owner"]["title"],
            "income": row["income"],
            "mine": row["mine"],
        }
        for row in body["spots"]
    }
    row = next((one for one in body["spots"] if one["here"]), None)
    return {"spots": spots, "here": _band(body, row) if row else None}


def _band(body: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Плашка у дома: чей он, сколько приносит и что можно нажать."""
    in_hand = bool(body.get("muster") or body.get("siege") or body.get("result"))
    band = {
        "code": row["code"],
        "title": row["title"],
        "mark": row["mark"],
        "owner": row["owner"]["title"],
        "income": row["income"],
        "mine": row["mine"],
        "in_hand": in_hand,
    }
    if in_hand:
        band["button"] = "open"
        band["text"] = (
            "Штурм идёт." if body.get("siege") else
            "Итог штурма ждёт." if body.get("result") else
            "Отряд собирается."
        )
    elif row["can_storm"]:
        band["button"] = "call"
        band["text"] = f"Держит: {row['owner']['title']}."
    else:
        band["button"] = ""
        band["text"] = _why_not(body, row)
    return band


def _why_not(body: dict[str, Any], row: dict[str, Any]) -> str:
    """Почему кнопки сбора нет. Молча гасить её — значит врать."""
    if row["mine"]:
        return "Точка и так ваша."
    if row.get("busy"):
        return "За точку уже дерутся."
    if row["calm"]:
        return f"Точка отдыхает после боя: {row['calm_text']}."
    if body["side"]["nobody"]:
        return "Воевать за территории тебе пока не за кого."
    return f"Держит: {row['owner']['title']}."


__all__ = [
    "build_turf",
    "build_war",
    "fighter_row",
    "map_marks",
    "muster_payload",
    "siege_payload",
    "spot_row",
]
