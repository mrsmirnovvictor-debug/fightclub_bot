"""Школа боевых искусств: записаться, набрать дела, выучить приём.

Правила — в `bot.game.dojo`, здесь дела.

**Дела пишет бой, а не школа.** Движок начисляет их тем же событием, что
кормит энергию (`combat._fill_energy`), складывает бойцу в память раунда,
а служба боя одной строкой сливает их в базу — `count_deeds`. Поэтому
школе считать нечего: она только смотрит в ведомость.

**Ведомость пожизненная, зачёт — от записи.** В базе лежит всё, что боец
сделал за свою жизнь, а при записи в школу запоминается, сколько он
сделал к этому часу. Учится он с этой отметки: иначе ветеран приходил бы
в школу и выходил из неё готовым приёмом, а «взять в обучение» ничего не
значило бы.

**В обучении один приём.** Записался на второй — первый брошен вместе со
своими делами: учится тело, а не ведомость.
"""

from __future__ import annotations

import logging

from bot.content.abilities import ABILITIES, get_ability
from bot.database import Database
from bot.game.abilities import Ability, Source
from bot.game.dojo import (
    BRANCHES,
    branch_of,
    deed_name,
    deed_of,
    done as deeds_done,
    needed,
    price,
    ready,
)
from bot.game.health import now_ts
from bot.models import Player

logger = logging.getLogger(__name__)


class DojoError(Exception):
    """Отказ школы, который показывают игроку как есть."""


async def count_deeds(db: Database, fighters: dict[int, object]) -> int:
    """Слить боевые дела этого раунда в ведомость. Отдаёт, сколько дел.

    Зовут это все бои города — дуэль, групповой, рейд, осада, драка в
    подворотне, — и зовут одной строкой. Боец помнит свои дела до слива,
    а слив их у него забирает: второй вызов на том же раунде ничего не
    запишет, и ошибкой это не будет.

    Противники в счёт не идут: ведомость есть только у людей, а номера у
    боссов и наёмных отрицательные.
    """
    written = 0
    for fighter in fighters.values():
        user_id = int(getattr(fighter, "user_id", 0))
        tally = getattr(fighter, "deeds", None)
        if user_id <= 0 or not tally:
            continue
        counts = {source.value: int(count) for source, count in tally.items()}
        tally.clear()
        await db.add_deeds(user_id, counts)
        written += sum(counts.values())
    return written


async def totals(db: Database, user_id: int) -> dict[Source, int]:
    """Вся ведомость бойца: дело → сколько сделано за жизнь."""
    rows = await db.deed_counts(user_id)
    return {source: int(rows.get(source.value, 0)) for source in Source}


async def training(
    db: Database, player: Player, now: int | None = None
) -> dict | None:
    """На что боец записан и сколько уже набрал. None — ни на что."""
    row = await db.dojo_row(player.user_id)
    if row is None:
        return None
    ability = get_ability(str(row["code"]))
    if ability is None:  # pragma: no cover - приём убрали из каталога
        await db.clear_dojo(player.user_id)
        return None
    deed = deed_of(ability)
    total = (await totals(db, player.user_id))[deed]
    baseline = int(row["baseline"])
    return {
        "ability": ability,
        "deed": deed,
        "done": deeds_done(total, baseline, ability),
        "needed": needed(ability),
        "ready": ready(total, baseline, ability),
        "started_at": int(row["started_at"]),
        "forget": forget_for(player, ability),
    }


def forget_for(player: Player, ability: Ability) -> Ability | None:
    """Какой приём освободит место этому. None — место свободно.

    Место занимает тот, что стоит столько же энергии: ступеней четыре,
    слотов четыре, и у выросшего бойца занято всё.
    """
    for code, tier in player.loadout.slots.items():
        if tier == ability.tier and code != ability.code:
            return get_ability(code)
    return None


async def start(
    db: Database, player: Player, code: str, now: int | None = None
) -> str:
    """Взять приём в обучение. Прежнее обучение при этом брошено."""
    moment = now_ts() if now is None else now
    ability = get_ability(code)
    if ability is None:
        raise DojoError("Такого приёма в школе нет.")
    if code in player.loadout:
        raise DojoError(f"«{ability.title}» боец и так знает.")
    was = await db.dojo_row(player.user_id)
    if was is not None and str(was["code"]) == code:
        raise DojoError(f"«{ability.title}» и так в обучении.")
    deed = deed_of(ability)
    total = (await totals(db, player.user_id))[deed]
    await db.set_dojo(player.user_id, code, deed.value, total, moment)
    logger.info(
        "Боец %s взял в обучение %s (нужно %s %s)",
        player.user_id, code, needed(ability), deed.value,
    )
    dropped = ""
    if was is not None:
        old = get_ability(str(was["code"]))
        if old is not None:
            dropped = (
                f" «{old.title}» брошен: дела, сделанные для него, не "
                f"переносятся."
            )
    return (
        f"«{ability.title}» в обучении: нужно {needed(ability)} "
        f"{deed_name(deed, needed(ability))}.{dropped}"
    )


async def stop(db: Database, player: Player) -> str:
    """Бросить обучение. Сделанные дела при этом пропадают."""
    row = await db.dojo_row(player.user_id)
    if row is None:
        raise DojoError("Боец ни на что не записан.")
    ability = get_ability(str(row["code"]))
    await db.clear_dojo(player.user_id)
    logger.info("Боец %s бросил обучение %s", player.user_id, row["code"])
    title = f"«{ability.title}»" if ability else "Приём"
    return f"{title} брошен. Дела, сделанные для него, не сохранились."


async def learn(
    db: Database, player: Player, now: int | None = None
) -> Ability:
    """Выучить набранный приём. Прежний с той же ценой забывается."""
    moment = now_ts() if now is None else now
    state = await training(db, player, moment)
    if state is None:
        raise DojoError("Боец ни на что не записан: сперва выбери приём.")
    ability: Ability = state["ability"]
    if not state["ready"]:
        left = state["needed"] - state["done"]
        raise DojoError(
            f"Рано: до «{ability.title}» осталось {left} "
            f"{deed_name(state['deed'], left)}."
        )
    if ability.code in player.loadout:  # pragma: no cover - записались и выучили
        raise DojoError(f"«{ability.title}» боец и так знает.")
    forget = forget_for(player, ability)
    try:
        if forget is None:
            player.loadout.learn(ability.code, ability.tier)
        else:
            player.loadout.relearn(forget.code, ability.code, ability.tier)
    except ValueError as error:
        raise DojoError(str(error)) from error
    await db.save_abilities(player.user_id, player.loadout)
    await db.clear_dojo(player.user_id)
    logger.info(
        "Боец %s выучил в школе %s вместо %s",
        player.user_id, ability.code, forget.code if forget else "ничего",
    )
    return ability


def catalogue(player: Player) -> tuple[Ability, ...]:
    """Все приёмы школы: по ветвям, внутри ветви — по ступеням.

    В школе лежат все приёмы клуба — и свои, и чужих классов: в этом её
    смысл. Уже выученные из списка не уходят, они просто помечены.

    Порядок не алфавитный, а по ветвям: приём выбирают от дела, которым
    за него платить, и четыре лестницы рядом читаются, а шестнадцать
    приёмов вперемешку — нет.
    """
    return tuple(
        sorted(
            ABILITIES,
            key=lambda one: (BRANCHES.index(branch_of(one)), one.tier),
        )
    )


__all__ = [
    "DojoError",
    "catalogue",
    "count_deeds",
    "forget_for",
    "learn",
    "price",
    "start",
    "stop",
    "totals",
    "training",
]
