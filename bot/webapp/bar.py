"""Бар глазами мини-аппа: бармен за стойкой и разговор с ним.

Правил здесь нет — их держит `bot.game.quests`, слова живут в
`bot.content.bartender`, действия — в `bot.quest_service`; тут перевод
состояния в json.

**Разговор собирает сервер, а не страница.** Узел один, и какой именно —
решает журнал заданий: нечего дать — бармен молчит о делах, дал — ждёт,
сделано — принимает, глава кончилась раньше уровня — зовёт расти.
Страница рисует текст и кнопки ответов, не зная ни одного правила: иначе
половина разговора жила бы в браузере и там же ломалась.

**Журнал идёт главами.** Четыре темы по пять заданий, у каждой свой
уровень, и у каждого задания видно, чем оно закрыто: уровнем, предыдущим
заданием или ничем. Шесть состояний на строку, и все шесть названы: без
этого «закрыто уровнем» и «ждёт своей очереди» выглядели бы одинаково
серыми.

**Бармен стоит на картинке.** Он нарисован с прозрачным фоном и ложится
поверх вида изнутри — у стойки, правее середины. Значок над ним говорит,
есть ли дело: восклицательный знак — предлагает, часы — ждёт, галочка —
принимает работу. Закрытое уровнем задание значка не даёт: подходить
пока незачем, и звать к стойке зря — обман.
"""

from __future__ import annotations

from typing import Any

from bot.content.bartender import (
    BYE,
    HAND,
    HELLO,
    IDLE,
    LATER,
    NAME,
    NOT_YET,
    PICTURE,
    READY,
    TAKE,
    TITLE,
    grow_up,
    quiz_of,
    talk_of,
)
from bot.game.health import now_ts
from bot.game.progress import QuestLog
from bot.game.quests import (
    CHAPTERS,
    ORDER,
    QUESTS,
    Quest,
    chapter_quests,
    get_quest,
    is_ready,
    locked_by,
    needs_left,
    next_quest,
    quiz_marks,
    quiz_open,
)
from bot.models import Player

# Что висит над барменом. Значок один на состояние: по нему и видно,
# стоит ли подходить
MARKS: dict[str, str] = {
    "offer": "❗",
    # Вопрос — это тоже дело: бармен не ждёт, он спрашивает
    "quiz": "❗",
    "wait": "🕓",
    "ready": "✅",
    # Глава кончилась, а уровень ещё нет: подходить пока незачем
    "grow": "",
    "idle": "",
}

# Состояния строки журнала. Шесть, и каждое означает своё
DONE = "done"  # сдано
READY_STATE = "ready"  # сделано, осталось дойти до стойки
TAKEN = "taken"  # на руках
OPEN = "open"  # можно брать прямо сейчас
LOCKED_LEVEL = "locked_level"  # закрыто уровнем
LOCKED_ORDER = "locked_order"  # закрыто предыдущим заданием

STATE_MARKS: dict[str, str] = {
    DONE: "✅",
    READY_STATE: "🕓",
    TAKEN: "🕓",
    OPEN: "❗",
    LOCKED_LEVEL: "🔒",
    LOCKED_ORDER: "",
}


def quest_state(
    quest: Quest,
    log: QuestLog | None,
    player: Player,
    first_open: str,
) -> str:
    """В каком состоянии строка журнала.

    `first_open` — код задания, которое бармен предложит следующим: всё,
    что стоит за ним по порядку, закрыто не уровнем, а очередью, и
    путать эти два замка нельзя. «Закрыто уровнем» игрок исправляет
    ростом, «закрыто предыдущим» — работой.
    """
    if log and log.done:
        return DONE
    if log and log.taken:
        return READY_STATE if is_ready(quest, set(log.marks), player) else TAKEN
    if quest.code == first_open:
        return OPEN
    if player.level < quest.min_level:
        return LOCKED_LEVEL
    return LOCKED_ORDER


def quest_row(
    quest: Quest, log: QuestLog | None, player: Player, first_open: str = ""
) -> dict[str, Any]:
    """Строка задания в списке: что делать, чем кончится и как идёт."""
    state = quest_state(quest, log, player, first_open)
    left = (
        [one.goal.value for one in needs_left(quest, set(log.marks), player)]
        if log and log.taken
        else []
    )
    return {
        "code": quest.code,
        "title": quest.title,
        "task": quest.task,
        "chapter": quest.chapter,
        "min_level": quest.min_level,
        "exp": quest.exp,
        "credits": quest.credits,
        # Награды показываем по отдельности: базовая, условный бонус,
        # аванс и выдаваемая вещь — разные вещи, и сумма одной строкой
        # обещала бы не то, что придёт
        "win_bonus": quest.win_bonus,
        "advance": quest.advance,
        "gives": quest.gives,
        "gym_pass": quest.gym_pass,
        "reward": quest.reward_text,
        "state": state,
        "mark": STATE_MARKS.get(state, ""),
        # Сделано, но ещё не сдано: бармена надо найти и сказать
        "ready": state == READY_STATE,
        "left": left,
        "number": ORDER.index(quest.code) + 1,
    }


def quests_payload(
    player: Player, log: dict[str, QuestLog]
) -> list[dict[str, Any]]:
    """Все задания бармена по порядку — он же список во вкладке."""
    first = next_quest(
        {one.code for one in log.values() if one.done},
        next((one.code for one in log.values() if one.taken), ""),
        player.level,
    )
    first_open = first.code if first else ""
    return [
        quest_row(quest, log.get(quest.code), player, first_open)
        for quest in QUESTS
    ]


def chapters_payload(
    player: Player, log: dict[str, QuestLog]
) -> list[dict[str, Any]]:
    """Журнал главами: четыре темы, в каждой пять заданий по порядку."""
    rows = {one["code"]: one for one in quests_payload(player, log)}
    out: list[dict[str, Any]] = []
    for chapter in CHAPTERS:
        inside = [rows[one.code] for one in chapter_quests(chapter.code)]
        out.append(
            {
                "code": chapter.code,
                "title": chapter.title,
                "level": chapter.level,
                # Доросли ли до главы. По этому и рисуется замок на всей
                # теме, а не на каждой строке по отдельности
                "open": player.level >= chapter.level,
                "done": sum(1 for one in inside if one["state"] == DONE),
                "total": len(inside),
                "quests": inside,
            }
        )
    return out


def reply(code: str, text: str, **extra: Any) -> dict[str, Any]:
    """Ответ игрока: код для сервера, слова для экрана."""
    return dict({"code": code, "text": text}, **extra)


def quiz_node(quest: Quest, marks: set[str]) -> dict[str, Any]:
    """Вопрос бармена — тот, до которого дошли.

    Спрашивает он по одному и только то, на что ещё не ответили: верный
    ответ запоминается, и возвращаться к нему незачем. Набор берётся по
    коду активного задания — своих вопросов у каждого свои.
    """
    questions = quiz_of(quest.quiz)
    step = next(
        (
            number
            for number in range(len(questions))
            if quiz_marks(quest.quiz, number) not in marks
        ),
        0,
    )
    question = questions[step]
    return {
        "kind": "quiz",
        "text": question.text,
        "step": step,
        "total": len(questions),
        "replies": [
            reply("answer", text, step=step, option=number)
            for number, text in enumerate(question.options)
        ]
        + [reply("bye", BYE)],
    }


def _about(quest: Quest) -> dict[str, Any]:
    """Короткая справка о задании — она идёт рядом с репликой."""
    return {
        "code": quest.code,
        "title": quest.title,
        "task": quest.task,
        "chapter": quest.chapter_title,
        "min_level": quest.min_level,
        "exp": quest.exp,
        "credits": quest.credits,
        "win_bonus": quest.win_bonus,
        "advance": quest.advance,
        "gives": quest.gives,
        "gym_pass": quest.gym_pass,
        "reward": quest.reward_text,
    }


def talk_node(
    player: Player, log: dict[str, QuestLog], now: int | None = None
) -> dict[str, Any]:
    """Что бармен говорит прямо сейчас и что ему можно ответить."""
    taken = next((one for one in log.values() if one.taken), None)
    done = {one.code for one in log.values() if one.done}

    if taken is not None:
        quest = get_quest(taken.code)
        talk = talk_of(taken.code)
        if quest is None or talk is None:  # pragma: no cover - задание убрали
            return {"kind": "idle", "text": IDLE, "replies": [reply("bye", BYE)]}
        marks = set(taken.marks)
        if is_ready(quest, marks, player):
            return {
                "kind": "ready",
                "text": READY,
                "quest": _about(quest),
                "replies": [
                    reply("hand", HAND, quest=quest.code),
                    reply("bye", BYE),
                ],
            }
        # Вопросы бармен задаёт сам, как только условия закрыты
        if quiz_open(quest, marks, player):
            return dict(quiz_node(quest, marks), quest=_about(quest))
        return {
            "kind": "wait",
            # Сперва «пока не всё», потом чем именно: напоминание без
            # этой строки читается как новое задание
            "text": NOT_YET + "\n\n" + talk.hint,
            "quest": _about(quest),
            "replies": [reply("bye", BYE)],
        }

    ahead = next_quest(done, "", player.level)
    if ahead is None:
        # Либо всё сдано, либо глава кончилась раньше уровня — это
        # разные новости, и говорить их надо по-разному
        grow = locked_by(done, "", player.level)
        if grow:
            return {
                "kind": "grow",
                "text": grow_up(grow),
                "need_level": grow,
                "replies": [reply("bye", BYE)],
            }
        return {"kind": "idle", "text": IDLE, "replies": [reply("bye", BYE)]}
    talk = talk_of(ahead.code)
    if talk is None:  # pragma: no cover - у задания нет слов
        return {"kind": "idle", "text": IDLE, "replies": [reply("bye", BYE)]}
    # Первому заданию предшествует знакомство: с «новое лицо» разговор
    # начинается ровно один раз
    text = (HELLO + "\n\n" + talk.offer) if not done else talk.offer
    return {
        "kind": "offer",
        "text": text,
        "quest": _about(ahead),
        "replies": [
            reply("take", TAKE, quest=ahead.code),
            reply("later", LATER),
        ],
    }


def build_bar(
    player: Player,
    log: dict[str, QuestLog],
    now: int | None = None,
    said: str = "",
) -> dict[str, Any]:
    """Экран бара: бармен, его значок, разговор и журнал заданий."""
    moment = now_ts() if now is None else now
    talk = talk_node(player, log, moment)
    kind = talk["kind"]
    chapters = chapters_payload(player, log)
    rows = [one for chapter in chapters for one in chapter["quests"]]
    return {
        "title": "Бар",
        # Кошелёк в шапке: за кастет бармен даёт вперёд, и видеть,
        # сколько на руках, нужно прямо тут
        "credits": player.credits,
        "level": player.level,
        "npc": {
            "name": NAME,
            "title": TITLE,
            "picture": PICTURE,
            # Значок над барменом: есть ли к нему дело
            "mark": MARKS.get(kind, ""),
            "hint": {
                "offer": "У бармена есть работа",
                "quiz": "У бармена есть вопрос",
                "wait": "Бармен ждёт",
                "ready": "Бармену есть что сказать",
                "grow": "Бармен ждёт, пока подрастёшь",
                "idle": "Бармен не прочь поговорить",
            }.get(kind, ""),
        },
        "talk": talk,
        "chapters": chapters,
        "quests": rows,
        "done": sum(1 for row in rows if row["state"] == DONE),
        "total": len(rows),
        "said": said,
    }


__all__ = [
    "DONE",
    "LOCKED_LEVEL",
    "LOCKED_ORDER",
    "MARKS",
    "OPEN",
    "READY_STATE",
    "STATE_MARKS",
    "TAKEN",
    "build_bar",
    "chapters_payload",
    "quest_row",
    "quest_state",
    "quests_payload",
    "quiz_node",
    "talk_node",
]
