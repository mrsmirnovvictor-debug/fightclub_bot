"""Сквозной прогон дуэли через DuelService с поддельным ботом."""

import asyncio
import random
from dataclasses import dataclass, field

import pytest

from bot.config import Config
from bot.duel_service import DuelError, DuelService
from bot.game.classes import get_class
from bot.models import Player

CHAT_ID = -1001
THREAD_ID = 7


@dataclass
class SentMessage:
    message_id: int
    chat_id: int
    text: str
    thread_id: int | None = None
    reply_markup: object | None = None


@dataclass
class FakeBot:
    """Минимальная замена aiogram.Bot: копит отправленное и правки."""

    sent: list[SentMessage] = field(default_factory=list)
    edits: list[SentMessage] = field(default_factory=list)
    _next_id: int = 100

    async def send_message(self, chat_id, text, message_thread_id=None, **kwargs):
        self._next_id += 1
        message = SentMessage(
            message_id=self._next_id,
            chat_id=chat_id,
            text=text,
            thread_id=message_thread_id,
            reply_markup=kwargs.get("reply_markup"),
        )
        self.sent.append(message)
        return message

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        message = SentMessage(
            message_id=message_id,
            chat_id=chat_id,
            text=text,
            reply_markup=kwargs.get("reply_markup"),
        )
        self.edits.append(message)
        return message

    @property
    def texts(self) -> list[str]:
        return [m.text for m in self.sent]


def make_player(user_id: int, nickname: str, class_code: str = "warrior") -> Player:
    fclass = get_class(class_code)
    stats = fclass.base_stats
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code=class_code,
        strength=stats.strength,
        agility=stats.agility,
        intuition=stats.intuition,
        endurance=stats.endurance,
    )


@pytest.fixture
def bot():
    return FakeBot()


def make_service(bot, db, turn_timeout: int = 600) -> DuelService:
    config = Config(
        bot_token="test", db_path=":memory:", turn_timeout=turn_timeout, challenge_timeout=600
    )
    return DuelService(bot=bot, db=db, config=config, rng=random.Random(2024))


async def play_round(service: DuelService, session) -> None:
    """Обе стороны честно выбирают удар и блоки."""
    zones = ["head", "chest", "belly", "belt", "legs"]
    for index, user_id in enumerate(session.order):
        fighter = session.fighters[user_id]
        await service.handle_choice(session.id, user_id, "attack", zones[index])
        for offset in range(fighter.derived.block_zones):
            await service.handle_choice(
                session.id, user_id, "block", zones[(index + offset + 1) % len(zones)]
            )


async def test_full_duel_from_challenge_to_result(bot, db):
    service = make_service(bot, db)
    first = make_player(1, "Тайлер", "warrior")
    second = make_player(2, "Марла", "rogue")
    await db.save_player(first)
    await db.save_player(second)

    challenge = await service.open_challenge(CHAT_ID, THREAD_ID, first)
    assert "вызывает любого желающего" in bot.texts[0]
    assert bot.sent[0].thread_id == THREAD_ID

    session = await service.accept_challenge(challenge.id, second)
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is session
    assert service.is_busy(1) and service.is_busy(2)
    assert "Дуэль на кулаках" in bot.texts[1]

    for _ in range(40):
        if service.duel_in_chat(CHAT_ID, THREAD_ID) is None:
            break
        await play_round(service, session)
    else:  # pragma: no cover
        pytest.fail("бой не закончился за 40 раундов")

    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert not service.is_busy(1) and not service.is_busy(2)

    final = bot.texts[-1]
    assert "🏆" in final or "Ничья" in final
    assert any("Раунд 1" in text for text in bot.texts)

    winner = await db.get_player(1)
    loser = await db.get_player(2)
    assert {winner.wins, loser.wins} == {0, 1}
    assert winner.wins + loser.wins == 1
    assert winner.exp + loser.exp > 0

    history = await db.recent_duels(CHAT_ID)
    assert len(history) == 1
    assert history[0]["winner_id"] in {1, 2}
    assert history[0]["rounds"] >= 1


async def test_timeout_makes_the_judge_choose(bot, db):
    """Если бойцы молчат, судья доигрывает бой за них."""
    service = make_service(bot, db, turn_timeout=0)
    await db.save_player(make_player(1, "Тайлер", "assassin"))
    await db.save_player(make_player(2, "Марла", "tank"))

    await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    for _ in range(400):
        if service.duel_in_chat(CHAT_ID, THREAD_ID) is None:
            break
        await asyncio.sleep(0)
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert any("судья засчитывает" in text or "команды не поступило" in text for text in bot.texts)


async def test_choice_is_private_and_toggleable(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    hint = await service.handle_choice(session.id, 1, "attack", "head")
    assert "голова" in hint
    await service.handle_choice(session.id, 1, "block", "chest")
    hint = await service.handle_choice(session.id, 1, "block", "chest")  # снимаем
    assert "🛡 —" in hint

    # третья зона вытесняет первую: воин закрывает только две
    await service.handle_choice(session.id, 1, "block", "chest")
    await service.handle_choice(session.id, 1, "block", "belly")
    hint = await service.handle_choice(session.id, 1, "block", "legs")
    assert "живот, ноги" in hint
    assert session.choice_of(1).is_ready(2)


async def test_outsider_cannot_press_buttons(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    with pytest.raises(DuelError):
        await service.handle_choice(session.id, 999, "attack", "head")


async def test_give_up_awards_win_to_opponent(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    await service.give_up(session.id, 1)

    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert "полотенце" in bot.texts[-1]
    assert (await db.get_player(2)).wins == 1
    assert (await db.get_player(1)).losses == 1


async def test_one_ring_one_pair(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    await db.save_player(make_player(3, "Боб"))
    await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    with pytest.raises(DuelError):
        await service.open_challenge(CHAT_ID, THREAD_ID, await db.get_player(3))


async def test_cannot_accept_own_challenge(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    challenge = await service.open_challenge(
        CHAT_ID, THREAD_ID, await db.get_player(1)
    )
    with pytest.raises(DuelError):
        await service.accept_challenge(challenge.id, await db.get_player(1))
    await service.shutdown()


async def test_targeted_challenge_rejects_strangers(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    await db.save_player(make_player(3, "Боб"))
    challenge = await service.open_challenge(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    with pytest.raises(DuelError):
        await service.accept_challenge(challenge.id, await db.get_player(3))
    session = await service.accept_challenge(challenge.id, await db.get_player(2))
    assert set(session.fighters) == {1, 2}


async def test_challenge_can_be_cancelled(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    challenge = await service.open_challenge(CHAT_ID, THREAD_ID, await db.get_player(1))
    with pytest.raises(DuelError):
        await service.cancel_challenge(challenge.id, 2)
    await service.cancel_challenge(challenge.id, 1)
    assert not service.is_busy(1)
    assert "передумал" in bot.edits[-1].text


async def test_accepting_while_having_own_challenge_withdraws_it(bot, db):
    """Двое кинули вызов одновременно — один принимает, свой вызов снимается."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))

    first = await service.open_challenge(CHAT_ID, THREAD_ID, await db.get_player(1))
    second = await service.open_challenge(CHAT_ID, None, await db.get_player(2))

    session = await service.accept_challenge(first.id, await db.get_player(2))
    assert set(session.fighters) == {1, 2}
    assert any("снят" in edit.text for edit in bot.edits)
    with pytest.raises(DuelError):
        await service.accept_challenge(second.id, await db.get_player(1))
