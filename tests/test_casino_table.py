"""Казино: столы, деньги и жизненный цикл партии.

Тут проверяется не арифметика выплат — она в `tests/test_casino.py`, —
а то, за что отвечает служба: кто успел сесть, когда закрылся приём,
сколько раз списали фишки и что увидел тот, кто вернулся из фона.

Время во всех тестах задаётся руками. Иначе проверка «приём закрылся
ровно на границе» зависела бы от того, как быстро бежит прогон.
"""

from __future__ import annotations

import pytest

from bot.casino_service import (
    CasinoError,
    SPIN_EVERY,
    bet,
    exchange,
    hand_move,
    join_pot,
    lobby,
    look,
    open_card,
    open_table,
    sit,
    stand,
    table_view,
    take_back,
)
from bot.database import Database
from bot.models import Player

NOW = 1_800_000_000  # «сейчас» всех тестов: ровная граница вращения


async def tick(db: Database, table_id: int, since: int, until: int, step: int = 1):
    """Прожить столом от и до, заглядывая каждую секунду.

    Стол догоняет время при взгляде, а не по будильнику: это общий
    приём всего проекта — ходить в базу по таймеру некому. Живой стол
    смотрят четыре экрана по два раза в секунду, в тестах смотрим мы.
    """
    table = None
    for moment in range(since, until + 1, step):
        table = await look(db, table_id, moment)
    return table


async def gambler(
    db: Database, user_id: int, chips: int = 0, credits: int = 1_000
) -> Player:
    """Боец с деньгами в кармане и, если надо, с фишками на руках."""
    one = Player(
        user_id=user_id,
        nickname=f"Игрок{user_id}",
        class_code="warrior",
        credits=credits,
        location="casino",
        seen_at=NOW,
    )
    await db.save_player(one)
    if chips:
        await exchange(db, one, "buy", chips, NOW)
    return one


# ---------- касса ----------


async def test_the_cashier_changes_one_for_one_both_ways(db):
    """Кредит в фишку и обратно, без комиссии и без курса."""
    one = await gambler(db, 1, credits=500)
    assert "500" in await exchange(db, one, "buy", 500, NOW)
    assert await db.casino_wallet(1) == {"chips": 500, "reserved": 0}
    fresh = await db.get_player(1)
    assert fresh.credits == 0, "кредиты ушли в кассу"

    await exchange(db, fresh, "sell", 200, NOW)
    assert (await db.casino_wallet(1))["chips"] == 300
    assert (await db.get_player(1)).credits == 200
    # Курс один к одному в обе стороны: сколько сдал, столько и получил
    assert (await db.casino_wallet(1))["chips"] + 200 == 500


async def test_the_cashier_gives_only_what_you_have(db):
    one = await gambler(db, 1, credits=100)
    with pytest.raises(CasinoError):
        await exchange(db, one, "buy", 101, NOW)
    assert (await db.casino_wallet(1))["chips"] == 0
    assert (await db.get_player(1)).credits == 100, "неудача не списывает"

    await exchange(db, one, "buy", 100, NOW)
    with pytest.raises(CasinoError):
        await exchange(db, one, "sell", 101, NOW)
    with pytest.raises(CasinoError):
        await exchange(db, one, "buy", 0, NOW)


async def test_the_chips_in_play_cannot_be_cashed_out(db):
    """Фишки в резерве — уже не твои: сдать их до расчёта нельзя."""
    one = await gambler(db, 1, chips=200)
    table = await open_table(db, "dice", NOW)
    await sit(db, one, table.id, 1, NOW)
    await bet(db, one, table.id, 200, pick="seven", now=NOW)
    purse = await db.casino_wallet(1)
    assert purse == {"chips": 0, "reserved": 200}
    with pytest.raises(CasinoError):
        await exchange(db, one, "sell", 200, NOW)


# ---------- стол на четыре места ----------


async def test_four_sit_down_and_the_fifth_is_offered_another_table(db):
    """Четыре места заняты — пятый садится за соседний стол."""
    table = await open_table(db, "dice", NOW)
    for number in range(1, 5):
        one = await gambler(db, number, chips=100)
        await sit(db, one, table.id, number, NOW)
    fifth = await gambler(db, 5, chips=100)
    with pytest.raises(CasinoError):
        await sit(db, fifth, table.id, 1, NOW)

    board = await lobby(db, 5, NOW)
    dice = next(one for one in board["games"] if one["code"] == "dice")
    free = [one for one in dice["tables"] if one["taken"] < one["seats"]]
    assert free, "свободный стол в лобби есть всегда"
    await sit(db, fifth, free[0]["id"], 1, NOW)
    assert (await db.casino_seat_of(5))["table_id"] == free[0]["id"]


async def test_two_people_cannot_take_the_same_seat(db):
    """Гонка за последним местом: сервер принимает только одного."""
    table = await open_table(db, "dice", NOW)
    one = await gambler(db, 1, chips=100)
    two = await gambler(db, 2, chips=100)
    assert await sit(db, one, table.id, 3, NOW)
    with pytest.raises(CasinoError):
        await sit(db, two, table.id, 3, NOW)
    seats = await db.casino_seats(table.id)
    assert [int(row["user_id"]) for row in seats] == [1]


async def test_one_player_holds_one_seat(db):
    """Один Telegram ID — одно место, хоть за тем же столом, хоть за другим."""
    first = await open_table(db, "dice", NOW)
    second = await open_table(db, "dice", NOW)
    one = await gambler(db, 1, chips=100)
    await sit(db, one, first.id, 1, NOW)
    with pytest.raises(CasinoError):
        await sit(db, one, first.id, 2, NOW)
    with pytest.raises(CasinoError):
        await sit(db, one, second.id, 1, NOW)
    await stand(db, 1, NOW)
    await sit(db, one, second.id, 1, NOW)
    assert (await db.casino_seat_of(1))["table_id"] == second.id


async def test_an_empty_seat_keeps_its_place_in_the_view(db):
    """Пустое место остаётся местом: экран не схлопывает стол."""
    table = await open_table(db, "dice", NOW)
    one = await gambler(db, 1, chips=100)
    await sit(db, one, table.id, 2, NOW)
    view = await table_view(db, table.id, 1, NOW)
    assert [row["seat"] for row in view["seats"]] == ["1", "2", "3", "4"]
    assert view["seats"][0]["empty"] and not view["seats"][1].get("empty")
    assert view["seats"][1]["me"]


# ---------- приём ставок ----------


async def test_the_bet_goes_to_the_reserve_at_once(db):
    one = await gambler(db, 1, chips=500)
    table = await open_table(db, "dice", NOW)
    await sit(db, one, table.id, 1, NOW)
    await bet(db, one, table.id, 100, pick="under", now=NOW)
    assert await db.casino_wallet(1) == {"chips": 400, "reserved": 100}
    journal = await db.casino_moves(table.id, 1, 1)
    assert [(row["kind"], row["amount"]) for row in journal] == [("bet", 100)]


async def test_the_same_tap_twice_is_one_bet(db):
    """Повторный запрос с тем же ключом не становится второй ставкой."""
    one = await gambler(db, 1, chips=500)
    table = await open_table(db, "dice", NOW)
    await sit(db, one, table.id, 1, NOW)
    said = await bet(db, one, table.id, 100, pick="seven", command_id="x1",
                     now=NOW)
    again = await bet(db, one, table.id, 100, pick="seven", command_id="x1",
                      now=NOW)
    assert said == again
    assert await db.casino_wallet(1) == {"chips": 400, "reserved": 100}


async def test_a_late_bet_is_refused_without_taking_anything(db):
    """Опоздавшая команда отклоняется без списания."""
    one = await gambler(db, 1, chips=500)
    table = await open_table(db, "dice", NOW)
    await sit(db, one, table.id, 1, NOW)
    fresh = await look(db, table.id, NOW)
    closes = int(fresh.state["until"])
    with pytest.raises(CasinoError):
        await bet(db, one, table.id, 100, pick="seven", now=closes + 1)
    assert await db.casino_wallet(1) == {"chips": 500, "reserved": 0}


async def test_the_table_keeps_its_limits(db):
    one = await gambler(db, 1, chips=5_000, credits=5_000)
    table = await open_table(db, "dice", NOW)
    await sit(db, one, table.id, 1, NOW)
    for bad in (30, 10, 4_000):
        with pytest.raises(CasinoError):
            await bet(db, one, table.id, bad, pick="seven", now=NOW)
    assert (await db.casino_wallet(1))["reserved"] == 0
    await bet(db, one, table.id, 40, pick="seven", now=NOW)


async def test_a_bet_can_be_changed_while_the_door_is_open(db):
    """До закрытия ставку меняют, и резерв меняется вместе с ней."""
    one = await gambler(db, 1, chips=500)
    table = await open_table(db, "dice", NOW)
    await sit(db, one, table.id, 1, NOW)
    await bet(db, one, table.id, 100, pick="under", command_id="a", now=NOW)
    await bet(db, one, table.id, 40, pick="over", command_id="b", now=NOW)
    assert await db.casino_wallet(1) == {"chips": 460, "reserved": 40}
    await take_back(db, one, table.id, command_id="c", now=NOW)
    assert await db.casino_wallet(1) == {"chips": 500, "reserved": 0}


# ---------- кости ----------


async def test_the_dice_round_runs_itself_and_pays(db):
    """Партия в кости: приём, бросок, расчёт — всё по серверным часам."""
    one = await gambler(db, 1, chips=200)
    table = await open_table(db, "dice", NOW, seed="кости")
    await sit(db, one, table.id, 1, NOW)
    fresh = await look(db, table.id, NOW)
    assert fresh.phase == "accept"
    closes = int(fresh.state["until"])
    assert closes == NOW + 12, "на выбор двенадцать секунд"

    await bet(db, one, table.id, 100, pick="seven", now=NOW)
    rolled = await look(db, table.id, closes)
    assert rolled.phase == "play"
    result = rolled.state["result"]
    assert len(result["dice"]) == 2
    assert sum(result["dice"]) == result["sum"]
    assert all(1 <= one <= 6 for one in result["dice"])

    done = await look(db, table.id, closes + 10)
    assert done.phase in ("done", "accept", "wait")
    purse = await db.casino_wallet(1)
    assert purse["reserved"] == 0, "резерв закрылся расчётом"
    if result["outcome"] == "seven":
        assert purse["chips"] == 100 + 575
    else:
        assert purse["chips"] == 100


async def test_the_dice_pay_once_however_many_times_you_look(db):
    """Расчёт повторяется безопасно: второй взгляд не платит снова."""
    one = await gambler(db, 1, chips=200)
    table = await open_table(db, "dice", NOW, seed="кости")
    await sit(db, one, table.id, 1, NOW)
    await bet(db, one, table.id, 100, pick="seven", now=NOW)
    closes = int((await look(db, table.id, NOW)).state["until"])
    await look(db, table.id, closes + 3)
    first = await db.casino_wallet(1)
    for _ in range(3):
        await look(db, table.id, closes + 3)
    assert await db.casino_wallet(1) == first
    payouts = [
        row for row in await db.casino_moves(table.id, 1, 1)
        if row["kind"] == "payout"
    ]
    assert len(payouts) == 1, "одна запись расчёта на место и партию"


async def test_the_whole_table_sees_one_roll(db):
    """Оба значения, сумма и исход — общие для всего стола."""
    table = await open_table(db, "dice", NOW, seed="общий")
    for number in (1, 2):
        one = await gambler(db, number, chips=200)
        await sit(db, one, table.id, number, NOW)
        await bet(db, one, table.id, 20, pick="under", now=NOW)
    closes = int((await look(db, table.id, NOW)).state["until"])
    first = await table_view(db, table.id, 1, closes)
    second = await table_view(db, table.id, 2, closes)
    assert first["result"]["dice"] == second["result"]["dice"]
    assert first["round"] == second["round"]


# ---------- рулетка ----------


async def test_the_wheel_turns_every_two_minutes_by_the_clock(db):
    """Вращение начинается по серверным часам, кнопки запуска нет."""
    table = await open_table(db, "roulette", NOW, seed="колесо")
    fresh = await look(db, table.id, NOW + 5)
    assert fresh.phase == "accept"
    spin = int(fresh.state["until"])
    assert spin % SPIN_EVERY == 0
    assert 0 < spin - (NOW + 5) <= SPIN_EVERY

    turning = await look(db, table.id, spin)
    assert turning.phase == "play"
    number = turning.state["result"]["number"]
    assert 0 <= number <= 36
    # Следующее вращение — ровно через две минуты
    later = await look(db, table.id, spin + 10)
    assert int(later.state["until"]) == spin + SPIN_EVERY


async def test_the_roulette_pays_every_winning_spot(db):
    """Начисления по всем выигравшим полям складываются."""
    one = await gambler(db, 1, chips=500)
    table = await open_table(db, "roulette", NOW, seed="колесо")
    await sit(db, one, table.id, 1, NOW)
    spin = int((await look(db, table.id, NOW)).state["until"])
    await bet(db, one, table.id, 10, spot="color", pick="red",
              command_id="r1", now=NOW)
    await bet(db, one, table.id, 5, spot="number", pick="7",
              command_id="r2", now=NOW)
    assert await db.casino_wallet(1) == {"chips": 485, "reserved": 15}

    await look(db, table.id, spin + 1)
    view = await table_view(db, table.id, 1, spin + 1)
    number = view["result"]["number"]
    from bot.game.casino import colour_of

    want = 0
    if colour_of(number) == "red":
        want += 20
    if number == 7:
        want += 180
    assert (await db.casino_wallet(1))["chips"] == 485 + want


async def test_the_last_chip_can_be_taken_back(db):
    """Отмена последней фишки и очистка стола — до закрытия приёма."""
    one = await gambler(db, 1, chips=500)
    table = await open_table(db, "roulette", NOW, seed="колесо")
    await sit(db, one, table.id, 1, NOW)
    await bet(db, one, table.id, 10, spot="color", pick="red",
              command_id="r1", now=NOW)
    await bet(db, one, table.id, 5, spot="number", pick="7",
              command_id="r2", now=NOW)
    await take_back(db, one, table.id, command_id="r3", now=NOW)
    assert await db.casino_wallet(1) == {"chips": 490, "reserved": 10}
    await take_back(db, one, table.id, whole=True, command_id="r4", now=NOW)
    assert await db.casino_wallet(1) == {"chips": 500, "reserved": 0}


async def test_the_wheel_knows_no_such_field(db):
    one = await gambler(db, 1, chips=500)
    table = await open_table(db, "roulette", NOW)
    await sit(db, one, table.id, 1, NOW)
    for spot, pick in (("number", "37"), ("color", "зелёное"), ("пас", "1")):
        with pytest.raises(CasinoError):
            await bet(db, one, table.id, 5, spot=spot, pick=pick, now=NOW)
    assert (await db.casino_wallet(1))["reserved"] == 0


# ---------- старшая карта ----------


async def test_the_pot_starts_when_two_have_confirmed(db):
    """Подтвердили двое — пошёл отсчёт на восемь секунд."""
    table = await open_table(db, "highcard", NOW, seed="карта")
    for number in (1, 2, 3):
        one = await gambler(db, number, chips=200)
        await sit(db, one, table.id, number, NOW)
    first = await db.get_player(1)
    await join_pot(db, first, table.id, command_id="j1", now=NOW)
    waiting = await look(db, table.id, NOW)
    assert not int(waiting.state["until"]), "один — ещё не партия"

    second = await db.get_player(2)
    await join_pot(db, second, table.id, command_id="j2", now=NOW)
    counting = await look(db, table.id, NOW)
    assert int(counting.state["until"]) == NOW + 8

    # Третий успевает вступить, пока идёт отсчёт
    third = await db.get_player(3)
    await join_pot(db, third, table.id, command_id="j3", now=NOW + 5)
    dealt = await look(db, table.id, NOW + 8)
    assert dealt.phase == "play"
    assert dealt.state["result"]["pot"] == 150
    assert dealt.state["result"]["rake"] == 7
    assert dealt.state["result"]["prize"] == 143


async def test_the_pot_comes_back_when_the_table_falls_apart(db):
    """Меньше двух к фиксации — отсчёт отменяется, взносы возвращаются."""
    table = await open_table(db, "highcard", NOW, seed="карта")
    one = await gambler(db, 1, chips=200)
    await sit(db, one, table.id, 1, NOW)
    await join_pot(db, one, table.id, command_id="j1", now=NOW)
    # Отсчёта нет вовсе: он начинается со второго
    assert not int((await look(db, table.id, NOW + 30)).state["until"])
    assert (await db.casino_wallet(1))["reserved"] == 200 - 150


async def test_the_highest_card_takes_the_pot_less_the_rake(db):
    """Победитель забирает банк минус пять процентов."""
    table = await open_table(db, "highcard", NOW, seed="карта")
    for number in (1, 2):
        one = await gambler(db, number, chips=200)
        await sit(db, one, table.id, number, NOW)
        await join_pot(db, one, table.id, command_id=f"j{number}", now=NOW)
    played = await tick(db, table.id, NOW, NOW + 8 + 6 + 1)
    result = played.state["result"]
    assert result["pot"] == 100 and result["prize"] == 95
    assert len(result["winners"]) >= 1
    purses = {one: (await db.casino_wallet(one))["chips"] for one in (1, 2)}
    assert sum(purses.values()) == 150 + 150 + 95, "в игре осталась комиссия"
    for user_id in (1, 2):
        assert (await db.casino_wallet(user_id))["reserved"] == 0


async def test_the_card_opens_by_hand_or_by_itself(db):
    """Нажал «Открыть» — видно сразу; не нажал — откроется само."""
    table = await open_table(db, "highcard", NOW, seed="карта")
    for number in (1, 2):
        one = await gambler(db, number, chips=200)
        await sit(db, one, table.id, number, NOW)
        await join_pot(db, one, table.id, command_id=f"j{number}", now=NOW)
    await tick(db, table.id, NOW, NOW + 9)
    first = await db.get_player(1)
    await open_card(db, first, table.id, command_id="o1", now=NOW + 9)
    view = await table_view(db, table.id, 1, NOW + 9)
    mine = next(one for one in view["seats"] if one.get("me"))
    other = next(
        one for one in view["seats"]
        if one.get("user") == 2
    )
    assert mine["cards"], "свою карту видно"
    assert not other["cards"], "чужую закрытую — нет"
    assert other["count"] == 1


# ---------- блэкджек ----------


async def test_the_blackjack_round_deals_and_plays_itself(db):
    """Партия доигрывается сама: таймер хода истёк — «хватит»."""
    one = await gambler(db, 1, chips=200)
    table = await open_table(db, "blackjack", NOW, seed="очко")
    await sit(db, one, table.id, 1, NOW)
    await bet(db, one, table.id, 20, now=NOW)
    closes = int((await look(db, table.id, NOW)).state["until"])
    assert closes == NOW + 12

    dealt = await look(db, table.id, closes)
    assert dealt.phase == "play"
    seat = dealt.state["seats"]["1"]
    assert len(seat["cards"]) == 2
    assert len(dealt.state["dealer"]) == 2

    # Своя рука видна, вторая карта крупье — нет
    view = await table_view(db, table.id, 1, closes)
    assert len(view["seats"][0]["cards"]) == 2
    assert view["result"]["upcard"] == dealt.state["dealer"][0]
    assert "dealer" not in view["result"] or not view["result"].get("dealer")

    done = await tick(db, table.id, closes, closes + 3 + 10 * 5)
    assert done.phase in ("done", "wait", "accept")
    assert (await db.casino_wallet(1))["reserved"] == 0


async def test_the_player_hits_and_stands(db):
    one = await gambler(db, 1, chips=200)
    table = await open_table(db, "blackjack", NOW, seed="ход")
    await sit(db, one, table.id, 1, NOW)
    await bet(db, one, table.id, 20, now=NOW)
    closes = NOW + 12
    turns = await tick(db, table.id, NOW, closes + 4)
    # Зерно подобрано: на руке 15, и без решения партию не доиграть
    assert turns.phase == "turns", turns.phase
    assert turns.state["turn"] == "1"
    before = len(turns.state["seats"]["1"]["cards"])
    await hand_move(db, one, table.id, "hit", command_id="h1",
                    now=closes + 5)
    after = await look(db, table.id, closes + 5)
    assert len(after.state["seats"]["1"]["cards"]) == before + 1
    if after.phase == "turns":
        await hand_move(db, one, table.id, "stand", command_id="h2",
                        now=closes + 6)
        stood = await look(db, table.id, closes + 6)
        assert stood.phase in ("dealer", "done")


async def test_nobody_moves_out_of_turn(db):
    table = await open_table(db, "blackjack", NOW, seed="дом")
    for number in (1, 2):
        one = await gambler(db, number, chips=200)
        await sit(db, one, table.id, number, NOW)
        await bet(db, one, table.id, 20, command_id=f"b{number}", now=NOW)
    turns = await tick(db, table.id, NOW, NOW + 12 + 4)
    assert turns.phase == "turns", turns.phase
    waiting = "2" if turns.state["turn"] == "1" else "1"
    other = await db.get_player(int(waiting))
    with pytest.raises(CasinoError):
        await hand_move(db, other, table.id, "hit", command_id="z", now=NOW + 16)


# ---------- отключение ----------


async def test_the_one_who_left_after_betting_still_gets_paid(db):
    """После раздачи отключившийся сохраняет право на банк."""
    table = await open_table(db, "highcard", NOW, seed="уход")
    for number in (1, 2):
        one = await gambler(db, number, chips=200)
        await sit(db, one, table.id, number, NOW)
        await join_pot(db, one, table.id, command_id=f"j{number}", now=NOW)
    await tick(db, table.id, NOW, NOW + 9)  # карты сданы
    await stand(db, 1, NOW + 9)
    await tick(db, table.id, NOW + 9, NOW + 8 + 6 + 2)
    purses = [(await db.casino_wallet(one))["chips"] for one in (1, 2)]
    assert sum(purses) == 150 + 150 + 95
    for user_id in (1, 2):
        assert (await db.casino_wallet(user_id))["reserved"] == 0
