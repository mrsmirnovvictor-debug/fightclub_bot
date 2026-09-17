"""Приёмы бойца: выдать стартовый, предложить выбор, записать выученное.

Долг по приёму нигде не хранится и ни на что не подписан: он считается из
самого бойца — ступень пройдена, а приёма с неё нет. Так это работает и
для тех, кто вырос до третьего уровня задолго до появления системы, и для
любого места, где боец берёт уровень: ринг, отряд, рейд, турнир. Крючок на
повышение пришлось бы ставить в каждом, и один из них однажды забыли бы.

Первая ступень выбора не знает: класс приходит со своим приёмом, и он
выдаётся молча при первом же обращении.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.content.abilities import CHOICES, choices_at, get_ability, starter_of
from bot.database import Database
from bot.game.abilities import TIERS, Ability
from bot.models import Player


class AbilityError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass(frozen=True)
class Choice:
    """Развилка, на которой стоит боец: ступень и что предлагают."""

    tier: int
    options: tuple[Ability, ...]


def pending_tier(player: Player) -> int:
    """Ступень, за которую боец ещё не взял приём. Ноль — долгов нет.

    Считается снизу вверх: перескочивший сразу через две ступени выбирает
    по одной, начиная с младшей, — иначе развилки шестого уровня можно
    было бы взять, не тронув третий.
    """
    for tier in TIERS:
        if player.level < tier:
            break
        if not any(taken == tier for taken in player.loadout.slots.values()):
            return tier
    return 0


def pending_choice(player: Player) -> Choice | None:
    """Что бойцу предлагают выбрать прямо сейчас. Первая ступень сюда не идёт."""
    tier = pending_tier(player)
    if tier <= 1:
        return None
    return Choice(tier=tier, options=choices_at(player.class_code, tier))


async def ensure_starter(db: Database, player: Player) -> Ability | None:
    """Выдать классовый приём, если его ещё нет. Отдаёт выданное."""
    if pending_tier(player) != 1:
        return None
    ability = starter_of(player.class_code)
    player.loadout.learn(ability.code, 1)
    await db.save_abilities(player.user_id, player.loadout)
    return ability


async def learn(
    db: Database, player: Player, code: str, forget: str = ""
) -> Ability:
    """Выучить выбранный приём на той ступени, за которую боец должен.

    `forget` — что забыть, если слоты кончились. Пока ступеней ровно
    четыре, до этого не доходит; понадобится, когда приёмы начнут давать
    в обучении.
    """
    choice = pending_choice(player)
    if choice is None:
        raise AbilityError("Выбирать нечего: все приёмы своих ступеней уже взяты.")
    ability = get_ability(code)
    if ability is None:
        raise AbilityError("Такого приёма нет.")
    if ability not in choice.options:
        titles = ", ".join(f"«{one.title}»" for one in choice.options)
        raise AbilityError(
            f"На {choice.tier} ступени выбирают из трёх: {titles}."
        )
    if code in player.loadout:
        raise AbilityError(f"«{ability.title}» уже выучен.")

    try:
        player.loadout.learn(code, choice.tier, forget=forget)
    except ValueError as error:
        raise AbilityError(str(error)) from error
    await db.save_abilities(player.user_id, player.loadout)
    return ability


def known(player: Player) -> tuple[tuple[Ability, int], ...]:
    """Что боец знает: приём и ступень, в порядке ступеней."""
    rows = [
        (get_ability(code), tier)
        for code, tier in player.loadout.slots.items()
        if get_ability(code) is not None
    ]
    return tuple(sorted(rows, key=lambda row: (row[1], row[0].title)))


def next_tier_after(level: int) -> int:
    """Ближайшая ступень выше этого уровня. Ноль — расти больше некуда."""
    for tier in TIERS:
        if tier > level:
            return tier
    return 0


__all__ = [
    "AbilityError",
    "CHOICES",
    "Choice",
    "ensure_starter",
    "known",
    "learn",
    "next_tier_after",
    "pending_choice",
    "pending_tier",
]
