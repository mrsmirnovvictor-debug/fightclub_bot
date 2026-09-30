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
from bot.game.health import now_ts
from bot.game.work import (
    COOLDOWN_SECONDS,
    DAY_HOURS,
    PASS_SHARE,
    SHIFT_HOURS,
    SHIFT_SECONDS,
    VACANCIES,
    Vacancy,
    get_vacancy,
    hours_of,
    moscow_day,
    shift_fits,
    vacancy_at,
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


# ---------- кто где работает ----------


async def vacancies(db: Database, player: Player, now: int | None = None) -> list[dict]:
    """Доска агентства: все пять мест и что с каждым из них у этого бойца."""
    moment = now_ts() if now is None else now
    await release_the_absent(db, moment)
    taken = await db.taken_jobs()
    blocks = await db.job_blocks_of(player.user_id, moment)
    rows = []
    for one in VACANCIES:
        block = blocks.get(one.code)
        holder = taken.get(one.code)
        rows.append(
            {
                "vacancy": one,
                "taken_by": holder["nickname"] if holder else "",
                "mine": player.job_code == one.code,
                "blocked_until": block["until"] if block else 0,
                "block_reason": block["reason"] if block else "",
            }
        )
    return rows


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
    for code, holder in (await db.taken_jobs()).items():
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
    return get_vacancy(player.job_code) if player.works else None


# ---------- приём на работу ----------


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
    if player.works:
        mine = my_vacancy(player)
        raise WorkError(
            f"У тебя уже есть работа: {mine.title if mine else 'одна'}. "
            "Работа одна на бойца."
        )

    blocks = await db.job_blocks_of(player.user_id, moment)
    if code in blocks:
        raise WorkError(_block_text(blocks[code], moment))

    # Держателя, у которого неделя давно кончилась, сначала рассчитываем:
    # иначе место занимал бы вечно тот, кто перестал заходить
    await release_the_absent(db, moment)
    taken = await db.taken_jobs()
    if code in taken:
        raise WorkError(
            f"Место занято: здесь уже работает {taken[code]['nickname']}."
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

    if not await db.take_job(player.user_id, code, moment, week_of(moment)):
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
        raise WorkError(
            f"На сегодня хватит: в сутки работают не больше {DAY_HOURS} часов."
        )

    today = moscow_day(moment).isoformat()
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
    """Сколько отработано сегодня. Новые сутки — счёт с нуля."""
    if player.shift_day != moscow_day(now).isoformat():
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

    slip = await _pay(db, player, vacancy, moment, fired=None)
    said = (
        f"Жалованье за неделю: {slip.paid} 💰 за {slip.hours:g} ч "
        f"из {vacancy.hours}."
    )
    if slip.fired:
        await _release(db, player, vacancy, moment, "fired")
        said += (
            " Отработано меньше половины нормы — тебя уволили. "
            "Вакансия снова в агентстве, но подать на неё можно "
            "через неделю."
        )
    return said


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
    dismissed = vacancy.fires(hours) if fired is None else fired

    if paid:
        if player.has_account:
            player.account_balance += paid
        else:  # pragma: no cover - счёт открывают при приёме
            player.credits += paid
    player.job_minutes = 0
    player.job_week = week_of(now)
    await db.save_player(player)
    logger.info(
        "Жалованье бойцу %s: %s за %s ч (%s)",
        player.user_id,
        paid,
        hours,
        "увольнение" if dismissed else "работает дальше",
    )
    return Payslip(vacancy, hours, paid, dismissed)


async def _release(
    db: Database, player: Player, vacancy: Vacancy, now: int, reason: str
) -> None:
    """Освободить место и закрыть его для этого бойца на неделю."""
    await db.leave_job(player.user_id)
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
    "my_vacancy",
    "quit_job",
    "quiz_payload",
    "release_the_absent",
    "settle",
    "start_shift",
    "vacancies",
    "vacancy_at",
]
