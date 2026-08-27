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
from bot.game.health import FULL_REGEN_SECONDS, now_ts
from bot.game.economy import (
    RATING_START,
    WIN_CREDITS_MIN,
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


ZONES = ["head", "chest", "belly", "belt", "legs"]


async def choose(service: DuelService, session, user_id: int, index: int = 0) -> None:
    """Полный выбор бойца: удар каждым оружием и один блок."""
    fighter = session.fighters[user_id]
    for slot in range(fighter.attacks_per_round):
        await service.handle_choice(
            session.id, user_id, "attack", ZONES[(index + slot) % len(ZONES)], slot
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
    assert any("пропуск хода" in text for text in bot.texts)
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
    assert "пропусков подряд: 2" in bot.texts[-1]  # предупреждение в шапке раунда

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
        for text in bot.texts
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
    assert "Грудь + живот" in hint  # блок закрывает смежные зоны
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


async def test_only_the_winner_gets_exp_and_credits(bot, db):
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
    assert winner.credits >= WIN_CREDITS_MIN
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


async def test_draw_gives_nothing_but_costs_rating(bot, db):
    """Ничья считается поражением обоим."""
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
        assert player.rating < RATING_START


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
    assert "без опыта" in final  # строка проигравшего


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

    rounds = [text for text in bot.texts if text.startswith("<b>⚔️ Раунд")]
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

    icons, block_width = session.panel
    assert icons == ("👊",)
    assert block_width == 2  # у танка тоже две зоны
    # панель одна: за раунд ушло приглашение и ничего больше
    prompts = [m for m in bot.sent if m.reply_markup is not None]
    assert len(prompts) == 1
    assert session.prompt_message_id == prompts[0].message_id


async def test_every_name_in_the_fight_log_opens_the_card(bot, db):
    """Имя бойца кликабельно везде: вызов, стойка, панель раунда, удары, итог."""
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
        for text in named:
            for user_id, name in ((1, "Тайлер"), (2, "Марла")):
                # каждое упоминание имени должно быть завёрнуто в ссылку
                assert text.count(name) == text.count(links_by_id[user_id]), (
                    f"имя {name} где-то без ссылки на карточку: {text[:160]}"
                )
    finally:
        links.configure("", "")
