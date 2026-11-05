"""Опыт за бой: ставка старшего, отношение ХП, повтор и подписка.

Формула одна на все бои и считается одним выражением:

    ceil(ставка[старший, но не выше моего + 2]
         × отношение ХП
         × повторный бой
         × подписка)

Здесь проверяется каждый множитель отдельно и то, что округление одно и
последнее. Сквозные прогоны по сервисам лежат рядом, в тестах дуэли,
клубного боя и подворотни.
"""

import math

import pytest

from bot.game.economy import (
    BASE_EXP,
    DUEL_DAMAGE_CAP,
    GROUP_DAMAGE_CAP,
    LEVEL_REACH,
    MAX_LEVEL,
    REPEAT_SHARES,
    base_exp,
    base_level,
    damage_share,
    repeat_share,
    win_exp,
)
from bot.game.pro import PREMIUM_TIER, PRO_TIER


# ---------- ставка по старшему ----------


def test_the_rate_is_taken_from_the_older_fighter():
    """Победа над старшим платится по его ставке, а не по своей."""
    assert base_level(5, 7) == 7
    assert base_exp(base_level(5, 7)) == BASE_EXP[7]
    # А над младшим — по своей: его ставка ниже, и опускаться до неё
    # значило бы наказывать за то, что вызвали слабого
    assert base_level(5, 2) == 5
    assert base_exp(base_level(5, 2)) == BASE_EXP[5]


def test_the_reach_upwards_is_two_levels_and_no_more():
    """Дотянуться можно на две ступени вверх, дальше ставка не растёт.

    Без предела первый уровень брался бы двумя победами над бойцом
    двенадцатого — ставка 400 против своих 50, а весь уровень стоит
    700, — и начало игры проходил бы не боец, а его старший знакомый.
    """
    assert LEVEL_REACH == 2
    assert base_level(1, 3) == 3
    assert base_level(1, 4) == 3, "дотянулись дальше, чем на две ступени"
    assert base_level(1, MAX_LEVEL) == 3
    assert base_exp(base_level(1, MAX_LEVEL)) == BASE_EXP[3]


def test_the_rate_never_climbs_past_the_ceiling():
    assert base_level(MAX_LEVEL, MAX_LEVEL) == MAX_LEVEL
    assert base_level(MAX_LEVEL - 1, MAX_LEVEL) == MAX_LEVEL
    assert base_exp(MAX_LEVEL + 5) == BASE_EXP[MAX_LEVEL]


# ---------- отношение ХП ----------


def test_the_duel_corridor_is_five_percent_wide():
    assert DUEL_DAMAGE_CAP == 1.05
    # Соперник не крепче — ровно ставка, сколько бы крови ни пустили
    assert damage_share(50, 200, 2) == 1.0
    assert damage_share(100, 200, 2) == 1.0
    # Крепче — прибавка, но не больше пяти процентов
    assert damage_share(104, 200, 2) == pytest.approx(1.04)
    assert damage_share(300, 200, 2) == DUEL_DAMAGE_CAP


def test_the_group_corridor_is_ten_times_wider():
    """В отряде урон решает: полтора базового — тому, кто работал.

    В дуэли победа означает, что боец снял с соперника все его ХП, и
    отношение упирается в сами ХП. В групповом бою снять можно и полтора
    бойца, и трёх — вот там прибавка и работает.
    """
    assert GROUP_DAMAGE_CAP == 1.50
    assert damage_share(100, 400, 4, group=True) == 1.0
    assert damage_share(150, 400, 4, group=True) == pytest.approx(1.5)
    assert damage_share(400, 400, 4, group=True) == GROUP_DAMAGE_CAP


def test_the_duel_threshold_is_an_opponent_a_tenth_tougher():
    """Пять процентов достаются тому, чей соперник крепче в 1,105 раза.

    Число не выбрано, а выведено: победа — это снятые с соперника ХП,
    то есть отношение равно 2h/(m+h), и пяти процентов оно достигает
    ровно при h >= 1,105 m.
    """
    mine = 100
    assert damage_share(110, mine + 110, 2) < DUEL_DAMAGE_CAP
    assert damage_share(111, mine + 111, 2) == DUEL_DAMAGE_CAP
    # А равный по запасу соперник не даёт ничего
    assert damage_share(mine, mine + mine, 2) == 1.0


def test_a_fight_without_hit_points_does_not_divide_by_zero():
    assert damage_share(10, 0, 0) == 1.0
    assert damage_share(0, 200, 2) == 1.0


# ---------- повторные бои ----------


def test_the_repeat_share_softens_step_by_step():
    assert REPEAT_SHARES == (1.0, 0.8, 0.6, 0.5)
    assert [repeat_share(one) for one in range(6)] == [
        1.0, 0.8, 0.6, 0.5, 0.5, 0.5
    ]
    assert repeat_share(-3) == 1.0


# ---------- всё вместе ----------


def test_the_whole_formula_is_one_expression():
    """Четыре множителя и одно округление вверх в самом конце."""
    expected = math.ceil(BASE_EXP[7] * 1.02 * 0.6 * PREMIUM_TIER.exp_share)
    got = win_exp(
        102,
        5,
        7,
        200,
        2,
        previous_fights=2,
        exp_share=PREMIUM_TIER.exp_share,
    )
    assert got == expected


def test_the_experience_is_never_below_one():
    """Победа всегда что-то стоит, как бы ни урезали награду."""
    assert win_exp(0, 1, 1, 200, 2, previous_fights=9, exp_share=0.0) >= 1


@pytest.mark.parametrize(
    "share,name",
    [(1.0, "без подписки"), (PRO_TIER.exp_share, "PRO"),
     (PREMIUM_TIER.exp_share, "PREMIUM")],
)
def test_the_corridor_holds_for_every_tier(share, name):
    """Весь разброс опыта за победу — от половины ставки до её потолка."""
    rate = BASE_EXP[5]
    lowest = win_exp(1, 5, 5, 200, 2, previous_fights=3, exp_share=share)
    highest = win_exp(
        1_000, 5, 5, 200, 2, group=True, exp_share=share
    )
    assert lowest == math.ceil(rate * 0.5 * share), name
    assert highest == math.ceil(rate * GROUP_DAMAGE_CAP * share), name
