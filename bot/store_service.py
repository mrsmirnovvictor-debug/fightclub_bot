"""Касса: счета в Telegram Stars, начисление кредитов и возвраты.

Всё, что связано с деньгами, держится на двух правилах.

Первое — идемпотентность. Telegram может прислать один и тот же платёж
дважды (переотправка апдейта, перезапуск бота на неподтверждённом
обновлении), поэтому кредиты начисляет только запись в журнал: не легла
строка — значит, платёж уже учтён, и второй раз ничего не капает.

Второе — возврат. Telegram требует, чтобы бот умел вернуть звёзды за
цифровой товар. Возвращаем, пока купленные кредиты целы: если боец уже
оделся на них, возвращать нечего — иначе кредиты можно было бы потратить,
а звёзды забрать назад.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import LabeledPrice, SuccessfulPayment

from bot.config import Config
from bot.database import Database
from bot.game.equipment import MAGIC_ITEMS, Item, OwnedItem, get_item
from bot.game.pro import BENEFITS, PRO_ITEM, PRO_LOOK
from bot.game.pro import EMOJI as PRO_EMOJI
from bot.game.pro import TITLE as PRO_TITLE
from bot.game.pro import ProOffer, paid_offer
from bot.game.store import (
    PACK_KIND,
    PACKS,
    PRO_KIND,
    RELIC_KIND,
    STARS,
    CreditPack,
    get_pack,
    parse_payload,
    payload_for,
    pro_payload,
    relic_payload,
)
from bot.pro_service import grant_pro

# Что вообще продаётся за звёзды: пачка кредитов, вещь мага или подписка
Goods = CreditPack | Item | ProOffer

logger = logging.getLogger(__name__)


class StoreError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class Grant:
    """Что случилось с оплатой: что купили, что начислили и не повтор ли это."""

    goods: Goods
    credits: int
    balance: int
    already: bool = False

    # Что пришло вместе с подпиской: клинок, образ и до какого часа она жива
    pro: object | None = None

    @property
    def label(self) -> str:
        """Как назвать покупку в ответе: у пачки свой ярлык, у вещи — значок."""
        if isinstance(self.goods, CreditPack):
            return self.goods.label
        if isinstance(self.goods, ProOffer):
            return f"{PRO_EMOJI} {PRO_TITLE}"
        return f"{self.goods.emoji} {self.goods.title}"

    @property
    def is_relic(self) -> bool:
        return isinstance(self.goods, Item)

    @property
    def is_pro(self) -> bool:
        return isinstance(self.goods, ProOffer)


class StoreService:
    """Счета, начисления и возвраты. Ничего не знает про интерфейс."""

    def __init__(self, bot: Bot, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config

    # ---------- счета ----------

    async def invoice_link(self, goods: Goods, user_id: int) -> str:
        """Ссылка на оплату — её открывает и мини-апп, и кнопка в чате."""
        label = self.label(goods)
        return await self.bot.create_invoice_link(
            title=label,
            description=self.description(goods),
            payload=self.payload(goods, user_id),
            currency=STARS,
            prices=[LabeledPrice(label=label, amount=goods.stars)],
        )

    @staticmethod
    def payload(goods: Goods, user_id: int) -> str:
        if isinstance(goods, CreditPack):
            return payload_for(goods, user_id)
        if isinstance(goods, ProOffer):
            return pro_payload(user_id)
        return relic_payload(goods.code, user_id)

    @staticmethod
    def label(goods: Goods) -> str:
        if isinstance(goods, CreditPack):
            return goods.label
        if isinstance(goods, ProOffer):
            return f"{PRO_EMOJI} {PRO_TITLE}"
        return f"{goods.emoji} {goods.title}"

    @staticmethod
    def description(goods: Goods) -> str:
        if isinstance(goods, ProOffer):
            return (
                f"Подписка клуба на {goods.term_text}: "
                + ", ".join(BENEFITS[:3]).lower()
                + "."
            )
        if isinstance(goods, Item):
            parts = [f"{goods.title} — вещь из лавки мага."]
            bonus = goods.describe_bonus()
            if bonus:
                parts.append(bonus + ".")
            parts.append(f"Нужен {goods.level_required} уровень.")
            return " ".join(parts)
        parts = [f"{goods.total} кредитов на счёт бойца."]
        if goods.bonus:
            parts.append(f"{goods.credits} + {goods.bonus} сверху.")
        if goods.note:
            parts.append(goods.note + ".")
        return " ".join(parts)

    @staticmethod
    def packs() -> tuple[CreditPack, ...]:
        return PACKS

    @staticmethod
    def relics() -> tuple[Item, ...]:
        return MAGIC_ITEMS

    # ---------- оплата ----------

    def check(self, payload: str) -> Goods:
        """Проверить счёт перед списанием: товар должен быть нашим и живым."""
        kind, code = parse_payload(payload)
        if kind == PACK_KIND:
            pack = get_pack(code)
            if pack is None:
                raise StoreError("Этой пачки больше нет в кассе.")
            return pack
        if kind == RELIC_KIND:
            item = get_item(code)
            if item is None or not item.is_magic or not item.on_sale:
                raise StoreError("Этого товара у мага больше нет.")
            return item
        if kind == PRO_KIND:
            # За звёзды подписку берут на обычных условиях всегда, даже пока
            # идёт акция: акция — это разовый бесплатный вход, а не скидка на
            # продление. Счёта на ноль звёзд Telegram и не примет.
            return paid_offer()
        raise StoreError("Этой пачки больше нет в кассе.")

    async def grant(self, user_id: int, payment: SuccessfulPayment) -> Grant:
        """Выдать оплаченное. Повторный платёж не выдаёт ничего.

        Порядок один на оба товара: сначала запись в журнал, и только если
        она легла — начисление. Не легла, значит этот платёж уже отработан.
        """
        goods = self.check(payment.invoice_payload)
        player = await self.db.get_player(user_id)
        if player is None:
            raise StoreError("Сначала заведи бойца: /start")

        credits = goods.total if isinstance(goods, CreditPack) else 0
        if isinstance(goods, CreditPack):
            code, kind = goods.code, "credits"
        elif isinstance(goods, ProOffer):
            code, kind = PRO_KIND, PRO_KIND
        else:
            code, kind = goods.code, RELIC_KIND
        fresh = await self.db.add_purchase(
            user_id=user_id,
            code=code,
            stars=payment.total_amount,
            credits=credits,
            charge_id=payment.telegram_payment_charge_id,
            kind=kind,
        )
        if not fresh:
            logger.info("Повторный платёж %s, товар уже выдан",
                        payment.telegram_payment_charge_id)
            return Grant(goods=goods, credits=0, balance=player.credits, already=True)

        pro = None
        if isinstance(goods, CreditPack):
            player.grant_credits(goods.total)
            await self.db.save_player(player)
        elif isinstance(goods, ProOffer):
            pro = await grant_pro(self.db, player, goods)
        else:
            # Вещь падает в рюкзак как обычная покупка: надеть её можно,
            # когда боец дорастёт до требований.
            await self.db.add_gear(user_id, goods.code)
        logger.info(
            "Оплата: боец %s, товар %s, %s ⭐",
            user_id,
            code,
            payment.total_amount,
        )
        return Grant(
            goods=goods, credits=credits, balance=player.credits, pro=pro
        )

    # ---------- возврат ----------

    async def refund(self, user_id: int, charge_id: str = "") -> dict:
        """Вернуть звёзды за покупку и забрать то, что за неё выдали."""
        row = await self._refundable(user_id, charge_id)
        player = await self.db.get_player(user_id)
        if player is None:  # pragma: no cover - персонажа удалили
            raise StoreError("Бойца больше нет — возвращать некому.")

        is_pro = row["kind"] == PRO_KIND
        relic = (
            self._relic_to_take_back(row, player)
            if row["kind"] == RELIC_KIND
            else None
        )
        if relic is None and not is_pro and player.credits < row["credits"]:
            raise StoreError(
                "Кредиты уже потрачены — вернуть звёзды нельзя. "
                f"На счету {player.credits}, в покупке было {row['credits']}."
            )

        await self.bot.refund_star_payment(
            user_id=user_id, telegram_payment_charge_id=row["charge_id"]
        )
        if relic is not None:
            await self.db.delete_gear(relic.id)
        elif is_pro:
            await self._revoke_pro(player)
        else:
            player.grant_credits(-row["credits"])
            await self.db.save_player(player)
        await self.db.mark_refunded(row["charge_id"])
        logger.info("Возврат: боец %s, платёж %s", user_id, row["charge_id"])
        return row

    async def _revoke_pro(self, player) -> None:
        """Снять подписку целиком: срок, клинок и образ.

        Клинок с образом называются вечными, но вечны они у того, кто за них
        заплатил. Забрал звёзды — вернул и то, что они принесли.
        """
        player.pro_until = 0
        if player.look == PRO_LOOK:
            player.look = ""
        blade = next(
            (owned for owned in player.gear if owned.code == PRO_ITEM), None
        )
        if blade is not None:
            await self.db.delete_gear(blade.id)
            player.drop_gear(blade)
        await self.db.drop_look(player.user_id, PRO_LOOK)
        await self.db.save_player(player)

    @staticmethod
    def _relic_to_take_back(row: dict, player) -> "OwnedItem":
        """Экземпляр вещи, который уйдёт обратно к магу.

        Вещь должна быть цела: рассыпавшуюся в труху не вернёшь, иначе за
        звёзды можно было бы взять меч, износить его и забрать деньги.
        """
        item = get_item(row["code"])
        owned = next(
            (
                thing
                for thing in sorted(player.gear, key=lambda g: g.wear)
                if thing.code == row["code"]
            ),
            None,
        )
        if owned is None:
            title = item.title if item else row["code"]
            raise StoreError(
                f"«{title}» у тебя больше нет — возвращать звёзды не за что."
            )
        return owned

    async def _refundable(self, user_id: int, charge_id: str) -> dict:
        """Найти покупку, за которую ещё можно вернуть звёзды."""
        if charge_id:
            row = await self.db.get_purchase(charge_id)
            if row is None or row["user_id"] != user_id:
                raise StoreError("Такой покупки за тобой не числится.")
        else:
            rows = [
                item
                for item in await self.db.purchases_of(user_id)
                if not item["refunded_at"]
            ]
            if not rows:
                raise StoreError("Возвращать нечего: покупок за тобой нет.")
            row = rows[0]
        if row["refunded_at"]:
            raise StoreError("За эту покупку звёзды уже вернули.")
        if row["kind"] == "gift":
            raise StoreError("Это подарок клуба, а не покупка — возвращать нечего.")
        return row

    async def history(self, user_id: int, limit: int = 10) -> list[dict]:
        return await self.db.purchases_of(user_id, limit)


def spent_stars(rows: list[dict]) -> int:
    """Сколько звёзд боец занёс в клуб, не считая возвращённых."""
    return sum(row["stars"] for row in rows if not row["refunded_at"])


__all__ = ["Grant", "StoreError", "StoreService", "spent_stars"]
