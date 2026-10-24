"""Барбершоп и салон красоты глазами мини-аппа: полка образов.

Правил тут нет — их держат `bot.game.looks` и `bot.looks_service`. Здесь
перевод в json.

**Экран один, а домов два.** Барбершоп режет мужские лица, салон красоты
делает женские; разница в том, что лежит на полке, а не в том, какой это
экран. Заводить два похожих значило бы дважды поправлять одну кнопку.

**Смотрят полку все.** Женщина в барбершопе видит её целиком и не берёт
с неё ничего: кнопка гаснет и называет причину. Прятать полку от чужого
пола незачем — посмотреть на витрину не грех, а знать, что в городе
есть, полезно обоим.
"""

from __future__ import annotations

from typing import Any

from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.looks import GENDER_TITLES, LOOK_PRICE, gender_at, shop_of
from bot.models import Player
from bot.webapp.bank import purse_payload


async def build_barber(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Экран дома, где торгуют образами: полка, кошелёк и записка."""
    from bot.looks_service import shelf

    moment = now_ts() if now is None else now
    here = player.where(moment)
    place = get_location(here)
    sells = gender_at(here)
    body: dict[str, Any] = {
        "title": place.title if place else "Образы",
        "place": here,
        "sells": sells,
        "sells_title": GENDER_TITLES.get(sells, ""),
        # Свой ли это дом бойцу: смотреть можно всем, брать — своему полу
        "mine": player.sex == sells,
        "price": LOOK_PRICE,
        "credits": player.credits,
        "purse": purse_payload(player, moment, Service.LOOKS),
        "shelf": await shelf(db, player, here, moment),
        "said": "",
    }
    body["note"] = _note(body, player)
    return body


def _note(body: dict[str, Any], player: Player) -> str:
    """Строка сверху: что тут делают и кому."""
    if not body["sells"]:  # pragma: no cover - дверь сюда не ведёт
        return "Образами здесь не торгуют."
    if not body["mine"]:
        mine = get_location(shop_of(player.sex))
        where = mine.title.lower() if mine else "другой дом"
        kind = "мужские" if body["sells"] == "male" else "женские"
        return (
            f"Здесь делают {kind} образы — смотреть можно, брать нет. "
            f"Свой выбирают в {where}."
        )
    taken = sum(1 for one in body["shelf"] if one["owned"])
    return (
        f"Образ покупается раз и навсегда: {body['price']} 💰, и дальше он "
        f"твой. Переключаются между своими в карточке, во вкладке "
        f"«Персонаж», — там это бесплатно. На бой образ не влияет ничем. "
        f"Своих у тебя {taken} из {len(body['shelf'])}."
    )


__all__ = ["build_barber"]
