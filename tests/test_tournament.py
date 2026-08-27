"""Турнир: сетка, запись, посев, бои по кругам и жизнь после перезапуска."""

import random

import pytest

from bot.config import Config
from bot.duel_service import DuelService
from bot.game.bracket import (
    MAX_REPLAYS,
    Match,
    bracket_size,
    first_round,
    next_round,
    round_title,
    seed_positions,
)
from bot.game.classes import get_class
from bot.models import Player
from bot.tournament_service import TournamentError, TournamentService
from tests.test_duel_flow import FakeBot, play_round

CHAT_ID = -100700
THREAD_ID = 9


def make_player(user_id: int, nickname: str, class_code: str = "warrior") -> Player:
    stats = get_class(class_code).base_stats
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code=class_code,
        level=5,
        **stats.as_dict(),
    )


def make_services(bot, db, registration: int = 600):
    config = Config(
        bot_token="test",
        db_path=":memory:",
        turn_timeout=600,
        challenge_timeout=600,
        tournament_registration=registration,
    )
    duels = DuelService(bot=bot, db=db, config=config, rng=random.Random(2024))
    tournaments = TournamentService(bot=bot, db=db, config=config, duels=duels)
    return duels, tournaments


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    """Пауза между боями круга нужна людям, а не тестам."""
    monkeypatch.setattr("bot.tournament_service.MATCH_PAUSE", 0)


async def roster(db, count: int, ratings: list[int] | None = None) -> list[Player]:
    players = []
    for index in range(count):
        player = make_player(901 + index, f"Боец{index + 1}")
        if ratings:
            player.rating = ratings[index]
        await db.save_player(player)
        players.append(player)
    return players


# ---------- чистая арифметика сетки ----------


def test_bracket_size_rounds_up_to_a_power_of_two():
    assert bracket_size(2) == 2
    assert bracket_size(3) == 4
    assert bracket_size(5) == 8
    assert bracket_size(8) == 8
    assert bracket_size(9) == 16
    assert bracket_size(0) == 2  # меньше двоих турнира не бывает


def test_seed_positions_send_the_top_seeds_to_opposite_corners():
    assert seed_positions(2) == [0, 1]
    assert seed_positions(4) == [0, 3, 1, 2]
    assert seed_positions(8) == [0, 7, 3, 4, 1, 6, 2, 5]


def test_first_round_pairs_the_strongest_with_the_weakest():
    assert first_round([1, 2, 3, 4, 5, 6, 7, 8]) == [(1, 8), (4, 5), (2, 7), (3, 6)]


def test_players_without_a_rival_get_a_bye():
    pairs = first_round([1, 2, 3, 4, 5])
    assert (1, None) in pairs  # первый посев проходит без боя
    assert sum(1 for first, second in pairs if first and second) == 1
    byes = [pair for pair in pairs if (pair[0] is None) != (pair[1] is None)]
    assert len(byes) == 3  # пятеро на сетке из восьми: трое проходят даром


def test_next_round_takes_winners_in_bracket_order():
    assert next_round([1, 4, 2, 3]) == [(1, 4), (2, 3)]
    assert next_round([1, 2]) == [(1, 2)]


def test_round_titles_are_named_the_way_people_name_them():
    assert round_title(1) == "Финал"
    assert round_title(2) == "Полуфиналы"
    assert round_title(4) == "Четвертьфиналы"
    assert round_title(8) == "1/8 финала"


def test_match_knows_a_bye_from_a_real_pair():
    real = Match(round=1, slot=0, first_id=1, second_id=2)
    bye = Match(round=1, slot=1, first_id=3, second_id=None)
    empty = Match(round=1, slot=2, first_id=None, second_id=None)

    assert not real.is_bye and not real.is_done
    assert bye.is_bye and bye.bye_winner == 3
    assert empty.is_empty and empty.is_done
    assert real.can_replay and not Match(1, 0, 1, 2, replays=MAX_REPLAYS).can_replay


# ---------- запись ----------


async def test_tournament_takes_only_the_sizes_it_knows(bot, db):
    _, service = make_services(bot, db)
    (opener,) = await roster(db, 1)
    with pytest.raises(TournamentError, match="8, 16, 32"):
        await service.create(CHAT_ID, THREAD_ID, opener, size=5)


async def test_two_tournaments_do_not_fit_in_one_club(bot, db):
    _, service = make_services(bot, db)
    (opener,) = await roster(db, 1)
    await service.create(CHAT_ID, THREAD_ID, opener, size=8)
    with pytest.raises(TournamentError, match="уже идёт турнир"):
        await service.create(CHAT_ID, THREAD_ID, opener, size=8)
    await service.shutdown()


async def test_the_announcer_has_to_fit_his_own_level_limits(bot, db):
    _, service = make_services(bot, db)
    (opener,) = await roster(db, 1)
    with pytest.raises(TournamentError, match="по уровню"):
        await service.create(CHAT_ID, THREAD_ID, opener, size=8, levels=(8, 10))


async def test_registration_card_counts_seats_and_the_clock(bot, db):
    _, service = make_services(bot, db, registration=3600)
    players = await roster(db, 2)
    tournament = await service.create(
        CHAT_ID, THREAD_ID, players[0], size=8, title="Кубок подвала"
    )
    assert "Кубок подвала" in bot.texts[-1]
    assert "1/8" in bot.texts[-1]
    assert "уровни <b>любые</b>" in bot.texts[-1]  # рамок не задавали
    assert tournament.seconds_left > 3000

    await service.join(tournament.id, players[1])
    assert "2/8" in bot.edits[-1].text
    assert players[1].nickname in bot.edits[-1].text
    await service.shutdown()


async def test_you_cannot_sign_up_twice_or_out_of_level(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 2)
    rookie = make_player(950, "Салага")
    rookie.level = 1
    await db.save_player(rookie)

    tournament = await service.create(
        CHAT_ID, THREAD_ID, players[0], size=8, levels=(4, 6)
    )
    await service.join(tournament.id, players[1])
    with pytest.raises(TournamentError, match="уже в списке"):
        await service.join(tournament.id, players[1])
    with pytest.raises(TournamentError, match="Уровень не тот"):
        await service.join(tournament.id, rookie)
    await service.shutdown()


async def test_leaving_frees_the_seat_but_only_until_the_gong(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 2)
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    await service.join(tournament.id, players[1])

    await service.leave(tournament.id, players[1].user_id)
    assert len(await db.tournament_players(tournament.id)) == 1
    assert "1/8" in bot.edits[-1].text

    await db.update_tournament(tournament.id, state="running")
    with pytest.raises(TournamentError, match="не уходят"):
        await service.leave(tournament.id, players[0].user_id)
    await service.shutdown()


# ---------- сетка и ход турнира ----------


def stub_matches(service) -> list[dict]:
    """Отключить настоящие бои: пары просто записываются в список."""
    started: list[dict] = []

    async def fake(tournament, row, match):
        started.append(row)
        service._running.add(tournament.id)
        await service.db.update_match(row["id"], state="running")

    service._run_match = fake
    return started


async def test_seeding_puts_the_top_rating_first(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 4, ratings=[10, 40, 30, 20])
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    for player in players[1:]:
        await service.join(tournament.id, player)

    stub_matches(service)
    await service.start(tournament.id)

    seeds = {row["user_id"]: row["seed"] for row in await db.tournament_players(tournament.id)}
    assert seeds[players[1].user_id] == 1  # рейтинг 40
    assert seeds[players[0].user_id] == 4  # рейтинг 10

    matches = await db.tournament_matches(tournament.id, 1)
    assert len(matches) == 2
    # первый посев встречает последнего, второй — третьего
    assert {matches[0]["first_id"], matches[0]["second_id"]} == {
        players[1].user_id,
        players[0].user_id,
    }
    assert {matches[1]["first_id"], matches[1]["second_id"]} == {
        players[2].user_id,
        players[3].user_id,
    }
    await service.shutdown()


async def test_a_full_list_starts_the_tournament_without_waiting(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 2)
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    await db.update_tournament(tournament.id, size=2)

    stub_matches(service)
    await service.join(tournament.id, players[1])

    assert (await db.get_tournament(tournament.id))["state"] == "running"
    await service.shutdown()


async def test_a_lonely_tournament_is_cancelled(bot, db):
    _, service = make_services(bot, db)
    (opener,) = await roster(db, 1)
    tournament = await service.create(CHAT_ID, THREAD_ID, opener, size=8)
    await service.start(tournament.id)

    assert (await db.get_tournament(tournament.id))["state"] == "cancelled"
    assert "бойцов не набралось" in bot.edits[-1].text
    await service.shutdown()


async def test_odd_man_walks_into_the_next_round_without_a_fight(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 3, ratings=[30, 20, 10])
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    for player in players[1:]:
        await service.join(tournament.id, player)

    started = stub_matches(service)
    await service.start(tournament.id)

    assert any("без боя" in text for text in bot.texts)
    # первый посев прошёл даром, дерутся второй с третьим
    assert len(started) == 1
    assert {started[0]["first_id"], started[0]["second_id"]} == {
        players[1].user_id,
        players[2].user_id,
    }
    bye = [row for row in await db.tournament_matches(tournament.id, 1) if row["id"] != started[0]["id"]]
    assert bye[0]["winner_id"] == players[0].user_id
    await service.shutdown()


async def test_before_every_match_both_fighters_are_healed(bot, db):
    duels, service = make_services(bot, db)
    players = await roster(db, 2)
    for player in players:
        player.set_hp(3)
        await db.save_player(player)

    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    await service.join(tournament.id, players[1])
    await service.start(tournament.id)

    for player in players:
        fresh = await db.get_player(player.user_id)
        assert fresh.current_hp() == fresh.max_hp
    assert any("вылечены до полного здоровья" in text for text in bot.texts)
    assert duels.duel_in_chat(CHAT_ID, THREAD_ID) is not None
    await service.shutdown()


async def test_four_fighters_play_the_whole_bracket_to_one_winner(bot, db):
    duels, service = make_services(bot, db)
    players = await roster(db, 4, ratings=[40, 30, 20, 10])
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    for player in players[1:]:
        await service.join(tournament.id, player)

    await service.start(tournament.id)
    for _ in range(200):
        session = duels.duel_in_chat(CHAT_ID, THREAD_ID)
        if session is None:
            break
        await play_round(duels, session)
    else:  # pragma: no cover
        pytest.fail("турнир не доигрался")

    row = await db.get_tournament(tournament.id)
    assert row["state"] == "finished"
    assert row["round"] == 2  # полуфиналы и финал
    assert row["winner_id"] in {player.user_id for player in players}

    assert any("Турнир взят" in text for text in bot.texts)
    assert any("Полуфиналы" in text for text in bot.texts)
    assert any("Финал" in text for text in bot.texts)

    out = {
        row["user_id"]: row["out_at_round"]
        for row in await db.tournament_players(tournament.id)
    }
    assert sorted(value for value in out.values() if value) == [1, 1, 2]
    assert out[row["winner_id"]] is None

    matches = await db.tournament_matches(tournament.id)
    assert len(matches) == 3
    assert all(match["state"] == "done" for match in matches)
    await service.shutdown()


async def test_a_draw_is_replayed_and_then_decided_by_rating(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 2, ratings=[10, 90])
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    await service.join(tournament.id, players[1])

    stub_matches(service)
    await service.start(tournament.id)
    live = await service.load(tournament.id)

    for attempt in range(1, MAX_REPLAYS + 1):
        row = (await db.tournament_matches(tournament.id))[0]
        await service._handle_draw(live, row)
        row = (await db.tournament_matches(tournament.id))[0]
        assert row["replays"] == attempt
        assert row["winner_id"] is None
        assert row["state"] == "pending"
        assert f"{attempt} из {MAX_REPLAYS}" in bot.texts[-1]

    row = (await db.tournament_matches(tournament.id))[0]
    await service._handle_draw(live, row)
    row = (await db.tournament_matches(tournament.id))[0]
    assert row["winner_id"] == players[1].user_id  # рейтинг 90
    assert row["state"] == "done"
    assert "По рейтингу дальше идёт" in bot.texts[-1]

    out = {
        item["user_id"]: item["out_at_round"]
        for item in await db.tournament_players(tournament.id)
    }
    assert out[players[0].user_id] == 1
    await service.shutdown()


async def test_bracket_shows_every_round_and_its_outcome(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 3, ratings=[30, 20, 10])
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    for player in players[1:]:
        await service.join(tournament.id, player)

    stub_matches(service)
    await service.start(tournament.id)

    text = await service.bracket(tournament.id)
    assert "Сетка" in text
    assert "Полуфиналы" in text
    assert "🎟" in text and "без боя" in text
    assert players[1].nickname in text and players[2].nickname in text
    await service.shutdown()


async def test_stopping_a_tournament_closes_it_for_good(bot, db):
    _, service = make_services(bot, db)
    players = await roster(db, 2)
    tournament = await service.create(CHAT_ID, THREAD_ID, players[0], size=8)
    await service.join(tournament.id, players[1])

    await service.stop(tournament.id)
    assert (await db.get_tournament(tournament.id))["state"] == "cancelled"
    assert not await db.live_tournaments(CHAT_ID)
    assert "остановлен" in bot.texts[-1]
    assert tournament.id not in service._timers
    await service.shutdown()


# ---------- перезапуск бота ----------


async def test_registration_survives_a_restart(bot, db):
    _, first_run = make_services(bot, db, registration=3600)
    players = await roster(db, 2)
    tournament = await first_run.create(CHAT_ID, THREAD_ID, players[0], size=8)
    await first_run.shutdown()  # бот упал

    _, second_run = make_services(bot, db, registration=3600)
    await second_run.resume()
    assert tournament.id in second_run._timers

    # запись продолжается там же, где оборвалась
    await second_run.join(tournament.id, players[1])
    assert len(await db.tournament_players(tournament.id)) == 2
    await second_run.shutdown()


async def test_an_interrupted_match_is_started_again_after_a_restart(bot, db):
    _, first_run = make_services(bot, db)
    players = await roster(db, 2, ratings=[30, 10])
    tournament = await first_run.create(CHAT_ID, THREAD_ID, players[0], size=8)
    await first_run.join(tournament.id, players[1])
    stub_matches(first_run)
    await first_run.start(tournament.id)
    assert (await db.tournament_matches(tournament.id))[0]["state"] == "running"
    await first_run.shutdown()

    _, second_run = make_services(bot, db)
    restarted = stub_matches(second_run)
    await second_run.resume()

    assert len(restarted) == 1
    assert {restarted[0]["first_id"], restarted[0]["second_id"]} == {
        players[0].user_id,
        players[1].user_id,
    }
    await second_run.shutdown()


async def test_a_finished_tournament_is_not_resumed(bot, db):
    _, service = make_services(bot, db)
    (opener,) = await roster(db, 1)
    tournament = await service.create(CHAT_ID, THREAD_ID, opener, size=8)
    await db.update_tournament(tournament.id, state="finished")
    await service.shutdown()

    _, second_run = make_services(bot, db)
    await second_run.resume()
    assert not second_run._timers
    await second_run.shutdown()
