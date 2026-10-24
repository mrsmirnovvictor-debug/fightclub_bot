"""Дорога по городу: кто куда идёт и кого никуда не пускают.

Ходьба сама по себе простая — вышел, дождался, пришёл, — и живёт она в
модели бойца. Здесь то, что модель знать не должна: занят ли боец
прямо сейчас чем-то, из чего не уходят.

Из недодранного боя не выпускают. Это не строгость ради строгости: пока
вызов висит, соперник ждёт ответа, а пока идёт бой — ждёт хода. Уйти
за покупками посреди этого значит бросить того, кто напротив.

Так же и с тем, что держит бойца на месте по часам: смена на работе и
тренировка в зале. У них нет сторожа, который сказал бы «занят», —
замок спадает сам, когда час пройдёт, и спрашивают о нём саму базу.
"""

from __future__ import annotations

import logging

from bot.car_service import spend_charge
from bot.database import Database
from bot.game.charge import km_for
from bot.game.health import format_duration, format_term, now_ts
from bot.game.police import PRISON, jail_left
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
        # Из тюрьмы не выходят, пока срок идёт. Замок стоит здесь, а не
        # в сторожах боя: сидящий ничем не занят — он просто заперт, и
        # отпирает его время, а не чужая развязка
        until, _ = await self.db.jail_term(player.user_id)
        left = jail_left(until, moment)
        if left and player.location == PRISON:
            raise LockedError(
                f"Ты сидишь — до освобождения {format_term(left)}. "
                "Из тюрьмы не выходят."
            )
        # Со смены не уходят: боец на работе, а не гуляет по городу.
        # Замок спадает сам, когда час смены пройдёт, — сторожа у него
        # нет, как и у дороги.
        #
        # Кроме водителя такси: его работа и есть город. Взял машину в
        # парке — катайся всю смену, в том и смысл места
        if player.on_shift(moment) and not on_the_clock(player, moment):
            left = format_duration(player.shift_left(moment))
            raise LockedError(
                f"Ты на смене — до конца {left}. С работы не уходят."
            )
        # И с занятия: учёба это два часа на месте, а не отметка в
        # журнале. Замок спадает сам, как и у смены
        if player.on_lesson(moment):
            left = format_duration(player.lesson_left(moment))
            raise LockedError(
                f"Ты на занятии — до конца {left}. С учёбы не уходят."
            )
        # И с тренировки: пока идёт подход, боец в зале, а не на дороге.
        # Пришедший за результатом замок не держит — забрать его можно и
        # вернувшись, а вот уйти посреди самой тренировки нельзя
        visit = await self.db.gym_visit_of(player.user_id)
        if visit is not None and visit["until"] > moment:
            left = format_duration(visit["until"] - moment)
            raise LockedError(
                f"Ты на тренировке — до конца {left}. Из зала не уходят."
            )

        # Травмированный идёт вдвое дольше: с переломом не побегаешь.
        # А за рулём дорога делится на машину — от двух раз до десяти
        # Расстояние считаем пешим шагом: заряд тратит дорога, а не
        # время за рулём. Снять его надо до `set_out` — после него боец
        # уже «в пути», и откуда он выехал, спрашивать поздно
        on_foot = travel_seconds(player.location, place.code)
        player.set_out(
            place.code,
            travel_seconds(
                player.location, place.code, player.limping,
                player.car_times(moment),
            ),
            moment,
        )
        await self.db.save_player(player)
        # Доехать боец доедет — он выезжал заряженным, — а на том конце
        # выйдет, если бак опустел по дороге
        await spend_charge(self.db, player, km_for(on_foot), moment)
        return place


def on_the_clock(player: Player, now: int) -> bool:
    """Водитель такси за рулём парковой машины: ему город и есть работа.

    Это единственная смена, с которой уходят: таксист, запертый в
    парке, — не таксист. Своя машина тут не считается — в разъезд
    отпускает парк, а не кошелёк.
    """
    from bot.game.cars import TAXI_CAR, TAXI_JOB

    return player.job_code == TAXI_JOB and player.driving(now) == TAXI_CAR


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
