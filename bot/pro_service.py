"""Выдача подписки PRO.

Одна дверь на все входы: и оплата звёздами, и бесплатная акция приходят
сюда. Внутри всегда одно и то же — продлить срок, положить клинок и открыть
образ. Клинок с образом выдаются один раз: продлевать их незачем, они и так
навсегда.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.health import now_ts
from bot.game.pro import (
    PRO_ITEM,
    PRO_LOOK,
    ProOffer,
    current_offer,
    promo_is_on,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class ProError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class ProGrant:
    """Итог выдачи: до какого часа подписка и что пришло вместе с ней."""

    offer: ProOffer
    until: int
    blade: bool = False  # клинок выдали прямо сейчас
    look: bool = False  # образ открыли прямо сейчас
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

    # Клинок кладём один раз: второй такой же был бы просто хламом в рюкзаке
    blade = not any(owned.code == PRO_ITEM for owned in player.gear)
    if blade:
        owned = await db.add_gear(player.user_id, PRO_ITEM)
        player.gear.append(owned)

    look = PRO_LOOK not in await db.owned_looks(player.user_id)
    if look:
        await db.add_look(player.user_id, PRO_LOOK)

    await db.save_player(player)
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
        blade=blade,
        look=look,
        renewed=renewed,
    )


async def claim_free_pro(
    db: Database, player: Player, now: int | None = None
) -> ProGrant:
    """Забрать подписку по акции. Вне акции — отказ, и это проверяет сервер."""
    offer = current_offer()
    if not offer.free or not promo_is_on():
        raise ProError(
            "Акция кончилась — подписку теперь берут за звёзды в лавке мага."
        )
    return await grant_pro(db, player, offer, now)


__all__ = ["ProError", "ProGrant", "claim_free_pro", "grant_pro"]
