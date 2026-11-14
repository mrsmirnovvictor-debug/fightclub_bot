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

**Учится разом один приём, а набранный ждёт сколько угодно.** Это два
разных состояния одной записи, и различает их сама ведомость: дел
набрано меньше нужного — приём учится, набрано сколько надо — приём
готов и ждёт подтверждения. Пока он ждёт, боец берётся за следующий:
новый приём занимает место старого, и торопить с этим выбором незачем.

Отсюда и устройство базы: строк у бойца столько, сколько приёмов он не
довёл до слота, а «готов или нет» не хранится вовсе — оно считается.
Хранить его значило бы держать две правды об одном.
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


async def learning(
    db: Database, player: Player, now: int | None = None
) -> list[dict]:
    """Все незакрытые записи бойца: что учится и что уже набрано.

    Запись на приём, который боец успел получить с уровня, снимается
    тут же: учить выученное незачем, и держать такую строку значило бы
    однажды предложить забыть приём ради него самого.
    """
    counts = await totals(db, player.user_id)
    out = []
    for row in await db.dojo_rows(player.user_id):
        ability = get_ability(str(row["code"]))
        if ability is None or ability.code in player.loadout:
            await db.clear_dojo(player.user_id, str(row["code"]))
            continue
        deed = deed_of(ability)
        total = counts[deed]
        baseline = int(row["baseline"])
        out.append({
            "ability": ability,
            "deed": deed,
            "done": deeds_done(total, baseline, ability),
            "needed": needed(ability),
            "ready": ready(total, baseline, ability),
            "started_at": int(row["started_at"]),
            "forget": forget_for(player, ability),
        })
    return out


async def started(
    db: Database, player: Player, now: int | None = None
) -> dict | None:
    """Приём, который учится прямо сейчас. None — ни один.

    Набранные сюда не идут: они уже выучены всем, кроме подтверждения, и
    место в обучении не занимают.
    """
    for one in await learning(db, player, now):
        if not one["ready"]:
            return one
    return None


async def waiting(
    db: Database, player: Player, now: int | None = None
) -> list[dict]:
    """Набранные приёмы, которые ждут подтверждения."""
    return [one for one in await learning(db, player, now) if one["ready"]]


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
    """Взять приём в обучение. Разом учится только один."""
    moment = now_ts() if now is None else now
    ability = get_ability(code)
    if ability is None:
        raise DojoError("Такого приёма в школе нет.")
    if code in player.loadout:
        raise DojoError(f"«{ability.title}» боец и так знает.")
    rows = await learning(db, player, moment)
    mine = next((one for one in rows if one["ability"].code == code), None)
    if mine is not None:
        raise DojoError(
            f"«{ability.title}» уже набран — осталось подтвердить."
            if mine["ready"]
            else f"«{ability.title}» и так в обучении."
        )
    busy = next((one for one in rows if not one["ready"]), None)
    if busy is not None:
        raise DojoError(
            f"Разом учат один приём, и это «{busy['ability'].title}»: "
            f"{busy['done']} из {busy['needed']}. Доучи его или брось."
        )
    deed = deed_of(ability)
    total = (await totals(db, player.user_id))[deed]
    await db.set_dojo(player.user_id, code, deed.value, total, moment)
    logger.info(
        "Боец %s взял в обучение %s (нужно %s %s)",
        player.user_id, code, needed(ability), deed.value,
    )
    return (
        f"«{ability.title}» в обучении: нужно {needed(ability)} "
        f"{deed_name(deed, needed(ability))}."
    )


async def stop(
    db: Database, player: Player, code: str = "", now: int | None = None
) -> str:
    """Бросить обучение. Сделанные дела при этом пропадают.

    Набранный приём бросить нельзя: он уже выучен всем, кроме
    подтверждения, и ждать он может сколько угодно — выбрасывать за
    бойца то, за что он отработал пятьсот ударов, школа не станет.
    """
    moment = now_ts() if now is None else now
    rows = await learning(db, player, moment)
    if code:
        one = next((row for row in rows if row["ability"].code == code), None)
    else:
        one = next((row for row in rows if not row["ready"]), None)
    if one is None:
        raise DojoError("Боец ни на что не записан.")
    if one["ready"]:
        raise DojoError(
            f"«{one['ability'].title}» уже набран: его подтверждают, а не "
            f"бросают. Ждать он может сколько угодно."
        )
    await db.clear_dojo(player.user_id, one["ability"].code)
    logger.info(
        "Боец %s бросил обучение %s", player.user_id, one["ability"].code
    )
    return (
        f"«{one['ability'].title}» брошен. Дела, сделанные для него, не "
        f"сохранились."
    )


async def learn(
    db: Database, player: Player, code: str = "", now: int | None = None
) -> Ability:
    """Выучить набранный приём. Прежний с той же ценой забывается."""
    moment = now_ts() if now is None else now
    rows = await learning(db, player, moment)
    if code:
        state = next(
            (one for one in rows if one["ability"].code == code), None
        )
        if state is None:
            raise DojoError("На этот приём боец не записывался.")
    else:
        state = next((one for one in rows if one["ready"]), None)
        if state is None:
            raise DojoError("Набранных приёмов нет: сперва отработай своё.")
    ability: Ability = state["ability"]
    if not state["ready"]:
        left = state["needed"] - state["done"]
        raise DojoError(
            f"Рано: до «{ability.title}» осталось {left} "
            f"{deed_name(state['deed'], left)}."
        )
    forget = state["forget"]
    try:
        if forget is None:
            player.loadout.learn(ability.code, ability.tier)
        else:
            player.loadout.relearn(forget.code, ability.code, ability.tier)
    except ValueError as error:
        raise DojoError(str(error)) from error
    await db.save_abilities(player.user_id, player.loadout)
    await db.clear_dojo(player.user_id, ability.code)
    logger.info(
        "Боец %s выучил в школе %s вместо %s",
        player.user_id, ability.code, forget.code if forget else "ничего",
    )
    return ability


def catalogue(player: Player) -> tuple[Ability, ...]:
    """Приёмы, которые бойцу ещё можно выучить.

    Выученные из списка уходят: учить их второй раз нельзя, и лежат они
    на своей вкладке. Порядок — по ветвям, внутри ветви по ступеням:
    приём выбирают от дела, которым за него платить, и четыре лестницы
    рядом читаются, а шестнадцать приёмов вперемешку — нет.
    """
    return tuple(
        sorted(
            (one for one in ABILITIES if one.code not in player.loadout),
            key=lambda one: (BRANCHES.index(branch_of(one)), one.tier),
        )
    )


__all__ = [
    "DojoError",
    "catalogue",
    "count_deeds",
    "forget_for",
    "learn",
    "learning",
    "price",
    "start",
    "started",
    "stop",
    "totals",
    "waiting",
]
