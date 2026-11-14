"""Счёт бонусного пулла: что записали, что накапало и кому заплатили.

Правила и лестницы — в `bot.game.bonuses`, здесь работа с базой.

**Считает тот, кто сделал.** Нападение, задержание, захват,
освобождение — по единице в журнал в тот же миг, из того места, где
событие и случилось. Отдельного наблюдателя за игрой в клубе нет, и
заводить его ради счёта значило бы завести вторую правду.

**Время копится обзором.** Часы владения никто не нажимает, и считать
их приходится по разнице: у точки лежит час, до которого её владение
уже записано, и всякий взгляд на карту дописывает журнал до «сейчас».
Если между взглядами прошла неделя, разница делится по неделям — иначе
воскресные часы падали бы в понедельник.

**Неделю закрывает тот, кто пришёл.** Часов, которые ходили бы по базе
в понедельник в девять, здесь нет — как нет их у работы, у участка и у
точек. Пулл считается и раздаётся при первом взгляде после девяти
утра, и отметка о выдаче живёт в базе: второй раз его не дадут.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.bonuses import (
    ARRESTS,
    EVEN,
    FREED,
    GUARDED,
    HELD,
    MAFIA_BONUSES,
    POLICE_BONUSES,
    RAIDS,
    SEIZED,
    pool_for,
    split_pool,
    weight_of,
)
from bot.game.health import now_ts
from bot.game.turf import CLAN, SERVICE, Owner
from bot.game.work import WEEK_SECONDS, payday_after, week_of

logger = logging.getLogger(__name__)

# Меньше минуты в журнал не дописываем. Обзор точек зовут на каждом
# экране, и запись на каждый взгляд была бы десятком лишних походов в
# базу ради секунд, которые никуда не денутся: час счёта остаётся на
# месте, и эти секунды придут со следующим разом
MIN_STEP = 60

# Какие мерки чьи: у семьи и у города они разные, а журнал один
BY_SIDE: dict[str, tuple] = {CLAN: MAFIA_BONUSES, SERVICE: POLICE_BONUSES}

# Какая мерка считает время владения у какой стороны
CLOCK: dict[str, str] = {CLAN: HELD, SERVICE: GUARDED}


def bonuses_for(owner: Owner) -> tuple:
    """Три дела этой стороны. Пусто — сторона не воюет и не служит."""
    return BY_SIDE.get(owner.kind, ())


async def note(
    db: Database, owner: Owner, metric: str, amount: int = 1,
    now: int | None = None,
) -> int:
    """Записать стороне сделанное. Возвращает, сколько стало за неделю."""
    if owner.nobody or amount <= 0:
        return 0
    moment = now_ts() if now is None else now
    return await db.add_bonus(
        owner.kind, owner.key, week_of(moment), metric, amount
    )


async def note_raid(db: Database, clan_id: int, now: int | None = None) -> int:
    """Нападение семьи."""
    return await note(db, Owner(CLAN, str(clan_id)), RAIDS, 1, now)


async def note_arrest(
    db: Database, code: str, now: int | None = None
) -> int:
    """Задержание преступника по делу о нападении."""
    return await note(db, Owner(SERVICE, code), ARRESTS, 1, now)


async def note_spot(
    db: Database, owner: Owner, now: int | None = None
) -> int:
    """Точка сменила хозяина: семье — захват, городу — освобождение.

    Город берёт точку у мафии и боем, и без боя, и считается это одним
    делом: освобождённый дом — он и есть освобождённый дом, сколько бы
    волн на него ни ушло.
    """
    metric = SEIZED if owner.kind == CLAN else FREED
    return await note(db, owner, metric, 1, now)


# ---------- часы владения ----------


async def accrue(db: Database, now: int | None = None) -> None:
    """Дописать журнал временем владения — до этого часа.

    Разницу раскладываем по неделям: между двумя взглядами на карту
    может пройти и понедельник, и тогда часть часов принадлежит уже
    закрытой неделе. Сложить их все в текущую значило бы заплатить за
    воскресенье по понедельничному счёту.
    """
    moment = now_ts() if now is None else now
    for code, row in (await db.turf_rows()).items():
        if row.owner.nobody or row.owner.kind not in CLOCK:
            continue
        # Точка, которую ещё не считали, считается с часа захвата
        since = row.counted_at or row.taken_at
        if not since or since > moment:
            await db.mark_turf_counted(code, moment)
            continue
        if moment - since < MIN_STEP:
            continue
        metric = CLOCK[row.owner.kind]
        while since < moment:
            edge = min(payday_after(since), moment)
            await db.add_bonus(
                row.owner.kind, row.owner.key, week_of(since), metric,
                edge - since,
            )
            since = edge
        await db.mark_turf_counted(code, moment)


# ---------- понедельник ----------


async def settle(db: Database, now: int | None = None) -> list[dict]:
    """Раздать пуллы за все закрытые недели. Возвращает, что роздано."""
    moment = now_ts() if now is None else now
    done: list[dict] = []
    for kind, key, week in await db.owed_bonus_weeks(week_of(moment)):
        owner = Owner(kind, key)
        bonuses = bonuses_for(owner)
        if not bonuses:  # pragma: no cover - сторона из будущей версии
            continue
        tally = await db.bonus_week(kind, key, week)
        pot = pool_for(bonuses, tally)
        if not await db.pay_bonus_week(kind, key, week, pot, moment):
            continue  # pragma: no cover - гонка двух взглядов
        if not pot:
            continue
        if owner.kind == CLAN:
            await db.move_vault(owner.clan_id, pot)
            logger.info(
                "Бонусный пулл семьи %s за неделю %s: %s в сейф",
                owner.key, week, pot,
            )
        else:
            spent = await _hand_out(db, owner, pot)
            logger.info(
                "Бонусный пулл службы %s за неделю %s: %s, роздано %s",
                owner.key, week, pot, spent,
            )
        done.append({"kind": kind, "key": key, "week": week, "pot": pot})
    return done


async def _hand_out(db: Database, owner: Owner, pot: int) -> int:
    """Разложить пулл службы по людям. Остаток уходит в казну.

    Делит начальник — заранее и один раз: настройка лежит у службы, а
    не у человека, и смена начальника её не теряет.
    """
    from bot.police_service import officers

    heads = await officers(db)
    if not heads:
        await db.add_to_service_vault(owner.service, pot)
        return 0
    split = await db.service_split(owner.service)
    people = []
    for user_id in heads:
        one = await db.get_player(user_id)
        if one is None:  # pragma: no cover - бойца удалили
            continue
        people.append(one)
    if not people:  # pragma: no cover - состав разошёлся между запросами
        await db.add_to_service_vault(owner.service, pot)
        return 0
    weights = (
        [1] * len(people) if split == EVEN
        else [max(1, weight_of(one.job_code)) for one in people]
    )
    spent = 0
    for one, amount in zip(people, split_pool(pot, weights)):
        if not amount:
            continue
        one.credits += amount
        await db.save_player(one)
        spent += amount
    if pot - spent:
        # Остаток не делится на доли — он остаётся службе, а не первому
        # в списке: случайный получатель был бы несправедливостью
        await db.add_to_service_vault(owner.service, pot - spent)
    return spent


async def sweep(db: Database, now: int | None = None) -> None:
    """Досчитать часы и закрыть кончившиеся недели. Одним движением.

    Порядок тут важен: сперва часы, потом выдача. Иначе воскресные
    часы, не дописанные в журнал, не попали бы в пулл той недели, за
    которую их и заработали.
    """
    moment = now_ts() if now is None else now
    await accrue(db, moment)
    await settle(db, moment)


# ---------- что показывать ----------


async def scoreboard(
    db: Database, owner: Owner, now: int | None = None
) -> dict:
    """Счёт стороны за эту неделю и то, что ей выдали за прошлую."""
    moment = now_ts() if now is None else now
    week = week_of(moment)
    return {
        "week": week,
        "ends_at": payday_after(moment),
        "tally": await db.bonus_week(owner.kind, owner.key, week),
        "last": await db.bonus_payout(
            owner.kind, owner.key, week - WEEK_SECONDS
        ),
    }


__all__ = [
    "accrue",
    "bonuses_for",
    "note",
    "note_arrest",
    "note_raid",
    "note_spot",
    "scoreboard",
    "settle",
    "sweep",
]
