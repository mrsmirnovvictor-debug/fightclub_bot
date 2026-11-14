"""Турнирная арена глазами мини-аппа: расписание, пулл и сетка.

Правил тут нет — их держат `bot.game.arena` и `bot.arena_service`.
Здесь перевод в json.

**Сетка едет деревом.** В ширину телефона плей-офф не помещается, и
доска ездит вбок: круг — колонка, пара — карточка, исход — клетка с
галкой или крестом. Круги, до которых турнир ещё не дошёл, стоят
пустыми (`_blank_match`): сетка затем и нужна, чтобы видеть, куда
ведёт линия, а не только где уже дрались.

**Часы идут на странице.** До открытия записи, до гонга, до конца
подтверждения — всё это сроки, и каждый приезжает часом, а тикает
страница: так же, как сбор в рейд и смена на работе.
"""

from __future__ import annotations

from typing import Any

from bot.game.arena import (
    CLUB_SHARE,
    CONFIRM_SECONDS,
    DONE,
    ENTRY_FEE,
    FIGHTING,
    MIN_PLAYERS,
    OPEN,
    PHASE_TITLES,
    SEEDING,
    closes_at,
    confirm_left,
    next_opening,
    phase_of,
    pot_of,
    prizes,
    round_at,
)
from bot.game.bracket import bracket_size, round_title
from bot.game.clock import club_moment
from bot.game.health import format_term, now_ts
from bot.models import Player


def side_payload(
    user_id: int | None, people: dict[int, dict], match: dict, viewer_id: int
) -> dict[str, Any] | None:
    """Боец в паре: кто он и чем для него кончилась эта пара."""
    if user_id is None:
        return None
    one = people.get(int(user_id), {})
    done = match["state"] == "done"
    return {
        "user_id": int(user_id),
        "name": one.get("name", "боец"),
        "level": one.get("level", 0),
        "you": int(user_id) == viewer_id,
        # Зелёный — прошёл дальше, красный — вылетел. Пока пара не
        # сыграна, цвета нет вовсе: гадать за судью экран не должен
        "won": bool(done and match["winner_id"] == int(user_id)),
        "out": bool(done and match["winner_id"] != int(user_id)),
        "ready": bool(
            match["red_ready"] if int(user_id) == match["red_id"]
            else match["blue_ready"]
        ),
    }


def match_payload(
    match: dict, people: dict[int, dict], viewer_id: int, moment: int
) -> dict[str, Any]:
    """Пара сетки: кто с кем, чем кончилось и сколько ждут выхода."""
    red = side_payload(match["red_id"], people, match, viewer_id)
    blue = side_payload(match["blue_id"], people, match, viewer_id)
    mine = bool(
        viewer_id in (match["red_id"], match["blue_id"])
        and match["state"] != "done"
    )
    return {
        "slot": int(match["slot"]),
        "red": red,
        "blue": blue,
        "state": match["state"],
        "note": match["note"],
        "winner_id": match["winner_id"],
        "mine": mine,
        # Сколько осталось выйти на бой. Ноль — либо ещё не открывали,
        # либо время вышло и судья уже развёл
        "left": confirm_left(int(match["opened_at"] or 0), moment),
        "confirm": CONFIRM_SECONDS,
        "bye": bool(match["red_id"] is None) != bool(match["blue_id"] is None),
    }


def _blank_match(slot: int) -> dict[str, Any]:
    """Пустая пара: круг, до которого турнир ещё не дошёл.

    Такие пары рисуются на доске серыми прочерками — иначе дерево росло
    бы на глазах по колонке в полчаса, и до самого финала было бы не
    видно, сколько кругов впереди.
    """
    return {
        "slot": slot,
        "red": None,
        "blue": None,
        "state": "waiting",
        "note": "",
        "winner_id": None,
        "mine": False,
        "left": 0,
        "confirm": CONFIRM_SECONDS,
        "bye": False,
    }


async def build_arena(
    db, player: Player, duels=None, now: int | None = None
) -> dict[str, Any]:
    """Арена целиком: расписание, пулл, сетка и свой бой."""
    from bot.arena_service import my_match, sweep
    from bot.game.arena import week_of

    moment = now_ts() if now is None else now
    await sweep(db, duels, moment)
    week = week_of(moment)
    row = await db.arena_week(week)
    played = bool(row and row["state"] in ("done", "void"))
    phase = phase_of(moment, played)

    players = await db.arena_players(week)
    people: dict[int, dict] = {}
    for one in players:
        who = await db.get_player(int(one["user_id"]))
        people[int(one["user_id"])] = {
            "name": who.nickname if who else "боец",
            "level": int(one["level"]),
            "seed": int(one["seed"]),
            "out_round": int(one["out_round"]),
            "place": int(one["place"]),
        }

    joined = player.user_id in people
    pot = int(row["pot"]) if row and row["pot"] else pot_of(len(players))
    first, second = prizes(pot)
    rounds = int(row["rounds"]) if row else 0
    matches = await db.arena_matches(week)
    by_round: dict[int, list[dict]] = {}
    for one in matches:
        by_round.setdefault(int(one["round"]), []).append(one)

    mine = await my_match(db, week, player.user_id) if joined else None
    body = {
        "title": "Турнирная арена",
        "phase": phase,
        "phase_title": PHASE_TITLES.get(phase, ""),
        "entry": ENTRY_FEE,
        "club_share": round(CLUB_SHARE * 100),
        "credits": player.credits,
        "pot": pot,
        "prizes": {"winner": first, "runner": second},
        "signed": len(players),
        "min_players": MIN_PLAYERS,
        "joined": joined,
        "can_join": bool(
            phase == OPEN and not joined and player.credits >= ENTRY_FEE
        ),
        # Часы: до чего осталось и сколько именно
        "until": _until(phase, moment),
        "until_text": format_term(max(0, _until(phase, moment) - moment))
        if _until(phase, moment) else "",
        "opens_text": club_moment(next_opening(moment), "%d.%m %H:%M"),
        "closes_text": club_moment(closes_at(moment), "%H:%M"),
        "first_text": club_moment(round_at(moment, 1), "%H:%M"),
        "players": [
            dict(
                people[int(one["user_id"])],
                user_id=int(one["user_id"]),
                you=int(one["user_id"]) == player.user_id,
            )
            for one in players
        ],
        "rounds": _rounds(
            rounds, by_round, people, player.user_id, moment, len(players)
        ),
        "my_match": (
            match_payload(mine, people, player.user_id, moment) if mine else None
        ),
        "champion": _one_of(people, row["winner_id"] if row else None),
        "runner_up": _one_of(people, row["runner_id"] if row else None),
        "void": bool(row and row["state"] == "void"),
    }
    body["note"] = _note(body, phase)
    return body


def _rounds(
    rounds: int,
    by_round: dict[int, list[dict]],
    people: dict[int, dict],
    viewer_id: int,
    moment: int,
    signed: int,
) -> list[dict[str, Any]]:
    """Круги турнира целиком: сыгранные парами, остальные — прочерками."""
    size = bracket_size(signed)
    board: list[dict[str, Any]] = []
    for number in range(1, rounds + 1):
        pairs = sorted(by_round.get(number, []), key=lambda row: row["slot"])
        cards = [
            match_payload(one, people, viewer_id, moment) for one in pairs
        ] or [
            _blank_match(slot) for slot in range(max(1, size // 2 ** number))
        ]
        board.append(
            {
                "number": number,
                "title": round_title(len(cards)),
                "at": round_at(moment, number),
                "at_text": club_moment(round_at(moment, number), "%H:%M"),
                "matches": cards,
            }
        )
    return board


def _until(phase: str, moment: int) -> int:
    """До какого часа идёт нынешняя пора арены. Ноль — часов нет.

    Пока идут круги, общих часов у арены нет: ждут не её, а свой бой, и
    его срок стоит у боя. Строка «круг идёт 00:00:00» отвечала бы на
    вопрос, которого никто не задавал.
    """
    if phase == OPEN:
        return closes_at(moment)
    if phase == SEEDING:
        return round_at(moment, 1)
    if phase == DONE:
        return next_opening(moment)
    return 0


def _one_of(people: dict[int, dict], user_id: int | None) -> dict | None:
    if user_id is None:
        return None
    one = people.get(int(user_id))
    return dict(one, user_id=int(user_id)) if one else None


def _note(body: dict[str, Any], phase: str) -> str:
    """Строка сверху: что сейчас и чего ждать."""
    if phase == OPEN:
        tail = "Ты в списке." if body["joined"] else f"Взнос {body['entry']} 💰."
        return (
            f"Запись до {body['closes_text']}, первые бои в "
            f"{body['first_text']}. {tail}"
        )
    if phase == SEEDING:
        return f"Запись закрыта, сетку сеют. Первый круг в {body['first_text']}."
    if phase == FIGHTING:
        return "Круги идут по часам: следующий — через полчаса после этого."
    if body["void"]:
        return (
            f"В этот раз не собрались: нужно хотя бы {body['min_players']}. "
            f"Взносы вернули. Следующая запись {body['opens_text']}."
        )
    if body["champion"]:
        return (
            f"Чемпион недели — {body['champion']['name']}. "
            f"Следующая запись {body['opens_text']}."
        )
    return f"Арена ждёт воскресенья: запись с {body['opens_text']}."


__all__ = ["build_arena", "match_payload", "side_payload"]
