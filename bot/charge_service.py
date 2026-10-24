"""АЗС: три колонки, таймер и машина, которую с них забирают.

Правила заряда лежат в `bot.game.charge`, состояние колонок — в базе,
здесь — порядок действий.

**Колонок три, и они общие на город.** Не у каждого бойца своя: ключ
таблицы — номер колонки, и двух машин в одной не бывает. Очередь к
зарядке — часть игры: на весь город три места, и занял — значит занял.

**Платят вперёд, забирают потом.** Цена и время растут ровно с
процентами, торга нет. Пока идёт зарядка, машина при хозяине только по
бумагам: за руль её не возьмёшь, на парковку не поставишь и на рынок не
выставишь, — ключи у колонки.

**Заряд наливается в момент, когда машину забирают.** Разницы с
«наливается по таймеру» нет: ехать на стоящей в колонке всё равно
нельзя, — зато так в базе на одно правило меньше. Часов у города нет, и
доливать некому: зарядка кончается тем, что за машиной пришли.

**Лишнее пропадает.** Колонка наливает столько процентов, за сколько
заплатили, а выше полного баку деваться некуда. Сколько прольётся мимо,
видно до оплаты — на кнопке.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.cars import get_car, parking_title
from bot.game.charge import (
    CHARGE_SLOTS,
    CHARGE_STEPS,
    after_charge,
    charge_price,
    charge_seconds,
    column_picture,
    percent_left,
    wasted,
)
from bot.game.health import format_term, now_ts
from bot.game.locations import Service
from bot.models import Player

logger = logging.getLogger(__name__)


class ChargeError(Exception):
    """Колонка отказала: занята, не та машина, не хватило денег."""


async def plugged(db: Database, user_id: int) -> dict[str, int]:
    """Машины бойца на колонках: код машины — номер колонки."""
    return {
        str(one["code"]): int(one["slot"])
        for one in await db.charging_rows()
        if int(one["user_id"]) == user_id
    }


async def sweep(db: Database, now: int | None = None) -> int:
    """Освободить колонки, в которых заряжать больше нечего.

    Машина могла кончиться арендой, пока стояла: салон забрал её
    обратно, а колонка так и осталась бы занятой — на весь город их
    три. Часов у города нет, и освобождает колонку всякий, кто на
    станцию смотрит.
    """
    from bot.car_service import sweep as sweep_cars

    moment = now_ts() if now is None else now
    await sweep_cars(db, moment)
    freed = 0
    for row in await db.charging_rows():
        if await db.car_row(int(row["user_id"]), str(row["code"])) is None:
            await db.unplug(int(row["slot"]))
            logger.info(
                "Колонка %s освобождена: машины %s у бойца %s больше нет",
                row["slot"], row["code"], row["user_id"],
            )
            freed += 1
    return freed


async def slots(
    db: Database, player: Player, now: int | None = None
) -> list[dict]:
    """Все три колонки: что в них стоит, чьё и сколько осталось."""
    moment = now_ts() if now is None else now
    await sweep(db, moment)
    busy = {int(one["slot"]): one for one in await db.charging_rows()}
    out = []
    for slot in range(1, CHARGE_SLOTS + 1):
        row = busy.get(slot)
        if row is None:
            out.append({
                "slot": slot, "busy": False, "mine": False, "code": "",
                "title": "", "car_image": "", "owner": "", "percent": 0,
                "left": 0, "left_text": "", "ready": False,
                # Картинка самой колонки: она у слота своя и не зависит
                # от того, стоит в ней машина или нет
                "image": column_picture(slot),
            })
            continue
        car = get_car(str(row["code"]))
        owner = await db.get_player(int(row["user_id"]))
        left = max(0, int(row["ready_at"]) - moment)
        out.append({
            "slot": slot,
            "busy": True,
            "mine": int(row["user_id"]) == player.user_id,
            "code": car.code if car else str(row["code"]),
            "title": car.title if car else "Машина",
            "image": column_picture(slot),
            # Машина рисуется под колонкой, а не вместо неё
            "car_image": car.image if car else "",
            "owner": owner.nickname if owner else "боец",
            "percent": int(row["percent"]),
            "left": left,
            "left_text": format_term(left) if left else "",
            "ready": not left,
        })
    return out


async def plug(
    db: Database,
    player: Player,
    code: str,
    slot: int,
    percent: int,
    now: int | None = None,
) -> str:
    """Поставить машину на колонку и заплатить за проценты.

    Машина должна быть при бойце: не на парковке, не просроченная и не
    на другой колонке. Сидящий за рулём выходит сам — иначе пришлось бы
    сперва идти в карточку и снимать её там.
    """
    from bot.car_service import park

    moment = now_ts() if now is None else now
    car = get_car(code)
    if car is None:
        raise ChargeError("Такой машины в городе нет.")
    if percent not in CHARGE_STEPS:
        raise ChargeError("Колонка наливает по своим меркам, не на глаз.")
    if slot not in range(1, CHARGE_SLOTS + 1):
        raise ChargeError(f"Колонок тут {CHARGE_SLOTS}.")
    # Своим взглядом станция и освобождает брошенные колонки: та, что
    # бойцу нужна, могла стоять занятой кончившейся арендой
    await sweep(db, moment)
    row = await db.car_row(player.user_id, car.code)
    if row is None or (row["until"] and int(row["until"]) <= moment):
        raise ChargeError("Этой машины у тебя нет.")
    if row["parked"]:
        raise ChargeError(
            f"Она стоит на парковке ({parking_title(str(row['parked']))}). "
            "Сперва забери её оттуда."
        )
    standing = await plugged(db, player.user_id)
    if car.code in standing:
        raise ChargeError(
            f"{car.title} уже на колонке — на {standing[car.code]}-й."
        )
    driven = int(row["driven"])
    if percent_left(car, driven) >= 100:
        raise ChargeError(f"{car.title} и так заряжена полностью.")
    price = player.price_here(charge_price(percent), moment, Service.CHARGE)
    if not player.can_afford(price, moment, Service.CHARGE):
        raise ChargeError(
            f"Зарядка стоит {price} 💰, а "
            f"{player.purse_note(moment, Service.CHARGE)}."
        )
    ready_at = moment + charge_seconds(percent)
    if not await db.plug_in(
        slot, player.user_id, car.code, percent, ready_at, moment
    ):
        raise ChargeError("Эту колонку заняли раньше. Возьми свободную.")
    # Платим после того, как колонка занята: иначе боец заплатил бы за
    # место, которое у него из-под рук забрали
    player.pay(price, moment, Service.CHARGE, about=f"Зарядка {car.title}")
    await db.save_player(player)
    # Из машины выходят вместе с постановкой на колонку: стоящую в
    # колонке всё равно не поведёшь
    if player.driving(moment) == car.code:
        await park(db, player, moment)
    logger.info(
        "Боец %s поставил %s на колонку %s на %s%%",
        player.user_id, car.code, slot, percent,
    )
    spill = wasted(car, driven, percent)
    tail = (
        f" Мимо бака уйдёт {spill}% — бак не настолько пуст."
        if spill
        else ""
    )
    return (
        f"{car.title} на {slot}-й колонке: +{percent}% за {price} 💰. "
        f"Готова через {format_term(charge_seconds(percent))}.{tail}"
    )


async def collect(
    db: Database, player: Player, slot: int, now: int | None = None
) -> str:
    """Забрать свою машину с колонки. Раньше срока не отдают."""
    moment = now_ts() if now is None else now
    row = await db.charging_row(slot)
    if row is None:
        raise ChargeError("Эта колонка пуста.")
    if int(row["user_id"]) != player.user_id:
        raise ChargeError("Это чужая машина. Своя — на другой колонке.")
    left = max(0, int(row["ready_at"]) - moment)
    if left:
        raise ChargeError(f"Ещё заряжается: осталось {format_term(left)}.")
    code = str(row["code"])
    percent = int(row["percent"])
    car = get_car(code)
    await db.unplug(slot)
    if car is None:  # pragma: no cover - машину убрали из каталога
        return "Колонка свободна."
    car_row = await db.car_row(player.user_id, code)
    if car_row is None:
        # Срок аренды кончился, пока машина стояла в колонке: машины
        # нет, и заряжать нечего. Денег за это не возвращают — колонка
        # налила, а срок считал салон
        return (
            f"{car.title} сдалась в салон раньше, чем зарядилась: срок "
            f"аренды кончился. Колонка свободна."
        )
    driven = after_charge(car, int(car_row["driven"]), percent)
    await db.set_driven(player.user_id, code, driven)
    logger.info(
        "Боец %s забрал %s с колонки %s", player.user_id, code, slot
    )
    return (
        f"{car.title} заряжена: {percent_left(car, driven)}%. "
        f"Ключи при тебе, за руль — в карточке."
    )


__all__ = ["ChargeError", "collect", "plug", "plugged", "slots", "sweep"]
