"""Лестница наград за вход: что дают на какой день месяца.

Здесь только содержимое. Правила — в `bot.game.daily`.

Пустых дней в календаре нет: на вехах ждут кредиты и склянки, а во все
остальные дни — рейд-пасс. Это и есть смысл ежедневной награды: заходить
стоит каждый день, а не шесть раз в месяц. В последний день месяца сверх
того кладётся простая заточка на оружие — единственный подарок, который
остаётся с бойцом насовсем.

Лестницу будут дополнять, и для этого достаточно дописать строку в
`LADDER`: всё остальное — сетка в мини-аппе, выдача, подсчёт невзятого —
считает награды по этой таблице и про конкретные дни ничего не знает.

День — это не число месяца, а какой по счёту раз боец заглянул в клуб за
месяц. Зашёл первого, третьего и десятого — это первый, второй и третий
вход, награда за третий уже ждёт. Клеток в календаре ровно столько,
сколько дней в месяце: больше входов, чем дней, не бывает.
"""

from __future__ import annotations

from dataclasses import replace

from bot.game.daily import Reward, days_in_month, merge, month_index
from bot.game.potions import RAID_PASS

# Эликсир за четырнадцатый вход меняется от месяца к месяцу: три склянки
# по кругу, чтобы за квартал боец собрал все три
MONTHLY_POTIONS: tuple[tuple[str, str, str], ...] = (
    ("boost_strength", "Эликсир силы", "💪"),
    ("boost_agility", "Эликсир ловкости", "🤸"),
    ("boost_intuition", "Эликсир интуиции", "🔮"),
)

# Простая заточка оружия: приз за последний день месяца
LAST_DAY_MOD = "sharpen_weapon_1"


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
        big=True,
    ),
    3: Reward(
        day=3,
        title="Эликсир восстановления",
        icon="🧪",
        potion="heal_small",
        note="Мутная склянка от бармена. Ставит на ноги.",
        big=True,
    ),
    7: Reward(
        day=7,
        title="50 кредитов",
        icon="💰",
        credits=50,
        note="Неделя в клубе — неделя на счету.",
        big=True,
    ),
    14: Reward(day=14, title="Эликсир месяца", icon="🔮", potion="", big=True),
    21: Reward(
        day=21,
        title="100 кредитов",
        icon="💰",
        credits=100,
        note="Три недели в клубе. Такое замечают.",
        big=True,
    ),
    28: Reward(
        day=28,
        title="200 кредитов",
        icon="💰",
        credits=200,
        note="Месяц без пропусков. Вот это уже характер.",
        big=True,
    ),
}

# Дни, на которых ждёт что-то крупное, — по возрастанию. Остальные дни не
# пустые, но там всегда одно и то же
MILESTONES: tuple[int, ...] = tuple(sorted(LADDER))

# Что лежит в обычный день — во все, кроме вех
EVERYDAY = Reward(
    day=0,
    title="Рейд-пасс",
    icon="🎟",
    potion=RAID_PASS,
    note="Мятый талон с печатью клуба. Пускает в подвал на всё окно.",
)

# И что кладётся сверх того в последний день месяца — хоть двадцать
# восьмой он, хоть тридцать первый
LAST_DAY = Reward(
    day=0,
    title="Простая заточка оружия",
    icon="🗡",
    mod=LAST_DAY_MOD,
    note="Последний день месяца. Единственное, что останется насовсем.",
    big=True,
)


def reward_for(day: int, month: str) -> Reward | None:
    """Что ждёт за этот по счёту вход. None — такого дня в месяце нет."""
    last = days_in_month(month)
    if day < 1 or day > last:
        return None

    base = LADDER.get(day, EVERYDAY)
    if day == 14:
        code, title, icon = monthly_potion(month)
        base = Reward(
            day=14,
            title=title,
            icon=icon,
            potion=code,
            note="Склянка месяца: в следующем будет другая.",
            big=True,
        )
    base = replace(base, day=day)
    if day != last:
        return base
    # Заглавным ставим то, что крупнее: в обычный день это заточка, а в
    # феврале — веха двадцать восьмого дня, к которой заточка идёт довеском
    return merge(base, LAST_DAY) if base.big else merge(LAST_DAY, base)


def month_days(month: str) -> int:
    """Сколько клеток в календаре этого месяца."""
    return days_in_month(month)


def unclaimed(days: int, claimed: int, month: str) -> tuple[Reward, ...]:
    """Что боец заслужил, но ещё не забрал.

    `claimed` — до какого дня награды уже взяты. Невзятое не сгорает:
    дошёл до третьего дня и отвлёкся — заберёшь вместе с седьмым.
    """
    return tuple(
        reward
        for day in range(claimed + 1, min(days, days_in_month(month)) + 1)
        if (reward := reward_for(day, month)) is not None
    )


def next_milestone(days: int, month: str) -> int:
    """До какого дня расти дальше. Ноль — календарь этого месяца пройден."""
    return days + 1 if days < days_in_month(month) else 0


__all__ = [
    "EVERYDAY",
    "LADDER",
    "LAST_DAY",
    "LAST_DAY_MOD",
    "MILESTONES",
    "MONTHLY_POTIONS",
    "month_days",
    "monthly_potion",
    "next_milestone",
    "reward_for",
    "unclaimed",
]
