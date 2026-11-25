"""Трансферный рынок школы: бойцов продают, меняют и доплачивают.

Отдельная служба, а не часть `bot.squad_service`: там действия хозяина
со своим отрядом, а здесь сделка двоих. У сделки свои правила и своя
главная забота — чтобы ни один боец не достался двоим и ни один не
пропал между карманами.

**Лот живёт, пока боец у хозяина.** Выставленный боец остаётся в
отряде и дерётся как обычно: продаётся не место, а человек. Поэтому
каждая сделка перед самым переводом ещё раз смотрит, у того ли он ещё
хозяина, — между «выставил» и «купил» проходят часы.

**Купить бойца выше себя нельзя.** Уровень бойца не поднимается выше
хозяйского, и сделка не должна уметь то, чего не умеет рост: иначе
третий уровень купил бы девятого и сразу получил бы бойца, которого
ему не вырастить.

**Вещи остаются у продавца.** За экипировку платил он, и уходить ей
вместе с бойцом незачем: продают человека, а не его рюкзак. Так же
устроено увольнение — вещи возвращаются хозяину, — и двум похожим
случаям незачем расходиться.

**Обмен — тот же лот, но ответом.** Покупатель кладёт своего бойца и
доплату, продавец соглашается или нет. Деньги не списываются заранее:
предложение может провисеть сутки, и платить за ожидание никто не
должен.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.health import now_ts
from bot.game.squad import SQUAD_LIMIT, can_hold
from bot.models import Player
from bot.squad_service import SquadError, fighters

logger = logging.getLogger(__name__)

# Сколько просят за бойца самое меньшее и самое большее. Нижняя граница
# отсекает лот за единицу — такой не продают, а передают, и для этого
# рынок не нужен; верхняя не даёт выставить число, которого не бывает
# ни у кого, и тем самым засорить витрину
MIN_PRICE = 100
MAX_PRICE = 1_000_000


class TransferError(Exception):
    """Отказ по сделке, который показывают игроку как есть."""


async def _free_fighter(db: Database, owner: Player, npc_id: int) -> dict:
    """Строка своего бойца, который сейчас ничем не занят.

    Занятого не продают и не меняют: он держит точку или лежит в
    больнице, и сделка увела бы его из-под обязательств, о которых
    вторая сторона не знает.
    """
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != owner.user_id:
        raise TransferError("Это не твой боец.")
    if row["hospital_at"]:
        raise TransferError("Этот боец в больнице. Сперва забери его.")
    if row["spot"]:
        raise TransferError("Он держит точку. Сперва сними его оттуда.")
    return row


async def put_up(
    db: Database, owner: Player, npc_id: int, price: int, now: int | None = None
) -> dict:
    """Выставить своего бойца на трансферный рынок."""
    moment = now_ts() if now is None else now
    await _free_fighter(db, owner, npc_id)
    if not MIN_PRICE <= price <= MAX_PRICE:
        raise TransferError(
            f"Цена бойца — от {MIN_PRICE} до {MAX_PRICE} 💰."
        )
    await db.put_transfer(npc_id, owner.user_id, price, moment)
    logger.info("Боец %s выставил %s за %s", owner.user_id, npc_id, price)
    return {"npc_id": npc_id, "price": price}


async def take_down(db: Database, owner: Player, npc_id: int) -> None:
    """Снять своего бойца с рынка вместе со всеми предложениями."""
    lot = await db.transfer_of(npc_id)
    if lot is None or lot["owner_id"] != owner.user_id:
        raise TransferError("Этот лот не твой.")
    await db.drop_transfer(npc_id)
    logger.info("Боец %s снял лот %s", owner.user_id, npc_id)


async def _hand_over(
    db: Database, npc_id: int, giver: Player, taker: Player
) -> Player:
    """Перевести бойца в другой отряд. Вещи остаются у прежнего хозяина."""
    npc = await db.get_player(npc_id)
    if npc is None:  # pragma: no cover - бойца удалили по ходу сделки
        raise TransferError("Этого бойца уже нет.")
    if npc.level > taker.level:
        raise TransferError(
            f"{npc.nickname} {npc.level} уровня, а ты {taker.level}: "
            "боец не бывает старше хозяина."
        )
    for item in await db.list_gear(npc_id):
        await db.hand_over_gear(item.id, npc_id, giver.user_id)
    await db.hand_over_squad(npc_id, taker.user_id)
    await db.drop_transfer(npc_id)
    await db.drop_bids_of(npc_id)
    return npc


async def buy(
    db: Database, buyer: Player, npc_id: int, now: int | None = None
) -> Player:
    """Выкупить бойца по цене лота.

    Хозяина проверяем здесь же, перед самым переводом: между «выставил»
    и «купил» проходят часы, и за это время бойца могли уволить,
    продать другому или поставить на точку.
    """
    moment = now_ts() if now is None else now
    lot = await db.transfer_of(npc_id)
    if lot is None:
        raise TransferError("Этого лота больше нет.")
    if lot["owner_id"] == buyer.user_id:
        raise TransferError("Это твой боец.")
    seller = await db.get_player(int(lot["owner_id"]))
    if seller is None:  # pragma: no cover - продавца удалили
        raise TransferError("Продавца больше нет в городе.")
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != seller.user_id:
        await db.drop_transfer(npc_id)
        raise TransferError("Этого бойца уже забрали.")
    if row["hospital_at"] or row["spot"]:
        raise TransferError("Боец сейчас занят. Загляни позже.")
    if not can_hold(len(await db.squad_of(buyer.user_id))):
        raise TransferError(
            f"В отряде уже {SQUAD_LIMIT} бойцов. Сперва освободи место."
        )
    price = int(lot["price"])
    if buyer.credits < price:
        raise TransferError(f"Этот боец стоит {price} 💰, а у тебя меньше.")

    npc = await _hand_over(db, npc_id, seller, buyer)
    buyer.credits -= price
    seller.credits += price
    await db.save_player(buyer)
    await db.save_player(seller)
    logger.info(
        "Боец %s купил %s у %s за %s", buyer.user_id, npc_id, seller.user_id, price
    )
    return npc


async def offer(
    db: Database,
    buyer: Player,
    npc_id: int,
    give_id: int = 0,
    extra: int = 0,
    now: int | None = None,
) -> int:
    """Предложить за лот своего бойца с доплатой — или одни деньги.

    Деньги не списываем: предложение может провисеть сутки, и платить
    за ожидание покупатель не должен. Хватит ли их в час ответа, решит
    сам ответ.
    """
    moment = now_ts() if now is None else now
    lot = await db.transfer_of(npc_id)
    if lot is None:
        raise TransferError("Этого лота больше нет.")
    if lot["owner_id"] == buyer.user_id:
        raise TransferError("Это твой боец.")
    if extra < 0:
        raise TransferError("Доплата не бывает отрицательной.")
    if give_id:
        await _free_fighter(db, buyer, give_id)
    elif not extra:
        raise TransferError("Пустое предложение: положи бойца или деньги.")

    bid = await db.add_bid(npc_id, buyer.user_id, give_id, extra, moment)
    logger.info(
        "Боец %s предложил за %s: свой %s и %s 💰",
        buyer.user_id, npc_id, give_id or "никого", extra,
    )
    return bid


async def accept(
    db: Database, seller: Player, bid_id: int, now: int | None = None
) -> dict:
    """Принять встречное предложение: бойцы меняются местами.

    Всё проверяется заново в этот самый миг: предложение могло
    пролежать сутки, и за это время у покупателя мог кончиться и боец,
    и кошелёк, и место в отряде.
    """
    moment = now_ts() if now is None else now
    bid = await db.bid_row(bid_id)
    if bid is None:
        raise TransferError("Этого предложения больше нет.")
    npc_id = int(bid["npc_id"])
    lot = await db.transfer_of(npc_id)
    if lot is None or lot["owner_id"] != seller.user_id:
        raise TransferError("Этот лот не твой.")
    buyer = await db.get_player(int(bid["buyer_id"]))
    if buyer is None:
        await db.drop_bid(bid_id)
        raise TransferError("Покупателя больше нет в городе.")

    extra = int(bid["extra"])
    give_id = int(bid["give_id"])
    if buyer.credits < extra:
        raise TransferError("У покупателя не хватает денег на доплату.")
    await _free_fighter(db, seller, npc_id)
    if give_id:
        await _free_fighter(db, buyer, give_id)

    # Места считаем до перевода и с учётом размена: отдающий бойца
    # освобождает своё же место, и запрещать ему обмен при полном
    # отряде было бы неправдой
    seller_room = len(await db.squad_of(seller.user_id)) - 1 + (1 if give_id else 0)
    buyer_room = len(await db.squad_of(buyer.user_id)) + 1 - (1 if give_id else 0)
    if seller_room > SQUAD_LIMIT or buyer_room > SQUAD_LIMIT:
        raise TransferError("После размена отряд не поместится.")

    mine = await _hand_over(db, npc_id, seller, buyer)
    theirs = None
    if give_id:
        theirs = await _hand_over(db, give_id, buyer, seller)
    buyer.credits -= extra
    seller.credits += extra
    await db.save_player(buyer)
    await db.save_player(seller)
    await db.drop_bid(bid_id)
    logger.info(
        "Размен: %s отдал %s, получил %s и %s 💰",
        seller.user_id, npc_id, give_id or "деньги", extra,
    )
    return {"gave": mine, "got": theirs, "extra": extra}


async def decline(db: Database, seller: Player, bid_id: int) -> None:
    """Отклонить предложение по своему лоту."""
    bid = await db.bid_row(bid_id)
    if bid is None:
        raise TransferError("Этого предложения больше нет.")
    lot = await db.transfer_of(int(bid["npc_id"]))
    if lot is None or lot["owner_id"] != seller.user_id:
        raise TransferError("Этот лот не твой.")
    await db.drop_bid(bid_id)


async def withdraw(db: Database, buyer: Player, bid_id: int) -> None:
    """Забрать своё предложение обратно."""
    bid = await db.bid_row(bid_id)
    if bid is None:
        raise TransferError("Этого предложения больше нет.")
    if int(bid["buyer_id"]) != buyer.user_id:
        raise TransferError("Это не твоё предложение.")
    await db.drop_bid(bid_id)


__all__ = [
    "MAX_PRICE",
    "MIN_PRICE",
    "TransferError",
    "accept",
    "buy",
    "decline",
    "offer",
    "put_up",
    "take_down",
    "withdraw",
]
