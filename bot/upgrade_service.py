"""Раздача свободных очков характеристик.

Одни правила на все входы: и на кнопки в личке, и на панель в мини-аппе.
Правил всего три, и все три про то, чтобы очки нельзя было нарисовать.

Очки ложатся в *свои* характеристики — те, что боец вырастил сам. Прибавки
с вещей и от эликсиров в базу не попадают: вещь можно снять, эликсир
кончится, а вписанное в базу останется навсегда.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.classes import ALL_STATS, Stats
from bot.models import Player

logger = logging.getLogger(__name__)


class UpgradeError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


def parse_points(raw: dict) -> Stats:
    """Разобрать заявку «куда сколько». Мусор и минусы не проходят."""
    points: dict[str, int] = {}
    for stat in ALL_STATS:
        value = raw.get(stat.value, 0)
        try:
            amount = int(value)
        except (TypeError, ValueError) as error:
            raise UpgradeError(f"Непонятно, сколько очков в {stat.title}.") from error
        if amount < 0:
            raise UpgradeError("Очки можно только добавлять, не отнимать.")
        points[stat.value] = amount
    return Stats(**points)


async def spend_points(db: Database, player: Player, raw: dict) -> Stats:
    """Вложить очки в характеристики. Вернуть то, что вложили."""
    gain = parse_points(raw)
    total = gain.total()
    if total <= 0:
        raise UpgradeError("Не выбрано ни одного очка.")
    if total > player.free_points:
        raise UpgradeError(
            f"Столько очков нет: свободных {player.free_points}, "
            f"а разложено {total}."
        )

    # Складываем со своими, а не с итоговыми: иначе прибавка с меча и от
    # выпитого осела бы в базе навсегда, и её можно было бы «сдать» ещё раз.
    player.apply_stats(player.base_stats.merge(gain))
    player.free_points -= total
    await db.save_player(player)
    logger.info(
        "Боец %s разложил %s очков: %s",
        player.user_id,
        total,
        {stat.value: gain.get(stat) for stat in ALL_STATS if gain.get(stat)},
    )
    return gain


__all__ = ["UpgradeError", "parse_points", "spend_points"]
