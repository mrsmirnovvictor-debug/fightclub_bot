"""Турнирная арена: недельный плей-офф по расписанию.

Клуб живёт вечерами, а арена — неделями. Воскресенье: в полдень
открывается запись, в шесть она закрывается, и с этой минуты всё идёт
по часам — первые бои в 18:05, каждый следующий круг через полчаса.

**Расписание считается, а не хранится.** Из любого часа видно, что
сейчас с ареной: запись, жеребьёвка, круг номер такой-то или уже
ничего. Хранить расписание в базе значило бы заводить второй источник
правды о времени, а время у клуба одно — московское.

**Взнос собирается в пулл, клуб добавляет половину.** Двести кредитов
с бойца; сколько собралось — столько же плюс половина сверху, и это
весь призовой фонд. Победителю восемьдесят процентов, второму — двадцать:
за второе место платят, потому что дойти до финала — это выиграть все
бои, кроме одного.

**На бой выходят оба.** У пары есть пять минут, чтобы подтвердить
выход; подтвердили оба — гонг сразу. Не подтвердил один — проходит
тот, кто подтвердил: соперник не пришёл, и ждать его круг не станет.
Не подтвердил никто — дальше идёт старший по уровню, потому что сетка
должна ехать дальше, а бой, на который не вышли, не состоялся.

**Сетка едет на сильных.** Если бойцов не степень двойки, лишние места
в первом круге достаются сильнейшим по уровню: они проходят без боя.
Это обычный посев — сильный встречает сильного не раньше, чем должен.

Сетка и пары — в `bot.game.bracket`, ход турнира — в
`bot.arena_service`, экран — в `bot.webapp.arena`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.game.clock import MOSCOW

MINUTE = 60
HOUR = 60 * MINUTE

# Когда что происходит. Воскресенье по московским часам: в полдень
# открывается запись, в шесть закрывается
OPEN_WEEKDAY = 6  # воскресенье
OPEN_HOUR = 12
CLOSE_HOUR = 18
# Первый бой — через пять минут после закрытия записи: столько нужно,
# чтобы посеять сетку и позвать людей
FIRST_MATCH = 5 * MINUTE
# Каждый следующий круг — через полчаса после начала предыдущего
ROUND_EVERY = 30 * MINUTE
# Сколько у пары есть на то, чтобы выйти на бой
CONFIRM_SECONDS = 5 * MINUTE

# Взнос, доля клуба сверх собранного и как делится пулл
ENTRY_FEE = 200
CLUB_SHARE = 0.5
WINNER_SHARE = 0.8
RUNNER_SHARE = 0.2

# Меньше двоих — турнира нет: взносы возвращаются
MIN_PLAYERS = 2

# Докуда тянется турнирный вечер: до московской полуночи. Кругов
# больше пяти не бывает даже при полной сетке, и к полуночи всё давно
# сыграно — а без этой границы «идут круги» висело бы до следующего
# воскресенья
ENDS_HOUR = 24

# Что сейчас с ареной
OPEN = "open"  # идёт запись
SEEDING = "seeding"  # запись закрыта, сетку вот-вот посеют
FIGHTING = "fighting"  # идут круги
DONE = "done"  # турнир этой недели сыгран

PHASE_TITLES: dict[str, str] = {
    OPEN: "Идёт запись",
    SEEDING: "Жеребьёвка",
    FIGHTING: "Турнир идёт",
    DONE: "Турнир сыгран",
}


def _moscow(moment: int) -> datetime:
    return datetime.fromtimestamp(moment, MOSCOW)


def opens_at(moment: int) -> int:
    """Полдень того воскресенья, к которому относится этот час.

    До воскресенья — ближайшее будущее; после полудня воскресенья и до
    следующего — то самое, уже наступившее.
    """
    here = _moscow(moment)
    sunday = here - timedelta(days=(here.weekday() - OPEN_WEEKDAY) % 7)
    noon = sunday.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
    if noon > here:
        # Воскресенье, но ещё до полудня: неделя та, что кончается сегодня
        noon -= timedelta(days=7)
    return int(noon.timestamp())


def week_of(moment: int) -> str:
    """Ключ недели: дата того воскресенья, в которое играют."""
    return _moscow(opens_at(moment)).strftime("%Y-%m-%d")


def closes_at(moment: int) -> int:
    """Когда закрывается запись этой недели."""
    return opens_at(moment) + (CLOSE_HOUR - OPEN_HOUR) * HOUR


def ends_at(moment: int) -> int:
    """Когда турнирный вечер этой недели кончается — в полночь."""
    return opens_at(moment) + (ENDS_HOUR - OPEN_HOUR) * HOUR


def round_at(moment: int, number: int) -> int:
    """Когда начинается круг с этим номером. Первый — в 18:05."""
    return closes_at(moment) + FIRST_MATCH + ROUND_EVERY * (max(1, number) - 1)


def round_now(moment: int) -> int:
    """Какой круг идёт по часам. Ноль — бои ещё не начинались."""
    first = round_at(moment, 1)
    if moment < first:
        return 0
    return int((moment - first) // ROUND_EVERY) + 1


def next_opening(moment: int) -> int:
    """Когда откроется ближайшая запись. Идёт сейчас — эта же."""
    opened = opens_at(moment)
    return opened if moment < closes_at(moment) else opened + 7 * 24 * HOUR


def phase_of(moment: int, played: bool = False) -> str:
    """Что сейчас с ареной. `played` — турнир этой недели уже сыгран."""
    if played or moment >= ends_at(moment):
        return DONE
    if moment < closes_at(moment):
        return OPEN
    if moment < round_at(moment, 1):
        return SEEDING
    return FIGHTING


def pot_of(entries: int) -> int:
    """Призовой фонд: взносы плюс половина от клуба."""
    collected = max(0, entries) * ENTRY_FEE
    return collected + int(collected * CLUB_SHARE)


def prizes(pot: int) -> tuple[int, int]:
    """Сколько получат первый и второй. Остаток от деления — первому."""
    runner = int(pot * RUNNER_SHARE)
    return pot - runner, runner


def confirm_left(opened_at: int, now: int) -> int:
    """Сколько секунд ещё ждут выхода на бой. Ноль — время вышло."""
    if not opened_at:
        return 0
    return max(0, opened_at + CONFIRM_SECONDS - now)


@dataclass(frozen=True)
class Standing:
    """Боец в сетке: кто он и чем для него кончился турнир."""

    user_id: int
    name: str
    level: int
    out_round: int = 0  # в каком круге вылетел; ноль — ещё в игре
    place: int = 0  # 1 или 2 у призёров, иначе ноль


__all__ = [
    "CLOSE_HOUR",
    "ENDS_HOUR",
    "ends_at",
    "CLUB_SHARE",
    "CONFIRM_SECONDS",
    "DONE",
    "ENTRY_FEE",
    "FIGHTING",
    "FIRST_MATCH",
    "MIN_PLAYERS",
    "OPEN",
    "OPEN_HOUR",
    "OPEN_WEEKDAY",
    "PHASE_TITLES",
    "ROUND_EVERY",
    "RUNNER_SHARE",
    "SEEDING",
    "Standing",
    "WINNER_SHARE",
    "closes_at",
    "confirm_left",
    "next_opening",
    "opens_at",
    "phase_of",
    "pot_of",
    "prizes",
    "round_at",
    "round_now",
    "week_of",
]
