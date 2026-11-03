"""«Карусель удачи»: барабан, шансы и выдача приза.

Игра без стола: мест нет, крутят её сколько угодно человек разом.
Поэтому и проверяется тут не очередь, а две вещи, за которые в казино
спорят: что выпадает и с каким шансом, и что приз нельзя получить
дважды — ни забрав его два раза, ни забрав и сдав.
"""

from __future__ import annotations

import random

import pytest

from bot.casino_service import (
    CasinoError,
    carousel_roll,
    carousel_sell,
    carousel_take,
    carousel_view,
)
from bot.database import Database
from bot.game import carousel
from bot.game.equipment import ItemKind
from bot.models import Player
from tests.test_casino_table import NOW, gambler, night


# ---------- барабан ----------


def test_the_drum_holds_what_the_shops_sell():
    """В барабане то же, чем торгуют лавка и салон, а не свой список."""
    pool = carousel.prizes()
    kinds = {one.kind for one in pool}
    assert kinds == {"item", "chips", "car"}

    from bot.content.items import shelf_of

    want = {
        one.code for one in shelf_of("")
        if one.kind in (ItemKind.WEAPON, ItemKind.SHIELD)
        and one.level_required in (7, 8, 9) and one.price and not one.retired
    }
    assert {one.code for one in pool if one.kind == "item"} == want
    assert len(want) == 30, "оружие и щиты седьмого, восьмого и девятого"

    # Жетоны и машины — ровно те, что названы в правилах
    assert sorted(
        one.price for one in pool if one.kind == "chips"
    ) == [250, 500, 750]
    assert sorted(
        one.price for one in pool if one.kind == "car"
    ) == [5_000, 16_000, 20_000]


def test_every_prize_has_a_picture():
    """Картинка есть у всего, что едет по ленте, — жетоны не исключение.

    Стопки жетонов рисовали отдельно: без них на ленте стояли три
    пустых места, а с ними видно, что в барабане не только железо.
    """
    for prize in carousel.prizes():
        assert prize.image, prize.title
    packs = sorted(
        one.image for one in carousel.prizes() if one.kind == "chips"
    )
    assert [one.rsplit("/", 1)[-1] for one in packs] == [
        "pack-250.webp", "pack-500.webp", "pack-750.webp"
    ]
    assert all("casino/chips/" in one for one in packs)


def test_the_dearer_the_prize_the_rarer_it_falls():
    """Шанс обратен цене, и это проверяется по всему барабану."""
    pool = sorted(carousel.prizes(), key=lambda one: one.price)
    for cheap, dear in zip(pool, pool[1:]):
        if cheap.price == dear.price:
            continue
        assert carousel.chance(cheap) > carousel.chance(dear), (
            cheap.title, dear.title
        )
    # Машина за двадцать тысяч — самый редкий приз в городе
    top = max(pool, key=lambda one: one.price)
    assert carousel.chance(top) < 0.001
    assert top.weight == carousel.WEIGHT_SCALE


def test_the_draw_follows_the_weights():
    """Прогон барабана даёт те доли, что обещаны весами."""
    rng = random.Random("карусель")
    tally: dict[str, int] = {}
    rounds = 20_000
    for _ in range(rounds):
        prize = carousel.draw(rng)
        tally[prize.code] = tally.get(prize.code, 0) + 1

    for prize in carousel.prizes():
        want = carousel.chance(prize)
        got = tally.get(prize.code, 0) / rounds
        # Редкие призы ловить двадцатью тысячами вращений нечем:
        # проверяем те, у которых ожидание больше сотни попаданий
        if want * rounds < 100:
            continue
        assert abs(got - want) < want * 0.25, (prize.title, got, want)
    # А машины всё-таки выпадают: иначе это не приз, а украшение
    assert sum(
        tally.get(one.code, 0) for one in carousel.prizes()
        if one.kind == "car"
    ) > 0


def test_the_cashier_takes_the_prize_back_for_half():
    """Сдача — половина цены лавки, а жетоны идут по номиналу."""
    for prize in carousel.prizes():
        if prize.kind == "chips":
            assert prize.sells_for == prize.price
        else:
            assert prize.sells_for == prize.price // 2


def test_the_belt_holds_the_whole_drum_and_never_changes():
    """Лента постоянная: весь барабан разом и в одном порядке.

    Карусель едет — этого ей и довольно. Прежде лента пересчитывалась
    раз в пять секунд, и товар на ходу подменялся: со стороны это
    выглядело не каруселью, а прыжком каждые пять секунд.
    """
    first = carousel.belt()
    assert [one.code for one in first] == [
        one.code for one in carousel.belt()
    ], "лента одна и та же, сколько её ни спрашивай"
    assert {one.code for one in first} == {
        one.code for one in carousel.prizes()
    }, "на ленте весь барабан: по ней и видно, что может выпасть"
    assert len(first) == len(carousel.prizes())

    # Порядок перемешан: иначе дорогое стояло бы кучей в конце
    prices = [one.price for one in first]
    assert prices != sorted(prices) and prices != sorted(prices, reverse=True)


def test_the_house_keeps_its_share():
    """Числа игры названы, а не подразумеваются.

    Ролл стоит 250. В среднем приз стоит дороже — по ценам лавки, — но
    сдача идёт за половину, и жетонами казино возвращает меньше, чем
    взяло. Это и есть доход дома с барабана, и он больше, чем за
    столами: там возврат 95–96%.
    """
    assert carousel.ROLL_PRICE == 250
    assert 330 <= carousel.average_win() <= 340
    assert 175 <= carousel.average_sale() <= 190
    share = carousel.average_sale() / carousel.ROLL_PRICE
    assert 0.70 <= share <= 0.75


# ---------- вращение ----------


async def test_a_roll_costs_two_hundred_fifty_and_decides_at_once(db):
    """Жетоны снимаются сразу, приз решается до первого кадра."""
    one = await gambler(db, 1, chips=1_000, credits=9_000)
    await carousel_roll(db, one, NOW)

    assert (await db.casino_wallet(1))["chips"] == 750
    view = await carousel_view(db, one, NOW)
    assert view["price"] == 250
    assert view["prize"], "приз решён сразу, до первого кадра"
    assert view["prize"]["ready"] == NOW + carousel.SPIN_SECONDS
    assert carousel.SPIN_SECONDS == 7, "семь секунд: пять рывка и выкат"


async def test_the_roll_says_nothing_about_the_prize(db):
    """Ответ на ролл молчит о призе: вся игра в эти семь секунд.

    Приз решён и уже лежит в строке — экран ведёт к нему ленту. Но
    напечатать его над крутящимся барабаном значит напечатать ответ:
    интриги не остаётся вовсе, и лента крутится зря.
    """
    one = await gambler(db, 1, chips=1_000, credits=9_000)

    for _ in range(25):
        said = await carousel_roll(db, one, NOW)
        prize = (await carousel_view(db, one, NOW))["prize"]
        assert prize, "приз решён, просто не назван"
        assert prize["title"].lower() not in said.lower(), said
        assert str(prize["price"]) not in said, said
        # Разбираем и крутим снова: за двадцать пять раз попадутся и
        # жетоны, и вещи, и ответ должен молчать про всё
        await (
            carousel_take if prize["kind"] != "car" else carousel_sell
        )(db, one, NOW + carousel.SPIN_SECONDS)
        await db.casino_give(1, 250, NOW)


async def test_the_drum_waits_until_it_stops(db):
    """Пока барабан крутится, приз не забрать и не сдать."""
    one = await gambler(db, 1, chips=1_000, credits=9_000)
    while (await carousel_view(db, one, NOW))["prize"] is None:
        await carousel_roll(db, one, NOW)

    for take in (carousel_take, carousel_sell):
        with pytest.raises(CasinoError) as early:
            await take(db, one, NOW + 2)
        assert "крутится" in str(early.value)
    # А как барабан встанет — пожалуйста
    await carousel_sell(db, one, NOW + carousel.SPIN_SECONDS)


async def test_one_prize_at_a_time(db):
    """Крутить заново, не разобравшись с прошлым призом, нельзя."""
    one = await gambler(db, 1, chips=2_000, credits=9_000)
    while (await carousel_view(db, one, NOW))["prize"] is None:
        await carousel_roll(db, one, NOW)
    had = (await db.casino_wallet(1))["chips"]

    with pytest.raises(CasinoError) as busy:
        await carousel_roll(db, one, NOW)
    assert "забери или сдай" in str(busy.value)
    assert (await db.casino_wallet(1))["chips"] == had, "отказ не берёт денег"


async def test_the_chips_are_taken_and_never_sold(db):
    """Жетоны проходят тот же барабан, но их забирают, а не сдают.

    Прежде жетоны начислялись прямо в ответе на ролл: лента для них не
    крутилась вовсе, и на каждом девятом ролле барабан стоял. Теперь
    они ждут остановки, как и всё остальное, — а вот выбора «забрать
    или сдать» у них нет: сдавать жетоны не во что.
    """
    one = await gambler(db, 1, chips=5_000, credits=9_000)
    while True:
        await carousel_roll(db, one, NOW)
        prize = (await carousel_view(db, one, NOW))["prize"]
        if prize["kind"] == "chips":
            break
        await carousel_sell(db, one, NOW + carousel.SPIN_SECONDS)
    had = (await db.casino_wallet(1))["chips"]

    with pytest.raises(CasinoError) as sale:
        await carousel_sell(db, one, NOW + carousel.SPIN_SECONDS)
    assert "не сдают" in str(sale.value)

    said = await carousel_take(db, one, NOW + carousel.SPIN_SECONDS)

    assert str(prize["price"]) in said
    assert (await db.casino_wallet(1))["chips"] == had + prize["price"]
    assert await db.casino_prize(1) is None
    assert await db.list_gear(1) == [], "жетоны в рюкзак не кладут"


async def test_the_prize_is_given_once_and_not_twice(db):
    """Забрать и сдать один и тот же выигрыш нельзя."""
    one = await gambler(db, 1, chips=2_000, credits=9_000)
    while True:
        await carousel_roll(db, one, NOW)
        prize = (await carousel_view(db, one, NOW))["prize"]
        if prize and prize["kind"] == "item":
            break
    await carousel_take(db, one, NOW + carousel.SPIN_SECONDS)

    gear = await db.list_gear(1)
    assert [row.code for row in gear] == [prize["code"]]
    with pytest.raises(CasinoError):
        await carousel_take(db, one, NOW + carousel.SPIN_SECONDS)
    with pytest.raises(CasinoError):
        await carousel_sell(db, one, NOW + carousel.SPIN_SECONDS)
    assert len(await db.list_gear(1)) == 1


async def test_the_prize_can_be_sold_for_chips_on_the_spot(db):
    """Сдал — жетоны на счёт, и вещь в рюкзак не попала."""
    one = await gambler(db, 1, chips=2_000, credits=9_000)
    while True:
        await carousel_roll(db, one, NOW)
        prize = (await carousel_view(db, one, NOW))["prize"]
        if prize and prize["kind"] == "item":
            break
    had = (await db.casino_wallet(1))["chips"]

    said = await carousel_sell(db, one, NOW + carousel.SPIN_SECONDS)

    assert str(prize["sells_for"]) in said
    assert (await db.casino_wallet(1))["chips"] == had + prize["sells_for"]
    assert await db.list_gear(1) == [], "сданное в рюкзак не кладут"


async def test_the_car_goes_to_the_garage(db):
    """Выигранная машина достаётся насовсем и места в гараже требует."""
    from bot.game.carousel import Prize

    one = await gambler(db, 1, chips=1_000, credits=9_000)
    # Машину ловить вращениями дорого: кладём её призом прямо в базу —
    # выдача от того, как приз попал в строку, не зависит
    await db.casino_keep_prize(1, "car", "city_sedan", 5_000, NOW)

    said = await carousel_take(db, one, NOW + carousel.SPIN_SECONDS)

    assert "Кибер" in said
    rows = await db.cars_of(1)
    assert [row["code"] for row in rows] == ["city_sedan"]
    assert not rows[0]["until"], "своя машина, а не аренда"

    # Вторая машина в руки не влезет, и приз об этом говорит прямо
    await db.casino_keep_prize(1, "car", "iron_muscle", 20_000, NOW)
    with pytest.raises(CasinoError) as full:
        await carousel_take(db, one, NOW + carousel.SPIN_SECONDS)
    assert "парковку" in str(full.value)
    # И приз при этом не пропал: он всё ещё ждёт решения
    assert (await db.casino_prize(1)) is not None
    assert Prize("car", "iron_muscle", "", 20_000).sells_for == 10_000


async def test_the_empty_purse_turns_no_drum(db):
    """Нет жетонов — нет вращения, и долга за барабан не остаётся."""
    one = await gambler(db, 1, chips=100, credits=100)

    with pytest.raises(CasinoError) as poor:
        await carousel_roll(db, one, NOW)

    assert "250" in str(poor.value)
    assert (await db.casino_wallet(1))["chips"] == 100


async def test_the_drum_sleeps_with_the_casino(db):
    """Ночью барабан не крутится: у дома общие часы."""
    one = await gambler(db, 1, chips=1_000, credits=9_000)

    with pytest.raises(CasinoError) as shut:
        await carousel_roll(db, one, night(4))

    assert "08:00" in str(shut.value)
    assert (await db.casino_wallet(1))["chips"] == 1_000
    view = await carousel_view(db, one, night(4))
    assert view["open"] is False and view["shut"]
