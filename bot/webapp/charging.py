"""АЗС глазами мини-аппа: три колонки и свои машины рядом с ними.

Правил тут нет — их держат `bot.game.charge` и `bot.charge_service`.
Здесь перевод в json.

**Экран — это три колонки в один ряд.** Станция и выглядит так: ряд
колонок, под занятой стоит машина, под машиной — сколько ей осталось.
Списка своих машин на экране нет: он нужен ровно в одну секунду — когда
выбрана свободная колонка, — и приходит окном выбора.

**Заряжают только то, что при себе.** Машина, оставленная на парковке
дома, лежит в чужом гараже, и подать её к колонке по телефону нельзя: за
ней идут ногами. Поэтому в выбор она не попадает вовсе.

**Пролитое считает сервер.** Сколько процентов уйдёт мимо бака, видно
до оплаты и на каждой мерке отдельно: колонка наливает ровно то, за что
заплатили, и узнавать об этом после оплаты обидно.
"""

from __future__ import annotations

from typing import Any

from bot.game.charge import (
    CHARGE_SLOTS,
    CHARGE_STEPS,
    charge_price,
    charge_seconds,
    wasted,
)
from bot.game.cars import get_car
from bot.game.health import format_term, now_ts
from bot.game.locations import Service, get_location
from bot.models import Player
from bot.webapp.bank import purse_payload


def step_rows(
    player: Player, moment: int, car=None, driven: int = 0
) -> list[dict[str, Any]]:
    """Три мерки колонки: объём, цена, время и сколько прольётся мимо."""
    out = []
    for percent in CHARGE_STEPS:
        price = player.price_here(
            charge_price(percent), moment, Service.CHARGE
        )
        seconds = charge_seconds(percent)
        out.append({
            "percent": percent,
            "price": price,
            "seconds": seconds,
            "time_text": format_term(seconds),
            "waste": wasted(car, driven, percent) if car else 0,
            "afford": player.can_afford(price, moment, Service.CHARGE),
        })
    return out


def car_rows(
    player: Player, keys: list[dict[str, Any]], moment: int
) -> list[dict[str, Any]]:
    """Ключи бойца у колонки: заряд, мерки и причина отказа."""
    out = []
    for one in keys:
        car = get_car(str(one["code"]))
        if car is None:  # pragma: no cover - машину убрали из каталога
            continue
        # Пробег обратно из остатка: мерки считают пролитое от него
        driven = max(0, int(one["range"]) - int(one["km_left"]))
        # Полную машину заряжать нечем, а на 99% — уже можно: лишнее
        # пропадёт, и колонка скажет об этом до оплаты
        full = int(one["charge"]) >= 100
        can = bool(not one["charging"] and not full)
        out.append({
            "code": car.code,
            "title": car.title,
            "image": car.image,
            "charge": int(one["charge"]),
            "km_left": int(one["km_left"]),
            "range": int(one["range"]),
            "empty": bool(one["empty"]),
            "charging": bool(one["charging"]),
            "slot": int(one["charging_slot"]),
            "left": int(one["charging_left"]),
            "left_text": (
                format_term(int(one["charging_left"]))
                if one["charging_left"]
                else ""
            ),
            "parked": str(one["parked"] or ""),
            "parked_text": str(one["parked_text"] or ""),
            "full": full,
            "can_charge": can,
            "why": _why(one, full),
            "steps": step_rows(player, moment, car, driven),
        })
    return out


def _why(one: dict[str, Any], full: bool) -> str:
    """Почему эту машину на колонку не поставить."""
    if one["charging"]:
        return f"Уже на {one['charging_slot']}-й колонке."
    if full:
        return "Бак полон — заряжать нечего."
    return ""


async def build_charge(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Экран АЗС: колонки города и свои машины при них."""
    from bot.car_service import at_hand
    from bot.charge_service import slots as station_slots

    moment = now_ts() if now is None else now
    place = get_location(player.where(moment))
    columns = await station_slots(db, player, moment)
    # Только то, что при бойце: оставленную в гараже дома машину к
    # колонке не подать — за ней надо сходить
    keys = await at_hand(db, player, moment)
    body: dict[str, Any] = {
        "title": place.title if place else "АЗС",
        "slots": columns,
        "count": CHARGE_SLOTS,
        "free": sum(1 for one in columns if not one["busy"]),
        "steps": step_rows(player, moment),
        "cars": car_rows(player, keys, moment),
        "credits": player.credits,
        "purse": purse_payload(player, moment, Service.CHARGE),
        "said": "",
    }
    body["note"] = _note(body)
    return body


def _note(body: dict[str, Any]) -> str:
    """Строка сверху: что тут делают и чего не хватает."""
    ready = [one for one in body["slots"] if one["mine"] and one["ready"]]
    if ready:
        which = ", ".join(f"{one['slot']}-я" for one in ready)
        return (
            f"Заряжено, можно забирать: {which}. Колонка освободится, "
            f"как только машину заберут."
        )
    standing = [one for one in body["slots"] if one["mine"]]
    if standing:
        one = standing[0]
        return (
            f"{one['title']} на {one['slot']}-й колонке: осталось "
            f"{one['left_text']}. Ждать тут не обязательно — зарядка "
            f"идёт и без тебя."
        )
    if not body["cars"]:
        return (
            "Колонок тут три, и заряжают они то, что при тебе. Машину с "
            "парковки дома сюда не подать — за ней надо сходить. А если "
            "машины нет вовсе, берут её в автосалоне или на авторынке, "
            "он тут же, по соседству."
        )
    if not body["free"]:
        return (
            "Все три колонки заняты. Освободятся, когда хозяева заберут "
            "свои машины, — зарядка идёт минутами, а не часами."
        )
    cheap = body["steps"][0]
    rich = body["steps"][-1]
    return (
        f"Колонка наливает проценты: +{cheap['percent']}% за "
        f"{cheap['price']} 💰 и {cheap['time_text']}, +{rich['percent']}% "
        f"за {rich['price']} 💰 и {rich['time_text']}. Сверх полного бака "
        f"заряд пропадает."
    )


__all__ = ["build_charge", "car_rows", "step_rows"]
