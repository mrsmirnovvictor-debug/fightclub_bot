"""Рейды: сбор отряда, волны против босса, итог и призы.

Босс в этих тестах намеренно слабый или, наоборот, несокрушимый — рейд
проверяется как механика, а не как баланс: за баланс отвечает симулятор.
"""

import random

import pytest

from bot.config import Config
from bot.game.combat import Fighter
from bot.game.equipment import get_item
from bot.game.raid import (
    BOSS_ID,
    LEVELS_ABOVE,
    MAX_PARTY,
    CELLAR_BOSS,
    RaidEnd,
    boss_action,
    boss_fighter,
    boss_level,
    judge_raid,
    prize_for,
)
from bot.raid_service import RaidError, RaidService
from tests.test_duel_flow import FakeBot
from tests.test_battle_flow import make_player

CHAT_ID = -100500
THREAD_ID = 7
ZONES = ["head", "chest", "belly", "belt", "legs"]


def make_service(bot, db, **over) -> RaidService:
    settings = dict(
        bot_token="test",
        db_path=":memory:",
        raid_lobby_timeout=600,
        raid_turn_timeout=600,
        raid_break=0,
        raid_price=0,
    )
    settings.update(over)
    return RaidService(
        bot=bot, db=db, config=Config(**settings), rng=random.Random(7)
    )


@pytest.fixture
def bot():
    return FakeBot()


async def fill(db, count: int, first_id: int = 1, level: int = 5) -> list:
    players = []
    for index in range(count):
        player = make_player(first_id + index, f"Боец{first_id + index}")
        player.level = level
        await db.save_player(player)
        players.append(player)
    return players


async def gather(service, db, count: int = 2, size: int | None = None):
    """Собрать отряд и выйти на босса."""
    players = await fill(db, count)
    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, players[0], size or count, chat_title="Клуб"
    )
    for player in players[1:]:
        await service.join(lobby.id, player)
    return players, service.raid_of_user(players[0].user_id)


async def punch(service, session, user_id: int, zone: str = "head") -> None:
    """Отработать ход одним бойцом: удар и блок."""
    await service.handle_choice(session.id, user_id, "attack", zone)
    await service.handle_choice(session.id, user_id, "block", "belt")


async def storm(service, session, players) -> None:
    """Пройтись по отряду, пока рейд ещё идёт: босс может упасть на первом."""
    for player in players:
        if service.raid_of_user(player.user_id) is None:
            return
        if not session.fighters[player.user_id].alive:
            continue
        await punch(service, session, player.user_id)


def weaken(session, hp: int = 1) -> None:
    """Оставить боссу на один удар: рейд должен закончиться победой."""
    session.enemy.hp = hp


# ---------- правила ----------


def test_the_boss_stands_four_levels_above_the_party():
    """Босс считается от среднего: ветеран в компании новичков их не топит."""
    assert boss_level([5, 5, 5]) == 5 + LEVELS_ABOVE
    assert boss_level([1, 1, 1, 9]) == 3 + LEVELS_ABOVE
    assert boss_level([]) == 1 + LEVELS_ABOVE


def test_the_boss_comes_dressed():
    """Все слоты заняты, а в руке — оружие этого босса."""
    enemy = boss_fighter(CELLAR_BOSS, [6, 6], hp_share=0)

    kit = {slot.value: owned.code for slot, owned in enemy.equipment.items.items()}

    assert len(kit) == 8  # восемь слотов, пустых нет
    assert kit["weapon"] == CELLAR_BOSS.weapon
    assert enemy.user_id == BOSS_ID
    assert enemy.level == 6 + LEVELS_ABOVE


def test_the_boss_grows_with_the_crowd():
    """Он дерётся с каждым по очереди, поэтому толпе достаётся босс потолще."""
    alone = boss_fighter(CELLAR_BOSS, [5, 5], hp_share=0)
    crowd = boss_fighter(CELLAR_BOSS, [5] * 10, hp_share=0.4)

    assert crowd.max_hp > alone.max_hp * 2
    # без доли босс один и тот же на любой отряд
    assert boss_fighter(CELLAR_BOSS, [5] * 10, hp_share=0).max_hp == alone.max_hp


def test_the_boss_swings_at_random():
    """Босс не выбирает зону с умыслом — и удар, и блок у него случайные."""
    rng = random.Random(1)
    moves = {
        (action.attack.value, action.block[0].value)
        for action in (boss_action(rng) for _ in range(50))
    }
    assert len(moves) > 5


def make_fighter(user_id: int, hp: int = 50) -> Fighter:
    from bot.game.classes import get_class

    fclass = get_class("warrior")
    fighter = Fighter(user_id, f"Боец{user_id}", fclass, fclass.base_stats)
    fighter.hp = hp
    return fighter


def test_the_raid_is_won_while_someone_still_stands():
    boss = make_fighter(BOSS_ID, hp=0)
    party = {1: make_fighter(1, hp=10), 2: make_fighter(2, hp=0)}

    outcome = judge_raid(boss, party)

    assert outcome.end is RaidEnd.WIN
    assert outcome.survivors == [1]


def test_everyone_down_with_the_boss_is_a_draw():
    boss = make_fighter(BOSS_ID, hp=0)
    party = {1: make_fighter(1, hp=0), 2: make_fighter(2, hp=0)}

    assert judge_raid(boss, party).end is RaidEnd.DRAW


def test_a_standing_boss_over_a_dead_party_is_a_loss():
    boss = make_fighter(BOSS_ID, hp=40)
    party = {1: make_fighter(1, hp=0)}

    assert judge_raid(boss, party).end is RaidEnd.LOSS


def test_while_both_stand_the_raid_goes_on():
    assert judge_raid(make_fighter(BOSS_ID, 40), {1: make_fighter(1, 10)}) is None


def test_the_prize_is_something_you_can_wear_today():
    """Вещь с прилавка своего уровня: приз на вырост — мёртвый груз."""
    rng = random.Random(3)
    for _ in range(20):
        code = prize_for(2, rng)
        assert get_item(code).level_required <= 2


# ---------- сбор отряда ----------


async def test_a_raid_gathers_a_party(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 3)

    lobby = await service.open_raid(CHAT_ID, THREAD_ID, players[0], 3)
    await service.join(lobby.id, players[1])

    assert lobby.total == 2 and not lobby.is_full
    assert service.lobby_of_user(players[1].user_id) is lobby
    # третий добирает состав — и рейд начинается сам
    await service.join(lobby.id, players[2])
    assert service.raid_of_user(players[0].user_id) is not None


async def test_the_party_size_has_edges(bot, db):
    service = make_service(bot, db)
    player = (await fill(db, 1))[0]

    with pytest.raises(RaidError, match="от 2 до 10"):
        await service.open_raid(CHAT_ID, THREAD_ID, player, 1)
    with pytest.raises(RaidError, match="от 2 до 10"):
        await service.open_raid(CHAT_ID, THREAD_ID, player, MAX_PARTY + 1)


async def test_one_raid_a_day_from_one_fighter(bot, db):
    """Свой рейд — раз в сутки. В чужой можно идти хоть сразу."""
    service = make_service(bot, db)
    players = await fill(db, 3)
    lobby = await service.open_raid(CHAT_ID, THREAD_ID, players[0], 2)
    await service.leave(lobby.id, players[0].user_id)  # сбор закрылся сам собой

    # запись о попытке снимается вместе с несостоявшимся сбором
    await service.open_raid(CHAT_ID, THREAD_ID, players[0], 2)
    await service.join(service.lobby_of_user(players[0].user_id).id, players[1])

    session = service.raid_of_user(players[0].user_id)
    weaken(session)
    await storm(service, session, players)

    with pytest.raises(RaidError, match="раз в сутки"):
        await service.open_raid(CHAT_ID, THREAD_ID, players[0], 2)
    # второму собирать никто не мешал
    await service.open_raid(CHAT_ID, THREAD_ID, players[1], 2)


async def test_a_failed_gathering_gives_the_money_back(bot, db):
    """Сбор не состоялся — и попытка, и кредиты возвращаются."""
    service = make_service(bot, db, raid_price=25)
    player = (await fill(db, 1))[0]
    player.credits = 100
    await db.save_player(player)

    lobby = await service.open_raid(CHAT_ID, THREAD_ID, player, 3)
    assert (await db.get_player(player.user_id)).credits == 75

    await service._cancel_lobby(lobby, "время вышло")

    assert (await db.get_player(player.user_id)).credits == 100
    assert await db.raid_cooldown(player.user_id, 24 * 3600) == 0


async def test_a_raid_cannot_be_afforded_without_credits(bot, db):
    service = make_service(bot, db, raid_price=500)
    player = (await fill(db, 1))[0]

    with pytest.raises(RaidError, match="стоит 500"):
        await service.open_raid(CHAT_ID, THREAD_ID, player, 2)


# ---------- волны ----------


async def test_a_press_resolves_that_fighter_at_once(bot, db):
    """Нажал — размен посчитан сразу, остальных не ждём."""
    service = make_service(bot, db)
    players, session = await gather(service, db, 3, size=3)
    before = session.enemy.hp

    await punch(service, session, players[0].user_id)

    assert players[0].user_id in session.acted
    assert session.enemy.hp <= before
    assert len(session.waiting_for()) == 2  # волна ещё идёт


async def test_a_wave_closes_when_everyone_has_swung(bot, db):
    service = make_service(bot, db)
    players, session = await gather(service, db, 2)

    for player in players:
        await punch(service, session, player.user_id)

    assert session.wave == 2  # следующая волна началась сама
    assert session.acted == set()


async def test_silence_costs_a_turn_but_the_boss_swings_anyway(bot, db):
    """Не нажал за отпущенное время — пропустил удар, а босс своё берёт."""
    service = make_service(bot, db)
    players, session = await gather(service, db, 2)
    quiet = players[1]
    before = session.fighters[quiet.user_id].hp

    await punch(service, session, players[0].user_id)
    await service.skip_the_rest(session)

    assert session.fighters[quiet.user_id].damage_dealt == 0
    assert session.fighters[quiet.user_id].hp <= before
    assert session.wave == 2


async def test_the_judge_calls_a_break_after_six_strikes(bot, db):
    service = make_service(bot, db, raid_break=60, raid_strikes_per_break=6)
    players, session = await gather(service, db, 3, size=3)

    for _ in range(2):  # шесть ударов: три бойца по два раза
        for player in players:
            await punch(service, session, player.user_id)

    assert session.resting is True
    assert session.strikes == 0
    with pytest.raises(RaidError, match="Передышка"):
        await punch(service, session, players[0].user_id)
    service._cancel_timer(session)


async def test_the_dead_do_not_press(bot, db):
    service = make_service(bot, db)
    players, session = await gather(service, db, 2)
    session.fighters[players[1].user_id].hp = 0

    with pytest.raises(RaidError, match="вынесли"):
        await punch(service, session, players[1].user_id)


async def test_nobody_swings_twice_in_one_wave(bot, db):
    service = make_service(bot, db)
    players, session = await gather(service, db, 3, size=3)

    await punch(service, session, players[0].user_id)

    with pytest.raises(RaidError, match="уже отработал"):
        await punch(service, session, players[0].user_id)


# ---------- итог и награда ----------


async def test_a_dead_boss_pays_everyone_and_the_top_three(bot, db):
    service = make_service(bot, db, raid_reward=50)
    players, session = await gather(service, db, 4, size=4)

    # первая волна — чтобы у каждого был свой счёт по урону
    await storm(service, session, players)
    weaken(session, hp=1)
    await storm(service, session, players)

    for player in players:
        fresh = await db.get_player(player.user_id)
        assert fresh.credits >= 50, "кредиты за победу получают все"
        assert fresh.wins == 1
    # вещи достались троим, и только тем, кто успел набить урон
    gear = [len(await db.list_gear(player.user_id)) for player in players]
    assert sum(1 for count in gear if count) == 3

    raids = await db.raids_of(players[0].user_id)
    assert raids and raids[0]["outcome"] == "win"
    assert raids[0]["boss_level"] == session.enemy.level


async def test_a_lost_raid_pays_nothing(bot, db):
    service = make_service(bot, db, raid_reward=50)
    players, session = await gather(service, db, 2)
    for user_id, fighter in session.fighters.items():
        fighter.hp = 1 if user_id == players[0].user_id else 0

    # последний живой падает — отряд кончился, босс на ногах
    session.fighters[players[0].user_id].hp = 0
    await service.skip_the_rest(session)

    for player in players:
        fresh = await db.get_player(player.user_id)
        assert fresh.credits == 0 and fresh.losses == 1
        assert await db.list_gear(player.user_id) == []
    assert (await db.raids_of(players[0].user_id))[0]["outcome"] == "loss"


async def test_the_result_holds_the_screen_until_it_is_closed(bot, db):
    """Итог рейда остаётся за бойцом: в аппе его больше прочитать негде."""
    service = make_service(bot, db)
    players, session = await gather(service, db, 2)
    weaken(session)
    await storm(service, session, players)

    kept = service.result_of_user(players[0].user_id)

    assert kept is session and kept.finished
    assert any("Босс повержен" in line for line in kept.summary)
    service.forget_result(players[0].user_id)
    assert service.result_of_user(players[0].user_id) is None


async def test_the_judge_speaks_the_same_words_as_the_log(bot, db):
    """В ветку и в разбор уходит одно и то же — судья говорит один раз."""
    service = make_service(bot, db)
    players, session = await gather(service, db, 2)

    await punch(service, session, players[0].user_id)

    turn = session.rounds[0]
    assert turn["lines"], "судья промолчал"
    assert {strike["attacker_id"] for strike in turn["strikes"]} == {
        players[0].user_id, BOSS_ID
    }


async def test_health_survives_the_raid(bot, db):
    """С чем вышел из подвала, с тем и остался: здоровье пишется в базу."""
    service = make_service(bot, db)
    players, session = await gather(service, db, 2)
    weaken(session)
    session.fighters[players[0].user_id].hp = 17

    # босса роняет второй; первый в этой волне так и не успел ударить
    await punch(service, session, players[1].user_id)

    assert (await db.get_player(players[0].user_id)).current_hp() == 17
