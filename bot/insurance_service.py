"""Оформление и продление страхового полиса.

Три двери, и все три здесь: оформить, продлить и выключить продление.
Цену держит `bot.game.insurance`, отказы — этот модуль.

**Автопродление сводится на месте.** Часов, которые обходили бы базу и
списывали кредиты по будильнику, в клубе нет и не надо: полис нужен ровно
в двух местах — в страховой и в больнице, — и в обе минуты сюда заходят
через `settle`. Срок вышел и продление включено — списываем месяц и
продлеваем; кредитов не хватило — выключаем продление и говорим об этом
один раз, а не отказом на каждом шаге.

Подписчику полис бесплатен, и это не скидка: цена нулевая. Поэтому и
автопродление у него бесплатное — иначе подписка давала бы первый месяц,
а второй брала бы за кредиты.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.health import now_ts
from bot.game.insurance import POLICY_PRICE, Policy, extended
from bot.models import Player

logger = logging.getLogger(__name__)


class InsuranceError(Exception):
    """Отказ, который показывают игроку как есть."""


@dataclass
class PolicyDeal:
    """Итог похода в страховую: что теперь за полис и сколько он стоил."""

    policy: Policy
    price: int
    renewed: bool = False  # продлили живой, а не выписали новый
    free: bool = False  # подписчику — даром


def price_for(player: Player) -> int:
    """Сколько полис стоит этому бойцу. Подписчику — нисколько."""
    return 0 if player.is_pro() else POLICY_PRICE


async def buy_policy(
    db: Database, player: Player, now: int | None = None
) -> PolicyDeal:
    """Оформить полис или продлить его ещё на месяц."""
    moment = now_ts() if now is None else now
    price = price_for(player)
    if not player.can_afford(price):
        raise InsuranceError(
            f"Не хватает кредитов: полис стоит {price} 💰, "
            f"а на счету {player.credits} 💰."
        )

    was = player.policy
    policy = extended(was, moment)
    renewed = was is not None and was.is_active(moment)

    if price:
        player.pay(price)
        await db.save_player(player)
    await db.set_policy(player.user_id, policy)
    player.policy = policy
    logger.info(
        "Полис бойцу %s до %s за %s (%s)",
        player.user_id,
        policy.until,
        price,
        "продление" if renewed else "новый",
    )
    return PolicyDeal(policy=policy, price=price, renewed=renewed, free=not price)


async def set_renew(
    db: Database, player: Player, auto_renew: bool
) -> Policy:
    """Включить или выключить автопродление."""
    policy = player.policy
    if policy is None:
        raise InsuranceError("Полиса нет — продлевать нечего.")
    if policy.auto_renew == auto_renew:
        return policy
    fresh = Policy(
        issued=policy.issued, until=policy.until, auto_renew=auto_renew
    )
    await db.set_policy_renew(player.user_id, auto_renew)
    player.policy = fresh
    logger.info(
        "Автопродление полиса бойца %s: %s",
        player.user_id,
        "включено" if auto_renew else "выключено",
    )
    return fresh


async def settle(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Свести автопродление. Возвращает, что сказать бойцу, или пусто.

    Зовётся оттуда, где полис смотрят или предъявляют, — и ровно поэтому
    молчит, когда продлевать нечего: это не действие игрока, а сверка
    часов, и говорить о ней стоит только когда что-то случилось.
    """
    policy = player.policy
    if policy is None or not policy.due(now_ts() if now is None else now):
        return ""

    moment = now_ts() if now is None else now
    price = price_for(player)
    if not player.can_afford(price):
        # Выключаем, а не пробуем каждый раз: иначе боец, которому не
        # хватает трёхсот, платил бы за полис первым же кредитом, пришедшим
        # на счёт, — и узнавал об этом по пустому кошельку
        await set_renew(db, player, False)
        return (
            f"Автопродление полиса выключено: на счету было "
            f"{player.credits} 💰, а продление стоит {price} 💰."
        )

    renewed = policy.renewed(moment)
    if price:
        player.pay(price)
        await db.save_player(player)
    await db.set_policy(player.user_id, renewed)
    player.policy = renewed
    logger.info("Полис бойца %s продлён сам до %s", player.user_id, renewed.until)
    tail = "" if price else " (по подписке — даром)"
    return f"Полис продлён автоматически: списано {price} 💰{tail}."


__all__ = [
    "InsuranceError",
    "PolicyDeal",
    "buy_policy",
    "price_for",
    "set_renew",
    "settle",
]
