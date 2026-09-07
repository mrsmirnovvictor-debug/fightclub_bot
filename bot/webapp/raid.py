"""Рейд глазами мини-аппа: то же состояние, что в ветке, только данными.

Правил здесь нет: волны считает `RaidService`, а тут перевод живого рейда
в json, который умеет нарисовать страница. Экран опрашивает ручку раз в
пару секунд, поэтому ответ всегда полный — по нему видно, что рисовать:
сбор отряда, идущую волну или итог.
"""

from __future__ import annotations

from typing import Any

from bot.game.equipment import get_item
from bot.game.raid import MAX_PARTY, MIN_PARTY
from bot.models import Player
from bot.raid_service import RaidLobby, RaidService, RaidSession
from bot.webapp.fight import ATTACK_BUTTONS, BLOCK_BUTTONS


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


def lobby_payload(lobby: RaidLobby, viewer_id: int) -> dict[str, Any]:
    return {
        "id": lobby.id,
        "size": lobby.size,
        "total": lobby.total,
        "mine": lobby.opener_id == viewer_id,
        "joined": viewer_id in lobby.members,
        "in_app": lobby.chat_id is None,
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
            "attack": mine.attack.value if mine and mine.attack else None,
            "block": mine.block[0].value if mine and mine.block else None,
        },
        "log": session.rounds,
    }


def build_raid(player: Player, service: RaidService | None) -> dict[str, Any]:
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
    }
    if service is None:  # pragma: no cover - бот без рейдов не живёт
        return body

    session = service.raid_of_user(player.user_id) or service.result_of_user(
        player.user_id
    )
    if session is not None:
        body["raid"] = raid_payload(session, player.user_id)
        return body

    own = service.lobby_of_user(player.user_id)
    if own is not None:
        body["lobby"] = lobby_payload(own, player.user_id)
    body["lobbies"] = [
        lobby_payload(lobby, player.user_id)
        for lobby in service.open_lobbies()
        if player.user_id not in lobby.members
    ]
    return body


def raid_row(row: dict[str, Any]) -> dict[str, Any]:
    """Строка истории рейдов: с кем дрались, чем кончилось и что унесли."""
    from bot.game.raid import RAID_END_EMOJI, RAID_END_TITLES, RaidEnd, get_boss

    end = RaidEnd(row["outcome"])
    boss = get_boss(row["boss"])
    prize = get_item(row["prize"]) if row["prize"] else None
    return {
        "id": row["id"],
        "boss": boss.title,
        "emoji": RAID_END_EMOJI[end],
        "result": end.value,
        "result_title": RAID_END_TITLES[end],
        "boss_level": row["boss_level"],
        "waves": row["waves"],
        "damage": row["damage"],
        "alive": bool(row["alive"]),
        "prize": prize.title if prize else None,
        "date": (row["created_at"] or "")[:10],
    }


__all__ = ["build_raid", "lobby_payload", "raid_payload", "raid_row"]
