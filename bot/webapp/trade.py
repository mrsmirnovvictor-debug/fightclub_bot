"""Рынок глазами мини-аппа: кто здесь стоит и что лежит на столе.

Правил тут нет — их держат `bot.game.trade` и `bot.trade_service`. Здесь
только перевод в json, и одна забота сверх перевода: **свою половину
стола страница узнаёт по ключу, а не по порядку**. Стол отдаётся как
`mine` и `his`, уже разложенный сервером, — иначе страница решала бы, что
чьё, и любая ошибка в этом решении означала бы правку чужой половины.

Что положить на стол, считается тоже здесь, но по своему рюкзаку: чужой
рюкзак в ответ не попадает вовсе. Соперник видит выложенное, а не то, что
у тебя есть.
"""

from __future__ import annotations

from typing import Any

from bot.game.equipment import MAX_WEAR
from bot.game.health import now_ts
from bot.game.locations import Service, where_to
from bot.game.potions import get_potion
from bot.game.presence import is_online, presence_text
from bot.game.trade import INVITE_SECONDS, MAX_ITEMS
from bot.models import Player
from bot.trade_service import GEAR, POTION, Invite, Offer, Side, Trade, TradeService


def neighbour_payload(
    one: Player, service: TradeService, moment: int
) -> dict[str, Any]:
    """Строка списка рынка: кого видно и можно ли его позвать."""
    trading = service.trade_of(one.user_id) is not None
    busy = service.busy(one.user_id)
    return {
        "user_id": one.user_id,
        "nickname": one.nickname,
        "level": one.level,
        "pro": one.is_pro(),
        "fclass": {
            "code": one.fclass.code,
            "title": one.fclass.title,
            "emoji": one.fclass.emoji,
        },
        "online": is_online(one.seen_at, moment),
        "presence": presence_text(one.seen_at, moment),
        "trading": trading,
        "busy": busy,
        # Позвать можно того, кто свободен. Ушедших с рынка в списке нет
        # вовсе, так что про место здесь спрашивать нечего
        "callable": not trading and not busy,
    }


def gear_offer(owned, count: int = 1) -> dict[str, Any]:
    """Вещь на столе или в рюкзаке у стола."""
    return {
        "kind": GEAR,
        "key": str(owned.id),
        "title": owned.title,
        "icon": owned.emoji,
        "image": owned.image,
        "slot_title": owned.item.slot.section.capitalize(),
        "wear": owned.wear,
        "max_wear": owned.max_wear or MAX_WEAR,
        "wear_text": owned.describe_wear(),
        # Вещь всегда одна: номер у неё один, делить её не на что
        "count": count,
        "max_count": 1,
        "stack": False,
        "shop_price": owned.item.price if owned.item.on_sale else 0,
    }


def potion_offer(potion, count: int, have: int) -> dict[str, Any]:
    """Склянки на столе или в рюкзаке: то же самое, но числом."""
    return {
        "kind": POTION,
        "key": potion.code,
        "title": potion.title,
        "icon": potion.emoji,
        "image": potion.picture,
        "slot_title": "Прочее",
        "wear": 0,
        "max_wear": potion.max_wear,
        "wear_text": potion.describe_wear(),
        "count": count,
        # Сколько штук можно выставить: больше, чем есть, не передашь
        "max_count": have,
        "stack": True,
        "shop_price": potion.price,
    }


def offer_payload(offer: Offer, owner: Player | None) -> dict[str, Any]:
    """Выкладка со стола. Хозяин нужен для вещи: её данные лежат у него.

    Чужого хозяина у нас нет, и это не беда: у вещи на чужой половине
    остаются название, картинка и износ, а больше в строке и не видно.
    """
    if offer.kind == GEAR:
        owned = owner.find_gear(offer.item_id) if owner else None
        if owned is not None:
            return gear_offer(owned, offer.count)
        return {
            "kind": GEAR,
            "key": offer.key,
            "title": "Вещь",
            "icon": "🎒",
            "image": "",
            "slot_title": "",
            "wear": 0,
            "max_wear": MAX_WEAR,
            "wear_text": "",
            "count": offer.count,
            "max_count": 1,
            "stack": False,
            "shop_price": 0,
        }
    potion = get_potion(offer.key)
    if potion is None:  # pragma: no cover - склянку выкинули из правил
        return {}
    return potion_offer(potion, offer.count, offer.count)


def side_payload(side: Side, owner: Player | None) -> dict[str, Any]:
    """Половина стола: сколько денег, что за вещи и нажата ли кнопка."""
    return {
        "user_id": side.user_id,
        "nickname": side.nickname,
        "credits": side.credits,
        "ready": side.ready,
        "items": [
            row for row in (offer_payload(offer, owner) for offer in side.offers) if row
        ],
        "empty": side.empty,
    }


def basket_payload(player: Player, side: Side) -> list[dict[str, Any]]:
    """Свой рюкзак у стола: то, что ещё можно выложить.

    Надетое сюда не идёт — его сначала снимают, как и в комиссионке. А
    уже выложенное идёт: по нему видно, сколько склянок на столе, и
    число правится тем же нажатием.
    """
    rows = [gear_offer(owned) for owned in player.backpack]
    for potion, count in player.potions_in_bag():
        rows.append(potion_offer(potion, 0, count))
    for row in rows:
        offer = side.find(row["kind"], row["key"])
        row["on_table"] = offer is not None
        row["count"] = offer.count if offer else 0
    return rows


def trade_payload(trade: Trade, player: Player) -> dict[str, Any]:
    """Стол целиком, уже разложенный на свою половину и чужую."""
    mine = trade.side(player.user_id)
    return {
        "id": trade.id,
        # По нему страница понимает, что стол поменялся, и гасит согласие
        "version": trade.version,
        "mine": side_payload(mine, player),
        "his": side_payload(trade.other(player.user_id), None),
        "max_items": MAX_ITEMS,
        "max_credits": player.credits,
        "basket": basket_payload(player, mine),
        # Обмен проходит, когда оба нажали. Пусто у обоих — нажимать не за что
        "waiting": mine.ready and not trade.other(player.user_id).ready,
    }


def invite_payload(invite: Invite | None, name: str = "") -> dict[str, Any]:
    if invite is None:
        return {}
    return {
        "from_id": invite.from_id,
        "from_name": invite.from_name,
        "to_id": invite.to_id,
        "name": name or invite.from_name,
        "seconds_left": invite.seconds_left(),
    }


async def build_trade(
    player: Player, service: TradeService | None, now: int | None = None
) -> dict[str, Any]:
    """Рынок целиком: толпа, приглашения и стол, если он открыт."""
    moment = now_ts() if now is None else now
    place = where_to(Service.TRADE)
    body: dict[str, Any] = {
        "credits": player.credits,
        "where": place.title if place else "Рынок",
        "max_items": MAX_ITEMS,
        "invite_seconds": INVITE_SECONDS,
        "crowd": [],
        "invite": {},
        "sent": {},
        "trade": {},
        "done": "",
    }
    if service is None:  # pragma: no cover - бот без службы обмена не живёт
        return body

    body["done"] = service.take_done(player.user_id)
    trade = service.trade_of(player.user_id)
    if trade is not None:
        trade = await service.alive(trade, moment)
    if trade is None and not body["done"]:
        body["done"] = service.take_done(player.user_id)
    if trade is not None:
        body["trade"] = trade_payload(trade, player)
        return body

    here = await service.crowd(player, moment)
    # Кто в сети — выше: с ним обмен состоится сейчас, а не когда-нибудь
    here.sort(key=lambda one: (not is_online(one.seen_at, moment), -one.seen_at))
    body["crowd"] = [neighbour_payload(one, service, moment) for one in here]

    body["invite"] = invite_payload(service.invite_to(player.user_id))
    sent = service.invite_from(player.user_id)
    if sent is not None:
        waiting = next(
            (one.nickname for one in here if one.user_id == sent.to_id),
            "боец",
        )
        body["sent"] = invite_payload(sent, waiting)
    return body


__all__ = [
    "basket_payload",
    "build_trade",
    "gear_offer",
    "invite_payload",
    "neighbour_payload",
    "offer_payload",
    "potion_offer",
    "side_payload",
    "trade_payload",
]
