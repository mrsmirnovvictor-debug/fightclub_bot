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
from bot.game.cars import IN_IMPOUND, IN_STREET
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
# Дом участка: за машиной и с заявлением приходят сюда
STATION = "vcpd"


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
    if player.where(moment) != STATION:
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


async def open_thefts(db: Database) -> list[dict]:
    """Дела об угоне, по которым машину ещё не вернули."""
    return [
        one
        for one in await db.theft_cases(judged=None)
        if not one["seized_at"]
    ]


async def theft_of(db: Database, row: dict) -> dict | None:
    """Дело, по которому эту машину ищут. None — за ней ничего нет.

    Ищут двумя путями, и это главное место во всём угоне. Первый —
    прямой: на машине стоит отметка розыска. Второй — через базу: номер
    перебит, отметки нет, но выданный городом знак (`plate_first`) в
    деле записан, и сверка его узнаёт. Перебитый номер прячет машину от
    учёта, но не от бумаг.
    """
    for case in await open_thefts(db):
        if int(case["car_id"]) == int(row["id"]) and row["stolen_filed"]:
            return case
        if row["restamped"] and str(case["plate"]) == str(row["plate_first"]):
            return case
    return None


async def has_theft_warrant(db: Database, suspect_id: int) -> bool:
    """Держит ли этот разыскиваемую машину: по ней и задерживают."""
    for case in await open_thefts(db):
        row = await db.car_by_id(int(case["car_id"]))
        if row is None or int(row["user_id"]) != suspect_id:
            continue
        if await theft_of(db, row) is not None:
            return True
    return False


async def theft_suspects(db: Database) -> set[int]:
    """Кто держит разыскуемую машину: по ней его и задерживают.

    Один запрос на весь дом: список соседей считают пачкой, и спрашивать
    про каждого отдельно было бы тем же самым, но дороже.
    """
    out: set[int] = set()
    for case in await open_thefts(db):
        row = await db.car_by_id(int(case["car_id"]))
        if row is None:
            continue
        if int(row["user_id"]) == int(case["owner_id"]):
            continue
        if await theft_of(db, row) is not None:
            out.add(int(row["user_id"]))
    return out


async def seize_held_cars(
    db: Database, officer_id: int, thief_id: int, now: int
) -> list[str]:
    """Забрать у задержанного все разыскиваемые машины — в участок."""
    taken: list[str] = []
    for case in await open_thefts(db):
        row = await db.car_by_id(int(case["car_id"]))
        if row is None or int(row["user_id"]) != thief_id:
            continue
        if await theft_of(db, row) is None:
            continue
        back = await db.impound_car(
            int(row["id"]), STATION, now, int(case["owner_id"])
        )
        if back is None:  # pragma: no cover - строку только что читали
            continue
        await db.mark_theft_seized(int(row["id"]), now)
        taken.append(str(back["plate"]))
        logger.info(
            "Сотрудник %s изъял %s у задержанного %s",
            officer_id, back["plate"], thief_id,
        )
    return taken


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
                # Штрафуют того, кто едет без прав. Себя не штрафуют:
                # патрульный без прав — тоже нарушитель, но не сам себе
                "can_fine": bool(
                    driving and not licence
                    and int(row["user_id"]) != officer.user_id
                ),
                # Перебитый номер виден только полиции: город выдавал
                # другой, и в базе он остался. Это и есть сверка с базой
                "restamped": bool(row["restamped"]),
                "plate_first": str(row["plate_first"] or ""),
                "place": str(row["at_place"] or "") or str(row["location"] or ""),
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


async def found_cars(
    db: Database, officer: Player, now: int | None = None
) -> list[dict]:
    """Где сейчас разыскиваемые машины: в чьих руках и у какого здания.

    Это и есть вся работа по угону: участок не возвращает машину
    кнопкой — он говорит, где она, а ехать туда сотруднику.
    """
    from bot.game.cars import get_car, parking_title
    from bot.game.locations import get_location

    moment = now_ts() if now is None else now
    if not serves_in_police(officer):
        return []
    out: list[dict] = []
    for case in await open_thefts(db):
        row = await db.car_by_id(int(case["car_id"]))
        if row is None:
            continue
        # Перебитый номер уводит машину из розыска: отметки на ней уже
        # нет, и участок видит только то, что след потерян. Найти её
        # теперь можно лишь сверкой номеров в автоучёте
        lost = await theft_of(db, row) is None
        car = get_car(str(row["code"]))
        spot = str(row["parked"] or "")
        at_street = spot == IN_STREET
        where = str(row["at_place"] or "") if at_street else str(
            row["location"] or ""
        )
        if lost:
            where = ""
        place = get_location(where)
        holder = int(row["user_id"])
        online = is_online(int(row["seen_at"] or 0), moment)
        # Машина у хозяина — дело уже кончилось само: он её забрал
        mine_again = holder == int(case["owner_id"])
        out.append(
            {
                "case": int(case["id"]),
                "car": int(row["id"]),
                "plate": str(case["plate"]),
                "now_plate": str(row["plate"]),
                "restamped": bool(row["restamped"]),
                "title": car.title if car else str(row["code"]),
                "holder_id": holder,
                "holder": str(row["nickname"]),
                "online": online,
                "where": where,
                "where_title": place.title if place else where,
                "street": at_street,
                "parked": parking_title(spot),
                "returned": mine_again,
                "lost": lost,
                # Брошенную доставляют в участок, при угонщике — берут
                # его самого: в сети дракой, не в сети молча
                "can_deliver": bool(
                    at_street and not mine_again and not lost
                    and where == officer.where(moment)
                ),
                "can_take": bool(
                    not at_street and not mine_again and not lost
                    and not online and where == officer.where(moment)
                ),
                "can_arrest": bool(
                    not at_street and not mine_again and not lost
                    and online and where == officer.where(moment)
                ),
                "here": where == officer.where(moment),
            }
        )
    return out


async def deliver(
    db: Database, officer: Player, car_id: int, now: int | None = None
) -> str:
    """Доставить брошенную машину в участок. Хозяин заберёт её там."""
    moment = now_ts() if now is None else now
    if not serves_in_police(officer):
        raise PoliceError("Машины доставляет полиция.")
    row = await db.car_by_id(car_id)
    if row is None:
        raise PoliceError("Такой машины в городе нет.")
    if str(row["parked"]) != IN_STREET:
        raise PoliceError("Эта машина не брошена: её так просто не заберёшь.")
    if await theft_of(db, row) is None:
        raise PoliceError("Эта машина не в розыске: заявления по ней нет.")
    if str(row["at_place"]) != officer.where(moment):
        raise PoliceError("Машина не здесь: до неё надо доехать.")
    case = await theft_of(db, row)
    back = await db.impound_car(
        car_id, STATION, moment, int(case["owner_id"]) if case else 0
    )
    if back is None:  # pragma: no cover - строку только что читали
        raise PoliceError("Машину уже забрали.")
    await db.mark_theft_seized(car_id, moment)
    owner = await db.get_player(int(back["owner"]))
    logger.info(
        "Сотрудник %s доставил %s в участок", officer.user_id, back["plate"]
    )
    name = owner.nickname if owner else "хозяину"
    if not back["kept"]:
        return (
            f"Машина {back['plate']} доставлена в участок. У хозяина такая "
            "уже есть, так что хранить её некому."
        )
    return (
        f"Машина {back['plate']} доставлена в участок. {name} заберёт её "
        "здесь."
    )


async def take_from_thief(
    db: Database, officer: Player, car_id: int, now: int | None = None
) -> str:
    """Забрать машину у угонщика, которого нет в сети.

    Он не в игре — задержание проходит молча: машина уезжает в участок,
    а дело остаётся ждать суда. С тем, кто в сети, так нельзя: его
    задерживают дракой.
    """
    moment = now_ts() if now is None else now
    if not serves_in_police(officer):
        raise PoliceError("Задержание — дело полиции.")
    row = await db.car_by_id(car_id)
    if row is None:
        raise PoliceError("Такой машины в городе нет.")
    if await theft_of(db, row) is None:
        raise PoliceError("Эта машина не в розыске: заявления по ней нет.")
    where = str(row["at_place"] or "") if str(row["parked"]) == IN_STREET \
        else str(row["location"] or "")
    if where != officer.where(moment):
        raise PoliceError("Машина не здесь: до неё надо доехать.")
    if is_online(int(row["seen_at"] or 0), moment):
        raise PoliceError(
            "Угонщик в городе — молча машину не забрать. Задерживай."
        )
    case = await theft_of(db, row)
    back = await db.impound_car(
        car_id, STATION, moment, int(case["owner_id"]) if case else 0
    )
    if back is None:  # pragma: no cover - строку только что читали
        raise PoliceError("Машину уже забрали.")
    await db.mark_theft_seized(car_id, moment)
    logger.info(
        "Сотрудник %s забрал %s у спящего угонщика %s",
        officer.user_id, back["plate"], back["holder"],
    )
    return (
        f"Машина {back['plate']} изъята и доставлена в участок. Угонщик "
        "спал — брать его дракой не пришлось."
    )


async def claim_car(
    db: Database, owner: Player, car_id: int, now: int | None = None
) -> str:
    """Забрать свою машину со стоянки участка."""
    from bot.car_service import _check_room

    moment = now_ts() if now is None else now
    if owner.where(moment) != STATION:
        raise PoliceError("За машиной приходят в участок.")
    row = await db.car_by_id(car_id)
    if row is None or str(row["parked"]) != IN_IMPOUND:
        raise PoliceError("Такой машины в участке нет.")
    if int(row["user_id"]) != owner.user_id:
        raise PoliceError("Эта машина не твоя.")
    from bot.car_service import CarError

    try:
        await _check_room(db, owner, moment)
    except CarError as error:
        raise PoliceError(str(error)) from error
    await db.unpark_car(owner.user_id, str(row["code"]))
    logger.info("Боец %s забрал %s из участка", owner.user_id, row["plate"])
    return f"Машина {row['plate']} снова при тебе."


# ---------- городская стоянка ----------
#
# Двадцать мест под фонарём, открытых всем, и единственное платное
# место в городе: за асфальт и за патруль, который на него смотрит,
# берут полдня аренды машины. Платят при постановке и один раз —
# счётчиков в клубе нет, — а забирают своё даром: выезд оплачен
# въездом. Деньги идут в копилку участка, как и штрафы: стоянку держит
# служба, ей и считать.


async def park_at_station(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Поставить свою машину на стоянку у участка. Место платное."""
    from bot.car_service import CarError, leave
    from bot.game.cars import IN_POLICE, get_car, parking_fee
    from bot.game.locations import Service

    moment = now_ts() if now is None else now
    if player.where(moment) != STATION:
        raise PoliceError("Машину на эту стоянку приводят, а не присылают.")
    car = get_car(code)
    if car is None:
        raise PoliceError("Такой машины в городе нет.")
    fee = player.price_here(parking_fee(code), moment, Service.POLICE)
    if not fee:
        raise PoliceError("Служебная машина ночует в гараже службы, а не тут.")
    if not player.can_afford(fee, moment, Service.POLICE):
        raise PoliceError(
            f"Место стоит {fee} 💰, а "
            f"{player.purse_note(moment, Service.POLICE)}."
        )
    # Сперва ставим, потом берём деньги: мест может не быть, и платить
    # за отказ боец не должен
    try:
        await leave(db, player, code, IN_POLICE, 0, moment)
    except CarError as error:
        raise PoliceError(str(error)) from error
    # Машину ставят вместе с выходом из неё, и этот выход уже сохранён
    # в базе: берём бойца свежим, иначе заплатит он, а за рулём
    # окажется прежний
    fresh = await db.get_player(player.user_id) or player
    fresh.pay(fee, moment, Service.POLICE, about=f"Стоянка: {car.title}")
    await db.save_player(fresh)
    await db.add_to_service_vault(POLICE, fee)
    logger.info(
        "Боец %s поставил %s на стоянку участка за %s",
        player.user_id, code, fee,
    )
    return (
        f"{car.title} на стоянке у участка. Место обошлось в {fee} 💰 — "
        "забрать её можно когда угодно и даром."
    )


async def impound_rows(
    db: Database, player: Player, now: int | None = None
) -> list[dict]:
    """Что лежит в участке и ждёт хозяина."""
    from bot.game.cars import get_car

    moment = now_ts() if now is None else now
    out = []
    for row in await db.cars_at(STATION, moment):
        if str(row["parked"]) != IN_IMPOUND:
            continue
        car = get_car(str(row["code"]))
        out.append(
            {
                "id": int(row["id"]),
                "title": car.title if car else str(row["code"]),
                "plate": str(row["plate"]),
                "owner": str(row["nickname"]),
                "mine": int(row["user_id"]) == player.user_id,
            }
        )
    return out


async def my_thefts(db: Database, owner_id: int) -> list[dict]:
    """Дела об угоне, по которым этот боец — хозяин и ещё ничего не решено.

    Берём их из дел, а не из флажка на машине. Флажок стирает
    перебитый номер, и хозяин, пришедший в участок после автомойки,
    оставался бы без заявления — а значит, и без машины. Бумага же не
    стирается ничем: её завёл сам угон.
    """
    return [
        one
        for one in await db.theft_cases(owner_id=owner_id, filed=None)
        if not one["seized_at"]
    ]


async def file_theft(
    db: Database, player: Player, car_id: int, now: int | None = None
) -> str:
    """Заявить об угоне. С этой минуты машину ищут по номеру.

    Право заявить даёт **дело**, а не отметка на машине: номер к этому
    часу могли перебить уже дважды, и спрашивать с хозяина за чужую
    поездку на автомойку город не станет. Срока у заявления нет, как и
    у заявления о нападении.
    """
    moment = now_ts() if now is None else now
    if player.where(moment) != STATION:
        raise PoliceError("Заявление об угоне пишут в участке.")
    mine = [
        one for one in await my_thefts(db, player.user_id)
        if int(one["car_id"]) == car_id
    ]
    if not mine:
        raise PoliceError("По этой машине заявление не принять.")
    case = mine[0]
    if case["filed_at"]:
        raise PoliceError("Заявление по ней уже написано.")
    await db.file_stolen(car_id, moment)
    # С заявления дело идёт в суд: до него угон — дело двоих
    await db.file_theft_case(car_id, player.user_id, moment)
    logger.info(
        "Боец %s заявил об угоне %s (%s)",
        player.user_id, case["code"], case["plate"],
    )
    return (
        f"Заявление принято: машину {case['plate']} ищут. Найдут у кого "
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
    if player.where(moment) != STATION:
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
    "my_thefts",
    "officers",
    "park_at_station",
    "reward_arrest",
    "serves_in_police",
    "settle_week",
    "term_of",
    "theft_suspects",
]
