"""Бои на много бойцов: сбор состава, пары по ходам, выбывание, итог."""

import random

import pytest

from bot.battle_service import BattleError, BattleService
from bot.config import Config
from bot.game.battle import (
    BLUE,
    MAX_BATTLE_ROUNDS,
    RED,
    BattleKind,
    judge,
    pair_up,
)
from bot.game.classes import get_class
from bot.game.combat import Fighter
from bot.game.modes import FightMode
from bot.models import Player
from tests.test_duel_flow import FakeBot, earned_credits

CHAT_ID = -100500
THREAD_ID = 7
ZONES = ["head", "chest", "belly", "belt", "legs"]


def make_player(user_id: int, nickname: str, class_code: str = "warrior") -> Player:
    stats = get_class(class_code).base_stats
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code=class_code,
        level=5,
        **stats.as_dict(),
    )


def make_fighter(user_id: int, hp: int = 60) -> Fighter:
    fclass = get_class("warrior")
    fighter = Fighter(user_id, f"Боец{user_id}", fclass, fclass.base_stats)
    fighter.hp = hp
    return fighter


@pytest.fixture
def bot():
    return FakeBot()


def make_service(bot, db, lobby_timeout: int = 600) -> BattleService:
    config = Config(
        bot_token="test",
        db_path=":memory:",
        turn_timeout=600,
        lobby_timeout=lobby_timeout,
        round_break=0,
    )
    return BattleService(bot=bot, db=db, config=config, rng=random.Random(7))


async def fill(db, count: int, first_id: int = 1, level: int = 5) -> list[Player]:
    players = []
    for index in range(count):
        player = make_player(first_id + index, f"Боец{first_id + index}")
        player.level = level
        await db.save_player(player)
        players.append(player)
    return players


async def choose_all(service: BattleService, session) -> None:
    """Каждый, кому досталась пара, выбирает удар и блок.

    Как только выбрали все, раунд считается сам — поэтому останавливаемся,
    едва номер раунда сменился: дальше уже другая раскладка.
    """
    round_number = session.round_number
    for index, (first, second) in enumerate(list(session.pairs)):
        if session.round_number != round_number:
            return
        for offset, user_id in enumerate((first, second)):
            fighter = session.fighters[user_id]
            if not fighter.alive:
                continue  # упавший кнопки уже не жмёт
            zone = ZONES[(index + offset) % len(ZONES)]
            await service.handle_choice(session.id, user_id, "attack", zone)
            await service.handle_choice(
                session.id, user_id, "block", ZONES[(index + offset + 1) % len(ZONES)]
            )


async def fight_to_the_end(service: BattleService, session) -> None:
    for _ in range(MAX_BATTLE_ROUNDS + 2):
        if service.battle_in_chat(session.chat_id, session.thread_id) is None:
            return
        await choose_all(service, session)
    raise AssertionError("бой не закончился за отведённые раунды")


# ---------- раскладка по парам ----------


def test_everyone_takes_turns_and_nobody_idles_forever():
    """В неравном составе без пары каждый раунд остаётся другой боец."""
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 6)}
    teams = {1: RED, 2: RED, 3: RED, 4: BLUE, 5: BLUE}

    idle_over_time = []
    for round_number in range(1, 7):
        pairs, idle = pair_up(fighters, teams, BattleKind.TEAM, round_number)
        assert len(pairs) == 2  # трое против двоих — две пары
        assert all(teams[a] != teams[b] for a, b in pairs)
        idle_over_time += idle
    assert set(idle_over_time) == {1, 2, 3}, "кто-то простоял весь бой"


def test_the_dead_leave_the_rotation():
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 5)}
    teams = {1: RED, 2: RED, 3: BLUE, 4: BLUE}
    fighters[3].hp = 0

    pairs, idle = pair_up(fighters, teams, BattleKind.TEAM, 1)
    assert len(pairs) == 1 and 3 not in {u for pair in pairs for u in pair}
    assert 3 not in idle  # мёртвый вообще не в раскладке


def test_royale_pairs_shuffle_and_the_odd_one_waits():
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 6)}
    seen = set()
    for round_number in range(1, 6):
        pairs, idle = pair_up(fighters, {}, BattleKind.ROYALE, round_number)
        assert len(pairs) == 2 and len(idle) == 1
        seen.update(pairs)
    assert len(seen) > 2, "пары не меняются от раунда к раунду"


def test_judge_calls_the_battle_when_a_side_is_gone():
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 5)}
    teams = {1: RED, 2: RED, 3: BLUE, 4: BLUE}
    assert not judge(fighters, teams, BattleKind.TEAM, 1).finished

    fighters[3].hp = fighters[4].hp = 0
    outcome = judge(fighters, teams, BattleKind.TEAM, 2)
    assert outcome.finished and outcome.winning_team == RED
    assert set(outcome.winners) == {1, 2}

    # королевская битва идёт до последнего
    solo = {user_id: make_fighter(user_id) for user_id in range(1, 4)}
    assert not judge(solo, {}, BattleKind.ROYALE, 1).finished
    solo[1].hp = solo[2].hp = 0
    assert judge(solo, {}, BattleKind.ROYALE, 2).winners == (3,)


def test_judge_counts_health_when_rounds_run_out():
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 5)}
    teams = {1: RED, 2: RED, 3: BLUE, 4: BLUE}
    fighters[1].hp = fighters[2].hp = 50
    fighters[3].hp = fighters[4].hp = 20

    outcome = judge(fighters, teams, BattleKind.TEAM, MAX_BATTLE_ROUNDS)
    assert outcome.finished and outcome.by_rounds and outcome.winning_team == RED


# ---------- сбор состава ----------


async def test_lobby_starts_the_fight_as_soon_as_it_is_full(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 4)

    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    assert service.lobby_in_chat(CHAT_ID, THREAD_ID) is lobby
    await service.join(lobby.id, players[1], RED)
    await service.join(lobby.id, players[2], BLUE)
    assert service.battle_in_chat(CHAT_ID, THREAD_ID) is None  # ещё не полный состав

    await service.join(lobby.id, players[3], BLUE)
    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    assert session is not None, "полный состав — гонг сразу"
    assert len(session.fighters) == 4
    assert service.lobby_in_chat(CHAT_ID, THREAD_ID) is None
    await service.shutdown()


async def test_lobby_keeps_the_level_bracket(bot, db):
    service = make_service(bot, db)
    host = make_player(1, "Хозяин")
    host.level = 5
    await db.save_player(host)
    rookie = make_player(2, "Новичок")
    rookie.level = 1
    await db.save_player(rookie)

    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, host, BattleKind.TEAM, size=2, levels=(4, 6)
    )
    with pytest.raises(BattleError, match="Уровень не тот"):
        await service.join(lobby.id, rookie, BLUE)
    await service.shutdown()


async def test_lobby_holds_one_ring_and_one_fighter(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 3)

    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    with pytest.raises(BattleError, match="уже собирают"):
        await service.open_lobby(
            CHAT_ID, THREAD_ID, players[1], BattleKind.TEAM, size=2
        )
    with pytest.raises(BattleError, match="уже записан"):
        await service.join(lobby.id, players[0], BLUE)

    await service.join(lobby.id, players[1], BLUE)
    await service.leave(lobby.id, players[1].user_id)
    assert players[1].user_id not in lobby.members
    assert not service.is_busy(players[1].user_id)
    await service.shutdown()


async def test_empty_lobby_closes_itself(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 1)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    await service.leave(lobby.id, players[0].user_id)
    assert service.lobby_in_chat(CHAT_ID, THREAD_ID) is None
    assert any("сбор закрыт" in edit.text for edit in bot.edits)
    await service.shutdown()


async def test_lobby_starts_on_timeout_with_whoever_came(bot, db):
    service = make_service(bot, db, lobby_timeout=0)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=3
    )
    await service.join(lobby.id, players[1], BLUE)

    await lobby.task  # время вышло
    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    assert session is not None and len(session.fighters) == 2
    await service.shutdown()


async def test_lobby_without_a_second_side_is_cancelled(bot, db):
    service = make_service(bot, db, lobby_timeout=0)
    players = await fill(db, 2)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    await service.join(lobby.id, players[1], RED)  # обе в одной команде

    await lobby.task
    assert service.battle_in_chat(CHAT_ID, THREAD_ID) is None
    assert any("не собрался" in edit.text for edit in bot.edits)
    await service.shutdown()


# ---------- сам бой ----------


async def test_team_battle_runs_to_a_winner(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    await fight_to_the_end(service, session)

    assert service.battle_in_chat(CHAT_ID, THREAD_ID) is None
    assert any("Победа" in text or "Ничья" in text for text in bot.texts)
    # итог записан, и у каждого сошёлся счёт
    battles = await db.recent_battles(CHAT_ID)
    assert len(battles) == 1 and battles[0]["kind"] == "team"
    members = await db.battle_members(battles[0]["id"])
    assert len(members) == 4
    for player in players:
        saved = await db.get_player(player.user_id)
        assert saved.wins + saved.losses + saved.draws == 1
    await service.shutdown()


async def test_the_fallen_cannot_press_buttons(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    fallen = players[3].user_id
    session.fighters[fallen].hp = 0
    session.pairs, session.idle = pair_up(
        session.fighters, session.teams, session.kind, session.round_number
    )

    with pytest.raises(BattleError, match="вне боя"):
        await service.handle_choice(session.id, fallen, "attack", "head")
    await service.shutdown()


async def test_a_stranger_cannot_press_buttons(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    with pytest.raises(BattleError, match="не в этом бою"):
        await service.handle_choice(session.id, 999, "attack", "head")
    await service.shutdown()


async def test_royale_leaves_one_standing(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 3)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.ROYALE, size=3
    )
    await service.join(lobby.id, players[1])
    await service.join(lobby.id, players[2])

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    assert session.kind is BattleKind.ROYALE
    await fight_to_the_end(service, session)

    winners = [
        text for text in bot.texts if "Последний на ногах" in text or "Ничья" in text
    ]
    assert winners, "судья не объявил победителя"
    battles = await db.recent_battles(CHAT_ID)
    assert battles[0]["kind"] == "royale"
    await service.shutdown()


async def test_fist_battle_leaves_the_gear_behind(bot, db):
    """Групповой бой на кулачном ринге идёт без вещей, на оружейном — с ними."""
    from bot.game.equipment import Slot

    service = make_service(bot, db)
    players = await fill(db, 4, level=8)
    for player in players:
        weapon = await db.add_gear(player.user_id, "bat")
        weapon.slot = Slot.WEAPON
        await db.save_gear(weapon)

    async def gather(thread_id: int, mode: FightMode):
        lobby = await service.open_lobby(
            CHAT_ID,
            thread_id,
            await db.get_player(players[0].user_id),
            BattleKind.TEAM,
            size=2,
            mode=mode,
        )
        for player, team in (
            (players[1], RED),
            (players[2], BLUE),
            (players[3], BLUE),
        ):
            await service.join(lobby.id, await db.get_player(player.user_id), team)
        return service.battle_in_chat(CHAT_ID, thread_id)

    fist = await gather(101, FightMode.FIST)
    assert all(
        fighter.weapon == "кулаком" for fighter in fist.fighters.values()
    )
    for user_id in list(fist.fighters):
        service._busy.pop(user_id, None)
    service._forget_battle(fist)

    armed = await gather(102, FightMode.ARMED)
    assert all(fighter.weapon == "битой" for fighter in armed.fighters.values())
    await service.shutdown()


# ---------- что именно видит ветка ----------


def test_pairs_swap_every_round_and_come_back():
    """Раунд 1: Т—А и М—Б. Раунд 2: Т—Б и М—А. Раунд 3: снова как в первом."""
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 5)}
    teams = {1: RED, 2: RED, 3: BLUE, 4: BLUE}

    rounds = [pair_up(fighters, teams, BattleKind.TEAM, r)[0] for r in (1, 2, 3, 4)]
    assert rounds[0] == [(1, 3), (2, 4)]
    assert rounds[1] == [(1, 4), (2, 3)]
    assert rounds[2] == rounds[0]
    assert rounds[3] == rounds[1]


def test_uneven_sides_rotate_after_a_full_circle():
    """Трое против двоих: сперва каждый обходит обоих, потом меняется лишний."""
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 6)}
    teams = {1: RED, 2: RED, 3: RED, 4: BLUE, 5: BLUE}

    seen: dict[int, set[int]] = {1: set(), 2: set(), 3: set()}
    idle_seen = []
    for round_number in range(1, 7):
        pairs, idle = pair_up(fighters, teams, BattleKind.TEAM, round_number)
        for red, blue in pairs:
            seen[red].add(blue)
        idle_seen += idle

    assert all(rivals == {4, 5} for rivals in seen.values()), "не все встретились"
    assert set(idle_seen) == {1, 2, 3}, "без пары стоит один и тот же"


async def test_the_judge_says_it_once(bot, db):
    """Про выбывшего и про безпарного судья говорит один раз, а не каждый раунд."""
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    await fight_to_the_end(service, session)

    log = "\n".join(bot.texts)
    for player in players:
        # каждое имя объявляют выбывшим не больше одного раза
        assert log.count(f"Выбывает из боя: <b>{player.nickname}</b>") <= 1
    # строка про безпарных появляется только при смене расклада, а не каждый ход
    rounds = sum(1 for text in bot.texts if "Пары этого хода" in text)
    assert log.count("Без пары") < rounds
    await service.shutdown()


async def test_the_result_lists_damage_exp_credits_and_rating(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    await fight_to_the_end(service, session)

    summary = next(text for text in bot.texts if "📊" in text)
    for player in players:
        line = next(
            row
            for row in summary.splitlines()
            if player.nickname in row and row.startswith(("🎉", "❌"))
        )
        assert "нанесено урона" in line
        assert "опыта" in line and "рейтинга" in line
        if line.startswith("❌"):
            assert "получено 0 очков опыта" in line
    await service.shutdown()


async def test_health_starts_healing_when_the_battle_ends(bot, db):
    """Упавший в третьем раунде отлёживается с конца боя, а не с момента падения."""
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    # роняем одного сразу, а бой тянем дальше
    early = players[3].user_id
    session.fighters[early].hp = 0
    session.pairs, session.idle = pair_up(
        session.fighters, session.teams, session.kind, session.round_number
    )
    await fight_to_the_end(service, session)

    saved = {}
    for player in players:
        saved[player.user_id] = await db.get_player(player.user_id)
    stamps = {row.hp_at for row in saved.values()}
    assert max(stamps) - min(stamps) <= 1, "часы старта восстановления разъехались"
    assert saved[early].hp == 0
    await service.shutdown()


async def test_a_group_win_pays_no_credits_either(bot, db):
    """Кредиты и здесь только за рост: за сам бой касса молчит."""
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    before = {
        player.user_id: (player.credits, earned_credits(player)) for player in players
    }
    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    await fight_to_the_end(service, session)

    for player in players:
        fresh = await db.get_player(player.user_id)
        was, grown = before[player.user_id]
        # прибавка к счёту ровно та, что дали апы и уровни за этот бой
        assert fresh.credits - was == earned_credits(fresh) - grown, fresh.nickname
    await service.shutdown()


async def test_the_group_board_stacks_the_pairs_one_under_another(bot, db):
    """Табло группового боя — те же четыре строки на пару, через пустую."""
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in zip(players[1:], (RED, BLUE, BLUE)):
        await service.join(lobby.id, player, team)
    session = service.battle_in_chat(CHAT_ID, THREAD_ID)

    board = service._prompt_text(session).splitlines()

    pairs = [line for line in board if "VS." in line]
    assert len(pairs) == len(session.pairs) == 2
    assert board.count("") >= 2  # пары разделены пустой строкой
    assert "<pre>" in "\n".join(board)  # табло идёт моноширинным блоком
    assert board[-1].endswith("Выберите удар и блок.")
    await service.shutdown()
