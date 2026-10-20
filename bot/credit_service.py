"""Вклады и кредиты: открыть, снять, выдать и списать.

Правила и числа — в `bot.game.credit`, здесь дела. Держится служба на
трёх мыслях.

**Деньги банка живут на счёте.** Вклад открывают со счёта и возвращают
на счёт, кредит выдают на карту — то есть туда же. Наличные тут не
ходят: суммами в десятки тысяч в этом городе по улице не гуляют.

**Платёж списывает не час, а взгляд.** Часов у города нет, и будить
бота в день платежа нечем. Списывает всякий заход в любой дом города —
той же дверью, которой банк сводит год обслуживания карты
(`Player.settle_card`). Значит, боец не может «не прийти» и не
заплатить: приходить в город — это и есть прийти.

**Просрочка не прощается и не копится молча.** Не хватило на карте —
платёж становится долгом, и на него капает два процента в день. Деньги
появились — следующий же взгляд списывает долг вместе с пенями.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.bank import GOT, PAY
from bot.game.credit import (
    DAY,
    DEPOSIT_MIN,
    DEPOSIT_MONTHS,
    LOAN_ACCOUNT_AGE,
    LOAN_MAX,
    LOAN_MIN,
    LOAN_TERMS,
    MONTH,
    Deposit,
    Loan,
    days_between,
    deposit_income,
    fits_income,
    late_penalty,
    monthly_payment,
)
from bot.game.health import format_term, now_ts
from bot.models import Player

logger = logging.getLogger(__name__)


class CreditError(Exception):
    """Отказ банка, который показывают игроку как есть."""


# ---------- вклады ----------


async def open_deposit(
    db: Database, player: Player, amount: int, months: int,
    now: int | None = None,
) -> str:
    """Открыть вклад: деньги уходят со счёта и ждут срока."""
    moment = now_ts() if now is None else now
    if not player.has_account:
        raise CreditError("Вклад открывают на счёт: сперва открой счёт.")
    if months not in DEPOSIT_MONTHS:
        raise CreditError("Вклад открывают на один, два или три месяца.")
    if amount < DEPOSIT_MIN:
        raise CreditError(f"Вклад — от {DEPOSIT_MIN} 💰.")
    if player.account_balance < amount:
        raise CreditError(
            f"На счету {player.account_balance} 💰 — столько не положить."
        )
    player.account_balance -= amount
    player.note_card(
        -amount, PAY, "Vegas Банк", moment, about=f"Вклад на {months} мес."
    )
    await db.save_player(player)
    until = moment + MONTH * months
    number = await db.open_deposit(player.user_id, amount, months, moment, until)
    logger.info(
        "Боец %s открыл вклад %s: %s на %s мес.",
        player.user_id, number, amount, months,
    )
    return (
        f"Вклад открыт: {amount} 💰 на {months} мес. "
        f"К сроку банк вернёт {amount + deposit_income(amount, months)} 💰."
    )


async def close_deposit(
    db: Database, player: Player, deposit_id: int, now: int | None = None
) -> str:
    """Снять вклад. Досрочно — без процентов: срок был обещанием."""
    moment = now_ts() if now is None else now
    row = await db.deposit_row(deposit_id, player.user_id)
    if row is None:
        raise CreditError("Такого вклада у тебя нет.")
    ripe = row.ripe(moment)
    income = row.income if ripe else 0
    if not await db.close_deposit(deposit_id, player.user_id):  # pragma: no cover
        raise CreditError("Этот вклад уже закрыт.")
    player.account_balance += row.amount + income
    player.note_card(
        row.amount + income, GOT, "Vegas Банк", moment,
        about="Вклад закрыт" if ripe else "Вклад закрыт досрочно",
    )
    await db.save_player(player)
    logger.info(
        "Боец %s закрыл вклад %s: %s + %s",
        player.user_id, deposit_id, row.amount, income,
    )
    if ripe:
        return f"Вклад закрыт: на счёт вернулось {row.amount + income} 💰."
    return (
        f"Вклад закрыт досрочно: {row.amount} 💰 вернулись на счёт, "
        "проценты не начислены."
    )


# ---------- кредиты ----------


def weekly_income(player: Player) -> int:
    """Недельный доход бойца: жалованье его места. Без работы — ноль."""
    from bot.game.work import get_vacancy

    vacancy = get_vacancy(player.job_code) if player.works else None
    return vacancy.salary if vacancy else 0


def account_age(player: Player, now: int) -> int:
    """Сколько счёт у бойца. Ноль у счетов, открытых до этого правила."""
    if not player.has_account or not player.account_at:
        return LOAN_ACCOUNT_AGE
    return max(0, now - player.account_at)


def why_not(player: Player, now: int) -> str:
    """Почему кредита не дадут. Пусто — дадут.

    Порядок — от того, что нельзя исправить за минуту, к тому, что
    можно: работа, потом знакомство с банком, потом карта.
    """
    if not player.works:
        return (
            "Кредит дают при официальном трудоустройстве: платёж считают "
            "от жалованья. Работу берут в HR-агентстве."
        )
    if not player.has_account:
        return "Кредит выдают на карту: сперва открой счёт."
    left = LOAN_ACCOUNT_AGE - account_age(player, now)
    if left > 0:
        return (
            "Банк даёт в долг тому, кого знает хотя бы неделю. "
            f"Твоему счёту ещё рано: осталось {format_term(left)}."
        )
    if not player.has_card:
        return "Выдача моментальная и только на карту: закажи карту."
    if not player.card_works(now):
        return "Карта не обслужена: оплати год — и приходи за кредитом."
    return ""


async def take_loan(
    db: Database, player: Player, amount: int, months: int,
    now: int | None = None,
) -> str:
    """Взять кредит. Деньги ложатся на карту сразу, платежи — по месяцам."""
    moment = now_ts() if now is None else now
    if await db.loan_of(player.user_id) is not None:
        raise CreditError(
            "Кредит у тебя уже есть, и он один на бойца: платёж считают "
            "от дохода, а доход один."
        )
    denial = why_not(player, moment)
    if denial:
        raise CreditError(denial)
    if months not in LOAN_TERMS:
        raise CreditError(
            f"Срок кредита — от {LOAN_TERMS[0]} до {LOAN_TERMS[-1]} месяцев."
        )
    if not LOAN_MIN <= amount <= LOAN_MAX:
        raise CreditError(f"Сумма кредита — от {LOAN_MIN} до {LOAN_MAX} 💰.")
    payment = monthly_payment(amount, months)
    weekly = weekly_income(player)
    if not fits_income(payment, weekly):
        raise CreditError(
            f"Платёж {payment} 💰 в месяц больше твоего недельного дохода "
            f"({weekly} 💰). Банк столько не даст: возьми меньше или дольше."
        )
    due_at = moment + MONTH
    if not await db.open_loan(
        player.user_id, amount, months, payment, moment, due_at
    ):  # pragma: no cover - второй кредит
        raise CreditError("Кредит у тебя уже есть.")
    player.account_balance += amount
    player.note_card(
        amount, GOT, "Vegas Банк", moment, about=f"Кредит на {months} мес."
    )
    await db.save_player(player)
    logger.info(
        "Боец %s взял кредит: %s на %s мес., платёж %s",
        player.user_id, amount, months, payment,
    )
    return (
        f"Кредит выдан: {amount} 💰 на карту. Платёж {payment} 💰 в месяц, "
        f"первое списание через месяц."
    )


async def settle(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Списать то, что банк хочет прямо сейчас. Пусто — списывать нечего.

    Зовётся отовсюду, где боец показывается городу: своих часов у банка
    нет, и платёж приносит взгляд. Один заход может закрыть несколько
    месяцев — боец мог не появляться полгода, и тогда списывается всё,
    что накопилось, по порядку.
    """
    moment = now_ts() if now is None else now
    loan = await db.loan_of(player.user_id)
    if loan is None:
        return ""
    left, due_at = loan.left, loan.due_at
    owed, penalty, penalty_at = loan.owed, loan.penalty, loan.penalty_at
    taken = 0
    missed = 0

    def accrue(until: int) -> None:
        """Досчитать пени на нынешний долг до этого часа."""
        nonlocal penalty, penalty_at
        if not owed or not penalty_at:
            return
        days = days_between(penalty_at, until)
        if days:
            penalty += late_penalty(owed, days)
            penalty_at += days * DAY

    while left > 0 and due_at <= moment:
        # Пени считаются по дням, и к часу платежа они уже другие, чем
        # были к прошлому: досчитываем долг до этого часа, а не до
        # «сейчас» — иначе за дни после платежа капало бы задним числом
        accrue(due_at)
        # Просроченное платим первым: долг старше нового платежа
        want = owed + penalty + loan.payment
        if player.account_balance >= want:
            player.account_balance -= want
            taken += want
            left -= 1
            owed, penalty, penalty_at = 0, 0, 0
        else:
            owed += loan.payment
            missed += 1
            if not penalty_at:
                penalty_at = due_at
            left -= 1
        due_at += MONTH

    # Пени за то время, что долг висит после последнего платежа
    accrue(moment)

    # Деньги могли появиться позже: долг снимают первым же взглядом, не
    # дожидаясь следующего платежа — на него и капают пени
    if (owed or penalty) and player.account_balance >= owed + penalty:
        player.account_balance -= owed + penalty
        taken += owed + penalty
        owed, penalty, penalty_at = 0, 0, 0

    if taken:
        player.note_card(
            -taken, PAY, "Vegas Банк", moment, about="Платёж по кредиту"
        )
    if left <= 0 and not owed and not penalty:
        await db.close_loan(player.user_id)
        await db.save_player(player)
        logger.info("Боец %s закрыл кредит", player.user_id)
        return "Кредит выплачен целиком. Банк больше ничего не хочет."

    changed = (
        left != loan.left
        or due_at != loan.due_at
        or owed != loan.owed
        or penalty != loan.penalty
        or penalty_at != loan.penalty_at
    )
    if changed:
        await db.save_loan(
            player.user_id, max(0, left), due_at, owed, penalty, penalty_at
        )
    if taken or missed:
        await db.save_player(player)
    if owed:
        logger.warning(
            "Боец %s просрочил платёж по кредиту: долг %s, пени %s",
            player.user_id, owed, penalty,
        )
        said = (
            f"Платёж по кредиту не прошёл: на карте не хватило. "
            f"Долг {owed} 💰, пени капают."
        )
        return (
            f"С карты списано {taken} 💰. {said}" if taken else said
        )
    if taken:
        return f"С карты списан платёж по кредиту: {taken} 💰."
    return ""


async def repay(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Погасить просрочку руками, не дожидаясь взгляда."""
    moment = now_ts() if now is None else now
    loan = await db.loan_of(player.user_id)
    if loan is None:
        raise CreditError("Кредита у тебя нет.")
    if not loan.late:
        raise CreditError("Просрочки нет: платежи идут по графику.")
    days = days_between(loan.penalty_at, moment)
    penalty = loan.penalty + late_penalty(loan.owed, days)
    want = loan.owed + penalty
    if player.account_balance < want:
        raise CreditError(
            f"Долг с пенями — {want} 💰, а на счету "
            f"{player.account_balance} 💰."
        )
    player.account_balance -= want
    player.note_card(
        -want, PAY, "Vegas Банк", moment, about="Погашение просрочки"
    )
    if loan.left <= 0:
        await db.close_loan(player.user_id)
        said = "Просрочка погашена, и кредит закрыт целиком."
    else:
        await db.save_loan(player.user_id, loan.left, loan.due_at, 0, 0, 0)
        said = f"Просрочка погашена: {want} 💰. Платежи идут дальше."
    await db.save_player(player)
    logger.info("Боец %s погасил просрочку: %s", player.user_id, want)
    return said


__all__ = [
    "CreditError",
    "account_age",
    "close_deposit",
    "open_deposit",
    "repay",
    "settle",
    "take_loan",
    "weekly_income",
    "why_not",
]
