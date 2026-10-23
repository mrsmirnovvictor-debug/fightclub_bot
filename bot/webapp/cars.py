"""Машины глазами мини-аппа: витрина салона, гараж службы и свои ключи.

Правил тут нет — их держат `bot.game.cars` и `bot.car_service`. Здесь
перевод в json.

**Экран один, а домов три.** Салон торгует, участок и парк выдают:
разница в том, что лежит в `shelf`, а не в том, какой это экран.
Заводить три похожих экрана значило бы трижды поправлять одну кнопку.

**Кнопку считает сервер.** Права, должность, свободные места в гараже,
деньги в кошельке — всё это решает служба, а страница рисует ответ и
называет причину отказа. Спрятанная кнопка обходится запросом мимо
интерфейса.
"""

from __future__ import annotations

from typing import Any

from bot.game.cars import (
    BUY_PRICE_DAYS,
    MONTH_PRICE_DAYS,
    PRO_MAX_CARS,
    SHOWCASE,
    TAXI,
    TAXI_SHIFT_PRICE,
    Car,
    garage_of,
    get_car,
    may_take,
)
from bot.game.health import format_term, now_ts
from bot.game.locations import Service, get_location
from bot.models import Player


def car_row(car: Car) -> dict[str, Any]:
    """Сама машина: картинка, скорость и подпись. Без цен и прав."""
    return {
        "code": car.code,
        "title": car.title,
        "image": car.image,
        "times": car.times,
        "times_text": car.times_text,
        "note": car.note,
        "service": car.service,
    }


def showcase_row(
    car: Car, player: Player, moment: int, own: dict[str, dict],
    room: bool = True,
) -> dict[str, Any]:
    """Строка витрины: цены и то, что у бойца уже есть.

    `room` — влезет ли бойцу ещё одна машина. Свою он выкупает и
    продлевает и без места: это та же машина, а не вторая.
    """
    mine = own.get(car.code)
    bought = bool(mine and not mine["until"])
    # Место нужно только под новую машину: продлить снятую и выкупить
    # её можно, сколько бы их ни было на руках
    fits = bool(room or mine)
    return dict(
        car_row(car),
        price=car.price,
        day=car.day,
        month=car.month,
        # Сколько суток в месяце и в покупке: цены считаются от суток, и
        # выгода видна только числом, а не словом «выгодно»
        month_days=MONTH_PRICE_DAYS,
        buy_days=BUY_PRICE_DAYS,
        own=bought,
        rented=bool(mine and mine["until"]),
        left=int(mine["left"]) if mine else 0,
        left_text=mine["left_text"] if mine else "",
        # Своя машина может стоять в гараже: на витрине это надо
        # сказать, иначе «Твоя насовсем» без неё в ключах — загадка
        parked=mine["parked"] if mine else "",
        parked_text=mine["parked_text"] if mine else "",
        fits=fits,
        can_buy=bool(
            not bought and fits
            and player.can_afford(car.price, moment, Service.CARS)
        ),
        can_rent_day=bool(
            not bought and fits
            and player.can_afford(car.day, moment, Service.CARS)
        ),
        can_rent_month=bool(
            not bought and fits
            and player.can_afford(car.month, moment, Service.CARS)
        ),
    )


async def garage_row(
    db, car: Car, player: Player, moment: int, own: dict[str, dict]
) -> dict[str, Any]:
    """Строка гаража: сколько мест, кто в разъезде и дадут ли тебе."""
    holders = await db.car_holders(car.code, moment)
    names = []
    for one in holders:
        who = await db.get_player(int(one["user_id"]))
        names.append({
            "user_id": int(one["user_id"]),
            "name": who.nickname if who else "сотрудник",
            "you": int(one["user_id"]) == player.user_id,
        })
    mine = own.get(car.code)
    allowed = may_take(car, player.job_code)
    free = max(0, car.seats - len(holders))
    # Такси берут на смену и за деньги, служебную в участке — просто так
    price = TAXI_SHIFT_PRICE if car.kind == TAXI else 0
    on_shift = player.on_shift(moment)
    return dict(
        car_row(car),
        seats=car.seats,
        taken=len(holders),
        free=free,
        holders=names,
        price=price,
        mine=bool(mine),
        left=int(mine["left"]) if mine else 0,
        left_text=mine["left_text"] if mine else "",
        allowed=allowed,
        needs_shift=bool(car.kind == TAXI),
        can_take=bool(
            allowed
            and not mine
            and free
            and (car.kind != TAXI or on_shift)
            and player.can_afford(price, moment, Service.CARS)
        ),
        can_give=bool(mine),
        why=_why_not(car, player, allowed, free, on_shift, bool(mine)),
    )


def _why_not(
    car: Car, player: Player, allowed: bool, free: int,
    on_shift: bool, mine: bool,
) -> str:
    """Почему машину не дадут. Пусто — дадут."""
    if mine:
        return ""
    if not allowed:
        if car.kind == TAXI:
            return "Машины парка выдают водителям таксопарка."
        if len(car.jobs) == 1:
            return "Эта машина — начальника полиции."
        return "Служебные машины выдают сотрудникам участка."
    if not free:
        return "Все в разъезде: дождись, пока вернут."
    if car.kind == TAXI and not on_shift:
        return "Машину дают на смену: встань на смену в парке."
    return ""


async def build_cars(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Дом с машинами: витрина или гараж, свои ключи и место за рулём."""
    from bot.car_service import (
        kept_count,
        mine as my_cars,
        release_strangers,
    )
    from bot.game.cars import cars_allowed

    moment = now_ts() if now is None else now
    here = player.where(moment)
    place = get_location(here)
    garage = garage_of(here)
    if garage:
        # Взгляд на гараж заодно отбирает машины у тех, кто больше не на
        # службе: часов для этого нет, а машин в гараже три
        await release_strangers(db, here, moment)
    keys = await my_cars(db, player, moment)
    own = {one["code"]: one for one in keys}
    driving = player.driving(moment)
    car = get_car(driving)
    # Сколько машин боец держит и сколько ему можно: одну, а с подпиской
    # три. Служебные в счёт не идут — они не его
    held = await kept_count(db, player, moment)
    # Оставленные на парковке в счёт не идут: в гараже они стоят, а не
    # в руках. Их видно там, где они стоят, — в особняке и у квартиры
    stored = sum(1 for one in keys if one["parked"])
    limit = cars_allowed(player.is_pro(moment))

    body: dict[str, Any] = {
        "title": place.title if place else "Машины",
        "place": here,
        "garage": bool(garage),
        "credits": player.credits,
        "has_licence": player.school.has_licence,
        "job": player.job_code,
        "driving": car_row(car) if car else None,
        "keys": keys,
        "held": held,
        "stored": stored,
        "limit": limit,
        "pro": player.is_pro(moment),
        "pro_limit": PRO_MAX_CARS,
        "shelf": [],
        "market": None,
        "said": "",
    }
    if garage:
        body["shelf"] = [
            await garage_row(db, one, player, moment, own) for one in garage
        ]
    elif place and place.allows(Service.USED_CARS):
        # Рынок вместо витрины: торгуют не с салоном, а друг с другом
        body["market"] = await market_payload(db, player, moment, keys)
    else:
        body["shelf"] = [
            showcase_row(one, player, moment, own, held < limit)
            for one in SHOWCASE
        ]
    body["note"] = _note(body, player, moment)
    return body


async def market_payload(
    db, player: Player, moment: int, keys: list[dict[str, Any]]
) -> dict[str, Any]:
    """Авторынок: что стоит на продаже и что боец может выставить.

    Своё и чужое разложено по разным спискам: кнопки у них разные —
    чужое покупают, своё снимают с продажи.
    """
    # `club_time`, а не `club_moment`: час выставления лежит в базе
    # строкой, как у комиссионки, а не числом секунд
    from bot.game.clock import club_time
    from bot.game.used_cars import (
        MAX_SHARE,
        MIN_SHARE,
        price_hint,
        price_range,
        sellable,
    )

    rows = []
    for lot in await db.car_lots():
        car = get_car(str(lot["code"]))
        if car is None:  # pragma: no cover - машину убрали из каталога
            continue
        rows.append(dict(
            car_row(car),
            id=int(lot["id"]),
            price=int(lot["price"]),
            seller=lot["seller"] or "боец без имени",
            mine=int(lot["seller_id"]) == player.user_id,
            listed=club_time(lot["listed_at"]),
            # Салонная цена рядом: по ней и видно, дорого ли просят
            salon=car.price,
        ))
    # Выставить можно только свою насовсем, не на парковке и не под собой
    driving = player.driving(moment)
    offer = []
    for one in keys:
        car = get_car(str(one["code"]))
        if car is None or not sellable(car) or not one["own"]:
            continue
        if one["parked"] or one["code"] == driving:
            continue
        low, high = price_range(car)
        offer.append(dict(
            car_row(car), low=low, high=high, hint=price_hint(car),
            salon=car.price,
        ))
    return {
        "lots": [one for one in rows if not one["mine"]],
        "mine": [one for one in rows if one["mine"]],
        "offer": offer,
        "min_share": MIN_SHARE,
        "max_share": MAX_SHARE,
    }


def _note(body: dict[str, Any], player: Player, moment: int) -> str:
    """Строка сверху: что здесь делают и чего не хватает."""
    if not body["has_licence"]:
        return (
            "Без водительского удостоверения за руль не сядешь: права "
            "выдают в участке, а учат на них в автошколе. Купить машину "
            "это не мешает."
        )
    if body["garage"]:
        free = sum(one["free"] for one in body["shelf"])
        paid = any(one["price"] for one in body["shelf"])
        how = (
            f"машину берут на смену, {TAXI_SHIFT_PRICE} 💰 за неё"
            if paid
            else "машины выдают своим и бесплатно"
        )
        return f"Гараж: {how}. Свободных мест: {free}."
    if body["held"] >= body["limit"]:
        # Машин на руках столько, сколько держат: витрина гаснет вся, и
        # сказать об этом надо раньше, чем про гашеные кнопки
        tail = (
            "Поставь одну на парковку: стоящая машина места не занимает."
            if body["pro"]
            else (
                "Поставь её на парковку — в особняке или у своей квартиры, "
                f"— и бери вторую. С подпиской держат {body['pro_limit']}."
            )
        )
        return (
            f"Машин на руках {body['held']} из {body['limit']} — "
            f"больше город не держит. {tail}"
        )
    if body["stored"]:
        return (
            f"На руках {body['held']} из {body['limit']}, в гаражах "
            f"стоит ещё {body['stored']}. Стоящая машина места не "
            f"занимает."
        )
    if body["driving"]:
        return (
            f"Ты за рулём: {body['driving']['title']}, "
            f"дорога короче в {body['driving']['times']} раз."
        )
    return (
        f"Машину берут в аренду на сутки или на месяц либо покупают "
        f"насовсем. Держат {body['limit']}, за руль садятся в карточке, "
        f"во вкладке «Персонаж»."
    )


def driving_payload(player: Player, now: int | None = None) -> dict | None:
    """Машина за бойцом — для карточки. None — боец пешком.

    Карточка рисует её по верхнему краю рамки, за фигурой: машина не
    слот, и места среди слотов ей нет, а задний план — ровно её место.
    """
    moment = now_ts() if now is None else now
    car = get_car(player.driving(moment))
    if car is None:
        return None
    left = max(0, player.car_until - moment) if player.car_until else 0
    return dict(
        car_row(car),
        # Вырезанная версия — только сюда: за спиной бойца студийный фон
        # машины ложился бы заплаткой поверх фона рамки. На витрине и в
        # гараже машина стоит со своим фоном, как её и рисовали
        cutout=car.cutout,
        until=player.car_until,
        left=left,
        left_text=format_term(left) if left else "",
        own=not player.car_until,
    )


__all__ = ["build_cars", "car_row", "driving_payload", "garage_row", "showcase_row"]
