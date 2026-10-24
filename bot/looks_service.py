"""Выбор и покупка образа: гардероб в карточке, продажа в двух домах.

Правил три, и они делят место с местом.

**Покупают образ в городе, а не в карточке.** Мужские лица режут в
барбершопе, женские делают в салоне красоты. Карточка образами больше не
торгует: в ней переключаются между своими, и это не придирка к
интерфейсу — за внешностью в этом городе ходят ногами, как за всем
остальным.

**Смотреть может любой, купить — только своего пола.** Женщина в
барбершопе видит всю полку и не берёт с неё ничего: лицо не вещь, его не
примеряют на чужое. Запрет живёт на сервере, а не в спрятанной кнопке, —
спрятанная обходится запросом мимо страницы.

**Купленный образ свой навсегда.** Смена между своими бесплатна и
мгновенна: это внешность, а не экипировка, на бой она не влияет никак.

Исключение одно, и оно старое: образ, который уже на бойце или уже
куплен, остаётся в гардеробе, какого бы он ни был пола. Бойцы, заведённые
до выбора пола, получили мужской, и отнимать у них купленное за кредиты
лицо из-за этого было бы воровством.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.looks import (
    DEFAULT_LOOK,
    LOOKS,
    Look,
    get_look,
    gender_at,
    looks_of_gender,
    shop_of,
)
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
    """Показывать ли этот образ этому бойцу в его гардеробе.

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


def where_to_buy(look: Look) -> str:
    """Куда идти за таким образом. Пусто — его не продают вовсе."""
    place = get_location(shop_of(look.gender))
    return place.title if place else ""


async def wear_look(
    db: Database, player: Player, code: str, now: int | None = None
) -> LookChoice:
    """Надеть свой образ. Чужой не надевают — за ним идут в город.

    Кредитов эта дверь не трогает вовсе: купить образ можно только там,
    где им торгуют. Иначе карточка осталась бы лавкой, из которой мы его
    и уносили.
    """
    look = get_look(code)
    if look is None:
        raise LookError("Такого образа в клубе нет.")

    owned = await db.owned_looks(player.user_id)
    if look.pro and look.code not in owned:
        # Этот образ раздавали с подпиской и раздавать перестали: ни за
        # кредиты, ни за звёзды его теперь не получить
        raise LookError("Этот образ больше не выдают.")
    if not is_owned(look, owned):
        where = where_to_buy(look)
        raise LookError(
            f"«{look.title}» ещё не твой. За ним — в {where.lower()}."
            if where
            else f"«{look.title}» ещё не твой."
        )
    if not shown_to(look, player, owned):
        raise LookError("Этот образ не из твоего гардероба.")

    await _put_on(db, player, look)
    return LookChoice(look=look, bought=False, credits=player.credits)


async def buy_look(
    db: Database, player: Player, code: str, place: str,
    now: int | None = None,
) -> LookChoice:
    """Купить образ в этом доме — и уйти из него с новым лицом.

    Пол дома и пол бойца должны совпасть: барбершоп режет мужчин, салон
    приводит в порядок женщин. Смотреть при этом можно обоим.
    """
    moment = now_ts() if now is None else now
    sells = gender_at(place)
    if not sells:
        raise LookError("Здесь образами не торгуют.")
    look = get_look(code)
    if look is None:
        raise LookError("Такого образа в клубе нет.")
    if look.pro:
        raise LookError("Этот образ больше не выдают.")
    if look.gender != sells:
        raise LookError("Этого образа тут нет: в этом доме делают другие.")
    if player.sex != sells:
        raise LookError(
            "Здесь делают мужские образы, и берут их мужчины."
            if sells == "male"
            else "Здесь делают женские образы, и берут их женщины."
        )

    owned = await db.owned_looks(player.user_id)
    if is_owned(look, owned):
        raise LookError(f"«{look.title}» и так твой: надевай в карточке.")
    price = player.price_here(look.price, moment, Service.LOOKS)
    if not player.can_afford(price, moment, Service.LOOKS):
        raise LookError(
            f"Образ стоит {price} 💰, а "
            f"{player.purse_note(moment, Service.LOOKS)}."
        )
    player.pay(price, moment, Service.LOOKS, about=f"Образ «{look.title}»")
    await db.add_look(player.user_id, look.code)
    logger.info(
        "Боец %s купил образ %s в доме %s", player.user_id, look.code, place
    )
    # Из барбершопа выходят с новой стрижкой, а не с квитанцией: купленный
    # образ сразу и надевается. Сменить его на другой свой можно тут же,
    # в карточке, и это бесплатно
    await _put_on(db, player, look)
    return LookChoice(look=look, bought=True, credits=player.credits)


async def wardrobe(
    db: Database, player: Player, now: int | None = None
) -> list[dict]:
    """Свои образы: только те, что у бойца есть. Докупать тут нечего."""
    moment = now_ts() if now is None else now
    owned = await db.owned_looks(player.user_id)
    chosen = current_look(player)
    return [
        _row(look, player, owned, chosen, moment)
        for look in LOOKS
        if shown_to(look, player, owned)
        # Надетый остаётся в гардеробе, даже если он не куплен: иначе его
        # нечем было бы сменить — выбор свёлся бы к пустой полке
        and (is_owned(look, owned) or look.code == player.look)
    ]


async def shelf(
    db: Database, player: Player, place: str, now: int | None = None
) -> list[dict]:
    """Полка дома: все образы того пола, с которым он работает.

    Смотрят полку все, поэтому на ней стоит всё — а кнопка гаснет с
    причиной. Старая выдача на полку не идёт: её не продают.
    """
    moment = now_ts() if now is None else now
    sells = gender_at(place)
    owned = await db.owned_looks(player.user_id)
    chosen = current_look(player)
    rows = []
    for look in looks_of_gender(sells):
        if look.pro:
            continue
        row = _row(look, player, owned, chosen, moment)
        mine = player.sex == sells
        row["can_buy"] = bool(not row["owned"] and mine and row["affordable"])
        row["why"] = _why(row, mine, sells)
        rows.append(row)
    return rows


def _row(
    look: Look, player: Player, owned: set[str], chosen: Look, moment: int
) -> dict:
    """Одна плитка образа: как рисуется, чья и чем занята."""
    return {
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
        "affordable": player.can_afford(look.price, moment, Service.LOOKS),
    }


def _why(row: dict, mine: bool, sells: str) -> str:
    """Почему эту плитку не купить."""
    if row["owned"]:
        return "Этот образ и так твой: надевай в карточке."
    if not mine:
        return (
            "Здесь делают мужские образы, и берут их мужчины."
            if sells == "male"
            else "Здесь делают женские образы, и берут их женщины."
        )
    if not row["affordable"]:
        return f"Образ стоит {row['price']} 💰, а столько не набирается."
    return ""


async def _put_on(db: Database, player: Player, look: Look) -> None:
    """Надеть образ и убрать загруженное фото: место на карточке одно."""
    player.look = look.code
    player.avatar_file_id = None
    await db.save_player(player)


__all__ = [
    "LookChoice",
    "LookError",
    "buy_look",
    "current_look",
    "shelf",
    "shown_to",
    "wardrobe",
    "wear_look",
    "where_to_buy",
]
