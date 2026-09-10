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
    EXIT_ZONE,
    Location,
    get_location,
)
from bot.models import Player


def place_row(place: Location, here: str) -> dict:
    """Дом на карте: зона нажатия, чем занят и стоит ли в нём боец."""
    return {
        "code": place.code,
        "title": place.title,
        "zone": place.zone.as_dict(),
        "services": [service.value for service in place.services],
        # Дом, за которым ещё нет услуги, открывается запиской «скоро»
        "soon": place.soon,
        "works": place.works,
        "here": place.code == here,
    }


def district_row(district, here: str) -> dict:
    return {
        "code": district.code,
        "title": district.title,
        "image": district.image,
        "here": any(place.code == here for place in district.places),
        "places": [place_row(place, here) for place in district.places],
    }


def build_map(player: Player, now: int | None = None) -> dict:
    """Город целиком: где боец, куда идёт и что где стоит."""
    here = player.where(now)
    place = get_location(here)
    left = player.road_left(now)
    going = get_location(player.travel_to) if left else None
    return {
        "here": here,
        "here_title": place.title if place else "—",
        "district": place.district if place else "",
        "road": {
            "going": bool(going),
            "to": going.code if going else "",
            "to_title": going.title if going else "",
            "seconds_left": left,
            "text": f"В пути до {going.whither} — {format_duration(left)}"
            if going
            else "",
        },
        "exit_zone": EXIT_ZONE.as_dict(),
        "districts": [district_row(district, here) for district in DISTRICTS],
    }


__all__ = ["build_map", "district_row", "place_row"]
