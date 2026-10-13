"""Аренда квартиры: снять, продлить, выселиться, сейф и кладовая.

Правила держит `bot.game.estate`, хранение — `bot.database`, а здесь то,
что меняет состояние: списать месяц, записать жильца, вернуть имущество.

**Место проверяется здесь, а не на странице.** Квартиру снимают в том
доме, где она стоит, — дошёл ногами, тогда и снимай. Сейф и кладовая
тоже в доме: до своих денег и своих вещей надо дойти, и это главное, чем
квартира отличается от рюкзака.

**Просрочка сводится на месте, без часов.** Будильников, которые ходили
бы по базе и выселяли жильцов, в клубе нет и не надо: квартиру смотрят
ровно в четырёх домах, и в каждую из этих минут сюда заходят через
`sweep`. Неделя просрочки прошла — квартира освобождается, а имущество
уезжает к хозяину: вещи в рюкзак, кредиты в наличные.
"""

from __future__ import annotations

import logging
import sqlite3

from bot.database import Database
from bot.game.equipment import get_item
from bot.game.estate import (
    GRACE_DAYS,
    GRACE_SECONDS,
    RENT_DAYS,
    Lease,
    extended,
    flat_at,
    is_house,
)
from bot.game.health import format_term, now_ts
from bot.game.locations import Service, get_location
from bot.models import Player

logger = logging.getLogger(__name__)


class EstateError(Exception):
    """Отказ по жилью, который показывают игроку как есть."""


def _house(player: Player, moment: int) -> str:
    """В каком жилом доме боец стоит. Иначе — отказ."""
    here = player.where(moment)
    if not is_house(here):
        raise EstateError("Это делают в жилом доме, а не отсюда.")
    return here


def _lease(player: Player) -> Lease:
    if player.flat is None:
        raise EstateError("Ты не снимаешь жилья.")
    return player.flat


def _at_home(player: Player, moment: int) -> Lease:
    """Боец у своей двери: сейф и кладовая открываются только там."""
    lease = _lease(player)
    if player.where(moment) != lease.house:
        place = get_location(lease.house)
        where = place.title if place else "своём доме"
        raise EstateError(f"Твоя квартира в «{where}» — туда и надо дойти.")
    if lease.locked(moment):
        left = lease.grace_left(moment)
        raise EstateError(
            "Квартира заперта: аренда кончилась. Продли её — тогда и откроют."
            + (f" Освободят через {format_term(left)}." if left else "")
        )
    return lease


async def _refresh(db: Database, player: Player) -> Lease | None:
    """Перечитать квартиру бойца: сейф и срок могли измениться."""
    player.flat = await db.lease_of(player.user_id)
    return player.flat


# ---------- снять и продлить ----------


async def rent(
    db: Database, player: Player, number: int, now: int | None = None
) -> str:
    """Снять квартиру в этом доме: месяц вперёд по цене комнатности."""
    moment = now_ts() if now is None else now
    house = _house(player, moment)
    await sweep(db, player, moment)
    if player.flat is not None:
        lease = player.flat
        place = get_location(lease.house)
        where = place.title if place else lease.house
        raise EstateError(
            f"Ты уже снимаешь {lease.title} в «{where}». "
            "Квартира на бойца одна: сперва выселись."
        )
    flat = flat_at(house, number)
    if flat is None:
        raise EstateError("Такой квартиры в этом доме нет.")
    if await db.lease_at(house, number) is not None:
        raise EstateError(f"{flat.title} уже занята.")

    price = player.price_here(flat.price, moment, Service.ESTATE)
    if not player.can_afford(price, moment, Service.ESTATE):
        raise EstateError(
            f"Месяц стоит {price} 💰, а "
            f"{player.purse_note(moment, Service.ESTATE)}."
        )
    try:
        lease = await db.rent_flat(
            player.user_id, house, number, flat.rooms,
            extended(0, moment), moment,
        )
    except sqlite3.IntegrityError as error:
        # Квартиру сняли между проверкой и записью: и «занято», и «одна
        # на бойца» держит сама база, и ловить это питоном значило бы
        # ловить с опозданием
        raise EstateError("Квартиру только что сняли. Выбери другую.") from error

    player.pay(price, moment, Service.ESTATE, note="Аренда квартиры")
    await db.save_player(player)
    player.flat = lease
    logger.info("Боец %s снял %s кв. %s за %s", player.user_id, house, number, price)
    return (
        f"{flat.rooms_title.capitalize()} {flat.title} снята на {RENT_DAYS} дней. "
        f"Сейф и кладовая на {flat.slots} вещей — твои."
    )


async def extend(db: Database, player: Player, now: int | None = None) -> str:
    """Продлить аренду ещё на месяц. Платят в своём доме."""
    moment = now_ts() if now is None else now
    _house(player, moment)
    await sweep(db, player, moment)
    lease = _lease(player)
    if player.where(moment) != lease.house:
        place = get_location(lease.house)
        where = place.title if place else lease.house
        raise EstateError(f"Платят за квартиру в своём доме — в «{where}».")

    price = player.price_here(lease.price, moment, Service.ESTATE)
    if not player.can_afford(price, moment, Service.ESTATE):
        raise EstateError(
            f"Месяц стоит {price} 💰, а "
            f"{player.purse_note(moment, Service.ESTATE)}."
        )
    paid_until = extended(lease.paid_until, moment)
    await db.extend_lease(lease.id, paid_until)
    player.pay(price, moment, Service.ESTATE, note="Продление аренды")
    await db.save_player(player)
    await _refresh(db, player)
    logger.info("Боец %s продлил квартиру до %s", player.user_id, paid_until)
    return f"Аренда продлена на {RENT_DAYS} дней. Списано {price} 💰."


async def move_out(db: Database, player: Player) -> str:
    """Выселиться. Вещи возвращаются в рюкзак, кредиты из сейфа — на руки.

    Самому отказу место не нужно — это отказ от жилья, а не поход в
    него, — но просят о нём через дверь жилого дома, и дальше неё никто
    не ходит. Имущество клуб возвращает сам и разом: забирать его по
    частям пришлось бы в своём доме, а выселяются обычно не из него.
    """
    lease = _lease(player)
    _, money, things = await db.release_flat(lease.id)
    fresh = await db.get_player(player.user_id)
    if fresh is not None:
        player.credits = fresh.credits
        player.gear = fresh.gear
    player.flat = None
    logger.info("Боец %s выселился из кв. %s", player.user_id, lease.number)
    said = f"Ты выселился из {lease.title}."
    if things:
        said += f" Вещей вернулось: {things}."
    if money:
        said += f" Из сейфа: {money} 💰 наличными."
    return said


# ---------- сейф ----------


async def deposit(
    db: Database, player: Player, amount: int, now: int | None = None
) -> str:
    """Положить кредиты в сейф квартиры."""
    moment = now_ts() if now is None else now
    lease = _at_home(player, moment)
    if amount <= 0:
        raise EstateError("Сколько положить-то? Напиши число.")
    if not player.can_afford(amount, moment, Service.ESTATE):
        raise EstateError(
            f"В сейф нужно {amount} 💰, а "
            f"{player.purse_note(moment, Service.ESTATE)}."
        )
    # Сперва сейф, потом карман: если квартиры уже нет, с бойца и не снято
    if not await db.move_flat_vault(lease.id, amount):  # pragma: no cover
        raise EstateError("Квартира больше не твоя.")
    player.pay(amount, moment, Service.ESTATE, note="Взнос в сейф квартиры")
    await db.save_player(player)
    fresh = await _refresh(db, player)
    return f"В сейфе квартиры теперь {fresh.vault if fresh else amount} 💰."


async def withdraw(
    db: Database, player: Player, amount: int, now: int | None = None
) -> str:
    """Взять кредиты из сейфа квартиры — наличными."""
    moment = now_ts() if now is None else now
    lease = _at_home(player, moment)
    if amount <= 0:
        raise EstateError("Сколько взять-то? Напиши число.")
    if not await db.move_flat_vault(lease.id, -amount):
        raise EstateError(f"В сейфе {lease.vault} 💰 — столько не взять.")
    player.credits += amount
    await db.save_player(player)
    await _refresh(db, player)
    return f"Из сейфа взято {amount} 💰 наличными."


# ---------- кладовая ----------


async def stash(
    db: Database, player: Player, item_id: int, now: int | None = None
) -> str:
    """Положить вещь в кладовую квартиры."""
    moment = now_ts() if now is None else now
    lease = _at_home(player, moment)
    owned = player.find_gear(item_id)
    if owned is None:
        raise EstateError("Такой вещи у тебя нет.")
    if owned.is_equipped:
        raise EstateError("Сперва сними вещь — надетое в кладовую не кладут.")
    if not await db.stash_in_flat(
        item_id, player.user_id, lease.id, lease.slots, moment
    ):
        raise EstateError(
            f"В кладовой {lease.slots} мест, и все заняты."
        )
    player.drop_gear(owned)
    return f"«{owned.item.title}» лежит в кладовой."


async def take(
    db: Database, player: Player, kept_id: int, now: int | None = None
) -> str:
    """Забрать вещь из кладовой в рюкзак."""
    moment = now_ts() if now is None else now
    lease = _at_home(player, moment)
    code = await db.take_flat_item(kept_id, lease.id, player.user_id)
    if not code:
        raise EstateError("Этой вещи в кладовой уже нет.")
    player.gear = await db.list_gear(player.user_id)
    item = get_item(code)
    return f"«{item.title if item else code}» теперь у тебя в рюкзаке."


# ---------- просрочка ----------


async def sweep(
    db: Database, player: Player | None = None, now: int | None = None
) -> list[int]:
    """Освободить квартиры, которые ждали хозяина дольше недели.

    Возвращает номера освобождённых съёмов. Зовётся оттуда, где на
    квартиры смотрят: в жилом доме. Своего жильца, если он тут и есть,
    перечитываем — ему вернулись вещи и кредиты, и карточка должна
    показывать их, а не прежние числа.
    """
    moment = now_ts() if now is None else now
    gone: list[int] = []
    for lease in await db.overdue_leases(moment - GRACE_SECONDS):
        await db.release_flat(lease.id)
        gone.append(lease.id)
        logger.info(
            "Квартира %s кв. %s освобождена за %s дней просрочки",
            lease.house, lease.number, GRACE_DAYS,
        )
        if player is not None and lease.user_id == player.user_id:
            fresh = await db.get_player(player.user_id)
            if fresh is not None:
                player.credits = fresh.credits
                player.gear = fresh.gear
            player.flat = None
    return gone


__all__ = [
    "EstateError",
    "deposit",
    "extend",
    "move_out",
    "rent",
    "stash",
    "sweep",
    "take",
    "withdraw",
]
