"""Больница глазами мини-аппа: две цены и полоса здоровья.

Правил здесь нет — прайс держит `bot.game.hospital`, а отказы
`bot.hospital_service`; тут перевод состояния в json, который умеет
нарисовать страница.

Кнопку гасит сервер, а не вёрстка: сколько именно дольют этому бойцу,
считается по его потолку здоровья, и страница такого не знает.
"""

from __future__ import annotations

from typing import Any

from bot.game.health import FULL_REGEN_SECONDS, now_ts
from bot.game.hospital import CURES
from bot.models import Player


def cure_payload(cure, player: Player, moment: int) -> dict[str, Any]:
    """Строка прайса: почём, сколько дольют и что мешает."""
    healed = cure.healed(player.current_hp(moment), player.max_hp)
    return {
        "code": cure.code,
        "title": cure.title,
        "price": cure.price,
        "note": cure.note,
        # Сколько здоровья этот приём даст именно этому бойцу. Ноль —
        # давать нечего: он уже целый
        "healed": healed,
        "affordable": player.can_afford(cure.price),
        "useful": healed > 0,
    }


def build_hospital(player: Player, now: int | None = None) -> dict[str, Any]:
    """Приёмный покой: счёт бойца, его здоровье и прайс."""
    moment = now_ts() if now is None else now
    current = player.current_hp(moment)
    return {
        "credits": player.credits,
        "hp": {
            "current": current,
            "max": player.max_hp,
            "percent": round(player.hp_percent(moment) * 100),
            "regen_seconds": FULL_REGEN_SECONDS,
            "missing": max(0, player.max_hp - current),
        },
        "cures": [cure_payload(cure, player, moment) for cure in CURES],
    }


__all__ = ["build_hospital", "cure_payload"]
