"""Турнирная арена: запись, сетка и бои по расписанию.

Правила и часы — в `bot.game.arena`, сетка — в `bot.game.bracket`.
Здесь работа с базой и живыми боями.

**Турнир ведёт тот, кто на него смотрит.** Часов, которые будили бы
бота в 18:05, здесь нет — как нет их у работы, у точек и у бонусов.
Всякий взгляд на арену досчитывает её до «сейчас»: закрывает запись,
сеет сетку, открывает круг, разводит не вышедших и венчает чемпиона.
Это то же правило, что и везде в клубе, и оно же спасает от
перезапуска: состояние целиком лежит в базе.

**Бой играется в карточке.** Подтвердили оба — служба заводит обычную
дуэль без ветки, и дерутся ею же, той же панелью, что и в клубе.
Арена только помнит, кто кого и в каком круге.

**Деньги считаются один раз.** Взнос списывается при записи, пулл
закрывается в час жеребьёвки — дальше он не растёт, сколько бы раз на
арену ни посмотрели, — а призы выдаются вместе с венчанием чемпиона.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.arena import (
    ENTRY_FEE,
    MIN_PLAYERS,
    OPEN,
    confirm_left,
    phase_of,
    pot_of,
    prizes,
    round_at,
    round_now,
    week_of,
)
from bot.game.bracket import bracket_size, first_round, next_round
from bot.game.health import now_ts
from bot.game.modes import FightMode
from bot.models import Player

logger = logging.getLogger(__name__)


class ArenaError(Exception):
    """Отказ арены, который показывают игроку как есть."""


# Чем дерутся на арене. С оружием: турнир — витрина клуба, и выходить
# на неё в том, что нажил, честнее, чем босиком
ARENA_MODE = FightMode.ARMED


# ---------- запись ----------


async def sign_up(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Записаться в турнир этой недели за взнос."""
    moment = now_ts() if now is None else now
    week = week_of(moment)
    if phase_of(moment) != OPEN:
        raise ArenaError("Запись закрыта. Она идёт по воскресеньям с 12:00.")
    if player.credits < ENTRY_FEE:
        raise ArenaError(f"Взнос — {ENTRY_FEE} 💰, а у тебя {player.credits}.")
    await db.open_arena_week(week)
    if not await db.add_arena_player(week, player.user_id, player.level, ENTRY_FEE):
        raise ArenaError("Ты уже записан.")
    player.credits -= ENTRY_FEE
    await db.save_player(player)
    logger.info("Арена %s: записался боец %s", week, player.user_id)
    return f"Ты в турнире. Взнос {ENTRY_FEE} 💰 ушёл в призовой фонд."


# ---------- ход турнира ----------


async def sweep(db: Database, duels=None, now: int | None = None) -> None:
    """Досчитать арену до этого часа. Зовётся всеми, кто на неё смотрит."""
    moment = now_ts() if now is None else now
    week = week_of(moment)
    row = await db.arena_week(week)
    if row is None or row["state"] in ("done", "void"):
        return
    if phase_of(moment) == OPEN:
        return
    if row["state"] == "open":
        await _seed(db, week, moment)
        row = await db.arena_week(week)
        if row is None or row["state"] != "live":  # pragma: no cover - гонка
            return
    await _run_rounds(db, duels, week, moment)


async def _seed(db: Database, week: str, moment: int) -> None:
    """Посеять сетку: сильные сверху, лишние места — им же.

    Бойцов не степень двойки — значит, кому-то соперника не хватило.
    Достаются эти места сильнейшим: обычный посев, при котором сильный
    встречает сильного не раньше, чем должен.
    """
    players = await db.arena_players(week)
    if len(players) < MIN_PLAYERS:
        await _call_off(db, week, players, moment)
        return

    seeded = [int(one["user_id"]) for one in players]
    for place, user_id in enumerate(seeded, start=1):
        await db.update_arena_player(week, user_id, seed=place)
    pairs = first_round(seeded)
    for slot, (red, blue) in enumerate(pairs):
        await db.add_arena_match(week, 1, slot, red, blue)
    rounds = max(1, bracket_size(len(seeded)).bit_length() - 1)
    await db.update_arena_week(
        week,
        state="live",
        pot=pot_of(len(players)),
        rounds=rounds,
        started_at=moment,
    )
    logger.info(
        "Арена %s: сетка посеяна на %s бойцов, кругов %s, пулл %s",
        week, len(players), rounds, pot_of(len(players)),
    )


async def _call_off(
    db: Database, week: str, players: list[dict], moment: int
) -> None:
    """Не собрались — турнира нет, взносы возвращаются."""
    for one in players:
        back = await db.get_player(int(one["user_id"]))
        if back is None:  # pragma: no cover - бойца удалили
            continue
        back.credits += int(one["paid"])
        await db.save_player(back)
    await db.update_arena_week(week, state="void", ended_at=moment)
    logger.info("Арена %s: не собрались, взносы вернули", week)


async def _run_rounds(
    db: Database, duels, week: str, moment: int
) -> None:
    """Довести сетку до текущего часа: открыть круг, развести, свести."""
    row = await db.arena_week(week)
    if row is None:  # pragma: no cover - неделю удалили
        return
    due = min(round_now(moment), int(row["rounds"]))
    for number in range(1, due + 1):
        matches = await db.arena_matches(week, number)
        if not matches:
            # Круга ещё нет: его пары собираются из победителей прошлого
            if not await _open_next(db, week, number):
                return
            matches = await db.arena_matches(week, number)
        opened = round_at(moment, number)
        for match in matches:
            await _settle_match(db, duels, week, match, opened, moment)
    await _crown_if_over(db, week, moment)


async def _open_next(db: Database, week: str, number: int) -> bool:
    """Собрать круг из победителей прошлого. False — прошлый не доигран."""
    before = await db.arena_matches(week, number - 1)
    if not before or any(one["state"] != "done" for one in before):
        return False
    winners = [one["winner_id"] for one in before]
    for slot, (red, blue) in enumerate(next_round(winners)):
        await db.add_arena_match(week, number, slot, red, blue)
    return True


async def _settle_match(
    db: Database, duels, week: str, match: dict, opened: int, moment: int
) -> None:
    """Довести пару до развязки: без боя, по неявке или гонгом."""
    if match["state"] == "done":
        return
    red, blue = match["red_id"], match["blue_id"]
    round_number, slot = int(match["round"]), int(match["slot"])

    # Пустое место в сетке и проход без боя считаются сразу
    if red is None and blue is None:
        await db.update_arena_match(week, round_number, slot, state="done")
        return
    if red is None or blue is None:
        await _win(db, week, match, red if blue is None else blue, "без боя")
        return

    if not match["opened_at"]:
        await db.update_arena_match(week, round_number, slot, opened_at=opened)
        match = dict(match, opened_at=opened)

    if match["state"] == "live":
        return  # бой идёт, развязку принесёт сам бой
    ready_red, ready_blue = bool(match["red_ready"]), bool(match["blue_ready"])
    if ready_red and ready_blue:
        await _gong(db, duels, week, match)
        return
    if confirm_left(int(match["opened_at"]), moment):
        return  # время ещё есть — ждём

    # Время вышло: проходит тот, кто вышел. Не вышел никто — старший по
    # уровню: сетка должна ехать дальше, а боя не было ни у кого
    if ready_red or ready_blue:
        await _win(db, week, match, red if ready_red else blue, "соперник не вышел")
        return
    await _win(db, week, match, await _stronger(db, red, blue), "никто не вышел")


async def _stronger(db: Database, red: int, blue: int) -> int:
    """Кто из двоих старше уровнем. Поровну — тот, кто записался раньше."""
    first = await db.get_player(red)
    second = await db.get_player(blue)
    if first is None:  # pragma: no cover - бойца удалили
        return blue
    if second is None:  # pragma: no cover - бойца удалили
        return red
    return red if first.level >= second.level else blue


async def _gong(db: Database, duels, week: str, match: dict) -> None:
    """Оба вышли — лечим обоих и даём гонг. Бой идёт в карточке."""
    round_number, slot = int(match["round"]), int(match["slot"])
    red = await db.get_player(int(match["red_id"]))
    blue = await db.get_player(int(match["blue_id"]))
    if red is None or blue is None:  # pragma: no cover - бойца удалили
        await _win(db, week, match, match["red_id"] or match["blue_id"], "без боя")
        return
    if duels is None:  # pragma: no cover - бот без дуэлей не поднимается
        return
    for one in (red, blue):
        # Турнирный бой решается мастерством, а не тем, кто успел
        # отлежаться: на арену выходят в полном здоровье
        one.heal_full()
        await db.save_player(one)

    await db.update_arena_match(week, round_number, slot, state="live")
    try:
        await duels.start_duel(
            None, None, red, blue, mode=ARENA_MODE,
            on_finish=_finisher(db, week, round_number, slot),
        )
    except Exception:  # pragma: no cover - дуэль не завелась
        logger.exception("Арена %s: бой не начался", week)
        await db.update_arena_match(week, round_number, slot, state="waiting")


def _finisher(db: Database, week: str, round_number: int, slot: int):
    """Кто победил — скажет сама дуэль, когда кончится."""

    async def handler(session, result) -> None:
        matches = await db.arena_matches(week, round_number)
        match = next(
            (one for one in matches if int(one["slot"]) == slot), None
        )
        if match is None:  # pragma: no cover - пару удалили
            return
        winner = result.winner_id
        if winner is None:
            # Ничья: дальше идёт тот, у кого больше здоровья, а при
            # равном — старший уровнем. Переигрывать некогда: круги
            # идут по часам, и следующий начнётся через полчаса
            winner = _healthier(session, match)
        await _win(db, week, match, winner, "" if result.winner_id else "по судьям")

    return handler


def _healthier(session, match: dict) -> int:
    """Кто дошёл с большим запасом. Поровну — красный угол."""
    red, blue = int(match["red_id"]), int(match["blue_id"])
    fighters = getattr(session, "fighters", {})
    mine = fighters.get(red)
    his = fighters.get(blue)
    if mine is None or his is None:  # pragma: no cover - бойца нет в бою
        return red
    return red if mine.hp_percent >= his.hp_percent else blue


async def _win(
    db: Database, week: str, match: dict, winner_id: int | None, note: str
) -> None:
    """Записать победителя пары и выбить проигравшего из турнира."""
    round_number, slot = int(match["round"]), int(match["slot"])
    await db.update_arena_match(
        week, round_number, slot, winner_id=winner_id, state="done", note=note,
    )
    for side in ("red_id", "blue_id"):
        other = match[side]
        if other is not None and other != winner_id:
            await db.update_arena_player(week, int(other), out_round=round_number)


async def _crown_if_over(db: Database, week: str, moment: int) -> None:
    """Последний круг доигран — венчаем чемпиона и платим призы."""
    row = await db.arena_week(week)
    if row is None or row["state"] != "live":  # pragma: no cover - гонка
        return
    last = await db.arena_matches(week, int(row["rounds"]))
    if not last or any(one["state"] != "done" for one in last):
        return
    final = last[0]
    champion = final["winner_id"]
    runner = final["red_id"] if champion == final["blue_id"] else final["blue_id"]
    first_prize, second_prize = prizes(int(row["pot"]))
    for user_id, prize, place in (
        (champion, first_prize, 1), (runner, second_prize, 2)
    ):
        if user_id is None:
            continue
        await db.update_arena_player(week, int(user_id), place=place)
        one = await db.get_player(int(user_id))
        if one is None:  # pragma: no cover - бойца удалили
            continue
        one.credits += prize
        await db.save_player(one)
    await db.update_arena_week(
        week, state="done", winner_id=champion, runner_id=runner, ended_at=moment,
    )
    logger.info(
        "Арена %s: чемпион %s (%s 💰), второй %s (%s 💰)",
        week, champion, first_prize, runner, second_prize,
    )


# ---------- выход на бой ----------


async def confirm(
    db: Database, duels, player: Player, now: int | None = None
) -> str:
    """Подтвердить выход на свой бой. Вышли оба — гонг сразу."""
    moment = now_ts() if now is None else now
    await sweep(db, duels, moment)
    week = week_of(moment)
    match = await my_match(db, week, player.user_id)
    if match is None or match["state"] != "waiting":
        raise ArenaError("Сейчас у тебя нет боя, на который выходят.")
    if not match["opened_at"]:
        raise ArenaError("Круг ещё не начался. Жди гонга.")
    if not confirm_left(int(match["opened_at"]), moment):
        raise ArenaError("Время вышло: на этот бой уже не выйти.")
    if not player.can_fight(moment):
        raise ArenaError("В таком виде на арену не выпускают. Подлечись.")

    side = "red_ready" if int(match["red_id"]) == player.user_id else "blue_ready"
    await db.update_arena_match(
        week, int(match["round"]), int(match["slot"]), **{side: 1}
    )
    fresh = await my_match(db, week, player.user_id)
    if fresh and fresh["red_ready"] and fresh["blue_ready"]:
        await _gong(db, duels, week, fresh)
        return "Оба вышли — гонг!"
    return "Ты вышел на бой. Ждём соперника."


async def my_match(db: Database, week: str, user_id: int) -> dict | None:
    """Пара этого бойца, которая ещё не сыграна."""
    for one in await db.arena_matches(week):
        if one["state"] == "done":
            continue
        if user_id in (one["red_id"], one["blue_id"]):
            return one
    return None


__all__ = [
    "ARENA_MODE",
    "ArenaError",
    "confirm",
    "my_match",
    "sign_up",
    "sweep",
]
