"""Казино для мини-аппа: что показать и что сделать.

Экран спрашивает одно из двух: лобби со столами или снимок того стола,
за которым сидит боец. Лобби считается не каждый раз — за столом оно
не нужно вовсе, а поллинг там частый: клиент спрашивает снимок каждую
секунду, пока идёт партия.

Решения тут нет ни одного. Всё, что можно решить, решает служба: эта
прослойка только разбирает запрос и собирает ответ.
"""

from __future__ import annotations

from typing import Any

from bot.casino_service import (
    CasinoError,
    bet,
    carousel_roll,
    carousel_sell,
    carousel_take,
    carousel_view,
    exchange,
    hand_move,
    join_pot,
    lobby,
    open_card,
    repeat_bet,
    sit,
    stand,
    table_view,
    take_back,
)
from bot.database import Database
from bot.game.casino import (
    GAME_LIST,
    REDS,
    ROULETTE_ODDS,
    ROULETTE_SPOTS,
    WHEEL,
)
from bot.game.health import now_ts
from bot.models import Player

# Поля рулетки для экрана: что нарисовать и во сколько раз платит.
# Список отдаём с сервера, чтобы правило и кнопка не разъезжались
ROULETTE_FIELDS = [
    {"spot": spot, "pick": pick, "odds": ROULETTE_ODDS[spot]}
    for spot, picks in ROULETTE_SPOTS.items()
    for pick in picks
]


async def build_casino(
    db: Database, player: Player, now: int | None = None, table_id: int = 0
) -> dict[str, Any]:
    """Снимок казино: стол, если боец за ним, иначе лобби."""
    moment = now_ts() if now is None else now
    seat = await db.casino_seat_of(player.user_id)
    if not table_id and seat is not None:
        table_id = int(seat["table_id"])
    body: dict[str, Any] = {
        "credits": player.credits,
        "wallet": await db.casino_wallet(player.user_id),
        # Карусель всегда под рукой: за неё не садятся, в неё играют
        # сколько угодно человек разом
        "draw": await carousel_view(db, player, moment),
        "fields": ROULETTE_FIELDS,
        # Порядок карманов уходит на клиент, а не живёт в нём: колесо
        # рисуется по тому же кольцу, по которому считается угол
        # остановки, и разойтись им негде
        "wheel": list(WHEEL),
        "reds": sorted(REDS),
        "now": moment,
    }
    if table_id:
        body["table"] = await table_view(db, table_id, player.user_id, moment)
        body["seated"] = seat is not None and int(seat["table_id"]) == table_id
        return body
    body["lobby"] = await lobby(db, player.user_id, moment)
    return body


async def casino_action(
    db: Database, player: Player, data: dict[str, Any], now: int | None = None
) -> tuple[str, int]:
    """Сделать то, что просит экран. Отдаёт слова и номер стола.

    Номер стола возвращается вместе с ответом не для удобства: сев за
    стол, экран обязан показать именно его, а не лобби, из которого
    ушёл.
    """
    moment = now_ts() if now is None else now
    action = str(data.get("action") or "")
    table_id = int(data.get("table") or 0)
    command_id = str(data.get("command") or "")

    if action == "exchange":
        said = await exchange(
            db, player, str(data.get("kind") or ""),
            int(data.get("amount") or 0), moment,
        )
        return said, 0
    if action == "sit":
        said = await sit(
            db, player, table_id, int(data.get("seat") or 0), moment
        )
        return said, table_id
    if action == "leave":
        return await stand(db, player.user_id, moment), 0
    if action == "bet":
        said = await bet(
            db, player, table_id, int(data.get("amount") or 0),
            pick=str(data.get("pick") or ""),
            spot=str(data.get("spot") or ""),
            command_id=command_id, now=moment,
        )
        return said, table_id
    if action in ("undo", "clear"):
        said = await take_back(
            db, player, table_id, whole=action == "clear",
            command_id=command_id, now=moment,
        )
        return said, table_id
    if action == "roll":
        return await carousel_roll(db, player, moment), 0
    if action == "take":
        return await carousel_take(db, player, moment), 0
    if action == "sell":
        return await carousel_sell(db, player, moment), 0
    if action == "repeat":
        said = await repeat_bet(
            db, player, table_id, command_id=command_id, now=moment
        )
        return said, table_id
    if action == "join":
        said = await join_pot(
            db, player, table_id, command_id=command_id, now=moment
        )
        return said, table_id
    if action == "move":
        said = await hand_move(
            db, player, table_id, str(data.get("move") or ""),
            command_id=command_id, now=moment,
        )
        return said, table_id
    if action == "open":
        said = await open_card(
            db, player, table_id, command_id=command_id, now=moment
        )
        return said, table_id
    raise CasinoError("Непонятное действие.")


def games_payload() -> list[dict[str, Any]]:
    """Столы казино списком — для справки и витрины."""
    return [
        {
            "code": one.code,
            "title": one.title,
            "about": one.about,
            "emoji": one.emoji,
            "seats": one.seats,
            "least": one.least,
            "most": one.most,
            "step": one.step,
            "accept": one.accept,
        }
        for one in GAME_LIST
    ]
