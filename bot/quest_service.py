"""Квесты: взять задание, сдать его и услышать от города, что сделано.

Правила держит `bot.game.quests`, слова — `bot.content.bartender`,
хранение — `bot.database`, а здесь то, что меняет состояние: записать
задание, выдать аванс, принять вещь и начислить награду.

**Город говорит сам.** Службы не знают про квесты и знать не должны: они
зовут `notice` одной строкой — «выиграл бой», «вошёл в такую-то дверь»,
«выставил вещь», «отработал занятие». Что с этим делать, решает здесь;
не нужное никому событие тихо пропадает.

**Место проверяется у стойки.** Задания берут и сдают в Баре: бармен
стоит там, а не ходит по городу. Проверяет это сервер — спрятанная
кнопка обходится запросом мимо интерфейса.
"""

from __future__ import annotations

import logging

from bot.content.bartender import QUIZ
from bot.database import Database
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.progress import QuestLog
from bot.game.quests import (
    GYM_DAYS,
    QUIZ_DONE,
    Goal,
    Quest,
    get_quest,
    is_ready,
    next_quest,
    quiz_marks,
)
from bot.models import Player

logger = logging.getLogger(__name__)

DAY = 24 * 60 * 60


class QuestError(Exception):
    """Отказ по заданию, который показывают игроку как есть."""


def _at_bar(player: Player, moment: int, what: str) -> None:
    place = get_location(player.where(moment))
    if place is None or not place.allows(Service.TAVERN):
        raise QuestError(f"{what} можно в Баре, у стойки.")


async def journal(db: Database, player: Player) -> dict[str, QuestLog]:
    """Журнал заданий бойца: по записи на каждое, что он трогал."""
    return {one.code: one for one in await db.quests_of(player.user_id)}


def taken_code(log: dict[str, QuestLog]) -> str:
    """Какое задание на руках. Пусто — ни одного."""
    return next((one.code for one in log.values() if one.taken), "")


def done_codes(log: dict[str, QuestLog]) -> set[str]:
    return {one.code for one in log.values() if one.done}


async def offered(db: Database, player: Player) -> Quest | None:
    """Какое задание бармен предложит следующим."""
    log = await journal(db, player)
    return next_quest(done_codes(log), taken_code(log))


# ---------- взять ----------


async def take(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Взять задание: записать его и выдать всё, что бармен даёт вперёд."""
    moment = now_ts() if now is None else now
    _at_bar(player, moment, "Брать задания")
    quest = get_quest(code)
    if quest is None:
        raise QuestError("Такого задания бармен не давал.")
    log = await journal(db, player)
    if taken_code(log):
        raise QuestError("Сперва доделай то, что уже взял.")
    if code in done_codes(log):
        raise QuestError("Это задание ты уже сдал.")
    if (await offered(db, player)) is not quest:
        raise QuestError("Бармен пока не предлагал это задание.")

    await db.take_quest(player.user_id, code, moment)
    said = ""
    if quest.advance:
        player.credits += quest.advance
        said = f" Бармен отсчитывает {quest.advance} 💰 вперёд."
    if quest.gives:
        await db.add_gear(player.user_id, quest.gives)
        player.gear = await db.list_gear(player.user_id)
        said += " Он кладёт вещь на стойку."
    if quest.gym_pass:
        player.gym_until = max(player.gym_until, moment) + GYM_DAYS * DAY
        said += " Абонемент в зал — у тебя."
    if quest.advance or quest.gym_pass:
        await db.save_player(player)
    logger.info("Боец %s взял задание %s", player.user_id, code)
    return f"Задание взято: «{quest.title}».{said}"


# ---------- сдать ----------


async def hand(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Сдать задание: бармен смотрит, забирает своё и платит."""
    moment = now_ts() if now is None else now
    _at_bar(player, moment, "Сдавать задания")
    quest = get_quest(code)
    if quest is None:
        raise QuestError("Такого задания бармен не давал.")
    log = await journal(db, player)
    one = log.get(code)
    if one is None or not one.taken:
        raise QuestError("Этого задания у тебя на руках нет.")
    if not is_ready(quest, set(one.marks), player):
        raise QuestError("Бармен качает головой: сделано не всё.")

    # Вещь уходит бармену: он её просил, он её и забирает
    if quest.goal is Goal.ITEM and quest.takes:
        owned = next(
            (
                one
                for one in player.gear
                if one.code == quest.target and not one.is_equipped
            ),
            None,
        )
        if owned is None:  # pragma: no cover - вещь сняли между проверками
            raise QuestError("Вещи при тебе нет.")
        await db.delete_gear(owned.id)
        player.drop_gear(owned)

    if not await db.finish_quest(player.user_id, code, moment):  # pragma: no cover
        raise QuestError("Это задание уже сдано.")

    report = player.grant_exp(quest.exp)
    if quest.credits:
        player.credits += quest.credits
    await db.save_player(player)
    logger.info("Боец %s сдал задание %s", player.user_id, code)

    said = f"Задание сдано: +{quest.exp} опыта"
    if quest.credits:
        said += f", +{quest.credits} 💰"
    if report.levels:
        said += f". Уровень: {player.level}"
    return said + "."


# ---------- вопросы бармена ----------


async def answer(
    db: Database, player: Player, step: int, option: int, now: int | None = None
) -> tuple[bool, str]:
    """Ответить на вопрос про PRO. Возвращает (верно ли, что сказал бармен).

    Верный ответ запоминается отметкой: переспрашивать уже отвеченное
    бармен не станет, а вот ошибиться можно сколько угодно — это
    разговор, а не экзамен.
    """
    moment = now_ts() if now is None else now
    _at_bar(player, moment, "Отвечать бармену")
    if not 0 <= step < len(QUIZ):
        raise QuestError("Такого вопроса не было.")
    question = QUIZ[step]
    log = await journal(db, player)
    code = taken_code(log)
    quest = get_quest(code) if code else None
    if quest is None or quest.goal is not Goal.QUIZ:
        raise QuestError("Бармен сейчас ни о чём не спрашивал.")
    if quest.mark not in log[code].marks:
        raise QuestError("Сперва сходи и посмотри сам.")

    if option != question.answer:
        return False, question.wrong

    await db.mark_quest(player.user_id, code, quiz_marks(step))
    marks = set((await journal(db, player))[code].marks)
    # Все три ответа верны — отмечаем разговор законченным. Отметка
    # отдельная нарочно: по ней видно, что спрашивать больше нечего
    if all(quiz_marks(number) in marks for number in range(len(QUIZ))):
        await db.mark_quest(player.user_id, code, QUIZ_DONE)
    return True, question.right


# ---------- город рассказывает ----------


async def notice(
    db: Database, player: Player, mark: str, now: int | None = None
) -> None:
    """Город увидел что-то, что может закрывать задание.

    Зовут это службы — бой, дорога, зал, комиссионка, — и зовут одной
    строкой. Задания на руках нет или оно про другое: тихо выходим, это
    не ошибка, а обычный день в городе.
    """
    log = await journal(db, player)
    code = taken_code(log)
    if not code:
        return
    quest = get_quest(code)
    if quest is None or not quest.by_event or quest.mark != mark:
        return
    if await db.mark_quest(player.user_id, code, mark):
        logger.info("Задание %s бойца %s: отмечено %s", code, player.user_id, mark)


async def notice_visit(
    db: Database, player: Player, service: Service, now: int | None = None
) -> None:
    """Боец вошёл в дверь — у города это тоже событие."""
    await notice(db, player, f"{Goal.VISIT.value}:{service.value}", now)


__all__ = [
    "QuestError",
    "answer",
    "done_codes",
    "hand",
    "journal",
    "notice",
    "notice_visit",
    "offered",
    "take",
    "taken_code",
]
