"""Выборы мэра: календарь, выдвижение, голос и смена кресла.

Порядок проверок — по тому, что дороже сломать:

1. **Календарь.** Первое воскресенье месяца, окна выдвижения и
   голосования, час вступления в должность.
2. **Выдвижение.** В администрации, в своё окно, один раз.
3. **Голос.** Один на человека и необратимый.
4. **Снятие.** Сел в тюрьму во время выдвижения — в бюллетень не попал.
5. **Кресло.** Мэр заступает в девять утра понедельника, прежний в тот
   же миг уходит.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.election_service import ElectionError, nominate, settle, state, vote
from bot.game.clock import MOSCOW
from bot.game.elections import (
    COUNTING,
    EVE,
    NOMINATION,
    PROGRAM_MAX,
    QUIET,
    VOTING,
    day_code,
    election_day,
    first_sunday,
    nomination_from,
    nomination_to,
    phase,
    phase_ends,
    term_from,
    voting_from,
    voting_to,
    winner_of,
)
from bot.police_service import jail
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data
from tests.test_robbery import make_player

ADMIN = "city_administration"
BAR = "bar"

# Выборы ноября двадцать шестого: воскресенье первого числа
DAY = date(2026, 11, 1)
WEDNESDAY = nomination_from(DAY) + 3600  # среда, десять утра
SATURDAY = nomination_to(DAY) - 3600  # суббота, семь вечера
SUNDAY = voting_from(DAY) + 3600  # воскресенье, девять утра
MONDAY = term_from(DAY) + 3600  # понедельник, десять утра


def headers(user_id: int = 1) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


def msk(moment: int) -> datetime:
    return datetime.fromtimestamp(moment, MOSCOW)


async def citizen(
    db: Database, user_id: int, nickname: str, where: str = ADMIN
):
    one = make_player(user_id, nickname, where=where)
    await db.save_player(one)
    return await db.get_player(user_id)


# ---------- календарь ----------


def test_the_election_is_the_first_sunday_of_the_month():
    """Первое воскресенье — и когда оно первое число, и когда седьмое."""
    for year, month, day in [
        (2026, 11, 1), (2026, 12, 6), (2027, 1, 3),
        (2027, 2, 7), (2027, 3, 7), (2027, 8, 1),
    ]:
        found = first_sunday(year, month)
        assert found == date(year, month, day), (year, month)
        assert found.weekday() == 6, "воскресенье"


def test_the_windows_stand_where_the_rules_say():
    """Среда девять, суббота восемь, воскресенье с восьми до восьми."""
    opened, closed = msk(nomination_from(DAY)), msk(nomination_to(DAY))
    assert (opened.weekday(), opened.hour) == (2, 9), "среда, девять утра"
    assert (closed.weekday(), closed.hour) == (5, 20), "суббота, восемь вечера"

    starts, ends = msk(voting_from(DAY)), msk(voting_to(DAY))
    assert (starts.weekday(), starts.hour) == (6, 8)
    assert (ends.weekday(), ends.hour) == (6, 20)
    assert starts.date() == DAY

    seat = msk(term_from(DAY))
    assert (seat.weekday(), seat.hour) == (0, 9), "понедельник, девять утра"


def test_every_hour_knows_its_phase():
    """По любому часу видно, что в него можно."""
    assert phase(nomination_from(DAY) - 60) == QUIET
    assert phase(nomination_from(DAY)) == NOMINATION
    assert phase(nomination_to(DAY) - 60) == NOMINATION
    assert phase(nomination_to(DAY)) == EVE
    assert phase(voting_from(DAY) - 60) == EVE
    assert phase(voting_from(DAY)) == VOTING
    assert phase(voting_to(DAY) - 60) == VOTING
    assert phase(voting_to(DAY)) == COUNTING
    assert phase(term_from(DAY) - 60) == COUNTING
    # С девяти утра понедельника цикл уже следующий
    assert election_day(term_from(DAY)) == date(2026, 12, 6)
    assert phase(term_from(DAY)) == QUIET


def test_the_phase_says_when_it_ends():
    """Отсчёт на экране идёт до конца текущей фазы, а не до выборов."""
    assert phase_ends(nomination_from(DAY) + 60) == nomination_to(DAY)
    assert phase_ends(voting_from(DAY) + 60) == voting_to(DAY)
    assert phase_ends(voting_to(DAY) + 60) == term_from(DAY)
    assert phase_ends(nomination_from(DAY) - 60) == nomination_from(DAY)


def test_a_tie_leaves_the_chair_empty():
    """Поровну — победителя нет: жребий был бы решением за город."""
    assert winner_of({}) == (0, 0)
    assert winner_of({7: 3, 8: 1}) == (7, 3)
    assert winner_of({7: 2, 8: 2}) == (0, 2)
    assert winner_of({7: 0}) == (0, 0)


# ---------- выдвижение ----------


async def test_the_candidate_is_registered_without_a_word(db):
    """Пришёл в администрацию в своё окно — и ты кандидат."""
    one = await citizen(db, 1, "Марла")
    said = await nominate(db, one, "Снесу казино", now=WEDNESDAY)
    assert "принята" in said

    rows = await db.candidates(day_code(DAY))
    assert len(rows) == 1
    assert rows[0]["user_id"] == 1 and rows[0]["program"] == "Снесу казино"

    with pytest.raises(ElectionError, match="уже выдвинулся"):
        await nominate(db, one, "", now=WEDNESDAY)


async def test_the_nomination_needs_the_house_and_the_hour(db):
    """Выдвигаются в администрации и только в окно приёма."""
    one = await citizen(db, 1, "Марла", where=BAR)
    with pytest.raises(ElectionError, match="администрации"):
        await nominate(db, one, "", now=WEDNESDAY)

    one = await citizen(db, 2, "Тайлер")
    with pytest.raises(ElectionError, match="не выдвигаются"):
        await nominate(db, one, "", now=nomination_from(DAY) - 3600)
    with pytest.raises(ElectionError, match="не выдвигаются"):
        await nominate(db, one, "", now=SUNDAY)


async def test_the_candidates_are_not_counted(db):
    """Кандидатов сколько угодно: ограничения на их число нет."""
    for number in range(12):
        one = await citizen(db, number + 1, f"Кандидат-{number}")
        await nominate(db, one, "", now=WEDNESDAY)
    assert len(await db.candidates(day_code(DAY))) == 12


async def test_a_long_program_is_cut_to_size(db):
    """Программа длиной в простыню режется, а не отвергается."""
    one = await citizen(db, 1, "Марла")
    await nominate(db, one, "о" * (PROGRAM_MAX + 200), now=WEDNESDAY)
    rows = await db.candidates(day_code(DAY))
    assert len(rows[0]["program"]) == PROGRAM_MAX


async def test_the_jailed_do_not_run(db):
    """Из тюрьмы в мэры не выдвигаются."""
    one = await citizen(db, 1, "Марла")
    await jail(db, one.user_id, WEDNESDAY)
    one = await db.get_player(one.user_id)
    one.location = ADMIN
    await db.save_player(one)
    with pytest.raises(ElectionError, match="тюрьмы"):
        await nominate(db, one, "", now=WEDNESDAY)


async def test_the_candidate_who_lands_in_jail_is_struck(db):
    """Сел, пока шло выдвижение, — в бюллетень не попал."""
    marla = await citizen(db, 1, "Марла")
    tyler = await citizen(db, 2, "Тайлер")
    await nominate(db, marla, "", now=WEDNESDAY)
    await nominate(db, tyler, "", now=WEDNESDAY)
    await jail(db, tyler.user_id, SATURDAY)

    sheet = await state(db, marla, now=SUNDAY)
    assert [one["user_id"] for one in sheet["candidates"]] == [1]
    # Снятие записано посадкой: вышел из тюрьмы до воскресенья — в
    # бюллетень всё равно не вернулся
    rows = {one["user_id"]: one for one in await db.candidates(day_code(DAY))}
    assert rows[2]["dropped_at"] > 0
    assert (await db.jail_term(tyler.user_id))[0] < SUNDAY, "срок уже вышел"
    sheet = await state(db, marla, now=SUNDAY)
    assert [one["user_id"] for one in sheet["candidates"]] == [1]


# ---------- голосование ----------


async def test_the_vote_is_one_and_final(db):
    """Голос один и необратимый."""
    marla = await citizen(db, 1, "Марла")
    tyler = await citizen(db, 2, "Тайлер")
    voter = await citizen(db, 3, "Боец", where=BAR)
    await nominate(db, marla, "", now=WEDNESDAY)
    await nominate(db, tyler, "", now=WEDNESDAY)

    said = await vote(db, voter, 1, now=SUNDAY)
    assert "принят" in said
    assert await db.ballot_of(day_code(DAY), 3) == 1

    with pytest.raises(ElectionError, match="уже голосовал"):
        await vote(db, voter, 2, now=SUNDAY)
    assert await db.ballot_of(day_code(DAY), 3) == 1, "передумать нельзя"


async def test_the_vote_waits_for_its_hour_and_its_ballot(db):
    """Голосуют в своё окно и только за того, кто в бюллетене."""
    marla = await citizen(db, 1, "Марла")
    voter = await citizen(db, 3, "Боец", where=BAR)
    await nominate(db, marla, "", now=WEDNESDAY)

    with pytest.raises(ElectionError, match="не идёт"):
        await vote(db, voter, 1, now=SATURDAY)
    with pytest.raises(ElectionError, match="нет"):
        await vote(db, voter, 99, now=SUNDAY)


async def test_anyone_votes_from_anywhere(db):
    """Голосуют откуда угодно: бюллетень приходит сам."""
    marla = await citizen(db, 1, "Марла")
    await nominate(db, marla, "", now=WEDNESDAY)
    for number, where in enumerate(["bar", "casino", "prison", "hospital"]):
        voter = await citizen(db, 10 + number, f"Боец-{number}", where=where)
        await vote(db, voter, 1, now=SUNDAY)
    assert await db.tally(day_code(DAY)) == {1: 4}


# ---------- кресло ----------


async def test_the_mayor_takes_the_chair_on_monday(db):
    """Девять утра понедельника: новый в правах, прежний — нет."""
    marla = await citizen(db, 1, "Марла")
    tyler = await citizen(db, 2, "Тайлер")
    await nominate(db, marla, "", now=WEDNESDAY)
    await nominate(db, tyler, "", now=WEDNESDAY)
    for number in range(3):
        voter = await citizen(db, 10 + number, f"Боец-{number}", where=BAR)
        await vote(db, voter, 1, now=SUNDAY)
    outsider = await citizen(db, 20, "Чужой", where=BAR)
    await vote(db, outsider, 2, now=SUNDAY)

    # До понедельника кресло не меняется
    done = await settle(db, term_from(DAY) - 60)
    assert done["seated"] is False
    assert (await db.mayor())["user_id"] == 0

    done = await settle(db, MONDAY)
    assert done["seated"] is True and done["winner"] == 1
    seat = await db.mayor()
    assert seat["user_id"] == 1 and seat["since"] == term_from(DAY)

    record = await db.election(day_code(DAY))
    assert record["winner_id"] == 1 and record["votes"] == 3
    assert record["turnout"] == 4

    # Второй заход ничего не меняет
    again = await settle(db, MONDAY + 3600)
    assert again["seated"] is False
    assert (await db.mayor())["user_id"] == 1


async def test_the_next_month_replaces_the_mayor(db):
    """Полномочия прежнего кончаются ровно тогда, когда начинаются новые."""
    marla = await citizen(db, 1, "Марла")
    await nominate(db, marla, "", now=WEDNESDAY)
    voter = await citizen(db, 10, "Боец", where=BAR)
    await vote(db, voter, 1, now=SUNDAY)
    await settle(db, MONDAY)
    assert (await db.mayor())["user_id"] == 1

    # Декабрьские выборы: выиграл другой
    december = first_sunday(2026, 12)
    tyler = await citizen(db, 2, "Тайлер")
    await nominate(db, tyler, "", now=nomination_from(december) + 60)
    second = await citizen(db, 11, "Боец-2", where=BAR)
    await vote(db, second, 2, now=voting_from(december) + 60)

    await settle(db, term_from(december) + 60)
    seat = await db.mayor()
    assert seat["user_id"] == 2, "прежний мэр ушёл в тот же миг"
    assert seat["since"] == term_from(december)


async def test_the_mandate_survives_a_later_arrest(db):
    """Сел после выборов — мэром всё равно стал.

    Снимает кандидата только посадка во время выдвижения. Мандат дал
    город, а не участок, и отбирать его задним числом никто не может.
    """
    marla = await citizen(db, 1, "Марла")
    tyler = await citizen(db, 2, "Тайлер")
    await nominate(db, marla, "", now=WEDNESDAY)
    await nominate(db, tyler, "", now=WEDNESDAY)
    for number in range(3):
        voter = await citizen(db, 10 + number, f"Боец-{number}", where=BAR)
        await vote(db, voter, 2, now=SUNDAY)
    one = await citizen(db, 20, "Один", where=BAR)
    await vote(db, one, 1, now=SUNDAY)

    # Тайлера взяли под стражу уже после голосования
    await jail(db, tyler.user_id, voting_to(DAY) + 60)
    await settle(db, MONDAY)
    record = await db.election(day_code(DAY))
    assert record["winner_id"] == 2 and record["votes"] == 3
    assert (await db.mayor())["user_id"] == 2


# ---------- ручки и экран ----------


async def test_the_ballot_rides_along_with_the_card(db, client):
    """Бюллетень приезжает с карточкой: за ним никуда не ходят."""
    one = await citizen(db, 1, "Марла", where=BAR)
    body = await (await client.get(
        "/api/card", headers=headers(one.user_id)
    )).json()
    assert "ballot" in body
    # Вне голосования листа нет вовсе
    assert body["ballot"]["shown"] is False
    assert body["ballot"]["phase"] in {QUIET, NOMINATION, EVE, VOTING, COUNTING}


async def test_the_handler_nominates_and_votes(db, client):
    """Выдвижение и голос через ручку, и экран показывает своё состояние."""
    one = await citizen(db, 1, "Марла")
    body = await (await client.get(
        "/api/elections", headers=headers(one.user_id)
    )).json()
    assert body["title"] == "Администрация города"
    assert "phase" in body and "candidates" in body

    answer = await client.post(
        "/api/elections",
        json={"action": "nominate", "program": "Снесу казино"},
        headers=headers(one.user_id),
    )
    # Вне окна приёма — отказ словами, а не молчанием
    if answer.status == 409:
        assert "не выдвигаются" in (await answer.json())["error"]
    else:
        body = await answer.json()
        assert body["standing"] is True
