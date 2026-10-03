"""Карта города для мини-аппа: районы, дома и где сейчас боец.

Отдаём всю карту разом: шесть картинок и четырнадцать домов — это
полсотни строк, и держать их на клиенте отдельным файлом значит
поддерживать два списка вместо одного. Заодно клиент не считает сам, что
где можно: доступность приходит с сервера, потому что он же её и
проверяет.
"""

from __future__ import annotations

from bot.game.health import format_duration
from bot.game.locations import (
    DISTRICTS,
    Location,
    get_location,
    travel_seconds,
)
from bot.models import Player


def place_row(place: Location, here: str, limping: bool = False) -> dict:
    """Дом на карте: вход, чем занят и сколько до него идти.

    Секунды считает сервер, хотя правило простое. Стоит повторить его на
    клиенте — и однажды они разойдутся: человек согласится на десять
    секунд, а прождёт двадцать. Тем более с травмой: она удваивает
    дорогу, и знает об этом только сервер.
    """
    return {
        "code": place.code,
        "title": place.title,
        # Вход — то, по чему рисуется подсветка: ровно дверь, и ничего
        # вокруг. Касание ловится по рамке с запасом: дверь пальцу мала
        "entrance": [[x, y] for x, y in place.entrance],
        "touch": place.touch.as_dict(),
        "zone": place.bounds.as_dict(),
        "services": [service.value for service in place.services],
        # Дом, за которым ещё нет услуги, открывается запиской «скоро»
        "soon": place.soon,
        "works": place.works,
        "here": place.code == here,
        "walk": travel_seconds(here, place.code, limping),
    }


def district_row(district, here: str, limping: bool = False) -> dict:
    return {
        "code": district.code,
        "title": district.title,
        "image": district.image,
        "here": any(place.code == here for place in district.places),
        # Куда ведут стрелки. Города целиком не видно — это единственное,
        # что связывает шесть картинок в один город
        "around": dict(district.around),
        "places": [place_row(place, here, limping) for place in district.places],
    }


def build_map(
    player: Player, now: int | None = None, raids: dict | None = None
) -> dict:
    """Город целиком: где боец, куда идёт и что где стоит.

    `raids` — плашки под вывесками рейдовых домов, по дому на запись: их
    считает сервер, потому что для них нужна база (свой рейд в этом окне
    уже пройден или ещё нет). Домов два, и расписания у них свои, поэтому
    и плашек две: одна сказала бы под стадионом часы казино.
    """
    here = player.where(now)
    place = get_location(here)
    left = player.road_left(now)
    going = get_location(player.travel_to) if left else None
    limping = player.limping
    return {
        "here": here,
        "here_title": place.title if place else "—",
        "district": place.district if place else "",
        "road": {
            "going": bool(going),
            "to": going.code if going else "",
            "to_title": going.title if going else "",
            "seconds_left": left,
            # Вся дорога целиком: без неё на клиенте нечем нарисовать,
            # насколько она пройдена, — а заряд батарейки считается
            # именно от этого. Заново открыв карту в пути, клиент
            # начала дороги уже не помнит
            "seconds": travel_seconds(here, going.code, limping) if going else 0,
            "text": f"В пути до {going.whither} — {format_duration(left)}"
            if going
            else "",
        },
        # Отсчёты рейдов по домам: пусто — плашки под этим домом нет
        "raids": raids or {},
        "districts": [
            district_row(district, here, limping) for district in DISTRICTS
        ],
    }


__all__ = ["build_map", "district_row", "place_row"]
