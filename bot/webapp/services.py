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
    one = service_of_job(player.job_code)
    if one is None:
        return {}
    return {
        "code": one.code,
        "title": one.title,
        "emoji": one.emoji,
        "badge": one.badge,
        "lawful": one.lawful,
        "house": one.house,
        "note": one.note,
    }


__all__ = ["service_badge"]
