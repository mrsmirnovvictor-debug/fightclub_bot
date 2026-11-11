"""Московские часы клуба: по ним считаются все события.

В базе метки времени лежат в UTC — так их ставит сама SQLite (`datetime('now')`
всегда даёт UTC) и так же пишет их Python. Это правильно: UTC не зависит от
того, где стоит сервер, и метки из разных мест сравниваются напрямую.

А вот показывать и раскладывать по дням их нужно по Москве. Клуб живёт по
московскому времени: расписание подвала московское, сутки наград кончаются в
московскую полночь, и статистику игрок читает по своим часам. Пока сутки в
списке боёв отсчитывались от UTC, рейд, отбитый в час ночи шестнадцатого,
попадал в статистику пятнадцатым — с точки зрения игрока просто в другой день.

Поэтому правило одно: **хранится UTC, показывается Москва**. Переводом занят
этот модуль, и только он. Ничего не пересчитывается в базе — старые записи
читаются тем же способом, что и новые, и после перевода встают на свои
настоящие места.

Смещение постоянное: в России часы не переводят с 2014 года.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Сутки клуба кончаются в полночь по Москве
MOSCOW = timezone(timedelta(hours=3))

# В каком виде метка может лежать в базе. Первый формат ставит SQLite,
# второй приходит из ISO-строк, остальные — из старых записей
STAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


# Неделя клуба начинается в воскресенье в полдень по Москве. По ней
# живут арена (`bot.game.arena`) и рейтинг недели (`bot.game.ladder`), и
# определение у неё одно — здесь. Два определения одной и той же недели
# однажды разъехались бы на час, и выяснилось бы это на выплате
WEEK_WEEKDAY = 6  # воскресенье: у `weekday()` понедельник ноль
WEEK_HOUR = 12
WEEK_SECONDS = 7 * 24 * 60 * 60


def club_day(moment: float) -> date:
    """Какой сегодня день по часам клуба."""
    return datetime.fromtimestamp(moment, MOSCOW).date()


def week_opens(moment: float) -> int:
    """Полдень того воскресенья, с которого идёт эта неделя клуба.

    Граница включающая: 11:59:59 воскресенья — ещё прошлая неделя,
    12:00:00 — уже новая. Поэтому полдень, оказавшийся в будущем,
    отступает на семь дней назад: воскресное утро принадлежит неделе,
    которая сегодня кончается, а не той, что начнётся в обед.
    """
    here = datetime.fromtimestamp(moment, MOSCOW)
    sunday = here - timedelta(days=(here.weekday() - WEEK_WEEKDAY) % 7)
    noon = sunday.replace(hour=WEEK_HOUR, minute=0, second=0, microsecond=0)
    if noon > here:
        noon -= timedelta(days=7)
    return int(noon.timestamp())


def week_closes(moment: float) -> int:
    """Когда эта неделя кончится — граница исключающая.

    Семь суток ровно, а не «следующее воскресенье»: в России часы не
    переводят с 2014 года, пояс у клуба постоянный, и неделя здесь
    всегда одной длины. Сложение смещения не знает — и потому точнее
    пересчёта по календарю.
    """
    return week_opens(moment) + WEEK_SECONDS


def week_key(moment: float) -> str:
    """Ключ недели: дата её воскресенья, «2026-11-08».

    Строкой, а не числом: ключ стоит в журнале выплат, и по нему потом
    глазами ищут, за какую именно неделю заплатили.
    """
    return datetime.fromtimestamp(week_opens(moment), MOSCOW).strftime("%Y-%m-%d")


def next_midnight(moment: float) -> float:
    """Ближайшая полночь по Москве — когда кончатся текущие сутки клуба."""
    here = datetime.fromtimestamp(moment, MOSCOW)
    midnight = (here + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight.timestamp()


def club_moment(moment: float, fmt: str = "%d.%m.%y %H:%M") -> str:
    """Момент времени для показа игроку — по московским часам.

    То же, что `club_time`, но для секунд, а не для метки из базы: сроки
    вроде подписки лежат числом, а не строкой.
    """
    return datetime.fromtimestamp(moment, MOSCOW).strftime(fmt)


def read_stamp(stamp: str | None) -> datetime | None:
    """Метка из базы как момент времени. None — прочитать не удалось.

    Всё, что лежит в базе без пометки о поясе, — это UTC.
    """
    if not stamp:
        return None
    text = stamp.strip()[:19]
    for fmt in STAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def to_moscow(stamp: str | None) -> datetime | None:
    """Метка из базы, переведённая на московские часы."""
    moment = read_stamp(stamp)
    return None if moment is None else moment.astimezone(MOSCOW)


def club_date(stamp: str | None) -> str:
    """«2026-09-15 21:30:00» → «2026-09-16»: день по московским часам.

    По этой строке бои раскладываются по дням. Пустая — если метку не
    разобрать: пусть лучше бой встанет отдельной кучкой, чем попадёт не в
    свой день.
    """
    moment = to_moscow(stamp)
    return "" if moment is None else moment.strftime("%Y-%m-%d")


def club_time(stamp: str | None, fmt: str = "%d.%m.%y %H:%M") -> str:
    """Метка из базы для показа игроку — по московским часам."""
    moment = to_moscow(stamp)
    return "" if moment is None else moment.strftime(fmt)


__all__ = [
    "MOSCOW",
    "STAMP_FORMATS",
    "WEEK_HOUR",
    "WEEK_SECONDS",
    "WEEK_WEEKDAY",
    "club_date",
    "club_day",
    "club_moment",
    "club_time",
    "next_midnight",
    "read_stamp",
    "to_moscow",
    "week_closes",
    "week_key",
    "week_opens",
]
