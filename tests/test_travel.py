"""Дорога по городу: где боец стоит, куда идёт и чего ему нельзя."""

import pytest

from bot.game.classes import get_class
from bot.game.locations import (
    FIGHT_CLUB,
    STEP_BETWEEN,
    STEP_INSIDE,
    Service,
    get_location,
    travel_seconds,
)
from bot.models import Player
from bot.travel_service import LockedError, Travel, TravelError, require


def make_player(user_id: int = 1) -> Player:
    fclass = get_class("rogue")
    return Player(
        user_id=user_id, nickname="Тайлер", class_code="rogue",
        **fclass.base_stats.as_dict(),
    )


class Keeper:
    """Служба боёв: занят боец или нет."""

    def __init__(self, busy: bool = False) -> None:
        self.busy = busy

    def is_busy(self, user_id: int) -> bool:
        return self.busy


# ---------- где боец ----------


def test_a_fresh_fighter_stands_in_the_club():
    """Все начинают в клубе: драться можно с первой минуты."""
    player = make_player()
    assert player.where() == FIGHT_CLUB
    require(player, Service.FIGHT)  # не бросает


def test_the_road_takes_longer_between_districts():
    """Соседнее здание ближе, чем другой конец города."""
    assert travel_seconds(FIGHT_CLUB, "weapons") == STEP_INSIDE
    assert travel_seconds(FIGHT_CLUB, "pharmacy") == STEP_BETWEEN
    assert travel_seconds(FIGHT_CLUB, FIGHT_CLUB) == 0


def test_on_the_road_a_fighter_is_neither_here_nor_there(db):
    """Пока идёшь — старое место уже недоступно, а новое ещё нет."""
    player = make_player()
    player.set_out("pharmacy", STEP_BETWEEN, now=1000)

    assert player.in_transit(1005) and player.road_left(1005) == 15
    with pytest.raises(TravelError, match="в дороге"):
        require(player, Service.FIGHT, now=1005)
    with pytest.raises(TravelError, match="в дороге"):
        require(player, Service.POTIONS, now=1005)

    # срок вышел — боец на месте, и таймер для этого не нужен
    assert player.where(1020) == "pharmacy"
    require(player, Service.POTIONS, now=1020)


async def test_arrival_is_written_down_when_somebody_looks(db):
    """Прибытие не по таймеру, а по часам: оно переживает перезапуск бота."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)

    await travel.go(player, "weapons", now=1000)
    assert (await db.get_player(1)).travel_to == "weapons"

    # бот «перезапустили»: задачи нет, а срок вышел сам собой
    fresh = await db.get_player(1)
    assert fresh.where(1010) == "weapons"
    assert fresh.arrive(1010) is True
    await db.save_player(fresh)
    assert (await db.get_player(1)).location == "weapons"


# ---------- что где можно ----------


@pytest.mark.parametrize(
    "place,service",
    [
        (FIGHT_CLUB, Service.FIGHT),
        ("weapons", Service.WEAPONS),
        ("clothes", Service.CLOTHES),
        ("pharmacy", Service.POTIONS),
        ("workshop", Service.REPAIR),
        ("casino", Service.RAID),
        ("premium", Service.PREMIUM),
        ("pawnshop", Service.MARKET),
    ],
)
def test_every_service_has_its_address(place, service):
    """У каждого дела свой адрес, и в чужом месте его не сделать."""
    player = make_player()
    player.location = place
    require(player, service)

    player.location = FIGHT_CLUB if place != FIGHT_CLUB else "weapons"
    with pytest.raises(TravelError, match="Здесь этого не делают"):
        require(player, service)


def test_the_refusal_says_where_to_go():
    """Отказ бесполезен, если не сказать, куда идти."""
    player = make_player()
    with pytest.raises(TravelError, match="Мастерская"):
        require(player, Service.REPAIR)


# ---------- кого не выпускают ----------


async def test_a_fighter_in_a_duel_stays_where_he_is(db):
    """Из недодранного боя не уходят: соперник ждёт хода."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)
    travel.watch(Keeper(busy=True))

    with pytest.raises(LockedError, match="Сначала закончи бой"):
        await travel.go(player, "weapons")

    assert (await db.get_player(1)).where() == FIGHT_CLUB


async def test_a_free_fighter_walks_out(db):
    """Бой кончился — держать больше нечем."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)
    keeper = Keeper(busy=True)
    travel.watch(keeper)

    keeper.busy = False
    place = await travel.go(player, "weapons")

    assert place.code == "weapons"
    assert (await db.get_player(1)).travel_to == "weapons"


async def test_the_road_cannot_be_interrupted_by_another_road(db):
    """На полпути не разворачиваются: сначала дойди."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)

    await travel.go(player, "pharmacy", now=1000)
    with pytest.raises(TravelError, match="ещё в дороге"):
        await travel.go(player, "weapons", now=1005)


async def test_walking_where_you_already_stand_is_refused(db):
    """Шаг на месте — не шаг."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)

    with pytest.raises(TravelError, match="и так"):
        await travel.go(player, FIGHT_CLUB)


def test_the_club_is_where_the_ring_is():
    """Клуб — единственное место с рингом: бои в городе больше нигде."""
    ringed = [place for place in get_location(FIGHT_CLUB).services]
    assert Service.FIGHT in ringed
