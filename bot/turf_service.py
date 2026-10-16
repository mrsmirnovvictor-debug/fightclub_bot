"""Война за территории: доход, гарнизон и штурм точки.

Правила держит `bot.game.turf`, хранение — `bot.database`, экран —
`bot.webapp.turf`. Здесь то, что меняет состояние: начислить доход,
собрать гарнизон и развести штурм.

**Доход сводится на месте, как неделя работы и срок аренды.** Часов,
которые ходили бы по базе раз в сутки, в клубе нет ни у чего. Доход
начисляется в тот миг, когда на точки смотрят: сколько полных суток
точка была твоей, столько раз она и заплатит. Неполные сутки не
пропадают — они переезжают в следующий расчёт, и владелец, не заходивший
месяц, не теряет ни кредита.

**Гарнизон собирается из живых бойцов.** Защитник не обязан сидеть в
сети и ждать нападения: его семью представляют её же люди — их
настоящие характеристики, их настоящие вещи. Поэтому гарнизон это не
«боссы с потолка», а `Fighter.from_player` по старшим семьи.

**Ничьих точек не бывает.** За пустой точкой стоит городская охрана, и
её тоже надо бить. Иначе первый зашедший снял бы весь город без единого
удара.

**Бой разводится здесь, а не на панели.** Штурм идёт волнами тем же
движком, что рейд и дуэль: `resolve_round`, та же усталость, те же
приёмы. Разводится он за один запрос и возвращает журнал — обе стороны
читают его потом. Живая панель с кнопками, как у рейда, сюда
подключается отдельно: граница для этого проведена нарочно — гарнизон
собирает одна функция, исход разбирает другая, а между ними лежит
простой список бойцов.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from bot.database import Database
from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.combat import Fighter, random_action, resolve_round
from bot.game.health import now_ts
from bot.game.services import get_service
from bot.game.turf import (
    CLAN,
    MAX_GARRISON,
    MAX_SPOTS_PER_OWNER,
    SERVICE,
    SPOTS,
    Owner,
    Spot,
    calm_left,
    get_spot,
    owner_of_clan,
    owner_of_service,
    payout,
)
from bot.models import Player

logger = logging.getLogger(__name__)

# Сколько волн терпит штурм, прежде чем судья разведёт стороны. Рейд без
# потолка живёт на усталости, но усталость замедлена впятеро, и штурм
# двух полных отрядов мог бы идти до скончания века
MAX_WAVES = 40

# Городская охрана: кто стоит на ничьей точке. Уровень берётся по
# нападающим — охрана не должна быть ни бесплатной, ни непроходимой
GUARD_CLASS = "tank"
GUARD_SIZE = 3


class TurfError(Exception):
    """Отказ по территории, который показывают игроку как есть."""


@dataclass
class Holding:
    """Точка глазами службы: правила, владелец и что с ней сейчас."""

    spot: Spot
    owner: Owner
    taken_at: int = 0
    fought_at: int = 0
    # Сколько доход накапал владельцу и не было начислено
    due: int = 0


@dataclass
class Assault:
    """Чем кончился штурм: кто бил, кто держал и что стало с точкой."""

    spot: Spot
    attacker: Owner
    defender: Owner
    won: bool
    waves: int = 0
    log: list[str] = field(default_factory=list)
    survivors: int = 0


# ---------- кто чем владеет ----------


def owner_of(player: Player) -> Owner:
    """За кого воюет этот боец. Пустой владелец — ни за кого.

    Семья и служба несовместимы, поэтому выбирать не из чего: кто в
    клане — воюет за клан, кто на светлой службе — за службу. Врач и
    секретарь не воюют ни за кого: у них служба без права силы.
    """
    if player.clan is not None:
        return owner_of_clan(player.clan.clan.id)
    from bot.game.services import service_of_job

    service = service_of_job(player.job_code)
    if service is not None and service.lawful:
        return owner_of_service(service.code)
    return Owner()


async def owner_title(db: Database, owner: Owner) -> str:
    """Как зовут владельца — для экрана и журнала."""
    if owner.nobody:
        return "городская охрана"
    if owner.kind == CLAN:
        clan = await db.clan_by_id(owner.clan_id)
        return clan.name if clan else "распавшаяся семья"
    service = get_service(owner.service)
    return service.title if service else owner.service


# ---------- доход ----------


async def survey(
    db: Database, now: int | None = None
) -> list[Holding]:
    """Все точки города: кто держит и сколько накапало. Сводит доход.

    Зовётся отовсюду, где на территории смотрят: своих часов у клуба
    нет, и доход начисляется тем, кто пришёл посмотреть, — в том числе
    чужим владельцам. Это не вольность: считается он по тем же правилам
    и в пользу отсутствующего.
    """
    moment = now_ts() if now is None else now
    rows = await db.turf_rows()
    out: list[Holding] = []
    for spot in SPOTS:
        row = rows.get(spot.code)
        if row is None:
            out.append(Holding(spot=spot, owner=Owner()))
            continue
        due = 0
        if not row.owner.nobody:
            due, paid_until = payout(spot, row.paid_until, moment)
            if due:
                await _pay(db, spot, row.owner, due, paid_until)
        out.append(
            Holding(
                spot=spot,
                owner=row.owner,
                taken_at=row.taken_at,
                fought_at=row.fought_at,
                due=due,
            )
        )
    return out


async def _pay(
    db: Database, spot: Spot, owner: Owner, amount: int, paid_until: int
) -> None:
    """Положить доход в казну владельца и передвинуть час расчёта."""
    if owner.kind == CLAN:
        await db.move_vault(owner.clan_id, amount)
    elif owner.kind == SERVICE:
        await db.add_to_service_vault(owner.service, amount)
    await db.pay_turf(spot.code, paid_until)
    logger.info(
        "Точка %s принесла %s владельцу %s:%s",
        spot.code, amount, owner.kind, owner.key,
    )


async def held_by(db: Database, owner: Owner, now: int | None = None) -> list[Holding]:
    """Точки этого владельца."""
    rows = await survey(db, now)
    return [one for one in rows if one.owner.same(owner)]


async def income_of(db: Database, owner: Owner, now: int | None = None) -> int:
    """Сколько владелец получает в сутки со всех своих точек."""
    return sum(one.spot.income for one in await held_by(db, owner, now))


async def vault_of(db: Database, owner: Owner) -> int:
    """Казна владельца: сейф семьи или казна службы."""
    if owner.kind == CLAN:
        clan = await db.clan_by_id(owner.clan_id)
        return clan.vault if clan else 0
    if owner.kind == SERVICE:
        return await db.service_vault(owner.service)
    return 0


# ---------- гарнизон ----------


async def garrison(
    db: Database, owner: Owner, levels: list[int], now: int | None = None
) -> list[Fighter]:
    """Кто защищает точку этого владельца.

    Ничья точка — городская охрана, и уровень у неё по нападающим: она
    не должна быть ни бесплатной, ни непроходимой.
    """
    if owner.nobody:
        return _guards(levels)
    players = await _members(db, owner)
    if not players:
        # Семья распалась или со службы все ушли: точку держит охрана
        return _guards(levels)
    return [
        Fighter.from_player(one, armed=True)
        for one in players[:MAX_GARRISON]
    ]


async def _members(db: Database, owner: Owner) -> list[Player]:
    """Живые бойцы владельца, старшие первыми."""
    ids: list[int] = []
    if owner.kind == CLAN:
        roster = await db.roster_of(owner.clan_id)
        if roster is not None:
            ids = [one.user_id for one in roster.members]
    elif owner.kind == SERVICE:
        service = get_service(owner.service)
        if service is not None:
            taken = await db.taken_jobs()
            for code in service.jobs:
                ids.extend(one["user_id"] for one in taken.get(code, []))
    out: list[Player] = []
    for user_id in ids:
        player = await db.get_player(user_id)
        if player is not None:
            out.append(player)
    return out


def _guards(levels: list[int]) -> list[Fighter]:
    """Городская охрана: трое по уровню нападающих."""
    level = max(1, round(sum(levels) / len(levels))) if levels else 1
    fclass = FIGHTER_CLASSES[GUARD_CLASS]
    out = []
    for number in range(GUARD_SIZE):
        stats = Stats(
            strength=4 + level,
            agility=2 + level // 2,
            intuition=2 + level // 2,
            endurance=6 + level,
        )
        out.append(
            Fighter(
                user_id=-(number + 1),
                name=f"Охранник {number + 1}",
                fclass=fclass,
                stats=stats,
                level=level,
            )
        )
    return out


# ---------- штурм ----------


async def assault(
    db: Database,
    player: Player,
    code: str,
    squad: list[int],
    now: int | None = None,
    rng: random.Random | None = None,
) -> Assault:
    """Взять точку боем. Отряд собирает тот, кто нападает."""
    moment = now_ts() if now is None else now
    rng = rng or random.Random()
    spot = get_spot(code)
    if spot is None:
        raise TurfError("Такой точки в городе нет.")

    mine = owner_of(player)
    if mine.nobody:
        raise TurfError(
            "За территории воюют семьи и службы. Вступи в клан или иди "
            "на службу."
        )
    rows = await survey(db, moment)
    here = next(one for one in rows if one.spot.code == code)
    if here.owner.same(mine):
        raise TurfError(f"{spot.title} и так за вами.")
    left = calm_left(here.fought_at, moment)
    if left:
        hours = max(1, left // 3600)
        raise TurfError(
            f"За {spot.genitive} недавно дрались — точка отдыхает ещё "
            f"{hours} ч."
        )
    held = [one for one in rows if one.owner.same(mine)]
    if len(held) >= MAX_SPOTS_PER_OWNER:
        raise TurfError(
            f"Больше {MAX_SPOTS_PER_OWNER} точек разом не удержать. "
            "Сперва отдайте какую-нибудь."
        )

    attackers = await _squad(db, mine, squad)
    if not attackers:
        raise TurfError("Отряд пуст: некому идти на штурм.")
    levels = [one.level for one in attackers]
    defenders = await garrison(db, here.owner, levels, moment)

    won, waves, log = _fight(attackers, defenders, rng)
    if won:
        await db.take_turf(code, mine.kind, mine.key, moment)
    else:
        await db.hold_turf(code, moment)
    logger.info(
        "Штурм точки %s отрядом %s:%s — %s за %s волн",
        code, mine.kind, mine.key, "взяли" if won else "отбили", waves,
    )
    return Assault(
        spot=spot,
        attacker=mine,
        defender=here.owner,
        won=won,
        waves=waves,
        log=log,
        survivors=sum(1 for one in attackers if one.alive),
    )


async def _squad(db: Database, owner: Owner, squad: list[int]) -> list[Fighter]:
    """Отряд нападающих: только свои и только те, кто может драться."""
    members = {one.user_id: one for one in await _members(db, owner)}
    out = []
    for user_id in squad[:MAX_GARRISON]:
        player = members.get(user_id)
        if player is None or not player.can_fight():
            continue
        out.append(Fighter.from_player(player, armed=True))
    return out


def _fight(
    attackers: list[Fighter], defenders: list[Fighter], rng: random.Random
) -> tuple[bool, int, list[str]]:
    """Развести штурм волнами. True — нападающие взяли точку.

    Волна — по разу на каждого живого с обеих сторон, как в рейде: номер
    волны идёт в движок усталости, и сквозной счётчик разменов тут не
    годится — он рос бы вдесятеро быстрее в большом отряде.
    """
    log: list[str] = []
    wave = 0
    while wave < MAX_WAVES:
        wave += 1
        for side, foes in ((attackers, defenders), (defenders, attackers)):
            for one in side:
                if not one.alive:
                    continue
                target = next((foe for foe in foes if foe.alive), None)
                if target is None:
                    break
                resolve_round(
                    one,
                    random_action(one, rng),
                    target,
                    random_action(target, rng),
                    wave,
                    rng,
                    limit=MAX_WAVES,
                )
        if not any(one.alive for one in defenders):
            log.append(f"Волна {wave}: гарнизон смят.")
            return True, wave, log
        if not any(one.alive for one in attackers):
            log.append(f"Волна {wave}: отряд не дошёл.")
            return False, wave, log
    log.append(f"Волна {wave}: судья развёл стороны — точка осталась за своими.")
    return False, wave, log


__all__ = [
    "MAX_WAVES",
    "Assault",
    "Holding",
    "TurfError",
    "assault",
    "garrison",
    "held_by",
    "income_of",
    "owner_of",
    "owner_title",
    "survey",
    "vault_of",
]
