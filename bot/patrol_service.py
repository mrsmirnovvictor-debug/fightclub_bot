"""Патрули в действии: где стоят, кого видят и кого берут.

Правила — в `bot.game.patrol`, драка — в `bot.robbery_service`. Здесь
только то, что связывает их с городом: где пара сейчас, стоит ли она
там же, где разыскиваемый, и можно ли его брать прямо сейчас.

**Патруль не ходит сам.** Фонового обхода в клубе нет ни у чего: ни у
доходов с точек, ни у недель, ни у смен. Где пара стоит — считается от
времени, и считается в тот миг, когда кто-нибудь посмотрел. Поэтому
задержание начинается не «по таймеру», а от шага самого игрока: вошёл
в дом, открыл дом на экране — город посмотрел и увидел, что он стоит
перед патрулём.

**Берут только того, кто может ответить.** Офлайн, в пути и еле
стоящего не трогают: драка без второй стороны — не задержание, а
штраф за то, что человек закрыл приложение. Это то же правило, по
которому живой сотрудник не может взять ушедшего из сети.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.health import now_ts
from bot.game.patrol import PAIRS, patrols_now, shift_left, where_now
from bot.game.presence import is_online
from bot.models import Player

logger = logging.getLogger(__name__)


def patrol_at(place: str, now: int | None = None) -> int:
    """Номер пары, которая стоит в этом доме. Минус один — никого."""
    moment = now_ts() if now is None else now
    return patrols_now(moment).get(place, -1)


def patrol_marks(now: int | None = None) -> dict[str, int]:
    """Где патрули сейчас: дом → номер пары. Для карты и для дома."""
    moment = now_ts() if now is None else now
    return patrols_now(moment)


def patrol_board(now: int | None = None) -> list[dict]:
    """Патрули для экрана: где стоит каждая пара и сколько ей осталось.

    Список открытый: разыскиваемый имеет право знать, куда не ходить.
    Прятаться от патруля — это и есть игра, которую ему оставляют.
    """
    moment = now_ts() if now is None else now
    left = shift_left(moment)
    return [
        {"pair": pair, "place": where_now(pair, moment), "left": left}
        for pair in range(PAIRS)
    ]


async def takeable(
    db: Database, player: Player, now: int | None = None
) -> bool:
    """Возьмёт ли патруль этого бойца, если встретит прямо сейчас."""
    from bot.police_service import has_warrant

    moment = now_ts() if now is None else now
    if player.user_id <= 0:  # pragma: no cover - у патруля нет карточки
        return False
    if player.in_transit(moment) or not is_online(player.seen_at, moment):
        return False
    if not player.can_fight(moment):
        return False
    return await has_warrant(db, player.user_id, moment)


async def catch(
    db: Database, service, player: Player, now: int | None = None
):
    """Взять разыскиваемого, если он стоит там же, где патруль.

    Пусто — задержания не было, и это обычное дело: патруля в этом
    доме нет, ордера нет, человек в пути или уже в драке.

    Отказ службы драки гасим: причина у него одна и та же — человека
    сейчас брать нельзя, — а экран, с которого спросили, не должен из-за
    этого отвечать ошибкой. Патруль просто постоит.
    """
    from bot.robbery_service import RobberyError

    moment = now_ts() if now is None else now
    if service is None:  # pragma: no cover - бот без службы не поднимается
        return None
    if service.is_busy(player.user_id):
        return None
    pair = patrol_at(player.where(moment), moment)
    if pair < 0:
        return None
    if not await takeable(db, player, moment):
        return None
    try:
        return await service.patrol_arrest(player, pair, moment)
    except RobberyError as error:
        logger.info(
            "Патруль %s не взял бойца %s: %s", pair, player.user_id, error
        )
        return None


__all__ = [
    "catch",
    "patrol_at",
    "patrol_board",
    "patrol_marks",
    "takeable",
]
