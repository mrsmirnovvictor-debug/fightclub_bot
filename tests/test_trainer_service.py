"""Заявка Тренера: когда висит, кого пускает и каким он выходит.

Заявка нигде не хранится — она следует из состояния Тренера. Поэтому
здесь проверяется не список вызовов, а само состояние: цел ли, занят ли
и каким телом выходит против ученика.
"""

from bot.game.classes import Stats
from bot.game.health import now_ts
from bot.game.trainer import BODY_LEVEL, TRAINER_ID, TRAINER_NAME, trainer_level
from bot.models import Player
from bot.trainer_service import (
    calls,
    ensure_trainer,
    refuse,
    rested,
    shaped_for,
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


async def test_the_trainer_is_written_down_once_and_read_after(db):
    """Запись заводится при первом обращении и больше не трогается."""
    first = await ensure_trainer(db)
    assert first.user_id == TRAINER_ID
    assert first.nickname == TRAINER_NAME
    assert first.level == BODY_LEVEL

    # Поранился и остался таким: второй вызов не лечит его заново
    first.set_hp(10)
    await db.save_player(first)

    again = await ensure_trainer(db)
    assert again.current_hp() == 10, "запись переписали поверх"


async def test_he_calls_only_while_whole_and_free(db):
    """Зовёт целым и свободным. Побитый или занятый — не зовёт."""
    trainer = await ensure_trainer(db)
    assert rested(trainer)
    assert calls(trainer, busy=False)

    # Занят боем с другим учеником
    assert not calls(trainer, busy=True)

    # Побит в прошлом бою и ещё не затянулся
    trainer.set_hp(trainer.max_hp - 1)
    assert not rested(trainer)
    assert not calls(trainer, busy=False)


async def test_his_wounds_heal_by_the_usual_club_clock(db):
    """Здоровье затягивается само, по тем же часам, что у живых бойцов."""
    trainer = await ensure_trainer(db)
    moment = now_ts()
    trainer.set_hp(1, now=moment - 60 * 60 * 24 * 7)

    # Неделя спустя он снова цел и снова зовёт
    assert rested(trainer, now=moment)
    assert calls(trainer, busy=False, now=moment)


async def test_he_steps_out_two_levels_above_the_one_who_came(db):
    """Против первого уровня — третий, против четвёртого — шестой."""
    trainer = await ensure_trainer(db)

    for level in (1, 4):
        shaped = shaped_for(trainer, a_learner(level))
        assert shaped.level == trainer_level(level)
        assert shaped.user_id == TRAINER_ID
        assert shaped.nickname == TRAINER_NAME


async def test_his_body_is_the_same_against_everybody(db):
    """Запас здоровья не зависит от того, кто пришёл.

    Иначе накопленное между боями здоровье было бы не с чем сравнивать:
    «половина» против новичка и «половина» против старшего ученика
    означали бы разные числа.
    """
    trainer = await ensure_trainer(db)
    pools = {shaped_for(trainer, a_learner(level)).max_hp for level in (1, 2, 3, 4)}
    assert len(pools) == 1, f"тело гуляет от ученика к ученику: {pools}"
    assert pools == {trainer.max_hp}


async def test_the_trainer_keeps_the_health_he_brought(db):
    """Побитый Тренер выходит побитым, а не целым под нового ученика."""
    trainer = await ensure_trainer(db)
    trainer.set_hp(30)

    shaped = shaped_for(trainer, a_learner(4))
    assert shaped.current_hp() == 30


async def test_he_turns_away_everybody_above_the_fourth_level(db):
    """Учатся до четвёртого. Пятому он отвечает словами, а не молчанием."""
    trainer = await ensure_trainer(db)
    assert trainer is not None

    for level in (1, 2, 3, 4):
        assert refuse(a_learner(level)) == ""

    said = refuse(a_learner(5))
    assert "новичков" in said and "четвёртый" in said
