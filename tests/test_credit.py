"""Вклады и кредиты: за что банк платит и за что берёт.

Порядок проверок — по тому, что дороже сломать:

1. **Арифметика.** Доход вклада и аннуитетный платёж считаются в одном
   месте, и у страницы своей формулы нет. Если разойдутся эти числа,
   банк покажет одну цену, а спишет другую.
2. **Двери кредита.** Работа, счёт неделю, карта, платёж по доходу.
   Каждая заперта на сервере, а не прятанием кнопки.
3. **Списание и просрочка.** Платёж приносит взгляд, а не часы; не
   хватило — долг и два процента в день, и ни то, ни другое не прощается
   молча.
"""

import pytest

from bot.bank_service import issue_card, open_account
from bot.credit_service import (
    CreditError,
    account_age,
    close_deposit,
    open_deposit,
    repay,
    settle,
    take_loan,
    weekly_income,
    why_not,
)
from bot.database import Database
from bot.game.bank import CARD_PRICE
from bot.game.classes import Stats
from bot.game.credit import (
    DAY,
    DEPOSIT_MIN,
    DEPOSIT_MONTHS,
    DEPOSIT_RATE,
    LATE_RATE,
    LOAN_ACCOUNT_AGE,
    LOAN_MAX,
    LOAN_MIN,
    LOAN_RATE,
    LOAN_TERMS,
    MONTH,
    YEAR,
    biggest_loan,
    days_between,
    deposit_income,
    fits_income,
    late_penalty,
    monthly_payment,
    overpay,
    schedule,
    total_cost,
)
from bot.models import Player

NOW = 1_800_000_000
BANK = "bank"
# Самое щедрое место города: только оно и выносит минимальный кредит
RICH = "bank_head"


def make_player(user_id: int = 42, credits: int = 1_000) -> Player:
    return Player(
        user_id=user_id, nickname="Тайлер", class_code="warrior", level=8,
        credits=credits, location=BANK,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def with_money(db: Database, money: int, now: int = NOW) -> Player:
    """Боец со счётом, на котором столько-то кредитов."""
    player = make_player()
    await db.save_player(player)
    await open_account(db, player, now)
    player.account_balance = money
    await db.save_player(player)
    return player


async def borrower(
    db: Database, job: str = RICH, now: int = NOW, opened: int | None = None
) -> Player:
    """Боец, которому банк готов дать: работа, счёт неделю, карта."""
    born = now - LOAN_ACCOUNT_AGE if opened is None else opened
    player = make_player(credits=CARD_PRICE)
    await db.save_player(player)
    await open_account(db, player, born)
    await issue_card(db, player, born)
    player.job_code = job
    await db.save_player(player)
    return player


# ---------- арифметика вклада ----------


def test_the_deposit_pays_simple_interest_for_the_months_it_sat():
    """Тринадцать процентов годовых — это процент за год, а не за срок."""
    year = DEPOSIT_MIN * DEPOSIT_RATE

    for months in DEPOSIT_MONTHS:
        assert deposit_income(DEPOSIT_MIN, months) == int(year * months / YEAR)

    # Три месяца под 13% с тысячи — тридцать два кредита
    assert deposit_income(1_000, 3) == 32
    assert deposit_income(100_000, 3) == 3_250


def test_a_deposit_of_nothing_brings_nothing():
    assert deposit_income(0, 3) == 0
    assert deposit_income(1_000, 0) == 0
    assert deposit_income(-1_000, 3) == 0


# ---------- арифметика кредита ----------


def test_the_annuity_payment_closes_the_loan_and_not_a_credit_less():
    """Аннуитет равный, и за срок он обязан закрыть долг с процентами."""
    for months in LOAN_TERMS:
        payment = monthly_payment(LOAN_MIN, months)
        # Округление вверх: иначе последний платёж был бы больше прочих
        assert payment * months >= LOAN_MIN
        # И не настолько вверх, чтобы переплатить лишний платёж
        assert (payment - 1) * months < total_cost(LOAN_MIN, months)


def test_the_payment_follows_the_formula_the_bank_names():
    """S · i / (1 − (1 + i)^−n), округление вверх. Числа сверены руками."""
    rate = LOAN_RATE / YEAR

    for amount, months in ((10_000, 4), (50_000, 12), (100_000, 6)):
        exact = amount * rate / (1 - (1 + rate) ** (-months))
        assert monthly_payment(amount, months) == int(exact) + 1

    assert monthly_payment(10_000, 4) == 2_632
    assert monthly_payment(10_000, 12) == 951


def test_a_longer_term_means_a_smaller_payment_and_a_bigger_overpay():
    """Главный выбор калькулятора — и он должен быть виден в числах."""
    payments = [monthly_payment(LOAN_MIN, one) for one in LOAN_TERMS]
    overpays = [overpay(LOAN_MIN, one) for one in LOAN_TERMS]

    assert payments == sorted(payments, reverse=True)
    assert overpays == sorted(overpays)
    assert all(one > 0 for one in overpays)


def test_the_ceiling_is_the_biggest_sum_whose_payment_the_income_carries():
    """Потолок считает банк: сотней больше — и платёж уже не по доходу."""
    weekly = 1_500

    for months in LOAN_TERMS:
        top = biggest_loan(weekly, months)
        if not top:
            # Не дают вовсе — значит, и минимум не по доходу
            assert not fits_income(monthly_payment(LOAN_MIN, months), weekly)
            continue
        assert LOAN_MIN <= top <= LOAN_MAX
        assert fits_income(monthly_payment(top, months), weekly)
        assert not fits_income(monthly_payment(top + 100, months), weekly)


def test_without_income_there_is_no_ceiling_at_all():
    assert biggest_loan(0, 12) == 0
    assert not fits_income(951, 0)


def test_the_schedule_starts_a_month_after_the_money_and_goes_month_by_month():
    rows = schedule(LOAN_MIN, 4, NOW)

    assert len(rows) == 4
    assert [when for when, _ in rows] == [NOW + MONTH * one for one in (1, 2, 3, 4)]
    assert {payment for _, payment in rows} == {monthly_payment(LOAN_MIN, 4)}


# ---------- пени ----------


def test_the_penalty_is_two_percent_of_the_debt_a_day():
    assert late_penalty(1_000, 1) == int(1_000 * LATE_RATE)
    assert late_penalty(1_000, 10) == 200
    # Месяц молчания дороже самого платежа
    assert late_penalty(1_000, 60) > 1_000


def test_an_unfinished_day_does_not_count_and_nothing_counts_backwards():
    assert days_between(NOW, NOW + DAY - 1) == 0
    assert days_between(NOW, NOW + DAY) == 1
    assert days_between(NOW, NOW - DAY) == 0
    assert days_between(0, NOW) == 0, "часа начала нет — и пеней нет"


# ---------- вклад ----------


async def test_a_deposit_moves_the_money_off_the_account(db):
    player = await with_money(db, 5_000)

    said = await open_deposit(db, player, 2_000, 3, NOW)

    assert player.account_balance == 3_000
    rows = await db.deposits_of(player.user_id)
    assert len(rows) == 1
    assert (rows[0].amount, rows[0].months) == (2_000, 3)
    assert rows[0].paid_until == NOW + MONTH * 3
    assert "2000" in said.replace(" ", "")


async def test_the_bank_will_not_take_a_deposit_it_has_no_money_for(db):
    player = await with_money(db, 500)

    with pytest.raises(CreditError, match="столько не положить"):
        await open_deposit(db, player, 2_000, 1, NOW)

    assert player.account_balance == 500
    assert await db.deposits_of(player.user_id) == []


async def test_a_deposit_smaller_than_the_minimum_is_not_a_deposit(db):
    player = await with_money(db, 5_000)

    with pytest.raises(CreditError, match=str(DEPOSIT_MIN)):
        await open_deposit(db, player, DEPOSIT_MIN - 1, 1, NOW)


@pytest.mark.parametrize("months", [0, 4, 12, -1])
async def test_the_term_of_a_deposit_is_one_two_or_three_months(db, months):
    player = await with_money(db, 5_000)

    with pytest.raises(CreditError, match="один, два или три"):
        await open_deposit(db, player, 2_000, months, NOW)


async def test_a_deposit_needs_an_account_because_it_lives_on_one(db):
    player = make_player()
    await db.save_player(player)

    with pytest.raises(CreditError, match="открой счёт"):
        await open_deposit(db, player, 2_000, 1, NOW)


async def test_a_ripe_deposit_comes_back_with_its_interest(db):
    player = await with_money(db, 5_000)
    await open_deposit(db, player, 2_000, 3, NOW)
    one = (await db.deposits_of(player.user_id))[0]

    said = await close_deposit(db, player, one.id, NOW + MONTH * 3)

    assert player.account_balance == 3_000 + 2_000 + deposit_income(2_000, 3)
    assert await db.deposits_of(player.user_id) == []
    assert "досрочно" not in said


async def test_a_deposit_taken_early_brings_the_money_and_no_interest(db):
    """Срок вклада — обещание: банк платит за срок, а срок не вышел."""
    player = await with_money(db, 5_000)
    await open_deposit(db, player, 2_000, 3, NOW)
    one = (await db.deposits_of(player.user_id))[0]

    said = await close_deposit(db, player, one.id, NOW + MONTH * 3 - 1)

    assert player.account_balance == 5_000, "проценты не начислены"
    assert "досрочно" in said


async def test_a_deposit_of_a_stranger_is_not_yours_to_close(db):
    mine = await with_money(db, 5_000)
    await open_deposit(db, mine, 2_000, 1, NOW)
    one = (await db.deposits_of(mine.user_id))[0]
    other = make_player(user_id=7)
    await db.save_player(other)

    with pytest.raises(CreditError, match="нет"):
        await close_deposit(db, other, one.id, NOW)

    assert len(await db.deposits_of(mine.user_id)) == 1


async def test_deposits_are_many_because_a_deposit_is_a_deal_not_a_purse(db):
    player = await with_money(db, 10_000)

    for months in DEPOSIT_MONTHS:
        await open_deposit(db, player, DEPOSIT_MIN, months, NOW)

    rows = await db.deposits_of(player.user_id)
    assert [one.months for one in rows] == list(DEPOSIT_MONTHS)
    assert player.account_balance == 10_000 - DEPOSIT_MIN * 3


async def test_the_deposit_shows_up_in_the_card_statement(db):
    player = await with_money(db, 5_000)
    await open_deposit(db, player, 2_000, 1, NOW)

    log = await db.card_log(player.user_id, 0, NOW + 1)

    assert any(one.amount == -2_000 and "Вклад" in one.about for one in log)


# ---------- двери кредита ----------


def test_the_weekly_income_is_the_salary_of_the_place_not_the_pocket():
    from bot.game.work import get_vacancy

    idle = make_player(credits=100_000)
    assert weekly_income(idle) == 0

    worker = make_player()
    worker.job_code = RICH
    assert weekly_income(worker) == get_vacancy(RICH).salary


async def test_without_a_job_there_is_no_loan_at_all(db):
    player = await with_money(db, 0)

    assert "трудоустройстве" in why_not(player, NOW)
    with pytest.raises(CreditError, match="трудоустройстве"):
        await take_loan(db, player, LOAN_MIN, 12, NOW)


async def test_a_loan_needs_an_account_because_it_is_paid_onto_a_card(db):
    player = make_player()
    player.job_code = RICH
    await db.save_player(player)

    assert "открой счёт" in why_not(player, NOW)
    with pytest.raises(CreditError, match="открой счёт"):
        await take_loan(db, player, LOAN_MIN, 12, NOW)


async def test_the_bank_lends_to_an_account_it_has_known_a_week(db):
    player = await borrower(db, opened=NOW - LOAN_ACCOUNT_AGE + DAY)

    assert "неделю" in why_not(player, NOW)
    with pytest.raises(CreditError, match="неделю"):
        await take_loan(db, player, LOAN_MIN, 12, NOW)

    # Через день счёту ровно неделя — и дверь открыта
    assert why_not(player, NOW + DAY) == ""


async def test_an_account_older_than_the_rule_counts_as_old_enough(db):
    """Счета, открытые до этого правила, часа открытия не помнят."""
    player = await borrower(db)
    player.account_at = 0

    assert account_age(player, NOW) == LOAN_ACCOUNT_AGE
    assert why_not(player, NOW) == ""


async def test_a_loan_without_a_card_has_nowhere_to_be_paid_to(db):
    player = make_player()
    player.job_code = RICH
    await db.save_player(player)
    await open_account(db, player, NOW - LOAN_ACCOUNT_AGE)

    assert "закажи карту" in why_not(player, NOW)


async def test_a_card_that_is_not_served_is_not_a_card_for_a_loan(db):
    player = await borrower(db)

    late = player.card_paid_until + DAY

    assert "не обслужена" in why_not(player, late)


# ---------- выдача ----------


async def test_the_loan_lands_on_the_card_the_same_minute(db):
    player = await borrower(db)
    months = LOAN_TERMS[-1]
    amount = biggest_loan(weekly_income(player), months)

    said = await take_loan(db, player, amount, months, NOW)

    assert player.account_balance == amount, "выдача моментальная и на карту"
    loan = await db.loan_of(player.user_id)
    assert loan is not None
    assert (loan.amount, loan.months) == (amount, months)
    assert loan.payment == monthly_payment(amount, months)
    assert loan.left == months
    assert loan.due_at == NOW + MONTH, "первое списание ровно через месяц"
    assert "моментал" in said or "на карту" in said


async def test_the_issue_is_written_into_the_card_statement(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, LOAN_TERMS[-1], NOW)

    log = await db.card_log(player.user_id, 0, NOW + 1)

    assert any(one.amount == LOAN_MIN and "Кредит" in one.about for one in log)


async def test_a_payment_bigger_than_the_weekly_income_is_refused(db):
    """Главное условие: платёж не больше недельного дохода."""
    player = await borrower(db)
    short = LOAN_TERMS[0]
    assert monthly_payment(LOAN_MIN, short) > weekly_income(player)

    with pytest.raises(CreditError, match="недельного дохода"):
        await take_loan(db, player, LOAN_MIN, short, NOW)

    assert await db.loan_of(player.user_id) is None
    assert player.account_balance == 0


async def test_a_hundred_credits_over_the_ceiling_is_already_too_much(db):
    player = await borrower(db)
    months = LOAN_TERMS[-1]
    top = biggest_loan(weekly_income(player), months)

    with pytest.raises(CreditError, match="недельного дохода"):
        await take_loan(db, player, top + 100, months, NOW)


@pytest.mark.parametrize("amount", [LOAN_MIN - 1, LOAN_MAX + 1, 0])
async def test_the_sum_of_a_loan_stays_between_ten_and_a_hundred_thousand(
    db, amount
):
    player = await borrower(db)

    with pytest.raises(CreditError, match="Сумма кредита"):
        await take_loan(db, player, amount, LOAN_TERMS[-1], NOW)


@pytest.mark.parametrize("months", [0, 3, 13])
async def test_the_term_of_a_loan_is_four_to_twelve_months(db, months):
    player = await borrower(db)

    with pytest.raises(CreditError, match="Срок кредита"):
        await take_loan(db, player, LOAN_MIN, months, NOW)


async def test_the_loan_is_one_per_fighter_because_the_income_is_one(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, LOAN_TERMS[-1], NOW)

    with pytest.raises(CreditError, match="уже есть"):
        await take_loan(db, player, LOAN_MIN, LOAN_TERMS[-1], NOW)


# ---------- списание ----------


async def test_nothing_is_taken_before_the_month_is_out(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    player.account_balance = 10_000

    assert await settle(db, player, NOW + MONTH - 1) == ""
    assert player.account_balance == 10_000


async def test_the_payment_goes_off_the_card_exactly_a_month_later(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment

    said = await settle(db, player, NOW + MONTH)

    assert player.account_balance == LOAN_MIN - payment
    loan = await db.loan_of(player.user_id)
    assert loan.left == 11
    assert loan.due_at == NOW + MONTH * 2, "дальше месяц в месяц"
    assert not loan.late
    assert str(payment) in said.replace(" ", "")


async def test_one_look_after_half_a_year_settles_every_month_it_missed(db):
    """Часов у города нет: платежи приносит взгляд, и сразу все."""
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = LOAN_MIN + payment * 6

    await settle(db, player, NOW + MONTH * 6)

    loan = await db.loan_of(player.user_id)
    assert loan.left == 6
    assert not loan.late
    assert player.account_balance == LOAN_MIN


async def test_the_last_payment_closes_the_loan_for_good(db):
    player = await borrower(db)
    months = 12
    await take_loan(db, player, LOAN_MIN, months, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    # Ровно на все платежи: кредит дороже выданного, и выданного не хватит
    player.account_balance = payment * months

    said = await settle(db, player, NOW + MONTH * months)

    assert await db.loan_of(player.user_id) is None
    assert player.account_balance == 0
    assert payment * months == total_cost(LOAN_MIN, months)
    assert "выплачен целиком" in said


async def test_a_payment_the_card_cannot_cover_becomes_a_debt(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = 0

    said = await settle(db, player, NOW + MONTH)

    loan = await db.loan_of(player.user_id)
    assert loan.late and loan.owed == payment
    assert loan.penalty == 0, "в день платежа пеней ещё нет"
    assert loan.penalty_at == NOW + MONTH
    assert "не прошёл" in said


async def test_the_debt_grows_by_two_percent_a_day(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = 0
    await settle(db, player, NOW + MONTH)

    await settle(db, player, NOW + MONTH + DAY * 10)

    loan = await db.loan_of(player.user_id)
    assert loan.owed == payment
    assert loan.penalty == late_penalty(payment, 10)
    assert loan.debt == payment + late_penalty(payment, 10)


async def test_the_debt_is_taken_the_first_look_after_the_money_appears(db):
    """Долг не ждёт следующего платежа: деньги появились — списали."""
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = 0
    await settle(db, player, NOW + MONTH)
    debt = payment + late_penalty(payment, 5)
    player.account_balance = debt

    said = await settle(db, player, NOW + MONTH + DAY * 5)

    loan = await db.loan_of(player.user_id)
    assert not loan.late and loan.penalty == 0
    assert player.account_balance == 0
    assert "списано" in said or "списан" in said


async def test_the_overdue_payment_is_taken_before_the_new_one(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = 0
    await settle(db, player, NOW + MONTH)
    # На второй платёж денег ровно на оба и на пени за месяц
    debt = payment + late_penalty(payment, 30)
    player.account_balance = debt + payment

    await settle(db, player, NOW + MONTH * 2)

    loan = await db.loan_of(player.user_id)
    assert not loan.late
    assert loan.left == 10
    assert player.account_balance == 0


async def test_the_penalty_of_each_month_counts_from_its_own_due_day(db):
    """Пени капают со дня платежа, а не задним числом от первого долга."""
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = 0

    await settle(db, player, NOW + MONTH * 2)

    loan = await db.loan_of(player.user_id)
    assert loan.owed == payment * 2
    # За первый платёж — месяц пеней, за второй — ни дня
    assert loan.penalty == late_penalty(payment, 30)


async def test_a_look_without_a_loan_asks_for_nothing(db):
    player = await with_money(db, 1_000)

    assert await settle(db, player, NOW + MONTH * 12) == ""
    assert player.account_balance == 1_000


# ---------- погашение руками ----------


async def test_the_debt_can_be_paid_by_hand_without_waiting_for_a_look(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = 0
    await settle(db, player, NOW + MONTH)
    later = NOW + MONTH + DAY * 3
    debt = payment + late_penalty(payment, 3)
    player.account_balance = debt + 500

    said = await repay(db, player, later)

    loan = await db.loan_of(player.user_id)
    assert not loan.late and loan.penalty == 0
    assert player.account_balance == 500
    assert "погашена" in said


async def test_a_debt_bigger_than_the_account_is_not_paid_in_half(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    player.account_balance = 0
    await settle(db, player, NOW + MONTH)
    player.account_balance = 10

    with pytest.raises(CreditError, match="на счету"):
        await repay(db, player, NOW + MONTH + DAY)

    assert player.account_balance == 10
    assert (await db.loan_of(player.user_id)).late


async def test_there_is_nothing_to_repay_while_the_schedule_holds(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)

    with pytest.raises(CreditError, match="Просрочки нет"):
        await repay(db, player, NOW)


async def test_repaying_has_no_sense_without_a_loan(db):
    player = await with_money(db, 1_000)

    with pytest.raises(CreditError, match="Кредита у тебя нет"):
        await repay(db, player, NOW)


async def test_the_last_debt_paid_by_hand_closes_the_loan(db):
    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    player.account_balance = 0
    # Все двенадцать платежей просрочены: график кончился, долг остался
    await settle(db, player, NOW + MONTH * 12)
    loan = await db.loan_of(player.user_id)
    assert loan.left == 0 and loan.late

    player.account_balance = loan.debt

    said = await repay(db, player, NOW + MONTH * 12)

    assert await db.loan_of(player.user_id) is None
    assert player.account_balance == 0
    assert "закрыт целиком" in said


# ---------- витрина ----------


async def test_the_bank_screen_shows_the_whole_calculator(db):
    from bot.webapp.bank import build_bank

    player = await borrower(db)
    body = build_bank(
        player, NOW,
        deposits=await db.deposits_of(player.user_id),
        loan=await db.loan_of(player.user_id),
    )
    loan = body["credit"]["loan"]

    assert [tab["code"] for tab in body["tabs"]] == [
        "new", "accounts", "deposits", "loans", "atm", "log",
    ]
    assert body["credit"]["deposit"]["min"] == DEPOSIT_MIN
    assert body["credit"]["deposit"]["rate"] == 13
    assert [one["months"] for one in body["credit"]["deposit"]["terms"]] == [1, 2, 3]
    assert loan["rate"] == 25 and loan["late_rate"] == 2
    assert loan["week_days"] == 7
    assert loan["weekly"] == weekly_income(player)
    assert loan["why"] == "" and loan["can_take"]
    assert [one["months"] for one in loan["calc"]] == list(LOAN_TERMS)
    for row in loan["calc"]:
        assert row["payment"] == monthly_payment(
            row["top"] or LOAN_MIN, row["months"]
        )
        assert row["fits"] == bool(row["top"])
        if row["fits"]:
            assert row["payment"] <= loan["weekly"]


async def test_the_screen_shows_the_deposit_and_the_loan_a_fighter_has(db):
    from bot.webapp.bank import build_bank

    player = await borrower(db)
    await take_loan(db, player, LOAN_MIN, 12, NOW)
    await open_deposit(db, player, DEPOSIT_MIN, 3, NOW)

    body = build_bank(
        player, NOW + MONTH * 3,
        deposits=await db.deposits_of(player.user_id),
        loan=await db.loan_of(player.user_id),
    )
    rows = body["credit"]["deposit"]["rows"]
    mine = body["credit"]["loan"]["mine"]

    assert len(rows) == 1 and rows[0]["ripe"]
    assert rows[0]["total"] == DEPOSIT_MIN + deposit_income(DEPOSIT_MIN, 3)
    assert mine["amount"] == LOAN_MIN
    assert mine["payment"] == monthly_payment(LOAN_MIN, 12)
    assert not body["credit"]["loan"]["can_take"], "кредит один на бойца"


async def test_the_screen_names_the_closed_door_instead_of_the_calculator(db):
    from bot.webapp.bank import build_bank

    player = await with_money(db, 0)

    loan = build_bank(player, NOW)["credit"]["loan"]

    assert "трудоустройстве" in loan["why"]
    assert not loan["can_take"]
    assert loan["weekly"] == 0
    assert all(not row["fits"] for row in loan["calc"])


# ---------- банк через ручку ----------
#
# Здесь время настоящее: ручки берут `now_ts()`, а не `NOW`. Поэтому
# состояние собирается от «сейчас», а не от круглого часа тестов.


@pytest.fixture
async def client(db):
    from aiohttp.test_utils import TestClient, TestServer

    from bot.config import Config
    from bot.webapp.server import create_app
    from tests.test_inventory import FakeBot
    from tests.test_webapp import TOKEN

    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    from tests.test_webapp import make_init_data

    return {"X-Telegram-Init-Data": make_init_data(user_id)}


async def test_a_deposit_is_opened_and_taken_back_by_the_handle(client, db):
    from bot.game.health import now_ts

    player = await with_money(db, 5_000, now_ts())

    body = await (
        await client.post(
            "/api/bank",
            json={"action": "open_deposit", "amount": 2_000, "months": 3},
            headers=headers(),
        )
    ).json()
    rows = body["credit"]["deposit"]["rows"]
    assert body["account"]["balance"] == 3_000
    assert len(rows) == 1 and rows[0]["amount"] == 2_000
    assert not rows[0]["ripe"]

    body = await (
        await client.post(
            "/api/bank",
            json={"action": "close_deposit", "id": rows[0]["id"]},
            headers=headers(),
        )
    ).json()
    assert body["account"]["balance"] == 5_000, "досрочно — без процентов"
    assert body["credit"]["deposit"]["rows"] == []


async def test_the_handle_passes_the_banks_refusal_as_it_is(client, db):
    from bot.game.health import now_ts

    await with_money(db, 100, now_ts())

    answer = await client.post(
        "/api/bank",
        json={"action": "open_deposit", "amount": 2_000, "months": 3},
        headers=headers(),
    )

    assert answer.status == 409
    assert "не положить" in (await answer.json())["error"]


async def test_a_loan_is_taken_by_the_handle_and_lands_on_the_card(client, db):
    from bot.game.health import now_ts

    moment = now_ts()
    player = await borrower(db, now=moment)
    months = LOAN_TERMS[-1]
    amount = biggest_loan(weekly_income(player), months)

    body = await (
        await client.post(
            "/api/bank",
            json={"action": "loan", "amount": amount, "months": months},
            headers=headers(),
        )
    ).json()
    mine = body["credit"]["loan"]["mine"]

    assert body["account"]["balance"] == amount
    assert mine["amount"] == amount and mine["pays_left"] == months
    assert not mine["late"]
    assert not body["credit"]["loan"]["can_take"]


async def test_the_handle_refuses_a_loan_to_the_unemployed(client, db):
    from bot.game.health import now_ts

    await with_money(db, 0, now_ts())

    answer = await client.post(
        "/api/bank",
        json={"action": "loan", "amount": LOAN_MIN, "months": 12},
        headers=headers(),
    )

    assert answer.status == 409
    assert "трудоустройстве" in (await answer.json())["error"]


async def test_every_door_of_the_city_brings_the_payment(client, db):
    """Своих часов у банка нет: списывает тот заход, который случился."""
    from bot.game.health import now_ts

    moment = now_ts()
    player = await borrower(db, now=moment - MONTH * 2)
    await take_loan(db, player, LOAN_MIN, 12, moment - MONTH)
    payment = (await db.loan_of(player.user_id)).payment
    player.account_balance = LOAN_MIN
    await db.save_player(player)

    # Заход в банк — такая же дверь города, как любая другая
    body = await (await client.get("/api/bank", headers=headers())).json()

    assert body["account"]["balance"] == LOAN_MIN - payment
    assert body["credit"]["loan"]["mine"]["pays_left"] == 11
