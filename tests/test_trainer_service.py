"""Заявка Тренера: когда висит, кого пускает и каким он выходит.

Заявка нигде не хранится — она следует из состояния Тренера. Поэтому
здесь проверяется не список вызовов, а само состояние: цел ли, занят ли
и каким телом выходит против ученика.
"""

from bot.game.classes import Stats
from bot.game.health import now_ts
from bot.game.trainer import (
    BODY_LEVEL,
    TRAINER_ID,
    TRAINER_NAME,
    trainer_level,
    trainer_stats,
)
from bot.models import Player
from bot.trainer_service import (
    calls,
    health_share,
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


async def test_he_steps_out_in_the_pupils_class_two_levels_above(db):
    """Класс — ученика, уровень — его плюс фора, прокачка — полная.

    Класс зеркалится, чтобы фора была одинаковой для всех четырёх
    классов: своя заточка у Тренера оказывалась едой для танка и стеной
    для воина. Уровень выше — он учитель, и сильнее по устройству.
    """
    trainer = await ensure_trainer(db)

    for level in (1, 4):
        for code in ("warrior", "rogue", "assassin", "tank"):
            learner = a_learner(level)
            learner.class_code = code
            learner.free_points = 5  # ученик свои очки ещё не роздал

            shaped = shaped_for(trainer, learner)

            assert shaped.class_code == code
            assert shaped.level == trainer_level(level)
            assert shaped.base_stats == trainer_stats(code, shaped.level)
            assert shaped.max_hp > learner.max_hp, "учитель не крепче ученика"
            assert shaped.free_points == 0, "у учителя остались свободные очки"
            # Остался при этом Тренером: номер и прозвище свои
            assert shaped.user_id == TRAINER_ID
            assert shaped.nickname == TRAINER_NAME


async def test_the_trainer_brings_his_wounds_as_a_share(db):
    """Побитый Тренер выходит побитым — долей, а не числом единиц.

    Запас у него на ринге каждый раз чужой: против танка он носит
    вдвое больше, чем против новичка. Переносить число значило бы
    выпускать его против танка почти целым.
    """
    trainer = await ensure_trainer(db)
    trainer.set_hp(trainer.max_hp // 2)
    assert 0.45 <= health_share(trainer) <= 0.55

    learner = a_learner(4)
    learner.endurance = 20  # ученик много крепче его собственного тела
    shaped = shaped_for(trainer, learner)

    share = shaped.current_hp() / shaped.max_hp
    assert 0.45 <= share <= 0.55, f"доля не сохранилась: {share:.0%}"
    assert shaped.current_hp() == round(shaped.max_hp * health_share(trainer))


async def test_a_whole_trainer_steps_out_whole(db):
    """Целый Тренер выходит на ринг целым, каким бы ни был ученик."""
    trainer = await ensure_trainer(db)

    for level in (1, 4):
        learner = a_learner(level)
        learner.endurance = 4 + 4 * level
        shaped = shaped_for(trainer, learner)
        assert shaped.current_hp() == shaped.max_hp


async def test_he_turns_away_everybody_above_the_fourth_level(db):
    """Учатся до четвёртого. Пятому он отвечает словами, а не молчанием."""
    trainer = await ensure_trainer(db)
    assert trainer is not None

    for level in (1, 2, 3, 4):
        assert refuse(a_learner(level)) == ""

    said = refuse(a_learner(5))
    assert "новичков" in said and "четвёртый" in said
