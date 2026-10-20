"""Машины: салон, служебные гаражи, место за рулём и дорога.

Порядок проверок — по тому, что дороже сломать:

1. **Права.** Без удостоверения за руль не садятся, что бы ни стояло в
   гараже. Купить машину это не мешает — ездить на ней мешает.
2. **Дорога.** Машина делит её на себя, и карта показывает ровно то
   число, которое боец и прождёт.
3. **Деньги.** Сутки, месяц и покупка считаются от одной цены: месяц
   не должен однажды оказаться дороже покупки.
4. **Гараж.** Мест столько, сколько машин, и выдают их по должности:
   патрульную — в участке, начальничью — начальнику, такси — водителю.
5. **Смена.** Таксист за рулём парковой машины катается по городу, а
   все прочие со смены не уходят.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.car_service import (
    CarError,
    buy,
    hand_over,
    mine,
    park,
    release_strangers,
    rent,
    sweep,
    take,
    wheel,
)
from bot.config import Config
from bot.database import Database
from bot.game.cars import (
    BUY_PRICE_DAYS,
    CARS,
    CHIEF_CAR,
    DAY,
    MONTH_DAYS,
    MONTH_PRICE_DAYS,
    PATROL_CAR,
    POLICE_PLACE,
    SALON_PLACE,
    SHOWCASE,
    TAXI_CAR,
    TAXI_PLACE,
    TAXI_SHIFT_PRICE,
    VEHICLES,
    get_car,
    road_seconds,
    times_of,
)
from bot.game.classes import Stats
from bot.game.driving import School
from bot.game.locations import STEP, travel_seconds
from bot.game.work import SHIFT_SECONDS
from bot.models import Player
from bot.travel_service import LockedError, Travel
from bot.webapp.cars import build_cars
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

NOW = 1_800_000_000
HATCH = "street_compact"
HYPER = "apex_hypercar"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


def make_player(
    user_id: int = 42, nickname: str = "Тайлер",
    credits: int = 100_000, where: str = SALON_PLACE,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=where, seen_at=NOW,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def licensed(
    db: Database, player: Player, when: int = NOW
) -> Player:
    """Боец с правами на руках: всё остальное про них проверяет автошкола."""
    await db.save_player(player)
    await db.set_school(player.user_id, School(passed_at=when, licence_at=when))
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


async def hired(
    db: Database, player: Player, job: str, where: str, shift: int = 0
) -> Player:
    """Боец на должности. Приёма тут не проверяем — это дело работы."""
    player.job_code = job
    player.job_since = NOW
    player.location = where
    player.travel_to = None
    player.arrives_at = 0
    player.shift_until = shift
    await db.save_player(player)
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


# ---------- витрина и числа ----------


def test_the_salon_sells_ten_and_the_garages_hold_the_rest():
    """Тринадцать машин: десять в салоне, три служебных по гаражам."""
    assert len(CARS) == 13
    assert len(SHOWCASE) == 10
    assert all(not one.service for one in SHOWCASE)
    service = [one for one in CARS if one.service]
    assert [one.code for one in service] == [PATROL_CAR, CHIEF_CAR, TAXI_CAR]
    # Мест в гаражах: две патрульные, одна начальничья, три такси
    assert [one.seats for one in service] == [2, 1, 3]
    # Картинки все из одной папки и все в png: их так и прислали
    for one in CARS:
        assert one.image.startswith(f"{VEHICLES}/") and one.image.endswith(".png")
        assert one.title and one.note


def test_the_speed_runs_from_two_to_ten():
    """Скорость — от двух раз до десяти, и дороже значит не медленнее."""
    times = [one.times for one in SHOWCASE]
    assert min(times) == 2 and max(times) == 10
    # По витрине скорость не падает: дороже — быстрее или столько же
    assert times == sorted(times)
    prices = [one.price for one in SHOWCASE]
    assert prices == sorted(prices) and len(set(prices)) == len(prices)


def test_the_month_is_twenty_days_and_the_car_is_five_months():
    """Три цены считаются от одной: месяц — двадцать суток, машина — пять месяцев."""
    for one in SHOWCASE:
        assert one.month == one.day * MONTH_PRICE_DAYS
        assert one.price == one.day * BUY_PRICE_DAYS
        assert one.price == one.month * 5
    # Служебные не продаются вовсе: у них нет цены покупки
    assert get_car(PATROL_CAR).price == 0
    assert get_car(CHIEF_CAR).price == 0
    # Такси стоит смену, а не сутки: цена у него одна
    assert get_car(TAXI_CAR).day == TAXI_SHIFT_PRICE


def test_the_road_is_divided_by_the_car():
    """Дорога делится на машину, но короче секунды не бывает."""
    assert road_seconds(40, 1) == 40
    assert road_seconds(40, 2) == 20
    assert road_seconds(40, 10) == 4
    # Одна дверь — десять секунд, и даже гиперкар не отменяет расстояние
    assert road_seconds(STEP, 10) == 1
    assert road_seconds(0, 10) == 0
    # Пешком — делить не на что
    assert times_of("") == 1 and times_of("нет такой") == 1
    assert times_of(HYPER) == 10


def test_the_car_shortens_the_road_after_the_limp():
    """Сперва травма удваивает дорогу, потом машина её делит."""
    whole = travel_seconds("fight_club", "pharmacy")
    assert travel_seconds("fight_club", "pharmacy", times=2) == whole // 2
    hurt = travel_seconds("fight_club", "pharmacy", limping=True)
    assert hurt == whole * 2
    assert travel_seconds(
        "fight_club", "pharmacy", limping=True, times=2
    ) == whole
    # На месте дорога нулевая, с машиной или без
    assert travel_seconds("bar", "bar", times=10) == 0


# ---------- права ----------


async def test_without_a_licence_the_wheel_stays_empty(db):
    """Машину купить можно без прав, ехать на ней — нельзя."""
    player = make_player()
    await db.save_player(player)

    await buy(db, player, HATCH, NOW)
    keys = await mine(db, player, NOW)
    assert [one["code"] for one in keys] == [HATCH]

    with pytest.raises(CarError, match="удостоверения"):
        await wheel(db, player, HATCH, NOW)
    assert player.driving(NOW) == ""


async def test_the_licence_opens_the_door_to_the_car(db):
    """С правами боец садится за руль, и дорога становится короче."""
    player = await licensed(db, make_player())
    await buy(db, player, HATCH, NOW)

    said = await wheel(db, player, HATCH, NOW)

    assert "Городской хэтчбек" in said
    assert player.driving(NOW) == HATCH
    assert player.car_times(NOW) == 2
    fresh = await db.get_player(player.user_id)
    assert fresh.car == HATCH and fresh.car_until == 0


# ---------- деньги ----------


async def test_the_bought_car_is_yours_for_good(db):
    """Покупка снимает цену и даёт машину без срока."""
    player = await licensed(db, make_player(credits=10_000))
    car = get_car(HATCH)

    said = await buy(db, player, HATCH, NOW)

    assert car.title in said
    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 10_000 - car.price
    keys = await mine(db, fresh, NOW)
    assert keys[0]["own"] is True and keys[0]["left"] == 0
    # Второй раз ту же машину не продают
    with pytest.raises(CarError, match="уже есть"):
        await buy(db, fresh, HATCH, NOW)
    # И не сдают: своя остаётся своей
    with pytest.raises(CarError, match="не сдают"):
        await hand_over(db, fresh, HATCH, NOW)


async def test_an_empty_pocket_buys_nothing(db):
    """Не хватает денег — машина остаётся в салоне."""
    player = await licensed(db, make_player(credits=100))

    with pytest.raises(CarError, match="Стоит"):
        await buy(db, player, HATCH, NOW)
    assert await mine(db, player, NOW) == []


async def test_the_rented_car_leaves_when_the_time_is_out(db):
    """Снятая машина своя до часа, а потом боец снова пешком."""
    player = await licensed(db, make_player())
    car = get_car(HATCH)

    said = await rent(db, player, HATCH, "day", NOW)

    assert "снята на сутки" in said
    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 100_000 - car.day
    await wheel(db, fresh, HATCH, NOW)
    assert fresh.driving(NOW) == HATCH
    # Сутки прошли: машины под бойцом нет, и сметать для этого ничего не надо
    assert fresh.driving(NOW + DAY) == ""
    assert fresh.car_times(NOW + DAY) == 1
    # А взгляд на свои ключи заодно убирает строку из гаража
    assert await mine(db, fresh, NOW + DAY) == []


async def test_the_rent_extends_from_its_own_end(db):
    """Снял дважды — сроки складываются, а не затирают друг друга."""
    player = await licensed(db, make_player())

    await rent(db, player, HATCH, "day", NOW)
    await rent(db, player, HATCH, "month", NOW)

    row = await db.car_row(player.user_id, HATCH)
    assert row["until"] == NOW + DAY + MONTH_DAYS * DAY
    # А купленная машина срока не получает ни при каком продлении
    await buy(db, await db.get_player(player.user_id), HATCH, NOW)
    assert (await db.car_row(player.user_id, HATCH))["until"] == 0


async def test_the_rented_car_can_be_handed_back_early(db):
    """Сдать аренду можно раньше срока — денег за остаток не вернут."""
    player = await licensed(db, make_player())
    await rent(db, player, HATCH, "day", NOW)
    await wheel(db, player, HATCH, NOW)

    said = await hand_over(db, player, HATCH, NOW + 60)

    assert "денег за остаток" in said.lower()
    assert await mine(db, player, NOW + 60) == []
    # И из машины боец вышел сам: ехать больше не на чем
    fresh = await db.get_player(player.user_id)
    assert fresh.driving(NOW + 60) == ""


async def test_the_salon_does_not_trade_service_cars(db):
    """Патрульную в салоне не купишь и не снимешь."""
    player = await licensed(db, make_player())

    with pytest.raises(CarError, match="в салоне нет"):
        await buy(db, player, PATROL_CAR, NOW)
    with pytest.raises(CarError, match="в салоне нет"):
        await rent(db, player, TAXI_CAR, "day", NOW)


# ---------- гараж участка ----------


async def test_the_patrol_car_is_free_and_there_are_two_of_them(db):
    """Патрульную выдают в участке бесплатно, и всего их две."""
    first = await hired(
        db, await licensed(db, make_player(1, "Патрульный")),
        "police_patrol", POLICE_PLACE,
    )
    second = await hired(
        db, await licensed(db, make_player(2, "Офицер")),
        "police_officer", POLICE_PLACE,
    )
    third = await hired(
        db, await licensed(db, make_player(3, "Второй патрульный")),
        "police_patrol", POLICE_PLACE,
    )

    assert "выдана" in await take(db, first, PATROL_CAR, NOW)
    await take(db, second, PATROL_CAR, NOW)
    # Денег не взяли ни с кого: служебная машина бесплатна
    assert (await db.get_player(1)).credits == 100_000
    with pytest.raises(CarError, match="в разъезде"):
        await take(db, third, PATROL_CAR, NOW)

    # Сдал — место освободилось
    await hand_over(db, first, PATROL_CAR, NOW)
    assert "выдана" in await take(db, third, PATROL_CAR, NOW)


async def test_the_chiefs_car_has_one_set_of_keys(db):
    """Машину начальника выдают начальнику, и она одна."""
    chief = await hired(
        db, await licensed(db, make_player(1, "Начальник")),
        "police_chief", POLICE_PLACE,
    )
    officer = await hired(
        db, await licensed(db, make_player(2, "Офицер")),
        "police_officer", POLICE_PLACE,
    )

    await take(db, chief, CHIEF_CAR, NOW)
    with pytest.raises(CarError, match="начальника полиции"):
        await take(db, officer, CHIEF_CAR, NOW)


async def test_the_garage_gives_nothing_to_a_stranger(db):
    """Гражданскому в служебном гараже не дают ничего."""
    plain = await licensed(db, make_player(where=POLICE_PLACE))

    with pytest.raises(CarError, match="сотрудникам участка"):
        await take(db, plain, PATROL_CAR, NOW)


async def test_the_service_car_goes_back_when_the_badge_is_gone(db):
    """Ушёл со службы — машина вернулась в гараж, и ждать этого не надо."""
    officer = await hired(
        db, await licensed(db, make_player(1, "Офицер")),
        "police_officer", POLICE_PLACE,
    )
    await take(db, officer, PATROL_CAR, NOW)
    await wheel(db, officer, PATROL_CAR, NOW)

    # Уволился: должность пуста, а машина всё ещё записана на него
    officer.job_code = ""
    await db.save_player(officer)
    freed = await release_strangers(db, POLICE_PLACE, NOW)

    assert freed == 1
    assert await db.car_holders(PATROL_CAR, NOW) == []
    fresh = await db.get_player(1)
    assert fresh.car == "" and fresh.driving(NOW) == ""


# ---------- гараж таксопарка ----------


async def test_the_taxi_is_taken_for_a_shift_and_paid_for(db):
    """Такси дают на смену и за деньги: двадцать кредитов за неё."""
    driver = await hired(
        db, await licensed(db, make_player(1, "Водитель", credits=500)),
        "taxi_driver", TAXI_PLACE, shift=NOW + SHIFT_SECONDS,
    )

    said = await take(db, driver, TAXI_CAR, NOW)

    assert "до конца смены" in said
    assert (await db.get_player(1)).credits == 500 - TAXI_SHIFT_PRICE
    row = await db.car_row(1, TAXI_CAR)
    assert row["until"] == NOW + SHIFT_SECONDS
    # Смена кончилась — машина в парке, и возвращать её вручную не надо
    assert await mine(db, driver, NOW + SHIFT_SECONDS + 1) == []


async def test_the_taxi_is_not_given_off_shift(db):
    """Машину парка дают на смену: без смены она остаётся в гараже."""
    driver = await hired(
        db, await licensed(db, make_player(1, "Водитель")),
        "taxi_driver", TAXI_PLACE,
    )

    with pytest.raises(CarError, match="смену"):
        await take(db, driver, TAXI_CAR, NOW)


async def test_the_depot_holds_three_cars(db):
    """Три машины на парк: четвёртому водителю не достанется."""
    drivers = []
    for number in range(1, 5):
        drivers.append(await hired(
            db, await licensed(db, make_player(number, f"Водитель-{number}")),
            "taxi_driver", TAXI_PLACE, shift=NOW + SHIFT_SECONDS,
        ))
    for one in drivers[:3]:
        await take(db, one, TAXI_CAR, NOW)

    with pytest.raises(CarError, match="в разъезде"):
        await take(db, drivers[3], TAXI_CAR, NOW)


# ---------- смена и дорога ----------


async def test_the_taxi_driver_rides_the_city_while_others_stand(db):
    """Единственная смена, с которой уходят: таксист за рулём парковой машины."""
    road = Travel(db)
    driver = await hired(
        db, await licensed(db, make_player(1, "Водитель")),
        "taxi_driver", TAXI_PLACE, shift=NOW + SHIFT_SECONDS,
    )

    # Пешком со смены не уходят, даже таксисту
    with pytest.raises(LockedError, match="смене"):
        await road.go(driver, "bar", NOW)

    await take(db, driver, TAXI_CAR, NOW)
    await wheel(db, driver, TAXI_CAR, NOW)
    await road.go(driver, "bar", NOW)

    assert driver.travel_to == "bar"
    # И дорога у него впятеро короче пешей
    walk = travel_seconds(TAXI_PLACE, "bar")
    assert driver.arrives_at - NOW == walk // 5


async def test_a_car_does_not_let_other_jobs_off_the_shift(db):
    """Своя машина со смены не отпускает: в разъезд пускает парк, а не кошелёк."""
    road = Travel(db)
    barman = await hired(
        db, await licensed(db, make_player(1, "Бармен")),
        "barman", "bar", shift=NOW + SHIFT_SECONDS,
    )
    await buy(db, barman, HYPER, NOW)
    await wheel(db, barman, HYPER, NOW)

    with pytest.raises(LockedError, match="смене"):
        await road.go(barman, "casino", NOW)


async def test_the_road_on_wheels_is_shorter_for_everyone(db):
    """Вне смены машина просто везёт: та же дорога, делённая на себя."""
    road = Travel(db)
    player = await licensed(db, make_player(where="fight_club"))
    await buy(db, player, HYPER, NOW)
    await wheel(db, player, HYPER, NOW)

    await road.go(player, "pharmacy", NOW)

    walk = travel_seconds("fight_club", "pharmacy")
    assert player.arrives_at - NOW == walk // 10 == 2


# ---------- экраны ----------


async def test_the_salon_shows_the_showcase_and_the_garage_the_seats(db):
    """Экран один, а домов три: в салоне цены, в гараже места."""
    player = await licensed(db, make_player())

    salon = await build_cars(db, player, NOW)
    assert salon["garage"] is False
    assert len(salon["shelf"]) == 10
    first = salon["shelf"][0]
    assert first["day"] and first["month"] and first["price"]
    assert first["can_buy"] is True

    officer = await hired(
        db, await licensed(db, make_player(2, "Офицер")),
        "police_officer", POLICE_PLACE,
    )
    station = await build_cars(db, officer, NOW)
    assert station["garage"] is True
    assert [one["code"] for one in station["shelf"]] == [PATROL_CAR, CHIEF_CAR]
    assert station["shelf"][0]["can_take"] is True
    # Начальничью офицеру не дают, и экран говорит, почему
    assert station["shelf"][1]["can_take"] is False
    assert "начальника" in station["shelf"][1]["why"]


async def test_the_screen_warns_about_the_missing_licence(db):
    """Без прав экран говорит об этом первым делом."""
    player = make_player()
    await db.save_player(player)

    body = await build_cars(db, player, NOW)

    assert body["has_licence"] is False
    assert "удостоверения" in body["note"]


async def test_the_card_carries_the_car_under_the_fighter(db, client):
    """Машина уезжает в карточку: её рисуют поверх образа, снизу."""
    player = await licensed(db, make_player())
    await buy(db, player, HATCH, NOW)
    await wheel(db, player, HATCH, NOW)

    card = await (await client.get("/api/card", headers=headers())).json()

    assert card["car"]["code"] == HATCH
    assert card["car"]["image"].endswith(".png")
    assert card["car"]["times"] == 2 and card["car"]["own"] is True


async def test_the_wheel_is_taken_from_the_card(db, client):
    """За руль садятся и выходят ручкой ключей, а не ручкой дома."""
    player = await licensed(db, make_player())
    await buy(db, player, HATCH, NOW)

    keys = await (await client.get("/api/keys", headers=headers())).json()
    assert [one["code"] for one in keys["keys"]] == [HATCH]

    answer = await client.post(
        "/api/keys", json={"action": "wheel", "code": HATCH},
        headers=headers(),
    )
    assert answer.status == 200
    body = await answer.json()
    assert body["card"]["car"]["code"] == HATCH
    assert body["keys"][0]["driving"] is True

    answer = await client.post(
        "/api/keys", json={"action": "park"}, headers=headers()
    )
    assert (await answer.json())["card"]["car"] is None


async def test_the_salon_is_bought_from_the_salon(db, client):
    """Машину берут в салоне: из бара витрина не открывается."""
    await licensed(db, make_player())

    body = await (await client.get("/api/cars", headers=headers())).json()
    assert body["title"] == "Автосалон"

    answer = await client.post(
        "/api/cars", json={"action": "buy", "code": HATCH}, headers=headers()
    )
    assert answer.status == 200
    assert "твоя" in (await answer.json())["said"]

    away = await db.get_player(42)
    away.location = "bar"
    await db.save_player(away)
    assert (await client.get("/api/cars", headers=headers())).status == 409


async def test_the_sweep_takes_only_the_expired(db):
    """Метла убирает кончившиеся аренды и не трогает своё."""
    player = await licensed(db, make_player())
    await buy(db, player, HATCH, NOW)
    await rent(db, player, HYPER, "day", NOW)

    assert await sweep(db, NOW + 60) == 0
    assert await sweep(db, NOW + DAY) == 1
    assert [one["code"] for one in await mine(db, player, NOW + DAY)] == [HATCH]


async def test_parking_an_empty_seat_says_so(db):
    """Снять нечего — так и говорим, а не делаем вид, что сняли."""
    player = await licensed(db, make_player())

    assert "пешком" in await park(db, player, NOW)


async def test_the_service_car_leaves_with_the_job(db):
    """Уволился сам — ключи от служебной ушли вместе с должностью."""
    from bot.work_service import quit_job

    officer = await hired(
        db, await licensed(db, make_player(1, "Офицер")),
        "police_officer", POLICE_PLACE,
    )
    await take(db, officer, PATROL_CAR, NOW)
    await wheel(db, officer, PATROL_CAR, NOW)

    await quit_job(db, officer, NOW)

    assert officer.car == "" and officer.driving(NOW) == ""
    assert await db.car_holders(PATROL_CAR, NOW) == []
    # А своя машина при увольнении остаётся своей
    await buy(db, officer, HATCH, NOW)
    assert [one["code"] for one in await mine(db, officer, NOW)] == [HATCH]


# ---------- парковки: особняк и своя квартира ----------


async def test_the_mansion_parking_holds_three_and_keeps_the_owner(db):
    """Парковка особняка: три места, и машина на них остаётся хозяйской."""
    from bot.car_service import leave, parked_at, pick_up
    from bot.clan_service import found, _join
    from bot.game.cars import CLAN_PARKING, IN_CLAN

    don = await licensed(db, make_player(1, "Дон", where="mafia_mansion"))
    clan = await found(db, don, "Коза Ностра", "black_ravens", now=NOW)
    mate = await licensed(db, make_player(2, "Боец", where="mafia_mansion"))
    await _join(db, mate, clan, NOW)
    don = await db.get_player(1)
    mate = await db.get_player(2)
    await buy(db, don, HATCH, NOW)
    await buy(db, mate, HYPER, NOW)

    said = await leave(db, don, HATCH, IN_CLAN, clan.id, NOW)

    assert "стоит на парковке" in said
    rows = await parked_at(db, IN_CLAN, clan.id, mate, NOW)
    assert len(rows) == 1
    # Своя семья машину видит, но хозяин у неё прежний
    assert rows[0]["owner"] == "Дон" and rows[0]["mine"] is False
    assert (await parked_at(db, IN_CLAN, clan.id, don, NOW))[0]["mine"] is True
    # Ключи при хозяине: в них видно, что машина стоит
    keys = await mine(db, don, NOW)
    assert keys[0]["parked"] == IN_CLAN
    assert keys[0]["parked_text"] == "парковка особняка"

    # Второй раз ту же машину не поставить
    with pytest.raises(CarError, match="и так стоит"):
        await leave(db, don, HATCH, IN_CLAN, clan.id, NOW)
    # А забрать — только хозяину, и после этого она снова при нём
    assert "снова при тебе" in await pick_up(db, don, HATCH, NOW)
    assert await parked_at(db, IN_CLAN, clan.id, don, NOW) == []
    assert CLAN_PARKING == 3


async def test_the_mansion_parking_runs_out_of_places(db):
    """Мест три: четвёртую машину в особняк не поставить."""
    from bot.car_service import leave
    from bot.clan_service import found
    from bot.game.cars import CLAN_PARKING, IN_CLAN

    don = await licensed(db, make_player(1, "Дон", where="mafia_mansion"))
    clan = await found(db, don, "Коза Ностра", "black_ravens", now=NOW)
    don = await db.get_player(1)
    codes = [one.code for one in SHOWCASE[:CLAN_PARKING + 1]]
    for code in codes:
        await buy(db, don, code, NOW)

    for code in codes[:CLAN_PARKING]:
        await leave(db, don, code, IN_CLAN, clan.id, NOW)

    with pytest.raises(CarError, match="Мест нет"):
        await leave(db, don, codes[CLAN_PARKING], IN_CLAN, clan.id, NOW)


async def test_a_parked_car_is_not_driven(db):
    """За руль с парковки не садятся: сперва забери машину."""
    from bot.car_service import leave, pick_up
    from bot.clan_service import found
    from bot.game.cars import IN_CLAN

    don = await licensed(db, make_player(1, "Дон", where="mafia_mansion"))
    clan = await found(db, don, "Коза Ностра", "black_ravens", now=NOW)
    don = await db.get_player(1)
    await buy(db, don, HATCH, NOW)
    await wheel(db, don, HATCH, NOW)

    # Ставят машину вместе с выходом из неё: за рулём её не оставить
    await leave(db, don, HATCH, IN_CLAN, clan.id, NOW)

    assert don.driving(NOW) == ""
    with pytest.raises(CarError, match="стоит на парковке"):
        await wheel(db, don, HATCH, NOW)
    await pick_up(db, don, HATCH, NOW)
    assert "под тобой" in await wheel(db, don, HATCH, NOW)


async def test_the_service_car_is_not_parked_in_the_mansion(db):
    """Служебная ночует в гараже службы: на чужую парковку её не ставят."""
    from bot.car_service import leave
    from bot.game.cars import IN_CLAN

    officer = await hired(
        db, await licensed(db, make_player(1, "Офицер")),
        "police_officer", POLICE_PLACE,
    )
    await take(db, officer, PATROL_CAR, NOW)

    # Служивого в семью и не возьмут, но отказ тут не про семью: на
    # парковку особняка служебную машину не поставить в любом случае
    with pytest.raises(CarError, match="гараже службы"):
        await leave(db, officer, PATROL_CAR, IN_CLAN, 1, NOW)


async def test_leaving_the_family_sends_the_car_home(db):
    """Ушёл из семьи — машина уехала с парковки к хозяину."""
    from bot.car_service import leave, parked_at
    from bot.clan_service import found, _join, leave as quit_clan
    from bot.game.cars import IN_CLAN

    don = await licensed(db, make_player(1, "Дон", where="mafia_mansion"))
    clan = await found(db, don, "Коза Ностра", "black_ravens", now=NOW)
    mate = await licensed(db, make_player(2, "Боец", where="mafia_mansion"))
    await _join(db, mate, clan, NOW)
    mate = await db.get_player(2)
    await buy(db, mate, HATCH, NOW)
    await leave(db, mate, HATCH, IN_CLAN, clan.id, NOW)

    await quit_clan(db, mate)

    assert await parked_at(db, IN_CLAN, clan.id, mate, NOW) == []
    keys = await mine(db, await db.get_player(2), NOW)
    assert keys[0]["parked"] == "", "машина должна вернуться к хозяину"


async def test_the_flat_has_one_place_for_a_car(db):
    """У квартиры одно место под машину, и оно своё."""
    from bot.car_service import leave, parked_at, pick_up
    from bot.estate_service import rent as rent_flat
    from bot.game.cars import FLAT_PARKING, IN_FLAT

    house = "residential_apartment"
    tenant = await licensed(db, make_player(1, "Жилец", where=house))
    await rent_flat(db, tenant, 1, now=NOW)
    lease = await db.lease_of(1)
    await buy(db, tenant, HATCH, NOW)
    await buy(db, tenant, HYPER, NOW)

    said = await leave(db, tenant, HATCH, IN_FLAT, lease.id, NOW)

    assert "стоит на парковке" in said
    rows = await parked_at(db, IN_FLAT, lease.id, tenant, NOW)
    assert len(rows) == 1 and rows[0]["mine"] is True
    assert FLAT_PARKING == 1
    # Место одно: вторая машина во двор не встанет
    with pytest.raises(CarError, match="Мест нет"):
        await leave(db, tenant, HYPER, IN_FLAT, lease.id, NOW)
    await pick_up(db, tenant, HATCH, NOW)
    await leave(db, tenant, HYPER, IN_FLAT, lease.id, NOW)
    assert (await parked_at(db, IN_FLAT, lease.id, tenant, NOW))[0]["code"] == HYPER


async def test_the_evicted_car_drives_home(db):
    """Квартиру освободили — машина уехала к хозяину, как и вещи."""
    from bot.car_service import leave, parked_at
    from bot.estate_service import rent as rent_flat
    from bot.game.cars import IN_FLAT

    house = "residential_apartment"
    tenant = await licensed(db, make_player(1, "Жилец", where=house))
    await rent_flat(db, tenant, 1, now=NOW)
    lease = await db.lease_of(1)
    await buy(db, tenant, HATCH, NOW)
    await leave(db, tenant, HATCH, IN_FLAT, lease.id, NOW)

    await db.release_flat(lease.id)

    assert await parked_at(db, IN_FLAT, lease.id, tenant, NOW) == []
    keys = await mine(db, await db.get_player(1), NOW)
    assert [one["parked"] for one in keys] == [""]


async def test_the_mansion_parks_a_car_from_the_estate_tab(db, client):
    """Машину ставят на «Имуществе» семьи, и оттуда же забирают."""
    from bot.clan_service import found

    don = await licensed(db, make_player(42, "Дон", where="mafia_mansion"))
    clan = await found(db, don, "Коза Ностра", "black_ravens", now=NOW)
    await buy(db, await db.get_player(42), HATCH, NOW)

    body = await (await client.get(
        f"/api/mafia?clan={clan.id}", headers=headers()
    )).json()
    garage = body["opened"]["estate"]["garage"]
    assert garage["free"] == 3 and garage["cars"] == []
    assert [one["code"] for one in garage["keys"]] == [HATCH]

    answer = await client.post(
        "/api/mafia",
        json={"action": "park", "car": HATCH, "open": clan.id},
        headers=headers(),
    )
    assert answer.status == 200
    body = await answer.json()
    garage = body["opened"]["estate"]["garage"]
    assert "стоит на парковке" in body["said"]
    assert [one["code"] for one in garage["cars"]] == [HATCH]
    assert garage["free"] == 2 and garage["keys"] == []
    assert garage["cars"][0]["mine"] is True

    answer = await client.post(
        "/api/mafia",
        json={"action": "unpark", "car": HATCH, "open": clan.id},
        headers=headers(),
    )
    assert answer.status == 200
    body = await answer.json()
    assert body["opened"]["estate"]["garage"]["cars"] == []


async def test_the_flat_parks_a_car_from_its_own_screen(db, client):
    """Место при квартире: ставят и забирают на экране своего дома."""
    from bot.estate_service import rent as rent_flat

    house = "residential_apartment"
    tenant = await licensed(db, make_player(42, "Жилец", where=house))
    await rent_flat(db, tenant, 1, now=NOW)
    await buy(db, await db.get_player(42), HATCH, NOW)

    body = await (await client.get("/api/flats", headers=headers())).json()
    assert body["parking"]["mine"] is True
    assert body["parking"]["slots"] == 1 and body["parking"]["free"] == 1
    assert [one["code"] for one in body["parking"]["keys"]] == [HATCH]

    answer = await client.post(
        "/api/flats", json={"action": "park", "car": HATCH}, headers=headers()
    )
    assert answer.status == 200
    body = await answer.json()
    assert [one["code"] for one in body["parking"]["cars"]] == [HATCH]
    assert body["parking"]["free"] == 0

    answer = await client.post(
        "/api/flats", json={"action": "unpark", "car": HATCH}, headers=headers()
    )
    assert answer.status == 200
    assert (await answer.json())["parking"]["cars"] == []


async def test_a_car_is_not_parked_at_a_strange_door(db, client):
    """Место под машину — у своей квартиры, а не у любой."""
    from bot.estate_service import rent as rent_flat

    house = "residential_apartment"
    tenant = await licensed(db, make_player(42, "Жилец", where=house))
    await rent_flat(db, tenant, 1, now=NOW)
    await buy(db, await db.get_player(42), HATCH, NOW)
    # Ушёл в другой дом: своё место осталось в своём
    away = await db.get_player(42)
    away.location = "residential_apartment_2"
    await db.save_player(away)

    answer = await client.post(
        "/api/flats", json={"action": "park", "car": HATCH}, headers=headers()
    )

    assert answer.status == 409
    assert "своей квартиры" in (await answer.json())["error"]
    body = await (await client.get("/api/flats", headers=headers())).json()
    assert body["parking"]["mine"] is False
