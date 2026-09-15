"""Лестница наград за вход: что дают на какой день месяца.

Здесь только содержимое. Правила — в `bot.game.daily`.

Лестницу будут дополнять, и для этого достаточно дописать сюда строку:
всё остальное — окно в мини-аппе, выдача, подсчёт невзятого — считает
награды по этой таблице и про конкретные дни ничего не знает.

День — это не число месяца, а какой по счёту раз боец заглянул в клуб за
месяц. Зашёл первого, третьего и десятого — это первый, второй и третий
вход, награда за третий уже ждёт.
"""

from __future__ import annotations

from bot.game.daily import Reward, month_index

# Эликсир за четырнадцатый вход меняется от месяца к месяцу: три склянки
# по кругу, чтобы за квартал боец собрал все три
MONTHLY_POTIONS: tuple[tuple[str, str, str], ...] = (
    ("boost_strength", "Эликсир силы", "💪"),
    ("boost_agility", "Эликсир ловкости", "🤸"),
    ("boost_intuition", "Эликсир интуиции", "🔮"),
)


def monthly_potion(month: str) -> tuple[str, str, str]:
    """Какая склянка ждёт в этом месяце."""
    return MONTHLY_POTIONS[month_index(month) % len(MONTHLY_POTIONS)]


# День входа → что за него дают. Склянка месяца собирается отдельно:
# она зависит от месяца, а остальное — нет
LADDER: dict[int, Reward] = {
    1: Reward(
        day=1,
        title="25 кредитов",
        icon="💰",
        credits=25,
        note="За то, что зашёл. Клуб помнит своих.",
    ),
    3: Reward(
        day=3,
        title="Эликсир восстановления",
        icon="🧪",
        potion="heal_small",
        note="Мутная склянка от бармена. Ставит на ноги.",
    ),
    7: Reward(
        day=7,
        title="50 кредитов",
        icon="💰",
        credits=50,
        note="Неделя в клубе — неделя на счету.",
    ),
    14: Reward(day=14, title="Эликсир месяца", icon="🔮", potion=""),
    21: Reward(
        day=21,
        title="100 кредитов",
        icon="💰",
        credits=100,
        note="Три недели в клубе. Такое замечают.",
    ),
    28: Reward(
        day=28,
        title="200 кредитов",
        icon="💰",
        credits=200,
        note="Месяц без пропусков. Вот это уже характер.",
    ),
}

# Дни, на которых что-то ждёт, — по возрастанию
MILESTONES: tuple[int, ...] = tuple(sorted(LADDER))


def reward_for(day: int, month: str) -> Reward | None:
    """Что ждёт за этот по счёту вход. None — за этот день ничего нет."""
    reward = LADDER.get(day)
    if reward is None:
        return None
    if day == 14:
        code, title, icon = monthly_potion(month)
        return Reward(
            day=14,
            title=title,
            icon=icon,
            potion=code,
            note="Склянка месяца: в следующем будет другая.",
        )
    return reward


def unclaimed(days: int, claimed: int, month: str) -> tuple[Reward, ...]:
    """Что боец заслужил, но ещё не забрал.

    `claimed` — до какого дня награды уже взяты. Невзятое не сгорает:
    дошёл до третьего дня и отвлёкся — заберёшь вместе с седьмым.
    """
    return tuple(
        reward
        for day in MILESTONES
        if claimed < day <= days and (reward := reward_for(day, month)) is not None
    )


def next_milestone(days: int) -> int:
    """До какого дня расти дальше. Ноль — лестница на этот месяц пройдена."""
    for day in MILESTONES:
        if day > days:
            return day
    return 0


__all__ = [
    "LADDER",
    "MILESTONES",
    "MONTHLY_POTIONS",
    "monthly_potion",
    "next_milestone",
    "reward_for",
    "unclaimed",
]
