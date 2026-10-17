"""Наём: кто кого берёт, почём и кого ставит на точку.

Правила и цены — в `bot.game.hirelings`. Здесь действия: нанять,
выставить на точку, собрать из нанятых гарнизон.

**Платит человек, а не казна.** Наёмник выходит из кармана того, кто
его взял, и в его же счёт идёт. Семья богатеет людьми, а не сейфом: сто
тысяч в сейфе не поставят на точку ни одного человека, если в семье
никого нет.

**Чин решает, сколько их на руках.** Три у рядового, четыре у старшего,
пять у самого старшего. Чины двух лестниц считаются одной меркой:
приближённый в семье и офицер в городе — одна ступень.

**Нанятый гибнет насовсем.** Отбили точку — его нет, и место в счёте
освобождается. Это и делает охрану расходом, а не покупкой.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.combat import Fighter
from bot.game.equipment import CATALOGUE, Equipment, OwnedItem, Slot
from bot.game.health import now_ts
from bot.game.hirelings import (
    GUARD_CLASS,
    GUARD_LEVEL,
    GUARD_SIZE,
    MAX_POSTED,
    MERC,
    PATROL,
    PER_PERSON,
    cap_for,
    get_grade,
    grades_for,
    guard_picture,
    hired_left,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class HireError(Exception):
    """Отказ по найму, который показывают игроку как есть."""


# ---------- чей наём ----------


def side_of(player: Player) -> str:
    """Кого нанимает этот боец. Пусто — никого: он сам по себе."""
    from bot.police_service import serves_in_police

    if player.clan is not None:
        return MERC
    if serves_in_police(player):
        return PATROL
    return ""


def rank_of(player: Player) -> str:
    """Чин бойца в той лестнице, по которой ему считают счёт."""
    if player.clan is not None:
        return player.clan.rank.value
    return player.job_code


def cap_of(player: Player) -> int:
    return cap_for(rank_of(player))


# ---------- наём ----------


async def hire(
    db: Database, player: Player, code: str, now: int | None = None
) -> dict:
    """Взять нанятого на свой счёт и за свои деньги."""
    moment = now_ts() if now is None else now
    side = side_of(player)
    if not side:
        raise HireError("Нанимают семьи и город. Ты пока сам по себе.")
    grade = get_grade(code)
    if grade is None or grade.side != side:
        raise HireError("Такого к себе не берут.")
    mine = await db.hirelings_of(player.user_id)
    cap = cap_of(player)
    if len(mine) >= cap:
        raise HireError(
            f"Больше {cap} на руках не держат. Выставь или потеряй кого-нибудь."
        )
    if player.credits < grade.price:
        raise HireError(f"Не хватает: {grade.title} стоит {grade.price} 💰.")

    player.credits -= grade.price
    await db.save_player(player)
    await db.hire(player.user_id, side, grade.code, moment)
    logger.info("Боец %s нанял %s", player.user_id, grade.code)
    return {"grade": grade, "left": hired_left(cap, len(mine) + 1)}


async def post(
    db: Database, player: Player, hire_id: int, spot: str,
    now: int | None = None,
) -> dict:
    """Выставить своего нанятого на точку."""
    from bot.game.turf import get_spot
    from bot.turf_service import owner_of, survey

    moment = now_ts() if now is None else now
    place = get_spot(spot)
    if place is None:
        raise HireError("Такой точки в городе нет.")
    if player.where(moment) != spot:
        raise HireError("Охрану ставят на месте, а не из кармана.")
    mine = owner_of(player)
    if mine.nobody:
        raise HireError("Охрану ставят за семью или за город.")
    rows = await survey(db, moment)
    here = next(one for one in rows if one.spot.code == spot)
    if not here.owner.same(mine):
        raise HireError("Точка не ваша. Сперва возьмите её.")

    standing = await db.hirelings_at(spot)
    if len(standing) >= MAX_POSTED:
        raise HireError(f"На точке уже {MAX_POSTED} — больше не ставят.")
    ours = [one for one in standing if int(one["owner_id"]) == player.user_id]
    if len(ours) >= PER_PERSON:
        raise HireError("От тебя здесь уже стоит человек. Своего второго нельзя.")

    if not await db.post_hireling(hire_id, player.user_id, spot):
        raise HireError("Этого выставить не вышло: он не твой или уже на точке.")
    logger.info("Боец %s выставил %s на %s", player.user_id, hire_id, spot)
    return {"total": len(standing) + 1, "max": MAX_POSTED}


async def roster(db: Database, player: Player) -> dict:
    """Что у бойца на руках и что ему предлагают взять."""
    side = side_of(player)
    mine = await db.hirelings_of(player.user_id) if side else []
    cap = cap_of(player)
    return {
        "side": side,
        "cap": cap,
        "hired": len(mine),
        "left": hired_left(cap, len(mine)),
        "mine": [
            {
                "id": int(one["id"]),
                "grade": str(one["grade"]),
                "title": _title(str(one["grade"])),
                "level": _level(str(one["grade"])),
                "picture": _picture(str(one["grade"])),
                "spot": str(one["spot"]),
            }
            for one in mine
        ],
        "grades": [
            {
                "code": one.code,
                "title": one.title,
                "level": one.level,
                "fclass": FIGHTER_CLASSES[one.fclass].emoji,
                "price": one.price,
                "picture": one.picture,
            }
            for one in (grades_for(side) if side else ())
        ],
    }


def _title(code: str) -> str:
    grade = get_grade(code)
    return grade.title if grade else code


def _level(code: str) -> int:
    grade = get_grade(code)
    return grade.level if grade else 1


def _picture(code: str) -> str:
    grade = get_grade(code)
    return grade.picture if grade else ""


# ---------- кто стоит на точке ----------


def _weapon_for(level: int, fclass: str) -> OwnedItem | None:
    """Оружие из лавки по росту и по классу.

    Отдельных наборов у нанятых пока нет, и берём мы самое дорогое с
    обычного прилавка, что этому уровню разрешено и этому классу
    годится: голыми руками наёмника не выпускают, но и фанатская бита
    за полторы тысячи ему не положена.
    """
    fits = [
        item
        for item in CATALOGUE.values()
        if item.slot == Slot.WEAPON
        and item.price
        and not item.stars
        and not item.retired
        and item.level_required <= level
        and (not item.for_classes or fclass in item.for_classes)
    ]
    if not fits:  # pragma: no cover - прилавок не бывает пустым
        return None
    best = max(fits, key=lambda item: (item.price, item.code))
    return OwnedItem(item=best, slot=Slot.WEAPON)


def _stats_for(level: int) -> Stats:
    """Характеристики нанятого: ровные, по уровню и без изысков."""
    return Stats(
        strength=4 + level,
        agility=2 + level // 2,
        intuition=2 + level // 2,
        endurance=6 + level,
    )


def _npc(
    number: int, name: str, fclass: str, level: int, picture: str = ""
) -> Fighter:
    weapon = _weapon_for(level, fclass)
    return Fighter(
        user_id=number,
        name=name,
        fclass=FIGHTER_CLASSES[fclass],
        stats=_stats_for(level),
        level=level,
        equipment=Equipment(items={Slot.WEAPON: weapon} if weapon else {}),
        picture=picture,
    )


def guards(start: int = -1) -> list[Fighter]:
    """Городская охрана: трое четвёртого уровня, класс танковый.

    Ничья и бесплатная. Стоит на всякой точке, которой не владеет семья:
    дом не бывает без сторожа, даже когда за него никто не дерётся.
    """
    face = guard_picture()
    return [
        _npc(
            start - number, f"Охранник {number + 1}", GUARD_CLASS,
            GUARD_LEVEL, face,
        )
        for number in range(GUARD_SIZE)
    ]


async def posted(db: Database, spot: str, start: int = -100) -> list[Fighter]:
    """Нанятые, выставленные на эту точку, как бойцы."""
    out: list[Fighter] = []
    for number, row in enumerate(await db.hirelings_at(spot)):
        grade = get_grade(str(row["grade"]))
        if grade is None:  # pragma: no cover - ступень из будущей версии
            continue
        out.append(
            _npc(
                start - number, grade.title, grade.fclass, grade.level,
                grade.picture,
            )
        )
    return out


__all__ = [
    "HireError",
    "cap_of",
    "guards",
    "hire",
    "post",
    "posted",
    "rank_of",
    "roster",
    "side_of",
]
