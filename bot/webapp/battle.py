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
    DEFAULT_WAIT,
    LEVEL_CHOICES,
    LEVEL_NEAR,
    MAX_ROYALE,
    MAX_TEAM_SIZE,
    MIN_ROYALE,
    MIN_TEAM_SIZE,
    RED,
    WAIT_MINUTES,
    BattleKind,
    team_name,
)
from bot.game.modes import FightMode
from bot.game.classes import BLOCK_WIDTH
from bot.models import Player
from bot.webapp.fight import (
    ATTACK_BUTTONS,
    abilities_payload,
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


def lobby_payload(
    lobby: Lobby,
    viewer_id: int,
    roster: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Сбор состава: кто уже записан, с кем он придёт и сколько ждать.

    `roster` — сами записавшиеся и бойцы, которых они привели: номер
    человека → его строка. Боец выходит в бой наравне с человеком и
    место на стороне занимает своё, поэтому в составе он стоит строкой,
    как все, и карточка у него открывается так же.
    """
    held = roster or {}
    return {
        "id": lobby.id,
        "kind": lobby.kind.value,
        "kind_title": KIND_TITLES[lobby.kind],
        "emoji": KIND_EMOJI[lobby.kind],
        "mode": mode_payload(lobby.mode),
        "size": lobby.size,
        "capacity": lobby.capacity,
        # Мест занято и людей записалось — числа разные: боец отряда
        # место занимает, но кнопку не нажимает
        "total": lobby.total,
        "people": lobby.people,
        "min_level": lobby.min_level,
        "max_level": lobby.max_level,
        # Сколько ждут состав и до какого часа: срок у каждого сбора
        # свой, и по нему же тикают часы на странице
        "wait": lobby.wait,
        "wait_text": f"{max(1, lobby.wait // 60)} мин",
        # Сколько осталось до начала. Страница тикает сама, а сервер
        # поправляет её на каждом опросе — часы у всех одни
        "seconds_left": lobby.seconds_left(),
        "mine": lobby.opener_id == viewer_id,
        "joined": viewer_id in lobby.members,
        "in_app": lobby.chat_id is None,
        "teams": [
            {
                "team": team,
                "title": team_name(team),
                "free": lobby.free_in(team),
                "members": [
                    dict(
                        held.get(user_id)
                        or {
                            "user_id": user_id,
                            "name": lobby.names.get(user_id, "боец"),
                            "level": 0,
                            "emoji": "",
                            "fclass": "",
                            "squad": [],
                        }
                    )
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
        # Приёмы и шкала — только свои: чужие заготовки соперник видеть не
        # должен, иначе приём перестаёт быть неожиданностью
        "abilities": abilities_payload(fighter),
        "log": session.rounds,
    }


def fighter_line(one: Player, owner_id: int = 0) -> dict[str, Any]:
    """Строка состава: имя, уровень и класс — всё, что нужно значку ℹ️.

    Поля зовутся так же, как в строке вызова на ринге: значок один на
    весь город, и второго переклада у него быть не должно.
    """
    from bot.game.classes import get_class

    fclass = get_class(one.class_code)
    return {
        "user_id": one.user_id,
        "name": one.nickname,
        "level": one.level,
        "emoji": fclass.emoji,
        "fclass": fclass.title,
        "pro": one.is_pro(),
        "badge": one.badge,
        "owner_id": owner_id,
    }


async def roster_of(db, lobbies: list[Lobby]) -> dict[int, dict[str, Any]]:
    """Состав показываемых сборов: сам человек и бойцы, с которыми он вошёл.

    Бойцы берутся из снимка сбора, а не из отряда: место на стороне
    занято с записи, и пометить дома кого-то другого — не значит
    поменять состав. Спрашивается ровно о показываемых сборах: тянуть
    отряды всего города ради одной строки незачем.
    """
    rows: dict[int, dict[str, Any]] = {}
    for lobby in lobbies:
        for user_id in lobby.members:
            one = await db.get_player(user_id)
            if one is None:  # pragma: no cover - записавшегося стёрли
                continue
            held = []
            for npc_id in lobby.parties.get(user_id, ()):
                npc = await db.get_player(npc_id)
                if npc is None:  # pragma: no cover - сломанная база
                    continue
                held.append(fighter_line(npc, owner_id=user_id))
            rows[user_id] = dict(fighter_line(one), squad=held)
    return rows


async def build_battle(
    db, player: Player, service: BattleService | None
) -> dict[str, Any]:
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
        # Из чего собирают бой: четыре вопроса, на которые отвечает
        # созывающий. Числа и слова держит `bot.game.battle`, страница
        # только рисует ответ
        "modes": [
            {"code": one.value, "title": one.title, "emoji": one.emoji}
            for one in (FightMode.ARMED, FightMode.FIST)
        ],
        "levels": [
            {
                "code": code,
                "title": title,
                "note": note,
                "mine": code == LEVEL_NEAR,
            }
            for code, title, note in LEVEL_CHOICES
        ],
        "waits": [
            {"minutes": one, "title": f"{one} мин", "mine": one == DEFAULT_WAIT}
            for one in WAIT_MINUTES
        ],
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
    others = [
        lobby
        for lobby in service.open_lobbies()
        if player.user_id not in lobby.members
    ]
    shown = ([own] if own is not None else []) + others
    roster = await roster_of(db, shown)
    if own is not None:
        body["lobby"] = lobby_payload(own, player.user_id, roster)
    body["lobbies"] = [
        lobby_payload(lobby, player.user_id, roster) for lobby in others
    ]
    return body


__all__ = [
    "battle_payload",
    "build_battle",
    "fighter_line",
    "lobby_payload",
    "roster_of",
]
