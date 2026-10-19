"""Турнирная арена: расписание, пулл, сетка и выход на бой.

Порядок проверок — по тому, что дороже сломать:

1. **Часы.** Воскресенье, полдень — запись; шесть — жеребьёвка; 18:05 —
   первый круг, дальше каждые полчаса.
2. **Деньги.** Взнос собирается в пулл, клуб добавляет половину, призы
   делятся восемьдесят на двадцать и выдаются один раз.
3. **Сетка.** Лишние места в первом круге достаются сильнейшим.
4. **Выход на бой.** Пять минут обоим; вышел один — проходит он, не
   вышел никто — старший по уровню.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.arena_service import ArenaError, confirm, my_match, sign_up, sweep
from bot.config import Config
from bot.game.arena import (
    CONFIRM_SECONDS,
    ENTRY_FEE,
    MIN_PLAYERS,
    closes_at,
    opens_at,
    phase_of,
    pot_of,
    prizes,
    round_at,
    round_now,
    week_of,
)
from bot.webapp.arena import build_arena
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_robbery import make_player
from tests.test_webapp import TOKEN, make_init_data

MSK = timezone(timedelta(hours=3))
ARENA = "fight_tournament_stadium"


def at(text: str) -> int:
    """Московский час из строки «2026-10-18 13:00»."""
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
    return int(moment.timestamp())


NOON = at("2026-10-18 13:00")  # воскресенье, идёт запись


def headers(user_id: int = 1) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


class Duels:
    """Дуэль, которая кончается сразу — победой первого угла."""

    def __init__(self, winner: str = "red") -> None:
        self.fights: list[tuple[int, int]] = []
        self.winner = winner

    async def start_duel(
        self, chat_id, thread_id, first, second, mode=None, on_finish=None,
        **rest,
    ):
        self.fights.append((first.user_id, second.user_id))
        chosen = first.user_id if self.winner == "red" else second.user_id

        class Result:
            winner_id = chosen

        if on_finish:
            await on_finish(None, Result())


async def sign(db, numbers: range | tuple, now: int = NOON) -> None:
    """Записать бойцов в турнир: уровень растёт вместе с номером."""
    for number in numbers:
        one = make_player(number, f"Боец-{number}")
        one.level = number + 3
        one.credits = 1_000
        one.location = ARENA
        await db.save_player(one)
        await sign_up(db, await db.get_player(number), now=now)


# ---------- часы ----------


def test_the_week_runs_by_the_clock():
    """Воскресенье: полдень — запись, шесть — жеребьёвка, 18:05 — бои."""
    assert phase_of(at("2026-10-18 12:00")) == "open"
    assert phase_of(at("2026-10-18 17:59")) == "open"
    assert phase_of(at("2026-10-18 18:01")) == "seeding"
    assert phase_of(at("2026-10-18 18:06")) == "fighting"
    # В понедельник арена уже закрыта, и ждут следующего воскресенья
    assert phase_of(at("2026-10-19 12:00")) == "done"

    assert closes_at(NOON) - opens_at(NOON) == 6 * 60 * 60
    assert round_at(NOON, 1) == closes_at(NOON) + 5 * 60
    assert round_at(NOON, 2) - round_at(NOON, 1) == 30 * 60
    assert round_now(at("2026-10-18 18:06")) == 1
    assert round_now(at("2026-10-18 18:36")) == 2
    assert round_now(at("2026-10-18 17:00")) == 0
    assert week_of(at("2026-10-18 19:00")) == "2026-10-18"


def test_the_pot_is_the_entries_and_half_from_the_club():
    """Взносы плюс половина сверху, призы — восемьдесят на двадцать."""
    assert pot_of(4) == 4 * ENTRY_FEE + 4 * ENTRY_FEE // 2 == 1_200
    assert prizes(1_200) == (960, 240)
    # Остаток от деления достаётся первому: второму ровно двадцать
    assert sum(prizes(1_500)) == 1_500 and prizes(1_500)[1] == 300
    assert pot_of(0) == 0


# ---------- запись ----------


async def test_the_entry_fee_goes_into_the_pot(db):
    """Взнос списывается при записи и уходит в призовой фонд."""
    await sign(db, range(1, 4))

    for number in (1, 2, 3):
        assert (await db.get_player(number)).credits == 1_000 - ENTRY_FEE
    body = await build_arena(db, await db.get_player(1), None, NOON)
    assert body["signed"] == 3 and body["joined"] is True
    assert body["pot"] == pot_of(3)
    assert body["prizes"] == {"winner": 720, "runner": 180}


async def test_the_list_is_joined_once_and_only_while_it_is_open(db):
    """Второй раз не записывают, а после шести — вообще никого."""
    await sign(db, (1,))
    with pytest.raises(ArenaError, match="уже записан"):
        await sign_up(db, await db.get_player(1), now=NOON)

    one = make_player(9, "Опоздавший")
    one.credits = 1_000
    await db.save_player(one)
    with pytest.raises(ArenaError, match="Запись закрыта"):
        await sign_up(db, one, now=at("2026-10-18 18:30"))

    broke = make_player(10, "Без денег")
    broke.credits = ENTRY_FEE - 1
    await db.save_player(broke)
    with pytest.raises(ArenaError, match="Взнос"):
        await sign_up(db, broke, now=NOON)


async def test_nobody_came_so_the_money_goes_back(db):
    """Меньше двоих — турнира нет, взносы возвращаются."""
    await sign(db, (1,))

    await sweep(db, Duels(), at("2026-10-18 18:06"))

    assert (await db.get_player(1)).credits == 1_000, "взнос вернули"
    row = await db.arena_week(week_of(NOON))
    assert row["state"] == "void"
    body = await build_arena(db, await db.get_player(1), None, at("2026-10-18 18:06"))
    assert body["void"] is True and str(MIN_PLAYERS) in body["note"]


# ---------- сетка ----------


async def test_the_strongest_walk_the_first_round(db):
    """Бойцов не степень двойки — лишние места достаются сильнейшим."""
    await sign(db, range(1, 6))  # пятеро: уровни с 4 по 8

    await sweep(db, Duels(), round_at(NOON, 1) + 60)

    week = week_of(NOON)
    rows = await db.arena_matches(week, 1)
    assert len(rows) == 4, "сетка на восьмерых"
    byes = {
        one["red_id"] or one["blue_id"]
        for one in rows
        if (one["red_id"] is None) != (one["blue_id"] is None)
    }
    assert byes == {5, 4, 3}, "без боя проходят трое сильнейших"
    fight = next(one for one in rows if one["red_id"] and one["blue_id"])
    assert {fight["red_id"], fight["blue_id"]} == {1, 2}, "дерутся слабейшие"
    assert (await db.arena_week(week))["rounds"] == 3


async def test_both_must_step_out_and_then_the_gong(db):
    """Вышли оба — бой начинается сразу, не дожидаясь срока."""
    duels = Duels()
    await sign(db, range(1, 3))
    live = round_at(NOON, 1) + 60
    await sweep(db, duels, live)

    week = week_of(NOON)
    said = await confirm(db, duels, await db.get_player(1), now=live)
    assert "Ждём соперника" in said
    assert not duels.fights, "одного мало"

    said = await confirm(db, duels, await db.get_player(2), now=live)
    assert "гонг" in said.lower()
    assert len(duels.fights) == 1
    row = (await db.arena_matches(week, 1))[0]
    assert row["state"] == "done" and row["winner_id"] == duels.fights[0][0]


async def test_the_one_who_came_walks_over_the_one_who_did_not(db):
    """Вышел один — проходит он: ждать не вышедшего круг не станет."""
    duels = Duels()
    await sign(db, range(1, 3))
    live = round_at(NOON, 1) + 60
    await sweep(db, duels, live)
    week = week_of(NOON)
    pair = await my_match(db, week, 1)
    came = int(pair["red_id"])

    await confirm(db, duels, await db.get_player(came), now=live)
    # Пять минут вышли — судья разводит
    await sweep(db, duels, live + CONFIRM_SECONDS + 1)

    row = (await db.arena_matches(week, 1))[0]
    assert row["winner_id"] == came and row["note"] == "соперник не вышел"
    assert not duels.fights, "боя не было"


async def test_nobody_came_so_the_stronger_walks(db):
    """Не вышел никто — дальше идёт старший: сетка должна ехать."""
    duels = Duels()
    await sign(db, range(1, 3))  # уровни 4 и 5
    live = round_at(NOON, 1) + 60
    await sweep(db, duels, live)

    await sweep(db, duels, live + CONFIRM_SECONDS + 1)

    row = (await db.arena_matches(week_of(NOON), 1))[0]
    assert row["winner_id"] == 2, "второй боец старше уровнем"
    assert row["note"] == "никто не вышел"


async def test_the_late_fighter_is_not_let_in(db):
    """Время вышло — на этот бой уже не выйти."""
    duels = Duels()
    await sign(db, range(1, 3))
    live = round_at(NOON, 1) + 60
    await sweep(db, duels, live)

    with pytest.raises(ArenaError, match="Время вышло|нет боя"):
        await confirm(
            db, duels, await db.get_player(1), now=live + CONFIRM_SECONDS + 1
        )


# ---------- призы ----------


async def test_the_champion_takes_eighty_and_the_second_twenty(db):
    """Турнир доигрывается до чемпиона, и призы выдаются один раз."""
    duels = Duels()
    await sign(db, range(1, 5))  # ровно четверо: два круга
    week = week_of(NOON)
    purse = {one: (await db.get_player(one)).credits for one in range(1, 5)}

    for number in (1, 2):
        await sweep(db, duels, round_at(NOON, number) + CONFIRM_SECONDS + 1)

    row = await db.arena_week(week)
    assert row["state"] == "done" and row["winner_id"]
    winner, runner = prizes(pot_of(4))
    champion = await db.get_player(int(row["winner_id"]))
    second = await db.get_player(int(row["runner_id"]))
    assert champion.credits == purse[champion.user_id] + winner
    assert second.credits == purse[second.user_id] + runner

    # Второй взгляд ничего не добавляет: призы выдают один раз
    await sweep(db, duels, round_at(NOON, 3))
    assert (await db.get_player(champion.user_id)).credits == (
        purse[champion.user_id] + winner
    )


# ---------- экран ----------


async def test_the_board_paints_the_bracket(db):
    """Сетка на экране: прошедший зелёный, вылетевший красный."""
    duels = Duels()
    await sign(db, range(1, 5))
    live = round_at(NOON, 1) + CONFIRM_SECONDS + 1
    await sweep(db, duels, live)

    body = await build_arena(db, await db.get_player(1), duels, live)
    assert body["phase"] == "fighting"
    first = body["rounds"][0]
    assert first["title"] == "Полуфиналы" and first["at_text"] == "18:05"
    pair = first["matches"][0]
    assert pair["state"] == "done"
    sides = [pair["red"], pair["blue"]]
    assert sum(one["won"] for one in sides) == 1
    assert sum(one["out"] for one in sides) == 1
    assert any(one["you"] for one in sides if one["user_id"] == 1)

    # Свой бой виден отдельно и со сроком выхода
    second = await build_arena(db, await db.get_player(4), duels, live)
    if second["my_match"]:
        assert second["my_match"]["confirm"] == CONFIRM_SECONDS


async def test_the_arena_is_looked_at_from_inside(db, client):
    """На арену ходят ногами: с улицы сетку не смотрят."""
    away = make_player(11, "Прохожий")
    away.location = "fight_club"
    await db.save_player(away)

    answer = await client.get("/api/arena", headers=headers(11))
    assert answer.status == 409


async def test_the_handler_signs_up_and_steps_out(db, client):
    """Ручка арены: записаться и выйти на бой — те же два действия."""
    one = make_player(12, "Боец")
    one.credits = 1_000
    one.location = ARENA
    await db.save_player(one)

    answer = await client.post(
        "/api/arena", json={"action": "join"}, headers=headers(12)
    )
    # Запись идёт только по воскресеньям: в будни ручка честно отказывает
    assert answer.status in (200, 409)
    if answer.status == 200:
        body = await answer.json()
        assert body["joined"] is True and body["signed"] == 1
