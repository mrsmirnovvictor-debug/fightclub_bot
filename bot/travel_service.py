"""Дорога по городу: кто куда идёт и кого никуда не пускают.

Ходьба сама по себе простая — вышел, дождался, пришёл, — и живёт она в
модели бойца. Здесь то, что модель знать не должна: занят ли боец
прямо сейчас чем-то, из чего не уходят.

Из недодранного боя не выпускают. Это не строгость ради строгости: пока
вызов висит, соперник ждёт ответа, а пока идёт бой — ждёт хода. Уйти
за покупками посреди этого значит бросить того, кто напротив.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.health import format_duration, now_ts
from bot.game.locations import (
    Location,
    Service,
    get_location,
    travel_seconds,
    where_to,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class TravelError(Exception):
    """Идти нельзя — и на то есть причина, которую видно человеку."""


class LockedError(TravelError):
    """Боец занят: из боя и сбора отряда не уходят."""


class Travel:
    """Кто ведёт бойцов по карте."""

    def __init__(self, db: Database) -> None:
        self.db = db
        # Службы, у которых спрашивают, занят ли боец. Подставляются при
        # старте бота: сама дорога о боях ничего не знает
        self._keepers: list = []

    def watch(self, *services) -> None:
        """Кого спрашивать, можно ли уходить."""
        self._keepers.extend(service for service in services if service is not None)

    def locked_by(self, user_id: int) -> bool:
        return any(keeper.is_busy(user_id) for keeper in self._keepers)

    async def go(self, player: Player, target: str, now: int | None = None) -> Location:
        """Отправить бойца в путь. Возвращает, куда он пошёл."""
        moment = now_ts() if now is None else now
        place = get_location(target)
        if place is None:
            raise TravelError("Такого места в городе нет.")
        if player.in_transit(moment):
            left = format_duration(player.road_left(moment))
            raise TravelError(f"Ты ещё в дороге — идти {left}.")
        if player.arrive(moment):
            await self.db.save_player(player)
        if player.location == place.code:
            raise TravelError(f"Ты и так {here_text(place)}.")
        if self.locked_by(player.user_id):
            raise LockedError(
                "Сначала закончи бой: соперник ждёт, и уходить посреди "
                "этого нечестно."
            )

        player.set_out(place.code, travel_seconds(player.location, place.code), moment)
        await self.db.save_player(player)
        return place


def here_text(place: Location) -> str:
    """«в бойцовском клубе» — как это читается в фразе про место."""
    return f"в локации «{place.title}»"


def denied(service: Service) -> str:
    """Почему здесь нельзя и куда за этим идти."""
    place = where_to(service)
    if place is None:  # pragma: no cover - услуга без адреса
        return "Здесь этого не делают."
    return f"Здесь этого не делают. За этим — в «{place.title}»."


def require(player: Player, service: Service, now: int | None = None) -> None:
    """Проверить, что боец на месте и здесь это можно. Иначе — TravelError."""
    if player.in_transit(now):
        left = format_duration(player.road_left(now))
        raise TravelError(f"Ты в дороге — идти ещё {left}.")
    place = get_location(player.where(now))
    if place is None or not place.allows(service):
        raise TravelError(denied(service))


__all__ = ["LockedError", "Travel", "TravelError", "denied", "here_text", "require"]
