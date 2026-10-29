"""Кто здесь: люди в этом доме и драки, которые в нём идут.

Экран отвечает на два вопроса сразу. Первый — «кто рядом»: город
большой, а знать, с кем ты стоишь в одной комнате, нельзя было нигде,
кроме рынка. Второй — «что тут происходит»: ограбление идёт прямо в
доме, и видеть его должны все, кто в нём стоит.

**Список — не рейтинг.** Сначала те, кто в сети: с ними что-то можно
сделать прямо сейчас. Остальные — ниже и серым: их карточку можно
открыть, но позвать или взять за карман — нет.

**Кнопку нападения считает сервер.** Своя семья, покой ограбленного,
лицензия, чужой бой, здоровье обоих — всё это решает
`bot.robbery_service`, а страница только рисует ответ и называет
причину отказа. Спрятанная кнопка обходится запросом мимо интерфейса.
"""

from __future__ import annotations

from typing import Any

from bot.game.classes import BLOCK_WIDTH
from bot.game.battle import RED
from bot.game.health import format_term, now_ts
from bot.game.presence import is_online, presence_text
from bot.game.robbery import (
    LICENCE_PRICE,
    ATTACKS_PER_LICENCE,
    LOOT_SHARE,
    MAX_SIDE,
    SAFE_HOURS,
    safe_left,
)
from bot.models import Player

# Сколько разменов журнала уходит в панель. Драка короче штурма, но и
# тут читают последние, а не первые
LOG_TAIL = 60


def fighter_row(
    one, user_id: int, name: str, viewer_id: int, acted: bool = False
) -> dict[str, Any]:
    """Боец в панели драки: имя, запас, жив ли он и сделал ли ход."""
    return {
        "user_id": user_id,
        "name": name,
        "hp": max(0, one.hp),
        "max_hp": one.max_hp,
        "percent": max(0, round(one.hp / one.max_hp * 100)) if one.max_hp else 0,
        "alive": one.alive,
        "level": one.level,
        "fclass": one.fclass.emoji,
        # Лицо есть у нанятых и охраны, у игрока его нет
        "picture": one.picture,
        "is_self": user_id == viewer_id,
        "acted": acted,
    }


def side_rows(
    raid, side: int, viewer_id: int, ours: bool = False
) -> list[dict[str, Any]]:
    """Сторона целиком. Галочку хода отдаём только своим.

    Чужая готовность — не наше дело: на доске её не показывают, и в
    ответе ей тоже делать нечего.
    """
    return [
        fighter_row(
            raid.fighters[one], one, raid.names.get(one, "боец"), viewer_id,
            ours and one in raid.acted,
        )
        for one in raid.side_ids(side)
    ]


def raid_payload(raid, viewer_id: int) -> dict[str, Any]:
    """Панель драки: обе стороны, что нажато и чем кончилось."""
    from bot.webapp.fight import (
        ATTACK_BUTTONS,
        abilities_payload,
        block_buttons,
        hands_payload,
    )

    mine = raid.choices.get(viewer_id)
    fighter = raid.fighters.get(viewer_id)
    foe = raid.foe_of(viewer_id)
    side = raid.side_of(viewer_id) if viewer_id in raid.fighters else None
    return {
        "id": raid.id,
        "kind": raid.kind,
        "place": raid.place,
        "place_title": raid.place_title,
        "attacker": raid.names.get(raid.attacker, "налётчик"),
        "victim": raid.names.get(raid.victim, "боец"),
        "round": raid.round_number,
        "finished": raid.finished,
        "won": raid.won,
        "loot": raid.loot,
        "bonus": raid.bonus,
        "summary": raid.summary,
        "hands": hands_payload(fighter),
        "attacks": [dict(row) for row in ATTACK_BUTTONS],
        "blocks": block_buttons(fighter.block_width if fighter else BLOCK_WIDTH),
        "red": side_rows(raid, RED, viewer_id, side == RED),
        "blue": side_rows(raid, 1 - RED, viewer_id, side == 1 - RED),
        # Кого смотрящий бьёт в этом раунде. Пусто — пары не досталось:
        # сторона больше другой, и кто-то раунд пропускает
        "foe": (
            fighter_row(foe, _id_of(raid, foe), _name_of(raid, foe), viewer_id)
            if foe is not None
            else None
        ),
        "yours": viewer_id in raid.fighters,
        "side": side,
        "alive": bool(fighter and fighter.alive),
        "acted": viewer_id in raid.acted,
        "paired": viewer_id in raid.paired,
        "seconds_left": raid.seconds_left(),
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
        "log": raid.rounds[-LOG_TAIL:],
    }


def _id_of(raid, fighter) -> int:
    return next(
        (one for one, who in raid.fighters.items() if who is fighter), 0
    )


def _name_of(raid, fighter) -> str:
    return raid.names.get(_id_of(raid, fighter), "боец")


def raid_row(
    raid, player: Player, clan_id: int, badge: bool = False
) -> dict[str, Any]:
    """Строка идущей драки в списке дома: кто кого и можно ли вмешаться."""
    sides = {
        "red": [raid.names.get(one, "боец") for one in raid.side_ids(RED)],
        "blue": [raid.names.get(one, "боец") for one in raid.side_ids(1 - RED)],
    }
    side = None
    if clan_id:
        for number in (RED, 1 - RED):
            if any(raid.clans.get(one) == clan_id for one in raid.side_ids(number)):
                side = number
                break
    full = side is not None and len(raid.side_ids(side)) >= MAX_SIDE
    # Сторона полицейского: где уже есть значок, а если нет — за того,
    # на кого напали. Лицензия ему не нужна, это работа
    badge_side = _badge_side(raid)
    badge_full = len(raid.side_ids(badge_side)) >= MAX_SIDE
    inside = player.user_id in raid.fighters
    return {
        "id": raid.id,
        "kind": raid.kind,
        "attacker": raid.names.get(raid.attacker, "налётчик"),
        "victim": raid.names.get(raid.victim, "боец"),
        "round": raid.round_number,
        "sides": sides,
        "mine": inside,
        "side": side,
        "side_title": "" if side is None else (
            "за налётчика" if side == RED else "за жертву"
        ),
        "can_join": bool(side is not None and not full and not inside),
        "badge_side": badge_side,
        "can_step_in": bool(badge and not badge_full and not inside),
        "why": (
            _why_not_step_in(raid, player, badge_full)
            if badge
            else _why_not_join(raid, player, clan_id, side, full)
        ),
    }


def _badge_side(raid) -> int:
    for side in (RED, 1 - RED):
        if any(one in raid.badges for one in raid.side_ids(side)):
            return side
    return raid.side_of(raid.victim)


def _why_not_step_in(raid, player: Player, full: bool) -> str:
    if player.user_id in raid.fighters:
        return "Ты в этой драке."
    if full:
        return f"На этой стороне мест нет: больше {MAX_SIDE} не лезут."
    return ""


def _why_not_join(raid, player: Player, clan_id: int, side, full: bool) -> str:
    if player.user_id in raid.fighters:
        return "Ты в этой драке."
    if not clan_id:
        return "Вмешиваются за семью. У тебя её нет."
    if side is None:
        return "Это не твоя драка: ни одна сторона тебе не своя."
    if full:
        return f"На твоей стороне мест нет: больше {MAX_SIDE} не лезут."
    return ""


async def neighbour_row(
    db, one: Player, player: Player, clan_id: int, mine: int, moment: int,
    busy: bool, badge: bool = False, wanted: bool = False,
) -> dict[str, Any]:
    """Сосед по дому: кто он, в сети ли и можно ли его взять."""
    online = is_online(one.seen_at, moment)
    rest = safe_left(await db.robbed_at(one.user_id), moment)
    same = bool(mine and clan_id == mine)
    return {
        # Розыск и значок: по ним считается кнопка задержания. Покой
        # ограбленного тут ни при чём — он защищает от мафии, а не от
        # полиции, и прятаться за ним от ордера нельзя
        "wanted": wanted,
        "can_arrest": bool(
            badge and wanted and online and not busy and one.can_fight(moment)
        ),
        "user_id": one.user_id,
        "name": one.nickname,
        "level": one.level,
        "clan": "",
        "online": online,
        "presence": presence_text(one.seen_at, moment),
        "same_clan": same,
        "calm": rest,
        "calm_text": format_term(rest) if rest else "",
        "busy": busy,
        "can_rob": bool(
            mine and online and not same and not rest and not busy
            and one.can_fight(moment)
        ),
        # Машина под соседом: её угоняют, и для этого в сети он быть не
        # обязан. Поля заполняет `car_mark` — тут только место под них
        "car": {},
        # Угнанная машина в его руках — для значка. Заполняет
        # `wanted_car_mark`, и видит её только патруль
        "wanted_car": {},
    }


async def car_mark(
    db, player: Player, one: Player, moment: int
) -> dict[str, Any]:
    """Машина под этим соседом: что за неё и можно ли её увести.

    Угон не спрашивает, в сети ли хозяин: машина стоит в городе, а не в
    кармане. Зато спрашивает, не поставил ли он её на охраняемое место
    и бил ли ты его только что, — это решает `stealable`.
    """
    from bot.car_service import stealable
    from bot.game.cars import get_car

    code = one.driving(moment)
    if not code:
        return {}
    row = await db.car_row(one.user_id, code)
    if row is None:
        return {}
    full = dict(
        row,
        user_id=one.user_id,
        driving=code,
        seen_at=one.seen_at,
        location=one.where(moment),
    )
    car = get_car(code)
    why = await stealable(db, player, full, moment)
    return {
        "id": int(row["id"]),
        "code": code,
        "title": car.title if car else code,
        "plate": str(row.get("plate") or ""),
        "can_steal": not why,
        "why": why,
    }


async def wanted_car_mark(
    db, player: Player, one: Player, moment: int
) -> dict[str, Any]:
    """Разыскиваемая машина в руках этого соседа — глазами патрульного.

    Угнанную держат на руках, а не под рулём: отдельная отметка нужна
    именно поэтому. Спящему угонщику машину не возвращают дракой — её
    просто забирают; того, кто в сети, задерживают.
    """
    from bot.game.cars import get_car
    from bot.police_service import serves_in_police, theft_of

    if not serves_in_police(player) or one.user_id == player.user_id:
        return {}
    for row in await db.cars_of(one.user_id):
        if row["until"] and int(row["until"]) <= moment:
            continue
        if await theft_of(db, dict(row)) is None:
            continue
        car = get_car(str(row["code"]))
        return {
            "id": int(row["id"]),
            "title": car.title if car else str(row["code"]),
            "plate": str(row["plate"] or ""),
            "can_take": not is_online(one.seen_at, moment),
        }
    return {}


def _why_not_arrest(row: dict[str, Any]) -> str:
    """Почему нельзя задержать этого."""
    if not row["wanted"]:
        return "На него нет ордера."
    if not row["online"]:
        return "Не в сети."
    if row["busy"]:
        return "Уже дерётся."
    return "Еле стоит. Не сегодня."


def _why_not_rob(row: dict[str, Any], mine: int, licences: int) -> str:
    """Почему нельзя взять этого. Молча гасить кнопку — значит врать."""
    if not mine:
        return "Грабят семьями. Одиночке лицензию не выпишут."
    if row["same_clan"]:
        return "Свои."
    if not row["online"]:
        return "Не в сети."
    if row["busy"]:
        return "Уже дерётся."
    if row["calm"]:
        return f"Недавно обобрали: покой ещё {row['calm_text']}."
    if not row["can_rob"]:
        return "Еле стоит. Не сегодня."
    if not licences:
        return "Нет лицензии: её берут в особняке."
    return ""


async def build_crowd(
    db, player: Player, service, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Дом целиком: кто в нём стоит, что в нём идёт и что можно нажать."""
    from bot.game.locations import get_location

    from bot.police_service import serves_in_police, theft_suspects

    moment = now_ts() if now is None else now
    here = player.where(moment)
    place = get_location(here)
    clan_id = player.clan.clan.id if player.clan else 0
    badge = serves_in_police(player)
    hunted = {int(one["suspect_id"]) for one in await db.wanted()}
    # Угон — тоже ордер: кто держит разыскуемую машину, того задерживают
    # так же, как грабителя. Считаем это только для значка: остальным
    # список розыска ни к чему
    if badge:
        hunted |= await theft_suspects(db)

    body: dict[str, Any] = {
        "place": here,
        "place_title": place.title if place else here,
        "credits": player.credits,
        "badge": badge,
        "clan": player.clan.clan.name if player.clan else "",
        "share": round(LOOT_SHARE * 100),
        "safe_hours": SAFE_HOURS,
        "max_side": MAX_SIDE,
        "price": LICENCE_PRICE,
        "pack": ATTACKS_PER_LICENCE,
        "said": said,
        "crowd": [],
        "fights": [],
        "fight": None,
        "result": None,
        "licences": {"left": 0, "bought": 0, "pack": ATTACKS_PER_LICENCE,
                     "price": LICENCE_PRICE},
    }
    if service is None:  # pragma: no cover - бот без службы не поднимается
        body["note"] = _note(body, clan_id)
        return body

    body["licences"] = await service.licences(player.user_id, moment)
    live = service.of_user(player.user_id)
    result = service.result_of(player.user_id)
    body["fight"] = raid_payload(live, player.user_id) if live else None
    body["result"] = raid_payload(result, player.user_id) if result else None
    body["fights"] = [
        raid_row(one, player, clan_id, badge) for one in service.at(here)
    ]

    here_now = [
        one
        for one in await db.players_at(here)
        if one.user_id != player.user_id
        and one.where(moment) == here
        and not one.in_transit(moment)
    ]
    # Кто в сети — выше: с ним что-то можно сделать прямо сейчас
    here_now.sort(key=lambda one: (not is_online(one.seen_at, moment), -one.seen_at))
    clans = await db.clans_of([one.user_id for one in here_now])
    left = body["licences"]["left"]
    rows = []
    for one in here_now:
        his_clan, name = clans.get(one.user_id, (0, ""))
        row = await neighbour_row(
            db, one, player, his_clan, clan_id, moment,
            busy=service.is_busy(one.user_id),
            badge=badge, wanted=one.user_id in hunted,
        )
        row["clan"] = name
        row["can_rob"] = row["can_rob"] and bool(left)
        row["car"] = await car_mark(db, player, one, moment)
        if badge:
            row["wanted_car"] = await wanted_car_mark(db, player, one, moment)
        # Сотруднику говорим про ордер, остальным — про лицензию: у них
        # разные кнопки, и причина отказа у каждой своя
        row["why"] = (
            _why_not_arrest(row) if badge and not row["can_arrest"]
            else "" if badge
            else _why_not_rob(row, clan_id, left)
        )
        rows.append(row)
    body["crowd"] = rows
    body["note"] = _note(body, clan_id)
    return body


async def crowd_marks(db, player: Player, service, now: int | None = None
                      ) -> dict[str, Any]:
    """Счётчик для карты: сколько народу в доме и сколько в нём драк.

    Считается отдельно от экрана и нарочно коротко: карта открывается
    на каждом шаге по городу, и тащить ради значка в углу полсотни
    соседей со всеми их отказами было бы дорого ни за что.
    """
    moment = now_ts() if now is None else now
    here = player.where(moment)
    rows = [
        one
        for one in await db.players_at(here)
        if one.user_id != player.user_id
        and one.where(moment) == here
        and not one.in_transit(moment)
    ]
    return {
        "total": len(rows),
        "online": sum(1 for one in rows if is_online(one.seen_at, moment)),
        "fights": len(service.at(here)) if service is not None else 0,
    }


def _note(body: dict[str, Any], clan_id: int) -> str:
    # Названия домов лежат в именительном, и падежей у них нет. Поэтому
    # дом ставим через двоеточие, а не внутрь фразы: «драка в бар» —
    # хуже, чем «драка идёт прямо сейчас: Бар»
    where = body["place_title"]
    if body["fight"]:
        return f"Драка идёт прямо сейчас: {where}."
    total = len(body["crowd"])
    online = sum(1 for one in body["crowd"] if one["online"])
    if not total:
        return f"Тут больше никого: {where}."
    head = f"Здесь ещё {total}, из них в сети {online}."
    if not clan_id:
        return head
    left = body["licences"]["left"]
    tail = (
        f"Нападений на руках: {left}."
        if left
        else "Нападений на руках нет — лицензию берут в особняке."
    )
    return f"{head} {tail}"


__all__ = [
    "LOG_TAIL",
    "build_crowd",
    "crowd_marks",
    "fighter_row",
    "neighbour_row",
    "raid_payload",
    "raid_row",
    "side_rows",
    "wanted_car_mark",
]
