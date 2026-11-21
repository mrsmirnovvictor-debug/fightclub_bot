"""Правила Тренера: кого он учит, каким выходит и сколько за него дают.

Здесь только счёт без базы и без ринга. Сама заявка, ход боя и награда —
в тестах боёв.
"""

import random
import statistics

from bot.game.classes import FIGHTER_CLASSES, get_class
from bot.game.combat import NO_GONG, Fighter, derive, random_action, resolve_round
from bot.game.reference import developed_stats
from bot.game.trainer import (
    BODY_LEVEL,
    LEARNER_LEVELS,
    TRAINER_CLASS,
    WIN_SHARES,
    body_stats,
    lesson_exp,
    may_learn,
    win_share,
)


def test_only_the_first_four_levels_are_let_in_to_him():
    """Учатся с первого по четвёртый. Пятый уже не ученик."""
    assert [may_learn(level) for level in (1, 2, 3, 4)] == [True] * 4
    assert not may_learn(5)
    assert not may_learn(0), "нулевого уровня не бывает, но и пускать некого"


def test_his_own_body_is_a_real_fighters_body():
    """Запись Тренера — не заглушка: по ней считается его здоровье.

    На ринг эти числа не выходят (там тело ученика), но запас по ним
    затягивается между боями, и запись с нулевой выносливостью сделала
    бы «сто процентов здоровья» бессмысленным.
    """
    mine = body_stats()
    assert mine == developed_stats(get_class(TRAINER_CLASS), BODY_LEVEL)
    body = derive(get_class(TRAINER_CLASS), mine, 0)
    assert body.max_hp > 0
    assert body.damage_max > 0


def test_a_mirror_fight_is_won_half_the_time_by_every_class():
    """Отражение даёт половину побед — и каждому классу, и на каждом уровне.

    Это не подогнанное число: бой симметричный, и другим он быть не
    может. Прогон держим коротким, но достаточным, чтобы заметить
    перекос самого движка — вроде преимущества того, кто стоит первым.
    """
    rng = random.Random(20261121)
    runs = 150
    shares = []
    for fclass in FIGHTER_CLASSES.values():
        for level in range(LEARNER_LEVELS[0], LEARNER_LEVELS[1] + 1):
            stats = developed_stats(fclass, level)
            wins = {1: 0, 2: 0, None: 0}
            for _ in range(runs):
                pupil = Fighter(1, "Ученик", fclass, stats, level)
                coach = Fighter(2, "Тренер", fclass, stats, level)
                number = 1
                while True:
                    result = resolve_round(
                        pupil, random_action(pupil, rng),
                        coach, random_action(coach, rng),
                        number, rng, gong=NO_GONG,
                    )
                    if result.finished:
                        wins[result.winner_id] += 1
                        break
                    number += 1
            share = (wins[1] + wins[None] / 2) / runs
            shares.append(share)
            assert 0.35 <= share <= 0.65, (
                f"{fclass.title} ур.{level}: ученик берёт {share:.0%}"
            )
    assert 0.45 <= statistics.mean(shares) <= 0.55, (
        f"в среднем {statistics.mean(shares):.0%}"
    )


def test_the_first_two_wins_a_day_pay_and_the_rest_do_not():
    """Первая победа за сутки — полный опыт, вторая — половина, дальше ноль."""
    assert win_share(0) == 1.0
    assert win_share(1) == 0.5
    assert win_share(2) == 0.0
    assert win_share(9) == 0.0
    assert list(WIN_SHARES) == [1.0, 0.5]


def test_the_share_of_the_day_turns_into_whole_experience():
    """Доли опыта: полный, половина, ноль — и половина не обнуляется."""
    assert lesson_exp(100, 0) == 100
    assert lesson_exp(100, 1) == 50
    assert lesson_exp(100, 2) == 0
    assert lesson_exp(1, 1) == 1
