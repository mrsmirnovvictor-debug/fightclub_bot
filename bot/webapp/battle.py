"""Групповой бой глазами мини-аппа: сбор состава и ход раунда.

Правил здесь нет — раунды считает `BattleService`. Тут только перевод
живого лобби или боя в json, который умеет нарисовать страница. Экран
опрашивает ручку раз в пару секунд, поэтому ответ всегда полный: по нему
видно, что рисовать — сбор, идущий раунд или итог.
"""

from __future__ import annotations

from typing import Any

from bot.battle_service import BattleService, BattleSession, Lobby
from bot.game.battle import (
    BLUE,
    MAX_ROYALE,
    MAX_TEAM_SIZE,
    MIN_ROYALE,
    MIN_TEAM_SIZE,
    RED,
    BattleKind,
    team_name,
)
from bot.game.classes import BLOCK_WIDTH
from bot.models import Player
from bot.webapp.fight import (
    ATTACK_BUTTONS,
    BLOCK_BUTTONS,
    block_buttons,
    hands_payload,
    mode_payload,
)

KIND_TITLES = {
    BattleKind.TEAM: "Командный бой",
    BattleKind.ROYALE: "Королевская битва",
}
KIND_EMOJI = {BattleKind.TEAM: "🤝", BattleKind.ROYALE: "🌪"}


def lobby_payload(lobby: Lobby, viewer_id: int) -> dict[str, Any]:
    """Сбор состава: кто уже записан и за какую сторону."""
    return {
        "id": lobby.id,
        "kind": lobby.kind.value,
        "kind_title": KIND_TITLES[lobby.kind],
        "emoji": KIND_EMOJI[lobby.kind],
        "mode": mode_payload(lobby.mode),
        "size": lobby.size,
        "capacity": lobby.capacity,
        "total": lobby.total,
        "min_level": lobby.min_level,
        "max_level": lobby.max_level,
        "mine": lobby.opener_id == viewer_id,
        "joined": viewer_id in lobby.members,
        "in_app": lobby.chat_id is None,
        "teams": [
            {
                "team": team,
                "title": team_name(team),
                "free": lobby.size - len(lobby.side(team)),
                "members": [
                    {"user_id": user_id, "name": lobby.names.get(user_id, "боец")}
                    for user_id in lobby.side(team)
                ],
            }
            for team in ((RED, BLUE) if lobby.kind is BattleKind.TEAM else (RED,))
        ],
    }


def member_payload(
    session: BattleSession, user_id: int, viewer_id: int
) -> dict[str, Any]:
    fighter = session.fighters[user_id]
    rival_id = session.opponent_of(user_id)
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
        "team": session.teams.get(user_id, RED),
        "team_title": team_name(session.teams.get(user_id, RED)),
        # Против кого стоит в этом ходу: без пары ход пропускается
        "rival_id": rival_id,
        "rival": session.fighters[rival_id].name if rival_id is not None else None,
        "ready": session.is_ready(user_id),
        "you": user_id == viewer_id,
    }


def battle_payload(session: BattleSession, viewer_id: int) -> dict[str, Any]:
    """Панель раунда: пары, что уже нажато и чем всё кончилось."""
    mine = session.choices.get(viewer_id)
    fighter = session.fighters.get(viewer_id)
    return {
        "id": session.id,
        "kind": session.kind.value,
        "kind_title": KIND_TITLES[session.kind],
        "emoji": KIND_EMOJI[session.kind],
        "mode": mode_payload(session.mode),
        "round": session.round_number,
        "in_app": session.chat_id is None,
        "finished": session.finished,
        "summary": session.summary,
        # Набор кнопок у каждого свой: второе оружие даёт второй столбец
        # ударов, щит — блок в три зоны
        "hands": hands_payload(fighter),
        "blocks": block_buttons(fighter.block_width if fighter else BLOCK_WIDTH),
        "party": [
            member_payload(session, user_id, viewer_id) for user_id in session.fighters
        ],
        "yours": viewer_id in session.fighters,
        "alive": bool(fighter and fighter.alive),
        # В этом ходу пары не досталось — кнопки прячем
        "fighting": session.is_fighting(viewer_id),
        "acted": session.is_ready(viewer_id),
        "chosen": {
            "attacks": {
                str(hand): zone.value
                for hand, zone in (mine.attacks.items() if mine else ())
            },
            "block": mine.block[0].value if mine and mine.block else None,
        },
        "log": session.rounds,
    }


def build_battle(player: Player, service: BattleService | None) -> dict[str, Any]:
    """Всё, что нужно разделу «Отряд», одним ответом."""
    body: dict[str, Any] = {
        "attacks": [dict(row) for row in ATTACK_BUTTONS],
        "blocks": [dict(row) for row in BLOCK_BUTTONS],
        "kinds": [
            {
                "code": BattleKind.TEAM.value,
                "title": KIND_TITLES[BattleKind.TEAM],
                "emoji": KIND_EMOJI[BattleKind.TEAM],
                "min": MIN_TEAM_SIZE,
                "max": MAX_TEAM_SIZE,
            },
            {
                "code": BattleKind.ROYALE.value,
                "title": KIND_TITLES[BattleKind.ROYALE],
                "emoji": KIND_EMOJI[BattleKind.ROYALE],
                "min": MIN_ROYALE,
                "max": MAX_ROYALE,
            },
        ],
        "can_fight": player.can_fight(),
        "battle": None,
        "lobby": None,
        "lobbies": [],
    }
    if service is None:  # pragma: no cover - бот без групповых боёв не живёт
        return body

    session = service.battle_of_user(player.user_id) or service.result_of_user(
        player.user_id
    )
    if session is not None:
        body["battle"] = battle_payload(session, player.user_id)
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


__all__ = ["battle_payload", "build_battle", "lobby_payload"]
