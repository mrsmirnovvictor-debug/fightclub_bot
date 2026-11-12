"""Квесты: взять задание, сдать его и услышать от города, что сделано.

Правила держит `bot.game.quests`, слова — `bot.content.bartender`,
хранение — `bot.database`, а здесь то, что меняет состояние: записать
задание, выдать аванс, принять вещь и начислить награду.

**Город говорит сам.** Службы не знают про квесты и знать не должны: они
зовут `notice` одной строкой — «вышел на ринг и проиграл», «вошёл в
такую-то дверь», «выставил вещь номер такой-то», «отработал занятие».
Что с этим делать, решает здесь; не нужное никому событие тихо пропадает.

**Место проверяется у стойки.** Задания берут и сдают в Баре: бармен
стоит там, а не ходит по городу. Проверяет это сервер — спрятанная
кнопка обходится запросом мимо интерфейса.

**Уровень тоже проверяется здесь.** Главы открываются уровнем, и
проверка стоит в том же месте, где и место: на сервере. Страница о ней
знает, чтобы нарисовать замок, но держится правило не страницей.

**Сперва отметка, потом вещи.** Сдача закрывается одним запросом
(`finish_quest` — отметка и проверка разом), и только после него
забирается вещь и платится награда. В прежнем порядке вещь уходила
первой, и сорвись отметка — боец остался бы и без вещи, и без задания.
Из двух плохих концов этот хуже: вещь, оставшуюся у бойца при сданном
задании, видно и можно простить, а потерянную не вернуть.
"""

from __future__ import annotations

import logging

from bot.content.bartender import quiz_of
from bot.database import Database
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.progress import QuestLog
from bot.game.quests import (
    GYM_DAYS,
    Goal,
    carried,
    Quest,
    get_quest,
    given_mark,
    is_ready,
    locked_by,
    next_quest,
    quiz_done_mark,
    quiz_marks,
    quiz_open,
    reward_of,
    ring_won,
    wants,
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
    return next_quest(done_codes(log), taken_code(log), player.level)


async def locked_level(db: Database, player: Player) -> int:
    """До какого уровня расти, чтобы бармен дал новое дело. Ноль — не надо."""
    log = await journal(db, player)
    return locked_by(done_codes(log), taken_code(log), player.level)


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
    if player.level < quest.min_level:
        raise QuestError(
            f"Это дело с {quest.min_level} уровня. Подрасти и возвращайся."
        )
    if (await offered(db, player)) is not quest:
        raise QuestError("Бармен пока не предлагал это задание.")

    await db.take_quest(player.user_id, code, moment)
    said = ""
    if quest.advance:
        player.credits += quest.advance
        said = f" Бармен отсчитывает {quest.advance} 💰 вперёд."
    if quest.gives:
        owned = await db.add_gear(player.user_id, quest.gives)
        player.gear = await db.list_gear(player.user_id)
        # Номер выданной вещи — в журнал: выставить в комиссионку нужно
        # именно её, а код у вещей общий
        await db.mark_quest(player.user_id, code, given_mark(owned.id))
        said += " Он кладёт вещь на стойку."
    if quest.gym_pass:
        player.gym_until = max(player.gym_until, moment) + GYM_DAYS * DAY
        # Абонемент живёт в своей таблице, а не в строке бойца:
        # `save_player` его не пишет, и раньше бармен обещал абонемент,
        # которого на самом деле не выдавал
        await db.set_gym_pass(player.user_id, player.gym_until)
        said += " Абонемент в зал — у тебя."
    if quest.advance:
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
    marks = set(one.marks)
    if not is_ready(quest, marks, player):
        raise QuestError("Бармен качает головой: сделано не всё.")

    # Вещь, которую бармен просил, должна быть при бойце до отметки:
    # иначе задание закрылось бы, а отдавать стало бы нечего
    owned = None
    if quest.takes:
        target = next(
            (need.target for need in quest.needs if need.goal is Goal.ITEM), ""
        )
        owned = next(
            (
                item
                for item in player.gear
                if item.code == target and not item.is_equipped
            ),
            None,
        )
        if owned is None:  # pragma: no cover - вещь сняли между проверками
            raise QuestError("Вещи при тебе нет.")

    # Отметка первой: она же и проверка, что задание не сдано дважды.
    # Всё, что ниже, случается только для того, кто эту отметку поставил
    if not await db.finish_quest(player.user_id, code, moment):
        raise QuestError("Это задание уже сдано.")

    if owned is not None:
        await db.delete_gear(owned.id)
        player.drop_gear(owned)

    # Перенесённому заданию платим по первой версии: условия, на
    # которые боец соглашался, были другими
    exp, credits = reward_of(quest, marks)
    report = player.grant_exp(exp)
    paid = credits
    # Доплата за победу: её даёт первый зачтённый бой, а не лучший из
    # них, — поздняя победа зачтённое поражение не заменяет. У
    # перенесённого задания её не было вовсе, и задним числом она не
    # появляется
    bonus = (
        quest.win_bonus
        if quest.win_bonus and not carried(marks) and ring_won(marks)
        else 0
    )
    paid += bonus
    if paid:
        player.credits += paid
    await db.save_player(player)
    logger.info("Боец %s сдал задание %s", player.user_id, code)

    said = f"Задание сдано: +{exp} опыта"
    if paid:
        said += f", +{paid} 💰"
    if bonus:
        said += f" (из них {bonus} за победу)"
    if report.levels:
        said += f". Уровень: {player.level}"
    return said + "."


# ---------- вопросы бармена ----------


async def answer(
    db: Database, player: Player, step: int, option: int, now: int | None = None
) -> tuple[bool, str]:
    """Ответить на вопрос активного задания. Возвращает (верно ли, что сказал).

    Набор вопросов у каждого задания свой, и спрашивают из набора того
    задания, что на руках: ответ про зал не закрывает вопрос про
    полицию. Верный ответ запоминается отметкой — переспрашивать уже
    отвеченное бармен не станет, — а ошибиться можно сколько угодно:
    это разговор, а не экзамен, и штрафа за ошибку нет.
    """
    moment = now_ts() if now is None else now
    _at_bar(player, moment, "Отвечать бармену")
    log = await journal(db, player)
    code = taken_code(log)
    quest = get_quest(code) if code else None
    if quest is None or not quest.quiz:
        raise QuestError("Бармен сейчас ни о чём не спрашивал.")
    marks = set(log[code].marks)
    if not quiz_open(quest, marks, player):
        raise QuestError("Сперва сходи и посмотри сам.")

    questions = quiz_of(quest.quiz)
    if not 0 <= step < len(questions):
        raise QuestError("Такого вопроса не было.")
    question = questions[step]
    if not 0 <= option < len(question.options):
        raise QuestError("Такого ответа бармен не предлагал.")
    if option != question.answer:
        return False, question.wrong

    await db.mark_quest(player.user_id, code, quiz_marks(quest.quiz, step))
    marks = set((await journal(db, player))[code].marks)
    # Весь набор отвечен — отмечаем разговор законченным. Отметка
    # отдельная нарочно: по ней видно, что спрашивать больше нечего, а
    # награда за ответы платится один раз при сдаче, а не за попытку
    if all(
        quiz_marks(quest.quiz, number) in marks
        for number in range(len(questions))
    ):
        await db.mark_quest(player.user_id, code, quiz_done_mark(quest.quiz))
    return True, question.right


# ---------- город рассказывает ----------


async def notice(
    db: Database, player: Player, *marks: str, now: int | None = None
) -> None:
    """Город увидел что-то, что может закрывать задание.

    Зовут это службы — ринг, дорога, зал, рынок, комиссионка, — и зовут
    одной строкой или несколькими сразу. Задания на руках нет или оно
    про другое: тихо выходим, это не ошибка, а обычный день в городе.

    Несколько отметок за раз нужны двери: вход — это и услуга, и
    конкретный дом, и читать журнал дважды ради одного шага незачем. А
    отметки, которую задание не ждёт, в журнал не ложится вовсе: иначе
    он зарастал бы чужими событиями, а по ним потом читают, что боец
    делал.
    """
    log = await journal(db, player)
    code = taken_code(log)
    if not code:
        return
    quest = get_quest(code)
    if quest is None:
        return
    held = set(log[code].marks)
    for mark in marks:
        if not wants(quest, mark, held):
            continue
        if await db.mark_quest(player.user_id, code, mark):
            held.add(mark)
            logger.info(
                "Задание %s бойца %s: отмечено %s", code, player.user_id, mark
            )


def visit_mark(service: Service) -> str:
    return f"{Goal.VISIT.value}:{service.value}"


def place_mark(place: str) -> str:
    return f"{Goal.PLACE.value}:{place}"


def seen_mark(catalog: str) -> str:
    return f"{Goal.SEEN.value}:{catalog}"


async def notice_visit(
    db: Database,
    player: Player,
    service: Service,
    place: str = "",
    now: int | None = None,
) -> None:
    """Боец вошёл в дверь — у города это тоже событие.

    Отметок две: услуга и дом. Услуга годится там, где дверь одна на
    весь город или где подходит любая из нескольких — образы смотрят и
    в барбершопе, и в салоне. Дом нужен там, где услуга открыта в
    нескольких местах, а спрашивают про одно: учат в пяти домах, а
    задание про университет.
    """
    await notice(
        db,
        player,
        *([visit_mark(service)] + ([place_mark(place)] if place else [])),
        now=now,
    )


async def notice_seen(
    db: Database, player: Player, catalog: str, now: int | None = None
) -> None:
    """Боец открыл карточку из каталога — приёма или машины.

    Отдельно от посещения: войти в школу и прочитать условия изучения
    приёма — разные дела, и задание про второе. Проверяет это сервер:
    карточку спрашивают у него, и он же смотрит, что код настоящий.
    """
    await notice(db, player, seen_mark(catalog), now=now)


__all__ = [
    "QuestError",
    "answer",
    "done_codes",
    "hand",
    "journal",
    "locked_level",
    "notice",
    "notice_seen",
    "notice_visit",
    "place_mark",
    "seen_mark",
    "offered",
    "take",
    "taken_code",
    "visit_mark",
]
