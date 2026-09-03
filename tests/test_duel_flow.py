"""Сквозной прогон дуэли через DuelService с поддельным ботом."""

import asyncio
import random
from dataclasses import replace
from dataclasses import dataclass, field

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from bot.config import Config
from bot.duel_service import DuelError, DuelService
from bot.game.combat import MAX_MISSED_TURNS
from bot.game.classes import get_class
from bot.game.classes import Zone
from bot.game.combat import Fighter
from bot.game.equipment import Slot
from bot.game.health import FULL_REGEN_SECONDS, now_ts
from bot.game.modes import FightMode
from bot.game.economy import (
    LEVEL_CREDITS,
    MICRO_UPS_PER_LEVEL,
    RATING_START,
    UP_CREDITS,
    exp_to_next_level,
    win_exp,
)
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
    # Всё сказанное подряд: итог раунда приходит правкой панели, а не новым
    # сообщением, и по одному списку sent его уже не видно
    said: list[SentMessage] = field(default_factory=list)
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
        self.said.append(message)
        return message

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        message = SentMessage(
            message_id=message_id,
            chat_id=chat_id,
            text=text,
            reply_markup=kwargs.get("reply_markup"),
        )
        self.edits.append(message)
        self.said.append(message)
        return message

    @property
    def texts(self) -> list[str]:
        return [m.text for m in self.sent]

    @property
    def log(self) -> list[str]:
        """Лог боя целиком — и новые сообщения, и правки, по порядку."""
        return [m.text for m in self.said]


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


def make_service(bot, db, turn_timeout: int = 600, round_break: int = 0) -> DuelService:
    """Бой без перерывов между раундами: тесты гоняют ходы подряд.

    Перерыв — это ожидание по часам, а в тестах часов нет. Отдых проверяют
    отдельные тесты, которые заводят его явно.
    """
    config = Config(
        bot_token="test",
        db_path=":memory:",
        turn_timeout=turn_timeout,
        challenge_timeout=600,
        round_break=round_break,
    )
    return DuelService(bot=bot, db=db, config=config, rng=random.Random(2024))


ZONES = ["head", "chest", "belly", "belt", "legs"]


async def choose(service: DuelService, session, user_id: int, index: int = 0) -> None:
    """Полный выбор бойца: один удар и один блок."""
    await service.handle_choice(
        session.id, user_id, "attack", ZONES[index % len(ZONES)]
    )
    await service.handle_choice(
        session.id, user_id, "block", ZONES[(index + 1) % len(ZONES)]
    )


async def play_round(service: DuelService, session) -> None:
    """Обе стороны честно выбирают удар и блок."""
    for index, user_id in enumerate(session.order):
        await choose(service, session, user_id, index)


async def force_round(service: DuelService, session) -> None:
    """Досчитать раунд принудительно — ровно это делает таймер, когда время вышло."""
    await service._resolve(session)


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
    assert not session.started
    assert "Вызов принят" in bot.texts[-1]

    # без подтверждения вызвавшего бой не начинается
    with pytest.raises(DuelError):
        await service.handle_choice(session.id, 1, "attack", "head")
    await service.confirm_duel(session.id, first.user_id)
    assert session.started
    assert any("Кулачный бой" in text for text in bot.texts)

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


async def test_silence_on_both_sides_ends_in_technical_draw(bot, db):
    """Молчат оба — три пропуска подряд, и судья закрывает бой ничьёй."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "assassin"))
    await db.save_player(make_player(2, "Марла", "tank"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    for _ in range(MAX_MISSED_TURNS):
        await force_round(service, session)

    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert session.round_number == MAX_MISSED_TURNS
    assert any("пропуск хода" in text for text in bot.log)
    assert "перестали отвечать" in bot.texts[-1]
    # никто никого не бил — здоровье целое
    assert all(f.hp == f.max_hp for f in session.fighters.values())
    assert (await db.get_player(1)).draws == 1
    assert (await db.get_player(2)).draws == 1


async def test_silent_fighter_loses_by_technical_decision(bot, db):
    """Один бьёт, второй молчит — техпоражение молчуну."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    for _ in range(MAX_MISSED_TURNS):
        await service.handle_choice(session.id, 1, "attack", "head")
        await service.handle_choice(session.id, 1, "block", "chest")
        await service.handle_choice(session.id, 1, "block", "belly")
        await force_round(service, session)

    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert session.fighters[1].missed_turns == 0
    assert session.fighters[2].missed_turns == MAX_MISSED_TURNS
    assert "Техническая победа" in bot.texts[-1]
    assert (await db.get_player(1)).wins == 1
    assert (await db.get_player(2)).losses == 1


async def test_missed_turn_counter_resets_after_any_press(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    # соперник каждый раунд что-то нажимает, молчит только первый боец
    for _ in range(2):
        await service.handle_choice(session.id, 2, "block", "head")
        await force_round(service, session)
    assert session.fighters[1].missed_turns == 2
    assert session.fighters[2].missed_turns == 0
    assert "пропусков подряд 2" in bot.texts[-1]  # предупреждение под табло

    await service.handle_choice(session.id, 1, "block", "head")
    await service.handle_choice(session.id, 2, "block", "head")
    await force_round(service, session)
    assert session.fighters[1].missed_turns == 0
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is not None


async def test_partial_choice_goes_into_the_round_as_is(bot, db):
    """Выбрал только блоки — не бьёшь, но и пропуска хода нет."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    opponent_hp = session.fighters[2].hp

    await service.handle_choice(session.id, 1, "block", "head")
    await force_round(service, session)

    assert session.fighters[1].missed_turns == 0
    assert session.fighters[2].hp == opponent_hp  # удара не было
    assert any(
        "бить не стал" in text or "глухую оборону" in text or "только защищается" in text
        for text in bot.log
    )


async def test_surrender_is_gone(bot, db):
    """Сдаться больше нельзя: ни кнопки, ни метода."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    assert not hasattr(service, "give_up")
    with pytest.raises(DuelError):
        await service.handle_choice(session.id, 1, "giveup", "")
    with pytest.raises(DuelError):  # мусорная зона из старой кнопки
        await service.handle_choice(session.id, 1, "attack", "nose")
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is session


async def test_choice_is_private_and_toggleable(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    hint = await service.handle_choice(session.id, 1, "attack", "head")
    assert "голова" in hint
    assert "Осталось выбрать блок" in hint

    hint = await service.handle_choice(session.id, 1, "block", "chest")
    assert "Корпус + живот" in hint  # блок закрывает смежные зоны
    assert session.is_ready(1)

    # новый блок заменяет прежний целиком
    hint = await service.handle_choice(session.id, 1, "block", "legs")
    assert "Ноги + голова" in hint
    assert session.choice_of(1).block == (Zone.LEGS, Zone.HEAD)


async def test_outsider_cannot_press_buttons(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    with pytest.raises(DuelError):
        await service.handle_choice(session.id, 999, "attack", "head")


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


# ---------- экономика ----------


async def heal_everyone(db, *user_ids: int) -> None:
    """Отмотать восстановление: как будто прошло десять минут."""
    for user_id in user_ids:
        player = await db.get_player(user_id)
        player.heal_full()
        await db.save_player(player)


async def fight_to_the_end(service: DuelService, session) -> None:
    for _ in range(40):
        if service.duel_in_chat(session.chat_id, session.thread_id) is None:
            return
        await play_round(service, session)
    raise AssertionError("бой не закончился за 40 раундов")


def earned_credits(player) -> int:
    """Сколько кредитов боец должен был получить за апы и уровни — и только."""
    ups = (player.level - 1) * MICRO_UPS_PER_LEVEL + player.micro_ups
    return ups * UP_CREDITS + (player.level - 1) * LEVEL_CREDITS


async def test_only_the_winner_gets_exp_and_the_ring_pays_no_credits(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "rogue"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    await fight_to_the_end(service, session)

    first, second = await db.get_player(1), await db.get_player(2)
    winner, loser = (first, second) if first.wins else (second, first)

    assert winner.total_exp > 0
    # за сам бой денег нет: кредиты только те, что дали апы и уровни
    assert winner.credits == earned_credits(winner)
    assert winner.rating > RATING_START
    # проигравшему — ни опыта, ни кредитов, только минус в рейтинге
    assert loser.total_exp == 0
    assert loser.credits == 0
    assert loser.rating < RATING_START
    assert winner.rating - RATING_START == RATING_START - loser.rating


async def test_exp_depends_on_damage_dealt(bot, db):
    """Крепкий соперник приносит больше опыта, чем хлипкий."""
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    fighter = Fighter.from_player(await db.get_player(1))
    fighter.damage_dealt = 40
    weak = win_exp(fighter.damage_dealt, 1, 1)
    fighter.damage_dealt = 120
    strong = win_exp(fighter.damage_dealt, 1, 1)
    assert strong > weak

    # и уровень соперника тоже влияет
    assert win_exp(60, 1, 5) > win_exp(60, 1, 1) > win_exp(60, 5, 1)


async def test_a_draw_leaves_the_rating_where_it_was(bot, db):
    """Ничья не двигает рейтинг: никто не уступил — наказывать не за что.

    Раньше ничья шла поражением обоим, и двое равных бойцов уходили с ринга
    беднее, чем пришли.
    """
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    for _ in range(MAX_MISSED_TURNS):  # оба молчат — техническая ничья
        await force_round(service, session)

    for user_id in (1, 2):
        player = await db.get_player(user_id)
        assert player.draws == 1
        assert player.total_exp == 0
        assert player.credits == 0
        assert player.rating == RATING_START

    rewards = next(text for text in bot.log if "Итоги" in text)
    assert "без изменений" in rewards and "(−" not in rewards


async def test_repeat_fights_pay_less(bot, db):
    """Второй бой той же пары за сутки приносит половину, третий — пятую часть."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))

    gains = []
    for _ in range(3):
        await heal_everyone(db, 1, 2)  # между боями бойцы успевают отлежаться
        before = await db.get_player(1)
        session = await service.start_duel(
            CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
        )
        # первый боец бьёт, второй молчит: исход предсказуем
        for _ in range(MAX_MISSED_TURNS):
            await service.handle_choice(session.id, 1, "attack", "head")
            await service.handle_choice(session.id, 1, "block", "chest")
            await service.handle_choice(session.id, 1, "block", "belly")
            await force_round(service, session)
        after = await db.get_player(1)
        gains.append(after.total_exp - before.total_exp)

    assert (await db.get_player(1)).wins == 3
    assert gains[0] > gains[1] > gains[2]
    assert gains[1] == pytest.approx(gains[0] * 0.5, abs=2)
    assert gains[2] == pytest.approx(gains[0] * 0.2, abs=2)


async def test_rewards_block_is_shown_in_the_thread(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "rogue"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    await fight_to_the_end(service, session)

    final = bot.texts[-1]
    assert "📊" in final
    assert "опыта" in final and "рейтинг" in final
    assert "получено 0 опыта" in final  # строка проигравшего
    # Урон — первым: по нему судья и решает бой, дошедший до последнего гонга
    assert "Нанесено урона" in final
    assert "всего" not in final  # кошелёк целиком в итог боя не пишем


async def test_rating_transfer_stays_symmetric_when_the_winner_levels_up(bot, db):
    """Уровень, взятый в этом же бою, не должен менять цену рейтинга."""
    service = make_service(bot, db)
    almost = make_player(1, "Тайлер", "warrior")
    almost.exp = exp_to_next_level(1) - 1  # следующая победа даст уровень
    await db.save_player(almost)
    await db.save_player(make_player(2, "Марла", "warrior"))

    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    for _ in range(MAX_MISSED_TURNS):
        await service.handle_choice(session.id, 1, "attack", "head")
        await service.handle_choice(session.id, 1, "block", "chest")
        await service.handle_choice(session.id, 1, "block", "belly")
        await force_round(service, session)

    winner, loser = await db.get_player(1), await db.get_player(2)
    assert winner.level == 2  # уровень действительно взят
    assert winner.rating - RATING_START == RATING_START - loser.rating


# ---------- здоровье между боями ----------


async def test_hp_carries_over_to_the_next_fight(bot, db):
    """После боя здоровье сохраняется, а не откатывается к полному."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "rogue"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    await fight_to_the_end(service, session)

    for user_id in (1, 2):
        player = await db.get_player(user_id)
        assert player.hp == session.fighters[user_id].hp
        assert player.hp_at > 0
    # проигравший лежит на нуле
    loser = next(f for f in session.fighters.values() if f.hp == 0)
    assert (await db.get_player(loser.user_id)).hp == 0


async def test_beaten_fighter_cannot_start_a_new_duel(bot, db):
    service = make_service(bot, db)
    beaten = make_player(1, "Тайлер", "warrior")
    beaten.set_hp(1)  # только что получил по лицу
    await db.save_player(beaten)
    await db.save_player(make_player(2, "Марла", "rogue"))

    with pytest.raises(DuelError) as error:
        await service.open_challenge(CHAT_ID, THREAD_ID, await db.get_player(1))
    assert "не в форме" in str(error.value)
    assert "🔴" in str(error.value)
    assert not service.is_busy(1)


async def test_beaten_fighter_cannot_be_challenged_or_accept(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    beaten = make_player(2, "Марла", "rogue")
    beaten.set_hp(int(beaten.max_hp * 0.5))  # жёлтая зона
    await db.save_player(beaten)

    # адресный вызов отклоняется сразу
    with pytest.raises(DuelError) as error:
        await service.open_challenge(
            CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
        )
    assert "🟡" in str(error.value)

    # и открытый вызов принять тоже нельзя
    challenge = await service.open_challenge(
        CHAT_ID, THREAD_ID, await db.get_player(1)
    )
    with pytest.raises(DuelError):
        await service.accept_challenge(challenge.id, await db.get_player(2))
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None


async def test_scratched_fighter_enters_the_ring_with_what_is_left(bot, db):
    """Зелёная зона пускает в бой, но здоровье в бою — неполное."""
    service = make_service(bot, db)
    scratched = make_player(1, "Тайлер", "warrior")
    partial = int(scratched.max_hp * 0.85)
    scratched.set_hp(partial)
    await db.save_player(scratched)
    await db.save_player(make_player(2, "Марла", "rogue"))

    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    assert session.fighters[1].hp == partial
    assert session.fighters[1].hp < session.fighters[1].max_hp
    assert session.fighters[2].hp == session.fighters[2].max_hp
    assert any("не долечился" in text for text in bot.texts)  # интро предупреждает


async def test_healed_fighter_is_allowed_again(bot, db):
    service = make_service(bot, db)
    beaten = make_player(1, "Тайлер", "warrior")
    beaten.set_hp(0, now=now_ts() - FULL_REGEN_SECONDS)  # отлежался десять минут
    await db.save_player(beaten)
    await db.save_player(make_player(2, "Марла", "rogue"))

    player = await db.get_player(1)
    assert player.current_hp() == player.max_hp
    challenge = await service.open_challenge(CHAT_ID, THREAD_ID, player)
    session = await service.accept_challenge(challenge.id, await db.get_player(2))
    await service.confirm_duel(session.id, 1)
    assert session.fighters[1].hp == session.fighters[1].max_hp


# ---------- стойка перед боем ----------


async def test_challenger_can_walk_away_after_seeing_the_opponent(bot, db):
    """Посмотрел на соперника, испугался — разошлись без боя."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "assassin"))

    challenge = await service.open_challenge(
        CHAT_ID, THREAD_ID, await db.get_player(1)
    )
    session = await service.accept_challenge(challenge.id, await db.get_player(2))
    assert not session.started
    # карточка показывает, с кем предстоит драться
    card = bot.texts[-1]
    assert "Марла" in card and "Ассасин" in card and "рейтинг" in card

    await service.decline_duel(session.id, 1)
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert not service.is_busy(1) and not service.is_busy(2)
    assert "отказывается от боя" in bot.edits[-1].text
    # несостоявшийся бой не пишется в статистику
    assert (await db.get_player(1)).fights == 0


async def test_opponent_may_also_back_out(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    challenge = await service.open_challenge(
        CHAT_ID, THREAD_ID, await db.get_player(1)
    )
    session = await service.accept_challenge(challenge.id, await db.get_player(2))
    await service.decline_duel(session.id, 2)
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None


async def test_only_the_challenger_gives_the_gong(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    challenge = await service.open_challenge(
        CHAT_ID, THREAD_ID, await db.get_player(1)
    )
    session = await service.accept_challenge(challenge.id, await db.get_player(2))

    with pytest.raises(DuelError):
        await service.confirm_duel(session.id, 2)
    with pytest.raises(DuelError):
        await service.confirm_duel(session.id, 999)
    assert not session.started

    await service.confirm_duel(session.id, 1)
    assert session.started
    with pytest.raises(DuelError):  # второй раз гонга не будет
        await service.confirm_duel(session.id, 1)


async def test_standoff_frees_the_ring_when_nobody_decides(bot, db):
    service = make_service(bot, db)
    service.config = replace(service.config, challenge_timeout=0)
    await db.save_player(make_player(1, "Тайлер"))
    await db.save_player(make_player(2, "Марла"))
    session = await service.open_standoff(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    for _ in range(200):
        if service.duel_in_chat(CHAT_ID, THREAD_ID) is None:
            break
        await asyncio.sleep(0)
    assert not session.started
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert "не состоялся" in bot.edits[-1].text


# ---------- как это читается в ветке ----------


async def test_round_report_speaks_the_new_language(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Victor", "warrior"))
    await db.save_player(make_player(2, "Евгений The One", "warrior"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    for _ in range(6):
        if service.duel_in_chat(CHAT_ID, THREAD_ID) is None:
            break
        await play_round(service, session)

    rounds = [text for text in bot.log if text.startswith("<b>⚔️ Раунд")]
    assert rounds
    body = "\n".join(rounds)
    assert "кулаком" in body  # без оружия бьют кулаком
    assert any(mark in body for mark in ("👊", "🛡", "🤸", "🥊", "🩸"))
    # полоски здоровья остались в панели, в отчёте только цифры
    assert "▰" not in body
    assert "[" in body and "]" in body


# ---------- лимиты Telegram ----------


class FloodyBot(FakeBot):
    """Telegram, который упирается в флуд-контроль.

    Правки он отвергает всегда, а первую отправку каждого сообщения —
    один раз. Ровно на этом бой раньше вставал намертво.
    """

    def __init__(self) -> None:
        super().__init__()
        self.refused_sends = 0
        self.refused_edits = 0

    def _flood(self):
        return TelegramRetryAfter(
            method=SendMessage(chat_id=1, text="x"),
            message="Flood control exceeded",
            retry_after=0,
        )

    async def send_message(self, chat_id, text, message_thread_id=None, **kwargs):
        if self.refused_sends < 1:
            self.refused_sends += 1
            raise self._flood()
        return await super().send_message(chat_id, text, message_thread_id, **kwargs)

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self.refused_edits += 1
        raise self._flood()


async def test_flood_control_does_not_freeze_the_duel(db):
    """Раунд должен доигрываться, даже когда Telegram просит подождать."""
    bot = FloodyBot()
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))

    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    assert bot.refused_sends == 1  # первую отправку Telegram отверг

    for _ in range(40):
        if service.duel_in_chat(CHAT_ID, THREAD_ID) is None:
            break
        await play_round(service, session)

    assert bot.refused_edits > 0  # правки действительно отвергались
    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None  # но бой дошёл до конца
    assert any("🏆" in text or "Ничья" in text for text in bot.texts)
    winner = await db.get_player(1)
    loser = await db.get_player(2)
    assert winner.fights == 1 and loser.fights == 1


async def test_panel_is_one_for_both_fighters(bot, db):
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "tank"))
    await db.save_player(make_player(2, "Марла", "rogue"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    assert session.panel == "👊"  # кулаки у обоих
    # панель одна: за раунд ушло приглашение и ничего больше
    prompts = [m for m in bot.sent if m.reply_markup is not None]
    assert len(prompts) == 1
    assert session.prompt_message_id == prompts[0].message_id


async def test_every_name_in_the_fight_log_opens_the_card(bot, db):
    """Имя бойца кликабельно везде: вызов, стойка, панель раунда, удары, итог."""
    import re

    from bot.game.links import links

    links.configure("vegasfightclub_bot", "card")
    try:
        service = make_service(bot, db)
        await db.save_player(make_player(1, "Тайлер", "warrior"))
        await db.save_player(make_player(2, "Марла", "rogue"))

        await service.open_challenge(CHAT_ID, THREAD_ID, await db.get_player(1))
        session = await service.start_duel(
            CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
        )
        await fight_to_the_end(service, session)

        links_by_id = {
            user_id: f'href="https://t.me/vegasfightclub_bot/card?startapp={user_id}"'
            for user_id in (1, 2)
        }
        named = [
            text
            for text in bot.texts
            if "Тайлер" in text or "Марла" in text
        ]
        assert named, "в логе боя вообще нет имён"
        # подсказка про свободные очки ведёт в ту же карточку, но имени в ней
        # нет — иначе счёт ссылок и имён не сойдётся
        hint = re.compile(r"<a href=\"[^\"]+\">карточке бойца</a>")
        # табло раунда идёт моноширинным блоком: ссылке внутри такого блока
        # Telegram жить не даёт, поэтому имена там намеренно без ссылок
        board = re.compile(r"<pre>.*?</pre>", re.S)
        for text in named:
            bare = hint.sub("", board.sub("", text))
            for user_id, name in ((1, "Тайлер"), (2, "Марла")):
                # каждое упоминание имени должно быть завёрнуто в ссылку
                assert bare.count(name) == bare.count(links_by_id[user_id]), (
                    f"имя {name} где-то без ссылки на карточку: {text[:160]}"
                )
    finally:
        links.configure("", "")


# ---------- бой с оружием доходит до ринга боем с оружием ----------


async def armed_pair(db):
    """Двое с оружием в руках, готовые к /fight."""
    from bot.game.classes import Stats
    from bot.inventory_service import buy, equip

    players = []
    for user_id, nickname, code in ((1, "Тайлер", "pipe"), (2, "Марла", "switchblade")):
        player = make_player(user_id, nickname)
        player.level = 6
        player.credits = 800
        player.apply_stats(Stats(strength=16, agility=14, intuition=12, endurance=14))
        await db.save_player(player)
        owned = await buy(db, player, code)
        await equip(db, player, owned.id)
        players.append(await db.get_player(user_id))
    return players


async def test_an_armed_challenge_stays_armed_all_the_way_to_the_gong(bot, db):
    """Вызов, стойка и выход на ринг — везде бой с оружием.

    Карточка стойки перерисовывается трижды, и режим у неё в умолчании
    кулачный: стоило забыть его на подтверждении, и бойцы выходили на ринг
    «без вещей» — с уроном без оружия и подписью про раздевалку, хотя дрались
    как раз надетым.
    """
    first, second = await armed_pair(db)

    challenge = await service_armed(bot, db, first, second)
    service, session = challenge
    await service.confirm_duel(session.id, first.user_id)

    call = bot.texts[0]
    assert "на бой с оружием" in call and "кулачный" not in call

    card = bot.edits[-1].text
    assert "Вызов принят. Бой с оружием." in card

    intro = next(text for text in bot.texts if "Бойцовский клуб." in text)
    assert "Бой с оружием" in intro
    assert "Дерутся тем, что надето." in intro
    assert "раздевалке" not in intro

    # и оружие никуда не делось: оно на бойце, а не в рюкзаке
    weapon = (await db.get_player(1)).gear_in_slot(Slot.WEAPON)
    assert weapon is not None and weapon.code == "pipe"


async def service_armed(bot, db, first, second):
    service = make_service(bot, db)
    challenge = await service.open_challenge(
        CHAT_ID, THREAD_ID, first, second, "Клуб", FightMode.ARMED
    )
    session = await service.accept_challenge(challenge.id, second)
    return service, session


async def test_walking_away_from_an_armed_fight_says_so_too(bot, db):
    """Отказ и просроченная стойка тоже не переобувают бойцов в кулаки."""
    first, second = await armed_pair(db)
    service, session = await service_armed(bot, db, first, second)

    await service.decline_duel(session.id, first.user_id)

    card = bot.edits[-1].text
    assert "Вызов принят. Бой с оружием." in card
    assert "отказывается от боя" in card


async def test_the_ring_sends_the_winner_to_the_card_and_not_to_a_command(bot, db):
    """В ветке боя команды не работают — за очками зовём в карточку.

    Раньше строка кончалась на «— /upgrade», и люди пробовали набрать это
    прямо на ринге: команды бота слушает личка, а не группа.
    """
    from bot.game.links import links
    from bot.game.narrator import upgrade_hint

    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "rogue"))

    was = (links.bot_username, links.miniapp_name, links.main_app)
    links.configure("vegasfightclub_bot", "card")
    try:
        session = await service.start_duel(CHAT_ID, THREAD_ID, *[
            await db.get_player(uid) for uid in (1, 2)
        ])
        await fight_to_the_end(service, session)

        rewards = next(text for text in bot.texts if "Итоги" in text)
        assert "получает" in rewards or "уровень" in rewards
        assert "/upgrade" not in rewards, "в ветке боя это не наберёшь"
        assert "карточке бойца" in rewards

        # а без настроенного мини-аппа честно зовём в личку
        links.configure("", "", False)
        assert "/upgrade" in upgrade_hint(await db.get_player(1))
    finally:
        links.configure(*was)


# ---------- боксёрские раунды и перерывы ----------


async def three_turns(service, session) -> None:
    """Отбоксировать раунд целиком — три хода."""
    from bot.game.combat import TURNS_PER_ROUND

    for _ in range(TURNS_PER_ROUND):
        await play_round(service, session)


async def test_after_three_turns_the_judge_sends_them_to_the_corners(bot, db):
    """Раунд — три удара, дальше гонг и минута отдыха."""
    from bot.game.combat import MATCH_ROUNDS

    service = make_service(bot, db, round_break=60)
    await db.save_player(make_player(1, "Тайлер", "tank"))
    await db.save_player(make_player(2, "Марла", "tank"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    await three_turns(service, session)

    gong = bot.log[-1]
    assert f"Раунд 1 из {MATCH_ROUNDS} окончен" in gong
    assert "Отдых минута" in gong and "на раунд 2" in gong
    # следующий удар не начался: бойцы в углах
    assert session.round_number == 3
    assert session.prompt_message_id is None
    await service.shutdown()


async def test_the_next_round_starts_when_the_rest_is_over(bot, db):
    """Перерыв кончился — судья зовёт на новый раунд сам."""
    service = make_service(bot, db, round_break=60)
    await db.save_player(make_player(1, "Тайлер", "tank"))
    await db.save_player(make_player(2, "Марла", "tank"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    await three_turns(service, session)

    # прогоняем отдых, не дожидаясь настоящей минуты
    session.timer.cancel()
    await service._start_round(session)

    assert session.round_number == 4
    assert "Раунд 2, удар 1 из 3" in bot.log[-1]
    assert session.prompt_message_id is not None
    await service.shutdown()


async def test_a_knockout_in_the_third_turn_skips_the_break(bot, db):
    """Бой кончился — по углам никого не разводят."""
    service = make_service(bot, db, round_break=60)
    await db.save_player(make_player(1, "Тайлер", "assassin"))
    await db.save_player(make_player(2, "Марла", "assassin"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    session.fighters[2].hp = 1

    await play_round(service, session)

    assert service.duel_in_chat(CHAT_ID, THREAD_ID) is None
    assert not any("окончен" in text for text in bot.log)
    await service.shutdown()


async def test_a_fight_that_goes_the_distance_is_decided_by_damage(bot, db):
    """Шесть раундов без нокаута — побеждает тот, кто больше нанёс."""
    from bot.game.combat import MAX_TURNS

    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "tank"))
    await db.save_player(make_player(2, "Марла", "tank"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    # первый бьёт, второй только закрывается: нокаута не выйдет, урон будет
    session.fighters[1].hp = session.fighters[2].hp = 5000
    for _ in range(MAX_TURNS):
        if service.duel_in_chat(CHAT_ID, THREAD_ID) is None:
            break
        # бьём в живот, а закрывается соперник по голове: удар доходит
        await service.handle_choice(session.id, 1, "attack", "belly")
        await service.handle_choice(session.id, 1, "block", "legs")
        await service.handle_choice(session.id, 2, "block", "head")
        await service._resolve(session)

    assert session.round_number == MAX_TURNS
    verdict = next(text for text in bot.log if "Финальный гонг" in text)
    assert "6 раундов позади" in verdict
    assert "По нанесённому урону побеждает" in verdict
    assert session.fighters[1].damage_dealt > session.fighters[2].damage_dealt
    assert (await db.get_player(1)).wins == 1
    await service.shutdown()


async def test_two_boxing_rounds_fit_one_minute_of_chat_budget(bot, db):
    """Ради этого перерыв и придуман: за минуту в лимит влезают два раунда.

    Отдых по умолчанию — полминуты, значит за минуту чат успевает увидеть
    два раунда. Если раунд станет дороже или отдых короче, здесь и вылезет.
    """
    from bot.config import Config
    from bot.messaging import CHAT_WRITES_PER_MINUTE

    service = make_service(bot, db, round_break=Config.round_break)
    await db.save_player(make_player(1, "Тайлер", "tank"))
    await db.save_player(make_player(2, "Марла", "tank"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )

    await three_turns(service, session)
    session.timer.cancel()
    await service._start_round(session)
    await three_turns(service, session)

    assert len(bot.said) <= CHAT_WRITES_PER_MINUTE
    assert service.voice.budget.left(CHAT_ID) > 0
    await service.shutdown()


# ---------- панель и табло ----------


def test_the_panel_is_two_columns_of_full_names():
    """Столбца два — удар и блок, — и зоны помещаются целиком."""
    from bot.keyboards import fight_keyboard

    rows = fight_keyboard(1).inline_keyboard
    assert [[button.text for button in row] for row in rows] == [
        ["👊Голова", "🛡 Голова + Корпус"],
        ["👊Корпус", "🛡 Корпус + Живот"],
        ["👊Живот", "🛡 Живот + Пояс"],
        ["👊Пояс", "🛡 Пояс + Ноги"],
        ["👊Ноги", "🛡 Ноги + Голова"],
    ]
    # значок берётся с оружия: с ножом в руке кнопки подписаны ножом
    armed = fight_keyboard(1, "🔪").inline_keyboard
    assert [row[0].text for row in armed][0] == "🔪Голова"


def test_the_panel_buttons_point_at_the_right_zones():
    """Под надписями — те же зоны, и ни одной лишней кнопки удара."""
    from bot.game.classes import ALL_ZONES
    from bot.keyboards import FightCB, fight_keyboard

    rows = fight_keyboard(7).inline_keyboard
    assert all(len(row) == 2 for row in rows)  # второго удара на панели нет
    for zone, (hit, block) in zip(ALL_ZONES, rows):
        data = FightCB.unpack(hit.callback_data)
        assert (data.action, data.zone) == ("attack", zone.value)
        guard = FightCB.unpack(block.callback_data)
        assert (guard.action, guard.zone, guard.duel_id) == ("block", zone.value, 7)


async def test_the_board_shows_both_fighters_side_by_side(bot, db):
    """Табло: кто с кем, здоровье, цветная полоска и готовность."""
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Victor", "warrior"))
    await db.save_player(make_player(2, "x RED x", "assassin"))
    session = await service.start_duel(
        CHAT_ID, THREAD_ID, await db.get_player(1), await db.get_player(2)
    )
    session.fighters[1].hp = 2  # почти нокаут: полоска должна покраснеть
    await service.handle_choice(session.id, 1, "attack", "head")
    await service.handle_choice(session.id, 1, "block", "legs")

    board = service._prompt_text(session).splitlines()

    assert board[2].startswith("<pre>") and board[5].endswith("</pre>")
    head = board[2].removeprefix("<pre>")
    assert "Victor" in head and "VS." in head and "x RED x" in head
    assert head.endswith("[1]")  # уровень стоит у каждого имени
    assert board[3].startswith("[2/")
    assert board[4].startswith("🟥") and "🟩" in board[4]
    assert board[5].startswith("✅ Готов") and "⏳ Думает" in board[5]
    assert board[-1].endswith("Выберите удар и блок.")

    # и правая колонка на всех четырёх строках начинается с одного места
    from bot.game.narrator import BOARD_COLUMN, cells

    rows = [head] + board[3:5] + [board[5].removesuffix("</pre>")]
    for row in rows:
        left, _, right = row.partition("  ")
        assert cells(left) < BOARD_COLUMN
        assert cells(row) - cells(right.lstrip()) == BOARD_COLUMN
    await service.shutdown()


def test_the_bar_changes_colour_with_the_damage():
    """Зелёный, пока цел, жёлтый на середине, красный под нокаутом."""
    from bot.game.narrator import BOARD_BAR, color_bar

    assert color_bar(100, 100) == "🟩" * BOARD_BAR
    assert color_bar(50, 100).startswith("🟨")
    assert color_bar(5, 100).startswith("🟥")
    assert color_bar(0, 100) == "⬛" * BOARD_BAR
    # живой боец не остаётся с пустой полоской, сколько бы ни пропустил
    assert color_bar(1, 1000) == "🟥" + "⬛" * (BOARD_BAR - 1)
