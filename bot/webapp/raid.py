"""Рейд глазами мини-аппа: то же состояние, что в ветке, только данными.

Правил здесь нет: волны считает `RaidService`, а тут перевод живого рейда
в json, который умеет нарисовать страница. Экран опрашивает ручку раз в
пару секунд, поэтому ответ всегда полный — по нему видно, что рисовать:
сбор отряда, идущую волну или итог.
"""

from __future__ import annotations

from typing import Any

from bot.game.classes import ALL_ZONES, BLOCK_WIDTH
from bot.game.combat import (
    Fighter,
    total_accuracy,
    total_anticrit,
    total_block_hold,
    total_counter,
    total_crit,
    total_dodge,
)
from bot.game.equipment import LEFT_SLOTS, RIGHT_SLOTS, get_item
from bot.game.raid import (
    BOSS_HP_SHARE,
    LEVELS_ABOVE,
    MAX_PARTY,
    MIN_PARTY,
    Boss,
    CELLAR_BOSS,
    boss_fighter,
)
from bot.models import Player
from bot.raid_service import RaidLobby, RaidService, RaidSession
from bot.webapp.card import slot_payload
from bot.webapp.fight import ATTACK_BUTTONS, BLOCK_BUTTONS, block_buttons, hands_payload


def boss_payload(session: RaidSession) -> dict[str, Any]:
    enemy = session.enemy
    return {
        "code": session.boss.code,
        "title": enemy.name,
        "emoji": session.boss.emoji,
        "image": session.boss.image,
        "level": enemy.level,
        "hp": enemy.hp,
        "max_hp": enemy.max_hp,
        "percent": round(enemy.hp_percent * 100),
        "weapon": enemy.weapon,
    }


def member_payload(session: RaidSession, user_id: int, viewer_id: int) -> dict[str, Any]:
    fighter = session.fighters[user_id]
    return {
        "user_id": user_id,
        "name": fighter.name,
        "level": fighter.level,
        "emoji": fighter.fclass.emoji,
        "hp": fighter.hp,
        "max_hp": fighter.max_hp,
        "percent": round(fighter.hp_percent * 100),
        "damage_dealt": fighter.damage_dealt,
        "alive": fighter.alive,
        # Отработал в этой волне: ждать его больше не надо
        "acted": user_id in session.acted,
        "you": user_id == viewer_id,
    }


def boss_card(enemy: Fighter, boss: Boss, live: bool) -> dict[str, Any]:
    """Всё про босса, что показывает кнопка «i».

    `live` — это настоящий босс идущего рейда. Иначе прикидка: каким он
    выйдет к бойцу, который смотрит, если тот соберёт отряд прямо сейчас.
    """
    derived, equipment = enemy.derived, enemy.equipment
    return {
        "code": boss.code,
        "title": boss.title,
        # Кукла босса собирается тем же кодом, что и карточка бойца: те же
        # слоты, те же подложки под пустыми, тот же аватар в середине
        "name": boss.title,
        "avatar": {"url": boss.image, "emoji": boss.emoji},
        "slots": {
            "left": [
                slot_payload(equipment, slot, enemy.fclass) for slot in LEFT_SLOTS
            ],
            "right": [
                slot_payload(equipment, slot, enemy.fclass) for slot in RIGHT_SLOTS
            ],
        },
        "emoji": boss.emoji,
        "image": boss.image,
        "tagline": boss.tagline,
        "live": live,
        "levels_above": LEVELS_ABOVE,
        "level": enemy.level,
        "fclass": enemy.fclass.title,
        "fclass_emoji": enemy.fclass.emoji,
        "max_hp": enemy.max_hp,
        "weapon": equipment.weapon_title or enemy.weapon,
        "weapon_icon": equipment.weapon_icon,
        "damage": [derived.damage_min, derived.damage_max],
        "stats": {
            "strength": enemy.stats.strength,
            "agility": enemy.stats.agility,
            "intuition": enemy.stats.intuition,
            "endurance": enemy.stats.endurance,
        },
        # Проценты считаем той же арифметикой, что и ринг: в карточке босса
        # стоит ровно то, с чем он выйдет драться
        "combat": {
            "crit_chance": round(total_crit(derived.crit_chance, equipment.crit) * 100),
            "crit_power": derived.crit_power,
            "anticrit": round(
                total_anticrit(derived.anticrit, equipment.anticrit) * 100
            ),
            "dodge_chance": round(
                total_dodge(derived.dodge_chance, equipment.dodge) * 100
            ),
            "accuracy": round(total_accuracy(derived.accuracy, equipment.accuracy) * 100),
            "counter_chance": round(
                total_counter(derived.counter_chance, equipment.counter) * 100
            ),
            "resist": round(derived.resist * 100),
            "penetration": round(derived.penetration * 100),
            "block_hold": round(total_block_hold(derived.block_hold) * 100),
        },
        "armor": [
            {
                "zone": zone.value,
                "title": zone.title.capitalize(),
                "emoji": zone.emoji,
                "min": low,
                "max": high,
            }
            for zone, (low, high) in (
                (zone, equipment.armor_range(zone)) for zone in ALL_ZONES
            )
        ],
        "kit": [
            {"slot": slot.value, "title": owned.title, "emoji": owned.emoji}
            for slot, owned in equipment.items.items()
        ],
    }


def lobby_payload(
    lobby: RaidLobby, viewer_id: int, timeout: int = 0
) -> dict[str, Any]:
    return {
        "id": lobby.id,
        "size": lobby.size,
        "total": lobby.total,
        "mine": lobby.opener_id == viewer_id,
        "joined": viewer_id in lobby.members,
        "in_app": lobby.chat_id is None,
        # Сколько ещё ждут отставших. Страница тикает сама, а сервер
        # поправляет её на каждом опросе — часы у всех одни.
        "seconds_left": lobby.seconds_left(timeout),
        "timeout": timeout,
        # Отряд уже боеспособен: созвавший может не ждать отсчёта
        "can_start": lobby.can_start,
        "boss": {
            "code": lobby.boss.code,
            "title": lobby.boss.title,
            "emoji": lobby.boss.emoji,
            "image": lobby.boss.image,
            "tagline": lobby.boss.tagline,
        },
        "members": [
            {"user_id": user_id, "name": name, "level": lobby.levels.get(user_id, 1)}
            for user_id, name in lobby.members.items()
        ],
    }


def raid_payload(session: RaidSession, viewer_id: int) -> dict[str, Any]:
    """Панель рейда: босс, отряд, что уже нажато и чем всё кончилось."""
    mine = session.choices.get(viewer_id)
    fighter = session.fighters.get(viewer_id)
    return {
        # Набор кнопок у каждого свой: второе оружие даёт второй столбец
        # ударов, щит — блок в три зоны
        "hands": hands_payload(fighter),
        "blocks": block_buttons(fighter.block_width if fighter else BLOCK_WIDTH),
        "id": session.id,
        "wave": session.wave,
        "in_app": session.chat_id is None,
        "resting": session.resting,
        "finished": session.finished,
        "summary": session.summary,
        "boss": boss_payload(session),
        "party": [
            member_payload(session, user_id, viewer_id) for user_id in session.fighters
        ],
        "yours": viewer_id in session.fighters,
        "alive": bool(fighter and fighter.alive),
        # Отработал в этой волне — кнопки прячем до следующей
        "acted": viewer_id in session.acted,
        "chosen": {
            "attacks": {
                str(hand): zone.value
                for hand, zone in (mine.attacks.items() if mine else ())
            },
            "attack": mine.attack.value if mine and mine.attack else None,
            "block": mine.block[0].value if mine and mine.block else None,
        },
        "log": session.rounds,
    }


def build_raid(
    player: Player, service: RaidService | None, timeout: int = 0
) -> dict[str, Any]:
    """Всё, что нужно разделу «Рейд», одним ответом."""
    body: dict[str, Any] = {
        "attacks": [dict(row) for row in ATTACK_BUTTONS],
        "blocks": [dict(row) for row in BLOCK_BUTTONS],
        "min_party": MIN_PARTY,
        "max_party": MAX_PARTY,
        "can_fight": player.can_fight(),
        "raid": None,
        "lobby": None,
        "lobbies": [],
        # Каким босс выйдет на этого бойца, если он соберёт отряд сейчас.
        # Здоровье тут за одного: с каждым лишним бойцом он крепче.
        "boss": boss_card(
            boss_fighter(CELLAR_BOSS, [player.level], BOSS_HP_SHARE),
            CELLAR_BOSS,
            live=False,
        ),
    }
    if service is None:  # pragma: no cover - бот без рейдов не живёт
        return body

    session = service.raid_of_user(player.user_id) or service.result_of_user(
        player.user_id
    )
    if session is not None:
        body["raid"] = raid_payload(session, player.user_id)
        body["boss"] = boss_card(session.enemy, session.boss, live=True)
        return body

    own = service.lobby_of_user(player.user_id)
    if own is not None:
        body["lobby"] = lobby_payload(own, player.user_id, timeout)
    body["lobbies"] = [
        lobby_payload(lobby, player.user_id, timeout)
        for lobby in service.open_lobbies()
        if player.user_id not in lobby.members
    ]
    return body


# Как исход рейда называется в списке боёв: там важно не «босс повержен», а
# что вышло у тебя — рядом с победами и поражениями в дуэлях
HISTORY_TITLES = {"win": "Победа", "draw": "Ничья", "loss": "Поражение"}
HISTORY_MARKS = {"win": "🏆", "draw": "🤝", "loss": "❌"}


def raid_row(row: dict[str, Any]) -> dict[str, Any]:
    """Строка истории рейдов: с кем ходили, чем кончилось и что унесли."""
    from bot.game.raid import RAID_END_TITLES, RaidEnd, get_boss

    end = RaidEnd(row["outcome"])
    boss = get_boss(row["boss"])
    prize = get_item(row["prize"]) if row["prize"] else None
    allies = row.get("allies") or ""
    with_whom = f" (с {allies})" if allies else ""
    return {
        "kind": "raid",
        "id": row["id"],
        "boss": boss.title,
        "boss_emoji": boss.emoji,
        "emoji": HISTORY_MARKS[end.value],
        "result": end.value,
        "result_title": HISTORY_TITLES[end.value],
        # «Поражение (с Марлой) — рейд против Босса Подвала»
        "caption": f"{HISTORY_TITLES[end.value]}{with_whom} — рейд против {boss.whom}",
        "verdict": RAID_END_TITLES[end.value],
        "allies": allies,
        "boss_level": row["boss_level"],
        "waves": row["waves"],
        "damage": row["damage"],
        "alive": bool(row["alive"]),
        "prize": prize.title if prize else None,
        "created_at": row["created_at"],
        "date": (row["created_at"] or "")[:10],
    }


__all__ = ["build_raid", "lobby_payload", "raid_payload", "raid_row"]
