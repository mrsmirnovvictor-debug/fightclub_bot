"""Тренажёрный зал: абонемент, тренировка и улучшение.

Правила держит `bot.game.gym`, отказы — этот модуль. Своих часов у зала
нет и не надо: тренировка длится пятнадцать минут, и узнаётся об этом в
ту минуту, когда за очком пришли, — как и продление страхового полиса.

Три проверки стоят на сервере, а не прятанием кнопки:

1. **Абонемент.** Без него в зал не пускают вовсе.
2. **Время.** Записаться можно только в идущий слот и только пока до его
   конца больше пятнадцати минут.
3. **Место.** Тренируются в зале. Ушёл до конца тренировки — очка нет:
   пятнадцать минут стоят именно в зале, а не где придётся.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.classes import Stat, Stats
from bot.game.gym import (
    TRAINING_SECONDS,
    UPGRADE_GAIN,
    Pass,
    can_upgrade,
    get_pass,
    price_of_upgrade,
    slot_now,
    training_for,
)
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.models import Player
from bot.travel_service import require

logger = logging.getLogger(__name__)


class GymError(Exception):
    """Отказ, который показывают игроку как есть."""


@dataclass
class Progress:
    """Что боец натренировал по одной характеристике."""

    stat: Stat
    points: int = 0
    ups: int = 0

    @property
    def price(self) -> int:
        """Сколько тренировок стоит следующее улучшение. 0 — их больше нет."""
        return price_of_upgrade(self.ups)

    @property
    def ready(self) -> bool:
        return can_upgrade(self.points, self.ups)

    @property
    def maxed(self) -> bool:
        return self.price == 0


@dataclass
class Visit:
    """Идущая тренировка: чей слот, что качает и когда кончится."""

    slot: str
    stat: Stat
    until: int

    def seconds_left(self, now: int) -> int:
        return max(0, self.until - now)

    def is_over(self, now: int) -> bool:
        return now >= self.until


@dataclass
class Upgrade:
    """Итог улучшения: что подняли и до чего."""

    stat: Stat
    value: int
    ups: int
    spent: int


def has_pass(until: int, now: int | None = None) -> bool:
    return until > (now_ts() if now is None else now)


async def progress_of(db: Database, player: Player) -> dict[Stat, Progress]:
    """Прогресс бойца по всем трём характеристикам зала."""
    from bot.game.gym import GYM_STATS

    saved = await db.gym_progress_of(player.user_id)
    rows: dict[Stat, Progress] = {}
    for stat in GYM_STATS:
        points, ups = saved.get(stat.value, (0, 0))
        rows[stat] = Progress(stat=stat, points=points, ups=ups)
    return rows


async def visit_of(db: Database, player: Player) -> Visit | None:
    row = await db.gym_visit_of(player.user_id)
    if row is None:
        return None
    try:
        stat = Stat(row["stat"])
    except ValueError:  # pragma: no cover - характеристика из будущей версии
        return None
    return Visit(slot=row["slot"], stat=stat, until=row["until"])


# ---------- абонемент ----------


async def buy_pass(
    db: Database, player: Player, code: str, now: int | None = None
) -> Pass:
    """Купить абонемент. Живой продлевается с конца, кончившийся — с сейчас."""
    ticket = get_pass(code)
    if ticket is None:
        raise GymError("Такого абонемента в зале не продают.")
    if not player.can_afford(ticket.price):
        raise GymError(
            f"Не хватает кредитов: абонемент стоит {ticket.price} 💰, "
            f"а на счету {player.credits} 💰."
        )

    moment = now_ts() if now is None else now
    was = await db.gym_pass_of(player.user_id)
    # Продлевается с конца, а не с сегодняшнего дня: купивший второй месяц
    # заранее получает два месяца, а не один
    start = was if has_pass(was, moment) else moment
    player.pay(ticket.price)
    await db.save_player(player)
    await db.set_gym_pass(player.user_id, start + ticket.days * 24 * 60 * 60)
    logger.info(
        "Абонемент в зал бойцу %s: %s за %s", player.user_id, ticket.code, ticket.price
    )
    return ticket


# ---------- тренировка ----------


def _require_gym(player: Player, now: int | None = None) -> None:
    try:
        require(player, Service.TRAIN, now)
    except Exception as error:
        raise GymError(str(error)) from error


async def join(db: Database, player: Player, now: int | None = None) -> Visit:
    """Встать на тренировку идущего слота."""
    moment = now_ts() if now is None else now
    _require_gym(player, moment)

    if not has_pass(await db.gym_pass_of(player.user_id), moment):
        raise GymError("Нужен абонемент — без него на тренировку не пускают.")
    if await visit_of(db, player) is not None:
        raise GymError("Ты уже на тренировке. Дотерпи до конца.")

    slot = slot_now(moment)
    if slot is None:
        raise GymError("Сейчас занятий нет — посмотри расписание.")
    if not slot.takes_joiners(moment):
        raise GymError(
            "До конца занятия меньше пятнадцати минут — "
            "записывайся на следующее."
        )

    started = await db.start_gym_visit(
        player.user_id, slot.id, slot.training.stat.value, moment + TRAINING_SECONDS
    )
    if not started:
        raise GymError("В этом занятии ты уже отработал. Следующее — по расписанию.")
    logger.info(
        "Боец %s встал на тренировку %s в слоте %s",
        player.user_id,
        slot.training.code,
        slot.id,
    )
    return Visit(
        slot=slot.id, stat=slot.training.stat, until=moment + TRAINING_SECONDS
    )


async def settle(db: Database, player: Player, now: int | None = None) -> str:
    """Свести законченную тренировку. Возвращает, что сказать, или пусто.

    Зовётся оттуда, где на зал смотрят. Это не действие игрока, а сверка
    часов, и говорить о ней стоит, только когда что-то случилось.
    """
    moment = now_ts() if now is None else now
    visit = await visit_of(db, player)
    if visit is None or not visit.is_over(moment):
        return ""

    training = training_for(visit.stat)
    # Пятнадцать минут стоят в зале. Ушёл — тренировка не считается, и
    # слот на неё потрачен: так и в жизни
    try:
        require(player, Service.TRAIN, moment)
    except Exception:
        await db.drop_gym_visit(player.user_id, visit.slot)
        logger.info("Боец %s ушёл с тренировки %s", player.user_id, visit.slot)
        return "Ты ушёл из зала до конца занятия — тренировка не зачтена."

    if not await db.close_gym_visit(player.user_id, visit.slot):
        return ""  # pragma: no cover - её закрыл соседний запрос
    points = await db.add_gym_point(player.user_id, visit.stat.value)
    logger.info(
        "Боец %s отработал тренировку %s, очков стало %s",
        player.user_id,
        visit.stat.value,
        points,
    )
    name = training.title if training else "Тренировка"
    return f"{name} отработана: +1 очко в {visit.stat.dative}."


async def leave(db: Database, player: Player) -> None:
    """Уйти с тренировки досрочно. Слот при этом потрачен."""
    visit = await visit_of(db, player)
    if visit is None:
        raise GymError("Ты сейчас не тренируешься.")
    await db.drop_gym_visit(player.user_id, visit.slot)


# ---------- улучшение ----------


async def upgrade(
    db: Database, player: Player, stat: Stat, now: int | None = None
) -> Upgrade:
    """Потратить очки и поднять характеристику на единицу."""
    if training_for(stat) is None:
        raise GymError("Эту характеристику в зале не качают.")
    _require_gym(player, now)

    rows = await progress_of(db, player)
    row = rows[stat]
    if row.maxed:
        raise GymError(f"{stat.title.capitalize()} уже на потолке зала.")
    if not row.ready:
        need = row.price - row.points
        raise GymError(
            f"Не хватает тренировок: нужно ещё {need} "
            f"из {row.price}."
        )

    price = row.price
    if not await db.spend_gym_points(player.user_id, stat.value, price):
        raise GymError("Очки уже потрачены — обнови зал.")

    # Складываем со своими, а не с итоговыми: иначе прибавка с меча и от
    # выпитого осела бы в базе навсегда — та же оговорка, что у раздачи очков
    player.apply_stats(player.base_stats.merge(Stats(**{stat.value: UPGRADE_GAIN})))
    await db.save_player(player)
    logger.info(
        "Боец %s поднял %s до %s за %s тренировок",
        player.user_id,
        stat.value,
        player.base_stats.get(stat),
        price,
    )
    return Upgrade(
        stat=stat, value=player.base_stats.get(stat), ups=row.ups + 1, spent=price
    )


__all__ = [
    "GymError",
    "Progress",
    "Upgrade",
    "Visit",
    "buy_pass",
    "has_pass",
    "join",
    "leave",
    "progress_of",
    "settle",
    "upgrade",
    "visit_of",
]
