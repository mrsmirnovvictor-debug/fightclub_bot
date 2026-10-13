"""Квесты бармена: десять заданий, с которых начинается город.

Квест — это не история, а повод дойти ногами. Город большой, домов в нём
три десятка, и новичок обходит их годами; бармен проводит по десяти
главным за вечер: ринг, элитный магазин, оружейник, банк, страховая,
агентство, автошкола, ювелир, комиссионка и зал.

**Берут по одному и по порядку.** Пока первое не сдано, второе не
предлагают: это не ветка, а лестница. Отказаться можно — бармен не
обидится и предложит то же в следующий раз.

**Проверяет город, а не слово игрока.** Бармен не спрашивает «сходил
ли» — он смотрит. Часть заданий закрывается событием (выиграл бой, дошёл
до двери, отработал тренировку, выставил вещь), и о событии службы
говорят сами; часть — состоянием бойца (счёт открыт, работа есть, вещь в
рюкзаке), и его видно без всякой памяти. Поэтому обмануть квест нельзя:
соврать можно только тому, кто слушает.

Правила здесь, слова бармена — в `bot.content.bartender`, действия — в
`bot.quest_service`, хранение — в `bot.database`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Кто выдаёт. Пока один на весь город, но код стоит в записи: вторым
# будет кто-нибудь ещё, и тогда список заданий у каждого станет своим
BARTENDER = "bartender"

# Сколько дней держится абонемент, который бармен даёт на тренировку
GYM_DAYS = 1


class Goal(str, Enum):
    """Чем задание закрывается — то есть что именно город должен увидеть."""

    FIGHT = "fight"  # выиграть бой
    VISIT = "visit"  # дойти до двери и войти
    ITEM = "item"  # принести вещь
    ACCOUNT = "account"  # открыть счёт в банке
    JOB = "job"  # устроиться на работу
    SCHOOL = "school"  # записаться на курс в автошколе
    MARKET = "market"  # выставить вещь в комиссионку
    TRAIN = "train"  # отработать тренировку в зале
    QUIZ = "quiz"  # ответить бармену на вопросы


# Какие цели закрываются событием, а какие видно по самому бойцу. Первые
# помнит запись квеста, вторые не нужно помнить вовсе
BY_EVENT: frozenset[Goal] = frozenset(
    {Goal.FIGHT, Goal.VISIT, Goal.MARKET, Goal.TRAIN, Goal.QUIZ}
)


@dataclass(frozen=True)
class Quest:
    """Задание бармена: что сделать и что за это будет."""

    code: str
    title: str
    task: str  # строка задания — она же стоит в списке квестов
    goal: Goal
    target: str = ""  # услуга для `VISIT`, код вещи для `ITEM`, место работы
    exp: int = 50
    credits: int = 0  # награда кредитами
    advance: int = 0  # сколько бармен даёт вперёд, при взятии
    gives: str = ""  # вещь, которую бармен даёт при взятии
    takes: bool = False  # забирает ли принесённую вещь себе
    gym_pass: bool = False  # даёт ли свой абонемент в зал

    @property
    def by_event(self) -> bool:
        """Ждёт ли задание события от города."""
        return self.goal in BY_EVENT

    @property
    def mark(self) -> str:
        """Какого события ждёт задание. Пусто — событий не ждём.

        У разговорного задания событие — дверь: бармен спрашивает про
        то, что боец видел своими глазами, и до похода вопросов нет.
        """
        if not self.by_event:
            return ""
        goal = Goal.VISIT if self.goal is Goal.QUIZ else self.goal
        return f"{goal.value}:{self.target}" if self.target else goal.value


QUESTS: tuple[Quest, ...] = (
    Quest(
        code="ring",
        title="Первая кровь",
        task="Зайти в Бойцовский клуб, вызвать бойца на кулачный бой и выиграть",
        goal=Goal.FIGHT,
    ),
    Quest(
        code="pro",
        title="Что дают за звёзды",
        task="Заглянуть в элитный магазин и вернуться с ответами про PRO",
        goal=Goal.QUIZ,
        target="premium",
    ),
    Quest(
        code="knuckles",
        title="Кастет для бармена",
        task="Купить кастет в оружейном магазине и принести его бармену",
        goal=Goal.ITEM,
        target="knuckles",
        credits=100,
        advance=80,
        takes=True,
    ),
    Quest(
        code="bank",
        title="Деньги любят счёт",
        task="Открыть счёт в Vegas Банке",
        goal=Goal.ACCOUNT,
    ),
    Quest(
        code="policy",
        title="На всякий случай",
        task="Зайти в страховую компанию и узнать, что даёт полис",
        goal=Goal.VISIT,
        target="insurance",
    ),
    Quest(
        code="work",
        title="Своя смена",
        task="Устроиться барменом через HR-агентство",
        goal=Goal.JOB,
        target="bartender",
    ),
    Quest(
        code="licence",
        title="Права на будущее",
        task="Записаться на курс ПДД в автошколе",
        goal=Goal.SCHOOL,
        credits=300,
    ),
    Quest(
        code="ring_gift",
        title="Кольцо для бармена",
        task="Купить кольцо силы у ювелира и принести его бармену",
        goal=Goal.ITEM,
        target="ring_strength",
        credits=100,
        takes=True,
    ),
    Quest(
        code="market",
        title="Лишнее — на прилавок",
        task="Выставить кастет бармена в комиссионный магазин",
        goal=Goal.MARKET,
        gives="knuckles",
    ),
    Quest(
        code="gym",
        title="Железо не ждёт",
        task="Отработать тренировку в тренажёрном зале",
        goal=Goal.TRAIN,
        exp=100,
        gym_pass=True,
    ),
)

BY_CODE: dict[str, Quest] = {quest.code: quest for quest in QUESTS}

# Порядок — он же лестница: задание открывается, когда сдано прошлое
ORDER: tuple[str, ...] = tuple(quest.code for quest in QUESTS)


def get_quest(code: str) -> Quest | None:
    return BY_CODE.get(code)


def next_quest(done: set[str], taken: str = "") -> Quest | None:
    """Какое задание бармен предложит следующим. None — все сданы.

    Берут по одному: пока одно на руках, второго не дают. Отказ ничего
    не меняет — то же задание предложат снова.
    """
    if taken:
        return None
    for code in ORDER:
        if code not in done:
            return BY_CODE[code]
    return None


def is_ready(quest: Quest, marks: set[str], player) -> bool:
    """Сделано ли задание. Смотрим город, а не слово бойца."""
    if quest.goal is Goal.QUIZ:
        # Сперва дойти, потом ответить: вопросы про то, что видно на
        # прилавке, и спрашивать их раньше похода незачем
        return quest.mark in marks and QUIZ_DONE in marks
    if quest.by_event:
        return quest.mark in marks
    if quest.goal is Goal.ITEM:
        return any(
            owned.code == quest.target and not owned.is_equipped
            for owned in player.gear
        )
    if quest.goal is Goal.ACCOUNT:
        return player.has_account
    if quest.goal is Goal.JOB:
        return player.job_code == quest.target
    if quest.goal is Goal.SCHOOL:
        return player.school.enrolled
    return False  # pragma: no cover - цель без проверки не заводится


# Отметка о том, что бармен получил все ответы про PRO
QUIZ_DONE = "quiz:done"


def quiz_marks(step: int) -> str:
    """Отметка о верном ответе на вопрос номер `step`."""
    return f"quiz:{step}"


__all__ = [
    "BARTENDER",
    "BY_CODE",
    "BY_EVENT",
    "GYM_DAYS",
    "ORDER",
    "QUESTS",
    "QUIZ_DONE",
    "Goal",
    "Quest",
    "get_quest",
    "is_ready",
    "next_quest",
    "quiz_marks",
]
