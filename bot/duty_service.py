"""Служебная форма: её выдают должностью и ею же забирают.

Это не покупка и не награда, а **аренда у кресла**. Форму начальника
полиции не продают в лавке, её нельзя выиграть в рейде и нельзя оставить
себе: пока боец сидит в кресле — она на нём, ушёл с должности — вернулась
на склад в кабинет. Поэтому у вещи стоит `duty` с кодом рабочего места, а
не цена.

**Склад — не хранилище, а стойка с формой.** Вещей в ней ровно столько,
сколько в комплекте, и лежат они всегда там же: взятая со стойки вещь не
исчезает из неё, а помечается «у начальника». Это честнее, чем список
ячеек: форма не предмет обстановки, а то, что числится за должностью.

**Чужими руками её не унести.** Служебную вещь не выставляют на рынок, не
кладут в сейф семьи и в кладовую квартиры и не отправляют посылкой:
каждая из этих дверей спрашивает `is_duty` и отвечает отказом. Иначе
достаточно было бы один раз стать начальником, переслать комплект себе же
в мешок и уйти с должности богатым.

Возврат — ленивый, как всё в этом клубе: он случается в тот миг, когда
боец уходит с места (`work_service`), и второй раз ему случаться не с
чем — вещей уже нет.
"""

from __future__ import annotations

import logging

from bot.content.items import CATALOGUE
from bot.database import Database
from bot.game.gear import Item, Slot
from bot.game.health import now_ts
from bot.models import Player

logger = logging.getLogger(__name__)


class DutyError(Exception):
    """Отказ по служебной форме — показывают игроку как есть."""


def kit_for(job_code: str) -> tuple[Item, ...]:
    """Вся форма этой должности, в порядке слотов карточки."""
    if not job_code:
        return ()
    order = list(Slot)
    rows = [one for one in CATALOGUE.values() if one.duty == job_code]
    return tuple(sorted(rows, key=lambda one: order.index(one.slot)))


def duty_items(player: Player) -> list:
    """Служебные вещи, что сейчас на руках у бойца."""
    return [one for one in player.gear if one.item.is_duty]


def strangers(player: Player) -> list:
    """Служебные вещи не по должности: их пора вернуть на склад."""
    return [one for one in duty_items(player) if one.item.duty != player.job_code]


async def take(db: Database, player: Player, code: str) -> str:
    """Взять вещь со стойки. Берёт только тот, кто в кресле."""
    item = CATALOGUE.get(code)
    if item is None or not item.is_duty:
        raise DutyError("Такой формы на складе нет.")
    if player.job_code != item.duty:
        raise DutyError("Эту форму выдают под должность, а не под просьбу.")
    if any(one.code == code for one in player.gear):
        raise DutyError(f"«{item.title}» у тебя уже на руках.")
    await db.add_gear(player.user_id, code)
    player.gear = await db.list_gear(player.user_id)
    logger.info("Форма %s выдана бойцу %s", code, player.user_id)
    return f"«{item.title}» — получено под роспись."


async def hand_back(db: Database, player: Player, code: str = "") -> int:
    """Сдать форму обратно. Пусто — всю, что на руках.

    Надетую снимать не просим: она уходит со слота вместе со строкой в
    базе, и держать её на бойце, у которого должности больше нет, было
    бы страннее, чем снять.
    """
    rows = [
        one
        for one in duty_items(player)
        if not code or one.code == code
    ]
    for one in rows:
        await db.delete_gear(one.id)
    if rows:
        player.gear = await db.list_gear(player.user_id)
        logger.info(
            "Сдано формы: %s, боец %s", len(rows), player.user_id
        )
    return len(rows)


async def collect_the_stray(db: Database, player: Player) -> int:
    """Забрать форму, которая бойцу больше не по должности."""
    rows = strangers(player)
    for one in rows:
        await db.delete_gear(one.id)
    if rows:
        player.gear = await db.list_gear(player.user_id)
        logger.info(
            "Форма вернулась на склад: %s вещей, боец %s",
            len(rows), player.user_id,
        )
    return len(rows)


def rack_rows(player: Player, job_code: str) -> list[dict]:
    """Стойка с формой: что в ней и где эта вещь сейчас."""
    held = {one.code: one for one in player.gear}
    rows = []
    for item in kit_for(job_code):
        owned = held.get(item.code)
        rows.append(
            {
                "code": item.code,
                "title": item.title,
                "slot": item.slot.value,
                "slot_title": item.slot.title,
                "icon": item.emoji,
                "picture": item.picture,
                "taken": owned is not None,
                "worn": bool(owned and owned.is_equipped),
                "where": (
                    "надета" if owned and owned.is_equipped
                    else "на руках" if owned
                    else "на складе"
                ),
            }
        )
    return rows


# ---------- склад кабинета ----------
#
# Полки у службы такие же, как у семьи: вещь переезжает на склад
# целиком, со своим износом и модификацией, и так же возвращается. Кто
# кладёт и кто берёт — решает должность: кабинет один на город, и
# хозяин у него один.

RACK_SLOTS = 15


async def stash(
    db: Database, player: Player, item_id: int, now: int | None = None
) -> str:
    """Положить вещь на склад кабинета."""
    moment = now_ts() if now is None else now
    service = office_of(player)
    if not service:
        raise DutyError("Склад кабинета — дело должности, а не службы.")
    owned = player.find_gear(item_id)
    if owned is None:
        raise DutyError("Такой вещи у тебя нет.")
    if owned.is_equipped:
        raise DutyError("Сперва сними вещь — надетое на полку не кладут.")
    if owned.item.is_duty:
        raise DutyError(
            f"«{owned.title}» и так числится за кабинетом: её сдают, а не "
            "кладут на полку."
        )
    if not await db.stash_service_gear(
        item_id, player.user_id, service, RACK_SLOTS, moment
    ):
        raise DutyError(f"На складе {RACK_SLOTS} ячеек, и все заняты.")
    player.drop_gear(owned)
    logger.info(
        "Боец %s положил вещь %s на склад службы %s",
        player.user_id, item_id, service,
    )
    return f"«{owned.item.title}» лежит на складе кабинета."


async def take_stored(
    db: Database, player: Player, stored_id: int
) -> str:
    """Забрать вещь со склада кабинета в рюкзак."""
    service = office_of(player)
    if not service:
        raise DutyError("Склад кабинета — дело должности, а не службы.")
    code = await db.take_service_item(stored_id, service, player.user_id)
    if not code:
        raise DutyError("Этой вещи на складе уже нет.")
    player.gear = await db.list_gear(player.user_id)
    item = CATALOGUE.get(code)
    logger.info(
        "Боец %s забрал вещь %s со склада службы %s",
        player.user_id, code, service,
    )
    return f"«{item.title if item else code}» у тебя в рюкзаке."


def office_of(player: Player) -> str:
    """Чей кабинет у этого бойца. Пусто — кабинета у него нет.

    Кабинет открывает должность, а не значок: в дежурную часть ходят
    все сотрудники, за склад и казну отвечает один.
    """
    from bot.game.services import service_of_job

    if not kit_for(player.job_code):
        return ""
    service = service_of_job(player.job_code)
    return service.code if service else ""


async def store_rows(db: Database, player: Player) -> list[dict]:
    """Что лежит на складе кабинета — строками для экрана."""
    from bot.webapp.clans import stored_row

    service = office_of(player)
    if not service:
        return []
    moment = now_ts()
    rows = [
        row
        for row in (
            stored_row(player, one, moment)
            for one in await db.service_items(service)
        )
        if row
    ]
    return rows


__all__ = [
    "DutyError",
    "RACK_SLOTS",
    "collect_the_stray",
    "duty_items",
    "hand_back",
    "kit_for",
    "office_of",
    "rack_rows",
    "stash",
    "store_rows",
    "strangers",
    "take",
    "take_stored",
]
