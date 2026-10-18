"""Разовые работы по живой базе.

Миграции правят форму: добавить колонку, завести таблицу. А иногда
править надо данные — сбросить точки после смены правил, пересчитать
накопленное. Такая работа делается один раз на живой базе и не должна
повториться при перезапуске бота.

**Отметка и проверка — один запрос.** Бот перезапускается чаще, чем
кажется, и два захода подряд не должны сбросить город дважды.

**Работу не удаляют, когда она сделана.** Код остаётся здесь навсегда:
по нему видно, что с базой уже случалось, а стоит он три строки. Снять
его — значит однажды сделать то же самое второй раз.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.health import now_ts

logger = logging.getLogger(__name__)

# Сброс захваченных точек. Правила владения поменялись: точка платит,
# только пока на ней стоит нанятый, — а захвачены они были по старым,
# и держать их задаром было нечестно по отношению к тем, кто придёт
# после. Город начинает заново, ничей
WIPE_TURF = "2026-10-18-wipe-turf"

# Рагнар садится в кресло начальника полиции. Работа ручная и честно
# временная: полицейскую ветку надо смотреть изнутри — дежурная часть,
# ордера, тюрьма, зарплатная норма, — а пройти её с улицы это неделя
# учёбы и два месяца стажа. Диплом академии выдаём вместе с местом:
# начальник без бумаги в участке выглядел бы ошибкой, а не тестом
RAGNAR_CHIEF = "2026-10-18-ragnar-chief"
CHIEF_NAME = "Ragnar"
CHIEF_JOB = "police_chief"
CHIEF_DIPLOMA = "police"


async def run(db: Database, now: int | None = None) -> list[str]:
    """Сделать всё, чего ещё не делали. Возвращает, что было сделано."""
    moment = now_ts() if now is None else now
    done: list[str] = []

    if await db.mark_chore(WIPE_TURF, moment):
        wiped = await db.wipe_turf()
        # Выставленных снимаем вместе с владельцами: охрана без точки —
        # это строки в таблице, за которые уже заплачено, а стоять им
        # больше негде
        hired = await db.clear_all_hirelings()
        logger.info(
            "Точки сброшены: владельцев %s, выставленных %s", wiped, hired
        )
        done.append(
            f"город сброшен: снято владельцев {wiped}, охраны {hired}"
        )

    if await db.mark_chore(RAGNAR_CHIEF, moment):
        done.append(await _seat_the_chief(db, moment))

    return done


async def _seat_the_chief(db: Database, now: int) -> str:
    """Посадить Рагнара в кресло начальника полиции."""
    from bot.game.work import week_of

    chief = await db.find_by_nickname(CHIEF_NAME)
    if chief is None:
        logger.info("Начальника не сажаем: бойца %s в городе нет", CHIEF_NAME)
        return f"{CHIEF_NAME} не найден — кресло начальника пустует"
    if CHIEF_DIPLOMA not in chief.diplomas:
        chief.diplomas = [*chief.diplomas, CHIEF_DIPLOMA]
        await db.save_player(chief)
    # С прежнего места снимаем сами: занять второе нельзя, а условие
    # «место свободно» считает та же база, что и записывает
    if chief.job_code and chief.job_code != CHIEF_JOB:
        await db.leave_job(chief.user_id, chief.job_experience)
    taken = await db.take_job(chief.user_id, CHIEF_JOB, now, week_of(now))
    logger.info(
        "Начальник полиции: %s (%s)", CHIEF_NAME,
        "сел" if taken else "кресло уже занято",
    )
    return (
        f"{CHIEF_NAME} — начальник полиции"
        if taken
        else f"кресло начальника занято не {CHIEF_NAME}"
    )


__all__ = ["RAGNAR_CHIEF", "WIPE_TURF", "run"]
