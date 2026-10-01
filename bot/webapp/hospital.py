"""Больница глазами мини-аппа: две цены и полоса здоровья.

Правил здесь нет — прайс держит `bot.game.hospital`, а отказы
`bot.hospital_service`; тут перевод состояния в json, который умеет
нарисовать страница.

Кнопку гасит сервер, а не вёрстка: сколько именно дольют этому бойцу,
считается по его потолку здоровья, и страница такого не знает.

Цену лечения травмы сбивает полис, и на экран идут оба числа — со
скидкой и без. Одно число выглядело бы просто дешёвым лечением: по нему
не видно, что скидку дал документ, за который заплачено.
"""

from __future__ import annotations

from typing import Any

from bot.game.health import FULL_REGEN_SECONDS, now_ts
from bot.game.hospital import CURES
from bot.game.insurance import HEAL_DISCOUNT, saved
from bot.game.locations import Service
from bot.injury_service import cure_price
from bot.webapp.bank import purse_payload
from bot.models import Player


def cure_payload(cure, player: Player, moment: int) -> dict[str, Any]:
    """Строка прайса: почём, сколько дольют и что мешает."""
    healed = cure.healed(player.current_hp(moment), player.max_hp)
    price = player.price_here(cure.price, moment, Service.HEAL)
    return {
        "code": cure.code,
        "title": cure.title,
        "price": price,
        "full_price": cure.price,
        "off": cure.price - price,
        "note": cure.note,
        # Сколько здоровья этот приём даст именно этому бойцу. Ноль —
        # давать нечего: он уже целый
        "healed": healed,
        "affordable": player.can_afford(price, moment, Service.HEAL),
        "useful": healed > 0,
    }


def injury_row(player: Player, moment: int) -> dict[str, Any]:
    """Травма на приёме: что лечим, почём и сколько после этого лежать."""
    active = player.injury
    injury = active.injury if active else None
    if active is None or injury is None or not active.is_active(moment):
        return {}
    full = injury.hurt.price
    # Полис снимает восемьдесят процентов, карта — десять с остатка.
    # На экран идут оба числа: по одному не видно, кто сколько снял
    by_policy = cure_price(player, injury, moment)
    price = player.price_here(by_policy, moment, Service.HEAL)
    return {
        "code": injury.code,
        "title": injury.title,
        "hurt_title": injury.hurt.title,
        "text": active.describe(moment),
        "price": price,
        # Полная цена и сколько снял полис: без этих двух чисел скидка
        # выглядит просто дешёвым лечением, и полис незаметен
        "full_price": full,
        "insured": player.insured(moment),
        "saved": saved(full) if player.insured(moment) else 0,
        "discount": round(HEAL_DISCOUNT * 100),
        "by_policy": by_policy,
        "off": by_policy - price,
        "affordable": player.can_afford(price, moment, Service.HEAL),
        # После капельницы боец лежит уже минуты, а не часы
        "cure_minutes": injury.hurt.cure_seconds // 60,
        "crippled": player.crippled,
    }


def build_hospital(player: Player, now: int | None = None) -> dict[str, Any]:
    """Приёмный покой: счёт бойца, его здоровье и прайс."""
    moment = now_ts() if now is None else now
    current = player.current_hp(moment)
    return {
        "credits": player.credits,
        "purse": purse_payload(player, moment, Service.HEAL),
        # Травма лечится отдельно от здоровья: это другая беда и другая
        # цена. Пусто — лечить нечего
        "injury": injury_row(player, moment),
        "hp": {
            "current": current,
            "max": player.max_hp,
            "percent": round(player.hp_percent(moment) * 100),
            "regen_seconds": FULL_REGEN_SECONDS,
            "missing": max(0, player.max_hp - current),
        },
        "cures": [cure_payload(cure, player, moment) for cure in CURES],
    }


__all__ = ["build_hospital", "cure_payload", "injury_row"]
