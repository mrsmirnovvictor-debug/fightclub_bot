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


def place_row(
    place: Location, here: str, limping: bool = False, times: int = 1
) -> dict:
    """Дом на карте: вход, чем занят и сколько до него идти.

    Секунды считает сервер, хотя правило простое. Стоит повторить его на
    клиенте — и однажды они разойдутся: человек согласится на десять
    секунд, а прождёт двадцать. Тем более с травмой и машиной: одна
    удваивает дорогу, вторая делит её на себя, и знает об этом только
    сервер.
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
        "walk": travel_seconds(here, place.code, limping, times),
    }


def district_row(
    district, here: str, limping: bool = False, times: int = 1
) -> dict:
    return {
        "code": district.code,
        "title": district.title,
        "image": district.image,
        "here": any(place.code == here for place in district.places),
        # Куда ведут стрелки. Города целиком не видно — это единственное,
        # что связывает шесть картинок в один город
        "around": dict(district.around),
        "places": [
            place_row(place, here, limping, times)
            for place in district.places
        ],
    }


def build_map(
    player: Player,
    now: int | None = None,
    raids: dict | None = None,
    turf: dict | None = None,
    crowd: dict | None = None,
    cars: list | None = None,
) -> dict:
    """Город целиком: где боец, куда идёт и что где стоит.

    `raids` — плашки под вывесками рейдовых домов, по дому на запись: их
    считает сервер, потому что для них нужна база (свой рейд в этом окне
    уже пройден или ещё нет). Домов два, и расписания у них свои, поэтому
    и плашек две: одна сказала бы под стадионом часы казино.

    `turf` — война за территории: флажок с владельцем под каждой точкой
    и плашка под ногами у того, кто до точки дошёл. Захват берут на
    месте, и карта — единственный экран, с которого это видно.
    """
    here = player.where(now)
    place = get_location(here)
    left = player.road_left(now)
    going = get_location(player.travel_to) if left else None
    limping = player.limping
    # Машина под бойцом делит дорогу: от двух раз у хэтчбека до
    # десяти у гиперкара. Карта обязана показывать то время, которое
    # человек и прождёт
    times = player.car_times(now)
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
            "seconds": travel_seconds(here, going.code, limping, times)
            if going
            else 0,
            "text": f"В пути до {going.whither} — {format_duration(left)}"
            if going
            else "",
        },
        # Отсчёты рейдов по домам: пусто — плашки под этим домом нет
        "raids": raids or {},
        # Война за территории: `spots` — флажки по домам, `here` —
        # плашка с кнопкой сбора, если боец стоит на точке
        "turf": turf or {"spots": {}, "here": None},
        # Сколько народу в этом доме и сколько в нём драк. Счётчик в
        # углу карты — единственное место, откуда видно, что рядом кто-то
        # есть: внутрь дома за этим не заходят
        "crowd": crowd or {"total": 0, "online": 0, "fights": 0},
        # Машины, брошенные у этого здания. Стоят они на улице, и видит
        # их всякий, кто сюда пришёл: счётчик в углу карты — там же,
        # где и люди
        "cars": cars or [],
        "districts": [
            district_row(district, here, limping, times)
            for district in DISTRICTS
        ],
    }


__all__ = ["build_map", "district_row", "place_row"]
