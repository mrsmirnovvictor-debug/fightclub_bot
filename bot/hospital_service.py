"""Лечение за кредиты: одна дверь, две цены.

Правило одно и то же для обеих: здоровье доливают тому, кому есть что
доливать, и только если он может заплатить. Полный боец уходит ни с чем
— и с кредитами при себе: продавать ему нечего.

Кто сейчас в бою, здесь не лечится. Попасть в больницу из боя нельзя —
дорога заперта, пока боец занят, — но в бой его могут втянуть и на
месте, из группы. А здоровье боя живёт в самом бою и в конце ложится
в карточку поверх всего: боец заплатил бы полста и получил бы ту же
рану обратно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.game.hospital import Cure, get_cure
from bot.models import Player

logger = logging.getLogger(__name__)


class HospitalError(Exception):
    """Отказ, который показывают игроку как есть."""


@dataclass
class CureResult:
    """Чем кончился приём: сколько долили и сколько это стоило."""

    cure: Cure
    healed: int
    price: int
    hp: int  # здоровье после приёма


async def heal(
    db: Database,
    player: Player,
    code: str,
    now: int | None = None,
    busy: bool = False,
) -> CureResult:
    """Вылечить бойца за кредиты. `busy` — его сейчас ждут в бою."""
    cure = get_cure(code)
    if cure is None:
        raise HospitalError("Такого в больнице не делают.")
    if busy:
        raise HospitalError("Тебя ждут в бою — лечиться будешь после.")

    moment = now_ts() if now is None else now
    current = player.current_hp(moment)
    healed = cure.healed(current, player.max_hp)
    if healed <= 0:
        raise HospitalError("Ты и так целый — врачу тут делать нечего.")
    # Больница у города одна, и услуга в ней одна: место известно без
    # вопросов, и спрашивать его параметром было бы лишним обрядом
    price = player.price_here(cure.price, moment, Service.HEAL)
    if not player.can_afford(price, moment, Service.HEAL):
        raise HospitalError(
            f"Не хватает кредитов: «{cure.title}» стоит {price} 💰, "
            f"а {player.purse_note(moment, Service.HEAL)}."
        )

    player.pay(price, moment, Service.HEAL)
    player.set_hp(current + healed, moment)
    await db.save_player(player)
    logger.info(
        "Больница подлатала бойца %s: %s, +%s здоровья за %s",
        player.user_id,
        cure.code,
        healed,
        price,
    )
    # В итоге стоит та цена, что ушла с кошелька, а не та, что в прайсе:
    # по ней игроку и скажут, во что обошёлся приём
    return CureResult(cure=cure, healed=healed, price=price, hp=player.hp)


__all__ = ["CureResult", "HospitalError", "heal"]
