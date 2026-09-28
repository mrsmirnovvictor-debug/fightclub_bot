"""Выбор и покупка образа.

Правило одно: платный образ покупается один раз, дальше он свой навсегда.
Смена образа между уже своими бесплатна и мгновенна — это внешность, а не
экипировка, на бой она не влияет никак.

**Гардероб показывает образы своего пола.** Пол выбирают при создании
персонажа, и дальше он и решает, из чего боец одевается: женщине незачем
листать мужские лица, чтобы добраться до своих. Чужой пол не прячется
запретом, а просто не приходит на страницу — выбирать там нечего.

Исключение одно: образ, который уже на бойце или уже куплен. Такой
показывается всегда, какого бы он ни был пола. Бойцы, заведённые до
выбора пола, получили мужской, и отнимать у них купленное за кредиты
лицо из-за этого было бы воровством.
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
    return look.code in owned if (look.paid or look.pro) else True


def shown_to(look: Look, player: Player, owned: set[str]) -> bool:
    """Показывать ли этот образ этому бойцу.

    Три причины показать: он своего пола, он уже куплен или он сейчас на
    бойце. Две последние важнее пола — купленное и надетое не отнимают.
    """
    if look.pro and look.code not in owned:
        # Старая выдача: у кого её нет, у того и не будет — кнопка,
        # которая ничего не делает, только дразнит
        return False
    return (
        look.gender == player.sex
        or look.code in owned
        or look.code == player.look
    )


async def choose_look(db: Database, player: Player, code: str) -> LookChoice:
    """Надеть образ, купив его, если он платный и ещё не куплен."""
    look = get_look(code)
    if look is None:
        raise LookError("Такого образа в клубе нет.")

    owned = await db.owned_looks(player.user_id)
    if look.pro and look.code not in owned:
        # Этот образ раздавали с подпиской и раздавать перестали: ни за
        # кредиты, ни за звёзды его теперь не получить
        raise LookError("Этот образ больше не выдают.")
    # Чужой пол не только не показывается, но и не продаётся. Спрятанная
    # кнопка обходится запросом мимо страницы, и без этой проверки боец
    # мог купить лицо, которого потом не увидит в своём гардеробе
    if not shown_to(look, player, owned):
        raise LookError("Этот образ не из твоего гардероба.")
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
    """Образы своего пола: какой надет, какие свои, какие ещё купить."""
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
            "pro": look.pro,
            "current": bool(player.look)
            and look.code == chosen.code
            and not player.avatar_file_id,
            "affordable": player.credits >= look.price,
        }
        for look in LOOKS
        if shown_to(look, player, owned)
    ]


__all__ = [
    "LookChoice",
    "LookError",
    "choose_look",
    "current_look",
    "shown_to",
    "wardrobe",
]
