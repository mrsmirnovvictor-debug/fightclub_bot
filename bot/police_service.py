"""Полиция: дежурная часть, заявления, тюрьма и копилка участка.

Сама драка здесь не живёт — ни вмешательство, ни задержание. Она та же,
что у ограбления, и идёт в `bot.robbery_service`: две стороны, пары,
раунды и журнал. Здесь то, что вокруг неё: кто числится в розыске, кого
посадили и на сколько, что лежит в копилке участка и кому она
достанется.

**Копилку сводит тот, кто пришёл смотреть.** Часов у клуба нет ни у
работы, ни у полиса, ни у дохода с точек, — и здесь их тоже нет. Неделя
кончилась, норма посчитана, копилка разделена — всё это случается в тот
миг, когда кто-нибудь открывает участок. Не открывал никто — деньги
лежат и ждут; открыли через месяц — разделятся за ту неделю, за которую
копились.

**Служба считается по рабочим местам.** Кто занял кресло в участке, тот
и полицейский, ушёл — и значка нет. Второй правды о составе службы мы
не заводим (`bot.game.services`).
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.health import now_ts
from bot.game.bank import PAY
from bot.game.presence import is_online
from bot.game.police import (
    ARREST_FEE,
    FINE_NO_LICENCE,
    PRISON,
    QUOTA_BONUS,
    jail_left,
    jail_seconds,
    quota_done,
    quota_target,
    shares,
)
from bot.game.services import get_service
from bot.game.work import week_is_over, week_of
from bot.models import Player

logger = logging.getLogger(__name__)

POLICE = "police"


class PoliceError(Exception):
    """Отказ по делам участка, который показывают игроку как есть."""


def serves_in_police(player: Player) -> bool:
    """Носит ли боец значок полиции. Решают рабочие места, а не список."""
    service = get_service(POLICE)
    return bool(service and player.job_code in service.jobs)


async def officers(db: Database) -> list[int]:
    """Кто сейчас служит в полиции."""
    service = get_service(POLICE)
    if service is None:  # pragma: no cover - служба есть всегда
        return []
    taken = await db.taken_jobs()
    out: list[int] = []
    for code in service.jobs:
        out.extend(int(one["user_id"]) for one in taken.get(code, []))
    return out


# ---------- заявление и ордер ----------


async def file_report(
    db: Database, player: Player, case_id: int, now: int | None = None
) -> str:
    """Написать заявление о нападении. С этой минуты есть ордер."""
    moment = now_ts() if now is None else now
    if player.where(moment) != "vcpd":
        raise PoliceError("Заявление пишут в участке.")
    if not await db.file_case(case_id, player.user_id, moment):
        raise PoliceError("По этому делу заявление уже не принять.")
    logger.info("Заявление по делу %s от бойца %s", case_id, player.user_id)
    return (
        "Заявление принято. Ордер выписан: задержать его теперь может "
        "любой сотрудник."
    )


async def has_warrant(db: Database, suspect_id: int) -> bool:
    """Есть ли на этого ордер: заявление написано, задержания не было."""
    return any(
        int(one["suspect_id"]) == suspect_id for one in await db.wanted()
    )


# ---------- дорога: проверка, штраф и изъятие ----------
#
# Город пускает за руль и без прав, зато смотрит. Автоучёт — это все
# живые машины города с номерами и хозяевами; по нему полиция и решает,
# кого остановить. Работает он и по тем, кто вышел из игры: машина
# стоит в городе, а не в кармане.


async def traffic_sheet(
    db: Database, officer: Player, now: int | None = None
) -> list[dict]:
    """Автоучёт города: чья машина, какой номер и что с ней не так.

    Отдаётся только со значком: это рабочая бумага патруля, а не общая
    сводка. Гражданскому в ней делать нечего — он и так знает, где его
    машина.
    """
    from bot.car_service import sweep
    from bot.game.cars import get_car, parking_title

    moment = now_ts() if now is None else now
    if not serves_in_police(officer):
        return []
    await sweep(db, moment)
    out: list[dict] = []
    for row in await db.city_cars(moment):
        car = get_car(str(row["code"]))
        licence = bool(row["licence_at"])
        driving = str(row["driving"] or "") == str(row["code"])
        out.append(
            {
                "id": int(row["id"]),
                "user_id": int(row["user_id"]),
                "owner": str(row["nickname"]),
                "code": str(row["code"]),
                "title": car.title if car else str(row["code"]),
                "plate": str(row["plate"]),
                "driving": driving,
                "parked": str(row["parked"]),
                "where": parking_title(str(row["parked"])) if row["parked"]
                else ("за рулём" if driving else "при хозяине"),
                # Права смотрим у хозяина, а не у машины: без них ездить
                # нельзя, а владеть — сколько угодно
                "licence": licence,
                "stolen": bool(row["stolen_from"]),
                "wanted": bool(row["stolen_filed"]),
                "online": is_online(int(row["seen_at"] or 0), moment),
                # Штрафуют того, кто едет без прав; изымают то, что
                # числится в розыске. Себя не штрафуют: патрульный без
                # прав — тоже нарушитель, но не сам себе
                "can_fine": bool(
                    driving and not licence
                    and int(row["user_id"]) != officer.user_id
                ),
                "can_seize": bool(row["stolen_filed"]),
            }
        )
    return out


async def fine_driver(
    db: Database, officer: Player, car_id: int, now: int | None = None
) -> str:
    """Выписать штраф за руль без прав. Деньги идут в копилку участка."""
    moment = now_ts() if now is None else now
    if not serves_in_police(officer):
        raise PoliceError("Штрафы выписывает полиция.")
    row = await db.car_by_id(car_id)
    if row is None:
        raise PoliceError("Такой машины в городе нет.")
    driver = await db.get_player(int(row["user_id"]))
    if driver is None:  # pragma: no cover - машина без хозяина не живёт
        raise PoliceError("Хозяина этой машины в городе нет.")
    if driver.driving(moment) != str(row["code"]):
        raise PoliceError("Он не за рулём: штрафовать не за что.")
    if driver.school.has_licence:
        raise PoliceError("Права у него есть — всё в порядке.")
    if driver.user_id == officer.user_id:
        raise PoliceError("Себя не штрафуют.")

    # Берём сколько есть: пустой карман не повод отпускать, но и долгов
    # город не выписывает — штраф не может сделать баланс отрицательным
    paid = min(FINE_NO_LICENCE, driver.credits + driver.account_balance)
    left = paid
    take = min(driver.credits, left)
    driver.credits -= take
    left -= take
    if left:
        driver.account_balance -= left
        driver.note_card(-left, PAY, "Полиция", moment, "Штраф: без прав")
    # Из-под нарушителя машину не забирают — только ключи зажигания
    driver.car = ""
    driver.car_until = 0
    await db.save_player(driver)
    await db.add_to_service_vault(POLICE, paid)
    logger.info(
        "Сотрудник %s оштрафовал бойца %s на %s",
        officer.user_id, driver.user_id, paid,
    )
    return (
        f"{driver.nickname} оштрафован на {paid} 💰 за руль без прав. "
        "Машину он оставил на месте."
    )


async def seize_car(
    db: Database, officer: Player, car_id: int, now: int | None = None
) -> str:
    """Изъять угнанную машину и вернуть хозяину.

    Возвращают по заявлению: без него угон — дело двоих, и город в него
    не лезет. Поэтому изымается то, что числится в розыске, а не всё, у
    чего однажды сменился хозяин.
    """
    moment = now_ts() if now is None else now
    if not serves_in_police(officer):
        raise PoliceError("Изымает машины полиция.")
    row = await db.car_by_id(car_id)
    if row is None:
        raise PoliceError("Такой машины в городе нет.")
    if not row["stolen_filed"]:
        raise PoliceError("Эта машина не в розыске: заявления по ней нет.")
    back = await db.return_car(car_id, moment)
    if back is None:  # pragma: no cover - розыск без угона не бывает
        raise PoliceError("Возвращать нечего.")
    owner = await db.get_player(int(back["owner"]))
    name = owner.nickname if owner else "хозяину"
    logger.info(
        "Сотрудник %s изъял %s (%s) у бойца %s",
        officer.user_id, back["code"], back["plate"], back["thief"],
    )
    if not back["kept"]:
        return (
            f"Машина {back['plate']} изъята. У хозяина такая уже есть, "
            "так что с угонщика она просто снята."
        )
    return f"Машина {back['plate']} изъята и возвращена: {name}."


async def file_theft(
    db: Database, player: Player, car_id: int, now: int | None = None
) -> str:
    """Заявить об угоне. С этой минуты машину ищут по номеру."""
    moment = now_ts() if now is None else now
    if player.where(moment) != "vcpd":
        raise PoliceError("Заявление об угоне пишут в участке.")
    row = await db.car_by_id(car_id)
    if row is None or int(row["stolen_from"]) != player.user_id:
        raise PoliceError("По этой машине заявление не принять.")
    if not await db.file_stolen(car_id, moment):
        raise PoliceError("Заявление по ней уже написано.")
    logger.info(
        "Боец %s заявил об угоне %s (%s)",
        player.user_id, row["code"], row["plate"],
    )
    return (
        f"Заявление принято: машину {row['plate']} ищут. Найдут у кого "
        "угодно — вернут тебе."
    )


# ---------- тюрьма ----------


async def jail(db: Database, user_id: int, now: int | None = None) -> int:
    """Посадить бойца. Возвращает, до какого часа он сидит.

    Срок растёт от числа отсидок, и сажаем мы заодно физически: боец
    переезжает в тюрьму и выйти оттуда не может, пока срок идёт.
    """
    moment = now_ts() if now is None else now
    _, times = await db.jail_term(user_id)
    times += 1
    until = moment + jail_seconds(times)
    await db.put_in_jail(user_id, until, times)
    player = await db.get_player(user_id)
    if player is not None:
        player.location = PRISON
        player.travel_to = None
        player.arrives_at = 0
        await db.save_player(player)
    # Сел во время выдвижения — снят с выборов. Снимает его посадка, а
    # не бюллетень: иначе пришлось бы потом гадать по журналу отсидок,
    # когда именно он сидел
    from bot.election_service import strike_if_running

    await strike_if_running(db, user_id, moment)
    logger.info("Боец %s сел в %s-й раз до %s", user_id, times, until)
    return until


async def term_of(
    db: Database, user_id: int, now: int | None = None
) -> dict[str, int]:
    """Сколько бойцу ещё сидеть и в который раз его взяли."""
    moment = now_ts() if now is None else now
    until, times = await db.jail_term(user_id)
    return {"until": until, "times": times, "left": jail_left(until, moment)}


# ---------- копилка участка ----------


async def reward_arrest(
    db: Database, officer_id: int, loot: int, now: int | None = None
) -> int:
    """Записать задержание: деньги в участок, счёт в недельный журнал.

    В копилку падает и награбленное, и городская премия за саму работу.
    Награбленное лежит в ней не навсегда: жертва придёт и заберёт своё,
    а до тех пор участок его держит.
    """
    from bot.bonus_service import note_arrest

    moment = now_ts() if now is None else now
    await db.add_to_service_vault(POLICE, ARREST_FEE + loot)
    await note_arrest(db, POLICE, moment)
    arrests = await db.add_service_arrest(POLICE, week_of(moment))
    logger.info(
        "Задержание бойцом %s: в участок %s, задержаний за неделю %s",
        officer_id, ARREST_FEE + loot, arrests,
    )
    return arrests


async def choose_split(db: Database, player: Player, split: str) -> str:
    """Сказать, как делить бонусный пулл. Говорит один — начальник.

    Настройка лежит у службы, а не у человека: смена начальника её не
    теряет, и в понедельник деньги уходят людям по последнему слову,
    кто бы ни сидел в кресле.
    """
    from bot.game.bonuses import SPLITS, SPLIT_TITLES

    service = get_service(POLICE)
    if not service or player.job_code != service.jobs[0]:
        raise PoliceError("Дележ премии — дело начальника.")
    if split not in SPLITS:
        raise PoliceError("Так премию не делят.")
    await db.set_service_split(POLICE, split)
    logger.info("Начальник %s выбрал дележ премии: %s", player.user_id, split)
    return f"Премию делим: {SPLIT_TITLES[split].lower()}."


async def collect(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Забрать из участка возвращённые деньги. Жертва приходит сама."""
    moment = now_ts() if now is None else now
    if player.where(moment) != "vcpd":
        raise PoliceError("За деньгами приходят в участок.")
    rows = [
        one
        for one in await db.cases_of_victim(player.user_id)
        if one["arrested_at"] and not one["repaid_at"] and one["loot"]
    ]
    if not rows:
        raise PoliceError("Забирать нечего: по твоим делам денег не вернули.")
    total = 0
    for one in rows:
        total += await db.repay_case(int(one["id"]), player.user_id, moment)
    if not total:  # pragma: no cover - забрали между двумя запросами
        raise PoliceError("Их уже забрали.")
    # Деньги уходят из копилки участка: он их держал, а не заработал
    await db.add_to_service_vault(POLICE, -total)
    player.credits += total
    await db.save_player(player)
    logger.info("Боец %s забрал из участка %s", player.user_id, total)
    return f"Возвращено {total} 💰. Участок своё отдал."


async def settle_week(db: Database, now: int | None = None) -> dict[str, int]:
    """Свести прошлую неделю: норма, премия и дележ копилки.

    Делим в тот миг, когда на участок смотрят. Кто смотрит — неважно:
    считается по тем же правилам и в пользу отсутствующих.
    """
    moment = now_ts() if now is None else now
    done = {"week": 0, "arrests": 0, "attacks": 0, "pot": 0, "each": 0,
            "heads": 0, "bonus": 0}
    past = week_of(moment) - 7 * 24 * 60 * 60
    if not week_is_over(past, moment):  # pragma: no cover - неделя та же
        return done
    arrests, paid = await db.service_week(POLICE, past)
    if paid:
        return done
    attacks = await db.attacks_in_week(past)
    if not arrests and not attacks:
        # Тихая неделя: делить нечего и отмечать нечего. Копилка, если
        # в ней что-то есть, дождётся недели, в которую работали
        return done
    if not await db.pay_service_week(POLICE, past):  # pragma: no cover - гонка
        return done

    bonus = QUOTA_BONUS if quota_done(arrests, attacks) else 0
    if bonus:
        await db.add_to_service_vault(POLICE, bonus)
    pot = await db.service_vault(POLICE)
    heads = await officers(db)
    cut = shares(pot, len(heads))
    for user_id, amount in zip(heads, cut):
        one = await db.get_player(user_id)
        if one is None:  # pragma: no cover - бойца удалили
            continue
        one.credits += amount
        await db.save_player(one)
    spent = sum(cut)
    if spent:
        await db.add_to_service_vault(POLICE, -spent)
    logger.info(
        "Неделя участка сведена: задержаний %s при %s нападениях, "
        "роздано %s на %s человек",
        arrests, attacks, spent, len(heads),
    )
    return {
        "week": past,
        "arrests": arrests,
        "attacks": attacks,
        "pot": pot,
        "each": cut[0] if cut else 0,
        "heads": len(heads),
        "bonus": bonus,
    }


async def board(
    db: Database, player: Player, service, now: int | None = None
) -> dict[str, object]:
    """Дежурная часть: нападения, розыск, тюрьма и копилка."""
    moment = now_ts() if now is None else now
    week = week_of(moment)
    arrests, _ = await db.service_week(POLICE, week)
    attacks = await db.attacks_in_week(week)
    return {
        "cases": await db.police_cases(),
        "wanted": await db.wanted(),
        "vault": await db.service_vault(POLICE),
        "week": {
            "arrests": arrests,
            "attacks": attacks,
            "target": quota_target(attacks),
            "done": quota_done(arrests, attacks),
            "bonus": QUOTA_BONUS,
            "fee": ARREST_FEE,
        },
        "officers": len(await officers(db)),
    }


__all__ = [
    "POLICE",
    "PoliceError",
    "board",
    "choose_split",
    "collect",
    "file_report",
    "has_warrant",
    "jail",
    "officers",
    "reward_arrest",
    "serves_in_police",
    "settle_week",
    "term_of",
]
