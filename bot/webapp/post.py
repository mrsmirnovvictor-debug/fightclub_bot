"""Почта глазами мини-аппа: что отправить и что тебя ждёт.

Правил здесь нет — их держит `bot.game.post`, действия живут в
`bot.post_service`; тут перевод состояния в json.

Экран из двух вкладок. «Отправить» — рюкзак, ник получателя, цена и
срок. «Посылки» — две полки: что пришло тебе (и что вернулось) и что
ты отправил сам. Вторая полка не про действие, а про память: по ней
видно, у кого твоя вещь и сколько ей там ещё лежать.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import format_term, now_ts
from bot.game.locations import Service
from bot.game.post import MAX_COD, SEND_PRICE, TERMS, Parcel
from bot.models import Player
from bot.webapp.bank import purse_payload


def parcel_row(player: Player, parcel: Parcel, moment: int) -> dict[str, Any]:
    """Строка посылки: что внутри, от кого, почём и сколько ей лежать.

    Строку вещи собирает рюкзачный `item_payload`: в посылке та же вещь,
    и описывать её вторым способом значило бы рассказывать про износ
    дважды. Зовётся он отсюда, а не сверху файла: карточка сама берёт у
    экранов свои куски, и встречный импорт закольцевал бы оба.
    """
    from bot.game.equipment import get_item
    from bot.game.gear import OwnedItem
    from bot.webapp.card import item_payload

    item = get_item(parcel.code)
    if item is None:  # pragma: no cover - вещь выкинули из раздачи
        return {}
    owned = OwnedItem(
        item=item,
        id=parcel.id,
        wear=parcel.wear,
        max_wear=parcel.max_wear,
        mod=parcel.mod,
        mod_value=parcel.mod_value,
    )
    row = item_payload(player, owned)
    due = parcel.due
    left = parcel.seconds_left(moment)
    row.update(
        {
            "parcel": parcel.id,
            "from": parcel.from_name,
            "to": parcel.to_name,
            "mine": parcel.from_id == player.user_id,
            "back": parcel.back,
            "price": parcel.price,
            # Сколько платить при получении: с возврата не берут
            "due": due,
            "gift": due == 0,
            "can_take": player.can_afford(
                player.price_here(due, moment, Service.POST), moment, Service.POST
            ),
            "sent": club_moment(parcel.sent_at) if parcel.sent_at else "",
            "term": parcel.term().title,
            # Сколько посылке ещё лежать. Пусто — до востребования
            "left": format_term(left) if left else "",
            "forever": not parcel.keep_until,
        }
    )
    return row


def build_post(
    player: Player,
    inbox: list[Parcel],
    outbox: list[Parcel],
    now: int | None = None,
) -> dict[str, Any]:
    """Экран почты: рюкзак на отправку и обе полки посылок."""
    from bot.webapp.card import item_payload

    moment = now_ts() if now is None else now
    price = player.price_here(SEND_PRICE, moment, Service.POST)
    waiting = [row for row in (parcel_row(player, one, moment) for one in inbox) if row]
    return {
        "title": "Почта",
        "price": price,
        "full_price": SEND_PRICE,
        "max_cod": MAX_COD,
        "credits": player.credits,
        "purse": purse_payload(player, moment, Service.POST),
        "can_pay": player.can_afford(price, moment, Service.POST),
        "terms": [
            {"code": one.code, "title": one.title, "days": one.days}
            for one in TERMS
        ],
        # Отправить можно то, что лежит в рюкзаке: надетое сперва снимают
        "bag": [item_payload(player, owned) for owned in player.backpack],
        "inbox": waiting,
        "outbox": [
            row for row in (parcel_row(player, one, moment) for one in outbox) if row
        ],
        "waiting": len(waiting),
        "note": post_note(len(waiting), price),
        "said": "",
    }


def post_note(waiting: int, price: int) -> str:
    """Строка сверху экрана: что тут делают."""
    if waiting:
        return (
            f"Тебя ждут посылки: {waiting}. Отправить свою — {price} 💰, "
            "можно наложенным платежом и с любым сроком хранения."
        )
    return (
        f"Отправить вещь любому бойцу — {price} 💰. Можно наложенным "
        "платежом: получатель заплатит назначенную цену, и она придёт тебе."
    )


__all__ = ["build_post", "parcel_row", "post_note"]
