"""Выдача подписки PRO.

Одна дверь на все входы: и оплата звёздами, и бесплатная акция приходят
сюда. Внутри всегда одно и то же — продлить срок и дотянуть до нового
срока то, что подписка держит: страховой полис и абонемент в зал.

Вещей подписка не выдаёт: ни клинка, ни образа. И то и другое оставалось
у бойца навсегда, то есть подписка продавала вечное за месячную цену. У
тех, кому их уже выдали, они остаются — отбирать выданное хуже, чем один
раз выдать лишнее.

Полис подписка не выдаёт месяцами, а держит: его срок выравнивается по
концу подписки, и не дальше. Поэтому продление подписки продлевает и
полис, а вот нажать «продлить полис» бесплатно нельзя ни разу —
прибавлять нечего.

Снаряжения подписка больше не даёт. Клинок ассасина из неё убран: вещь в
подписке — это сила за деньги. У тех, кому его уже выдали, он остаётся —
отбирать оплаченное было бы хуже, чем один раз выдать лишнее.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.health import now_ts
from bot.game.pro import ProOffer, promo_is_on, promo_offer
from bot.gym_service import cover_pass
from bot.insurance_service import cover_by_pro
from bot.models import Player

logger = logging.getLogger(__name__)


class ProError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class ProGrant:
    """Итог выдачи: до какого часа подписка и что пришло вместе с ней."""

    offer: ProOffer
    until: int
    policy: bool = False  # полис выписали или дотянули прямо сейчас
    gym: bool = False  # абонемент в зал открыли или дотянули прямо сейчас
    renewed: bool = False  # подписка была жива, мы её продлили

    def seconds_left(self, now: int | None = None) -> int:
        return max(0, self.until - (now_ts() if now is None else now))


async def grant_pro(
    db: Database, player: Player, offer: ProOffer, now: int | None = None
) -> ProGrant:
    """Выдать или продлить подписку на этих условиях."""
    moment = now_ts() if now is None else now
    renewed = player.is_pro(moment)
    player.extend_pro(offer.seconds, moment)

    await db.save_player(player)
    # Полис и абонемент — после сохранения срока: обе сверки смотрят на
    # `pro_until`, и до сохранения увидели бы прежний конец подписки
    policy = bool(await cover_by_pro(db, player, moment))
    gym = bool(await cover_pass(db, player, moment))
    logger.info(
        "PRO: боец %s до %s (%s дней, %s ⭐)",
        player.user_id,
        player.pro_until,
        offer.days,
        offer.stars,
    )
    return ProGrant(
        offer=offer,
        until=player.pro_until,
        policy=policy,
        gym=gym,
        renewed=renewed,
    )


def promo_claim_id(user_id: int) -> str:
    """Ключ бесплатной выдачи в журнале. Он же и защита от второй."""
    return f"promo:pro:{user_id}"


async def promo_taken(db: Database, user_id: int) -> bool:
    """Забирал ли боец бесплатную неделю. Одна на бойца, навсегда."""
    return await db.get_purchase(promo_claim_id(user_id)) is not None


async def claim_free_pro(
    db: Database, player: Player, now: int | None = None
) -> ProGrant:
    """Забрать подписку по акции.

    Бесплатная неделя даётся один раз на бойца: иначе кнопку «продлить
    бесплатно» можно тыкать до бесконечности, и подписка перестаёт что-либо
    стоить. Защита — запись в журнале с уникальным ключом, а не проверка
    перед вставкой: две быстрые нажатия подряд не проскочат.

    Идёт ли акция, решает сервер: со страницы дату не подделать.
    """
    if not promo_is_on():
        raise ProError(
            "Акция кончилась — подписку теперь берут "
            "за звёзды в магазине «Элита»."
        )

    first = await db.add_purchase(
        user_id=player.user_id,
        code="pro",
        stars=0,
        credits=0,
        charge_id=promo_claim_id(player.user_id),
        # Подарок, а не покупка: в историю не идёт и возврату не подлежит
        kind="gift",
    )
    if not first:
        raise ProError(
            "Бесплатную неделю ты уже забирал — она даётся один раз. "
            "Продлить подписку можно за звёзды в магазине «Элита»."
        )
    return await grant_pro(db, player, promo_offer(), now)


__all__ = [
    "ProError",
    "ProGrant",
    "claim_free_pro",
    "grant_pro",
    "promo_claim_id",
    "promo_taken",
]
