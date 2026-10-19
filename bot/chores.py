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

# Эликсир выносливости снят с прилавка: выносливость не поднимает ни
# одна вещь в городе, и склянка была единственным исключением из
# правила, которого никто не просил. Купленное возвращаем деньгами —
# за снятое с прилавка платить игроку не за что
DROP_ENDURANCE = "2026-10-19-drop-endurance-potion"
ENDURANCE_POTION = "boost_endurance"
ENDURANCE_PRICE = 200

# Лицензия на нападение стала бессрочной пачкой, а была недельной
# штукой, которая сгорала в понедельник. Недогулянные старые переносим
# нападение в нападение: за них заплачено, и сжигать их заодно с
# правилом было бы вторым наказанием ни за что
LICENCE_PACKS = "2026-10-19-licence-packs"
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

    if await db.mark_chore(DROP_ENDURANCE, moment):
        done.append(await _refund_the_potion(db))

    if await db.mark_chore(LICENCE_PACKS, moment):
        done.append(await _carry_the_licences(db))

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


async def _refund_the_potion(db: Database) -> str:
    """Вернуть деньги за эликсир выносливости и убрать его из рюкзаков."""
    hands = await db.hands_of_potion(ENDURANCE_POTION)
    given = 0
    for user_id, count in hands.items():
        player = await db.get_player(user_id)
        if player is None:  # pragma: no cover - бойца удалили
            continue
        player.credits += ENDURANCE_PRICE * count
        await db.save_player(player)
        given += ENDURANCE_PRICE * count
    rows = await db.drop_potion_everywhere(ENDURANCE_POTION)
    logger.info(
        "Эликсир выносливости снят: строк %s, возвращено %s", rows, given
    )
    return (
        f"эликсир выносливости снят: вернули {given} 💰 "
        f"на {len(hands)} бойцов"
    )


async def _carry_the_licences(db: Database) -> str:
    """Перенести неистраченные недельные лицензии в бессрочные."""
    old = await db.old_licences()
    for user_id, left in old.items():
        # Пачек не прибавляем: куплены они были по старому правилу, и
        # счёт покупок про него ничего не знает
        await db.buy_licences(user_id, 0, left)
    logger.info(
        "Лицензии перенесены: бойцов %s, нападений %s",
        len(old), sum(old.values()),
    )
    return (
        f"лицензии перенесены: {sum(old.values())} нападений "
        f"у {len(old)} бойцов"
    )


__all__ = [
    "DROP_ENDURANCE",
    "LICENCE_PACKS",
    "RAGNAR_CHIEF",
    "WIPE_TURF",
    "run",
]
