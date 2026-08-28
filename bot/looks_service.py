"""Выбор и покупка образа.

Правило одно: платный образ покупается один раз, дальше он свой навсегда.
Смена образа между уже своими бесплатна и мгновенна — это внешность, а не
экипировка, на бой она не влияет никак.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.looks import DEFAULT_LOOK, LOOKS, Look, get_look
from bot.models import Player

logger = logging.getLogger(__name__)


class LookError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class LookChoice:
    """Чем кончился выбор: образ, списали ли кредиты и сколько осталось."""

    look: Look
    bought: bool
    credits: int


def current_look(player: Player) -> Look:
    """Образ бойца. Не выбирал — считаем, что на нём стандартный."""
    return get_look(player.look) or get_look(DEFAULT_LOOK)


def is_owned(look: Look, owned: set[str]) -> bool:
    return not look.paid or look.code in owned


async def choose_look(db: Database, player: Player, code: str) -> LookChoice:
    """Надеть образ, купив его, если он платный и ещё не куплен."""
    look = get_look(code)
    if look is None:
        raise LookError("Такого образа в клубе нет.")

    owned = await db.owned_looks(player.user_id)
    bought = False
    if look.paid and look.code not in owned:
        if player.credits < look.price:
            raise LookError(
                f"Образ стоит {look.price} 💰, а на счету {player.credits}. "
                "Пополнить: /topup"
            )
        player.grant_credits(-look.price)
        await db.add_look(player.user_id, look.code)
        bought = True
        logger.info("Боец %s купил образ %s", player.user_id, look.code)

    player.look = look.code
    # Образ и загруженное фото — одно и то же место на карточке: выбрал
    # образ, значит фото больше не показываем.
    player.avatar_file_id = None
    await db.save_player(player)
    return LookChoice(look=look, bought=bought, credits=player.credits)


async def wardrobe(db: Database, player: Player) -> list[dict]:
    """Все образы разом: какой надет, какие свои, какие ещё купить."""
    owned = await db.owned_looks(player.user_id)
    chosen = current_look(player)
    return [
        {
            "code": look.code,
            "title": look.title,
            "emoji": look.emoji,
            "image": look.picture,
            "gender": look.gender,
            "price": look.price,
            "note": look.note,
            "owned": is_owned(look, owned),
            "current": bool(player.look)
            and look.code == chosen.code
            and not player.avatar_file_id,
            "affordable": player.credits >= look.price,
        }
        for look in LOOKS
    ]


__all__ = ["LookChoice", "LookError", "choose_look", "current_look", "wardrobe"]
