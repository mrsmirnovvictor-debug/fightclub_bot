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
from bot.game.classes import (
    ALL_ZONES,
    BLOCK_WIDTH,
    ZONE_PREPOSITIONAL,
    block_button,
    block_combos,
)
from bot.game.combat import (
    MATCH_ROUNDS,
    TURNS_PER_ROUND,
    Fighter,
    Outcome,
    RoundResult,
    Strike,
    boxing_round,
    turn_in_round,
)
from bot.game.modes import FightMode
from bot.models import Player

# Как назвать исход удара на экране. Значки те же, что в логе боя в ветке:
# лог один, читается одинаково и там, и здесь.
OUTCOME_TITLES: dict[Outcome, tuple[str, str]] = {
    Outcome.HIT: ("👊", "попал"),
    Outcome.CRIT: ("🩸", "крит"),
    Outcome.BREAK: ("🛡🩸", "пробил блок"),
    Outcome.BLOCK: ("🛡", "в блок"),
    Outcome.DODGE: ("🌀", "мимо"),
    Outcome.COUNTER: ("🔄", "контрудар"),
    Outcome.SKIP: ("🤲", "не бил"),
}

# Кнопки хода: они одни на весь клуб и от боя не зависят, поэтому
# считаются один раз при импорте.
ATTACK_BUTTONS: tuple[dict[str, str], ...] = tuple(
    {"zone": zone.value, "title": zone.title.capitalize()} for zone in ALL_ZONES
)
BLOCK_BUTTONS: tuple[dict[str, str], ...] = tuple(
    {"zone": combo[0].value, "title": block_button(combo)[2:]}
    for combo in block_combos(BLOCK_WIDTH)
)


def strike_payload(strike: Strike) -> dict[str, Any]:
    """Один удар: кто, куда, чем кончилось и сколько снял."""
    emoji, title = OUTCOME_TITLES[strike.outcome]
    return {
        "attacker_id": strike.attacker_id,
        "defender_id": strike.defender_id,
        "zone": strike.zone.value if strike.zone else None,
        "zone_title": strike.zone.title.capitalize() if strike.zone else "",
        # «в голову», «по ногам» — падеж берём из той же таблицы, по которой
        # говорит судья в ветке: иначе выходит «в голова»
        "zone_where": ZONE_PREPOSITIONAL[strike.zone] if strike.zone else "",
        "outcome": strike.outcome.value,
        "emoji": emoji,
        "title": title,
        "weapon": strike.weapon,
        "damage": strike.damage,
        "counter": strike.counter_damage,
        "armor": strike.armor,
        "hp_after": strike.defender_hp_after,
        "missed_turn": strike.missed_turn,
    }


def turn_payload(result: RoundResult) -> dict[str, Any]:
    """Ход целиком: номер раунда, номер удара и оба размена."""
    return {
        "number": result.number,
        "round": boxing_round(result.number),
        "turn": turn_in_round(result.number),
        "strikes": [strike_payload(strike) for strike in result.strikes],
        "hp_after": {str(uid): hp for uid, hp in result.hp_after.items()},
        "finished": result.finished,
        "winner_id": result.winner_id,
    }


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


__all__ = [
    "ATTACK_BUTTONS",
    "BLOCK_BUTTONS",
    "build_fights",
    "challenge_payload",
    "duel_payload",
    "turn_payload",
]
