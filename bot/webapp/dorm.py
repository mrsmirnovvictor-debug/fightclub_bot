"""Общежитие глазами мини-аппа: комната, сейф и полки.

Правил тут нет — их держат `bot.game.dorm` и `bot.dorm_service`. Здесь
перевод в json.

**Экран отвечает на один вопрос: есть ли у тебя комната.** Нет и не
учишься — записка, куда идти записываться. Учишься — кнопка «Получить
комнату». Есть — полки, сейф и срок, который измеряется не сутками, а
учёбой. Заперта — одна кнопка: забрать своё у администрации.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.dorm import ROOM_SLOTS
from bot.game.estate import Kept
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.study import get_course
from bot.models import Player
from bot.webapp.bank import purse_payload
from bot.webapp.estate import kept_row


def room_payload(room, player: Player, items: list[Kept], moment: int) -> dict:
    """Своя комната: номер, курс, полки и сейф. Пусто — комнаты нет."""
    if room is None:
        return {
            "mine": False, "closed": False, "number": 0, "title": "",
            "course": "", "course_title": "", "given": "",
            "slots": ROOM_SLOTS, "free": ROOM_SLOTS, "vault": 0,
            "items": [], "bag": [],
        }
    from bot.webapp.card import item_payload

    course = get_course(room.course)
    rows = [row for row in (kept_row(player, one, moment) for one in items) if row]
    return {
        "mine": True,
        "closed": room.closed,
        "number": room.number,
        "title": room.title,
        "course": room.course,
        "course_title": course.title if course else "",
        "given": club_moment(room.given_at) if room.given_at else "",
        "closed_at": club_moment(room.closed_at) if room.closed_at else "",
        "slots": ROOM_SLOTS,
        "free": max(0, ROOM_SLOTS - len(rows)),
        "vault": room.vault,
        "items": rows,
        # Что можно положить: снятое из рюкзака. Надетое на полку не
        # ложится — сперва его снимают в инвентаре
        "bag": [item_payload(player, owned) for owned in player.backpack],
    }


async def build_dorm(db, player: Player, now: int | None = None) -> dict[str, Any]:
    """Экран общежития: комната студента и всё, что в ней."""
    from bot.dorm_service import sweep

    moment = now_ts() if now is None else now
    # Взгляд на дом досчитывает комнату: учёба могла кончиться, пока
    # сюда не заходили
    room = await sweep(db, player, moment)
    items = await db.dorm_items(player.user_id) if room is not None else []
    place = get_location(player.where(moment))
    course = get_course(player.study_code)
    body: dict[str, Any] = {
        "title": place.title if place else "Общежитие",
        "slots": ROOM_SLOTS,
        "studies": player.studies,
        "study_title": course.title if course else "",
        "purse": purse_payload(player, moment, Service.DORM),
        "credits": player.credits,
        "room": room_payload(room, player, items, moment),
        "can_take": bool(player.studies and room is None),
        "said": "",
    }
    body["note"] = _note(body)
    return body


def _note(body: dict[str, Any]) -> str:
    """Строка сверху: что тут делают и что мешает."""
    room = body["room"]
    if room["closed"]:
        return (
            f"{room['title'].capitalize()} заперта: учёба кончилась. "
            "Своё забирают у администрации — разом, вещи в рюкзак, "
            "деньги наличными."
        )
    if room["mine"]:
        return (
            f"Твоя {room['title']}: живёшь, пока учишься "
            f"(«{room['course_title']}»). Платить не надо."
        )
    if body["studies"]:
        return (
            f"Комната студенту даётся бесплатно, на всё время учёбы: "
            f"{body['slots']} места под вещи и сейф. Ты учишься "
            f"(«{body['study_title']}») — комнату дадут."
        )
    return (
        "Комнату дают студенту: запишись на курс в корпусе, колледже, "
        "академии или университете. Права не считаются — там не учатся "
        "неделями, там сдают билет."
    )


__all__ = ["build_dorm", "room_payload"]
