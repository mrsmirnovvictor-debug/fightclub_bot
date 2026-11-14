"""Работа: приём по тесту, смена в два часа и жалованье по понедельникам.

Правила держит `bot.game.work`, вопросы — `bot.content.work_quiz`, отказы
— этот модуль. Дверей пять: посмотреть вакансии, подать заявку с
ответами, встать на смену, свести смену и свести неделю.

**Неделя сводится на месте, как полис и абонемент.** Часов, которые
ходили бы по базе в понедельник в девять и раздавали жалованье, здесь
нет: неделя закрывается в тот миг, когда за работой пришли — на экран
работы, в агентство или просто в карточку. Поэтому `settle` зовётся
отовсюду, где работу показывают.

**За один подход закрывается одна неделя.** Боец, не заходивший месяц,
увидит одно жалованье и одно увольнение, а не четыре: он всё равно
пропустил норму в первую же неделю, и три следующих ничего не меняют.

**Вакансию занимает база, а не питон.** Условие «место свободно» стоит в
том же запросе, что и запись: иначе двое, нажавшие «устроиться» в одну
секунду, оба прошли бы проверку и стали бы барменами.

**Смена платит вперёд временем, а не деньгами.** Часы записываются в тот
миг, когда смену начали, а замок на дороге держится до её конца. Уйти со
смены нельзя вовсе — не потому, что жалко, а потому, что уходить некуда:
боец заперт в своём доме, пока час не выйдет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.bank_service import open_account
from bot.content.work_quiz import grade, pass_mark, quiz_for
from bot.database import Database
from bot.game.bank import WAGE
from bot.game.health import now_ts
from bot.game.locations import get_location
from bot.game.work import (
    CITY_PAY_STEPS,
    COOLDOWN_SECONDS,
    DAY_HOURS,
    LICENCE,
    PASS_SHARE,
    SHIFT_HOURS,
    SHIFT_SECONDS,
    VACANCIES,
    Vacancy,
    agency_board,
    hired_at_house,
    get_vacancy,
    hours_before_payday,
    hours_of,
    day_is_over,
    months_count,
    plural_months,
    work_day,
    shift_fits,
    vacancies_at,
    week_is_over,
    week_of,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class WorkError(Exception):
    """Отказ, который показывают игроку как есть."""


@dataclass(frozen=True)
class Attempt:
    """Итог теста: сколько верных, сколько нужно было и взяли ли."""

    right: int
    total: int
    need: int
    hired: bool
    vacancy: Vacancy
    account: str = ""  # номер счёта, если его открыли прямо сейчас


@dataclass(frozen=True)
class Payslip:
    """Расчёт за неделю: сколько отработано и сколько заплатили."""

    vacancy: Vacancy
    hours: float
    paid: int
    fired: bool
    # Переработка: часы сверх нормы и что за них доплатили. Ноль —
    # неделя в норму уложилась
    overtime: float = 0.0
    extra: int = 0


# ---------- кто где работает ----------


async def vacancies(
    db: Database, player: Player, now: int | None = None,
    board: tuple | None = None,
) -> list[dict]:
    """Доска агентства: места города и что с каждым из них у этого бойца.

    `board` — какие места показывать. По умолчанию витрина агентства, в
    которой полицейских мест нет: в полицию приходят в полицию, и
    отдельная дверь у неё не для красоты.
    """
    moment = now_ts() if now is None else now
    await release_the_absent(db, moment)
    taken = await db.taken_jobs()
    blocks = await db.job_blocks_of(player.user_id, moment)
    rows = []
    for one in agency_board() if board is None else board:
        block = blocks.get(one.code)
        holders = taken.get(one.code, [])
        rows.append(
            {
                "vacancy": one,
                # Кто уже занял места. Имён столько же, сколько занятых
                # мест: на доске видно и сколько свободно, и к кому идти
                "holders": [row["nickname"] for row in holders],
                "taken_by": holders_text(holders),
                "taken_seats": len(holders),
                # Безлимитное место свободно всегда: его не считают
                "free_seats": (
                    max(0, one.seats - len(holders)) if one.limited else one.cap
                ),
                "limited": one.limited,
                "mine": player.job_code == one.code,
                "blocked_until": block["until"] if block else 0,
                "block_reason": block["reason"] if block else "",
                # Чего не хватает этому бойцу: диплома, опыта или ничего
                "lacks": lacking(player, one, moment),
            }
        )
    return rows


# Сколько имён помещается в отказ. Имена нужны затем, чтобы знать, к
# кому идти договариваться; за стойкой мест двадцать, и двадцать имён
# — это не ответ, а стена текста
NAMES_SHOWN = 4


def holders_text(holders: list[dict]) -> str:
    """Кто занял места — словами. Длинный список сворачивается в счёт."""
    names = [one["nickname"] for one in holders]
    if len(names) <= NAMES_SHOWN:
        return ", ".join(names)
    return ", ".join(names[:NAMES_SHOWN]) + f" и ещё {len(names) - NAMES_SHOWN}"


def lacking(player: Player, vacancy: Vacancy, now: int) -> str:
    """Чего бойцу не хватает для этого места. Пусто — хватает всего.

    Три двери: семья, бумага и стаж. Проверяем здесь, а не на странице,
    по той же причине, по какой там не проверяют цену: спрятанная кнопка
    обходится запросом мимо интерфейса.
    """
    # Семью спрашивают первой: мафиози не возьмут и с дипломом, и со
    # стажем, и говорить ему про бумаги незачем
    if vacancy.lawful and player.clan is not None:
        return (
            f"На службу не берут тех, кто в семье. Выйди из клана "
            f"«{player.clan.clan.name}» — тогда приходи."
        )
    if vacancy.needs and not _has_paper(player, vacancy.needs):
        return f"Нужен документ: {vacancy.education}."
    if vacancy.months:
        mine = player.experience_months(now)
        if mine < vacancy.months:
            return (
                f"Нужен опыт от {vacancy.months} "
                f"{plural_months(vacancy.months)}, а у тебя "
                f"{mine} {months_count(mine)}."
            )
    return ""


def _has_paper(player: Player, code: str) -> bool:
    """Есть ли у бойца документ, которого требует место."""
    if code == LICENCE:
        return player.school.has_licence
    return player.has_diploma(code)


async def release_the_absent(db: Database, now: int | None = None) -> list[str]:
    """Рассчитать и снять с мест тех, у кого неделя давно кончилась.

    Без этого место занимал бы вечно тот, кто перестал заходить: своя
    неделя сводится, только когда за работой приходят, а не приходит
    именно он. Место при этом одно на город — и «одно на город»
    превратилось бы в «навсегда у первого зашедшего».

    Смотрит на доску кто угодно, а рассчитываются при этом чужие недели.
    Это не вольность: расчёт идёт по тем же правилам и в пользу
    отсутствующего — ему платят за отработанное, прежде чем уволить.
    """
    moment = now_ts() if now is None else now
    freed: list[str] = []
    for code, holders in (await db.taken_jobs()).items():
        for holder in holders:
            if not holder["week"] or not week_is_over(holder["week"], moment):
                continue
            absent = await db.get_player(holder["user_id"])
            if absent is None:  # pragma: no cover - боец удалил персонажа
                await db.leave_job(holder["user_id"])
                freed.append(code)
                continue
            if await settle(db, absent, moment) and not absent.works:
                freed.append(code)
    return freed


def my_vacancy(player: Player) -> Vacancy | None:
    """Место этого бойца — и с тем жалованьем, какое назначено ему.

    Жалованье возвращается вместе с местом, а не рядом с ним: у кресел
    начальников оклад не твёрдый, его называет мэр из вилки, и всё, что
    считает деньги — расчёт недели, сверхурочные, строка на экране, —
    обязано считать по этому числу, а не по нижнему краю вилки.
    """
    if not player.works:
        return None
    one = get_vacancy(player.job_code)
    if one is None:
        return None
    return one.with_pay(player.job_salary)


# ---------- кресла начальников: заявка и назначение ----------
#
# Начальника город не нанимает, а назначает: мэр смотрит поданные
# заявки и сажает в кресло одного, называя ему жалованье из вилки. Это
# и вся разница с остальными местами — ни теста, ни «кто первый нажал».


async def bid_for_post(
    db: Database, player: Player, code: str, now: int | None = None
) -> Vacancy:
    """Подать заявку на кресло начальника.

    Заявка — не приём: она ничего не занимает и ни с кем не спорит. На
    своё нынешнее место она не влияет тоже — боец работает дальше, пока
    мэр не решит.
    """
    moment = now_ts() if now is None else now
    vacancy = get_vacancy(code)
    if vacancy is None or not vacancy.appointed:
        raise WorkError("На это место заявок не подают: сюда устраиваются.")
    if player.job_code == code:
        raise WorkError("Ты и так в этом кресле.")
    lack = lacking(player, vacancy, moment)
    if lack:
        raise WorkError(lack)
    await db.place_bid(player.user_id, code, moment)
    logger.info("Боец %s подал заявку на %s", player.user_id, code)
    # Городскому мэру заявку показывают тут же: кнопок он не нажимает,
    # и ждать ему нечего. Живой мэр решит сам, когда захочет
    await city_appoints(db, moment)
    return vacancy


async def withdraw_bid(db: Database, player: Player, code: str) -> Vacancy:
    """Забрать свою заявку."""
    vacancy = get_vacancy(code)
    if vacancy is None or not vacancy.appointed:
        raise WorkError("Такого кресла в городе нет.")
    await db.drop_bid(player.user_id, code)
    return vacancy


async def candidates_for(
    db: Database, code: str, now: int | None = None
) -> list[dict]:
    """Из кого мэру выбирать: подавшие заявку, а если никто — все с дипломом.

    Порядок тут не вежливость, а правило города: кресло пустым не
    держат. Подал заявку — стоишь первым; не подал никто — мэр смотрит
    на всех, у кого есть нужная бумага, и сажает любого.
    """
    moment = now_ts() if now is None else now
    vacancy = get_vacancy(code)
    if vacancy is None or not vacancy.appointed:
        return []
    rows = await db.bids_for(code)
    asked = True
    if not rows:
        asked = False
        rows = await db.players_with_diploma(vacancy.needs)
    out: list[dict] = []
    for row in rows:
        one = await db.get_player(int(row["user_id"]))
        if one is None:  # pragma: no cover - боец удалил персонажа
            continue
        if one.job_code == code:
            continue
        out.append(
            {
                "user_id": one.user_id,
                "name": one.nickname,
                "level": one.level,
                "asked": asked,
                "months": one.experience_months(moment),
                "job": one.job_code,
                "lacks": lacking(one, vacancy, moment),
            }
        )
    return out


async def appoint(
    db: Database,
    mayor: Player,
    code: str,
    user_id: int,
    salary: int,
    now: int | None = None,
) -> tuple[Vacancy, Player]:
    """Посадить бойца в кресло начальника на названное жалованье.

    Прежнего, если он там сидел, сначала рассчитывают: уходит он не с
    пустыми руками, а с деньгами за отработанное. Запрета на новую
    заявку ему при этом не ставят — его не уволили за безделье, его
    сменили, и завтра он может подать снова.
    """
    moment = now_ts() if now is None else now
    vacancy = get_vacancy(code)
    if vacancy is None or not vacancy.appointed:
        raise WorkError("Это кресло мэр не назначает.")
    seat = await db.mayor()
    if int(seat.get("user_id") or 0) != mayor.user_id:
        raise WorkError("Назначает начальников мэр города, а не всякий.")
    if not vacancy.fits_pay(salary):
        raise WorkError(
            f"Жалованье тут от {vacancy.salary} до {vacancy.salary_max} 💰."
        )
    chosen = await db.get_player(user_id)
    if chosen is None:
        raise WorkError("Такого бойца в городе нет.")
    lack = lacking(chosen, vacancy, moment)
    if lack:
        raise WorkError(f"{chosen.nickname}: {lack}")

    fresh = await _seat_chief(db, vacancy, chosen, salary, moment)
    logger.info(
        "Мэр %s назначил бойца %s на %s за %s",
        mayor.user_id, fresh.user_id, code, salary,
    )
    return vacancy.with_pay(salary), fresh


async def _seat_chief(
    db: Database, vacancy: Vacancy, chosen: Player, salary: int, moment: int
) -> Player:
    """Посадить бойца в кресло: освободить старое место и занять это.

    Своей частью, потому что сажают двое: мэр кнопкой и город сам
    собой. Правила посадки у них одни, и держать их в двух местах
    значило бы однажды их рассинхронить.
    """
    code = vacancy.code
    user_id = chosen.user_id
    # Старое место назначенного освобождается само: работа одна на бойца
    if chosen.works:
        if chosen.job_code == code:
            raise WorkError(f"{chosen.nickname} уже в этом кресле.")
        old = get_vacancy(chosen.job_code)
        await settle(db, chosen, moment)
        chosen = await db.get_player(user_id) or chosen
        if chosen.works and old is not None:
            await _pay(db, chosen, old.with_pay(chosen.job_salary), moment, False)
            await db.leave_job(chosen.user_id, chosen.experience(moment))
            chosen = await db.get_player(user_id) or chosen

    # Кресло одно: сидящего в нём рассчитывают и снимают
    for holder in (await db.taken_jobs()).get(code, []):
        before = await db.get_player(int(holder["user_id"]))
        if before is None:  # pragma: no cover - боец удалил персонажа
            await db.leave_job(int(holder["user_id"]))
            continue
        await _pay(db, before, vacancy.with_pay(before.job_salary), moment, False)
        await db.leave_job(before.user_id, before.experience(moment))
        logger.info("Кресло %s освобождено: боец %s снят", code, before.user_id)

    if not await db.take_job(
        chosen.user_id, code, moment, week_of(moment), vacancy.cap
    ):  # pragma: no cover - кресло только что освободили сами
        raise WorkError("Кресло занято. Попробуй ещё раз.")
    fresh = await db.get_player(chosen.user_id) or chosen
    fresh.job_salary = salary
    if not fresh.has_account:
        await open_account(db, fresh)
    await db.save_player(fresh)
    await db.clear_bids(code)
    return fresh


# ---------- мэр от города: назначение по заявкам и стажу ----------
#
# Городская управа кнопок не нажимает, и назначать за неё должно то,
# что и так случается при каждом взгляде. Правила у неё жёсткие и
# скучные нарочно:
#
# **Только по заявкам.** Не подал — не назначат, даже с дипломом и
# стажем. Живой мэр вправе посадить любого, у кого есть бумага; город
# на такое не идёт: у него нет ни знакомых, ни причин.
#
# **Первый — по стажу.** Из подавших садится тот, кто дольше работал;
# при равном стаже — кто выше уровнем, а при равном и уровне — кто
# подал раньше. Очередь считается, а не держится в базе.
#
# **Кресло у города не пожизненное.** Подал заявку человек с бо́льшим
# стажем, чем у сидящего, — город сменит сидящего на него. Иначе «по
# стажу» решал бы не стаж, а кто первый нажал: кресло пустует редко, и
# пришедший позже не виноват, что опоздал на минуту. Сидящего при этом
# рассчитывают, а не выгоняют: деньги за отработанное он получает, и
# подать заявку снова ему никто не мешает.
#
# **Жалованье по стажу тоже.** За каждый месяц сверх требуемого город
# добавляет четверть вилки — и не больше её верха. Торговаться с
# городом не о чем, и это честнее: одно и то же кресло стоит у него
# одного и того же.


def city_pay(vacancy: Vacancy, months: int) -> int:
    """Жалованье от города: низ вилки плюс четверть за каждый месяц сверх."""
    if not vacancy.appointed:
        return vacancy.salary
    step = max(1, (vacancy.salary_max - vacancy.salary) // CITY_PAY_STEPS)
    extra = max(0, months - vacancy.months)
    return min(vacancy.salary_max, vacancy.salary + step * extra)


async def city_appoints(
    db: Database, now: int | None = None
) -> list[tuple[str, int]]:
    """Занять пустые кресла за городского мэра. Отдаёт, кого куда посадил.

    Пока в кресле мэра живой человек, город в назначения не лезет
    вовсе: это его работа, и делать её за него — значит отнимать
    единственное, что мэру дано.
    """
    from bot.game.city import MAYOR_ID as CITY_MAYOR

    moment = now_ts() if now is None else now
    seat = await db.mayor()
    if int(seat.get("user_id") or 0) != CITY_MAYOR:
        return []
    taken = await db.taken_jobs()
    done: list[tuple[str, int]] = []
    for vacancy in VACANCIES:
        if not vacancy.appointed:
            continue
        chosen = await _city_pick(db, vacancy, moment)
        if chosen is None:
            continue
        one, months = chosen
        sitting = await _sitting_months(db, taken.get(vacancy.code), moment)
        if sitting is not None:
            # Кресло занято: сменят сидящего только за больший стаж
            holder_id, holder_months = sitting
            if holder_id == one.user_id or months <= holder_months:
                continue
        salary = city_pay(vacancy, months)
        try:
            await _seat_chief(db, vacancy, one, salary, moment)
        except WorkError:  # pragma: no cover - кресло заняли в этот же миг
            continue
        # Посадка могла снять этого же бойца с другого места: читаем
        # занятые кресла заново, иначе следующее сравнение пойдёт по
        # тому, как было до назначения
        taken = await db.taken_jobs()
        logger.info(
            "Город назначил бойца %s на %s за %s (стаж %s мес.)",
            one.user_id, vacancy.code, salary, months,
        )
        done.append((vacancy.code, one.user_id))
    return done


async def _sitting_months(
    db: Database, holders: list[dict] | None, moment: int
) -> tuple[int, int] | None:
    """Кто сидит в кресле и сколько у него стажа. None — кресло пустое."""
    for holder in holders or []:
        one = await db.get_player(int(holder["user_id"]))
        if one is None:  # pragma: no cover - боец удалил персонажа
            continue
        return one.user_id, one.experience_months(moment)
    return None


async def _city_pick(
    db: Database, vacancy: Vacancy, moment: int
) -> tuple[Player, int] | None:
    """Кого город посадит в это кресло. None — некого."""
    best: tuple[Player, int] | None = None
    order: tuple[int, int, int] = (-1, -1, 0)
    for place, row in enumerate(await db.bids_for(vacancy.code)):
        one = await db.get_player(int(row["user_id"]))
        if one is None:  # pragma: no cover - боец удалил персонажа
            continue
        if lacking(one, vacancy, moment):
            continue
        months = one.experience_months(moment)
        mine = (months, one.level, -place)
        if mine > order:
            best, order = (one, months), mine
    return best


# ---------- приём на работу ----------


async def hire_at_house(
    db: Database, player: Player, code: str, now: int | None = None
) -> Vacancy:
    """Взять место прямо в доме, без теста агентства.

    Тест в агентстве — это проверка, годится ли человек. У полиции она
    уже есть, и это академия: сдал её — приходи в участок и надевай
    форму. Второй экзамен на том же пороге был бы недоверием к первому.
    """
    moment = now_ts() if now is None else now
    vacancy = get_vacancy(code)
    if vacancy is None or not vacancy.at_house:
        raise WorkError("Сюда на эту работу не берут.")
    if vacancy.appointed:
        raise WorkError(
            "В это кресло не устраиваются: на него назначает мэр. "
            "Подай заявку — и жди решения."
        )
    if player.where(moment) != vacancy.place:
        raise WorkError("За формой приходят на место, а не из кармана.")
    if player.works:
        mine = my_vacancy(player)
        raise WorkError(
            f"У тебя уже есть работа: {mine.title if mine else 'одна'}. "
            "Работа одна на бойца."
        )
    lack = lacking(player, vacancy, moment)
    if lack:
        raise WorkError(lack)

    await release_the_absent(db, moment)
    taken = await db.taken_jobs()
    holders = taken.get(code, [])
    if vacancy.limited and len(holders) >= vacancy.seats:
        raise WorkError(
            f"Место занято: здесь уже служит {holders_text(holders)}."
        )
    if not await db.take_job(
        player.user_id, code, moment, week_of(moment), vacancy.cap
    ):
        raise WorkError("Место только что заняли.")
    player.job_code = code
    player.job_since = moment
    player.job_week = week_of(moment)
    player.job_minutes = 0
    player.shift_until = 0
    player.shift_day = ""
    player.shift_minutes = 0
    await db.save_player(player)
    logger.info("Боец %s принят в %s на %s", player.user_id, vacancy.place, code)
    return vacancy


async def apply(
    db: Database, player: Player, code: str, answers: list[int], now: int | None = None
) -> Attempt:
    """Пройти тест и, если сдал, занять место.

    Ответы приходят разом: вопросы показаны все пять, и сверяет их
    сервер. Верный ответ на страницу не уезжает вовсе — иначе тест
    проходился бы чтением ответа мини-аппа.
    """
    moment = now_ts() if now is None else now
    vacancy = get_vacancy(code)
    if vacancy is None:
        raise WorkError("Такой вакансии в агентстве нет.")
    if vacancy.at_house:
        # Агентство такие места и не показывает: в полицию приходят в
        # полицию, в банк — в банк. Проверка тут потому, что спрятанная
        # кнопка обходится запросом мимо страницы
        place = get_location(vacancy.place)
        raise WorkError(
            "Это место занимают на работе, а не в агентстве: приходи "
            f"{'в ' + place.title.lower() if place else 'на место'}."
        )
    if vacancy.appointed:  # pragma: no cover - все такие места и так at_house
        raise WorkError("Это кресло не занимают: на него назначает мэр.")
    if player.works:
        mine = my_vacancy(player)
        raise WorkError(
            f"У тебя уже есть работа: {mine.title if mine else 'одна'}. "
            "Работа одна на бойца."
        )

    blocks = await db.job_blocks_of(player.user_id, moment)
    if code in blocks:
        raise WorkError(_block_text(blocks[code], moment))

    # Бумаги спрашивают до теста: отвечать на вопросы о банке тому, у
    # кого нет диплома, незачем — его всё равно не возьмут
    lack = lacking(player, vacancy, moment)
    if lack:
        raise WorkError(lack)

    # Держателя, у которого неделя давно кончилась, сначала рассчитываем:
    # иначе место занимал бы вечно тот, кто перестал заходить
    await release_the_absent(db, moment)
    taken = await db.taken_jobs()
    holders = taken.get(code, [])
    if vacancy.limited and len(holders) >= vacancy.seats:
        who = holders_text(holders)
        raise WorkError(
            f"Мест нет: заняты все {vacancy.seats}. Здесь работают {who}."
            if vacancy.seats > 1
            else f"Место занято: здесь уже работает {who}."
        )

    right, total = grade(code, answers)
    need = pass_mark(total, PASS_SHARE)
    if right < need:
        # Неделя без второй попытки — на эту вакансию. Другие двери
        # открыты: провалить тест бармена не значит не уметь на почте
        await db.block_job(
            player.user_id, code, moment + COOLDOWN_SECONDS, "test"
        )
        logger.info(
            "Боец %s провалил тест на %s: %s из %s", player.user_id, code, right, total
        )
        return Attempt(right, total, need, False, vacancy)

    if not await db.take_job(
        player.user_id, code, moment, week_of(moment), vacancy.cap
    ):
        raise WorkError("Место только что заняли. Попробуй другую вакансию.")
    player.job_code = code
    player.job_since = moment
    player.job_week = week_of(moment)
    player.job_minutes = 0
    player.shift_until = 0
    player.shift_day = ""
    player.shift_minutes = 0

    # Счёт открывают дистанционно: жалованье должно куда-то приходить.
    # Карту при этом не выпускают — за неё платят, и решать за бойца,
    # что он её хочет, агентство не может
    account = ""
    if not player.has_account:
        account = await open_account(db, player)
    await db.save_player(player)
    logger.info("Боец %s принят на %s", player.user_id, code)
    return Attempt(right, total, need, True, vacancy, account)


async def quit_job(db: Database, player: Player, now: int | None = None) -> Vacancy:
    """Уйти с работы по своей воле. Место освобождается сразу."""
    moment = now_ts() if now is None else now
    vacancy = my_vacancy(player)
    if vacancy is None:
        raise WorkError("Ты нигде не работаешь.")
    if player.on_shift(moment):
        raise WorkError("Сначала доработай смену — с неё не уходят.")

    # Уходящему платят за то, что он отработал: неделя закрывается здесь
    # же, а не пропадает вместе с местом
    await _pay(db, player, vacancy, moment, fired=False)
    await _release(db, player, vacancy, moment, "quit")
    return vacancy


# ---------- смена ----------


async def start_shift(db: Database, player: Player, now: int | None = None) -> int:
    """Встать на смену. Возвращает, до какого часа она идёт."""
    moment = now_ts() if now is None else now
    await settle(db, player, moment)
    vacancy = my_vacancy(player)
    if vacancy is None:
        raise WorkError("Ты нигде не работаешь. За работой — в HR-агентство.")
    if player.where(moment) != vacancy.place:
        raise WorkError(f"Работать надо на месте: твоё — «{vacancy.title}».")
    if player.on_shift(moment):
        raise WorkError("Смена уже идёт.")
    if not shift_fits(_today_minutes(player, moment)):
        raise WorkError(day_is_over(moment, DAY_HOURS))

    today = work_day(moment)
    # Часы записываем сразу, а не по окончании смены: уйти со смены
    # нельзя — боец заперт, — и досчитывать по факту нечего
    player.shift_until = moment + SHIFT_SECONDS
    player.shift_minutes = _today_minutes(player, moment) + SHIFT_HOURS * 60
    player.shift_day = today
    player.job_minutes += SHIFT_HOURS * 60
    await db.save_player(player)
    logger.info(
        "Боец %s встал на смену %s до %s", player.user_id, vacancy.code, player.shift_until
    )
    return player.shift_until


def _today_minutes(player: Player, now: int) -> int:
    """Сколько отработано в этот рабочий день. Новый день — счёт с нуля.

    Рабочий день меняется в полночь, а в понедельник ещё и в девять утра,
    когда закрывается неделя: `work_day` держит оба перехода.
    """
    if player.shift_day != work_day(now):
        return 0
    return player.shift_minutes


# ---------- понедельник, девять утра ----------


async def settle(db: Database, player: Player, now: int | None = None) -> str:
    """Свести неделю, если она кончилась. Пусто — сводить нечего.

    Зовётся отовсюду, где на работу смотрят: своих часов у клуба нет.
    """
    moment = now_ts() if now is None else now
    vacancy = my_vacancy(player)
    if vacancy is None or not player.job_week:
        return ""
    if not week_is_over(player.job_week, moment):
        return ""

    # Неделя, в которую устроились, бывает короче нормы: пришёл в
    # пятницу — до понедельника остаются двое суток по две смены, и
    # половину нормы не закрыть никак. Увольнять за то, чего не дали
    # сделать, нечестно: первая неделя считается, но не судится
    short = short_week(player, vacancy)
    slip = await _pay(db, player, vacancy, moment, fired=False if short else None)
    said = (
        f"Жалованье за неделю: {slip.paid} 💰 за {slip.hours:g} ч "
        f"из {vacancy.hours}."
    )
    if slip.extra:
        said += (
            f" Из них оклад {vacancy.salary} 💰 и сверхурочные "
            f"{slip.extra} 💰 за {slip.overtime:g} ч сверх нормы."
        )
    if slip.fired:
        await _release(db, player, vacancy, moment, "fired")
        said += (
            " Отработано меньше половины нормы — тебя уволили. "
            "Вакансия снова в агентстве, но подать на неё можно "
            "через неделю."
        )
    elif short and slip.hours < vacancy.keep_hours():
        said += (
            " Неделя была короткой: устроился под её конец, и норму было "
            "не закрыть. На этот раз без увольнения."
        )
    return said


def short_week(player: Player, vacancy: Vacancy) -> bool:
    """Была ли эта неделя для бойца короче, чем норма его места.

    Считается только для той недели, в которую его приняли: дальше
    недели у него полные, и спрос с него обычный.
    """
    if player.job_since < player.job_week:
        return False
    return hours_before_payday(
        player.job_since, player.job_week
    ) < vacancy.keep_hours()


async def _pay(
    db: Database, player: Player, vacancy: Vacancy, now: int, fired: bool | None
) -> Payslip:
    """Заплатить за отработанное и обнулить неделю.

    Деньги идут на счёт, а не в наличные: за работу платят переводом, и
    счёт для того при приёме и открывали. Счёта нет — кладём наличными,
    чтобы жалованье не пропало.
    """
    hours = hours_of(player.job_minutes)
    paid = vacancy.payout(hours)
    extra = vacancy.overtime_pay(hours)
    dismissed = vacancy.fires(hours) if fired is None else fired

    if paid:
        if player.has_account:
            player.account_balance += paid
            player.note_card(paid, WAGE, f"Жалованье: {vacancy.title}", now)
        else:  # pragma: no cover - счёт открывают при приёме
            player.credits += paid
    player.job_minutes = 0
    player.job_week = week_of(now)
    await db.save_player(player)
    logger.info(
        "Жалованье бойцу %s: %s за %s ч (%s), сверхурочных %s",
        player.user_id,
        paid,
        hours,
        "увольнение" if dismissed else "работает дальше",
        extra,
    )
    return Payslip(
        vacancy, hours, paid, dismissed, vacancy.overtime(hours), extra
    )


async def _release(
    db: Database, player: Player, vacancy: Vacancy, now: int, reason: str
) -> None:
    """Освободить место и закрыть его для этого бойца на неделю.

    Опыт при этом не теряется: отработанное на этом месте складывается
    с отложенным и остаётся при бойце. Стаж — единственное, что он
    уносит с работы, и терять его за уход было бы странно.
    """
    # Служебная форма уходит вместе с должностью: она числилась за
    # креслом, а не за бойцом. Служебная машина — туда же: ключи от
    # патрульной выдавала служба, и уходит он без них
    from bot.duty_service import hand_back
    from bot.car_service import drop_service_cars

    player.job_experience = player.experience(now)
    await db.leave_job(player.user_id, player.job_experience)
    await hand_back(db, player)
    await drop_service_cars(db, player)
    await db.block_job(player.user_id, vacancy.code, now + COOLDOWN_SECONDS, reason)
    player.job_code = ""
    player.job_since = 0
    player.job_week = 0
    player.job_minutes = 0
    player.shift_until = 0
    player.shift_day = ""
    player.shift_minutes = 0


def _block_text(block: dict, now: int) -> str:
    """Почему на эту вакансию сейчас нельзя."""
    days = max(1, (block["until"] - now + 24 * 60 * 60 - 1) // (24 * 60 * 60))
    why = {
        "test": "Тест на это место ты уже провалил",
        "fired": "С этого места тебя уволили",
        "quit": "С этого места ты ушёл сам",
    }.get(block["reason"], "На это место тебе сейчас нельзя")
    return f"{why}. Подать заявку снова можно через {days} дн."


def quiz_payload(code: str) -> list[dict]:
    """Вопросы для страницы. Верного ответа здесь нет и быть не может."""
    return [
        {"text": one.text, "options": list(one.options)} for one in quiz_for(code)
    ]


__all__ = [
    "Attempt",
    "Payslip",
    "WorkError",
    "apply",
    "city_appoints",
    "city_pay",
    "my_vacancy",
    "quit_job",
    "quiz_payload",
    "holders_text",
    "lacking",
    "release_the_absent",
    "settle",
    "short_week",
    "start_shift",
    "vacancies",
    "vacancies_at",
]
