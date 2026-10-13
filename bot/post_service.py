"""Почта: отправить посылку, получить её и вернуть, если не забрали.

Правила держит `bot.game.post`, хранение — `bot.database`, а здесь то,
что меняет состояние: списать пять кредитов, снять вещь с рюкзака,
выдать посылку и перевести наложенный платёж отправителю.

**Место проверяется здесь, а не на странице.** И отправляют, и получают
на почте: спрятанная кнопка обходится запросом мимо интерфейса.

**Просрочка сводится на месте, без часов.** Будильников в клубе нет:
посылки смотрят на почте, и в ту же минуту сюда заходят через `sweep`.
Срок вышел — посылка разворачивается и ждёт отправителя уже без срока и
без наложенного платежа.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.equipment import get_item
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.post import (
    MAX_WAITING,
    SEND_PRICE,
    Parcel,
    get_term,
    price_problem,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class PostError(Exception):
    """Отказ по почте, который показывают игроку как есть."""


def _at_post(player: Player, moment: int, what: str) -> None:
    place = get_location(player.where(moment))
    if place is None or not place.allows(Service.POST):
        raise PostError(f"{what} можно на почте, а не отсюда.")


async def _mine(db: Database, player: Player, parcel_id: int) -> Parcel:
    """Посылка, которую этот боец может взять с полки."""
    parcel = await db.parcel(parcel_id)
    if parcel is None or parcel.owner != player.user_id:
        raise PostError("Такой посылки на почте нет.")
    return parcel


# ---------- отправить ----------


async def send(
    db: Database,
    player: Player,
    item_id: int,
    nickname: str,
    price: int = 0,
    term: str = "",
    now: int | None = None,
) -> str:
    """Отправить вещь бойцу: пять кредитов, цена и срок хранения."""
    moment = now_ts() if now is None else now
    _at_post(player, moment, "Отправить посылку")

    name = (nickname or "").strip()
    if not name:
        raise PostError("Кому отправляем? Напиши ник.")
    guest = await db.find_by_nickname(name)
    if guest is None:
        raise PostError(f"Бойца с ником «{name}» в клубе нет.")
    if guest.user_id == player.user_id:
        raise PostError("Себе посылки не отправляют: вещь и так у тебя.")

    trouble = price_problem(price)
    if trouble:
        raise PostError(trouble)
    keep = get_term(term)
    if keep is None:
        raise PostError("Такого срока хранения на почте нет.")

    owned = player.find_gear(item_id)
    if owned is None:
        raise PostError("Такой вещи у тебя нет.")
    if owned.is_equipped:
        raise PostError("Сперва сними вещь — надетое не отправляют.")
    if await db.waiting_count(guest.user_id) >= MAX_WAITING:
        raise PostError(
            f"У бойца {guest.nickname} на почте уже {MAX_WAITING} посылок — "
            "пусть сперва разберёт."
        )

    cost = player.price_here(SEND_PRICE, moment, Service.POST)
    if not player.can_afford(cost, moment, Service.POST):
        raise PostError(
            f"Отправка стоит {cost} 💰, а "
            f"{player.purse_note(moment, Service.POST)}."
        )
    parcel = await db.post_gear(
        item_id, player.user_id, guest.user_id, price,
        keep.until(moment), moment,
    )
    if parcel is None:  # pragma: no cover - вещь увели между двумя запросами
        raise PostError("Вещь исчезла из рюкзака. Попробуй ещё раз.")

    player.pay(cost, moment, Service.POST, note="Отправка посылки")
    await db.save_player(player)
    player.drop_gear(owned)
    logger.info(
        "Боец %s отправил %s бойцу %s (платёж %s, срок %s)",
        player.user_id, owned.code, guest.user_id, price, keep.code,
    )
    said = f"«{owned.item.title}» отправлена бойцу {guest.nickname}."
    if price:
        said += f" Наложенный платёж — {price} 💰."
    said += f" Срок хранения: {keep.title.lower()}."
    return said


# ---------- получить ----------


async def collect(
    db: Database, player: Player, parcel_id: int, now: int | None = None
) -> str:
    """Забрать посылку. С наложенным платежом — заплатив за неё."""
    moment = now_ts() if now is None else now
    _at_post(player, moment, "Получить посылку")
    await sweep(db, moment)
    parcel = await _mine(db, player, parcel_id)

    due = parcel.due
    if due:
        price = player.price_here(due, moment, Service.POST)
        if not player.can_afford(price, moment, Service.POST):
            raise PostError(
                f"За посылку просят {price} 💰, а "
                f"{player.purse_note(moment, Service.POST)}."
            )
    code = await db.hand_parcel(parcel.id, player.user_id)
    if not code:  # pragma: no cover - посылку выдали между двумя запросами
        raise PostError("Эту посылку уже выдали.")
    if due:
        # Деньги уходят отправителю наличными, и ждать его в сети незачем
        player.pay(due, moment, Service.POST, note="Посылка наложенным платежом")
        await db.pay_cash(parcel.from_id, due)
        await db.save_player(player)
    player.gear = await db.list_gear(player.user_id)

    item = get_item(code)
    title = item.title if item else code
    logger.info("Боец %s получил посылку %s", player.user_id, parcel.id)
    if parcel.back:
        return f"«{title}» вернулась к тебе: получатель её не забрал."
    said = f"«{title}» получена."
    if due:
        said += f" Отправителю ушло {due} 💰."
    return said


async def refuse(
    db: Database, player: Player, parcel_id: int, now: int | None = None
) -> str:
    """Отказаться от посылки: она поедет обратно отправителю."""
    moment = now_ts() if now is None else now
    _at_post(player, moment, "Отказаться от посылки")
    parcel = await _mine(db, player, parcel_id)
    if parcel.back:
        raise PostError("Это твоя же посылка — её только забрать.")
    await db.return_parcel(parcel.id)
    logger.info("Боец %s отказался от посылки %s", player.user_id, parcel.id)
    return "Посылка поедет обратно отправителю."


# ---------- просрочка ----------


async def sweep(db: Database, now: int | None = None) -> list[int]:
    """Развернуть посылки, чей срок хранения вышел.

    Возвращает номера развёрнутых. Зовётся оттуда, где на посылки
    смотрят, — с почты: часов у клуба нет, а срок считается от минуты
    отправки, и результат один и тот же, заходили на почту или нет.
    """
    moment = now_ts() if now is None else now
    sent_back: list[int] = []
    for parcel in await db.overdue_parcels(moment):
        await db.return_parcel(parcel.id)
        sent_back.append(parcel.id)
        logger.info(
            "Посылка %s вернулась к бойцу %s: срок хранения вышел",
            parcel.id, parcel.from_id,
        )
    return sent_back


__all__ = ["PostError", "collect", "refuse", "send", "sweep"]
