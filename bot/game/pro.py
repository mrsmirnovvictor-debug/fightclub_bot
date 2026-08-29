"""Подписка PRO: что она даёт, сколько стоит и сколько держится.

Подписка — единственное в клубе, что продаётся на время. Всё остальное за
звёзды покупается навсегда, поэтому её правила собраны отдельно и целиком
здесь: срок, цена, акция и список того, что боец получает.

Две вещи из этого списка остаются у бойца навсегда, даже когда подписка
кончится: клинок ассасина и его образ. Это осознанно — за них заплатили
один раз, отбирать их вместе со сроком было бы нечестно. Кончается только
то, что действительно про подписку: полуторный опыт и значок у имени.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from bot.game.art import MAGIC

# Значок у имени: он висит везде, где боец назван по имени
PRO_BADGE = "💎"

DAY = 24 * 60 * 60

# Обычные условия
PRO_STARS = 250
PRO_DAYS = 30

# Опыт за бой идёт с полуторным множителем
PRO_EXP_SHARE = 1.5

# Что приходит вместе с подпиской и остаётся навсегда
PRO_ITEM = "hidden_blade"
PRO_LOOK = "assassin"

# ---------- акция ----------
#
# До 1 сентября подписка достаётся даром, но на неделю вместо месяца. Час
# указан в UTC: полночь первого сентября по Москве — это 21:00 UTC 31 августа.
PROMO_UNTIL = datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc)
PROMO_STARS = 0
PROMO_DAYS = 7


@dataclass(frozen=True)
class ProOffer:
    """Условия, на которых подписку берут прямо сейчас."""

    stars: int
    days: int
    promo: bool = False

    @property
    def free(self) -> bool:
        """Даром: счёт в звёздах выставлять не за что."""
        return self.stars <= 0

    @property
    def seconds(self) -> int:
        return self.days * DAY

    @property
    def price_text(self) -> str:
        return "бесплатно" if self.free else f"{self.stars} ⭐"

    @property
    def term_text(self) -> str:
        return f"{self.days} дней" if self.days != 30 else "месяц"


def promo_is_on(now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    return moment < PROMO_UNTIL


def current_offer(now: datetime | None = None) -> ProOffer:
    """Цена и срок на этот момент. Идёт акция — даром и на неделю."""
    if promo_is_on(now):
        return ProOffer(stars=PROMO_STARS, days=PROMO_DAYS, promo=True)
    return ProOffer(stars=PRO_STARS, days=PRO_DAYS)


TITLE = "Подписка PRO"
EMOJI = "💎"
IMAGE = f"{MAGIC}/pro.jpeg"

# Что боец получает. Порядок тот же, в каком это показано на карточке товара.
BENEFITS: tuple[str, ...] = (
    "Полуторный опыт за каждый бой",
    "Клинок ассасина в инвентарь — навсегда",
    "Образ ассасина в гардероб — навсегда",
    f"Значок {PRO_BADGE} у имени, пока подписка жива",
)

NOTE = (
    "Клинок и образ остаются у бойца навсегда, даже когда срок выйдет. "
    "Кончаются только полуторный опыт и значок."
)


def promo_note(offer: ProOffer) -> str:
    """Строка про акцию — пусто, когда акции нет."""
    if not offer.promo:
        return ""
    return (
        f"🔥 До 1 сентября подписка достаётся даром — на {offer.days} дней "
        f"вместо месяца. Дальше {PRO_STARS} ⭐ за {PRO_DAYS} дней."
    )


__all__ = [
    "BENEFITS",
    "DAY",
    "EMOJI",
    "IMAGE",
    "NOTE",
    "PROMO_DAYS",
    "PROMO_STARS",
    "PROMO_UNTIL",
    "PRO_BADGE",
    "PRO_DAYS",
    "PRO_EXP_SHARE",
    "PRO_ITEM",
    "PRO_LOOK",
    "PRO_STARS",
    "TITLE",
    "ProOffer",
    "current_offer",
    "promo_is_on",
    "promo_note",
]
