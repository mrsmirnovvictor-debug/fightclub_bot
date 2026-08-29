"""Экономика прогресса: опыт, уровни, апы, кредиты и рейтинг.

Все числа игрового баланса собраны здесь — чтобы крутить их, не трогая
логику боя и хендлеры.
"""

from __future__ import annotations

from bot.game.pro import PRO_EXP_SHARE

# ---------- уровни и опыт ----------

MAX_LEVEL = 10
# Сколько опыта нужно, чтобы уйти с уровня на следующий
EXP_LEVEL_BASE = 100
EXP_LEVEL_STEP = 50
# Промежуточные апы: четвёртый совпадает с получением уровня
MICRO_UPS_PER_LEVEL = 4
POINTS_PER_UP = 1

# Опыт за победу: база плюс доля от нанесённого урона
EXP_BASE_WIN = 30
EXP_PER_DAMAGE = 0.5
# Поправка на разницу уровней: побить старшего выгоднее, младшего — почти нет
EXP_LEVEL_DIFF_STEP = 0.15
EXP_LEVEL_DIFF_MIN = 0.4
EXP_LEVEL_DIFF_MAX = 2.0

# Ручки на случай, если клуб начнёт выгорать: доля награды победителя,
# которая достанется проигравшему и участникам ничьей.
LOSS_EXP_SHARE = 0.0
DRAW_EXP_SHARE = 0.0

# ---------- кредиты ----------

# Кредиты капают только с роста бойца: за апы и за уровни. Сам бой денег не
# приносит — иначе доход считался бы не по времени в клубе, а по числу драк,
# и цены в лавке поплыли бы вслед за самым усидчивым.
UP_CREDITS = 10  # за каждый ап, включая тот, что совпал с уровнем
LEVEL_CREDITS = 50  # сверху за сам уровень

PRICE_RESPEC = 60
PRICE_CLASS_CHANGE = 200
PRICE_APPEARANCE = 20  # смена прозвища или аватара

# ---------- рейтинг ----------

RATING_START = 1000
RATING_BASE = 25
RATING_LEVEL_STEP = 0.2
RATING_MIN_DELTA = 5
RATING_MAX_DELTA = 60

# ---------- антифарм ----------

# Доля награды за первый, второй, третий и последующие бои
# с одним и тем же соперником за сутки.
REPEAT_SHARES = (1.0, 0.5, 0.2)
REPEAT_WINDOW_HOURS = 24


def exp_to_next_level(level: int) -> int:
    """Сколько опыта нужно, чтобы уйти с текущего уровня на следующий."""
    return EXP_LEVEL_BASE + EXP_LEVEL_STEP * (level - 1)


def exp_per_up(level: int) -> int:
    """Шаг промежуточного апа — четверть уровня."""
    return max(1, exp_to_next_level(level) // MICRO_UPS_PER_LEVEL)


def ups_earned(exp: int, level: int) -> int:
    """Сколько промежуточных апов заслужено накопленным на уровне опытом."""
    return min(MICRO_UPS_PER_LEVEL - 1, exp // exp_per_up(level))


def credits_per_level() -> int:
    """Сколько кредитов приносит уровень — весь доход бойца, других нет.

    На это число смотрят цены в лавке: набор одной ступени стоит примерно
    втрое дороже — поэтому на всё сразу не хватает и приходится выбирать.
    """
    return MICRO_UPS_PER_LEVEL * UP_CREDITS + LEVEL_CREDITS


def level_diff_multiplier(my_level: int, opponent_level: int) -> float:
    raw = 1.0 + EXP_LEVEL_DIFF_STEP * (opponent_level - my_level)
    return max(EXP_LEVEL_DIFF_MIN, min(EXP_LEVEL_DIFF_MAX, raw))


def win_exp(damage_dealt: int, my_level: int, opponent_level: int) -> int:
    """Опыт победителя: за участие, за нанесённый урон и за класс соперника."""
    raw = EXP_BASE_WIN + EXP_PER_DAMAGE * max(0, damage_dealt)
    return max(1, round(raw * level_diff_multiplier(my_level, opponent_level)))


def consolation_exp(winner_exp: int, share: float) -> int:
    """Утешительный опыт проигравшему или за ничью. По умолчанию — ноль."""
    if share <= 0:
        return 0
    return max(1, round(winner_exp * share))


def rating_delta(won: bool, my_level: int, opponent_level: int) -> int:
    """Изменение рейтинга: побить старшего дорого стоит, младшего — почти нет.

    Ничья считается поражением для обоих, поэтому отдельного случая нет.
    """
    difference = opponent_level - my_level if won else my_level - opponent_level
    raw = RATING_BASE * (1.0 + RATING_LEVEL_STEP * difference)
    delta = max(RATING_MIN_DELTA, min(RATING_MAX_DELTA, round(raw)))
    return delta if won else -delta


def repeat_share(previous_fights: int) -> float:
    """Во сколько раз урезать награду за повторный бой с тем же соперником."""
    if previous_fights < 0:
        previous_fights = 0
    if previous_fights >= len(REPEAT_SHARES):
        return REPEAT_SHARES[-1]
    return REPEAT_SHARES[previous_fights]


def apply_share(value: int, share: float) -> int:
    """Урезать награду, не обнуляя её полностью."""
    if value <= 0 or share >= 1.0:
        return value
    return max(1, round(value * share))


def pro_exp(exp: int, is_pro: bool) -> int:
    """Опыт с учётом подписки: PRO приносит полтора.

    Считаем в самом конце, после всех урезаний за повторные бои, — иначе
    подписка вытаскивала бы награду за фарм одного и того же соперника.
    """
    return round(exp * PRO_EXP_SHARE) if is_pro else exp
