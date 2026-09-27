"""Оформление и продление страхового полиса.

Три двери, и все три здесь: оформить, продлить и выключить продление.
Цену держит `bot.game.insurance`, отказы — этот модуль.

**Автопродление сводится на месте.** Часов, которые обходили бы базу и
списывали кредиты по будильнику, в клубе нет и не надо: полис нужен ровно
в двух местах — в страховой и в больнице, — и в обе минуты сюда заходят
через `settle`. Срок вышел и продление включено — списываем месяц и
продлеваем; кредитов не хватило — выключаем продление и говорим об этом
один раз, а не отказом на каждом шаге.

**Подписка держит полис сама, и тем же `settle`.** Пока PRO жива, срок
полиса дотянут до её последнего часа — даром и без нажатий. Прибавить
подписка ничего не может: она выравнивает срок по своему концу, а не
кладёт месяц сверху. Отсюда и цена — триста всегда, подписчику тоже:
он платит не за полис, который у него есть, а за время после подписки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.health import now_ts
from bot.game.insurance import POLICY_PRICE, Policy, extended, pro_cover
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


def price_for(player: Player) -> int:
    """Сколько месяц полиса стоит этому бойцу.

    Одна цена на всех, и подписка её не меняет — ровно потому, что
    подписка даёт не месяц, а срок. Бесплатный месяц подписчик мог бы
    взять сколько угодно раз, и эти месяцы жили бы после самой подписки.
    Платит он здесь за время после её конца, а не за то, что уже имеет.
    """
    return POLICY_PRICE


async def buy_policy(
    db: Database, player: Player, now: int | None = None
) -> PolicyDeal:
    """Оформить полис или продлить его ещё на месяц.

    Месяц всегда ложится поверх того срока, что есть, — в том числе
    поверх срока подписки. Подписчик, купивший месяц, получает месяц
    после подписки, а не вместо неё.
    """
    moment = now_ts() if now is None else now
    price = price_for(player)
    if not player.can_afford(price):
        raise InsuranceError(
            f"Не хватает кредитов: полис стоит {price} 💰, "
            f"а на счету {player.credits} 💰."
        )

    # Сначала выравниваем срок по подписке, и только потом кладём месяц
    # сверху: иначе купленный месяц считался бы от старого конца и часть
    # его уходила бы под то время, которое и так держит подписка
    await cover_by_pro(db, player, moment)

    was = player.policy
    policy = extended(was, moment)
    renewed = was is not None and was.is_active(moment)

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
    return PolicyDeal(policy=policy, price=price, renewed=renewed)


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
    """Свести полис с часами. Возвращает, что сказать бойцу, или пусто.

    Двух дел: дотянуть срок по живой подписке и продлить кончившийся полис
    за кредиты. Первое бесплатно и всегда, второе — только если
    автопродление включено.

    Зовётся оттуда, где полис смотрят или предъявляют, — и ровно поэтому
    молчит, когда делать нечего: это не действие игрока, а сверка часов, и
    говорить о ней стоит только когда что-то случилось.
    """
    moment = now_ts() if now is None else now

    # Подписка идёт первой: пока она жива, платить не за что
    by_pro = await cover_by_pro(db, player, moment)
    if by_pro:
        return by_pro

    policy = player.policy
    if policy is None or not policy.due(moment):
        return ""

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
    player.pay(price)
    await db.save_player(player)
    await db.set_policy(player.user_id, renewed)
    player.policy = renewed
    logger.info("Полис бойца %s продлён сам до %s", player.user_id, renewed.until)
    return f"Полис продлён автоматически: списано {price} 💰."


async def cover_by_pro(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Дотянуть срок полиса до конца подписки. Пусто — тянуть нечего.

    Это и есть «с PRO полис даётся автоматически»: не выдача месяца, а
    выравнивание срока по концу подписки. Зовётся и при выдаче самой
    подписки, и здесь — второе догоняет тех, у кого подписка началась
    раньше этого правила, и тех, кому её продлили мимо магазина.
    """
    moment = now_ts() if now is None else now
    cover = pro_cover(player.policy, player.pro_until, moment)
    if cover is None:
        return ""

    was = player.policy
    await db.set_policy(player.user_id, cover)
    player.policy = cover
    logger.info(
        "Полис бойца %s держится подпиской до %s", player.user_id, cover.until
    )
    return (
        "Полис продлён по подписке — до её конца."
        if was is not None
        else "Полис выписан по подписке — на весь её срок."
    )


__all__ = [
    "InsuranceError",
    "PolicyDeal",
    "buy_policy",
    "cover_by_pro",
    "price_for",
    "set_renew",
    "settle",
]
