"""Суд в действии: приговор по делу об угоне и его исполнение.

Два дела и обе половины одного правила. **Судья выносит**: смотрит дело,
видит, который это угон по счёту, и назначает штраф со сроком по
лестнице (`bot.game.court`). **Город исполняет**: берёт деньги и сажает.

Между этими половинами может пройти сколько угодно времени — приговор
выносят и заочно. Поэтому исполнение не висит на судье: оно случается
само, в тот миг, когда угонщик появится в городе. Как и всё остальное в
клубе, это ленивый сбор — часов, которые будили бы бота, здесь нет.

**Деньги ищут по порядку.** Карта, потом блокировка счёта на остаток,
потом наличные, потом часы решётки. Порядок не случайный: штраф — это
городское взыскание, и первым делом город идёт в банк; наличные берут у
того, у кого счёта нет вовсе; а час за триста кредитов — последнее
средство, когда взять больше нечего.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.court import extra_hours, sentence_for, sentence_text
from bot.game.health import now_ts
from bot.game.police import PRISON
from bot.models import Player

logger = logging.getLogger(__name__)

# Кто в суде судит. Секретарь ведёт протокол, а приговор выносит тот,
# кто за него отвечает: судья и его помощник
JUDGES: frozenset[str] = frozenset({"chief_judge", "judge_aide"})


class CourtError(Exception):
    """Отказ по судебным делам, который показывают игроку как есть."""


def judges(player: Player) -> bool:
    """Выносит ли этот боец приговоры."""
    return player.job_code in JUDGES


async def pending_for(db: Database, thief_id: int) -> list[dict]:
    """Приговоры, которые ждут этого бойца: вынесены, но не исполнены."""
    return [
        one
        for one in await db.theft_cases(judged=True, thief_id=thief_id)
        if not one["served_at"]
    ]


async def docket(db: Database, now: int | None = None) -> list[dict]:
    """Дела, ждущие приговора: кого судить, за что и по какой ступени."""
    moment = now_ts() if now is None else now
    out: list[dict] = []
    for case in await db.theft_cases(judged=False):
        thief = await db.get_player(int(case["thief_id"]))
        owner = await db.get_player(int(case["owner_id"]))
        times = await db.theft_count(int(case["thief_id"]), moment) + 1
        fine, hours = sentence_for(times)
        out.append(
            {
                "id": int(case["id"]),
                "thief_id": int(case["thief_id"]),
                "thief": thief.nickname if thief else "угонщик",
                "owner": owner.nickname if owner else "хозяин",
                "plate": str(case["plate"]),
                "code": str(case["code"]),
                "stolen_at": int(case["stolen_at"]),
                "seized": bool(case["seized_at"]),
                "times": times,
                "fine": fine,
                "jail_hours": hours,
                "sentence": sentence_text(fine, hours),
            }
        )
    return out


async def sentence(
    db: Database, judge: Player, case_id: int, now: int | None = None
) -> str:
    """Вынести приговор по делу. Мера — по лестнице, а не по настроению.

    Заочно — тоже приговор: угонщика в зале не ждут. Исполнится он в
    тот миг, когда тот появится в городе.
    """
    moment = now_ts() if now is None else now
    if not judges(judge):
        raise CourtError("Приговоры выносит судья, а не всякий.")
    if judge.where(moment) != "courthouse":
        raise CourtError("Приговор выносят в суде.")
    case = await db.theft_case(case_id)
    if case is None or not case["filed_at"]:
        raise CourtError("Такого дела в суде нет.")
    if case["judged_at"]:
        raise CourtError("По этому делу приговор уже вынесен.")
    if int(case["thief_id"]) == judge.user_id:
        raise CourtError("Себя не судят.")

    times = await db.theft_count(int(case["thief_id"]), moment) + 1
    fine, hours = sentence_for(times)
    if not await db.judge_theft(case_id, judge.user_id, fine, hours, moment):
        raise CourtError("По этому делу приговор уже вынесен.")
    logger.info(
        "Судья %s вынес приговор по делу %s: %s и %s ч",
        judge.user_id, case_id, fine, hours,
    )

    thief = await db.get_player(int(case["thief_id"]))
    name = thief.nickname if thief else "угонщик"
    said = f"{name}: {sentence_text(fine, hours)}. Угон {times}-й по счёту."
    if thief is not None:
        done = await apply_pending(db, thief, moment)
        if done:
            said += " Исполнено."
        else:
            said += " Исполнится, когда он появится в городе."
    return said


async def apply_pending(
    db: Database, player: Player, now: int | None = None
) -> str:
    """Исполнить всё, что по этому бойцу вынесено и не исполнено.

    Зовётся отовсюду, где боец показывается городу: приговор заочный, и
    ждать, пока угонщик сам придёт в суд, город не станет.
    """
    moment = now_ts() if now is None else now
    cases = await pending_for(db, player.user_id)
    if not cases:
        return ""
    lines: list[str] = []
    for case in cases:
        fine = int(case["fine"])
        hours = int(case["jail_hours"])
        unpaid = await take_fine(db, player, fine, moment)
        total = hours + extra_hours(unpaid)
        if total:
            await lock_up(db, player, total, moment)
        await db.serve_theft(int(case["id"]), moment)
        said = f"Приговор по делу об угоне {case['plate']}: "
        said += sentence_text(fine - unpaid, total) if (fine - unpaid or total) \
            else "взыскать нечего"
        if unpaid:
            said += f". Недоплата {unpaid} 💰 — это ещё {extra_hours(unpaid)} ч"
        lines.append(said + ".")
        logger.info(
            "Приговор по делу %s исполнен: боец %s, штраф %s, часов %s",
            case["id"], player.user_id, fine - unpaid, total,
        )
    return " ".join(lines)


async def take_fine(
    db: Database, player: Player, fine: int, now: int
) -> int:
    """Взять штраф. Отдаёт, сколько взять не удалось.

    Со счёта — если он есть: штраф городской, и первым делом город идёт
    в банк. Не хватило — остаток становится долгом, и счёт блокируется
    до пополнения. Счёта нет — берут наличными.
    """
    if fine <= 0:
        return 0
    if player.has_account:
        take = min(player.account_balance, fine)
        player.account_balance -= take
        left = fine - take
        if left:
            # Счёт блокируется на остаток: карта не платит, а всё, что
            # на счёт придёт, уходит в долг. Часами такую недоплату не
            # берут — у города есть где её дождаться
            player.account_debt += left
        await db.save_player(player)
        return 0
    take = min(player.credits, fine)
    player.credits -= take
    await db.save_player(player)
    return fine - take


async def lock_up(
    db: Database, player: Player, hours: int, now: int
) -> int:
    """Посадить на столько часов по приговору суда.

    Отсидка судебная, и счётчик задержаний она не трогает: он считает,
    сколько раз человека брала полиция, а не сколько раз его осудили.
    """
    sitting, times = await db.jail_term(player.user_id)
    until = max(now, sitting) + hours * 3_600
    await db.put_in_jail(player.user_id, until, times)
    player.location = PRISON
    player.travel_to = None
    player.arrives_at = 0
    await db.save_player(player)
    return until


__all__ = [
    "CourtError",
    "JUDGES",
    "apply_pending",
    "docket",
    "judges",
    "lock_up",
    "pending_for",
    "sentence",
    "take_fine",
]
