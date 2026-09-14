"""Мастерская: купить модификатор и наложить его на вещь.

Правил тут два, и оба про «один раз». Модификатор списывается один раз —
атомарно, как рейд-пасс: два нажатия подряд не потратят одну заточку
дважды. И вещь модифицируется один раз: заточенное оружие второй заточке
не поддаётся, и никакой уровень модификатора этого не меняет.

Сколько выпадет — решает бросок в момент модификации, а не покупки: два
одинаковых модификатора на двух одинаковых вещах дадут разное. Число
остаётся с вещью навсегда и уходит в базу вместе с ней.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from bot.content.mods import get_mod, star_of
from bot.database import Database
from bot.game.gear import Modifier, OwnedItem
from bot.models import Player


class ModError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass(frozen=True)
class ModResult:
    """Итог модификации: что выпало и как теперь выглядит вещь."""

    owned: OwnedItem
    mod: Modifier
    value: int

    @property
    def star(self) -> str:
        return star_of(self.mod.level)

    @property
    def gain(self) -> str:
        return self.mod.describe(self.value)


async def buy_mod(db: Database, player: Player, code: str) -> Modifier:
    """Купить модификатор в мастерской. Он ложится в рюкзак стопкой."""
    mod = get_mod(code)
    if mod is None:
        raise ModError("Такого модификатора в мастерской нет.")
    if not player.can_afford(mod.price):
        raise ModError(
            f"Не хватает кредитов: «{mod.title}» стоит {mod.price} 💰, "
            f"а на счету {player.credits} 💰."
        )
    player.pay(mod.price)
    await db.save_player(player)
    await db.add_mod(player.user_id, code)
    return mod


async def apply_mod(
    db: Database,
    player: Player,
    item_id: int,
    code: str,
    rng: random.Random | None = None,
) -> ModResult:
    """Наложить модификатор на вещь. Бросок делается здесь и только здесь."""
    mod = get_mod(code)
    if mod is None:
        raise ModError("Такого модификатора в мастерской нет.")
    owned = player.find_gear(item_id)
    if owned is None:
        raise ModError("Такой вещи в инвентаре нет.")
    if owned.is_modified:
        raise ModError(
            f"«{owned.title}» уже модифицирована: второй раз нельзя."
        )
    if not mod.fits(owned.item):
        raise ModError(f"«{mod.title}» не ложится на «{owned.title}».")

    # Списываем до модификации и атомарно: не списалось — модификатора нет
    if not await db.take_mod(player.user_id, code):
        raise ModError(f"«{mod.title}» нет в рюкзаке — его сначала покупают.")

    value = mod.roll(rng)
    owned.modify(code, value)
    await db.save_gear(owned)
    return ModResult(owned=owned, mod=mod, value=value)


__all__ = ["ModError", "ModResult", "apply_mod", "buy_mod"]
