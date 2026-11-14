"""Значок службы глазами мини-аппа: то, что встаёт у ника.

Правил тут нет — их держит `bot.game.services`, а состав службы считается
по рабочему месту. Здесь перевод в json и одна забота сверх перевода:
**значок есть не у всякого работающего**. Бармен, крупье и менеджер по
продажам работают, но службы за ними нет, и значка у них быть не должно.
"""

from __future__ import annotations

from typing import Any

from bot.game.services import service_of_job
from bot.models import Player


def service_badge(player: Player) -> dict[str, Any]:
    """Значок службы бойца. Пусто — он нигде не служит.

    Знак клана и значок службы носить разом нельзя, и проверять это
    здесь не нужно: в клан служивого не пускают, а на службу мафиози не
    берут. Если оба всё-таки сошлись, значит, сломалось не рисование.
    """
    from bot.game.work import get_vacancy

    one = service_of_job(player.job_code)
    if one is None:
        return {}
    vacancy = get_vacancy(player.job_code)
    return {
        "code": one.code,
        "title": one.title,
        "emoji": one.emoji,
        "badge": one.badge,
        "lawful": one.lawful,
        "house": one.house,
        "note": one.note,
        # Должность — то же, что чин в семье: под ником стоит «Полиция
        # Вегаса · Начальник», а не одно голое название службы
        "post": vacancy.title if vacancy else "",
        "rank_title": short_post(vacancy.title) if vacancy else "",
    }


def short_post(title: str) -> str:
    """Должность коротко: «Начальник полиции» → «Начальник».

    Название службы стоит в той же строке, и повторять его в должности
    незачем: «Полиция Вегаса · Начальник полиции» читается как заикание.
    """
    for tail in (" полиции", " части"):
        if title.endswith(tail):
            return title[: -len(tail)]
    return title


__all__ = ["service_badge", "short_post"]
