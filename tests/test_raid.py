"""Рейды: сбор отряда, волны против босса, итог и призы.

Босс в этих тестах намеренно слабый или, наоборот, несокрушимый — рейд
проверяется как механика, а не как баланс: за баланс отвечает симулятор.
"""

import random
from datetime import date, timedelta

import pytest

from bot.config import Config
from bot.game.combat import Fighter
from bot.game.potions import RAID_PASS
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
    elixir_for,
    ELIXIR_CHANCE,
    ELIXIR_PRIZES,
    MOSCOW,
    RAIDS_PER_DAY,
    RAID_PURSE,
    RAID_SLOTS,
    WINDOW_HOURS,
    next_window,
    schedule_text,
    slots_on,
    shares_of,
    window_of,
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
        # Расписание в тестах по умолчанию снято: проверяет его свой тест
        raid_any_time=True,
    )
    settings.update(over)
    return RaidService(
        bot=bot, db=db, config=Config(**settings), rng=random.Random(7)
    )


@pytest.fixture
def bot():
    return FakeBot()


async def fill(
    db, count: int, first_id: int = 1, level: int = 5, passes: int = 1
) -> list:
    """Бойцы с пропусками в рюкзаке: без них в подвал не пускают."""
    players = []
    for index in range(count):
        player = make_player(first_id + index, f"Боец{first_id + index}")
        player.level = level
        player.credits = 500
        await db.save_player(player)
        for _ in range(passes):
            player.potions[RAID_PASS] = await db.add_potion(player.user_id, RAID_PASS)
        players.append(player)
    return players


async def gather(service, db, count: int = 2, size: int | None = None):
    """Собрать отряд и выйти на босса. size остался для читаемости вызовов."""
    players = await fill(db, count)
    lobby = await service.open_raid(CHAT_ID, THREAD_ID, players[0], chat_title="Клуб")
    for player in players[1:]:
        await service.join(lobby.id, player)
    if service.raid_of_user(players[0].user_id) is None:
        await service.start_now(lobby.id, players[0].user_id)
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


def test_the_boss_comes_dressed_and_shielded():
    """Все слоты заняты, в руке оружие этого босса, во второй — щит."""
    from bot.game.equipment import ALL_SLOTS, get_item

    enemy = boss_fighter(CELLAR_BOSS, [6, 6], hp_share=0)

    kit = {slot.value: owned.code for slot, owned in enemy.equipment.items.items()}

    assert len(kit) == len(ALL_SLOTS)  # пустых слотов нет
    assert kit["weapon"] == CELLAR_BOSS.weapon
    # щит: блок в три зоны и броня по всему телу
    shield = get_item(kit["offhand"])
    assert shield.is_shield and enemy.has_shield
    assert enemy.block_width == 3
    assert shield.armor_max >= 10
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
        (action.attacks[0].value, action.block[0].value)
        for action in (boss_action(rng=rng) for _ in range(50))
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


def test_the_prize_is_an_elixir_and_not_every_time():
    """Лучшему по урону — склянка, и то не всегда."""
    from bot.game.potions import get_potion

    rng = random.Random(3)
    got = [elixir_for(rng) for _ in range(2000)]
    won = [code for code in got if code]
    assert set(won) == set(ELIXIR_PRIZES)
    assert all(get_potion(code).is_boost for code in won)
    # доля выпавших близка к заявленной
    assert abs(len(won) / len(got) - ELIXIR_CHANCE) < 0.05


def test_the_purse_is_split_evenly_and_nothing_is_lost():
    """Сто кредитов делятся поровну, остаток уходит первым по урону."""
    for party in range(1, MAX_PARTY + 1):
        shares = shares_of(RAID_PURSE, party)
        assert len(shares) == party
        assert sum(shares) == RAID_PURSE  # округление ничего не съедает
        assert max(shares) - min(shares) <= 1
    assert shares_of(RAID_PURSE, 1) == [100]
    assert shares_of(RAID_PURSE, 10) == [10] * 10


# ---------- расписание подвала ----------


def moscow(hour: int, minute: int = 0, day: int = 8) -> int:
    from datetime import datetime

    return int(datetime(2026, 9, day, hour, minute, tzinfo=MOSCOW).timestamp())


DAY = date(2026, 9, 8)


def test_the_cellar_opens_twice_a_day():
    """Два окна по два часа, всё остальное время подвал закрыт."""
    slots = slots_on(DAY)
    assert len(slots) == RAIDS_PER_DAY
    open_hours = {hour + step for hour in slots for step in range(WINDOW_HOURS)}

    for hour in range(24):
        window = window_of(moscow(hour))
        assert bool(window) is (hour in open_hours), hour

    first = slots[0]
    assert window_of(moscow(first)).start == moscow(first)
    assert window_of(moscow(first, 59)).title == (
        f"с {first:02d}:00 до {first + WINDOW_HOURS:02d}:00 мск"
    )


def test_the_slots_are_drawn_anew_every_day():
    """Слоты меняются изо дня в день и берутся из установленных."""
    seen = []
    for step in range(60):
        slots = slots_on(DAY + timedelta(days=step))
        assert len(slots) == RAIDS_PER_DAY
        assert set(slots) <= set(RAID_SLOTS), slots
        # в одно и то же время два дня подряд подвал не открывается
        assert not (seen and set(slots) & set(seen[-1])), (seen[-1], slots)
        seen.append(slots)
    # и это не круг из двух расписаний: за два месяца выпадают все пары
    assert len(set(seen)) > RAIDS_PER_DAY * 2


def test_the_draw_is_the_same_for_everyone():
    """Жребий заведён датой: бот, мини-апп и перезапуск видят одно и то же."""
    import bot.game.raid as rules

    before = slots_on(DAY + timedelta(days=3))
    rules._SLOTS.clear()  # как после перезапуска — память пуста
    assert slots_on(DAY + timedelta(days=3)) == before


def test_the_next_window_is_the_one_you_wait_for():
    first, second = slots_on(DAY)
    tomorrow = slots_on(DAY + timedelta(days=1))[0]

    assert next_window(moscow(first) - 1).start == moscow(first)
    assert next_window(moscow(first) + 60).start == moscow(second)
    # после последнего окна суток ждут первого завтрашнего
    assert next_window(moscow(23, 59)).start == moscow(tomorrow, day=9)


def test_the_schedule_says_todays_hours():
    """Расписание — на сегодня: слоты каждый день свои."""
    first, second = slots_on(DAY)
    assert schedule_text(moscow(12)) == (
        f"сегодня с {first}:00 до {first + WINDOW_HOURS}:00 "
        f"и с {second}:00 до {second + WINDOW_HOURS}:00 мск"
    )


# ---------- сбор отряда ----------


async def test_a_raid_gathers_a_party(bot, db):
    """Мест всегда десять: отряд — это те, кто успел зайти до гонга."""
    service = make_service(bot, db)
    players = await fill(db, 3)

    lobby = await service.open_raid(CHAT_ID, THREAD_ID, players[0])
    await service.join(lobby.id, players[1])

    assert (lobby.total, lobby.size) == (2, MAX_PARTY)
    assert not lobby.is_full  # десятерых почти никогда и не набирается
    assert service.lobby_of_user(players[1].user_id) is lobby

    await service.join(lobby.id, players[2])
    session = await service.start_now(lobby.id, players[0].user_id)
    assert session is not None and len(session.fighters) == 3


async def test_a_lone_fighter_may_go_down_alone(bot, db):
    """Отряд из одного — можно: босс подстроится под кого угодно."""
    service = make_service(bot, db)
    player = (await fill(db, 1))[0]

    lobby = await service.open_raid(CHAT_ID, THREAD_ID, player)
    session = await service.start_now(lobby.id, player.user_id)

    assert session is not None and len(session.fighters) == 1
    assert lobby.size == MAX_PARTY  # мест всегда десять, набирается кто успел


# ---------- пропуска ----------


async def test_the_pass_is_taken_once_per_window(bot, db):
    """Один пропуск на окно: проиграл — заходи снова, платить не нужно."""
    service = make_service(bot, db)
    player = (await fill(db, 1))[0]
    assert player.potion_count(RAID_PASS) == 1

    lobby = await service.open_raid(CHAT_ID, THREAD_ID, player)
    assert (await db.get_player(player.user_id)).potion_count(RAID_PASS) == 0

    # вышел из лобби и собрал заново — второй пропуск не нужен
    await service.leave(lobby.id, player.user_id)
    fresh = await db.get_player(player.user_id)
    await service.open_raid(CHAT_ID, THREAD_ID, fresh)
    assert (await db.get_player(player.user_id)).potion_count(RAID_PASS) == 0


async def test_without_a_pass_the_cellar_does_not_open(bot, db):
    service = make_service(bot, db)
    player = (await fill(db, 1, passes=0))[0]

    with pytest.raises(RaidError, match="Рейд-пасс"):
        await service.open_raid(CHAT_ID, THREAD_ID, player)
    assert service.lobby_of_user(player.user_id) is None


async def test_a_pass_can_be_bought_on_the_way_in(bot, db):
    """Пропуска нет, но есть кредиты — покупаем одним движением."""
    from bot.game.potions import get_potion

    service = make_service(bot, db)
    player = (await fill(db, 1, passes=0))[0]
    purse = player.credits

    await service.open_raid(CHAT_ID, THREAD_ID, player, buy=True)

    saved = await db.get_player(player.user_id)
    assert saved.credits == purse - get_potion(RAID_PASS).price
    assert saved.potion_count(RAID_PASS) == 0  # купили и тут же отдали


async def test_the_cellar_is_shut_outside_its_hours(bot, db, monkeypatch):
    """Расписание сильнее пропуска: не в окно — не пустят."""
    import bot.game.raid as rules

    service = make_service(bot, db, raid_any_time=False)
    player = (await fill(db, 1))[0]
    slots = slots_on(DAY)
    shut = next(hour for hour in range(24) if all(
        not (opens <= hour < opens + WINDOW_HOURS) for opens in slots
    ))
    monkeypatch.setattr(rules, "now_ts", lambda: moscow(shut))  # подвал закрыт

    with pytest.raises(RaidError, match="Подвал закрыт"):
        await service.open_raid(CHAT_ID, THREAD_ID, player)
    # пропуск остался в рюкзаке: за закрытую дверь не платят
    assert (await db.get_player(player.user_id)).potion_count(RAID_PASS) == 1

    monkeypatch.setattr(rules, "now_ts", lambda: moscow(slots[0]))  # окно открылось
    await service.open_raid(CHAT_ID, THREAD_ID, player)
    assert service.lobby_of_user(player.user_id) is not None


async def test_one_win_per_window(bot, db):
    """Победил — в это окно больше не пустят, даже с новым пропуском."""
    service = make_service(bot, db)
    players, session = await gather(service, db, 2)
    weaken(session)
    await storm(service, session, players)
    assert service.raid_of_user(players[0].user_id) is None

    fresh = await db.get_player(players[0].user_id)
    fresh.potions[RAID_PASS] = await db.add_potion(fresh.user_id, RAID_PASS)
    with pytest.raises(RaidError, match="Босс уже повержен"):
        await service.open_raid(CHAT_ID, THREAD_ID, fresh)
    assert (await db.get_player(fresh.user_id)).potion_count(RAID_PASS) == 1


async def test_the_gathering_counts_down_from_the_moment_it_opened(bot, db):
    """Отсчёт идёт от объявления сбора, а не от нажатия."""
    service = make_service(bot, db, raid_lobby_timeout=600)
    player = (await fill(db, 1))[0]

    lobby = await service.open_raid(CHAT_ID, THREAD_ID, player)

    assert lobby.seconds_left(600) == 600
    lobby.opened_at -= 570  # прошло девять с половиной минут
    assert lobby.seconds_left(600) == 30
    lobby.opened_at -= 100  # время вышло — но не ушло в минус
    assert lobby.seconds_left(600) == 0
    assert "выходим через 10 мин" in bot.texts[-1]


async def test_the_opener_can_leave_without_waiting(bot, db):
    """Кнопка «Выходим сейчас»: отряд неполон, но созвавший решил идти."""
    service = make_service(bot, db)
    players = await fill(db, 3)
    lobby = await service.open_raid(CHAT_ID, THREAD_ID, players[0])
    await service.join(lobby.id, players[1])

    session = await service.start_now(lobby.id, players[0].user_id)

    assert session is not None and len(session.fighters) == 2
    assert service.raid_of_user(players[0].user_id) is session
    assert service.lobby_of_user(players[0].user_id) is None


async def test_only_the_opener_leads_the_party_out(bot, db):
    service = make_service(bot, db)
    players = await fill(db, 3)
    lobby = await service.open_raid(CHAT_ID, THREAD_ID, players[0])
    await service.join(lobby.id, players[1])

    with pytest.raises(RaidError, match="кто его собрал"):
        await service.start_now(lobby.id, players[1].user_id)
    assert service.raid_of_user(players[0].user_id) is None


async def test_the_early_start_button_shows_up_with_the_second_fighter(bot, db):
    """Кнопка появляется, когда выходить уже есть с кем."""
    from bot.keyboards import raid_lobby_keyboard

    service = make_service(bot, db)
    players = await fill(db, 3)
    lobby = await service.open_raid(CHAT_ID, THREAD_ID, players[0])

    def buttons():
        return [
            button.text
            for row in raid_lobby_keyboard(lobby).inline_keyboard
            for button in row
        ]

    # выйти можно хоть одному, поэтому кнопка есть с самого начала
    assert any("Выходим сейчас" in text for text in buttons())
    await service.join(lobby.id, players[1])
    assert any("Выходим сейчас" in text for text in buttons())


async def test_a_failed_gathering_keeps_the_window_open(bot, db):
    """Сбор не состоялся — пропуск не вернётся, но окно уже открыто."""
    service = make_service(bot, db)
    player = (await fill(db, 1))[0]

    lobby = await service.open_raid(CHAT_ID, THREAD_ID, player)
    await service._cancel_lobby(lobby, "время вышло")

    saved = await db.get_player(player.user_id)
    assert saved.potion_count(RAID_PASS) == 0
    # и следующий заход в это же окно ничего не стоит
    await service.open_raid(CHAT_ID, THREAD_ID, saved)
    assert service.lobby_of_user(player.user_id) is not None


async def test_a_pass_cannot_be_bought_without_credits(bot, db):
    service = make_service(bot, db)
    player = (await fill(db, 1, passes=0))[0]
    player.credits = 2
    await db.save_player(player)

    with pytest.raises(RaidError, match="Не хватает кредитов"):
        await service.open_raid(CHAT_ID, THREAD_ID, player, buy=True)
    assert service.lobby_of_user(player.user_id) is None


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


async def test_a_dead_boss_splits_the_purse_between_everyone(bot, db):
    """Кошель делится поровну, вещей за рейд не дают вовсе."""
    service = make_service(bot, db, raid_purse=100)
    players, session = await gather(service, db, 4, size=4)
    purse = [(await db.get_player(p.user_id)).credits for p in players]

    for _ in range(4):
        await storm(service, session, players)
    weaken(session, hp=1)
    await storm(service, session, players)

    paid = [
        (await db.get_player(p.user_id)).credits - was
        for p, was in zip(players, purse)
    ]
    assert sum(paid) == 100  # весь кошель дошёл до отряда
    assert max(paid) - min(paid) <= 1  # и разошёлся поровну
    for player in players:
        fresh = await db.get_player(player.user_id)
        # Победа над боссом идёт в счёт рейдов, а не в личные победы
        assert (fresh.raid_wins, fresh.raid_fights) == (1, 1)
        assert (fresh.wins, fresh.losses, fresh.draws) == (0, 0, 0)
        assert await db.list_gear(player.user_id) == [], "вещей за рейд не дают"

    raids = await db.raids_of(players[0].user_id)
    assert raids and raids[0]["outcome"] == "win"
    assert raids[0]["boss_level"] == session.enemy.level


async def test_a_lone_raider_takes_the_whole_purse(bot, db):
    service = make_service(bot, db, raid_purse=100)
    players, session = await gather(service, db, 1)
    was = (await db.get_player(players[0].user_id)).credits

    weaken(session)
    await storm(service, session, players)

    assert (await db.get_player(players[0].user_id)).credits == was + 100


async def test_a_lost_raid_pays_nothing(bot, db):
    service = make_service(bot, db, raid_purse=100)
    players, session = await gather(service, db, 2)
    for user_id, fighter in session.fighters.items():
        fighter.hp = 1 if user_id == players[0].user_id else 0

    # последний живой падает — отряд кончился, босс на ногах
    session.fighters[players[0].user_id].hp = 0
    await service.skip_the_rest(session)

    for player in players:
        fresh = await db.get_player(player.user_id)
        assert fresh.credits == 500  # кошелёк не тронут
        # Неудачный заход в подвал не портит личный счёт: там дрались с
        # боссом, а не с человеком
        assert (fresh.raid_wins, fresh.raid_fights) == (0, 1)
        assert (fresh.wins, fresh.losses, fresh.draws) == (0, 0, 0)
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


async def test_the_fatigue_counts_waves_not_swings(bot, db):
    """Усталость растёт от волны, а не от числа ударов отряда.

    Движок берёт номер раунда, чтобы под конец боя бить сильнее. Волна —
    это по разу на каждого, то есть один раунд для всех. Если считать
    сквозняком, отряд из десяти человек прошёл бы всю шкалу усталости за
    полторы волны, и обычный удар выбивал бы под сотню.
    """
    service = make_service(bot, db)
    players, session = await gather(service, db, 4, size=4)

    for _ in range(2):  # две полные волны — восемь разменов
        await storm(service, session, players)

    assert session.turn_number >= 8, "разменов было много"
    assert session.wave <= 3
    # каждому ходу движок отдавал номер волны, а не номер размена
    assert max(turn["number"] for turn in session.rounds) <= session.wave


# ---------- рейды отделены от боёв с людьми ----------


async def test_old_raids_move_out_of_the_personal_record(bot, db):
    """Прошлые походы уезжают из побед и поражений при обновлении базы.

    До этой версии подвал писался в общий счёт: победа над боссом шла
    победой, неудачный заход — поражением. Терять историю не нужно —
    журнал рейдов помнит и состав отряда, и исход каждого захода,
    поэтому старые числа просто перекладываются в свою колонку.
    """
    player = make_player(1, "Ветеран")
    player.wins, player.losses, player.draws = 7, 4, 2
    await db.save_player(player)
    other = make_player(2, "Сосед")
    other.wins = 3
    await db.save_player(other)

    # три похода ветерана: победа, поражение и ничья — и один брошенный
    for outcome in ("win", "loss", "draw"):
        raid_id = await db.open_raid_record(
            chat_id=None, thread_id=None, opener_id=1, boss="cellar_boss", size=1
        )
        await db.close_raid_record(raid_id, outcome, waves=1, boss_level=5,
                                   members=[(1, 100, True, None)])
    abandoned = await db.open_raid_record(
        chat_id=None, thread_id=None, opener_id=1, boss="cellar_boss", size=1
    )
    assert abandoned  # без outcome он в счёт не идёт

    # база прошлой версии: колонок рейда в ней ещё нет
    await db.conn.execute("UPDATE players SET raid_wins = 0, raid_fights = 0")
    await db.conn.commit()
    await db._split_raids_from_record()

    veteran = await db.get_player(1)
    assert (veteran.raid_wins, veteran.raid_fights) == (1, 3)
    # из личного счёта ушли ровно те три похода
    assert (veteran.wins, veteran.losses, veteran.draws) == (6, 3, 1)
    # тот, кто в подвал не ходил, остался как был
    neighbour = await db.get_player(2)
    assert (neighbour.wins, neighbour.raid_fights) == (3, 0)


async def test_the_split_never_drives_a_record_below_zero(bot, db):
    """Счёт мог разъехаться с журналом — вычитание не уводит его в минус."""
    player = make_player(1, "Счетовод")
    player.wins = 0
    await db.save_player(player)
    raid_id = await db.open_raid_record(
        chat_id=None, thread_id=None, opener_id=1, boss="cellar_boss", size=1
    )
    await db.close_raid_record(raid_id, "win", waves=1, boss_level=5,
                               members=[(1, 10, True, None)])

    await db._split_raids_from_record()

    fresh = await db.get_player(1)
    assert fresh.wins == 0 and (fresh.raid_wins, fresh.raid_fights) == (1, 1)


# ---------- рейд идёт, пока кто-нибудь не упадёт ----------


async def test_a_raid_runs_past_thirty_waves_if_everyone_is_still_standing(bot, db):
    """Счётчика волн у рейда нет: бой идёт столько, сколько нужно.

    Раньше на тридцатой волне судья закрывал рейд поражением отряда.
    Выглядело это дико: босс на ногах, но и в отряде никто даже не ранен,
    а рейд уже проигран. Особенно часто это ловил большой отряд — там
    урон на каждого меньше, и до тридцатой волны никто не успевал упасть.
    """
    from bot.game.raid import FATIGUE_WAVES

    service = make_service(bot, db)
    players, session = await gather(service, db, 2)

    # Держим обоих и босса живыми: пусть волн пройдёт заведомо больше потолка
    for _ in range(FATIGUE_WAVES + 5):
        if service.raid_of_user(players[0].user_id) is None:
            break
        session.enemy.hp = session.enemy.max_hp
        for player in players:
            session.fighters[player.user_id].hp = (
                session.fighters[player.user_id].max_hp
            )
        await storm(service, session, players)

    assert session.wave > FATIGUE_WAVES, "волн прошло меньше потолка"
    assert service.raid_of_user(players[0].user_id) is not None, (
        "рейд закрыли, хотя все живы"
    )


async def test_the_fatigue_keeps_growing_past_its_own_scale(bot, db):
    """Именно усталость и доводит рейд до конца, поэтому она не упирается.

    Раз счётчик волн убран, что-то должно гарантировать конец боя. Это
    усталость: она растёт и после своей шкалы, а с ней растёт урон — рано
    или поздно кто-то падает.
    """
    from bot.game.combat import fatigue_multiplier
    from bot.game.raid import FATIGUE_WAVES

    on_scale = fatigue_multiplier(FATIGUE_WAVES, limit=FATIGUE_WAVES)
    beyond = fatigue_multiplier(FATIGUE_WAVES * 2, limit=FATIGUE_WAVES)

    assert beyond > on_scale > 1.0
