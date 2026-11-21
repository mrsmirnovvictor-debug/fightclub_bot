"""Правила Тренера: кого он учит, каким выходит и сколько за него дают.

Здесь только счёт без базы и без ринга. Сама заявка, ход боя и награда —
в тестах боёв.
"""

from bot.game.classes import get_class
from bot.game.combat import derive
from bot.game.trainer import (
    BODY_LEVEL,
    LEARNER_LEVELS,
    LEVEL_GAP,
    TRAINER_CLASS,
    WIN_SHARES,
    may_learn,
    trainer_level,
    trainer_stats,
    win_share,
)


def body(level: int):
    return derive(get_class(TRAINER_CLASS), trainer_stats(level), 0)


def test_the_trainer_steps_out_two_levels_above_his_pupil():
    """Уровень Тренера — уровень ученика плюс два, и это всё правило."""
    assert trainer_level(1) == 3
    assert trainer_level(4) == 6
    assert trainer_level(4) == LEARNER_LEVELS[1] + LEVEL_GAP


def test_only_the_first_four_levels_are_let_in_to_him():
    """Учатся с первого по четвёртый. Пятый уже не ученик."""
    assert [may_learn(level) for level in (1, 2, 3, 4)] == [True] * 4
    assert not may_learn(5)
    assert not may_learn(0), "нулевого уровня не бывает, но и пускать некого"


def test_the_trainer_keeps_one_body_for_every_pupil():
    """Запас здоровья у Тренера один на все бои.

    Здоровье у него копится между боями и затягивается по часам клуба.
    Если бы запас менялся от соперника, «восстановилось до ста
    процентов» означало бы каждый раз другое число, и накопленное было
    бы не с чем сравнивать.
    """
    pools = {body(level).max_hp for level in range(3, BODY_LEVEL + 1)}
    assert len(pools) == 1, f"тело гуляет от уровня к уровню: {pools}"


def test_he_grows_in_dodging_and_crits_and_not_in_muscle():
    """Заточка — перекос, а не отказ от силы.

    Чистая заточка «только уворот и крит» оставляет его без силы и
    выносливости, и на четвёртом уровне ученик оказывается крепче
    учителя. Поэтому три очка из четырёх идут в проворство и
    интуицию, а четвёртое — в силу и выносливость по очереди.
    """
    low, high = body(3), body(BODY_LEVEL)
    assert high.dodge_chance > low.dodge_chance
    assert high.crit_chance > low.crit_chance
    # Сила тоже растёт, просто медленнее
    assert high.damage_max > low.damage_max

    # И уворот у него выше, чем у игрока того же уровня с ровной
    # раскладкой очков: иначе заточки не видно вовсе
    even = derive(get_class(TRAINER_CLASS), _even_stats(BODY_LEVEL), 0)
    assert high.dodge_chance > even.dodge_chance
    assert high.crit_chance > even.crit_chance


def test_he_is_not_tougher_than_the_pupil_he_teaches():
    """Против старшего ученика Тренер берёт не запасом, а уворотом.

    Если бы он был крепче по всем числам, бой перестал бы быть уроком
    и стал бы стеной.
    """
    pupil = derive(get_class(TRAINER_CLASS), _even_stats(4), 0)
    teacher = body(trainer_level(4))
    assert teacher.max_hp < pupil.max_hp, "учитель толще ученика"
    assert teacher.dodge_chance > pupil.dodge_chance


def test_the_first_two_wins_a_day_pay_and_the_rest_do_not():
    """Первая победа за сутки — полный опыт, вторая — половина, дальше ноль."""
    assert win_share(0) == 1.0
    assert win_share(1) == 0.5
    assert win_share(2) == 0.0
    assert win_share(9) == 0.0
    assert list(WIN_SHARES) == [1.0, 0.5]


def _even_stats(level: int):
    """Боец того же класса с ровной раздачей очков — для сравнения."""
    from bot.game.classes import Stats
    from bot.game.economy import MICRO_UPS_PER_LEVEL, POINTS_PER_UP

    base = get_class(TRAINER_CLASS).base_stats
    points = POINTS_PER_UP * MICRO_UPS_PER_LEVEL * max(0, level - 1)
    share, rest = divmod(points, 4)
    return Stats(
        strength=base.strength + share + (1 if rest > 0 else 0),
        agility=base.agility + share + (1 if rest > 1 else 0),
        intuition=base.intuition + share + (1 if rest > 2 else 0),
        endurance=base.endurance + share,
    )
