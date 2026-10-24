"""Заряд машины и АЗС: три колонки на весь город.

Машина в городе ездит не вечно, и это первое её свойство после
скорости. Проверяем то, из чего правило состоит:

1. **Запас хода тратит расстояние, а не время за рулём.** Гиперкар
   проезжает те же четыре километра в десять раз быстрее хэтчбека — и
   тратит на них столько же. Иначе быстрая машина была бы ещё и
   выгоднее, и медленных в салоне держать было бы незачем.
2. **Километр — десять секунд пешком.** Ровно шаг города: второй меры
   длины в нём нет.
3. **Пустая машина не едет.** Заряд кончился — боец вышел прямо на
   дороге и дальше идёт пешком, а сесть обратно не может.
4. **Колонка наливает проценты за кредиты и за время.** Три мерки,
   цена и срок растут ровно с объёмом, а сверх полного бака заряд
   пропадает — и об этом говорят до оплаты.
5. **Колонок три на весь город.** Очередь к зарядке — часть игры: пока
   машина стоит в колонке, она при хозяине только по бумагам.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.car_service import CarError, buy, leave, mine, wheel
from bot.config import Config
from bot.charge_service import ChargeError, collect, plug, plugged, slots
from bot.game.cars import IN_CLAN, get_car
from bot.game.charge import (
    BASE_RANGE,
    CHARGE_SLOTS,
    CHARGE_STEPS,
    KM_SECONDS,
    after_charge,
    after_drive,
    charge_price,
    charge_seconds,
    empty,
    km_for,
    km_left,
    percent_left,
    range_km,
    wasted,
)
from bot.game.bank import CARD_PRICE, CASH, card_works, discount_of
from bot.game.locations import STEP, Service, get_location, travel_seconds
from bot.travel_service import Travel
from bot.webapp.charging import build_charge
from bot.webapp.server import create_app
from tests.test_cars import HATCH, HYPER, NOW, licensed, make_player
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

STATION = "gas_station"
SUV = "armored_suv"  # ровно 400 км запаса — машина из примера правил


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


async def driver(
    db, where: str = STATION, credits: int = 100_000, code=SUV,
    user_id: int = 42, name: str = "Тайлер",
):
    """Боец с правами, деньгами и своей машиной — там, где надо."""
    player = await licensed(
        db, make_player(user_id, name, where=where, credits=credits)
    )
    await db.put_car(player.user_id, code, 0, NOW)
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


async def drained(db, player, percent: int, code=SUV):
    """Спустить заряд машины до нужного процента."""
    car = get_car(code)
    await db.set_driven(
        player.user_id, code, range_km(car) - range_km(car) * percent // 100
    )
    return await db.get_player(player.user_id)


# ---------- дом и услуга ----------


def test_the_station_charges_cars_and_says_so():
    """АЗС перестала быть обещанием: в ней заряжают машины."""
    place = get_location(STATION)

    assert place.allows(Service.CHARGE)
    assert place.works and not place.soon, "дом обжит — обещать нечего"
    assert Service.CHARGE.title == "заряжать машину"


# ---------- запас хода ----------


def test_a_kilometre_is_one_city_step():
    """Километр — десять секунд пешком, то есть шаг города.

    Второй меры длины в городе нет: дорогу он считает шагами, и
    километр взят из того же шага.
    """
    assert KM_SECONDS == STEP


def test_the_range_grows_with_the_class():
    """Чем быстрее машина, тем дальше едет. Формула одна на всех."""
    hatch = get_car(HATCH)
    hyper = get_car(HYPER)

    assert range_km(hatch) == BASE_RANGE + 40 * hatch.times == 200
    assert range_km(hyper) == 520
    assert range_km(get_car(SUV)) == 400, "машина из примера правил"


def test_the_charge_is_spent_by_distance_not_by_time():
    """Сорок секунд пешего пути — четыре километра всякой машине.

    Гиперкар проезжает их за четыре секунды, хэтчбек за двадцать, а
    тратит каждый по четыре километра: иначе быстрая машина была бы
    ещё и экономнее.
    """
    assert km_for(40) == 4
    assert km_for(0) == 0
    # Шаг на месте расстоянием не считается, а полшага считается целым:
    # меньше километра колонка всё равно не мерит
    assert km_for(5) == 1


def test_the_example_from_the_rules_adds_up():
    """Пример из правил: 400 км, 40 секунд пешком — остаётся 99%."""
    car = get_car(SUV)

    driven = after_drive(car, 0, km_for(40))

    assert driven == 4
    assert km_left(car, driven) == 396
    assert percent_left(car, driven) == 99


def test_an_empty_car_stays_empty():
    """Пробег выше запаса не растёт, а заряд ниже нуля не падает."""
    car = get_car(SUV)

    assert after_drive(car, 399, 100) == 400, "сверх запаса не проедешь"
    assert percent_left(car, 400) == 0
    assert empty(car, 400) and empty(car, 10_000)
    assert not empty(car, 399)


# ---------- колонка ----------


def test_the_three_measures_cost_what_they_promise():
    """Три мерки: 50/75/100 кредитов и 10/15/20 минут."""
    assert CHARGE_STEPS == (50, 75, 100)
    assert [charge_price(one) for one in CHARGE_STEPS] == [50, 75, 100]
    assert [charge_seconds(one) // 60 for one in CHARGE_STEPS] == [10, 15, 20]


def test_the_charge_above_the_full_tank_is_lost():
    """Лишний заряд пропадает, и сколько его — сказано заранее."""
    car = get_car(SUV)
    # Бак на 70%: не хватает тридцати
    driven = 120

    assert percent_left(car, driven) == 70
    assert wasted(car, driven, 50) == 20
    assert wasted(car, driven, 30) == 0
    assert after_charge(car, driven, 50) == 0, "выше полного не наливают"
    assert percent_left(car, after_charge(car, driven, 30)) == 100


# ---------- дорога тратит заряд ----------


async def test_a_new_car_comes_fully_charged(db):
    """Из салона машина выезжает полной: сто процентов и весь запас."""
    player = await licensed(db, make_player())
    await buy(db, player, HATCH, NOW)

    keys = await mine(db, player, NOW)

    assert keys[0]["charge"] == 100
    assert keys[0]["km_left"] == keys[0]["range"] == 200
    assert keys[0]["empty"] is False


async def test_the_road_spends_the_charge_of_the_car_under_you(db):
    """Поехал — пробег вырос ровно на километры пешей дороги."""
    road = Travel(db)
    player = await driver(db, where="fight_club")
    await wheel(db, player, SUV, NOW)

    await road.go(player, "military_base", NOW)

    walk = travel_seconds("fight_club", "military_base")
    assert walk == 40, "дорога из примера правил"
    keys = await mine(db, player, NOW)
    assert keys[0]["km_left"] == 396
    assert keys[0]["charge"] == 99
    # А сама дорога короче в семь раз: заряд тратит расстояние, время
    # по-прежнему делится на машину
    assert player.arrives_at - NOW == walk // 7


async def test_walking_spends_nothing(db):
    """Пешком заряд не тратится: он тратится машиной, а не бойцом."""
    road = Travel(db)
    player = await driver(db, where="fight_club")

    await road.go(player, "military_base", NOW)

    keys = await mine(db, player, NOW)
    assert keys[0]["charge"] == 100


async def test_the_car_stops_when_the_charge_runs_out(db):
    """Заряд кончился — боец вышел на дороге и пошёл пешком.

    Доехать он доедет: выезжал заряженным, и дорога ему уже посчитана
    по машине. А на том конце машины под ним нет.
    """
    road = Travel(db)
    player = await driver(db, where="fight_club")
    player = await drained(db, player, 1)
    await wheel(db, player, SUV, NOW)

    await road.go(player, "military_base", NOW)

    assert player.driving(NOW) == "", "боец вышел"
    keys = await mine(db, player, NOW)
    assert keys[0]["charge"] == 0
    assert keys[0]["empty"] is True


async def test_an_empty_car_does_not_let_you_in(db):
    """За руль пустой машины не садятся: сперва колонка."""
    player = await driver(db, where="fight_club")
    player = await drained(db, player, 0)

    with pytest.raises(CarError, match="без заряда"):
        await wheel(db, player, SUV, NOW)


# ---------- три колонки ----------


async def test_the_station_has_three_free_columns(db):
    """Колонок три, и поначалу все свободны."""
    player = await driver(db)

    columns = await slots(db, player, NOW)

    assert len(columns) == CHARGE_SLOTS == 3
    assert [one["slot"] for one in columns] == [1, 2, 3]
    assert not any(one["busy"] for one in columns)


async def test_plugging_in_pays_and_starts_the_clock(db):
    """Поставил, заплатил, пошёл таймер. Машина при тебе, но не твоя."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)

    said = await plug(db, player, SUV, 2, 50, NOW)

    assert "2-й колонке" in said and "+50%" in said
    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 950, "цена — процент за кредит"
    columns = await slots(db, fresh, NOW)
    busy = columns[1]
    assert busy["busy"] and busy["mine"] and busy["percent"] == 50
    assert busy["left"] == charge_seconds(50) == 600
    assert busy["ready"] is False
    assert await plugged(db, fresh.user_id) == {SUV: 2}


async def test_a_charging_car_is_out_of_reach(db):
    """Пока машина в колонке, ею не распоряжаются: ключи у колонки."""
    from bot.used_cars_service import UsedCarError, sell_car

    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)
    await plug(db, player, SUV, 1, 50, NOW)
    fresh = await db.get_player(player.user_id)

    with pytest.raises(CarError, match="на колонке"):
        await wheel(db, fresh, SUV, NOW)
    with pytest.raises(CarError, match="на колонке"):
        await leave(db, fresh, SUV, IN_CLAN, 1, NOW)
    with pytest.raises(UsedCarError, match="на колонке"):
        await sell_car(db, fresh, SUV, get_car(SUV).price, NOW)


async def test_the_driver_steps_out_when_the_car_goes_on_the_column(db):
    """Поставил свою под собой — вышел из неё сам."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)
    await wheel(db, player, SUV, NOW)

    await plug(db, player, SUV, 1, 50, NOW)

    fresh = await db.get_player(player.user_id)
    assert fresh.driving(NOW) == ""


async def test_the_column_is_taken_by_the_first_one(db):
    """Колонка общая на город: занял — значит занял."""
    one = await driver(db, credits=1000)
    one = await drained(db, one, 20)
    two = await driver(
        db, credits=1000, code=HATCH, user_id=7, name="Марла"
    )
    two = await drained(db, two, 20, HATCH)
    await plug(db, one, SUV, 1, 50, NOW)

    with pytest.raises(ChargeError, match="заняли раньше"):
        await plug(db, two, HATCH, 1, 50, NOW)

    # Свободная рядом — берут её
    await plug(db, two, HATCH, 2, 50, NOW)
    assert await plugged(db, two.user_id) == {HATCH: 2}


async def test_there_is_no_fourth_column(db):
    """Колонок три, и четвёртой у станции нет."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)

    with pytest.raises(ChargeError, match="Колонок тут 3"):
        await plug(db, player, SUV, 4, 50, NOW)


async def test_the_column_pours_by_its_own_measures(db):
    """На глаз колонка не наливает: мерки у неё свои."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)

    with pytest.raises(ChargeError, match="по своим меркам"):
        await plug(db, player, SUV, 1, 37, NOW)


async def test_a_full_car_is_not_charged(db):
    """Полную машину не заряжают: наливать некуда."""
    player = await driver(db, credits=1000)

    with pytest.raises(ChargeError, match="заряжена полностью"):
        await plug(db, player, SUV, 1, 50, NOW)


async def test_the_column_wants_money_up_front(db):
    """Платят вперёд: нет кредитов — нет зарядки."""
    player = await driver(db, credits=10)
    player = await drained(db, player, 20)

    with pytest.raises(ChargeError, match="Зарядка стоит"):
        await plug(db, player, SUV, 1, 50, NOW)
    assert not any(one["busy"] for one in await slots(db, player, NOW))


# ---------- чем платят ----------


async def carded(db, player, money: int = 500, now: int = NOW):
    """Боец с картой и деньгами на счету. По умолчанию платит картой."""
    from tests.test_bank import with_card

    player = await with_card(db, player, now)
    player.account_balance = money
    await db.save_player(player)
    fresh = await db.get_player(player.user_id)
    assert fresh is not None and fresh.card_works(now)
    return fresh


async def test_the_card_takes_fifteen_percent_off_the_charge(db):
    """Картой зарядка дешевле на 15%: 42 кредита вместо пятидесяти."""
    assert discount_of(Service.CHARGE) == 15
    assert card_works(Service.CHARGE), "карта на АЗС ходит"

    player = await carded(db, await driver(db, credits=1000))
    player = await drained(db, player, 20)

    said = await plug(db, player, SUV, 1, 50, NOW)

    assert "42 💰" in said
    fresh = await db.get_player(player.user_id)
    assert fresh.account_balance == 500 - 42, "списали со счёта"
    assert fresh.credits == 1000 - CARD_PRICE, "наличные не тронули"
    # И в выписке видно, где боец оставил деньги и за что
    moves = await db.card_log(player.user_id)
    assert moves[0].note == "АЗС" and moves[0].amount == -42
    assert get_car(SUV).title in moves[0].about


async def test_cash_pays_the_full_price(db):
    """Наличными — полная цена: скидку даёт карта, а не колонка."""
    player = await carded(db, await driver(db, credits=1000))
    player = await drained(db, player, 20)
    player.pay_from = CASH
    await db.save_player(player)
    player = await db.get_player(player.user_id)

    said = await plug(db, player, SUV, 1, 50, NOW)

    assert "50 💰" in said
    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 1000 - CARD_PRICE - 50
    assert fresh.account_balance == 500, "счёт не тронули"


async def test_the_screen_counts_the_measures_by_the_chosen_purse(db):
    """Мерки считаются тем кошельком, которым боец здесь платит."""
    player = await carded(db, await driver(db, credits=1000))
    player = await drained(db, player, 20)

    body = await build_charge(db, player, NOW)

    assert body["purse"]["purse"] == "card"
    assert body["purse"]["discount"] == 15
    assert body["purse"]["now_off"] == 15
    assert body["purse"]["takes_card"] is True
    car = body["cars"][0]
    assert [one["price"] for one in car["steps"]] == [42, 63, 85]
    assert [one["price"] for one in body["steps"]] == [42, 63, 85]
    assert "+50% за 42 💰" in body["note"]

    # Переключился на наличные — цены вернулись к полным
    player.pay_from = CASH
    await db.save_player(player)
    cash = await build_charge(db, await db.get_player(player.user_id), NOW)
    assert cash["purse"]["purse"] == "cash" and cash["purse"]["now_off"] == 0
    assert [one["price"] for one in cash["steps"]] == [50, 75, 100]


async def test_a_card_without_money_does_not_charge_the_car(db):
    """На счету пусто — колонка отказывает и называет тот кошелёк."""
    player = await carded(db, await driver(db, credits=1000), money=10)
    player = await drained(db, player, 20)

    with pytest.raises(ChargeError, match="на счету"):
        await plug(db, player, SUV, 1, 50, NOW)

    assert not any(one["busy"] for one in await slots(db, player, NOW))


# ---------- забрать машину ----------


async def test_the_car_is_not_given_back_early(db):
    """Раньше срока машину с колонки не отдают."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)
    await plug(db, player, SUV, 1, 50, NOW)
    fresh = await db.get_player(player.user_id)

    with pytest.raises(ChargeError, match="Ещё заряжается"):
        await collect(db, fresh, 1, NOW + 599)


async def test_the_charged_car_comes_back_with_the_percent_paid_for(db):
    """Забрал — заряд прибавился ровно на оплаченные проценты."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)
    await plug(db, player, SUV, 1, 50, NOW)
    fresh = await db.get_player(player.user_id)

    said = await collect(db, fresh, 1, NOW + 600)

    assert "70%" in said
    keys = await mine(db, fresh, NOW + 600)
    assert keys[0]["charge"] == 70
    assert keys[0]["charging"] is False
    # И колонка снова свободна
    assert not any(one["busy"] for one in await slots(db, fresh, NOW + 600))
    assert await plugged(db, fresh.user_id) == {}
    # Заряженная машина едет
    await wheel(db, fresh, SUV, NOW + 600)
    assert fresh.driving(NOW + 600) == SUV


async def test_the_spill_is_paid_for_and_lost(db):
    """Заплатил за сто, а влезло тридцать — остальное пропало."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 70)
    await plug(db, player, SUV, 1, 100, NOW)
    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 900, "за лишнее тоже платят"

    await collect(db, fresh, 1, NOW + charge_seconds(100))

    keys = await mine(db, fresh, NOW + charge_seconds(100))
    assert keys[0]["charge"] == 100


async def test_only_the_owner_collects_the_car(db):
    """Чужую машину с колонки не отдают."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 20)
    await plug(db, player, SUV, 1, 50, NOW)
    stranger = await licensed(db, make_player(7, "Марла", where=STATION))

    with pytest.raises(ChargeError, match="чужая машина"):
        await collect(db, stranger, 1, NOW + 600)


async def test_a_rental_that_ran_out_frees_the_column(db):
    """Кончилась аренда, пока машина стояла, — колонка освободилась.

    Колонок на город три, и держать одну из них машиной, которую салон
    уже забрал, станция не станет: освобождает её всякий, кто на
    станцию смотрит.
    """
    player = await licensed(db, make_player(where=STATION, credits=1000))
    await db.put_car(player.user_id, SUV, 1800, NOW)
    player = await db.get_player(player.user_id)
    player = await drained(db, player, 20)
    await plug(db, player, SUV, 1, 50, NOW)

    # Срок вышел: салон забрал машину, а колонка стояла занятой
    columns = await slots(db, player, NOW + 3600)

    assert not any(one["busy"] for one in columns)
    with pytest.raises(ChargeError, match="пуста"):
        await collect(db, player, 1, NOW + 3600)


async def test_an_empty_column_has_nothing_to_give(db):
    """С пустой колонки забирать нечего."""
    player = await driver(db)

    with pytest.raises(ChargeError, match="пуста"):
        await collect(db, player, 3, NOW)


# ---------- экран ----------


async def test_the_station_screen_shows_the_columns_and_the_keys(db):
    """Экран АЗС: три колонки, свои машины и три мерки у каждой."""
    player = await driver(db, credits=1000)
    player = await drained(db, player, 40)

    body = await build_charge(db, player, NOW)

    assert body["title"] == get_location(STATION).title
    assert body["count"] == 3 and body["free"] == 3
    assert len(body["slots"]) == 3
    car = body["cars"][0]
    assert car["charge"] == 40 and car["km_left"] == 160
    assert car["can_charge"] is True and car["why"] == ""
    assert [one["percent"] for one in car["steps"]] == [50, 75, 100]
    assert [one["price"] for one in car["steps"]] == [50, 75, 100]
    # Пролитое видно до оплаты: бак на 40%, не хватает шестидесяти
    assert [one["waste"] for one in car["steps"]] == [0, 15, 40]
    assert "+50%" in body["note"]


async def test_the_screen_names_the_reason_the_button_is_dark(db):
    """Погасшая кнопка называет причину: колонка, парковка или полный бак."""
    player = await driver(db, credits=1000)

    full = (await build_charge(db, player, NOW))["cars"][0]
    assert full["can_charge"] is False and "Бак полон" in full["why"]

    player = await drained(db, player, 20)
    await plug(db, player, SUV, 3, 50, NOW)
    fresh = await db.get_player(player.user_id)
    body = await build_charge(db, fresh, NOW)
    standing = body["cars"][0]
    assert standing["charging"] is True and standing["slot"] == 3
    assert "3-й колонке" in standing["why"]
    assert body["free"] == 2
    assert "осталось" in body["note"]

    ready = await build_charge(db, fresh, NOW + 600)
    assert "можно забирать" in ready["note"]


async def test_a_parked_car_shows_its_charge_before_you_walk_for_it(db):
    """Заряд видно до того, как за машиной пошли в гараж."""
    from bot.car_service import parked_at

    player = await driver(db, credits=1000)
    player = await drained(db, player, 25)
    # Ставим машину прямо в базе: кому открыта парковка особняка —
    # дело клана, а не заряда
    await db.park_car(player.user_id, SUV, IN_CLAN, 1, 3)

    standing = await parked_at(db, IN_CLAN, 1, player, NOW)

    assert standing[0]["charge"] == 25
    assert standing[0]["km_left"] == 100


async def test_the_keys_in_the_bag_show_the_charge(db):
    """Ключи бойца носят заряд с собой: без него машина — загадка."""
    player = await driver(db)
    player = await drained(db, player, 50)

    keys = await mine(db, player, NOW)

    assert keys[0]["charge"] == 50
    assert keys[0]["km_left"] == 200
    assert keys[0]["range"] == 400
    assert keys[0]["charging"] is False


async def test_the_station_opens_only_inside_it(db, client):
    """Колонку занимают на станции: спрятанная кнопка обходится запросом.

    Экран АЗС проверяет место на сервере, как и всякий другой дом: иначе
    машину можно было бы поставить на колонку из любого угла города, и
    карта стала бы украшением.
    """
    player = await driver(db, credits=1000)
    await drained(db, player, 20)

    answer = await client.get("/api/charge", headers=headers())
    assert answer.status == 200
    body = await answer.json()
    assert body["count"] == CHARGE_SLOTS and body["free"] == CHARGE_SLOTS

    answer = await client.post(
        "/api/charge",
        json={"action": "plug", "code": SUV, "slot": 1, "percent": 50},
        headers=headers(),
    )
    assert answer.status == 200
    assert "1-й колонке" in (await answer.json())["said"]

    # Непонятного действия станция не делает
    answer = await client.post(
        "/api/charge", json={"action": "кнопка"}, headers=headers()
    )
    assert answer.status == 400

    # А из клуба станция не отвечает вовсе
    away = await db.get_player(player.user_id)
    away.location = "fight_club"
    await db.save_player(away)
    assert (await client.get("/api/charge", headers=headers())).status == 409
