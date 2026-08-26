"""Групповой сценарий целиком: /arena → /duel → принятие вызова → бой."""

from datetime import datetime, timezone

from aiogram.types import Chat, Message, User

from tests.harness import feed_callback, feed_message, ids

from bot.game.classes import get_class
from bot.keyboards import ChallengeCB, FightCB, fight_keyboard
from bot.models import Player

GROUP = Chat(id=-1002000, type="supergroup", title="Клуб")
THREAD_ID = 42
ZONES = ["head", "chest", "belly", "belt", "legs"]


def make_player(user_id: int, nickname: str, class_code: str) -> Player:
    stats = get_class(class_code).base_stats
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code=class_code,
        strength=stats.strength,
        agility=stats.agility,
        intuition=stats.intuition,
        endurance=stats.endurance,
    )


def group_message(user: User, text: str, thread_id: int | None = THREAD_ID, **kwargs):
    return Message(
        message_id=next(ids),
        date=datetime.now(timezone.utc),
        chat=GROUP,
        from_user=user,
        text=text,
        message_thread_id=thread_id,
        is_topic_message=thread_id is not None,
        **kwargs,
    )


async def send(user: User, text: str, thread_id: int | None = THREAD_ID) -> None:
    await feed_message(group_message(user, text, thread_id))


async def press(user: User, data: str) -> None:
    await feed_callback(
        user, GROUP, data, message_thread_id=THREAD_ID, is_topic_message=True
    )


def as_user(user_id: int, name: str) -> User:
    return User(id=user_id, is_bot=False, first_name=name)


async def test_duel_from_arena_setup_to_result(arena):
    db, duels, session = arena
    first = as_user(601, "Тайлер")
    second = as_user(602, "Марла")
    await db.save_player(make_player(first.id, "Тайлер", "warrior"))
    await db.save_player(make_player(second.id, "Марла", "assassin"))

    await send(first, "/arena Ринг")
    assert "ринг клуба" in session.texts[-1]
    assert (await db.get_arena(GROUP.id)).thread_id == THREAD_ID

    await send(first, "/duel")
    assert "вызывает любого желающего" in session.texts[-1]

    challenge_id = next(iter(duels._challenges))
    await press(second, ChallengeCB(action="accept", challenge_id=challenge_id).pack())

    duel = duels.duel_in_chat(GROUP.id, THREAD_ID)
    assert duel is not None
    duel_id = duel.id

    for _ in range(40):
        if duels.duel_in_chat(GROUP.id, THREAD_ID) is None:
            break
        for index, user in enumerate((first, second)):
            fighter = duel.fighters[user.id]
            await press(
                user,
                FightCB(action="attack", duel_id=duel_id, zone=ZONES[index]).pack(),
            )
            for offset in range(fighter.derived.block_zones):
                await press(
                    user,
                    FightCB(
                        action="block",
                        duel_id=duel_id,
                        zone=ZONES[(index + offset + 1) % len(ZONES)],
                    ).pack(),
                )
    assert duels.duel_in_chat(GROUP.id, THREAD_ID) is None
    assert "🏆" in session.texts[-1] or "Ничья" in session.texts[-1]

    winner = await db.get_player(first.id)
    loser = await db.get_player(second.id)
    assert winner.wins + loser.wins == 1
    assert len(await db.recent_duels(GROUP.id)) == 1


async def test_duel_outside_arena_thread_is_refused(arena):
    db, _, session = arena
    user = as_user(611, "Боб")
    await db.save_player(make_player(user.id, "Боб", "tank"))
    await db.set_arena(GROUP.id, THREAD_ID, "Ринг")

    await send(user, "/duel", thread_id=None)
    assert "Бои проходят" in session.texts[-1]


async def test_duel_without_character_sends_to_private(arena):
    _, _, session = arena
    await send(as_user(612, "Новичок"), "/duel")
    assert "нет бойца" in session.texts[-1]


async def test_reply_duel_targets_that_fighter(arena):
    db, duels, session = arena
    first = as_user(621, "Тайлер")
    second = as_user(622, "Марла")
    third = as_user(623, "Чужак")
    for user, code in ((first, "warrior"), (second, "rogue"), (third, "tank")):
        await db.save_player(make_player(user.id, user.first_name, code))

    reply_target = group_message(second, "я тут")
    await feed_message(group_message(first, "/duel", reply_to_message=reply_target))
    assert "вызывает <b>Марла</b>" in session.texts[-1]

    challenge_id = next(iter(duels._challenges))
    await press(third, ChallengeCB(action="accept", challenge_id=challenge_id).pack())
    assert duels.duel_in_chat(GROUP.id, THREAD_ID) is None  # чужак принять не может

    await press(second, ChallengeCB(action="accept", challenge_id=challenge_id).pack())
    assert duels.duel_in_chat(GROUP.id, THREAD_ID) is not None


async def test_no_surrender_button_and_no_giveup_command(arena):
    """Сдачи не существует: команда игнорируется, кнопки в клавиатуре нет."""
    db, duels, session = arena
    first = as_user(631, "Тайлер")
    second = as_user(632, "Марла")
    await db.save_player(make_player(first.id, "Тайлер", "warrior"))
    await db.save_player(make_player(second.id, "Марла", "rogue"))

    await send(first, "/duel")
    challenge_id = next(iter(duels._challenges))
    await press(second, ChallengeCB(action="accept", challenge_id=challenge_id).pack())
    duel = duels.duel_in_chat(GROUP.id, THREAD_ID)
    assert duel is not None

    buttons = [
        button.callback_data
        for row in fight_keyboard(duel.id).inline_keyboard
        for button in row
    ]
    assert all("giveup" not in (data or "") for data in buttons)

    await send(first, "/giveup")
    assert duels.duel_in_chat(GROUP.id, THREAD_ID) is duel  # бой продолжается
    assert (await db.get_player(second.id)).wins == 0


async def test_hurt_fighter_is_turned_away_from_the_ring(arena):
    """В ветке видно, сколько ждать до допуска."""
    db, duels, session = arena
    user = as_user(641, "Тайлер")
    player = make_player(user.id, "Тайлер", "warrior")
    player.set_hp(0)  # только что вынесли
    await db.save_player(player)

    await send(user, "/duel")
    text = session.texts[-1]
    assert "не в форме" in text
    assert "мин" in text  # обратный отсчёт на месте
    assert duels.duel_in_chat(GROUP.id, THREAD_ID) is None
    assert not duels.is_busy(user.id)
