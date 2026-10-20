"""Общежитие: выдать комнату студенту, запереть её после учёбы.

Правила — в `bot.game.dorm`, здесь дела. Вся служба держится на трёх
мыслях.

**Комнату даёт учёба, а не кошелёк.** Записан на курс — получи комнату;
не записан — нечего и просить. Цены у неё нет вовсе, поэтому нет и
продления: срок комнаты это срок учёбы.

**Учёба кончилась — комната заперта, но не обобрана.** Часов у города
нет, и запирает её всякий взгляд: зашёл в общежитие — комната
досчитана до «сейчас». Вещи при этом остаются за хозяином и ждут его у
администрации.

**Забирают всё разом.** Администрация не разбирает, что из этого нужно
сейчас: отдаёт вещи в рюкзак, деньги из сейфа наличными и освобождает
комнату. После этого можно получать новую — но не раньше, иначе
забытое копилось бы по всему дому.
"""

from __future__ import annotations

import logging

from bot.database import Database, DormRoom
from bot.game.dorm import DORM, ROOM_SLOTS, course_over, room_number, room_title
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.models import Player

logger = logging.getLogger(__name__)


class DormError(Exception):
    """Отказ общежития, который показывают игроку как есть."""


async def sweep(
    db: Database, player: Player, now: int | None = None
) -> DormRoom | None:
    """Досчитать комнату до «сейчас». Отдаёт её нынешнее состояние.

    Учёба кончилась — запираем: часов у города нет, и делает это всякий
    взгляд. Отобрать комнату раньше срока нечему, поэтому обратного
    действия тут нет.
    """
    moment = now_ts() if now is None else now
    room = await db.dorm_room(player.user_id)
    if room is None or room.closed:
        return room
    if not course_over(room.course, player.study_code):
        return room
    await db.close_dorm_room(player.user_id, moment)
    logger.info(
        "Комната %s заперта: боец %s больше не учится",
        room.number, player.user_id,
    )
    return await db.dorm_room(player.user_id)


async def check_in(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Получить комнату. Бесплатно и на всё время учёбы."""
    moment = now_ts() if now is None else now
    _at_dorm(player, moment)
    room = await sweep(db, player, moment)
    if room is not None and room.closed:
        raise DormError(
            "Твоя прежняя комната заперта: сперва забери из неё своё у "
            "администрации."
        )
    if room is not None:
        raise DormError(f"У тебя уже есть {room.title}.")
    if not player.studies:
        raise DormError(
            "Комнату дают студенту: запишись на курс в корпусе, колледже, "
            "академии или университете. Права не считаются — там не учатся "
            "неделями, там сдают билет."
        )
    number = room_number(await db.dorm_numbers())
    if not await db.give_dorm_room(
        player.user_id, number, player.study_code, moment
    ):  # pragma: no cover - вторую комнату не дают
        raise DormError("Комната у тебя уже есть.")
    logger.info(
        "Боец %s получил комнату %s под курс %s",
        player.user_id, number, player.study_code,
    )
    return (
        f"Твоя {room_title(number)}. Живи, пока учишься: платить не надо, "
        f"мест под вещи {ROOM_SLOTS}, сейф в комнате."
    )


async def deposit(
    db: Database, player: Player, amount: int, now: int | None = None
) -> str:
    """Положить кредиты в сейф комнаты."""
    moment = now_ts() if now is None else now
    room = await _open_room(db, player, moment)
    if amount <= 0:
        raise DormError("Сколько положить-то? Напиши число.")
    if not player.can_afford(amount, moment, Service.DORM):
        raise DormError(
            f"В сейф нужно {amount} 💰, а "
            f"{player.purse_note(moment, Service.DORM)}."
        )
    if not await db.move_dorm_vault(player.user_id, amount):  # pragma: no cover
        raise DormError("Комната больше не твоя.")
    player.pay(amount, moment, Service.DORM, about="Взнос в сейф общежития")
    await db.save_player(player)
    fresh = await db.dorm_room(player.user_id)
    return f"В сейфе {fresh.vault if fresh else amount} 💰."


async def withdraw(
    db: Database, player: Player, amount: int, now: int | None = None
) -> str:
    """Взять кредиты из сейфа комнаты — наличными."""
    moment = now_ts() if now is None else now
    room = await _open_room(db, player, moment)
    if amount <= 0:
        raise DormError("Сколько взять-то? Напиши число.")
    if not await db.move_dorm_vault(player.user_id, -amount):
        raise DormError(f"В сейфе {room.vault} 💰 — столько не взять.")
    player.credits += amount
    await db.save_player(player)
    return f"Из сейфа взято {amount} 💰 наличными."


async def stash(
    db: Database, player: Player, item_id: int, now: int | None = None
) -> str:
    """Положить вещь в комнату."""
    moment = now_ts() if now is None else now
    await _open_room(db, player, moment)
    owned = player.find_gear(item_id)
    if owned is None:
        raise DormError("Такой вещи у тебя нет.")
    if owned.is_equipped:
        raise DormError("Сперва сними вещь — надетое в комнату не кладут.")
    if owned.item.is_duty:
        raise DormError(
            f"«{owned.title}» — служебная форма: её место на складе службы."
        )
    if not await db.stash_in_dorm(item_id, player.user_id, ROOM_SLOTS, moment):
        raise DormError(f"В комнате {ROOM_SLOTS} места, и все заняты.")
    player.drop_gear(owned)
    return f"«{owned.item.title}» лежит в комнате."


async def take(
    db: Database, player: Player, kept_id: int, now: int | None = None
) -> str:
    """Забрать вещь из комнаты в рюкзак."""
    from bot.game.equipment import get_item

    moment = now_ts() if now is None else now
    await _open_room(db, player, moment)
    code = await db.take_dorm_item(kept_id, player.user_id)
    if not code:
        raise DormError("Этой вещи в комнате уже нет.")
    player.gear = await db.list_gear(player.user_id)
    item = get_item(code)
    return f"«{item.title if item else code}» теперь у тебя в рюкзаке."


async def collect(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Забрать своё у администрации и освободить комнату.

    Разбирать, что из этого нужно сейчас, администрация не станет:
    отдаёт всё разом — вещи в рюкзак, деньги наличными.
    """
    moment = now_ts() if now is None else now
    _at_dorm(player, moment)
    room = await sweep(db, player, moment)
    if room is None:
        raise DormError("Администрации нечего тебе отдать.")
    if not room.closed:
        raise DormError(
            f"Твоя {room.title} при тебе, пока ты учишься: забирать её "
            "у администрации нечего."
        )
    things, money = await db.clear_dorm_room(player.user_id)
    player.gear = await db.list_gear(player.user_id)
    fresh = await db.get_player(player.user_id)
    if fresh is not None:
        player.credits = fresh.credits
    said = [f"{room.title} сдана."]
    if things:
        said.append(f"Вещей забрано: {things}.")
    if money:
        said.append(f"Из сейфа — {money} 💰 наличными.")
    if not things and not money:
        said.append("Забирать было нечего.")
    return " ".join(said)


# ---------- внутреннее ----------


def _at_dorm(player: Player, moment: int) -> None:
    """Дела общежития делают в общежитии: ключи не носят с собой."""
    if player.where(moment) != DORM:
        raise DormError("Это делают в общежитии, а не отсюда.")


async def _open_room(db: Database, player: Player, moment: int) -> DormRoom:
    """Своя открытая комната. Запертая не пускает в себя никого."""
    _at_dorm(player, moment)
    room = await sweep(db, player, moment)
    if room is None:
        raise DormError("Комнаты у тебя нет: её дают студенту.")
    if room.closed:
        raise DormError(
            "Комната заперта: учёба кончилась. Своё забирают у "
            "администрации, разом."
        )
    return room


__all__ = [
    "DormError",
    "check_in",
    "collect",
    "deposit",
    "stash",
    "sweep",
    "take",
    "withdraw",
]
