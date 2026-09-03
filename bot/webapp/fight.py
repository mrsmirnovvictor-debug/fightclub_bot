"""Бой глазами мини-аппа: то же состояние, что и в ветке, только данными.

Мини-апп — второй пульт к тому же бою. Ходы считает всё тот же
`DuelService`, здесь нет ни одной строчки правил: только перевод живой
сессии в json, который умеет нарисовать страница.

Экран сам себя опрашивает раз в пару секунд, поэтому ответ всегда полный —
по нему видно, что рисовать: список вызовов, свой висящий вызов или
панель идущего боя.
"""

from __future__ import annotations

from typing import Any

from bot.duel_service import Challenge, DuelService, DuelSession
from bot.game.classes import ALL_ZONES, BLOCK_WIDTH, block_button, block_combos
from bot.game.combat import (
    MATCH_ROUNDS,
    TURNS_PER_ROUND,
    Fighter,
    boxing_round,
    turn_in_round,
)
from bot.game.fightlog import turn_payload
from bot.game.modes import FightMode, mode_of
from bot.models import Player

# Кнопки хода: они одни на весь клуб и от боя не зависят, поэтому
# считаются один раз при импорте.
ATTACK_BUTTONS: tuple[dict[str, str], ...] = tuple(
    {"zone": zone.value, "title": zone.title.capitalize()} for zone in ALL_ZONES
)
BLOCK_BUTTONS: tuple[dict[str, str], ...] = tuple(
    {"zone": combo[0].value, "title": block_button(combo)[2:]}
    for combo in block_combos(BLOCK_WIDTH)
)


def mode_payload(mode: FightMode) -> dict[str, Any]:
    return {"code": mode.value, "title": mode.title, "emoji": mode.emoji}


def fighter_payload(fighter: Fighter, ready: bool, you: bool) -> dict[str, Any]:
    return {
        "user_id": fighter.user_id,
        "name": fighter.name,
        "level": fighter.level,
        "emoji": fighter.fclass.emoji,
        "fclass": fighter.fclass.title,
        "hp": fighter.hp,
        "max_hp": fighter.max_hp,
        "percent": round(fighter.hp_percent * 100),
        "damage_dealt": fighter.damage_dealt,
        "ready": ready,
        "you": you,
        "weapon": fighter.weapon,
        "weapon_icon": fighter.weapon_icon,
    }


def challenge_payload(challenge: Challenge, viewer_id: int) -> dict[str, Any]:
    return {
        "id": challenge.id,
        "mode": mode_payload(challenge.mode),
        "mine": challenge.challenger.user_id == viewer_id,
        # Адресный вызов принять может только тот, кому он брошен
        "personal": challenge.target_id is not None,
        "in_app": challenge.chat_id is None,
        "challenger": {
            "user_id": challenge.challenger.user_id,
            "name": challenge.challenger.nickname,
            "level": challenge.challenger.level,
            "emoji": challenge.challenger.fclass.emoji,
            "fclass": challenge.challenger.fclass.title,
        },
    }


def duel_payload(session: DuelSession, viewer_id: int) -> dict[str, Any]:
    """Панель идущего боя: кто с кем, чей ход и что уже нажато."""
    mine = session.choices.get(viewer_id)
    return {
        "id": session.id,
        "mode": mode_payload(session.mode),
        "in_app": session.in_app,
        "started": session.started,
        "round": boxing_round(session.round_number) if session.round_number else 0,
        "turn": turn_in_round(session.round_number) if session.round_number else 0,
        "turns_per_round": TURNS_PER_ROUND,
        "rounds": MATCH_ROUNDS,
        # Пауза между раундами: в мини-аппе её нет, а бой из ветки может
        # застать бойцов в углах — тогда панель ждёт вместе с ними
        "resting": session.resting,
        "fighters": [
            fighter_payload(
                session.fighters[user_id],
                ready=session.is_ready(user_id),
                you=user_id == viewer_id,
            )
            for user_id in session.order
        ],
        # Что этот боец уже выбрал: страница подсвечивает нажатое
        "chosen": {
            "attack": mine.attack.value if mine and mine.attack else None,
            "block": mine.block[0].value if mine and mine.block else None,
        },
        "yours": viewer_id in session.fighters,
        # Разбор по ходам: свежий ход последний, как в ветке
        "log": [turn_payload(result) for result in session.rounds],
    }


def build_fights(player: Player, service: DuelService | None) -> dict[str, Any]:
    """Всё, что нужно вкладке «Бои», одним ответом."""
    body: dict[str, Any] = {
        "attacks": [dict(row) for row in ATTACK_BUTTONS],
        "blocks": [dict(row) for row in BLOCK_BUTTONS],
        "modes": [mode_payload(mode) for mode in FightMode],
        "duel": None,
        "challenge": None,
        "challenges": [],
        "can_fight": player.can_fight(),
    }
    if service is None:  # pragma: no cover - бот без сервиса боёв не живёт
        return body

    duel = service.duel_of_user(player.user_id)
    if duel is not None:
        body["duel"] = duel_payload(duel, player.user_id)
        return body

    body["challenge"] = None
    own = service.challenge_of_user(player.user_id)
    if own is not None:
        body["challenge"] = challenge_payload(own, player.user_id)
    body["challenges"] = [
        challenge_payload(challenge, player.user_id)
        for challenge in service.open_challenges()
        if service.challenge_for(player.user_id, challenge)
        and challenge.challenger.user_id != player.user_id
    ]
    return body


# ---------- история боёв ----------


def outcome_of(row: dict[str, Any], user_id: int) -> str:
    """Чем бой кончился для этого бойца: победа, поражение или ничья."""
    if row["winner_id"] is None:
        return "draw"
    return "win" if row["winner_id"] == user_id else "loss"


OUTCOME_MARKS = {"win": ("🏆", "Победа"), "loss": ("❌", "Поражение"),
                 "draw": ("🤝", "Ничья")}


def fight_row(row: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Строка списка боёв: с кем, чем кончилось и когда."""
    rival_id = (
        row["opponent_id"] if row["challenger_id"] == user_id else row["challenger_id"]
    )
    rival_name = (
        row["opponent_name"] if row["challenger_id"] == user_id
        else row["challenger_name"]
    )
    result = outcome_of(row, user_id)
    emoji, title = OUTCOME_MARKS[result]
    mode = mode_of(row["mode"])
    return {
        "id": row["id"],
        "rival_id": rival_id,
        "rival": rival_name or "боец без имени",
        "result": result,
        "emoji": emoji,
        "result_title": title,
        "rounds": row["rounds"],
        "mode": mode_payload(mode),
        "in_app": row["chat_id"] is None,
        "created_at": row["created_at"],
        "date": (row["created_at"] or "")[:10],
    }


def build_history(
    rows: list[dict[str, Any]], user_id: int, name: str
) -> dict[str, Any]:
    """Список боёв бойца, разложенный по дням — свежий день сверху."""
    fights = [fight_row(row, user_id) for row in rows]
    days: list[dict[str, Any]] = []
    for fight in fights:
        if not days or days[-1]["date"] != fight["date"]:
            days.append({"date": fight["date"], "fights": []})
        days[-1]["fights"].append(fight)
    counts = {"win": 0, "loss": 0, "draw": 0}
    for fight in fights:
        counts[fight["result"]] += 1
    return {
        "user_id": user_id,
        "name": name,
        "days": days,
        "total": len(fights),
        "counts": counts,
        # Куда листать дальше: последний бой этой страницы
        "before": fights[-1]["id"] if fights else None,
    }


def build_fight_log(
    row: dict[str, Any], log: list[dict[str, Any]], viewer_id: int
) -> dict[str, Any]:
    """Один бой целиком: кто с кем, чем кончился и как шёл по ходам."""
    names = {
        row["challenger_id"]: row["challenger_name"] or "боец без имени",
        row["opponent_id"]: row["opponent_name"] or "боец без имени",
    }
    return {
        "fight": fight_row(row, viewer_id),
        "names": {str(user_id): name for user_id, name in names.items()},
        "sides": [
            {"user_id": user_id, "name": name, "you": user_id == viewer_id}
            for user_id, name in names.items()
        ],
        "turns": log,
        # Лог мог не сохраниться: бои до этой версии писались только итогом
        "has_log": bool(log),
    }


__all__ = [
    "ATTACK_BUTTONS",
    "BLOCK_BUTTONS",
    "build_fights",
    "challenge_payload",
    "build_fight_log",
    "build_history",
    "duel_payload",
]
