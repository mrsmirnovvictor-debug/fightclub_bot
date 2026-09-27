"""Страховая компания глазами мини-аппа.

Правил здесь нет: цену и срок держит `bot.game.insurance`, отказы —
`bot.insurance_service`. Здесь перевод в json и одна забота сверх
перевода — **на экране всегда две цены**: обычная и та, по которой полис
достанется этому бойцу. Подписчику он бесплатен, и цифра «300», рядом с
которой написано «бесплатно», объясняет, за что заплачена подписка,
лучше любой подсказки.

Кнопка называется по делу: полиса нет — «Оформить», полис жив —
«Продлить на месяц». Одно слово на оба случая пришлось бы выбирать между
неправдой в первом и во втором.
"""

from __future__ import annotations

from typing import Any

from bot.game.health import now_ts
from bot.game.insurance import (
    BENEFITS,
    COVERS,
    EMOJI,
    HEAL_DISCOUNT,
    INSURER,
    NOTE,
    POLICY_DAYS,
    POLICY_PRICE,
    TITLE,
    discounted,
)
from bot.game.injuries import HURT_PRICE, Hurt
from bot.insurance_service import price_for
from bot.models import Player
from bot.webapp.documents import policy_document

# Тяжести по возрастанию — так же, как их показывает прайс больницы
HURT_ORDER: tuple[Hurt, ...] = (Hurt.LIGHT, Hurt.MEDIUM, Hurt.HEAVY)


def price_rows() -> list[dict[str, Any]]:
    """Во что превращается прайс больницы по полису — построчно.

    Проценты словами убеждают хуже чисел: «80% скидка» и «300 → 60» про
    одно и то же, но второе сразу говорит, со скольких походов в больницу
    полис себя окупит.
    """
    return [
        {
            "hurt": hurt.value,
            "title": hurt.title,
            "full": HURT_PRICE[hurt],
            "price": discounted(HURT_PRICE[hurt]),
        }
        for hurt in HURT_ORDER
    ]


def build_insurance(player: Player, now: int | None = None) -> dict[str, Any]:
    """Прилавок страховой: полис, его цена и что он даёт."""
    moment = now_ts() if now is None else now
    price = price_for(player)
    live = player.insured(moment)
    return {
        "credits": player.credits,
        "emoji": EMOJI,
        "title": TITLE,
        "issuer": INSURER,
        "covers": COVERS,
        "gives": list(BENEFITS),
        "note": NOTE,
        "days": POLICY_DAYS,
        "discount": round(HEAL_DISCOUNT * 100),
        # Полная цена и цена для этого бойца: подписчику вторая нулевая
        "full_price": POLICY_PRICE,
        "price": price,
        "free": price == 0,
        "pro": player.is_pro(),
        "affordable": player.can_afford(price),
        "insured": live,
        # Документ целиком — тот же, что лежит в «Документах»: страница
        # рисует его одной и той же вёрсткой в двух местах
        "policy": policy_document(player, moment),
        "action": "Продлить на месяц" if live else "Оформить полис",
        "prices": price_rows(),
        # Что сказать о продлении: строку сводит служба, но повод — здесь
        "auto_renew": bool(player.policy and player.policy.auto_renew),
        "said": "",
    }


__all__ = ["build_insurance", "price_rows"]
