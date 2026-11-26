"""Раздача очков владения: что проверяется, прежде чем очко ляжет.

Правила — в `bot.game.mastery`, хранение — в карточке бойца. Здесь
только одно действие: вложить очки в направление, — и проверки вокруг
него.

**Очки не создаются.** Запас обоих пулов считается от уровня, а не
копится в базе: свободный остаток — это уровень минус вложенное.
Поэтому повторный запрос ничего не создаёт и перескок уровней ничего не
теряет; проверять «не начислили ли дважды» попросту нечего.

**Пул выбирает сервер, а не страница.** Код направления однозначно
говорит, из какого он пула: «мечи» — вид, «щиты» — хват. Если бы пул
приезжал с нажатием, его можно было бы подменить и перелить очко вида в
хват — ровно то, что правила запрещают.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.mastery import (
    FAMILY_CODES,
    HOLD_CODES,
    HoldType,
    WeaponFamily,
    free_points,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class MasteryError(Exception):
    """Вложить не вышло: нет такого направления или не хватает очков."""


def pool_of(code: str) -> str:
    """Из какого пула это направление: «вид» или «хват».

    Пусто — направления с таким кодом нет вовсе.
    """
    if code in FAMILY_CODES:
        return "family"
    if code in HOLD_CODES:
        return "hold"
    return ""


def title_of(code: str) -> str:
    """Как направление называется в отказе и в журнале."""
    if code in FAMILY_CODES:
        return WeaponFamily(code).title
    if code in HOLD_CODES:
        return HoldType(code).title
    return code


async def invest(
    db: Database, player: Player, code: str, amount: int = 1
) -> int:
    """Вложить очки в направление. Возвращает, сколько в нём стало.

    Проверяет всё, что можно проверить только на сервере: есть ли такое
    направление, положительное ли вложение и хватает ли свободного
    остатка того пула, которому направление принадлежит. Страница
    считает то же самое, чтобы не рисовать мёртвых кнопок, но решает
    сервер: нажатие приходит из браузера, и верить ему нельзя.
    """
    pool = pool_of(code)
    if not pool:
        raise MasteryError("Такого направления нет.")
    if amount <= 0:
        raise MasteryError("Вложить можно только целое число очков.")

    points = player.mastery if pool == "family" else player.holds
    left = free_points(player.level, points)
    if amount > left:
        kind = "вида оружия" if pool == "family" else "типа владения"
        raise MasteryError(
            f"Свободных очков {kind} — {left}. Больше не вложить."
        )

    points[code] = max(0, points.get(code, 0)) + amount
    await db.save_player(player)
    logger.info(
        "Боец %s вложил %s очк. во владение «%s»",
        player.user_id, amount, title_of(code),
    )
    return points[code]


__all__ = ["MasteryError", "invest", "pool_of", "title_of"]
