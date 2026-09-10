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
    assert travel_seconds(FIGHT_CLUB, "weapon_shop") == STEP_INSIDE
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

    await travel.go(player, "weapon_shop", now=1000)
    assert (await db.get_player(1)).travel_to == "weapon_shop"

    # бот «перезапустили»: задачи нет, а срок вышел сам собой
    fresh = await db.get_player(1)
    assert fresh.where(1010) == "weapon_shop"
    assert fresh.arrive(1010) is True
    await db.save_player(fresh)
    assert (await db.get_player(1)).location == "weapon_shop"


# ---------- что где можно ----------


@pytest.mark.parametrize(
    "place,service",
    [
        (FIGHT_CLUB, Service.FIGHT),
        ("weapon_shop", Service.WEAPONS),
        ("clothes_shop", Service.CLOTHES),
        ("pharmacy", Service.POTIONS),
        ("workshop", Service.REPAIR),
        ("casino", Service.RAID),
        ("premium_shop", Service.PREMIUM),
        ("pawnshop", Service.MARKET),
    ],
)
def test_every_service_has_its_address(place, service):
    """У каждого дела свой адрес, и в чужом месте его не сделать."""
    player = make_player()
    player.location = place
    require(player, service)

    player.location = FIGHT_CLUB if place != FIGHT_CLUB else "weapon_shop"
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
        await travel.go(player, "weapon_shop")

    assert (await db.get_player(1)).where() == FIGHT_CLUB


async def test_a_free_fighter_walks_out(db):
    """Бой кончился — держать больше нечем."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)
    keeper = Keeper(busy=True)
    travel.watch(keeper)

    keeper.busy = False
    place = await travel.go(player, "weapon_shop")

    assert place.code == "weapon_shop"
    assert (await db.get_player(1)).travel_to == "weapon_shop"


async def test_the_road_cannot_be_interrupted_by_another_road(db):
    """На полпути не разворачиваются: сначала дойди."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)

    await travel.go(player, "pharmacy", now=1000)
    with pytest.raises(TravelError, match="ещё в дороге"):
        await travel.go(player, "weapon_shop", now=1005)


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


# ---------- зоны нажатия ----------


# Сетка, по которой проверяются силуэты: полтысячи точек на сторону.
# Мельче считать нечего — палец на телефоне крупнее пяти сотых процента
GRID = 500


def grid_points():
    for row in range(GRID):
        for column in range(GRID):
            yield column / GRID, row / GRID


def test_every_silhouette_stays_inside_its_map():
    """Точка за краем картинки недостижима: до неё не дотянуться пальцем."""
    from bot.game.locations import EXIT_ZONE, LOCATIONS

    for place in LOCATIONS:
        assert len(place.polygon) >= 3, place.code
        for x, y in place.polygon:
            assert 0 <= x <= 1 and 0 <= y <= 1, f"{place.code}: точка {x},{y}"
        # рамка вокруг силуэта — запасная, но и она должна быть на карте
        assert 0 <= place.zone.x and place.zone.x + place.zone.w <= 1.0 + 1e-9
        assert 0 <= place.zone.y and place.zone.y + place.zone.h <= 1.0 + 1e-9
    assert EXIT_ZONE.x + EXIT_ZONE.w <= 1.0 and EXIT_ZONE.y + EXIT_ZONE.h <= 1.0


def test_houses_on_one_map_do_not_share_a_point():
    """Два дома под одним пальцем — это нажатие наугад.

    Считаем по силуэтам, а не по рамкам вокруг них: рамки соседних домов
    краями пересекаются и в жизни (банк с рынком), а нажатие ловит силуэт.
    """
    from bot.game.locations import DISTRICTS

    for district in DISTRICTS:
        places = district.places
        for x, y in grid_points():
            hit = [place.code for place in places if place.holds(x, y)]
            assert len(hit) <= 1, f"{district.code}: {hit} в точке {x:.3f},{y:.3f}"


def test_the_way_out_is_not_covered_by_a_house():
    """Нижний проход общий для всех карт — его не должен закрывать дом."""
    from bot.game.locations import EXIT_ZONE, LOCATIONS

    for x, y in grid_points():
        if not EXIT_ZONE.holds(x, y):
            continue
        covered = [place.code for place in LOCATIONS if place.holds(x, y)]
        assert not covered, f"{covered} закрыли выход в точке {x:.3f},{y:.3f}"


def test_a_touch_finds_the_house_under_it():
    """Касание в дом попадает в него, а рядом с домом — уже никуда.

    Силуэт для того и обведён: у клуба срезаны углы крыши, и нажатие в
    угол его рамки должно проходить мимо — там нарисовано небо.
    """
    from bot.game.locations import get_location

    club = get_location("fight_club")

    assert club.holds(0.5, 0.2), "середина дома"
    assert club.zone.holds(0.17, 0.07), "угол рамки"
    assert not club.holds(0.17, 0.07), "а в самом доме этого угла нет"
    assert not club.holds(0.5, 0.9), "мимо дома"


def test_houses_without_a_trade_are_still_on_the_map():
    """Банк, рынок, почта, бар, стадион и «Вал» пока только стоят.

    Зайти в них можно — иначе город выглядит нарисованным наполовину, —
    но никакой услуги за ними нет, и сервер её не знает.
    """
    from bot.game.locations import LOCATIONS

    coming = [place for place in LOCATIONS if not place.works]
    assert {place.code for place in coming} == {
        "northern_wall_shop", "bank", "market", "post_office", "stadium", "bar"
    }
    for place in coming:
        assert place.soon, f"{place.code}: не сказано, что здесь будет"
        assert place.services == ()
