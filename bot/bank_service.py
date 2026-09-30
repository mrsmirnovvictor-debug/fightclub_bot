"""Vegas Банк: счёт, карта, банкомат и переводы.

Правила держит `bot.game.bank`, отказы — этот модуль. Дверей пять:
открыть счёт, выпустить карту, положить наличные, снять наличные и
перевести на чужой счёт по номеру.

**Деньги двигает база, а не питон.** Всякое движение — это `UPDATE` с
условием в том же запросе: «списать, если столько есть». Проверить в
питоне, а потом списать, значит оставить между проверкой и списанием щель
шириной в соседнее окно мини-аппа, — и туда пролезает покупка, после
которой на счёт ложится то, чего в наличных уже нет. Перевод между двумя
бойцами идёт одной сделкой на две строки: половины перевода не бывает.

**Обслуживание карты сводится на месте, как и полис.** Часов, которые
списывали бы сотню по будильнику, в клубе нет. Год подошёл — списываем
при первом же взгляде на карту; не хватило на счету — карта перестаёт
обслуживаться, но не закрывается. Списывается один год за подход, а не
все пропущенные: за время, в которое к услуге не приходили, клуб денег не
берёт.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.bank import (
    CARD,
    CARD_PRICE,
    CARD_YEAR_PRICE,
    CASH,
    PURSES,
    YEAR_SECONDS,
    make_number,
    tidy_number,
)
from bot.game.health import now_ts
from bot.models import Player

logger = logging.getLogger(__name__)

# Сколько раз разыграть номер, прежде чем сдаться. Номеров сто триллионов,
# и до второго круга дело не дойдёт ни при какой толпе; но молча отдавать
# бойцу чужой счёт нельзя, поэтому попытки считаны
NUMBER_TRIES = 8


class BankError(Exception):
    """Отказ, который показывают игроку как есть."""


@dataclass(frozen=True)
class Move:
    """Итог движения денег: сколько и куда."""

    amount: int
    cash: int  # сколько стало наличных
    balance: int  # сколько стало на счету


async def open_account(db: Database, player: Player, now: int | None = None) -> str:
    """Открыть счёт. Мгновенно, бесплатно и один на бойца."""
    if player.has_account:
        raise BankError("Счёт в Vegas Банке у тебя уже есть — он один на бойца.")

    for _ in range(NUMBER_TRIES):
        number = make_number()
        if await db.open_account(player.user_id, number):
            player.account_number = number
            logger.info("Банк открыл счёт %s бойцу %s", number, player.user_id)
            return number
        # Либо номер занят, либо счёт успели открыть в соседнем окне
        fresh = await db.get_player(player.user_id)
        if fresh is not None and fresh.has_account:
            player.account_number = fresh.account_number
            player.account_balance = fresh.account_balance
            raise BankError("Счёт в Vegas Банке у тебя уже есть — он один на бойца.")
    raise BankError("Банк не смог подобрать номер счёта. Попробуй ещё раз.")


async def issue_card(db: Database, player: Player, now: int | None = None) -> int:
    """Выпустить карту: сто за выпуск, первый год обслуживания внутри."""
    moment = now_ts() if now is None else now
    if not player.has_account:
        raise BankError("Сначала открой счёт: карта выпускается к нему.")
    if player.has_card:
        raise BankError("Карта Vegas Банка у тебя уже есть — она одна на бойца.")

    # Платят за выпуск со счёта или наличными — чем угодно, лишь бы
    # хватило. Картой заплатить нельзя по понятной причине: её ещё нет.
    # Сначала со счёта: деньги, принесённые в банк, для того и принесены
    where = CARD if player.account_balance >= CARD_PRICE else CASH
    if player.purse_money(where) < CARD_PRICE:
        raise BankError(
            f"Не хватает кредитов: выпуск карты стоит {CARD_PRICE} 💰, "
            f"а у тебя {player.credits} 💰 наличными "
            f"и {player.account_balance} 💰 на счету."
        )

    # Сначала выпуск, потом деньги: запрос сам не даст выпустить вторую
    # карту, и только пройдя его, мы знаем, что за эту сотню что-то дали
    paid_until = moment + YEAR_SECONDS
    if not await db.issue_card(player.user_id, moment, paid_until):
        raise BankError("Карта Vegas Банка у тебя уже есть — она одна на бойца.")
    player.card_at = moment
    player.card_paid_until = paid_until
    if where == CARD:
        player.account_balance -= CARD_PRICE
    else:
        player.credits -= CARD_PRICE
    await db.save_player(player)
    logger.info("Банк выпустил карту бойцу %s за %s", player.user_id, CARD_PRICE)
    return CARD_PRICE


async def settle_card(db: Database, player: Player, now: int | None = None) -> str:
    """Списать год обслуживания, если он подошёл. Пусто — списывать нечего.

    Зовётся отовсюду, где на карту смотрят или ею платят: своих часов у
    банка нет, как и у страховой.
    """
    moment = now_ts() if now is None else now
    taken = player.settle_card(moment)
    if taken:
        await db.save_player(player)
        logger.info("Банк списал год обслуживания с бойца %s", player.user_id)
        return (
            f"Обслуживание карты за год — {taken} 💰 со счёта. "
            f"Следующий год оплачен."
        )
    if player.has_card and not player.card_works(moment):
        return (
            f"Карта не обслуживается: на счету нет {CARD_YEAR_PRICE} 💰 "
            f"за год. Пополни счёт — карта заработает."
        )
    return ""


async def deposit(
    db: Database, player: Player, amount: int, now: int | None = None
) -> Move:
    """Положить наличные на счёт. Без комиссии."""
    _require_account(player)
    amount = _sane(amount)
    if amount > player.credits:
        raise BankError(
            f"Наличных {player.credits} 💰 — больше положить нечего."
        )
    if not await db.move_to_account(player.user_id, amount):
        raise BankError("Наличные изменились. Попробуй ещё раз.")
    player.credits -= amount
    player.account_balance += amount
    return Move(amount, player.credits, player.account_balance)


async def withdraw(
    db: Database, player: Player, amount: int, now: int | None = None
) -> Move:
    """Снять со счёта наличные в банкомате. Без комиссии."""
    _require_account(player)
    amount = _sane(amount)
    if amount > player.account_balance:
        raise BankError(
            f"На счету {player.account_balance} 💰 — больше снять нечего."
        )
    if not await db.move_to_cash(player.user_id, amount):
        raise BankError("Деньги на счету изменились. Попробуй ещё раз.")
    player.account_balance -= amount
    player.credits += amount
    return Move(amount, player.credits, player.account_balance)


async def send(
    db: Database, player: Player, said: str, amount: int, now: int | None = None
) -> tuple[Move, str]:
    """Перевести со своего счёта на чужой по номеру. Без комиссии."""
    _require_account(player)
    amount = _sane(amount)
    number = tidy_number(said)
    if not number:
        raise BankError("Это не похоже на номер счёта. Он выглядит так: VB-0000-0000-0000.")
    if number == player.account_number:
        raise BankError("Это твой собственный счёт.")
    if amount > player.account_balance:
        raise BankError(
            f"На счету {player.account_balance} 💰 — больше перевести нечего."
        )

    holder = await db.account_holder(number)
    if holder is None:
        raise BankError("Счёта с таким номером в Vegas Банке нет.")
    if not await db.send_to_account(player.user_id, number, amount):
        raise BankError("Деньги на счету изменились. Попробуй ещё раз.")

    player.account_balance -= amount
    logger.info(
        "Банк перевёл %s с бойца %s бойцу %s", amount, player.user_id, holder.user_id
    )
    return Move(amount, player.credits, player.account_balance), holder.nickname


async def choose_purse(db: Database, player: Player, purse: str) -> str:
    """Чем платить по умолчанию."""
    if purse not in PURSES:
        raise BankError("Платят наличными или картой, третьего не придумали.")
    if purse == CARD and not player.has_card:
        raise BankError("Карты Vegas Банка у тебя нет — платить ею нечем.")
    player.pay_from = purse
    await db.set_pay_from(player.user_id, purse)
    return purse


def _require_account(player: Player) -> None:
    if not player.has_account:
        raise BankError("Сначала открой счёт — он бесплатный и открывается сразу.")


def _sane(amount: int) -> int:
    if amount <= 0:
        raise BankError("Сумма должна быть больше нуля.")
    return amount


__all__ = [
    "BankError",
    "Move",
    "choose_purse",
    "deposit",
    "issue_card",
    "open_account",
    "send",
    "settle_card",
    "withdraw",
]
