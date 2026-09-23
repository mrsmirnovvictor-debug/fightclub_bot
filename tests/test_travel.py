"""Дорога по городу: где боец стоит, куда идёт и чего ему нельзя."""

import pytest

from bot.game.classes import get_class
from bot.game.locations import (
    FIGHT_CLUB,
    STEP,
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


def test_the_road_is_counted_in_steps():
    """Переход один и тот же: шаг в соседний район или вход в дверь.

    Соседний дом в своём районе — одна дверь. Дом в соседнем районе —
    шаг и дверь. Дальний конец города — столько шагов, сколько до него
    районов, и ещё один на дверь.
    """
    assert travel_seconds(FIGHT_CLUB, FIGHT_CLUB) == 0
    assert travel_seconds(FIGHT_CLUB, "weapon_shop") == STEP
    assert travel_seconds(FIGHT_CLUB, "pharmacy") == 2 * STEP
    # Пример из уговора: Старый город → Центр → Северный Вал (или
    # Торговый квартал, дорога та же) → Стадион, и дверь бара
    assert travel_seconds("casino", "bar") == 4 * STEP
    # Дорога одинакова в обе стороны: город не имеет уклона
    assert travel_seconds("bar", "casino") == 4 * STEP


def test_on_the_road_a_fighter_is_neither_here_nor_there(db):
    """Пока идёшь — старое место уже недоступно, а новое ещё нет."""
    player = make_player()
    player.set_out("pharmacy", 2 * STEP, now=1000)

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


def city_grid() -> dict[str, tuple[int, int]]:
    """Разложить районы по клеткам, идя от центра по сторонам света.

    Заодно это и проверка: если две дороги приводят в одну клетку разные
    районы или один район в разные клетки, карта сложена сама на себя —
    такое читается как «вправо, вниз, влево, вверх и ты в другом месте».
    """
    from collections import deque

    from bot.game.locations import DISTRICT_BY_CODE, DOWN, LEFT, RIGHT, UP

    shift = {UP: (0, -1), DOWN: (0, 1), LEFT: (-1, 0), RIGHT: (1, 0)}
    at = {"main_hub": (0, 0)}
    queue = deque(["main_hub"])
    while queue:
        code = queue.popleft()
        x, y = at[code]
        for side, other in DISTRICT_BY_CODE[code].around.items():
            spot = (x + shift[side][0], y + shift[side][1])
            if other in at:
                assert at[other] == spot, (
                    f"{code} — {side} → {other}: район уже стоит в {at[other]}, "
                    f"а эта дорога ведёт в {spot}"
                )
            else:
                at[other] = spot
                queue.append(other)
    return at


def test_the_city_is_a_grid_four_by_four():
    """Город — сетка, и по ней же считается дорога.

    Раньше связи писались от руки и расходились с рисунком: жилой
    квартал и кадетский городок стояли в одной клетке, справа от
    торгового квартала, — то есть один и тот же шаг вёл в два разных
    места.
    """
    from bot.game.locations import DISTRICTS

    at = city_grid()

    assert len(at) == len(DISTRICTS), "до какого-то района не дошли от центра"
    xs = {x for x, _ in at.values()}
    ys = {y for _, y in at.values()}
    assert (len(xs), len(ys)) == (4, 4)
    assert len(set(at.values())) == len(at), "два района в одной клетке"


def test_neighbours_on_the_grid_are_always_connected():
    """Соседние клетки связаны дорогой: дыр внутри сетки нет."""
    from bot.game.locations import DISTRICT_BY_CODE

    at = city_grid()
    where = {spot: code for code, spot in at.items()}
    for (x, y), code in where.items():
        for step in ((1, 0), (0, 1)):
            other = where.get((x + step[0], y + step[1]))
            if other is None:
                continue
            assert other in DISTRICT_BY_CODE[code].around.values(), (
                f"{code} и {other} стоят рядом, а дороги между ними нет"
            )


def test_the_far_corner_is_six_steps_away():
    """Дорога считается шагами по сетке, а не «свой район — чужой район».

    Из угла в угол сетки четыре на четыре — шесть шагов, и дорога стоит
    ровно столько же, сколько шагов, плюс дверь.
    """
    from bot.game.locations import STEP, district_hops, travel_seconds

    # Деловой квартал в левом верхнем углу, особняк мафии в правом нижнем
    assert district_hops("bank_market_post", "mafia_mansion") == 6
    assert travel_seconds("bank", "mafia_mansion") == 7 * STEP
    # Внутри одного района шагов нет вовсе — только дверь
    assert district_hops("main_hub", "main_hub") == 0
    assert travel_seconds("fight_club", "workshop") == STEP


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


def test_the_station_is_named_on_the_sign_but_not_in_the_bucket():
    """Вывеску переименовали, код — нет, и это нарочно.

    Файлы в хранилище названы `vcpd`: и карта района, и вид изнутри.
    Переименовать код вслед за вывеской значило бы разойтись с
    хранилищем — район открылся бы пустой картинкой, а заметил бы это
    игрок, а не тест.
    """
    from bot.game.locations import get_district, get_location

    station = get_location("vcpd")
    assert station.title == "Полицейский участок"
    assert station.whither == "полицейского участка"
    assert station.indoors.endswith("/interiors/vcpd_interior.jpeg")
    assert get_district(station.district).image.endswith(
        "/vcpd_hospital_district.png"
    )

    # То же самое и с академией: вывеску поменяли, код оставили
    academy = get_location("police_school")
    assert academy.title == "Полицейская академия"
    assert academy.indoors.endswith("/interiors/police_school_interior.jpeg")


def test_a_house_takes_the_picture_of_its_own_district():
    """Дом не считает адрес карты заново, а спрашивает у района.

    Формат у карт разный, и второе место, где адрес выводится, однажды
    разошлось бы с первым.
    """
    from bot.game.locations import DISTRICT_BY_CODE, LOCATIONS

    for place in LOCATIONS:
        assert place.image == DISTRICT_BY_CODE[place.district].image


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
    # Пять домов первой очереди и восемнадцать второй: город вырос
    # картинками раньше, чем правилами, и это нормально — лишь бы в
    # каждом было сказано, чего в нём ждать. Больница из этого списка
    # уже вышла: в ней лечат за кредиты
    assert {"bank", "market", "post_office", "stadium", "bar"} <= {
        place.code for place in coming
    }
    assert "hospital" not in {place.code for place in coming}
    assert len(coming) == 23
    for place in coming:
        assert place.soon, f"{place.code}: не сказано, что здесь будет"
        assert place.services == ()


# ---------- бланк разметки ----------


def test_the_blank_counts_pixels_the_way_the_manifest_does():
    """Пиксель с картинки и доля в коде — одно и то же число.

    Правки дверей приходят пикселями исходника и переводятся в доли
    скриптом. Ошибись он в размере картинки — все двери уехали бы разом
    и ровно настолько, чтобы это было незаметно на глаз.
    """
    from bot.game.locations import get_location
    from scripts.doors import HEIGHT, WIDTH, pixels, shares

    assert (WIDTH, HEIGHT) == (941, 1672), "размер исходника карт"
    door = get_location("clothes_shop").entrance
    # Контрольные пиксели этой двери записаны в docs/locations.md
    assert [pixels(corner) for corner in door] == [
        (411, 356), (599, 382), (596, 526), (419, 503),
    ]
    # И обратно: из тех же пикселей выходит та же дверь
    assert tuple(shares(*pixels(corner)) for corner in door) == door


def test_no_two_houses_share_one_door():
    """Одна дверь на два дома — след разметки по шаблону, а не по картинке.

    Вторая очередь города какое-то время стояла с дверьми, переписанными
    с магазина одежды и аптеки: палец в них попадал, а подсветка садилась
    мимо косяка. Теперь каждая дверь снята со своей картинки, и повтор
    означал бы, что чью-то разметку потеряли по дороге.
    """
    from bot.game.locations import DISTRICTS, LOCATIONS
    from scripts.doors import shared

    for place in LOCATIONS:
        assert shared(place, DISTRICTS) == "", place.code
    doors = {place.entrance for place in LOCATIONS}
    assert len(doors) == len(LOCATIONS)
