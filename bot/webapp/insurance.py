"""Страховая компания глазами мини-аппа.

Правил здесь нет: цену и срок держит `bot.game.insurance`, отказы —
`bot.insurance_service`. Здесь перевод в json и одна забота сверх
перевода — **сказать, по какому праву полис на руках**. Подписчику он
выписан подпиской и живёт ровно её срок; всем остальным — оплачен и живёт
месяц. На экране это две разные строки, а не одна с оговоркой.

Цена одна и та же для всех, включая подписчика: месяц он покупает не
вместо подписки, а после неё. Бесплатного месяца нет вовсе — он
складывался бы сам с собой сколько угодно раз.

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
    PRO_BENEFITS,
    TITLE,
    discounted,
)
from bot.game.injuries import HURT_PRICE, Hurt
from bot.game.locations import Service
from bot.insurance_service import price_for
from bot.webapp.bank import purse_payload
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
    full = price_for(player)
    price = player.price_here(full, moment, Service.INSURANCE)
    live = player.insured(moment)
    paper = policy_document(player, moment)
    by_pro = bool(paper.get("by_pro"))
    return {
        "credits": player.credits,
        "emoji": EMOJI,
        "title": TITLE,
        "issuer": INSURER,
        "covers": COVERS,
        "gives": list(PRO_BENEFITS if by_pro else BENEFITS),
        "note": NOTE,
        "days": POLICY_DAYS,
        "discount": round(HEAL_DISCOUNT * 100),
        "price": price,
        "full_price": full,
        "off": full - price,
        "purse": purse_payload(player, moment, Service.INSURANCE),
        "pro": player.is_pro(moment),
        # Полис держит подписка: покупать нечего, пока она жива
        "by_pro": by_pro,
        "affordable": player.can_afford(price, moment, Service.INSURANCE),
        "insured": live,
        # Документ целиком — тот же, что лежит в «Документах»: страница
        # рисует его одной и той же вёрсткой в двух местах
        "policy": paper,
        "action": "Продлить на месяц" if live else "Оформить полис",
        # Зачем подписчику покупать месяц, если полис у него и так есть:
        # без этой строки кнопка с ценой выглядит ошибкой
        "why": (
            "Полис держит подписка — до её последнего часа. Купленный месяц "
            "ляжет сверху и останется, когда подписка кончится."
            if by_pro
            else ""
        ),
        "prices": price_rows(),
        # Что сказать о продлении: строку сводит служба, но повод — здесь
        "auto_renew": bool(player.policy and player.policy.auto_renew),
        "said": "",
    }


__all__ = ["build_insurance", "price_rows"]
