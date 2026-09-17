"""Все события клуба считаются по московским часам.

Метки в базе лежат в UTC — так их ставит SQLite. Игрок же читает их по
своим часам, и это не косметика: рейд, отбитый в час ночи шестнадцатого,
попадал в статистику пятнадцатым, потому что в UTC это ещё вчерашние
сутки. Здесь проверяется, что переход через московскую полночь считается
московской полуночью, а не любой другой.
"""

from datetime import datetime, timezone

import pytest

from bot.game.clock import (
    MOSCOW,
    club_date,
    club_day,
    club_time,
    next_midnight,
    read_stamp,
    to_moscow,
)


def utc_stamp(year, month, day, hour=0, minute=0) -> str:
    """Метка в том виде, в каком её кладёт в базу SQLite: UTC, без пояса."""
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"


# ---------- перевод метки ----------


def test_a_stamp_without_a_zone_is_utc():
    moment = read_stamp("2026-09-15 21:30:00")

    assert moment == datetime(2026, 9, 15, 21, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-09-15 21:30:00",
        "2026-09-15T21:30:00",
        "2026-09-15 21:30",
    ],
)
def test_every_shape_the_base_stores_is_understood(stamp):
    assert club_date(stamp) == "2026-09-16"


def test_a_bare_date_stays_its_own_day():
    """Полночь UTC — это три часа ночи в Москве, число то же."""
    assert club_date("2026-09-15") == "2026-09-15"


def test_junk_does_not_pretend_to_be_a_day():
    """Непонятную метку лучше оставить без дня, чем приписать к чужому."""
    assert read_stamp("непонятно") is None
    assert read_stamp("") is None and read_stamp(None) is None
    assert club_date("непонятно") == "" and club_time(None) == ""
    assert to_moscow("непонятно") is None


# ---------- сутки ----------


def test_the_night_raid_counts_as_the_new_day():
    """Тот самый случай: рейд с 00:00 до 02:00 шестнадцатого.

    В UTC это ещё двадцать первый час пятнадцатого — и в статистике бой
    вставал вчерашним днём.
    """
    # 00:30 мск 16 сентября — это 21:30 UTC 15 сентября
    assert club_date(utc_stamp(2026, 9, 15, 21, 30)) == "2026-09-16"
    assert club_date(utc_stamp(2026, 9, 15, 23, 0)) == "2026-09-16"
    # а без перевода это был бы пятнадцатый день
    assert utc_stamp(2026, 9, 15, 21, 30)[:10] == "2026-09-15"


def test_the_day_turns_over_exactly_at_moscow_midnight():
    # 20:59:59 UTC — ещё 15-е по Москве (23:59:59)
    assert club_date("2026-09-15 20:59:59") == "2026-09-15"
    # 21:00:00 UTC — ровно полночь по Москве, уже 16-е
    assert club_date("2026-09-15 21:00:00") == "2026-09-16"


def test_the_evening_fight_stays_on_its_own_day():
    """Перевод не должен сдвигать то, что и так лежало верно."""
    assert club_date(utc_stamp(2026, 9, 15, 12, 0)) == "2026-09-15"


def test_the_day_of_a_moment_is_the_moscow_one():
    late = datetime(2026, 9, 16, 0, 15, tzinfo=MOSCOW)

    assert late.astimezone(timezone.utc).day == 15, "в UTC это ещё вчера"
    assert club_day(late.timestamp()).day == 16


def test_midnight_is_the_moscow_one():
    moment = datetime(2026, 9, 15, 23, 45, tzinfo=MOSCOW).timestamp()

    resets = next_midnight(moment)

    assert datetime.fromtimestamp(resets, MOSCOW).hour == 0
    assert club_day(resets).day == 16
    assert 0 < resets - moment <= 3600


# ---------- показ ----------


def test_the_shown_time_is_the_moscow_one():
    assert club_time("2013-10-26 22:31:00") == "27.10.13 01:31"
    assert club_time("2026-09-15 21:30:00", "%H:%M") == "00:30"


# ---------- один и тот же час во всём клубе ----------


def test_the_club_keeps_a_single_clock():
    """Расписание подвала, награды за вход и статистика — одни часы."""
    from bot.game.daily import MOSCOW as daily_moscow
    from bot.game.raid import MOSCOW as raid_moscow

    assert daily_moscow is MOSCOW and raid_moscow is MOSCOW


# ---------- статистика ----------


def duel_row(stamp: str, fight_id: int = 1) -> dict:
    return {
        "id": fight_id,
        "chat_id": None,
        "challenger_id": 42,
        "opponent_id": 7,
        "challenger_name": "Тайлер",
        "opponent_name": "Марла",
        "winner_id": 42,
        "rounds": 3,
        "end_reason": "knockout",
        "mode": "classic",
        "created_at": stamp,
    }


def raid_row_of(stamp: str, raid_id: int = 1) -> dict:
    return {
        "id": raid_id,
        "boss": "cellar",
        "boss_level": 4,
        "outcome": "win",
        "waves": 6,
        "damage": 120,
        "alive": 1,
        "prize": None,
        "allies": "",
        "created_at": stamp,
    }


def test_the_night_raid_lands_in_the_right_day_of_the_history():
    """Рейд с 00:00 до 02:00 шестнадцатого — шестнадцатым и записан."""
    from bot.webapp.fight import build_history

    history = build_history(
        [],
        42,
        "Тайлер",
        raids=[raid_row_of("2026-09-15 21:30:00")],  # 00:30 мск 16-го
    )

    assert [day["date"] for day in history["days"]] == ["2026-09-16"]


def test_a_night_and_an_evening_fight_are_different_days():
    """Вечерний бой и ночной после него — разные сутки, а не одни."""
    from bot.webapp.fight import build_history

    history = build_history(
        [
            duel_row("2026-09-15 17:00:00", 1),  # 20:00 мск 15-го
            duel_row("2026-09-15 22:10:00", 2),  # 01:10 мск 16-го
        ],
        42,
        "Тайлер",
    )

    assert [day["date"] for day in history["days"]] == ["2026-09-16", "2026-09-15"]
    assert [len(day["fights"]) for day in history["days"]] == [1, 1]


def test_fights_of_one_moscow_day_stay_together():
    """Час дня и одиннадцать вечера по Москве — одни сутки.

    Перевод не должен дробить день: в UTC второй бой уже перевалил за
    двадцать часов, но московская полночь ещё не наступила.
    """
    from bot.webapp.fight import build_history

    history = build_history(
        [
            duel_row("2026-09-15 10:00:00", 1),  # 13:00 мск
            duel_row("2026-09-15 20:00:00", 2),  # 23:00 мск, тот же день
        ],
        42,
        "Тайлер",
    )

    assert [day["date"] for day in history["days"]] == ["2026-09-15"]
    assert len(history["days"][0]["fights"]) == 2
