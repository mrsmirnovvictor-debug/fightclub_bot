"""Школа боевых искусств глазами мини-аппа: ведомость и все приёмы.

Правил тут нет — их держат `bot.game.dojo` и `bot.dojo_service`. Здесь
перевод в json.

**Экран отвечает на один вопрос: чем боец готов платить.** Сверху
ведомость — что он уже сделал в боях; под ней то, на что он записан, с
полоской; ниже все приёмы клуба, разложенные по четырём ветвям, с ценой в
делах у каждого.

**Что забудется, сказано до записи.** Слотов четыре, и новый приём
встаёт на место того, который стоит столько же энергии. Узнавать об этом
после пятисот ударов было бы обидно, поэтому имя приёма, который уйдёт,
стоит на карточке с самого начала.
"""

from __future__ import annotations

from typing import Any

from bot.game.abilities import MAX_ABILITIES, Source
from bot.game.classes import FIGHTER_CLASSES
from bot.game.dojo import (
    BRANCH_TITLES,
    DEED_OF,
    DEED_TITLES,
    branch_of,
    deed_name,
    deed_of,
    needed,
    price,
)
from bot.game.health import now_ts
from bot.game.locations import get_location
from bot.models import Player


def deed_rows(counts: dict[Source, int]) -> list[dict[str, Any]]:
    """Ведомость бойца: четыре дела и сколько каждого сделано."""
    return [
        {
            "deed": source.value,
            "title": DEED_TITLES[source],
            "count": int(counts.get(source, 0)),
            # Чья это ветка: по ней видно, что копится само
            "branch": branch,
            "branch_title": FIGHTER_CLASSES[branch].title,
        }
        for branch, source in DEED_OF.items()
    ]


def ability_row(
    player: Player, ability, trains: str, counts: dict[Source, int]
) -> dict[str, Any]:
    """Приём на полке школы: цена в делах, цена в энергии и что забудется."""
    from bot.dojo_service import forget_for

    deed = deed_of(ability)
    known = ability.code in player.loadout
    forget = None if known else forget_for(player, ability)
    return {
        "code": ability.code,
        "title": ability.title,
        "icon": ability.icon,
        "image": ability.picture,
        "note": ability.note,
        "tier": ability.tier,
        # Цена в энергии — наименьшая возможная: школа учит приём на его
        # собственной ступени
        "price": price(ability),
        "branch": branch_of(ability),
        "branch_title": FIGHTER_CLASSES[branch_of(ability)].title,
        # Родительный падеж — для заголовка «Приёмы Воина»
        "branch_of": BRANCH_TITLES[branch_of(ability)],
        "deed": deed.value,
        "deed_name": deed_name(deed, needed(ability)),
        "needed": needed(ability),
        "have": int(counts.get(deed, 0)),
        "known": known,
        "known_tier": player.loadout.slots.get(ability.code, 0),
        "trains": ability.code == trains,
        "can_start": bool(not known and ability.code != trains),
        "forget": None if forget is None else {
            "code": forget.code,
            "title": forget.title,
            "tier": forget.tier,
        },
        "why": (
            "Этот приём боец уже знает."
            if known
            else "Он и так в обучении." if ability.code == trains else ""
        ),
    }


async def build_dojo(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Экран школы: ведомость, обучение и все приёмы клуба."""
    from bot.dojo_service import catalogue, totals, training

    moment = now_ts() if now is None else now
    counts = await totals(db, player.user_id)
    state = await training(db, player, moment)
    trains = state["ability"].code if state else ""
    place = get_location(player.where(moment))
    body: dict[str, Any] = {
        "title": place.title if place else "Школа боевых искусств",
        "slots": MAX_ABILITIES,
        "deeds": deed_rows(counts),
        "training": None if state is None else {
            "code": state["ability"].code,
            "title": state["ability"].title,
            "icon": state["ability"].icon,
            "image": state["ability"].picture,
            "note": state["ability"].note,
            "tier": state["ability"].tier,
            "price": price(state["ability"]),
            "deed": state["deed"].value,
            "deed_name": deed_name(state["deed"], state["needed"]),
            "deed_title": DEED_TITLES[state["deed"]],
            "done": state["done"],
            "needed": state["needed"],
            "left": max(0, state["needed"] - state["done"]),
            "left_name": deed_name(
                state["deed"], max(0, state["needed"] - state["done"])
            ),
            "ready": state["ready"],
            "forget": None if state["forget"] is None else {
                "code": state["forget"].code,
                "title": state["forget"].title,
                "tier": state["forget"].tier,
            },
        },
        "shelf": [
            ability_row(player, one, trains, counts) for one in catalogue(player)
        ],
        "said": "",
    }
    body["note"] = _note(body)
    return body


def _note(body: dict[str, Any]) -> str:
    """Строка сверху: что тут делают и что мешает."""
    state = body["training"]
    if state and state["ready"]:
        tail = (
            f" На его место встанет «{state['forget']['title']}» — "
            f"забудется навсегда."
            if state["forget"]
            else ""
        )
        return (
            f"«{state['title']}» набран: {state['done']} из "
            f"{state['needed']}. Можно учить.{tail}"
        )
    if state:
        return (
            f"«{state['title']}» в обучении: {state['deed_title'].lower()} "
            f"{state['done']} из {state['needed']}. Дела считаются в любых "
            f"боях — в дуэли, в отряде, в рейде и на осаде."
        )
    return (
        "Школа учит любому приёму клуба, и стоит он здесь дешевле всего — "
        "столько, сколько у своего класса. Платят за это не кредитами, а "
        "делами в бою: удар копится точными ударами, уворот — уворотами, "
        "крит — критическими, лечение и парирование — удержанными блоками. "
        "В обучении один приём."
    )


__all__ = ["ability_row", "build_dojo", "deed_rows"]
