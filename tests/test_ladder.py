"""Правила недельного рейтинга: неделя, порог, порядок и призы.

Здесь только счёт без базы: граница недели, призовая лестница и четыре
ключа равенства. Журнал ELO, снимок периода и выплаты — в
`tests/test_ladder_pay.py`, экран — в `tests/test_webapp.py`.
"""

from datetime import datetime, timedelta

from bot.game.clock import (
    MOSCOW,
    WEEK_SECONDS,
    week_closes,
    week_key,
    week_opens,
)
from bot.game.ladder import (
    PRIZE_STEPS,
    QUALIFY,
    Standing,
    period_of,
    period_text,
    place_standings,
    prize_for,
    prize_table,
    progress_text,
)


def msk(year, month, day, hour=0, minute=0, second=0) -> int:
    """Московский момент числом — так его видит сервер."""
    return int(
        datetime(year, month, day, hour, minute, second, tzinfo=MOSCOW).timestamp()
    )


def row(user_id: int, rating: int, **kwargs) -> Standing:
    return Standing(
        user_id=user_id,
        rating=rating,
        fights=kwargs.pop("fights", QUALIFY),
        wins=kwargs.pop("wins", 5),
        losses=kwargs.pop("losses", 5),
        reached=kwargs.pop("reached", 0),
    )


# ---------- неделя клуба ----------


def test_the_week_runs_from_sunday_noon_to_sunday_noon():
    """Неделя клуба начинается в воскресный полдень и длится семь суток."""
    # 8 ноября 2026 — воскресенье
    sunday = msk(2026, 11, 8, 12)
    assert week_opens(sunday) == sunday
    assert week_closes(sunday) == sunday + WEEK_SECONDS
    assert week_closes(sunday) == msk(2026, 11, 15, 12)
    assert week_key(sunday) == "2026-11-08"


def test_sunday_morning_belongs_to_the_week_that_ends_today():
    """11:59:59 воскресенья — старая неделя, 12:00:00 — новая.

    Это та самая граница, на которой считаются бои: бой, кончившийся
    секундой раньше, идёт в закрытый итог, а секундой позже — в новый.
    """
    last = msk(2026, 11, 15, 11, 59, 59)
    first = msk(2026, 11, 15, 12, 0, 0)

    assert week_key(last) == "2026-11-08"
    assert week_key(first) == "2026-11-15"
    # И граница у них общая: конец старой — это начало новой
    assert week_closes(last) == week_opens(first) == first


def test_every_hour_of_the_week_names_the_same_sunday():
    """Любой час недели называет её воскресенье, и никакой другой."""
    opened = msk(2026, 11, 8, 12)
    hour = 3600
    moment = opened
    while moment < opened + WEEK_SECONDS:
        assert week_opens(moment) == opened, moment
        assert week_key(moment) == "2026-11-08", moment
        moment += hour
    # А первый час следующей недели — уже другая
    assert week_key(opened + WEEK_SECONDS) == "2026-11-15"


def test_the_week_is_seven_days_long_however_it_is_counted():
    """Семь суток сложением и семь суток по календарю — одно и то же.

    В России часы не переводят с 2014 года, пояс у клуба постоянный, и
    сложение здесь точнее календаря. Проверка держит это обещание: если
    в поясе однажды появится перевод, сломается она, а не выплата.
    """
    moment = msk(2026, 3, 1, 9)
    for _ in range(60):
        opened = datetime.fromtimestamp(week_opens(moment), MOSCOW)
        assert (
            week_closes(moment) == int((opened + timedelta(days=7)).timestamp())
        ), moment
        moment += 3 * 24 * 3600


def test_the_arena_and_the_rating_live_by_the_same_week():
    """У арены и у рейтинга неделя одна: определение в одном месте."""
    from bot.game.arena import opens_at, week_of

    moment = msk(2026, 11, 11, 20)
    assert opens_at(moment) == week_opens(moment)
    assert week_of(moment) == week_key(moment) == period_of(moment)


def test_the_period_says_its_own_borders_in_words():
    """Границы периода подписаны на экране — чтобы не угадывать."""
    said = period_text(msk(2026, 11, 11, 20))
    assert "08.11 12:00" in said
    assert "15.11 12:00" in said
    assert "Москв" in said


# ---------- порог ----------


def test_ten_fights_is_enough_and_nine_is_not():
    """«Минимум десять» — это десять, а не одиннадцать."""
    assert QUALIFY == 10
    assert not row(1, 2000, fights=9).qualified
    assert row(1, 1000, fights=10).qualified
    assert row(1, 1000, fights=99).qualified


def test_the_progress_counts_up_to_the_threshold_and_stops():
    """Личный прогресс не перерастает порога: «10 из 10», не «14 из 10»."""
    assert progress_text(0) == "0 из 10 PvP-боёв в Бойцовском клубе"
    assert progress_text(7) == "7 из 10 PvP-боёв в Бойцовском клубе"
    assert progress_text(10) == "10 из 10 PvP-боёв в Бойцовском клубе"
    assert progress_text(14) == "10 из 10 PvP-боёв в Бойцовском клубе"
    assert row(1, 1000, fights=7).left == 3
    assert row(1, 1000, fights=10).left == 0
    assert row(1, 1000, fights=14).left == 0


def test_a_fighter_below_the_threshold_takes_no_place_at_all():
    """Девять боёв — не место в хвосте, а отсутствие в списке.

    Иначе непрошедший сдвинул бы всех, кто ниже, и четвёртый получил бы
    премию за пятое место.
    """
    places = place_standings(
        [row(1, 2000, fights=9), row(2, 1500), row(3, 1400)]
    )
    assert [(place, one.user_id) for place, one in places] == [(1, 2), (2, 3)]


# ---------- порядок мест ----------


def test_the_places_go_by_overall_elo_not_by_the_weekly_gain():
    """Выше тот, у кого общий ELO больше. Прирост недели ни при чём."""
    places = place_standings(
        [row(1, 1200, wins=1, losses=9), row(2, 1100, wins=9, losses=1)]
    )
    assert [one.user_id for _, one in places] == [1, 2]


def test_equal_elo_is_broken_by_the_wins_of_the_week():
    places = place_standings([row(1, 1500, wins=3), row(2, 1500, wins=7)])
    assert [one.user_id for _, one in places] == [2, 1]


def test_equal_wins_are_broken_by_the_fewer_losses():
    places = place_standings(
        [row(1, 1500, wins=5, losses=9), row(2, 1500, wins=5, losses=2)]
    )
    assert [one.user_id for _, one in places] == [2, 1]


def test_the_one_who_reached_this_elo_earlier_stands_higher():
    places = place_standings(
        [
            row(1, 1500, wins=5, losses=5, reached=900),
            row(2, 1500, wins=5, losses=5, reached=100),
        ]
    )
    assert [one.user_id for _, one in places] == [2, 1]


def test_a_full_tie_is_broken_by_the_fighter_number():
    """Полное равенство разводит номер бойца — и только он.

    Без последнего ключа порядок равных строк не определён, и один и
    тот же боец мог бы попасть на две страницы сразу или ни на одну.
    """
    places = place_standings([row(51, 1500), row(7, 1500), row(42, 1500)])
    assert [one.user_id for _, one in places] == [7, 42, 51]


def test_the_keys_are_weighed_in_the_agreed_order():
    """Старший ключ решает раньше младшего, а не вместе с ним.

    Проверка на все четыре сразу: у каждого бойца свой ключ хуже, чем
    у соседа сверху, и лучше по всем младшим — верх списка всё равно
    определяется старшим ключом.
    """
    places = place_standings(
        [
            # ELO ниже всех, зато всё остальное идеально
            row(1, 1400, wins=99, losses=0, reached=1),
            # ELO выше, победы хуже
            row(2, 1500, wins=1, losses=0, reached=1),
            # То же ELO и те же победы, поражений больше
            row(3, 1500, wins=1, losses=5, reached=1),
            # То же всё, ELO достигнут позже
            row(4, 1500, wins=1, losses=5, reached=999),
        ]
    )
    assert [one.user_id for _, one in places] == [2, 3, 4, 1]


def test_the_places_run_without_gaps_and_without_sharing():
    """Места идут подряд: за место платят, и двух первых быть не может."""
    rows = [row(number, 1500) for number in range(1, 8)]
    places = place_standings(rows)
    assert [place for place, _ in places] == [1, 2, 3, 4, 5, 6, 7]


def test_an_empty_week_has_no_places():
    assert place_standings([]) == []
    assert place_standings([row(1, 9000, fights=0)]) == []


# ---------- призы ----------


def test_the_prize_table_pays_the_agreed_sums():
    """Та самая таблица из постановки, место за местом."""
    assert prize_for(1) == 400
    assert prize_for(2) == 375
    assert prize_for(3) == 350
    assert prize_for(4) == 300
    assert prize_for(5) == 200
    assert prize_for(9) == 200
    assert prize_for(10) == 100
    assert prize_for(19) == 100
    assert prize_for(20) == 50
    assert prize_for(50) == 50


def test_below_the_fiftieth_place_nobody_is_paid():
    """Пятьдесят первое место и ниже — ноль, и это не ошибка."""
    assert prize_for(51) == 0
    assert prize_for(200) == 0
    # Нулевое и отрицательное место — тоже ноль: места с таким номером
    # не бывает, и платить за него нечего
    assert prize_for(0) == 0
    assert prize_for(-3) == 0


def test_the_steps_cover_every_place_up_to_fifty_without_a_hole():
    """Между ступенями нет дыр: у каждого места с 1 по 50 есть цена."""
    for place in range(1, 51):
        assert prize_for(place) > 0, place
    # И ступени идут по убыванию: место выше не может стоить дешевле
    sums = [prize_for(place) for place in range(1, 51)]
    assert sums == sorted(sums, reverse=True)


def test_the_table_for_the_screen_is_counted_from_the_same_steps():
    """Таблица на экране считается из тех же ступеней, по которым платят."""
    table = prize_table()
    assert [one["place"] for one in table] == [
        "1", "2", "3", "4", "5–9", "10–19", "20–50"
    ]
    assert [one["prize"] for one in table] == [400, 375, 350, 300, 200, 100, 50]
    # Диапазоны идут подряд от первого места
    first = 1
    for one in table:
        assert one["from"] == first
        first = int(one["to"]) + 1
    assert first == 51
    # И каждая строка платит то же, что платит само место
    for one in table:
        assert prize_for(int(one["from"])) == one["prize"]
        assert prize_for(int(one["to"])) == one["prize"]


def test_the_steps_are_spelled_once():
    """Призовая лестница записана в одном месте и по возрастанию места."""
    places = [last for last, _ in PRIZE_STEPS]
    assert places == sorted(places)
    assert places[-1] == 50
