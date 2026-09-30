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

Абонемент подписчику держит сама подписка — тем же `settle`, что сводит и
тренировку. Не выдачей месяца, а выравниванием срока по концу подписки:
иначе бесплатный месяц складывался бы сам с собой и переживал бы PRO.

**Первая тренировка в сутки бесплатна, дальше за кредиты.** Цена растёт
по числу уже занятых сегодня занятий, а не по номеру слота: пропустивший
утро платит за вторую тренировку столько же, сколько и не пропустивший.
Списывается она при записи, и обратно не приходит — как и потраченный
слот, если боец ушёл раньше времени.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.classes import Stat, Stats
from bot.game.gym import (
    SLOT_LIMIT,
    TRAINING_SECONDS,
    UPGRADE_GAIN,
    VISITS_PER_DAY,
    Pass,
    can_upgrade,
    cover_by_pro,
    day_is_full,
    day_of_slot,
    get_pass,
    price_of_upgrade,
    price_of_visit,
    slot_is_full,
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
    price: int = 0  # сколько за неё списали сверх абонемента

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


async def cover_pass(db: Database, player: Player, now: int | None = None) -> str:
    """Дотянуть абонемент до конца подписки. Пусто — тянуть нечего.

    Это и есть «с PRO абонемент даётся сам»: не выдача месяца, а
    выравнивание срока по концу подписки. Зовётся и при выдаче самой
    подписки, и здесь — второе догоняет тех, у кого PRO началась раньше
    этого правила, и тех, кому её продлили мимо магазина.
    """
    moment = now_ts() if now is None else now
    was = await db.gym_pass_of(player.user_id)
    until = cover_by_pro(was, player.pro_until, moment)
    if not until:
        return ""
    await db.set_gym_pass(player.user_id, until)
    player.gym_until = until
    logger.info(
        "Абонемент бойца %s держится подпиской до %s", player.user_id, until
    )
    return (
        "Абонемент продлён по подписке — до её конца."
        if has_pass(was, moment)
        else "Абонемент открыт по подписке — на весь её срок."
    )


async def buy_pass(
    db: Database, player: Player, code: str, now: int | None = None
) -> Pass:
    """Купить абонемент. Живой продлевается с конца, кончившийся — с сейчас.

    Месяц ложится поверх того срока, что есть, — в том числе поверх срока
    подписки. Подписчик, купивший абонемент, получает время после
    подписки, а не вместо неё.
    """
    ticket = get_pass(code)
    if ticket is None:
        raise GymError("Такого абонемента в зале не продают.")
    moment = now_ts() if now is None else now
    price = player.price_here(ticket.price, moment, Service.TRAIN)
    if not player.can_afford(price, moment, Service.TRAIN):
        raise GymError(
            f"Не хватает кредитов: абонемент стоит {price} 💰, "
            f"а {player.purse_note(moment, Service.TRAIN)}."
        )

    # Сначала выравниваем по подписке, и только потом кладём срок сверху:
    # иначе купленный месяц считался бы от старого конца и часть его
    # ушла бы под время, которое и так держит подписка
    await cover_pass(db, player, moment)
    was = await db.gym_pass_of(player.user_id)
    # Продлевается с конца, а не с сегодняшнего дня: купивший второй месяц
    # заранее получает два месяца, а не один
    start = was if has_pass(was, moment) else moment
    player.pay(price, moment, Service.TRAIN)
    await db.save_player(player)
    until = start + ticket.days * 24 * 60 * 60
    await db.set_gym_pass(player.user_id, until)
    player.gym_until = until
    logger.info(
        "Абонемент в зал бойцу %s: %s за %s", player.user_id, ticket.code, price
    )
    return ticket


# ---------- тренировка ----------


def _require_gym(player: Player, now: int | None = None) -> None:
    try:
        require(player, Service.TRAIN, now)
    except Exception as error:
        raise GymError(str(error)) from error


async def day_count(db: Database, player: Player, day: str) -> int:
    """Сколько занятий боец уже занял в этих сутках."""
    return await db.gym_visits_today(player.user_id, day)


async def next_price(db: Database, player: Player, now: int | None = None) -> int:
    """Почём бойцу следующая тренировка сегодня. Ноль — первая, по абонементу."""
    moment = now_ts() if now is None else now
    from bot.game.gym import moscow_day

    return price_of_visit(
        await day_count(db, player, moscow_day(moment).isoformat())
    )


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
    if slot_is_full(await db.gym_slot_crowd(slot.id)):
        raise GymError(
            f"В этом занятии все {SLOT_LIMIT} мест заняты — "
            "приходи на следующее."
        )

    # Цена считается по уже занятым сегодня занятиям, а не по номеру слота:
    # пропустивший утро платит за вторую тренировку столько же, сколько и
    # не пропустивший
    taken = await day_count(db, player, day_of_slot(slot.id))
    if day_is_full(taken):
        raise GymError(
            f"На сегодня хватит: {VISITS_PER_DAY} занятий — это потолок суток."
        )
    price = player.price_here(price_of_visit(taken), moment, Service.TRAIN)
    if price and not player.can_afford(price, moment, Service.TRAIN):
        raise GymError(
            f"Занятие сверх абонемента стоит {price} 💰, "
            f"а {player.purse_note(moment, Service.TRAIN)}."
        )

    started = await db.start_gym_visit(
        player.user_id,
        slot.id,
        slot.training.stat.value,
        moment + TRAINING_SECONDS,
        limit=SLOT_LIMIT,
    )
    if not started:
        # Либо боец в этом занятии уже стоял, либо последнее место заняли
        # в ту же секунду. Второе бывает редко, и различать их незачем:
        # обе причины ведут к следующему занятию
        if await db.gym_slot_crowd(slot.id) >= SLOT_LIMIT:
            raise GymError(
                f"Место заняли раньше: в занятии всего {SLOT_LIMIT}. "
                "Приходи на следующее."
            )
        raise GymError("В этом занятии ты уже отработал. Следующее — по расписанию.")

    # Платим после записи: не записались — не списали
    if price:
        player.pay(price, moment, Service.TRAIN)
        await db.save_player(player)
    logger.info(
        "Боец %s встал на тренировку %s в слоте %s за %s",
        player.user_id,
        slot.training.code,
        slot.id,
        price,
    )
    return Visit(
        slot=slot.id,
        stat=slot.training.stat,
        until=moment + TRAINING_SECONDS,
        price=price,
    )


async def settle(db: Database, player: Player, now: int | None = None) -> str:
    """Свести часы зала. Возвращает, что сказать бойцу, или пусто.

    Двух дел: дотянуть абонемент по живой подписке и закрыть отстоявшую
    своё тренировку. Случиться могут оба разом — тогда говорим и о том, и
    о другом: открывшийся абонемент не та новость, которую стоит съесть.

    Зовётся оттуда, где на зал смотрят. Это не действие игрока, а сверка
    часов, и говорить о ней стоит, только когда что-то случилось.
    """
    moment = now_ts() if now is None else now
    # Подписка идёт первой: она может открыть абонемент, без которого в
    # зал не пустят вовсе
    lines = [await cover_pass(db, player, moment)]

    visit = await visit_of(db, player)
    if visit is not None and visit.is_over(moment):
        lines.append(await _close_visit(db, player, visit, moment))
    return " ".join(line for line in lines if line)


async def _close_visit(
    db: Database, player: Player, visit: Visit, moment: int
) -> str:
    """Закрыть отстоявшую своё тренировку и выдать за неё очко."""
    # Пятнадцать минут стоят в зале. Ушёл — тренировка не считается, и
    # слот на неё потрачен: так и в жизни
    try:
        require(player, Service.TRAIN, moment)
    except Exception:
        # Занятие закрываем, но очка не даём: оно потрачено. Стереть
        # запись значило бы вернуть и место в занятии, и место в дне, —
        # то есть отпустить бойца погулять и вернуться к той же цене
        await db.close_gym_visit(player.user_id, visit.slot)
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
    training = training_for(visit.stat)
    name = training.title if training else "Тренировка"
    return f"{name} отработана: +1 очко в {visit.stat.dative}."


async def leave(db: Database, player: Player) -> None:
    """Уйти с тренировки досрочно.

    Занятие при этом потрачено — и место в нём, и место в сегодняшнем
    счёте, и уплаченные за него кредиты. Иначе уход был бы бесплатным
    способом передумать: встал, посмотрел, ушёл, встал заново.
    """
    visit = await visit_of(db, player)
    if visit is None:
        raise GymError("Ты сейчас не тренируешься.")
    await db.close_gym_visit(player.user_id, visit.slot)


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
    "cover_pass",
    "day_count",
    "next_price",
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
