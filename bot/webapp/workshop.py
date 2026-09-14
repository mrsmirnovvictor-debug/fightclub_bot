"""Мастерская глазами мини-аппа: починка, прилавок модификаторов и мастер.

Три вкладки одной локации. Починка — то же, что раньше жило в рюкзаке, но
теперь на своём месте: чинят у мастера, а не на ходу. Прилавок продаёт
заточки и модификаторы. Мастер сводит вещь с модификатором в двух слотах.

Правил здесь нет — их держат `bot.game.gear` и `bot.mods_service`; тут
перевод состояния в json, который умеет нарисовать страница.
"""

from __future__ import annotations

from typing import Any

from bot.content.mods import MODS, star_of
from bot.game.gear import ModKind, Modifier, OwnedItem
from bot.models import Player
from bot.webapp.card import item_payload

# Виды товара на прилавке, в том порядке, в каком они лежат на витрине
KIND_TITLES: tuple[tuple[ModKind, str, str], ...] = (
    (ModKind.WEAPON, "Заточка оружия", "🗡"),
    (ModKind.SHIELD, "Заточка щита", "🛡"),
    (ModKind.GEAR, "Модификация предмета", "✨"),
)


def mod_payload(mod: Modifier, player: Player, mine: dict[str, int]) -> dict[str, Any]:
    """Строка прилавка: что делает, почём и сколько таких уже в рюкзаке."""
    return {
        "code": mod.code,
        "title": mod.title,
        "kind": mod.kind.value,
        "level": mod.level,
        "star": star_of(mod.level),
        "icon": mod.icon,
        "image": mod.picture,
        "span": mod.span,
        "stat": mod.stat,
        "price": mod.price,
        "gain": mod.describe(mod.low) + "…" + mod.describe(mod.high).split()[-1],
        "owned": mine.get(mod.code, 0),
        "can_afford": player.can_afford(mod.price),
    }


def target_payload(player: Player, owned: OwnedItem) -> dict[str, Any]:
    """Вещь глазами мастера: что это, надета ли и чем её можно точить."""
    row = item_payload(player, owned)
    row["equipped"] = owned.is_equipped
    # Какой вид модификатора на неё ложится — по нему мастер и подбирает пару
    row["mod_kind"] = (
        ModKind.WEAPON.value
        if owned.item.is_weapon
        else ModKind.SHIELD.value
        if owned.item.is_shield
        else ModKind.GEAR.value
    )
    return row


def build_workshop(player: Player, mine: dict[str, int]) -> dict[str, Any]:
    """Мастерская целиком: три вкладки одним ответом."""
    return {
        "credits": player.credits,
        # Починка: всё, что не надето. Надетое чинить не дают — сначала
        # снимают, иначе вещь чинится прямо на бойце
        "repair": [
            item_payload(player, owned)
            for owned in player.gear
            if not owned.is_equipped
        ],
        "shop": [
            {
                "kind": kind.value,
                "title": title,
                "icon": icon,
                "items": [
                    mod_payload(mod, player, mine)
                    for mod in MODS
                    if mod.kind is kind
                ],
            }
            for kind, title, icon in KIND_TITLES
        ],
        # Что у бойца в рюкзаке из модификаторов — второй слот мастера
        "mods": [
            mod_payload(mod, player, mine) for mod in MODS if mine.get(mod.code)
        ],
        # Что можно модифицировать: всё, на чём ещё нет звёздочки
        "targets": [
            target_payload(player, owned)
            for owned in player.gear
            if not owned.is_modified
        ],
    }


__all__ = ["build_workshop", "mod_payload", "target_payload"]
