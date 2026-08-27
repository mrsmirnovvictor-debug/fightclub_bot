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
from bot.game.store import PACKS, STARS, CreditPack, get_pack, payload_for

logger = logging.getLogger(__name__)


class StoreError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class Grant:
    """Что случилось с оплатой: пачка, сколько начислили и не повтор ли это."""

    pack: CreditPack
    credits: int
    balance: int
    already: bool = False


class StoreService:
    """Счета, начисления и возвраты. Ничего не знает про интерфейс."""

    def __init__(self, bot: Bot, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config

    # ---------- счета ----------

    async def invoice_link(self, pack: CreditPack, user_id: int) -> str:
        """Ссылка на оплату — её открывает и мини-апп, и кнопка в чате."""
        return await self.bot.create_invoice_link(
            title=pack.label,
            description=self.description(pack),
            payload=payload_for(pack, user_id),
            currency=STARS,
            prices=[LabeledPrice(label=pack.label, amount=pack.stars)],
        )

    @staticmethod
    def description(pack: CreditPack) -> str:
        parts = [f"{pack.total} кредитов на счёт бойца."]
        if pack.bonus:
            parts.append(f"{pack.credits} + {pack.bonus} сверху.")
        if pack.note:
            parts.append(pack.note + ".")
        return " ".join(parts)

    @staticmethod
    def packs() -> tuple[CreditPack, ...]:
        return PACKS

    # ---------- оплата ----------

    def check(self, payload: str) -> CreditPack:
        """Проверить счёт перед списанием: пачка должна быть нашей и живой."""
        parts = payload.split(":")
        pack = get_pack(parts[1]) if len(parts) >= 2 and parts[0] == "pack" else None
        if pack is None:
            raise StoreError("Этой пачки больше нет в кассе.")
        return pack

    async def grant(self, user_id: int, payment: SuccessfulPayment) -> Grant:
        """Начислить кредиты за оплату. Повторный платёж не начисляет ничего."""
        pack = self.check(payment.invoice_payload)
        player = await self.db.get_player(user_id)
        if player is None:
            raise StoreError("Сначала заведи бойца: /start")

        fresh = await self.db.add_purchase(
            user_id=user_id,
            code=pack.code,
            stars=payment.total_amount,
            credits=pack.total,
            charge_id=payment.telegram_payment_charge_id,
        )
        if not fresh:
            logger.info("Повторный платёж %s, кредиты уже начислены",
                        payment.telegram_payment_charge_id)
            return Grant(pack=pack, credits=0, balance=player.credits, already=True)

        player.grant_credits(pack.total)
        await self.db.save_player(player)
        logger.info(
            "Оплата: боец %s, пачка %s, %s ⭐ → %s кр.",
            user_id,
            pack.code,
            payment.total_amount,
            pack.total,
        )
        return Grant(pack=pack, credits=pack.total, balance=player.credits)

    # ---------- возврат ----------

    async def refund(self, user_id: int, charge_id: str = "") -> dict:
        """Вернуть звёзды за покупку и снять начисленные кредиты."""
        row = await self._refundable(user_id, charge_id)
        player = await self.db.get_player(user_id)
        if player is None:  # pragma: no cover - персонажа удалили
            raise StoreError("Бойца больше нет — возвращать некому.")
        if player.credits < row["credits"]:
            raise StoreError(
                "Кредиты уже потрачены — вернуть звёзды нельзя. "
                f"На счету {player.credits}, в покупке было {row['credits']}."
            )

        await self.bot.refund_star_payment(
            user_id=user_id, telegram_payment_charge_id=row["charge_id"]
        )
        player.grant_credits(-row["credits"])
        await self.db.save_player(player)
        await self.db.mark_refunded(row["charge_id"])
        logger.info("Возврат: боец %s, платёж %s", user_id, row["charge_id"])
        return row

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
        return row

    async def history(self, user_id: int, limit: int = 10) -> list[dict]:
        return await self.db.purchases_of(user_id, limit)


def spent_stars(rows: list[dict]) -> int:
    """Сколько звёзд боец занёс в клуб, не считая возвращённых."""
    return sum(row["stars"] for row in rows if not row["refunded_at"])


__all__ = ["Grant", "StoreError", "StoreService", "spent_stars"]
