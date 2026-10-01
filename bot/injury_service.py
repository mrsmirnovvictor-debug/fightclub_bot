"""Травмы: записать, снять, вылечить.

Бросок делает боевой движок — у него и кости, и добивающий удар. Сюда
приходит уже готовая травма, и здесь решают три вещи: ложится ли она
поверх прежней, что с неё слетает у бойца и до какого часа она висит.

Лечение — дело больницы: заплатил, и вместо часов остались минуты.
Совсем мгновенно не лечат: у каждой тяжести свой срок под капельницей,
и это единственное, что различает лечение лёгкой травмы и тяжёлой,
кроме цены.

Цену сбивает страховой полис — на восемьдесят процентов, и только её:
срок под капельницей полисом не сокращается. Страховая платит за лечение,
а не за время.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.game.injuries import ActiveInjury, Hurt, Injury
from bot.game.insurance import discounted
from bot.inventory_service import settle_gear
from bot.models import Player

logger = logging.getLogger(__name__)

# Тяжесть по возрастанию: по ней и решается, какая травма главнее
HURT_ORDER: tuple[Hurt, ...] = (Hurt.LIGHT, Hurt.MEDIUM, Hurt.HEAVY)


class InjuryError(Exception):
    """Отказ, который показывают игроку как есть."""


def cure_price(player: Player, injury: Injury, now: int | None = None) -> int:
    """Во сколько лечение обойдётся именно этому бойцу.

    Одна функция и для прайса на экране, и для списания: цена со скидкой,
    посчитанная в двух местах по-разному, — это счёт, который не сходится
    с ценником.
    """
    price = injury.hurt.price
    return discounted(price) if player.insured(now) else price


def worse(new: Injury, old: ActiveInjury | None, now: int) -> bool:
    """Ложится ли новая травма поверх старой.

    Лёгкая не отменяет тяжёлую: иначе фингал лечил бы перелом руки. При
    равной тяжести побеждает новая — время пошло заново.
    """
    if old is None or not old.is_active(now):
        return True
    current = old.injury
    if current is None:  # pragma: no cover - код из будущей версии
        return True
    return HURT_ORDER.index(new.hurt) >= HURT_ORDER.index(current.hurt)


async def hurt_player(
    db: Database, player: Player, injury: Injury, now: int | None = None
) -> ActiveInjury | None:
    """Записать травму бойцу. None — не легла, прежняя была тяжелее.

    Вместе с травмой слетает всё, на что характеристик больше не
    хватает: сломанной рукой двуручный меч не держат.
    """
    moment = now_ts() if now is None else now
    if not worse(injury, player.injury, moment):
        return None

    active = ActiveInjury(code=injury.code, until=moment + injury.seconds)
    await db.set_injury(player.user_id, active.code, active.until)
    player.injury = active
    player.dropped_gear = await settle_gear(db, player)
    logger.info(
        "Травма у бойца %s: %s (%s), снято вещей: %s",
        player.user_id,
        injury.code,
        injury.hurt.value,
        len(player.dropped_gear),
    )
    return active


async def heal_injury(
    db: Database, player: Player, now: int | None = None
) -> ActiveInjury:
    """Вылечить травму за кредиты. Возвращает то, что лечили."""
    moment = now_ts() if now is None else now
    active = player.injury
    injury = active.injury if active else None
    if active is None or injury is None or not active.is_active(moment):
        raise InjuryError("Лечить нечего — ты цел.")
    # Полис снимает восемьдесят процентов, карта — десять с того, что
    # осталось. Скидки складываются в этом порядке: полис назначает цену
    # лечения, карта — цену оплаты картой
    price = player.price_here(cure_price(player, injury, moment), moment, Service.HEAL)
    if not player.can_afford(price, moment, Service.HEAL):
        raise InjuryError(
            f"Не хватает кредитов: лечение стоит {price} 💰, "
            f"а {player.purse_note(moment, Service.HEAL)}."
        )

    player.pay(price, moment, Service.HEAL)
    # Совсем мгновенно не лечат: после капельницы боец ещё лежит, но
    # минуты вместо часов. Если травме и так оставалось меньше, срок не
    # удлиняем — за это платить незачем
    until = min(active.until, moment + injury.hurt.cure_seconds)
    await db.set_injury(player.user_id, active.code, until)
    await db.save_player(player)
    player.injury = ActiveInjury(code=active.code, until=until)
    logger.info(
        "Больница лечит бойца %s: %s за %s, осталось %s секунд",
        player.user_id,
        injury.code,
        price,
        until - moment,
    )
    return player.injury


async def record_injuries(
    db: Database,
    hurt: dict[int, Injury],
    players: dict[int, Player],
    now: int | None = None,
) -> dict[int, ActiveInjury]:
    """Разнести травмы размена по бойцам, которые есть в базе.

    Боссу и прочим не-людям травм не достаётся: их в базе нет, и лечить
    их некому.
    """
    got: dict[int, ActiveInjury] = {}
    for user_id, injury in hurt.items():
        player = players.get(user_id)
        if player is None:
            continue
        active = await hurt_player(db, player, injury, now)
        if active is not None:
            got[user_id] = active
    return got


__all__ = [
    "HURT_ORDER",
    "InjuryError",
    "cure_price",
    "heal_injury",
    "hurt_player",
    "record_injuries",
    "worse",
]
