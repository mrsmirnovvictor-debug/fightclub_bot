"""Бои на много бойцов: сбор состава, пары по ходам, выбывание, итог."""

import random

import pytest

from bot.battle_service import BattleError, BattleService
from bot.config import Config
from bot.game.battle import (
    BLUE,
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


# Сколько раундов стенд готов отсчитать, прежде чем объявить бой
# зависшим. Гонга в бою больше нет, и число это не правило, а предел
# терпения стенда: замер пары «ассасин и трикстер» против «ассасина и
# танка» на шестом уровне дал самый долгий бой из пяти тысяч на
# семьдесят третьем раунде, так что запас взят с четырёхкратным
PATIENCE = 300


async def fight_to_the_end(service: BattleService, session) -> None:
    for _ in range(PATIENCE):
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


def test_the_judge_counts_health_only_when_called_with_a_gong():
    """По здоровью судья считает лишь тогда, когда его позвали с гонгом.

    Гонг остался для одного случая: бой надо закрыть прямо сейчас,
    потому что пары больше не складываются. Сам по себе бой до него не
    доходит — он идёт до победы.
    """
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 5)}
    teams = {1: RED, 2: RED, 3: BLUE, 4: BLUE}
    fighters[1].hp = fighters[2].hp = 50
    fighters[3].hp = fighters[4].hp = 20

    outcome = judge(fighters, teams, BattleKind.TEAM, 25, gong=25)
    assert outcome.finished and outcome.by_rounds and outcome.winning_team == RED


def test_a_team_battle_has_no_gong_and_goes_to_the_victory():
    """Без гонга бой не кончается, сколько бы раундов ни прошло.

    Прежде он обрывался на двадцать пятом, и побеждал тот, у кого в
    этот миг выше доля здоровья, — то есть тот, у кого запас больше.
    Про ту же ошибку в дуэлях сказано в `judge_decision`, а в бою на
    многих она жила: «ассасин и трикстер» против «ассасина и танка» на
    шестом уровне брали с гонгом 53%, а до победы берут 76%.
    """
    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 5)}
    teams = {1: RED, 2: RED, 3: BLUE, 4: BLUE}
    fighters[1].hp = fighters[2].hp = 50
    fighters[3].hp = fighters[4].hp = 20

    for number in (25, 100, 1000):
        assert not judge(fighters, teams, BattleKind.TEAM, number).finished, number
    # И в мясорубке тоже: там гонга не стало вместе с командным боем
    assert not judge(fighters, {}, BattleKind.ROYALE, 1000).finished


def test_a_fighter_who_went_silent_leaves_the_fight():
    """Молчащий три хода подряд выходит из боя, и бой на этом кончается.

    Гонга больше нет, а пустое действие урона не наносит: без этого
    правила брошенный всеми бой не кончился бы никогда и сыпал бы в
    чат раунд за раундом по таймеру. Наказание то же, что в дуэли, и
    здоровье ушедшему сохраняется — он не побит, он ушёл.
    """
    from bot.game.combat import MAX_MISSED_TURNS
    from bot.game.battle import in_fight

    fighters = {user_id: make_fighter(user_id) for user_id in range(1, 5)}
    teams = {1: RED, 2: RED, 3: BLUE, 4: BLUE}

    assert not judge(fighters, teams, BattleKind.TEAM, 50).finished
    for user_id in (3, 4):
        fighters[user_id].missed_turns = MAX_MISSED_TURNS
        assert not in_fight(fighters[user_id])
        assert fighters[user_id].alive, "ушедший не побит, здоровье при нём"

    outcome = judge(fighters, teams, BattleKind.TEAM, 50)
    assert outcome.finished and outcome.winning_team == RED
    # И в пары ушедших больше не ставят
    pairs, idle = pair_up(fighters, teams, BattleKind.TEAM, 51)
    assert pairs == [] and sorted(idle) == [1, 2]


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


async def test_the_lobby_waits_as_long_as_the_opener_said(bot, db):
    """Срок сбора — у сбора, а не у клуба.

    Раньше он был общий и приезжал из настроек бота: три минуты всем,
    кто бы ни собирал. Теперь созывающий говорит, сколько ждёт, — и
    по этому сроку тикают часы и в чате, и в карточке.
    """
    from bot.game.battle import wait_seconds

    service = make_service(bot, db, lobby_timeout=600)
    players = await fill(db, 2)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2,
        wait=wait_seconds(3),
    )
    assert lobby.wait == 180
    assert any("3 мин" in call.text for call in bot.sent)

    # А из чата срок прежний, общий: там его не называют
    await service.leave(lobby.id, players[0].user_id)
    second = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    assert second.wait == 600
    await service.shutdown()


async def test_the_royale_starts_on_timeout_with_two(bot, db):
    """Королевская битва идёт, если к сроку собрались хотя бы двое."""
    service = make_service(bot, db, lobby_timeout=0)
    players = await fill(db, 5)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.ROYALE, size=8
    )
    await service.join(lobby.id, players[1])

    await lobby.task
    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    assert session is not None and len(session.fighters) == 2
    assert session.kind is BattleKind.ROYALE
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
    assert board[-1].endswith("Удар и блок — в карточке, вкладка «Клуб».")
    await service.shutdown()


# ---------- опыт за групповой бой ----------


async def test_the_group_fight_pays_by_the_wide_corridor(bot, db):
    """В клубном бою опыт считается групповой формулой, а не дуэльной.

    Коридор в десять раз шире дуэльного: полтора базового достаётся
    тому, кто уложил полтора средних бойца, а не тому, кто простоял в
    блоке, пока отряд работал. Сверяем не «больше нуля», а ровно то
    число, которое даёт формула на числах этого самого боя.
    """
    import math

    from bot.game.economy import base_exp, damage_share

    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    levels = {one: session.fighters[one].level for one in session.fighters}
    hp_total = sum(one.max_hp for one in session.fighters.values())
    on_ring = len(session.fighters)
    dealt = dict(session.fighters)
    await fight_to_the_end(service, session)

    winners = [one for one in players if (await db.get_player(one.user_id)).wins]
    assert winners, "бой кончился без победителей"
    for player in winners:
        fresh = await db.get_player(player.user_id)
        share = damage_share(
            dealt[player.user_id].damage_dealt, hp_total, on_ring, group=True
        )
        rate = base_exp(max(levels.values()))
        assert fresh.total_exp == math.ceil(rate * share), player.nickname
        # И коридор именно широкий: дуэльный обрезал бы на пяти процентах
        assert share <= 1.5
    await service.shutdown()


async def test_the_losers_of_a_group_fight_get_nothing(bot, db):
    """Опыт за поражение — ноль, как и за ничью."""
    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    for player, team in ((players[1], RED), (players[2], BLUE), (players[3], BLUE)):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    await fight_to_the_end(service, session)

    for player in players:
        fresh = await db.get_player(player.user_id)
        if not fresh.wins:
            assert fresh.total_exp == 0, fresh.nickname
    await service.shutdown()


async def test_the_rate_of_a_mixed_battle_comes_from_the_oldest_fighter(bot, db):
    """Ставка берётся по старшему на ринге, но не выше своего плюс два.

    В смешанном составе младший получает по ставке старшего — ровно
    настолько, насколько до неё дотягивается. Без предела первый уровень
    проходил бы не боец, а его старший знакомый.
    """
    import math

    from bot.game.economy import base_exp, base_level, damage_share

    service = make_service(bot, db)
    juniors = await fill(db, 2, first_id=1, level=1)
    seniors = await fill(db, 2, first_id=3, level=12)
    # Рамки созывают руками: по умолчанию сбор пускает ±2 уровня, и
    # такой разброс в него не влез бы
    lobby = await service.open_lobby(
        CHAT_ID,
        THREAD_ID,
        juniors[0],
        BattleKind.TEAM,
        size=2,
        levels=(1, 12),
    )
    for player, team in (
        (juniors[1], BLUE), (seniors[0], RED), (seniors[1], BLUE)
    ):
        await service.join(lobby.id, player, team)

    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    hp_total = sum(one.max_hp for one in session.fighters.values())
    on_ring = len(session.fighters)
    dealt = dict(session.fighters)
    await fight_to_the_end(service, session)

    for player in juniors + seniors:
        fresh = await db.get_player(player.user_id)
        if not fresh.wins:
            continue
        rate = base_exp(base_level(player.level, 12))
        share = damage_share(
            dealt[player.user_id].damage_dealt, hp_total, on_ring, group=True
        )
        assert fresh.total_exp == math.ceil(rate * share), fresh.nickname
        if player.level == 1:
            # Дотянулся на две ступени, а не до двенадцатой
            assert rate == base_exp(3)
    await service.shutdown()


async def test_a_group_fight_lands_in_the_elo_journal(bot, db):
    """Групповой бой идёт в недельный рейтинг наравне с дуэлью.

    Так решил заказчик: это такой же PvP-бой между живыми игроками, и
    собирают его в здании Бойцовского клуба. Записывается он тем же
    журналом ELO, по которому считается неделя
    (`bot.ladder_service`), — одним мигом на всех, кто вышел на ринг.
    """
    from bot.ladder_service import my_week

    service = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    await service.join(lobby.id, players[1], RED)
    await service.join(lobby.id, players[2], BLUE)
    await service.join(lobby.id, players[3], BLUE)
    session = service.battle_in_chat(CHAT_ID, THREAD_ID)
    await fight_to_the_end(service, session)

    keys = set()
    stamps = set()
    for player in players:
        rows = await db.rating_log_of(player.user_id)
        assert len(rows) == 1, f"бой не записан бойцу {player.user_id}"
        assert rows[0]["kind"] == "battle"
        assert rows[0]["outcome"] in ("win", "loss", "draw")
        # Записанный рейтинг — тот же, что в карточке
        assert rows[0]["rating"] == (await db.get_player(player.user_id)).rating
        assert await my_week(db, player.user_id) == 1
        keys.add(rows[0]["fight"])
        stamps.add(rows[0]["at"])

    assert len(keys) == 1, "у одного боя оказалось несколько ключей"
    assert len(stamps) == 1, "бойцы одного боя зафиксированы разным мигом"
    await service.shutdown()


async def test_a_duel_and_a_group_fight_do_not_share_a_journal_key(bot, db):
    """Номера дуэлей и отрядов свои — в журнале их разводит род боя.

    Оба счётчика живут в памяти и после перезапуска начинают с единицы.
    Без рода боя первая дуэль и первый групповой бой оказались бы одним
    боем, и второй из них в рейтинг не попал бы.
    """
    from bot.duel_service import DuelService
    from tests.test_duel_flow import make_service as duel_service
    from tests.test_duel_flow import play_round

    battles = make_service(bot, db)
    players = await fill(db, 4)
    lobby = await battles.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=2
    )
    await battles.join(lobby.id, players[1], RED)
    await battles.join(lobby.id, players[2], BLUE)
    await battles.join(lobby.id, players[3], BLUE)
    await fight_to_the_end(battles, battles.battle_in_chat(CHAT_ID, THREAD_ID))
    await battles.shutdown()

    duels: DuelService = duel_service(bot, db)
    session = await duels.start_duel(
        CHAT_ID, THREAD_ID + 1,
        await db.get_player(1), await db.get_player(2),
    )
    for _ in range(40):
        if duels.duel_in_chat(CHAT_ID, THREAD_ID + 1) is None:
            break
        await play_round(duels, session)

    rows = await db.rating_log_of(1)
    assert len(rows) == 2, "один из двух боёв пропал"
    assert {row["kind"] for row in rows} == {"duel", "battle"}
