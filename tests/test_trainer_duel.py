"""Бой с Тренером: как он начинается и как Тренер в нём ходит.

Награда, учёт и квест — отдельно; здесь только ринг.
"""

import pytest

from bot.config import Config
from bot.duel_service import DuelError, DuelService
from bot.game.classes import ALL_ZONES, Stats
from bot.game.trainer import TRAINER_ID, TRAINER_NAME, trainer_level
from bot.models import Player
from bot.trainer_service import ensure_trainer


class FakeBot:
    """Бот, который молча проглатывает всё: в этих боях чата нет вовсе."""

    async def send_message(self, *args, **kwargs):
        return None

    async def edit_message_text(self, *args, **kwargs):
        return None


@pytest.fixture
def duels(db) -> DuelService:
    return DuelService(
        bot=FakeBot(),
        db=db,
        config=Config(bot_token="test", db_path=":memory:", round_break=0),
    )


def a_learner(level: int = 1, user_id: int = 42) -> Player:
    stats = Stats(strength=4, agility=3, intuition=3, endurance=4)
    return Player(
        user_id=user_id,
        nickname="Новичок",
        class_code="warrior",
        strength=stats.strength,
        agility=stats.agility,
        intuition=stats.intuition,
        endurance=stats.endurance,
        level=level,
    )


async def test_the_trainer_takes_the_fight_at_once(duels, db):
    """Стойки нет и гонга ждать не от кого: урок начинается сразу."""
    learner = a_learner()
    await db.save_player(learner)

    session = await duels.start_trainer_duel(learner)

    assert session.started, "бой ждёт гонга, которого некому дать"
    assert session.npc == TRAINER_ID
    assert set(session.order) == {learner.user_id, TRAINER_ID}
    assert session.fighters[TRAINER_ID].name == TRAINER_NAME
    assert session.fighters[TRAINER_ID].level == trainer_level(learner.level)


async def test_the_trainer_has_already_moved_when_the_round_opens(duels, db):
    """Ход Тренера стоит в бою до того, как ученик что-то нажал.

    За него никто не нажимает, и ждать его бою нечего: он выбирает
    сразу, как начался ход.
    """
    learner = a_learner()
    await db.save_player(learner)

    session = await duels.start_trainer_duel(learner)

    choice = session.choices.get(TRAINER_ID)
    assert choice is not None, "Тренер не выбрал ничего и бой встал"
    assert choice.attacks, "не выбран удар"
    assert choice.block, "не выбран блок"
    assert set(choice.attacks.values()) <= set(ALL_ZONES)
    assert session.is_ready(TRAINER_ID)

    # А ученик ещё ничего не выбрал: бой ждёт только его
    assert not session.is_ready(learner.user_id)


async def test_he_moves_anew_every_round(duels, db):
    """Выбор не замирает на первом ходу: каждый ход — свой.

    Повторяющийся соперник учит одному — своей привычке, и ученик
    выносит его вслепую, ничего не разглядев.
    """
    learner = a_learner()
    await db.save_player(learner)
    session = await duels.start_trainer_duel(learner)

    seen = set()
    for _ in range(40):
        await duels._start_round(session)
        choice = session.choices[TRAINER_ID]
        seen.add((tuple(sorted(choice.attacks.items())), choice.block))
    assert len(seen) > 1, "Тренер ходит одно и то же"


async def test_only_the_first_four_levels_are_let_onto_the_mat(duels, db):
    """Пятому уровню Тренер отказывает словами."""
    learner = a_learner(level=5, user_id=43)
    await db.save_player(learner)

    with pytest.raises(DuelError, match="новичков"):
        await duels.start_trainer_duel(learner)


async def test_a_busy_trainer_turns_the_next_pupil_away(duels, db):
    """Принял бой — и стал недоступен остальным."""
    first = a_learner(user_id=42)
    second = a_learner(user_id=43)
    await db.save_player(first)
    await db.save_player(second)

    await duels.start_trainer_duel(first)
    with pytest.raises(DuelError, match="занят"):
        await duels.start_trainer_duel(second)


async def test_a_beaten_trainer_does_not_call_anybody(duels, db):
    """Побитый Тренер на ринг не выходит, пока не затянется."""
    learner = a_learner()
    await db.save_player(learner)
    trainer = await ensure_trainer(db)
    trainer.set_hp(5)
    await db.save_player(trainer)

    with pytest.raises(DuelError, match="занят|попозже"):
        await duels.start_trainer_duel(learner)
