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
    LEVEL_GAP,
    TRAINER_CLASS,
    WIN_SHARES,
    body_stats,
    lesson_exp,
    may_learn,
    trainer_level,
    trainer_stats,
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


def test_he_steps_out_stronger_by_the_whole_gap():
    """Уровень — ученика плюс фора, и прокачан он полностью.

    Учитель сильнее по устройству, а не по случайности броска: все очки
    своего уровня роздано, свободных не осталось.
    """
    for level in range(LEARNER_LEVELS[0], LEARNER_LEVELS[1] + 1):
        assert trainer_level(level) == level + LEVEL_GAP
        for fclass in FIGHTER_CLASSES.values():
            mine = developed_stats(fclass, level)
            his = trainer_stats(fclass.code, trainer_level(level))
            assert his == developed_stats(fclass, level + LEVEL_GAP)
            # Сильнее по сумме очков и крепче телом — на каждом уровне
            assert sum(his.as_dict().values()) > sum(mine.as_dict().values())
            assert (
                derive(fclass, his, 0).max_hp > derive(fclass, mine, 0).max_hp
            )


def test_the_gap_costs_the_pupil_most_of_the_fights_but_not_all():
    """Фора в два уровня стоит дорого — и это проверяется числом.

    На равных уровнях ученик брал бы половину боёв; с форой берёт около
    десятой части. Полоса здесь широкая нарочно: ловить надо не точное
    число, а два обрыва — Тренера, которого выносят с ходу, и Тренера,
    которого нельзя победить вовсе. Второе хуже: за поражение не дают
    ничего, и урок перестал бы быть уроком.
    """
    rng = random.Random(20261121)
    runs = 150
    shares = []
    for fclass in FIGHTER_CLASSES.values():
        for level in range(LEARNER_LEVELS[0], LEARNER_LEVELS[1] + 1):
            mine = developed_stats(fclass, level)
            coach_level = trainer_level(level)
            his = trainer_stats(fclass.code, coach_level)
            wins = {1: 0, 2: 0, None: 0}
            for _ in range(runs):
                pupil = Fighter(1, "Ученик", fclass, mine, level)
                coach = Fighter(2, "Тренер", fclass, his, coach_level)
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
            assert 0.02 <= share <= 0.35, (
                f"{fclass.title} ур.{level}: ученик берёт {share:.0%}"
            )
    assert 0.05 <= statistics.mean(shares) <= 0.20, (
        f"в среднем {statistics.mean(shares):.0%}"
    )


def test_without_the_gap_the_fight_would_be_even():
    """Фора — единственное, чем бой неровен: убери её, и выйдет половина.

    Проверка не про `LEVEL_GAP`, а про то, что класс зеркалится честно:
    на одном уровне с учеником Тренер — его точная копия, и половина
    побед тогда следует из симметрии, а не из подгонки чисел.
    """
    rng = random.Random(777)
    runs = 150
    shares = []
    for fclass in FIGHTER_CLASSES.values():
        stats = developed_stats(fclass, 4)
        assert trainer_stats(fclass.code, 4) == stats, "копия не копия"
        wins = {1: 0, 2: 0, None: 0}
        for _ in range(runs):
            pupil = Fighter(1, "Ученик", fclass, stats, 4)
            coach = Fighter(2, "Тренер", fclass, stats, 4)
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
        shares.append((wins[1] + wins[None] / 2) / runs)
    assert 0.40 <= statistics.mean(shares) <= 0.60, (
        f"на равных уровнях бой неровен: {statistics.mean(shares):.0%}"
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
