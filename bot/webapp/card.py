"""Сборка данных карточки персонажа для мини-аппа."""

from __future__ import annotations

import time
from datetime import datetime

from bot.game.classes import ALL_STATS, Stats
from bot.game.economy import MAX_LEVEL, MICRO_UPS_PER_LEVEL
from bot.game.equipment import LEFT_SLOTS, RIGHT_SLOTS, Equipment, Slot
from bot.game.health import FULL_REGEN_SECONDS, HealthState, format_duration
from bot.game.stats import derive
from bot.models import Player
from bot.webapp.auth import sign_avatar

# Ссылка на аватар живёт час — столько же, сколько открытая карточка
AVATAR_TTL = 60 * 60

STATE_COLORS = {
    HealthState.HURT: "red",
    HealthState.RECOVERING: "yellow",
    HealthState.READY: "green",
}


def format_birthday(created_at: str | None) -> str:
    """«2013-10-26 22:31:00» → «26.10.13 22:31»."""
    if not created_at:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(created_at[:19], fmt).strftime("%d.%m.%y %H:%M")
        except ValueError:
            continue
    return created_at  # pragma: no cover - формат из будущей версии


def slot_payload(equipment: Equipment, slot: Slot) -> dict:
    item = equipment.get(slot)
    return {
        "slot": slot.value,
        "title": slot.title,
        "placeholder": slot.emoji,
        "item": None
        if item is None
        else {
            "code": item.code,
            "title": item.title,
            "icon": item.emoji,
            "bonus": item.describe_bonus(),
        },
    }


def stats_payload(base: Stats, bonus: Stats) -> list[dict]:
    return [
        {
            "code": stat.value,
            "title": stat.title.capitalize(),
            "emoji": stat.emoji,
            "base": base.get(stat),
            "bonus": bonus.get(stat),
            "total": base.get(stat) + bonus.get(stat),
        }
        for stat in ALL_STATS
    ]


def build_card(
    player: Player,
    bot_token: str,
    viewer_id: int | None = None,
    now: int | None = None,
) -> dict:
    """Всё, что рисует карточка: имя, здоровье, слоты, характеристики, история."""
    moment = int(time.time()) if now is None else now
    equipment = player.equipment
    fclass = player.fclass
    derived = derive(fclass, player.stats, player.level, equipment.hp_bonus)

    current_hp = player.current_hp(moment)
    state = player.health_state(moment)
    ready_in = player.seconds_until_ready(moment)
    full_in = player.seconds_until_full(moment)

    avatar_url = None
    if player.avatar_file_id:
        expires = moment + AVATAR_TTL
        token = sign_avatar(player.user_id, bot_token, expires)
        avatar_url = f"avatar/{player.user_id}?expires={expires}&token={token}"

    return {
        "user_id": player.user_id,
        "name": player.nickname,
        "level": player.level,
        "is_self": viewer_id == player.user_id,
        "fclass": {
            "code": fclass.code,
            "title": fclass.title,
            "emoji": fclass.emoji,
            "tagline": fclass.tagline,
        },
        "avatar": {"emoji": player.avatar, "url": avatar_url},
        "hp": {
            "current": current_hp,
            "max": derived.max_hp,
            "percent": round(player.hp_percent(moment) * 100),
            "state": state.value,
            "state_title": state.title,
            "color": STATE_COLORS[state],
            "can_fight": state.can_fight,
            "ready_in": ready_in,
            "ready_in_text": format_duration(ready_in) if ready_in else "",
            "full_in": full_in,
            "regen_seconds": FULL_REGEN_SECONDS,
            "full_in_text": format_duration(full_in) if full_in else "",
        },
        "stats": stats_payload(player.base_stats, equipment.bonus),
        "slots": {
            "left": [slot_payload(equipment, slot) for slot in LEFT_SLOTS],
            "right": [slot_payload(equipment, slot) for slot in RIGHT_SLOTS],
        },
        "city": player.city,
        "progress": {
            "exp": player.exp,
            "exp_needed": player.exp_needed,
            "total_exp": player.total_exp,
            "micro_ups": player.micro_ups,
            "ups_per_level": MICRO_UPS_PER_LEVEL,
            "exp_to_next_up": player.exp_to_next_up,
            "capped": player.at_max_level,
            "max_level": MAX_LEVEL,
            "free_points": player.free_points,
        },
        "record": {
            "wins": player.wins,
            "losses": player.losses,
            "draws": player.draws,
            "rating": player.rating,
            "credits": player.credits,
        },
        "birthplace": player.home,
        "birthday": format_birthday(player.created_at),
        "combat": {
            "damage_min": derived.damage_min,
            "damage_max": derived.damage_max,
            "crit_chance": round(derived.crit_chance * 100),
            "dodge_chance": round(derived.dodge_chance * 100),
            "block_zones": derived.block_zones,
        },
    }
