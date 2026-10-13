"""Бар глазами мини-аппа: бармен за стойкой и разговор с ним.

Правил здесь нет — их держит `bot.game.quests`, слова живут в
`bot.content.bartender`, действия — в `bot.quest_service`; тут перевод
состояния в json.

**Разговор собирает сервер, а не страница.** Узел один, и какой именно —
решает журнал заданий: нечего дать — бармен молчит о делах, дал — ждёт,
сделано — принимает. Страница рисует текст и кнопки ответов, не зная ни
одного правила: иначе половина разговора жила бы в браузере и там же
ломалась.

**Бармен стоит на картинке.** Он нарисован с прозрачным фоном и
ложится поверх вида изнутри — у стойки, справа. Значок над ним говорит,
есть ли дело: восклицательный знак — предлагает, часы — ждёт, галочка —
принимает работу.
"""

from __future__ import annotations

from typing import Any

from bot.content.bartender import (
    BYE,
    HELLO,
    IDLE,
    NAME,
    PICTURE,
    QUIZ,
    READY,
    TITLE,
    talk_of,
)
from bot.game.health import now_ts
from bot.game.progress import QuestLog
from bot.game.quests import (
    ORDER,
    QUESTS,
    Goal,
    Quest,
    get_quest,
    is_ready,
    next_quest,
    quiz_marks,
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
    "idle": "",
}


def quest_row(quest: Quest, log: QuestLog | None, player: Player) -> dict[str, Any]:
    """Строка задания в списке: что делать, чем кончится и как идёт.

    Три состояния и три значка: сдано — галочка, на руках — часы, ещё не
    предложено — ничего. Четвёртого нет: отказ ничего не меняет, то же
    задание предложат снова.
    """
    done = bool(log and log.done)
    taken = bool(log and log.taken)
    ready = bool(taken and log and is_ready(quest, set(log.marks), player))
    return {
        "code": quest.code,
        "title": quest.title,
        "task": quest.task,
        "exp": quest.exp,
        "credits": quest.credits,
        "state": "done" if done else "taken" if taken else "soon",
        "mark": "✅" if done else "🕓" if taken else "",
        # Сделано, но ещё не сдано: бармена надо найти и сказать
        "ready": ready,
        "number": ORDER.index(quest.code) + 1,
    }


def quests_payload(
    player: Player, log: dict[str, QuestLog]
) -> list[dict[str, Any]]:
    """Все задания бармена по порядку — он же список во вкладке."""
    return [quest_row(quest, log.get(quest.code), player) for quest in QUESTS]


def reply(code: str, text: str, **extra: Any) -> dict[str, Any]:
    """Ответ игрока: код для сервера, слова для экрана."""
    return dict({"code": code, "text": text}, **extra)


def quiz_node(quest: Quest, marks: set[str]) -> dict[str, Any]:
    """Вопрос бармена про PRO — тот, до которого дошли.

    Спрашивает он по одному и только то, на что ещё не ответили: верный
    ответ запоминается, и возвращаться к нему незачем.
    """
    step = next(
        (
            number
            for number in range(len(QUIZ))
            if quiz_marks(number) not in marks
        ),
        0,
    )
    question = QUIZ[step]
    return {
        "kind": "quiz",
        "text": question.text,
        "step": step,
        "total": len(QUIZ),
        "replies": [
            reply("answer", text, step=step, option=number)
            for number, text in enumerate(question.options)
        ]
        + [reply("bye", BYE)],
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
                "quest": {"code": quest.code, "title": quest.title},
                "replies": [
                    reply("hand", talk.hand, quest=quest.code),
                    reply("bye", BYE),
                ],
            }
        # Про PRO бармен спрашивает сам, как только боец сходил посмотреть
        if quest.goal is Goal.QUIZ and quest.mark in marks:
            return dict(
                quiz_node(quest, marks),
                quest={"code": quest.code, "title": quest.title},
            )
        return {
            "kind": "wait",
            "text": talk.hint,
            "quest": {"code": quest.code, "title": quest.title, "task": quest.task},
            "replies": [reply("bye", BYE)],
        }

    ahead = next_quest(done)
    if ahead is None:
        return {"kind": "idle", "text": IDLE, "replies": [reply("bye", BYE)]}
    talk = talk_of(ahead.code)
    if talk is None:  # pragma: no cover - у задания нет слов
        return {"kind": "idle", "text": IDLE, "replies": [reply("bye", BYE)]}
    # Первому заданию предшествует знакомство: с «ну садись» разговор
    # начинается ровно один раз
    text = (HELLO + "\n\n" + talk.offer) if not done else talk.offer
    return {
        "kind": "offer",
        "text": text,
        "quest": {
            "code": ahead.code,
            "title": ahead.title,
            "task": ahead.task,
            "exp": ahead.exp,
            "credits": ahead.credits,
        },
        "replies": [
            reply("take", talk.offer_yes, quest=ahead.code),
            reply("later", talk.offer_no),
        ],
    }


def build_bar(
    player: Player,
    log: dict[str, QuestLog],
    now: int | None = None,
    said: str = "",
) -> dict[str, Any]:
    """Экран бара: бармен, его значок, разговор и список заданий."""
    moment = now_ts() if now is None else now
    talk = talk_node(player, log, moment)
    kind = talk["kind"]
    rows = quests_payload(player, log)
    return {
        "title": "Бар",
        # Кошелёк в шапке: за кастет бармен даёт вперёд, и видеть,
        # сколько на руках, нужно прямо тут
        "credits": player.credits,
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
                "idle": "Бармен не прочь поговорить",
            }.get(kind, ""),
        },
        "talk": talk,
        "quests": rows,
        "done": sum(1 for row in rows if row["state"] == "done"),
        "total": len(rows),
        "said": said,
    }


__all__ = [
    "MARKS",
    "build_bar",
    "quest_row",
    "quests_payload",
    "quiz_node",
    "talk_node",
]
