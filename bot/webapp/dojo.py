"""Школа боевых искусств глазами мини-аппа: свои приёмы и обучение.

Правил тут нет — их держат `bot.game.dojo` и `bot.dojo_service`. Здесь
перевод в json.

**Вкладок две, и они отвечают на разные вопросы.** «Ваши приёмы» — что у
бойца есть; «Обучение» — что он может взять и как идёт набор. Складывать
их в один список значило бы мешать «уже моё» с «ещё не моё», а это на
этом экране главное различие.

**Пока приём учится, выбирать нечего.** Разом учат один, и полка
каталога на это время уходит с экрана: держать перед глазами шестнадцать
кнопок, из которых ни одна не нажимается, незачем. Сервер и отдаёт её
пустой — правило живёт здесь, а не в том, что страница решила спрятать.

**Набранный приём ждёт отдельно.** Он уже выучен всем, кроме
подтверждения, и место в обучении не занимает: боец берётся за
следующий, а этот висит с кнопкой «Изучить» сколько угодно долго.

**Что забудется, сказано до записи.** Слотов четыре, и новый приём
встаёт на место того, который стоит столько же энергии. Узнавать об этом
после пятисот ударов было бы обидно, поэтому приём, который уйдёт,
приходит с картинкой — и в каталоге, и в окне подтверждения.
"""

from __future__ import annotations

from typing import Any

from bot.game.abilities import MAX_ABILITIES, TIERS, TIER_COST, Source
from bot.game.classes import FIGHTER_CLASSES
from bot.game.dojo import (
    BRANCH_TITLES,
    DEED_OF,
    DEED_TITLES,
    branch_of,
    deed_act,
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


def trick_row(ability, tier: int = 0) -> dict[str, Any]:
    """Приём как он рисуется всюду на этом экране."""
    return {
        "code": ability.code,
        "title": ability.title,
        "icon": ability.icon,
        "image": ability.picture,
        "note": ability.note,
        "tier": ability.tier,
        # Цена в энергии: у выученного — та, по которой он выучен, у
        # остальных — та, по которой его отдаст школа
        "price": TIER_COST[tier] if tier else price(ability),
        "branch": branch_of(ability),
        "branch_title": FIGHTER_CLASSES[branch_of(ability)].title,
        "branch_of": BRANCH_TITLES[branch_of(ability)],
    }


def shelf_row(player: Player, ability, taken: set[str]) -> dict[str, Any]:
    """Приём в каталоге обучения: цена в делах и что забудется."""
    from bot.dojo_service import forget_for

    deed = deed_of(ability)
    forget = forget_for(player, ability)
    return dict(
        trick_row(ability),
        deed=deed.value,
        deed_name=deed_name(deed, needed(ability)),
        # Задание словами: «точно ударить 500 раз»
        deed_act=deed_act(deed),
        needed=needed(ability),
        taken=ability.code in taken,
        forget=None if forget is None else trick_row(forget, forget.tier),
    )


def learning_row(one: dict[str, Any]) -> dict[str, Any]:
    """Запись обучения: сколько набрано и что встанет на место."""
    ability = one["ability"]
    left = max(0, one["needed"] - one["done"])
    return dict(
        trick_row(ability),
        deed=one["deed"].value,
        deed_title=DEED_TITLES[one["deed"]],
        deed_act=deed_act(one["deed"]),
        deed_name=deed_name(one["deed"], one["needed"]),
        done=one["done"],
        needed=one["needed"],
        left=left,
        left_name=deed_name(one["deed"], left),
        ready=one["ready"],
        forget=(
            None
            if one["forget"] is None
            else trick_row(one["forget"], one["forget"].tier)
        ),
    )


async def build_dojo(
    db, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Экран школы: свои приёмы, ведомость и обучение."""
    from bot.abilities_service import known
    from bot.dojo_service import catalogue, learning, totals

    moment = now_ts() if now is None else now
    counts = await totals(db, player.user_id)
    rows = await learning(db, player, moment)
    taken = {one["ability"].code for one in rows}
    busy = next((one for one in rows if not one["ready"]), None)
    place = get_location(player.where(moment))
    body: dict[str, Any] = {
        "title": place.title if place else "Школа боевых искусств",
        "slots": MAX_ABILITIES,
        # Вкладка «Ваши приёмы»: что боец знает и чем за это платил
        "known": [
            trick_row(ability, tier) for ability, tier in known(player)
        ],
        "deeds": deed_rows(counts),
        # Вкладка «Обучение»: что учится, что набрано и что можно взять
        "started": None if busy is None else learning_row(busy),
        "ready": [learning_row(one) for one in rows if one["ready"]],
        # Пока учится один, каталог пуст: разом учат один приём
        "shelf": (
            []
            if busy is not None
            else [shelf_row(player, one, taken) for one in catalogue(player)]
        ),
        # Цены, по которым каталог фильтруют: по приёму на ступень
        "costs": [TIER_COST[tier] for tier in TIERS],
        "said": "",
    }
    body["note"] = _note(body)
    return body


def _note(body: dict[str, Any]) -> str:
    """Строка сверху: что тут делают и что мешает."""
    state = body["started"]
    if state:
        return (
            f"«{state['title']}» в обучении: {state['deed_title'].lower()} "
            f"{state['done']} из {state['needed']}. Дела считаются в любых "
            f"боях — в дуэли, в отряде, в рейде и на осаде. Разом учат "
            f"один приём."
        )
    if body["ready"]:
        titles = ", ".join(f"«{one['title']}»" for one in body["ready"])
        return (
            f"Набрано и ждёт: {titles}. Подтверди — и приём встанет в "
            f"слот; ждать он может сколько угодно, а учить тем временем "
            f"можно следующий."
        )
    if not body["shelf"]:
        return "Все приёмы клуба выучены — учиться больше нечему."
    return (
        "Школа учит любому приёму клуба, и стоит он здесь дешевле всего — "
        "столько, сколько у своего класса. Платят за это не кредитами, а "
        "делами в бою: удар копится точными ударами, уворот — уворотами, "
        "крит — критическими, лечение и парирование — удержанными блоками."
    )


__all__ = ["build_dojo", "deed_rows", "learning_row", "shelf_row", "trick_row"]
