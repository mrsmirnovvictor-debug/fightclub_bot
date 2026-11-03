"""Казино: правила четырёх игр и расчёт до единой фишки.

Проверяется не «работает ли экран», а то, за что в казино спорят:
сколько начислено и почему. Поэтому таблицы прогоняются целиком — все
37 карманов рулетки и все 36 упорядоченных пар костей, — а не парой
удачных примеров.
"""

from __future__ import annotations

import random

from bot.game.casino import (
    BLACKJACK_PAY,
    BLACKJACK_STEP,
    DICE_PACKET,
    DICE_PAY,
    GAME_LIST,
    RANKS,
    ROULETTE_ODDS,
    SUITS,
    WHEEL,
    blackjack_pay,
    colour_of,
    dealer_draws,
    dice_outcome,
    dice_pay,
    fresh_deck,
    hand_total,
    high_rank,
    is_blackjack,
    leaders,
    peeks,
    pocket_angle,
    prize_of,
    rake_of,
    roulette_pay,
    roulette_wins,
    split_pot,
)


# ---------- рулетка ----------


def test_the_wheel_is_a_real_european_wheel():
    """37 карманов, каждое число по разу, и порядок не по возрастанию.

    Порядок важнее состава: по месту числа в кольце считается угол
    остановки диска. Если бы карманы шли по порядку, подсветка
    показывала бы одно, а шарик стоял бы в другом.
    """
    assert len(WHEEL) == 37
    assert sorted(WHEEL) == list(range(37))
    assert WHEEL[0] == 0
    assert WHEEL[1] == 32, "за нулём на европейском колесе стоит 32"
    assert WHEEL != tuple(range(37)), "колесо — не линейка"
    # Соседи по колесу — разного цвета, кроме соседей зеро
    for index, number in enumerate(WHEEL):
        nxt = WHEEL[(index + 1) % len(WHEEL)]
        if 0 in (number, nxt):
            continue
        assert colour_of(number) != colour_of(nxt), (number, nxt)


def test_each_pocket_has_its_own_angle():
    """У каждого кармана свой угол, и считается он по кольцу."""
    angles = {one: pocket_angle(one) for one in WHEEL}
    assert len(set(angles.values())) == 37
    assert angles[0] == 0.0
    assert round(angles[32], 6) == round(360 / 37, 6)


def test_the_table_pays_every_pocket_the_way_the_spec_says():
    """Все 37 исходов: что выиграло, то и начислено.

    Прогон целиком, а не примером: ошибка в одном кармане — это
    спор с игроком, которого не видно до выплаты.
    """
    for number in range(37):
        colour = colour_of(number)
        # Число выигрывает ровно одно — своё
        for guess in range(37):
            assert roulette_wins("number", str(guess), number) is (
                guess == number
            ), (number, guess)
        if number == 0:
            # Зеро: платит только ставка на ноль, всё остальное горит
            for spot, picks in (
                ("color", ("red", "black")),
                ("parity", ("even", "odd")),
                ("half", ("low", "high")),
                ("dozen", ("1", "2", "3")),
            ):
                for pick in picks:
                    assert not roulette_wins(spot, pick, 0), (spot, pick)
            assert roulette_pay([{"spot": "number", "pick": "0",
                                  "amount": 10}], 0) == 360
            continue
        assert colour in ("red", "black")
        assert roulette_wins("color", colour, number)
        assert not roulette_wins(
            "color", "red" if colour == "black" else "black", number
        )
        parity = "even" if number % 2 == 0 else "odd"
        assert roulette_wins("parity", parity, number)
        half = "low" if number <= 18 else "high"
        assert roulette_wins("half", half, number)
        dozen = str((number - 1) // 12 + 1)
        assert roulette_wins("dozen", dozen, number)
        # И начисление по каждому выигравшему полю — своё
        for spot, pick in (
            ("number", str(number)), ("color", colour),
            ("parity", parity), ("half", half), ("dozen", dozen),
        ):
            paid = roulette_pay([{"spot": spot, "pick": pick, "amount": 7}],
                                number)
            assert paid == 7 * ROULETTE_ODDS[spot], (number, spot)


def test_the_winning_spots_add_up():
    """Пример из ТЗ: 10 на красное и 5 на семёрку дают 200 при красной 7."""
    bets = [
        {"spot": "color", "pick": "red", "amount": 10},
        {"spot": "number", "pick": "7", "amount": 5},
    ]
    assert colour_of(7) == "red"
    assert roulette_pay(bets, 7) == 200
    # Чистый выигрыш — начисление минус поставленное
    assert roulette_pay(bets, 7) - 15 == 185
    # Чёрная семёрка невозможна, а вот чёрное число гасит ставку на число
    assert roulette_pay(bets, 9) == 20, "красная девятка платит только цвет"
    assert roulette_pay(bets, 11) == 0, "одиннадцать чёрное — не платит вовсе"


def test_the_house_edge_is_the_zero_and_nothing_else():
    """Возврат ровных полей — 36/37, и держит его один карман.

    Считается перебором всех карманов, а не формулой: формула
    повторила бы ошибку кода, если она там есть.
    """
    for spot, pick, odds in (
        ("color", "red", 2), ("parity", "even", 2),
        ("half", "low", 2), ("dozen", "1", 3),
    ):
        wins = sum(roulette_wins(spot, pick, one) for one in range(37))
        assert wins * odds / 37 == 36 / 37, (spot, pick, wins)
        assert odds == ROULETTE_ODDS[spot]
    wins = sum(roulette_wins("number", "7", one) for one in range(37))
    assert wins * 36 / 37 == 36 / 37


# ---------- кости ----------


def test_every_ordered_pair_of_dice_is_counted():
    """Все 36 пар: исход, шансы и начисление за пакет в 20."""
    tally = {"under": 0, "seven": 0, "over": 0}
    sevens = 0
    for first in range(1, 7):
        for second in range(1, 7):
            outcome = dice_outcome(first, second)
            tally[outcome] += 1
            if first + second == 7:
                sevens += 1
                assert outcome == "seven", (first, second)
            # Платит только угаданный исход, и ровно по таблице
            for pick in ("under", "seven", "over"):
                paid = dice_pay(pick, 20, first, second)
                assert paid == (DICE_PAY[pick] if pick == outcome else 0), (
                    first, second, pick
                )
    assert tally == {"under": 15, "seven": 6, "over": 15}
    assert sevens == 6, "семь собирается шестью способами"


def test_the_dice_pay_the_same_share_whatever_you_pick():
    """Возврат у всех трёх вариантов одинаковый — 95,83%.

    Это и есть смысл выплат 46 и 115: нет варианта «выгоднее», и
    выбирать игроку приходится не по таблице, а наугад.
    """
    chances = {"under": 15, "seven": 6, "over": 15}
    shares = {
        pick: count / 36 * DICE_PAY[pick] / DICE_PACKET
        for pick, count in chances.items()
    }
    assert len(set(round(one, 6) for one in shares.values())) == 1
    assert round(next(iter(shares.values())) * 100, 2) == 95.83


def test_the_dice_count_packets_not_percents():
    """Начисление считается пакетами по 20, а не умножением на 2,3."""
    assert dice_pay("seven", 100, 3, 4) == 575
    assert dice_pay("under", 40, 1, 2) == 92
    assert dice_pay("over", 0, 5, 5) == 0
    # Остаток от двадцатки не оплачивается вовсе — но до этого не
    # доходит: кратность проверяется на приёме ставки
    assert dice_pay("seven", 30, 3, 4) == 115


# ---------- карты ----------


def test_the_deck_is_fifty_two_and_every_card_is_there():
    deck = fresh_deck(random.Random(1))
    assert len(deck) == 52
    assert len(set(deck)) == 52
    assert {one[-1] for one in deck} == set(SUITS)
    assert sorted({one[:-1] for one in deck}, key=RANKS.index) == list(RANKS)


def test_the_ace_counts_as_eleven_while_it_fits():
    """Туз стоит 11, пока это не перебор, и 1 после."""
    assert hand_total(["A♠", "9♦"]) == (20, True)
    assert hand_total(["A♠", "9♦", "5♣"]) == (15, False)
    assert hand_total(["A♠", "A♦"]) == (12, True), "два туза — это 12"
    assert hand_total(["A♠", "A♦", "9♣"]) == (21, True)
    assert hand_total(["K♠", "Q♦"]) == (20, False)
    assert hand_total(["10♠", "7♦", "5♣"]) == (22, False)


def test_a_blackjack_is_only_the_first_two_cards():
    assert is_blackjack(["A♠", "K♦"])
    assert not is_blackjack(["7♠", "7♦", "7♣"]), "обычное 21 — не блэкджек"
    assert blackjack_pay(20, ["A♠", "K♦"], ["9♠", "8♦"]) == 50
    assert 20 * BLACKJACK_PAY // BLACKJACK_STEP == 50


def test_the_dealer_stands_on_every_seventeen():
    """Крупье берёт на 16 и меньше, встаёт на 17 — и на мягких тоже."""
    assert dealer_draws(["10♠", "6♦"])
    assert not dealer_draws(["10♠", "7♦"])
    assert not dealer_draws(["A♠", "6♦"]), "мягкие 17 — тоже 17"
    assert dealer_draws(["A♠", "5♦"]), "мягкие 16 — ещё добор"


def test_the_dealer_peeks_only_at_an_ace_or_a_ten():
    assert peeks("A♠")
    assert peeks("K♦")
    assert peeks("10♣")
    assert not peeks("9♥")


def test_the_dealers_blackjack_beats_everything_but_a_blackjack():
    dealer = ["A♠", "K♦"]
    assert blackjack_pay(20, ["A♥", "Q♣"], dealer) == 20, "возврат ставки"
    assert blackjack_pay(20, ["10♥", "9♣"], dealer) == 0
    assert blackjack_pay(20, ["10♥", "9♣", "2♦"], dealer) == 0, "21 не спасает"


def test_the_hand_against_the_dealer_pays_two_one_or_nothing():
    dealer = ["10♠", "8♦"]
    assert blackjack_pay(20, ["10♥", "9♣"], dealer) == 40
    assert blackjack_pay(20, ["10♥", "8♣"], dealer) == 20
    assert blackjack_pay(20, ["10♥", "7♣"], dealer) == 0
    assert blackjack_pay(20, ["10♥", "7♣", "9♦"], dealer) == 0, "перебор"
    # Перебор крупье платит всем, кто не перебрал
    busted = ["10♠", "6♦", "9♣"]
    assert blackjack_pay(20, ["5♥", "6♣"], busted) == 40
    assert blackjack_pay(20, ["10♥", "7♣", "9♦"], busted) == 0


# ---------- старшая карта ----------


def test_the_house_takes_five_percent_once():
    assert rake_of(200) == 10
    assert prize_of(200) == 190
    # Вниз: с трёх взносов по 50 комиссия 7, а не 7,5
    assert rake_of(150) == 7
    assert prize_of(150) == 143


def test_the_highest_rank_takes_the_pot_and_suits_are_equal():
    hands = {"1": "K♠", "2": "7♦", "3": "A♣"}
    assert leaders(hands) == ["3"]
    # Масти равны: две дамы — это пересдача, а не победа пики
    assert leaders({"1": "Q♠", "2": "Q♥"}) == ["1", "2"]
    assert high_rank("A♦") > high_rank("K♠") > high_rank("2♣")


def test_the_pot_splits_by_seat_when_there_is_nothing_left_to_deal():
    """Остаток раздаётся по одной фишке с младшего места."""
    assert split_pot(190, ["1", "2"]) == {"1": 95, "2": 95}
    assert split_pot(190, ["1", "2", "3"]) == {"1": 64, "2": 63, "3": 63}
    assert sum(split_pot(190, ["2", "3", "4"]).values()) == 190
    assert split_pot(7, ["4", "1"]) == {"1": 4, "4": 3}, "младшее место первым"


# ---------- столы ----------


def test_every_table_states_its_limits():
    """У стола есть наименьшая ставка, наибольшая и кратность."""
    for one in GAME_LIST:
        assert one.seats == 4, one.code
        assert 0 < one.least <= one.most, one.code
        assert one.step > 0 and one.least % one.step == 0, one.code
        assert one.accept > 0, one.code
    codes = [one.code for one in GAME_LIST]
    assert codes == ["dice", "roulette", "highcard", "blackjack"]
