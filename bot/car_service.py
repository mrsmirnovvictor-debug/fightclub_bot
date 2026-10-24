"""Машины: купить, снять, взять в гараже и сесть за руль.

Правил тут три, и они вместе отвечают на вопрос «чем машина отличается
от вещи».

**Машину держит не рюкзак, а строка со сроком.** Купленная — своя
насовсем, снятая в салоне — до часа, служебная — до конца смены.
Кончился срок — машины нет, и сметать для этого ничего не надо: час
написан в самой строке, и всякий, кто на неё смотрит, видит, жива она
или нет.

**За рулём одна машина.** «Надеть» вторую — значит выйти из первой;
она от этого не пропадает, а возвращается в гараж бойца.

**Служебную выдаёт должность, а не кошелёк.** Патрульную — любому в
участке, машину начальника — начальнику, такси — водителю на смене.
Снял форму — машина вернулась в гараж, и ждать этого никто не обязан.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.cars import (
    GARAGES,
    PARKINGS,
    PRO_MAX_CARS,
    SPANS,
    TAXI,
    TAXI_SHIFT_PRICE,
    Car,
    garage_of,
    get_car,
    cars_allowed,
    may_take,
    parking_slots,
    parking_title,
    rent_price,
    rent_seconds,
    span_title,
)
from bot.game.charge import after_drive, empty, percent_left
from bot.game.health import format_term, now_ts
from bot.game.locations import Service
from bot.models import Player

logger = logging.getLogger(__name__)


class CarError(Exception):
    """За руль нельзя — и человеку видно, почему."""


# ---------- что у бойца ----------


async def sweep(db: Database, now: int | None = None) -> int:
    """Убрать машины с кончившимся сроком. Зовётся отовсюду, где смотрят.

    Часов у города нет, и возвращать машину в гараж некому: это делает
    всякий взгляд на салон, гараж или свои ключи.
    """
    return await db.sweep_cars(now_ts() if now is None else now)


async def mine(
    db: Database, player: Player, now: int | None = None
) -> list[dict]:
    """Ключи бойца: что у него есть и до какого часа."""
    from bot.game.charge import km_left, range_km

    moment = now_ts() if now is None else now
    await sweep(db, moment)
    # Колонки спрашиваем один раз: их на город три, а машин у бойца
    # может быть до трёх, и три запроса за тем же ответом ни к чему
    at_station = {
        str(one["code"]): one
        for one in await db.charging_rows()
        if int(one["user_id"]) == player.user_id
    }
    out = []
    for row in await db.cars_of(player.user_id):
        car = get_car(str(row["code"]))
        if car is None:  # pragma: no cover - машину убрали из каталога
            continue
        left = max(0, int(row["until"]) - moment) if row["until"] else 0
        parked = str(row["parked"] or "")
        driven = int(row["driven"])
        plug = at_station.get(car.code)
        out.append({
            "code": car.code,
            "title": car.title,
            "image": car.image,
            "times": car.times,
            # Та же строка, что на витрине и в гараже: карточка машины
            # одна на все экраны, и подпись «дорога вдвое короче» она
            # берёт отсюда. Без неё в ключах висело «дорога undefined»
            "times_text": car.times_text,
            "note": car.note,
            "service": car.service,
            "own": not row["until"],
            "until": int(row["until"]),
            "left": left,
            "left_text": format_term(left) if left else "",
            "driving": player.driving(moment) == car.code,
            # Где машина стоит: пусто — при хозяине. За руль из-под
            # себя не садятся, и страница должна знать об этом сама
            "parked": parked,
            "parked_text": parking_title(parked),
            # Заряд: процент для глаза, километры для расчёта. Пустая
            # машина своя и при бойце, но за руль не пустит
            "charge": percent_left(car, driven),
            "km_left": km_left(car, driven),
            "range": range_km(car),
            "empty": empty(car, driven),
            # На колонке: машина при бойце, но до конца зарядки её не
            # забрать — ни за руль, ни на парковку, ни на продажу
            "charging": bool(plug),
            "charging_slot": int(plug["slot"]) if plug else 0,
            "charging_left": (
                max(0, int(plug["ready_at"]) - moment) if plug else 0
            ),
        })
    return out


async def at_hand(
    db: Database, player: Player, now: int | None = None
) -> list[dict]:
    """Машины при бойце: без тех, что оставлены на парковке.

    Это и есть его ключи: поставленную машину из карточки не возьмёшь —
    за ней идут в гараж, где она стоит.
    """
    moment = now_ts() if now is None else now
    return [one for one in await mine(db, player, moment) if not one["parked"]]


async def kept_count(db: Database, player: Player, now: int) -> int:
    """Сколько машин у бойца на руках — столько и считает потолок.

    Не в счёт две вещи. Служебная — она не его: её выдал гараж на
    смену. И та, что оставлена на парковке: в гараже она стоит, а не в
    руках, и место под машину освобождает — в этом и смысл гаража.
    """
    rows = await db.cars_of(player.user_id)
    return sum(
        1
        for row in rows
        if (car := get_car(str(row["code"]))) is not None
        and not car.service
        and not row["parked"]
        and (not row["until"] or int(row["until"]) > now)
    )


async def room_for_another(
    db: Database, player: Player, now: int | None = None
) -> bool:
    """Влезет ли бойцу ещё одна машина: одна на руках, с подпиской три."""
    moment = now_ts() if now is None else now
    return await kept_count(db, player, moment) < cars_allowed(
        player.is_pro(moment)
    )


async def _check_room(db: Database, player: Player, now: int) -> None:
    """Не пустить за второй машиной. Подписка раздвигает счёт до трёх.

    Выход из отказа есть, и он тут же назван: поставить свою на
    парковку. Стоящая машина места на руках не занимает.
    """
    if await room_for_another(db, player, now):
        return
    limit = cars_allowed(player.is_pro(now))
    if player.is_pro(now):
        raise CarError(
            f"Машин на руках {limit} — больше не держат даже с подпиской. "
            "Поставь одну на парковку: в особняке или у своей квартиры."
        )
    raise CarError(
        f"Машина у бойца одна, и она у тебя на руках. Поставь её на "
        f"парковку — в особняке или у своей квартиры, — и бери вторую. "
        f"С подпиской держат {PRO_MAX_CARS} разом."
    )


async def _check_free(db: Database, player: Player, code: str) -> None:
    """Не дать распорядиться машиной, которая стоит на колонке.

    Пока идёт зарядка, машина при бойце только по бумагам: ключи от
    неё у колонки. Её не берут за руль, не ставят на парковку, не
    сдают в салон и не выставляют на рынок — сперва забирают с АЗС.
    """
    for one in await db.charging_rows():
        if int(one["user_id"]) == player.user_id and str(one["code"]) == code:
            raise CarError(
                "Эта машина стоит на колонке АЗС. Сперва забери её "
                "оттуда — зарядка идёт и без тебя."
            )


async def has_car(db: Database, player: Player, code: str, now: int) -> bool:
    """Есть ли эта машина у бойца прямо сейчас."""
    row = await db.car_row(player.user_id, code)
    if row is None:
        return False
    return not row["until"] or int(row["until"]) > now


# ---------- салон ----------


async def buy(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Купить машину в салоне. Своя — насовсем, срока у неё нет."""
    moment = now_ts() if now is None else now
    car = _salon_car(code)
    if await has_car(db, player, car.code, moment):
        row = await db.car_row(player.user_id, car.code)
        if row is not None and not row["until"]:
            raise CarError("Такая у тебя уже есть.")
    else:
        # Снятую машину можно выкупить: это та же машина, а не вторая
        await _check_room(db, player, moment)
    await _pay(db, player, car.price, moment, f"Покупка {car.title}")
    await db.put_car(player.user_id, car.code, 0, moment)
    logger.info("Боец %s купил машину %s", player.user_id, car.code)
    return f"{car.title} теперь твоя. Ключи в гараже, за руль — в карточке."


async def rent(
    db: Database, player: Player, code: str, span: str,
    now: int | None = None,
) -> str:
    """Снять машину на сутки или на месяц. Срок продлевается с конца."""
    moment = now_ts() if now is None else now
    car = _salon_car(code)
    if span not in SPANS:
        raise CarError("Снять можно на сутки или на месяц.")
    row = await db.car_row(player.user_id, car.code)
    if row is not None and not row["until"]:
        raise CarError("Эта машина и так твоя — зачем её снимать.")
    if row is None:
        # Продление — не вторая машина: счёт растёт только от новой
        await _check_room(db, player, moment)
    await _pay(
        db, player, rent_price(car, span), moment,
        f"Аренда {car.title} на {span_title(span)}",
    )
    until = await db.put_car(
        player.user_id, car.code, rent_seconds(span), moment
    )
    logger.info(
        "Боец %s снял машину %s на %s", player.user_id, car.code, span
    )
    left = format_term(max(0, until - moment))
    return f"{car.title} снята на {span_title(span)}. Осталось {left}."


async def hand_over(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Отдать машину: сдать служебную или бросить аренду раньше срока.

    Денег за недоезженную аренду не возвращают: салон сдал сутки, а не
    часы. Купленную машину не отдают вовсе — её некому отдавать.
    """
    moment = now_ts() if now is None else now
    car = get_car(code)
    if car is None:
        raise CarError("Такой машины в городе нет.")
    row = await db.car_row(player.user_id, car.code)
    if row is None:
        raise CarError("Этой машины у тебя нет.")
    if not row["until"] and not car.service:
        raise CarError("Своя машина остаётся своей: её не сдают.")
    await _check_free(db, player, car.code)
    if player.driving(moment) == car.code:
        await park(db, player, moment)
    await db.drop_car(player.user_id, car.code)
    logger.info("Боец %s отдал машину %s", player.user_id, car.code)
    if car.service:
        return f"{car.title} вернулась в гараж."
    return f"{car.title} сдана в салон. Денег за остаток срока не вернут."


# ---------- гаражи службы ----------


async def take(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Взять машину из гаража службы. Бесплатно — кроме таксопарка."""
    moment = now_ts() if now is None else now
    car = get_car(code)
    place = player.where(moment)
    if car is None or car not in garage_of(place):
        raise CarError("Этой машины в здешнем гараже нет.")
    if not may_take(car, player.job_code):
        raise CarError(_denied(car))
    if await has_car(db, player, car.code, moment):
        raise CarError("Эта машина у тебя уже на руках.")
    await sweep(db, moment)
    taken = await db.car_holders(car.code, moment)
    if len(taken) >= car.seats:
        raise CarError(
            f"Все {car.seats} в разъезде. Дождись, пока вернут."
        )
    seconds = 0
    if car.kind == TAXI:
        # Такси берут на смену: кончилась смена — машина в парке, и
        # возвращать её вручную не надо
        if not player.on_shift(moment):
            raise CarError("Машину дают на смену. Встань на смену в парке.")
        seconds = player.shift_left(moment)
        await _pay(
            db, player, TAXI_SHIFT_PRICE, moment,
            f"{car.title} на смену",
        )
    until = await db.put_car(player.user_id, car.code, seconds, moment)
    logger.info("Боец %s взял служебную %s", player.user_id, car.code)
    if until:
        return (
            f"{car.title} твоя до конца смены — "
            f"{format_term(max(0, until - moment))}."
        )
    return f"{car.title} выдана. Сдать — в этом же гараже."


async def drop_service_cars(db: Database, player: Player) -> int:
    """Отобрать служебные машины у того, кто уходит со службы.

    Зовётся при увольнении — своём и городском: ключи от патрульной
    выдавала служба, и уходит боец без них. Гараж то же делает и сам,
    когда на него смотрят, но ждать чужого взгляда незачем.
    """
    freed = 0
    for row in await db.cars_of(player.user_id):
        car = get_car(str(row["code"]))
        if car is None or not car.service:
            continue
        await db.drop_car(player.user_id, car.code)
        if player.car == car.code:
            player.car = ""
            player.car_until = 0
        freed += 1
        logger.info(
            "Служебная %s ушла вместе с должностью бойца %s",
            car.code, player.user_id,
        )
    return freed


async def release_strangers(
    db: Database, place: str, now: int | None = None
) -> int:
    """Отобрать служебные машины у тех, кто больше не на службе.

    Ушёл из участка или из парка — машина возвращается в гараж. Часов
    для этого нет, и делает это всякий взгляд на гараж: машин в нём
    три, и перебрать их держателей дешевле, чем стеречь увольнения.
    """
    moment = now_ts() if now is None else now
    await sweep(db, moment)
    freed = 0
    for car in garage_of(place):
        for row in await db.car_holders(car.code, moment):
            holder = await db.get_player(int(row["user_id"]))
            if holder is not None and may_take(car, holder.job_code):
                continue
            await db.drop_car(int(row["user_id"]), car.code)
            if holder is not None and holder.car == car.code:
                holder.car = ""
                holder.car_until = 0
                await db.save_player(holder)
            freed += 1
            logger.info(
                "Служебная %s отобрана у бойца %s: он больше не на службе",
                car.code, row["user_id"],
            )
    return freed


# ---------- парковки: особняк и своя квартира ----------


async def leave(
    db: Database,
    player: Player,
    code: str,
    parked: str,
    spot: int,
    now: int | None = None,
) -> str:
    """Оставить машину на парковке. Хозяин у неё тот же — меняется место.

    Служебную не ставят: она принадлежит гаражу службы, и место ей там.
    За руль поставленной машины не садятся, пока её не заберут, — зато
    и срок аренды на парковке идёт как обычно: место машину не хранит,
    оно её держит.
    """
    moment = now_ts() if now is None else now
    car = get_car(code)
    if car is None or parked not in PARKINGS:
        raise CarError("Такой машины в городе нет.")
    if car.service:
        raise CarError("Служебная машина ночует в гараже службы, а не тут.")
    await sweep(db, moment)
    row = await db.car_row(player.user_id, car.code)
    if row is None or (row["until"] and int(row["until"]) <= moment):
        raise CarError("Этой машины у тебя нет.")
    if row["parked"]:
        raise CarError(f"Она и так стоит: {parking_title(str(row['parked']))}.")
    await _check_free(db, player, car.code)
    if player.driving(moment) == car.code:
        # Ставят машину вместе с выходом из неё: иначе пришлось бы
        # сперва идти в карточку и снимать её там
        await park(db, player, moment)
    if not await db.park_car(
        player.user_id, car.code, parked, spot, parking_slots(parked)
    ):
        raise CarError(
            f"Мест нет: на этой парковке их {parking_slots(parked)}."
        )
    logger.info(
        "Боец %s оставил %s на парковке %s:%s",
        player.user_id, car.code, parked, spot,
    )
    return f"{car.title} стоит на парковке. Ключи при тебе."


async def pick_up(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Забрать свою машину с парковки. Чужую не отдают никому."""
    moment = now_ts() if now is None else now
    car = get_car(code)
    if car is None:
        raise CarError("Такой машины в городе нет.")
    await sweep(db, moment)
    row = await db.car_row(player.user_id, car.code)
    if row is None or not row["parked"]:
        raise CarError("Этой машины на парковке нет.")
    await db.unpark_car(player.user_id, car.code)
    logger.info("Боец %s забрал %s с парковки", player.user_id, car.code)
    return f"{car.title} снова при тебе. За руль — в карточке."


async def take_away(
    db: Database,
    player: Player,
    car_id: int,
    parked: str,
    parked_id: int,
    now: int | None = None,
) -> str:
    """Забрать машину с общей парковки: ключи меняют хозяина.

    На парковке особняка машина общая: кто взял, того она и есть. Это
    не кража — это и есть смысл общего гаража: поставил свою, уехал на
    любой. Квартира устроена иначе, и забирать оттуда нечего, кроме
    своего, — там и парковка своя.
    """
    moment = now_ts() if now is None else now
    await sweep(db, moment)
    # Чужая машина с общей парковки — это новая машина на руках, и счёт
    # у неё общий: своя одна, с подпиской три. Своя же возвращается
    # всегда: она и так числится за хозяином
    standing = await db.parked_car(car_id, parked, parked_id)
    if standing is not None and int(standing["user_id"]) != player.user_id:
        await _check_room(db, player, moment)
    answer = await db.take_parked_car(car_id, parked, parked_id, player.user_id)
    if answer is None:
        raise CarError("Этой машины на парковке уже нет.")
    car = get_car(str(answer["code"]))
    title = car.title if car else "Машина"
    if not answer["taken"]:
        raise CarError(f"Такая у тебя уже есть: {title}.")
    logger.info(
        "Боец %s забрал %s с парковки %s:%s (была у %s)",
        player.user_id, answer["code"], parked, parked_id, answer["owner"],
    )
    if int(answer["owner"]) != player.user_id:
        return f"{title} теперь твоя. Ключи при тебе, за руль — в карточке."
    return f"{title} снова при тебе. За руль — в карточке."


async def parked_at(
    db: Database, parked: str, spot: int, viewer: Player,
    now: int | None = None,
) -> list[dict]:
    """Что стоит на этой парковке: чьё, какое и до какого часа.

    Видят парковку все, кому она открыта: семья свою, жилец свою. Чья
    машина — написано у каждой, но в особняке это только подпись:
    забрать её может любой свой.
    """
    from bot.game.charge import km_left

    moment = now_ts() if now is None else now
    await sweep(db, moment)
    out = []
    for row in await db.parked_cars(parked, spot, moment):
        car = get_car(str(row["code"]))
        if car is None:  # pragma: no cover - машину убрали из каталога
            continue
        owner = await db.get_player(int(row["user_id"]))
        left = max(0, int(row["until"]) - moment) if row["until"] else 0
        out.append({
            # Номер строки, а не код: двух одинаковых хэтчбеков разных
            # хозяев кодом не различить
            "id": int(row["id"]),
            "code": car.code,
            "title": car.title,
            "image": car.image,
            "times": car.times,
            "times_text": car.times_text,
            "note": car.note,
            "user_id": int(row["user_id"]),
            "owner": owner.nickname if owner else "боец",
            "mine": int(row["user_id"]) == viewer.user_id,
            "own": not row["until"],
            "left": left,
            "left_text": format_term(left) if left else "",
            # Заряд виден до того, как за машиной пошли: на общей
            # парковке особняка берут чужую, и пустая там такая же
            # машина, как полная, — отличить их можно только числом
            "charge": percent_left(car, int(row["driven"])),
            "km_left": km_left(car, int(row["driven"])),
        })
    return out


# ---------- за руль ----------


async def wheel(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Сесть за руль. Права обязательны, машина — своя или взятая."""
    moment = now_ts() if now is None else now
    car = get_car(code)
    if car is None:
        raise CarError("Такой машины в городе нет.")
    if not player.school.has_licence:
        raise CarError(
            "Без водительского удостоверения за руль не сядешь. "
            "Права — в автошколе и в участке."
        )
    await sweep(db, moment)
    row = await db.car_row(player.user_id, car.code)
    if row is None:
        raise CarError("Этой машины у тебя нет.")
    if row["until"] and int(row["until"]) <= moment:
        raise CarError("Срок этой машины вышел — она уже не твоя.")
    if row["parked"]:
        raise CarError(
            f"Эта машина стоит на парковке ({parking_title(str(row['parked']))}). "
            "Сперва забери её оттуда."
        )
    await _check_free(db, player, car.code)
    if empty(car, int(row["driven"])):
        raise CarError(
            f"{car.title} без заряда — она не поедет. Заряжают на АЗС, "
            f"у авторынка."
        )
    player.car = car.code
    player.car_until = int(row["until"])
    await db.save_player(player)
    logger.info("Боец %s сел за руль %s", player.user_id, car.code)
    return f"{car.title} под тобой. Дорога короче в {car.times} раз."


async def park(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Снять машину: дальше пешком."""
    moment = now_ts() if now is None else now
    code = player.driving(moment)
    player.car = ""
    player.car_until = 0
    await db.save_player(player)
    car = get_car(code)
    if car is None:
        return "Ты и так пешком."
    logger.info("Боец %s вышел из %s", player.user_id, car.code)
    return f"{car.title} осталась в гараже. Дальше пешком."


async def spend_charge(
    db: Database, player: Player, km: int, now: int | None = None
) -> str:
    """Списать заряд за проеханное. Пусто — ничего не случилось.

    Зовётся, когда боец отправляется в путь за рулём: заряд тратит
    расстояние, а не время, и считается оно в километрах пешего шага.
    Кончился — боец выходит прямо на дороге и доезжает... то есть
    доходит сам. Машина при этом никуда не девается: она его, просто
    пустая, и до колонки её ведут на буксире — в игре это значит, что
    сесть за руль снова можно только заряженной.
    """
    moment = now_ts() if now is None else now
    code = player.driving(moment)
    car = get_car(code)
    if car is None or km <= 0:
        return ""
    row = await db.car_row(player.user_id, code)
    if row is None:  # pragma: no cover - служебная не записана на бойца
        return ""
    driven = after_drive(car, int(row["driven"]), km)
    await db.set_driven(player.user_id, code, driven)
    if not empty(car, driven):
        return ""
    # Бак пуст: дальше пешком. Выходим сразу, не дожидаясь следующей
    # двери, — иначе боец «ехал бы» на пустой машине до самого города
    player.car = ""
    player.car_until = 0
    await db.save_player(player)
    logger.info("У бойца %s кончился заряд %s", player.user_id, code)
    return (
        f"{car.title} встала: заряд кончился. Дальше пешком, "
        f"а машину — на зарядку."
    )


def charge_of(car, row) -> int:
    """Заряд машины в процентах по её строке из базы."""
    return percent_left(car, int(row["driven"]) if row else 0)


# ---------- внутреннее ----------


def _salon_car(code: str) -> Car:
    """Машина с витрины салона. Служебные здесь не торгуются."""
    car = get_car(code)
    if car is None or car.service:
        raise CarError("Такой машины в салоне нет.")
    return car


def _denied(car: Car) -> str:
    """Почему гараж не даёт эту машину."""
    if car.kind == TAXI:
        return "Машины парка выдают водителям таксопарка."
    if len(car.jobs) == 1:
        return "Эта машина — начальника полиции, и ключи от неё одни."
    return "Служебные машины выдают сотрудникам участка."


async def _pay(
    db: Database, player: Player, price: int, now: int, about: str
) -> None:
    """Заплатить за машину тем же кошельком, которым платят всюду."""
    if price <= 0:
        return
    here = player.price_here(price, now, Service.CARS)
    if not player.can_afford(here, now, Service.CARS):
        raise CarError(
            f"Стоит {here} 💰, а {player.purse_note(now, Service.CARS)}."
        )
    player.pay(here, now, Service.CARS, about=about)
    await db.save_player(player)


__all__ = [
    "CarError",
    "GARAGES",
    "buy",
    "charge_of",
    "drop_service_cars",
    "hand_over",
    "at_hand",
    "has_car",
    "kept_count",
    "leave",
    "mine",
    "park",
    "parked_at",
    "pick_up",
    "take_away",
    "release_strangers",
    "rent",
    "room_for_another",
    "spend_charge",
    "sweep",
    "take",
    "wheel",
]
