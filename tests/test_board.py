"""Доска объявлений: кто позвал драться и куда об этом написать.

Бои и рейды заводят в мини-аппе, и в чате об этом не знает никто: боец
открыл вызов, а звать некому. Объявление в размеченной ветке — то, чем
клуб об этом узнаёт.
"""

import asyncio

import pytest

from bot.board_service import ARMED, FIST, RAID
from bot.config import Config
from bot.duel_service import DuelService
from bot.game.modes import FightMode
from bot.game.potions import RAID_PASS
from bot.raid_service import RaidService
from tests.test_duel_flow import FakeBot, make_player

CLUB = -100
FIST_THREAD = 11
ARMED_THREAD = 22
RAID_THREAD = 33


@pytest.fixture
def bot():
    return FakeBot()


def duels_of(bot, db) -> DuelService:
    return DuelService(bot=bot, db=db, config=Config(bot_token="test"))


def raids_of(bot, db) -> RaidService:
    return RaidService(
        bot=bot,
        db=db,
        config=Config(bot_token="test", raid_any_time=True, raid_lobby_timeout=60),
    )


async def mark_all(db) -> None:
    await db.set_announce_thread(CLUB, FIST, FIST_THREAD)
    await db.set_announce_thread(CLUB, ARMED, ARMED_THREAD)
    await db.set_announce_thread(CLUB, RAID, RAID_THREAD)


def board_posts(bot, thread_id: int) -> list[str]:
    return [m.text for m in bot.sent if m.thread_id == thread_id]


async def settle() -> None:
    """Дать закрепам сняться: они уходят отдельной задачей."""
    for _ in range(3):
        await asyncio.sleep(0)


# ---------- разметка веток ----------


async def test_without_a_marked_thread_nobody_is_told(bot, db):
    """Ветки нет — объявлять некуда, и это не ошибка, а ненастроенный клуб."""
    duels = duels_of(bot, db)
    challenger = make_player(1, "Тайлер")
    await db.save_player(challenger)

    await duels.open_challenge(None, None, challenger)

    assert bot.sent == [] and bot.pinned == []


async def test_a_marked_thread_takes_the_call_and_keeps_it_pinned(bot, db):
    """Вызов из мини-аппа приходит объявлением в свою ветку и висит закреплённым."""
    await mark_all(db)
    duels = duels_of(bot, db)
    challenger = make_player(1, "Тайлер")
    await db.save_player(challenger)

    await duels.open_challenge(None, None, challenger, mode=FightMode.FIST)

    posts = board_posts(bot, FIST_THREAD)
    assert len(posts) == 1
    assert "Тайлер" in posts[0] and "кулачный бой" in posts[0]
    # объявление закреплено — иначе его смоет разговором
    assert bot.pinned == [(CLUB, bot.sent[0].message_id)]
    # и попало только в свою ветку
    assert board_posts(bot, ARMED_THREAD) == []
    assert board_posts(bot, RAID_THREAD) == []


async def test_an_armed_call_goes_to_its_own_thread(bot, db):
    """У боя с оружием своя ветка: в кулачную он не попадает."""
    await mark_all(db)
    duels = duels_of(bot, db)
    challenger = make_player(1, "Тайлер")
    await db.save_player(challenger)

    await duels.open_challenge(None, None, challenger, mode=FightMode.ARMED)

    assert board_posts(bot, FIST_THREAD) == []
    assert len(board_posts(bot, ARMED_THREAD)) == 1


async def test_a_call_thrown_in_the_marked_thread_is_not_repeated(bot, db):
    """Вызов, брошенный прямо в этой ветке, уже стоит в ней сообщением."""
    await mark_all(db)
    duels = duels_of(bot, db)
    challenger = make_player(1, "Тайлер")
    await db.save_player(challenger)

    await duels.open_challenge(CLUB, FIST_THREAD, challenger)

    # ровно одно сообщение: сам вызов, без объявления следом
    assert len(board_posts(bot, FIST_THREAD)) == 1
    assert bot.pinned == []


@pytest.mark.parametrize(
    "close,tail",
    [
        ("accept", "Вызов принят"),
        ("withdraw", "Вызов снят"),
    ],
)
async def test_the_pin_comes_off_when_there_is_nobody_left_to_call(
    bot, db, close, tail
):
    """Вызов закрыт — закреп снимается, а объявление говорит, чем всё кончилось.

    Иначе шапка чата за вечер зарастает объявлениями о боях, которые
    давно отгремели.
    """
    await mark_all(db)
    duels = duels_of(bot, db)
    challenger = make_player(1, "Тайлер")
    rival = make_player(2, "Марла")
    for player in (challenger, rival):
        await db.save_player(player)

    challenge = await duels.open_challenge(None, None, challenger)
    pinned = bot.pinned[0]

    if close == "accept":
        await duels.accept_challenge(challenge.id, rival)
    else:
        await duels.withdraw_challenge(challenger.user_id)
    await settle()

    assert bot.unpinned == [pinned]
    assert tail in bot.edits[-1].text


async def test_a_raid_calls_the_club_down_to_the_cellar(bot, db):
    """Сбор отряда — объявлением в ветку рейдов, с закрепом."""
    await mark_all(db)
    raids = raids_of(bot, db)
    opener = make_player(1, "Тайлер")
    await db.save_player(opener)
    opener.potions[RAID_PASS] = await db.add_potion(opener.user_id, RAID_PASS)

    lobby = await raids.open_raid(None, None, opener)

    posts = board_posts(bot, RAID_THREAD)
    assert len(posts) == 1
    assert "Тайлер" in posts[0] and lobby.boss.raid_name in posts[0]
    assert bot.pinned == [(CLUB, bot.sent[0].message_id)]

    await raids.leave(lobby.id, opener.user_id)
    await settle()

    assert bot.unpinned == bot.pinned
    assert "Отряд не собрался" in bot.edits[-1].text


async def test_the_club_is_told_where_to_go(bot, db):
    """В объявлении есть ссылка: читают его те, кто сейчас не в приложении."""
    from bot.game.links import links

    links.configure(bot_username="vegasfightclub_bot", miniapp_name="card")
    try:
        await mark_all(db)
        duels = duels_of(bot, db)
        challenger = make_player(1, "Тайлер")
        await db.save_player(challenger)

        await duels.open_challenge(None, None, challenger)

        assert "startapp=ring" in board_posts(bot, FIST_THREAD)[0]
    finally:
        links.configure(bot_username="", miniapp_name="")


# ---------- окно рейда ----------


def moscow(hour: int, minute: int = 0, day: int = 8) -> int:
    from datetime import datetime

    from bot.game.raid import MOSCOW

    return int(datetime(2026, 9, day, hour, minute, tzinfo=MOSCOW).timestamp())


def scheduled(bot, db) -> RaidService:
    """Рейды по расписанию: именно его объявляет бот сам, без игроков."""
    return RaidService(
        bot=bot,
        db=db,
        config=Config(bot_token="test", raid_any_time=False, raid_lobby_timeout=60),
    )


async def test_the_open_window_is_announced_and_pinned(bot, db):
    """Подвал открылся — об этом узнаёт весь клуб, а не только гуляющие по карте."""
    from bot.game.raid import WINDOW_HOURS, slots_on

    await mark_all(db)
    raids = scheduled(bot, db)
    opens = slots_on(__import__("datetime").date(2026, 9, 8))[0]

    await raids._check_window(moscow(opens, 1))

    posts = board_posts(bot, RAID_THREAD)
    assert len(posts) == 1
    assert "подвал открыт" in posts[0].lower()
    # сколько он продлится — в самом объявлении: два часа
    assert f"до {opens + WINDOW_HOURS:02d}:00" in posts[0]
    assert bot.pinned == [(CLUB, bot.sent[0].message_id)]

    # пока окно то же, второй раз не объявляем
    await raids._check_window(moscow(opens, 30))
    assert len(board_posts(bot, RAID_THREAD)) == 1


async def test_a_restart_inside_the_window_does_not_announce_it_twice(bot, db):
    """Перезапуск посреди окна не зовёт в подвал во второй раз."""
    from bot.game.raid import slots_on

    await mark_all(db)
    opens = slots_on(__import__("datetime").date(2026, 9, 8))[0]
    await scheduled(bot, db)._check_window(moscow(opens, 1))
    assert len(board_posts(bot, RAID_THREAD)) == 1

    # новый сервис с той же базой — как после рестарта
    await scheduled(bot, db)._check_window(moscow(opens, 40))
    assert len(board_posts(bot, RAID_THREAD)) == 1


async def test_the_closed_window_lets_the_pin_go(bot, db):
    """Окно кончилось — закреп снимается: звать больше некуда."""
    from bot.game.raid import slots_on

    await mark_all(db)
    raids = scheduled(bot, db)
    opens = slots_on(__import__("datetime").date(2026, 9, 8))[0]

    await raids._check_window(moscow(opens, 1))
    await raids._check_window(moscow(opens + 3))  # окно уже закрылось

    assert bot.unpinned == bot.pinned
    # объявление переписано на итог: оно больше не зовёт
    assert "закрыто" in bot.edits[-1].text
