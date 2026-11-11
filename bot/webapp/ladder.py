"""Экран недельного рейтинга: что про него знает карточка.

Правила — в `bot.game.ladder`, подсчёт и выплаты — в
`bot.ladder_service`. Здесь только сборка ответа: страница мест,
границы периода и личный прогресс того, кто смотрит.

**Награда на экране — предварительная.** Пока неделя идёт, места
меняются с каждым боем, и сумма рядом с местом говорит не «ты получишь
столько», а «столько платят за место, на котором ты сейчас стоишь».
Подпись об этом стоит в самом ответе, а не в вёрстке: это часть правил,
а не оформления.

**Не прошедший порог видит прогресс, а не место.** Места у него нет —
его нет в рейтинге, — и показывать ноль было бы неправдой. Вместо
места он видит, сколько боёв осталось.
"""

from __future__ import annotations

from bot.game.ladder import (
    NOBODY_YET,
    PRELIMINARY,
    QUALIFY,
    Standing,
    period_text,
    prize_for,
    prize_table,
    progress_text,
)
from bot.models import Player
from bot.webapp.card import fighter_row


def ladder_row(
    place: int,
    one: Standing,
    player: Player | None,
    viewer_id: int | None,
    bot_token: str = "",
) -> dict:
    """Строка рейтинга недели: место, боец, его неделя и награда.

    Боец берётся из базы, а место и недельный счёт — из журнала: это
    разные источники, и строка их сводит. Бойца могли удалить между
    подсчётом и запросом — тогда в строке остаётся то, что знает
    журнал, и это лучше, чем дырка в списке мест.
    """
    base = (
        fighter_row(player, viewer_id, bot_token, place=place)
        if player is not None
        else _gone(place, one, viewer_id)
    )
    return {
        **base,
        # ELO берём из журнала, а не из карточки: у закрытой недели это
        # рейтинг на её границу, и сегодняшний показал бы не тот итог,
        # по которому платили
        "rating": one.rating,
        "fights": one.fights,
        "wins": one.wins,
        "losses": one.losses,
        "prize": prize_for(place),
    }


def _gone(place: int, one: Standing, viewer_id: int | None) -> dict:
    """Строка бойца, которого больше нет в клубе.

    Набор ключей тот же, что у живой строки: страница рисует её тем же
    кодом, и недостающий класс или знак обрушили бы ей весь список —
    из-за одного удалённого бойца пропали бы и все остальные места.
    """
    return {
        "user_id": one.user_id,
        "nickname": "боец выбыл",
        "level": 0,
        "rating": one.rating,
        "place": place,
        "pro": False,
        "badge": "",
        "is_self": one.user_id == viewer_id,
        "fclass": {"code": "", "title": "", "emoji": "❔"},
        "clan": {},
        "service": {},
    }


def build_ladder(
    places: list[tuple[int, Standing]],
    players: dict[int, Player],
    viewer_id: int | None,
    bot_token: str = "",
    now: int = 0,
    mine: int = 0,
    my_place: int = 0,
    offset: int = 0,
    total: int | None = None,
    page_size: int = 50,
    settled: bool = False,
) -> dict:
    """Страница недельного рейтинга.

    `places` — только строки этой страницы, но места в них уже
    посчитаны по всему рейтингу: на второй странице первая строка
    пятьдесят первая, а не первая.

    `mine` — сколько зачётных боёв у того, кто смотрит, за этот период.
    `my_place` — его место, если он порог прошёл; ноль — не прошёл.
    """
    whole = len(places) if total is None else total
    size = max(1, page_size)
    return {
        "fighters": [
            ladder_row(place, one, players.get(one.user_id), viewer_id, bot_token)
            for place, one in places
        ],
        "total": whole,
        "offset": offset,
        "page_size": size,
        "page": offset // size + 1,
        "pages": max(1, -(-whole // size)),
        # Границы периода словами: без них «рейтинг недели» не говорит,
        # какой именно недели
        "window": period_text(now),
        "qualify": QUALIFY,
        # Подпись о предварительности — часть правил, а не вёрстки
        "note": PRELIMINARY,
        "empty": NOBODY_YET,
        # Итог уже подведён: такой рейтинг не предварительный
        "settled": settled,
        "prizes": prize_table(),
        "mine": {
            "fights": mine,
            "left": max(0, QUALIFY - mine),
            "qualified": mine >= QUALIFY,
            "progress": progress_text(mine),
            "place": my_place,
            "prize": prize_for(my_place) if my_place else 0,
        },
    }


def build_payout(place: dict | None) -> dict:
    """Чем кончилась прошлая неделя для того, кто смотрит.

    Пусто — он в ней не участвовал. Это не ошибка и не пустой рейтинг:
    порог за ту неделю он просто не прошёл.
    """
    if not place:
        return {}
    return {
        "period": place["period"],
        "place": int(place["place"]),
        "prize": int(place["prize"]),
        "rating": int(place["rating"]),
        "fights": int(place["fights"]),
        "paid": bool(place["paid_at"]),
    }


__all__ = ["build_ladder", "build_payout", "ladder_row"]
