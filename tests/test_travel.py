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
    # Считаем по неразмеченному району: там дорога внутри по-прежнему
    # идёт по таймеру
    assert travel_seconds("bank", "market") == STEP_INSIDE
    assert travel_seconds(FIGHT_CLUB, "pharmacy") == STEP_BETWEEN
    assert travel_seconds(FIGHT_CLUB, FIGHT_CLUB) == 0


def test_inside_a_walkable_district_the_walk_is_the_road():
    """По размеченной улице боец идёт ногами, а не ждёт таймер.

    Брать десять секунд сверху значило бы взять дважды за одно и то же:
    он уже прошёл эту улицу своими шагами.
    """
    from bot.game.locations import get_district

    assert get_district("main_hub").walkable
    assert travel_seconds(FIGHT_CLUB, "weapon_shop") == 0
    assert travel_seconds("weapon_shop", "workshop") == 0
    # А между районами дорога осталась: ногами её не проходят
    assert travel_seconds(FIGHT_CLUB, "pharmacy") == STEP_BETWEEN


async def test_a_step_inside_a_walkable_district_arrives_at_once(db):
    """Нулевая дорога не оставляет бойца висеть «в пути»."""
    player = make_player()
    await db.save_player(player)
    travel = Travel(db)

    await travel.go(player, "weapon_shop", now=1000)

    fresh = await db.get_player(1)
    assert fresh.location == "weapon_shop"
    assert fresh.travel_to is None and fresh.arrives_at == 0
    assert not fresh.in_transit(1000)


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

    # Дорога между районами: внутри размеченного её больше нет
    await travel.go(player, "pharmacy", now=1000)
    assert (await db.get_player(1)).travel_to == "pharmacy"

    # бот «перезапустили»: задачи нет, а срок вышел сам собой
    fresh = await db.get_player(1)
    assert fresh.where(1030) == "pharmacy"
    assert fresh.arrive(1030) is True
    await db.save_player(fresh)
    assert (await db.get_player(1)).location == "pharmacy"


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
    place = await travel.go(player, "pharmacy")

    assert place.code == "pharmacy"
    assert (await db.get_player(1)).travel_to == "pharmacy"


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


def test_every_entrance_is_four_points_inside_its_map():
    """Вход — четырёхугольник по двери, и он на карте, а не за краем."""
    from bot.game.locations import LOCATIONS

    for place in LOCATIONS:
        assert len(place.entrance) == 4, f"{place.code}: не четыре точки"
        for x, y in place.entrance:
            assert 0 <= x <= 1 and 0 <= y <= 1, f"{place.code}: точка {x},{y}"
        box = place.bounds
        assert box.w > 0 and box.h > 0, place.code


def test_an_entrance_is_a_door_and_not_a_whole_building():
    """Дверь занимает угол карты, а не полкарты.

    Раньше зоной был весь дом, и нажатие «в здание» попадало в небо над
    крышей. Теперь целятся во вход — значит, он и должен быть размером с
    вход: если дверь вдруг вырастет в полкарты, это уже не дверь.
    """
    from bot.game.locations import LOCATIONS

    for place in LOCATIONS:
        box = place.bounds
        assert box.w <= 0.35, f"{place.code}: дверь шириной в треть карты"
        assert box.h <= 0.20, f"{place.code}: дверь высотой в пятую часть карты"


def test_doors_on_one_map_never_share_a_point():
    """Две двери под одним пальцем — это нажатие наугад.

    Считаем и по самим дверям, и по области касания с запасом: разъехаться
    должны обе, иначе запас под палец сам и создаст неоднозначность.
    """
    from bot.game.locations import DISTRICTS

    for district in DISTRICTS:
        places = district.places
        for x, y in grid_points():
            doors = [one.code for one in places if one.holds(x, y)]
            assert len(doors) <= 1, f"{district.code}: двери {doors}"
            near = [one.code for one in places if one.touch.holds(x, y)]
            assert len(near) <= 1, f"{district.code}: запас под палец {near}"


# Палец накрывает примерно сорок четыре точки экрана — это общая мерка
# для кнопок на телефоне. Карта на экране в 390 точек рисуется шириной в
# 362, то есть в масштабе 362/941 от исходника
PHONE_SCALE = 362 / 941
FINGER = 44


# ---------- город как целое ----------


def test_every_road_leads_back():
    """Соседство взаимное: вышел из района — сможешь вернуться.

    В коде это записано словами («стережёт тест»), а теста до второй
    очереди не было вовсе. С шестью районами связи держались в голове,
    с шестнадцатью — уже нет: одна опечатка в стороне света, и район
    становится ловушкой, из которой не выйти.
    """
    from bot.game.locations import DISTRICTS, DISTRICT_BY_CODE, OPPOSITE

    for district in DISTRICTS:
        for side, to in district.around.items():
            neighbour = DISTRICT_BY_CODE.get(to)
            assert neighbour is not None, f"{district.code} → {to}: такого района нет"
            back = neighbour.around.get(OPPOSITE[side])
            assert back == district.code, (
                f"{district.code} → {to} ({side}), а обратно — {back}"
            )


def test_the_whole_city_is_walkable_from_the_centre():
    """Из центра можно дойти до любого района, не проваливаясь в дыру."""
    from bot.game.locations import DISTRICTS, DISTRICT_BY_CODE

    seen = {"main_hub"}
    edge = ["main_hub"]
    while edge:
        district = DISTRICT_BY_CODE[edge.pop()]
        for to in district.around.values():
            if to not in seen:
                seen.add(to)
                edge.append(to)

    missing = {district.code for district in DISTRICTS} - seen
    assert not missing, f"до этих районов не дойти: {sorted(missing)}"


def test_no_district_is_drawn_empty():
    """На каждой карте стоит хотя бы один дом."""
    from bot.game.locations import DISTRICTS

    empty = [district.code for district in DISTRICTS if not district.places]
    assert not empty, f"районы без домов: {empty}"


def test_every_house_stands_on_a_drawn_district():
    """Дом не может стоять на карте, которой нет."""
    from bot.game.locations import DISTRICT_BY_CODE, LOCATIONS

    for place in LOCATIONS:
        assert place.district in DISTRICT_BY_CODE, f"{place.code}: район не нарисован"


def test_every_district_map_is_named_as_the_bucket_named_it():
    """Имена карт списаны с хранилища, а не придуманы по правилу.

    Стройной привычки в них нет: где-то на конце «_district», где-то
    нет, и первая очередь вдобавок в jpeg, а вторая в png. Угадать такое
    нельзя, поэтому список сверяется с выгрузкой целиком — иначе район
    открывается пустой картинкой, а заметит это игрок, а не тест.
    """
    from bot.game.locations import DISTRICTS

    in_bucket = {
        "main_hub.jpeg", "clothes_pharmacy.jpeg", "pawnshop_casino.jpeg",
        "northern_wall_premium.jpeg", "bank_market_post.jpeg",
        "stadium_bar.jpeg",
        "vcpd_hospital_district.png", "driving_school_insurance_district.png",
        "car_dealership.png", "gym_office_district.png",
        "police_school_medical_college.png", "military_base_training_ground.png",
        "cadet_corps_dormitory.png", "fight_tournament_stadium.png",
        "residential_district.png", "mafia_mansion.png",
    }
    ours = {district.image.rsplit("/", 1)[-1] for district in DISTRICTS}

    assert ours == in_bucket


def test_a_house_takes_the_picture_of_its_own_district():
    """Дом не считает адрес карты заново, а спрашивает у района.

    Формат у карт разный, и второе место, где адрес выводится, однажды
    разошлось бы с первым.
    """
    from bot.game.locations import DISTRICT_BY_CODE, LOCATIONS

    for place in LOCATIONS:
        assert place.image == DISTRICT_BY_CODE[place.district].image


def test_every_local_stands_on_the_street():
    """Завсегдатай не может стоять в стене.

    Место ему выбирают на глаз по картинке, и промахнуться легко — а
    промах виден только тому, кто дошёл до этого района ногами.
    """
    from bot.content.crowd import CROWD
    from bot.game.locations import get_district

    for code, locals_here in CROWD.items():
        district = get_district(code)
        assert district is not None, f"{code}: такого района нет"
        assert district.walkable, f"{code}: район не размечен, ходить негде"
        for local in locals_here:
            assert district.holds(local.x, local.y), (
                f"{code}/{local.code}: стоит не на улице"
            )


def test_the_marked_street_reaches_every_door():
    """С улицы можно подойти к каждой двери района.

    Иначе дом виден, а войти в него ногами нельзя — и это заметит игрок,
    а не разметчик.
    """
    from bot.game.locations import DISTRICTS

    # Настолько близко подходят к двери, чтобы в неё войти. То же число,
    # что в мини-аппе: DOOR_REACH в долях высоты картинки
    reach = 130 / 1672

    for district in DISTRICTS:
        if not district.walkable:
            continue
        for place in district.places:
            door = place.bounds
            x = door.x + door.w / 2
            close = any(
                district.holds(x, door.y + door.h + step / 1672)
                for step in range(0, int(reach * 1672), 10)
            )
            assert close, f"{district.code}/{place.code}: к двери не подойти"


def test_every_door_is_four_points_on_the_picture():
    """Вход — ровно четыре точки, и все они внутри картинки."""
    from bot.game.locations import LOCATIONS

    for place in LOCATIONS:
        assert len(place.entrance) == 4, f"{place.code}: не четырёхугольник"
        for x, y in place.entrance:
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, f"{place.code}: точка за краем"


def test_every_door_is_big_enough_for_a_finger():
    """В дверь должно попадать пальцем, а не прицеливаясь.

    Двери размечены по рисунку и бывают мелкими: у почты вход шириной в
    восемьдесят точек исходника. С запасом под палец такая дверь ещё
    берётся, без запаса — уже нет. Если разметку однажды уточнят до
    совсем узкой щели, это должно всплыть здесь, а не на телефоне.
    """
    from bot.game.locations import LOCATIONS

    for place in LOCATIONS:
        near = place.touch
        wide = near.w * 941 * PHONE_SCALE
        tall = near.h * 1672 * PHONE_SCALE
        assert wide >= FINGER, f"{place.code}: {wide:.0f} точек в ширину"
        assert tall >= FINGER, f"{place.code}: {tall:.0f} точек в высоту"


def test_the_touch_area_is_wider_than_the_door_but_only_around_it():
    """Дверь пальцу мала — её расширяют, но подсветка остаётся на двери."""
    from bot.game.locations import TOUCH_PAD_X, TOUCH_PAD_Y, get_location

    club = get_location("fight_club")
    door, near = club.bounds, club.touch

    assert near.w > door.w and near.h > door.h
    assert abs((door.x - near.x) - TOUCH_PAD_X) < 1e-9
    assert abs((door.y - near.y) - TOUCH_PAD_Y) < 1e-9
    # запас по ширине больше: соседние дома стоят бок о бок
    assert TOUCH_PAD_X > TOUCH_PAD_Y


def test_a_touch_finds_the_door_under_it():
    """Касание в дверь попадает в дом, а в стену рядом — уже никуда."""
    from bot.game.locations import get_location

    club = get_location("fight_club")

    assert club.holds(0.55, 0.30), "середина двери"
    assert not club.holds(0.20, 0.30), "стена слева от двери"
    assert not club.holds(0.55, 0.10), "вывеска над дверью"


def test_houses_without_a_trade_are_still_on_the_map():
    """Банк, рынок, почта, бар и стадион пока только стоят.

    Зайти в них можно — иначе город выглядит нарисованным наполовину, —
    но никакой услуги за ними нет, и сервер её не знает.
    """
    from bot.game.locations import LOCATIONS

    coming = [place for place in LOCATIONS if not place.works]
    # Пять домов первой очереди и все шестнадцать второй: город вырос
    # картинками раньше, чем правилами, и это нормально — лишь бы в
    # каждом было сказано, чего в нём ждать
    assert {"bank", "market", "post_office", "stadium", "bar"} <= {
        place.code for place in coming
    }
    assert len(coming) == 21
    for place in coming:
        assert place.soon, f"{place.code}: не сказано, что здесь будет"
        assert place.services == ()
