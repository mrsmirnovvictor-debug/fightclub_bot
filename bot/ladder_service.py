"""Недельный рейтинг: подсчёт, закрытие периода и выплаты.

Правила — в `bot.game.ladder`, журнал ELO и снимки — в базе, экран — в
`bot.webapp.ladder`. Здесь то, что их связывает.

**Период закрывает тот, кто на него смотрит.** Часов, которые будили бы
бота в полдень воскресенья, здесь нет — как нет их у работы, у точек, у
бонусов и у арены. Всякий взгляд на рейтинг досчитывает его до
«сейчас»: закрывает кончившиеся недели и доплачивает всё, за что ещё не
платили. Это то же правило, что и везде в клубе, и оно же спасает от
простоя: если бота не было в воскресенье, неделя закроется по его
возвращении — по состоянию на свою границу, а не на миг возвращения.

**Смотрит не обязательно сам награждённый.** Выплата не ждёт, пока
боец откроет карточку: период закрывается целиком и платится всем
сразу, кто бы на него ни посмотрел. Плюс к тому бот закрывает долги при
запуске, а перезапускается он с каждым выкатом.

**Закрытый период не пересчитывается.** Снимок неизменяем: новые бои и
новые изменения ELO в него не попадают, сколько бы их ни случилось
после границы. Поэтому опоздавший подсчёт даёт тот же итог, что дал бы
вовремя, — а не итог по сегодняшнему состоянию города.

**Деньги выдаются по одному месту и каждое отмечается до начисления.**
Обрыв посередине оставляет часть мест неоплаченными, и следующий заход
доплатит ровно их. Повторный запуск не задваивает ничего: отметка и
проверка — один запрос (см. `Database.pay_place`).
"""

from __future__ import annotations

import logging
from datetime import datetime

from bot.database import Database
from bot.game.clock import (
    MOSCOW,
    WEEK_HOUR,
    WEEK_SECONDS,
    week_closes,
    week_key,
    week_opens,
)
from bot.game.health import now_ts
from bot.game.ladder import (
    QUALIFY,
    Standing,
    place_standings,
    prize_for,
)

logger = logging.getLogger(__name__)

# Сколько недель подряд досчитываем за один заход. Город стоял бы очень
# долго, чтобы их накопилось больше: это не ограничение правил, а
# граница одного прохода — остальное доедет следующим взглядом
WEEKS_AT_ONCE = 60


def _standings(rows: list[dict]) -> list[Standing]:
    return [
        Standing(
            user_id=int(row["user_id"]),
            rating=int(row["rating"]),
            fights=int(row["fights"]),
            wins=int(row["wins"]),
            losses=int(row["losses"]),
            reached=int(row["reached"]),
        )
        for row in rows
    ]


async def live_week(db: Database, now: int | None = None) -> list[tuple[int, Standing]]:
    """Рейтинг идущей недели с местами. Ещё не итог — предварительный.

    Считается при каждом запросе, а не копится в таблице: пока неделя
    идёт, её рейтинг меняется с каждым боем, и хранить его значило бы
    держать второе, всегда немного устаревшее мнение о том же самом.
    """
    moment = now_ts() if now is None else now
    rows = await db.week_standings(
        week_opens(moment), week_closes(moment), least=QUALIFY
    )
    return place_standings(_standings(rows))


async def my_week(db: Database, user_id: int, now: int | None = None) -> int:
    """Сколько зачётных боёв у бойца на этой неделе."""
    moment = now_ts() if now is None else now
    return await db.week_fights_of(
        user_id, week_opens(moment), week_closes(moment)
    )


async def settle_due(db: Database, now: int | None = None) -> list[str]:
    """Закрыть все кончившиеся недели и доплатить всё, за что должны.

    Возвращает, что было сделано, — строкой на неделю. Пусто — делать
    было нечего, и это обычный случай: за неделю этот вызов делает
    работу один раз, а зовут его с каждым взглядом на рейтинг.
    """
    moment = now_ts() if now is None else now
    done: list[str] = []
    for period in await _due_periods(db, moment):
        said = await settle(db, period, moment)
        if said:
            done.append(said)
    # Долги прошлых заходов: снимок был записан, а деньги не доехали.
    # Их доплачиваем отдельно от закрытия — закрывать тут уже нечего
    for period in await db.owed_weeks():
        paid, players = await pay_week(db, period, moment)
        if paid:
            done.append(f"{period}: доплачено {paid} кр. бойцам: {players}")
    return done


async def _due_periods(db: Database, now: int) -> list[str]:
    """Недели, которые кончились, а посчитаны ещё не были.

    Ищем от первого боя, попавшего в журнал после последней закрытой
    недели: всё, что было раньше, уже посчитано, и перебирать те недели
    заново незачем — а зовут этот проход с каждым взглядом на рейтинг.

    Недели, в которые никто не дрался, не закрываются вовсе. Строка
    периода значит «здесь считали», и ставить её там, где считать было
    нечего, — писать о том, чего не было. Поэтому в журнале периодов
    лежат ровно те недели, в которые на ринг выходили.
    """
    this_week = week_opens(now)
    since = await db.last_closed_week()
    first = await db.first_logged_fight(since, this_week)
    if first is None:
        return []
    periods: list[str] = []
    start = week_opens(first)
    # Считаем просмотренные недели, а не найденные: иначе длинная череда
    # пустых недель прошла бы весь отрезок за один заход, сколько бы его
    # ни было. Недосмотренное доедет следующим взглядом, и зацикливания
    # тут нет: отсчёт начат от недели, в которой бой заведомо есть, —
    # значит каждый заход закрывает хотя бы её и сдвигает границу вперёд
    for _ in range(WEEKS_AT_ONCE):
        if start >= this_week:
            break
        closes = start + WEEK_SECONDS
        fought = await db.first_logged_fight(start, closes) is not None
        if fought and await db.settled_week(week_key(start)) is None:
            periods.append(week_key(start))
        start = closes
    return periods


async def settle(db: Database, period: str, now: int | None = None) -> str:
    """Посчитать и оплатить одну кончившуюся неделю.

    Снимок пишется первым и целиком, деньги идут по нему. Если снимок
    уже был (его записал другой обработчик или прошлый заход), считать
    заново нечего — платим по записанному.
    """
    moment = now_ts() if now is None else now
    opened = week_opens(_moment_of(period))
    closed = opened + WEEK_SECONDS
    if closed > moment:
        # Неделя ещё идёт: закрывать её рано, и предварительный итог
        # снимком становиться не должен
        return ""
    if await db.settled_week(period) is None:
        rows = await db.week_standings(opened, closed, least=QUALIFY)
        places = [
            {
                "user_id": one.user_id,
                "place": place,
                "rating": one.rating,
                "fights": one.fights,
                "wins": one.wins,
                "losses": one.losses,
                "reached": one.reached,
                "prize": prize_for(place),
            }
            for place, one in place_standings(_standings(rows))
        ]
        fresh = await db.close_week(period, opened, closed, moment, places)
        if fresh:
            logger.info(
                "Неделя %s закрыта: участников %s, призов %s кр.",
                period,
                len(places),
                sum(one["prize"] for one in places),
            )
    paid, players = await pay_week(db, period, moment)
    week = await db.settled_week(period) or {}
    return (
        f"{period}: участников {week.get('players', 0)}, "
        f"выплачено {paid} кр. бойцам: {players}"
    )


async def pay_week(db: Database, period: str, now: int) -> tuple[int, int]:
    """Выдать награды за этот период. Возвращает сумму и число бойцов.

    По одному месту за раз. Каждое отмечается оплаченным до того, как
    деньги легли в карман, и только если до этого оно оплаченным не
    было: два обработчика, пришедшие за одной строкой, разойдутся на
    этой отметке.
    """
    paid = 0
    players = 0
    for place in await db.owed_places(period):
        prize = await db.pay_place(period, int(place["user_id"]), now)
        if prize:
            paid += prize
            players += 1
    return paid, players


def _moment_of(period: str) -> int:
    """Начало недели по её ключу — обратная сторона `week_key`.

    Ключ — это дата воскресенья, с полудня которого неделя идёт. Из неё
    и восстанавливаем миг: полдень того дня по Москве.
    """
    day = datetime.strptime(period, "%Y-%m-%d").replace(
        hour=WEEK_HOUR, tzinfo=MOSCOW
    )
    return int(day.timestamp())


__all__ = [
    "WEEKS_AT_ONCE",
    "live_week",
    "my_week",
    "pay_week",
    "settle",
    "settle_due",
]
