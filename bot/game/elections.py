"""Выборы мэра: когда они идут и кто в них когда что может.

Раз в месяц город выбирает мэра. Здесь только календарь и границы —
кто за кого проголосовал, знает `bot.election_service`, экраны собирает
`bot.webapp.elections`.

**День один и считается, а не хранится.** Выборы — первое воскресенье
месяца, и это правило, а не запись в таблице: по любому часу видно, к
каким выборам он относится и что в этот час можно. Таблица хранит
только то, чего из календаря не выведешь: кто выдвинулся и кто за кого
отдал голос.

**Неделя выборов — та, что кончается этим воскресеньем.** Клубная
неделя начинается в понедельник в девять утра, и воскресенье — её
последний день. Поэтому выдвижение со среды по субботу лежит внутри той
же недели, что и само голосование, а новый мэр заступает уже в
следующей.

Часы по Москве, как и всё остальное в клубе.

    ср 09:00 ──── сб 20:00   вс 08:00 ── вс 20:00   пн 09:00
    │  выдвижение        │  │ голосование     │     │ мэр в правах
    └────────────────────┘  └─────────────────┘     └──────────────

**Между субботой и воскресеньем — затишье.** Список кандидатов уже
закрыт, а бюллетень ещё не открыт. Час этот нужен не для красоты:
выдвинуться в последнюю минуту перед голосованием значило бы попасть в
бюллетень, которого никто не успел прочесть.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from bot.game.clock import MOSCOW

# Часы по Москве: когда открывается выдвижение, когда закрывается, когда
# идёт голосование и когда новый мэр заступает
NOMINATION_FROM = time(9, 0)
NOMINATION_TO = time(20, 0)
VOTING_FROM = time(8, 0)
VOTING_TO = time(20, 0)
TERM_FROM = time(9, 0)

# Сколько знаков в предвыборной программе. Пустая — тоже программа:
# кандидат вправе не обещать ничего
PROGRAM_MAX = 400

# Фазы цикла. Их пять, и по любому часу видно, в какой он
QUIET = "quiet"  # до среды: выборы впереди, делать нечего
NOMINATION = "nomination"  # со среды по субботу: выдвигаются
EVE = "eve"  # ночь на воскресенье: список закрыт, бюллетень ещё нет
VOTING = "voting"  # воскресенье с восьми до восьми: голосуют
COUNTING = "counting"  # вечер воскресенья: считают

PHASE_TITLES: dict[str, str] = {
    QUIET: "До выборов",
    NOMINATION: "Выдвижение кандидатов",
    EVE: "Список закрыт",
    VOTING: "Идут выборы мэра",
    COUNTING: "Подсчёт голосов",
}


def _moscow(moment: int) -> datetime:
    return datetime.fromtimestamp(moment, MOSCOW)


def _stamp(day: date, clock: time) -> int:
    return int(datetime.combine(day, clock, MOSCOW).timestamp())


# `date.weekday()`: понедельник 0, воскресенье 6
SUNDAY = 6


def first_sunday(year: int, month: int) -> date:
    """Первое воскресенье этого месяца."""
    first = date(year, month, 1)
    return first + timedelta(days=(SUNDAY - first.weekday()) % 7)


def next_month(day: date) -> tuple[int, int]:
    year, month = day.year, day.month
    return (year + 1, 1) if month == 12 else (year, month + 1)


def nomination_from(day: date) -> int:
    """Среда той же недели, девять утра: открылось выдвижение."""
    return _stamp(day - timedelta(days=4), NOMINATION_FROM)


def nomination_to(day: date) -> int:
    """Суббота, восемь вечера: список кандидатов закрыт."""
    return _stamp(day - timedelta(days=1), NOMINATION_TO)


def voting_from(day: date) -> int:
    return _stamp(day, VOTING_FROM)


def voting_to(day: date) -> int:
    return _stamp(day, VOTING_TO)


def term_from(day: date) -> int:
    """Понедельник, девять утра: новый мэр в правах, прежний — нет."""
    return _stamp(day + timedelta(days=1), TERM_FROM)


def election_day(moment: int) -> date:
    """К каким выборам относится этот час.

    Пока прежние не вступили в силу — к ним. Вступили — к следующим: с
    этой минуты прошлые выборы уже история, и смотреть на них незачем.
    """
    here = _moscow(moment).date()
    day = first_sunday(here.year, here.month)
    if moment >= term_from(day):
        year, month = next_month(day)
        day = first_sunday(year, month)
    return day


def phase(moment: int) -> str:
    """В какой фазе цикл прямо сейчас."""
    day = election_day(moment)
    if moment < nomination_from(day):
        return QUIET
    if moment < nomination_to(day):
        return NOMINATION
    if moment < voting_from(day):
        return EVE
    if moment < voting_to(day):
        return VOTING
    return COUNTING


def phase_ends(moment: int) -> int:
    """Когда кончится текущая фаза. По этому и идёт отсчёт на экране."""
    day = election_day(moment)
    now_phase = phase(moment)
    return {
        QUIET: nomination_from(day),
        NOMINATION: nomination_to(day),
        EVE: voting_from(day),
        VOTING: voting_to(day),
        COUNTING: term_from(day),
    }[now_phase]


def can_nominate(moment: int) -> bool:
    return phase(moment) == NOMINATION


def can_vote(moment: int) -> bool:
    return phase(moment) == VOTING


def day_code(day: date) -> str:
    """Ключ выборов в базе: дата голосования по Москве."""
    return day.isoformat()


def winner_of(tally: dict[int, int]) -> tuple[int, int]:
    """Кто победил и с каким счётом. Ноль — победителя нет.

    При равенстве голосов победителя нет вовсе: жребий тут был бы
    решением за город, а второго тура мы не заводили. Нет победителя —
    кресло остаётся пустым до следующего месяца.
    """
    if not tally:
        return 0, 0
    best = max(tally.values())
    leaders = [one for one, votes in tally.items() if votes == best]
    if len(leaders) != 1 or not best:
        return 0, best
    return leaders[0], best


__all__ = [
    "COUNTING",
    "EVE",
    "NOMINATION",
    "PHASE_TITLES",
    "PROGRAM_MAX",
    "QUIET",
    "VOTING",
    "can_nominate",
    "can_vote",
    "day_code",
    "election_day",
    "first_sunday",
    "next_month",
    "nomination_from",
    "nomination_to",
    "phase",
    "phase_ends",
    "term_from",
    "voting_from",
    "voting_to",
    "winner_of",
]
