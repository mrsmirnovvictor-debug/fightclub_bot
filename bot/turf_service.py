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
from bot.game.combat import Fighter, random_action, resolve_round
from bot.game.health import now_ts
from bot.game.services import get_service
from bot.game.turf import (
    CLAN,
    FREE,
    MAX_GARRISON,
    MAX_SPOTS_PER_OWNER,
    SERVICE,
    SPOTS,
    Owner,
    Spot,
    abandoned,
    action_for,
    get_spot,
    final_payout,
    owner_of_clan,
    owner_of_service,
    payout,
    share_of,
)
from bot.models import Player

logger = logging.getLogger(__name__)

# Сколько волн терпит штурм, прежде чем судья разведёт стороны. Рейд без
# потолка живёт на усталости, но усталость замедлена впятеро, и штурм
# двух полных отрядов мог бы идти до скончания века
MAX_WAVES = 40



class TurfError(Exception):
    """Отказ по территории, который показывают игроку как есть."""


@dataclass
class Holding:
    """Точка глазами службы: правила, владелец и что с ней сейчас."""

    spot: Spot
    owner: Owner
    taken_at: int = 0
    # Сколько доход накапал владельцу и не было начислено
    due: int = 0
    # Стоит ли на точке хоть один нанятый. Нет — точка не платит
    guarded: bool = False


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


async def _let_go(db: Database, row, guarded: bool, now: int) -> bool:
    """Отпустить точку, которую держат одной вывеской.

    Возвращает True — дом вернулся городу и дальше считается ничьим.

    **Городу — сразу.** Полиция дом не присваивает, а охраняет: сняли с
    него последнего патрульного — и он в тот же миг под обычной
    городской охраной, как был. Держать за участком пустую вывеску
    сутки значило бы, что город чем-то владеет, а он не владеет.

    **Семье — через сутки.** Час опустения ведём здесь же: выставили
    человека — час сбрасываем, сняли последнего — ставим заново. Иначе
    пришлось бы помнить, когда именно точка опустела, а помнить это
    некому.
    """
    if row.owner.kind == SERVICE:
        if guarded:
            return False
        await db.free_turf(row.code)
        logger.info("Точка %s осталась без охраны и вернулась городу", row.code)
        return True
    if row.owner.kind != CLAN:
        return False
    if guarded:
        if row.empty_since:
            await db.mark_turf_empty(row.code, 0)
        return False
    if not row.empty_since:
        await db.mark_turf_empty(row.code, now)
        return False
    if not abandoned(row.empty_since, now):
        return False
    await db.free_turf(row.code)
    logger.info("Точка %s брошена и вернулась городу", row.code)
    return True


async def owner_badge(db: Database, owner: Owner) -> str:
    """Жетон владельца: знак семьи или значок службы.

    Пусто — ничей дом: у городской охраны своего знака нет, и рисовать
    за неё чужой значок было бы враньём. Её на карте помечают кружком.
    """
    if owner.nobody:
        return ""
    if owner.kind == CLAN:
        clan = await db.clan_by_id(owner.clan_id)
        return clan.picture if clan else ""
    service = get_service(owner.service)
    return service.badge if service else ""


# ---------- доход ----------


async def survey(
    db: Database, now: int | None = None
) -> list[Holding]:
    """Все точки города: кто держит и сколько накапало. Сводит доход.

    Зовётся отовсюду, где на территории смотрят: своих часов у клуба
    нет, и доход начисляется тем, кто пришёл посмотреть, — в том числе
    чужим владельцам. Это не вольность: считается он по тем же правилам
    и в пользу отсутствующего.

    Тем же взглядом сводится и бонусный журнал: часы владения капают
    сюда же, и дописать их надо раньше, чем брошенный дом вернётся
    городу, — иначе последние сутки семьи пропали бы вместе со строкой.
    """
    from bot.bonus_service import sweep

    moment = now_ts() if now is None else now
    await sweep(db, moment)
    rows = await db.turf_rows()
    # Кто на точках стоит. Точка платит, только пока её кто-то охраняет:
    # взятая и брошенная — это просто дом с вывеской, а не доход
    kept = await db.posted_counts()
    out: list[Holding] = []
    for spot in SPOTS:
        row = rows.get(spot.code)
        if row is None:
            out.append(Holding(spot=spot, owner=Owner()))
            continue
        guarded = bool(kept.get(spot.code))
        # Брошенный дом возвращается городу. Считается это здесь же, при
        # взгляде: часов у клуба нет, и сторожить сутки некому — зато
        # всякий, кто посмотрел на карту, досчитывает её за всех
        if await _let_go(db, row, guarded, moment):
            out.append(Holding(spot=spot, owner=Owner()))
            continue
        due = 0
        if not row.owner.nobody:
            due, paid_until = payout(
                spot, row.paid_until, moment, share_of(row.owner.kind)
            )
            if due and guarded:
                await _pay(db, spot, row.owner, due, paid_until)
            elif due:
                # Несчитанные сутки не копятся: поставишь наёмника
                # завтра — за вчерашнее брошенное стояние не дадут
                await db.pay_turf(spot.code, paid_until)
                due = 0
        out.append(
            Holding(
                spot=spot,
                owner=row.owner,
                taken_at=row.taken_at,
                due=due,
                guarded=guarded,
            )
        )
    return out


async def hand_over(
    db: Database, spot: Spot, owner: Owner, since: int, now: int,
    guarded: bool = True,
) -> int:
    """Рассчитать прежнего владельца за неполные сутки перед сменой.

    Обычный расчёт считает только полные сутки, а остаток переезжает в
    следующий — которого у потерявшего точку уже не будет. Держал
    четырнадцать часов, отбили на пятнадцатом: четырнадцать часов его.
    """
    if owner.nobody or not guarded:
        return 0
    due = final_payout(spot, since, now, share_of(owner.kind))
    if due:
        await _pay(db, spot, owner, due, now)
    return due


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
    """Сколько владелец получает в сутки. Брошенные точки не в счёт."""
    return sum(
        int(one.spot.income * share_of(owner.kind))
        for one in await held_by(db, owner, now)
        if one.guarded
    )


async def vault_of(db: Database, owner: Owner) -> int:
    """Казна владельца: сейф семьи или казна службы."""
    if owner.kind == CLAN:
        clan = await db.clan_by_id(owner.clan_id)
        return clan.vault if clan else 0
    if owner.kind == SERVICE:
        return await db.service_vault(owner.service)
    return 0


# ---------- гарнизон ----------


async def release(db: Database, player: Player, code: str,
                  now: int | None = None) -> str:
    """Снять с точки влияние семьи. Город делает это без боя.

    Драться на пустом доме не с кем: семья, не поставившая человека,
    держит его одной вывеской. Полицейский снимает её одним нажатием —
    и дом снова под городской охраной, как был до первого захвата.
    """
    moment = now_ts() if now is None else now
    spot = get_spot(code)
    if spot is None:
        raise TurfError("Такой точки в городе нет.")
    mine = owner_of(player)
    if mine.kind != SERVICE:
        raise TurfError("Освобождать дома — работа города.")
    if player.where(moment) != code:
        raise TurfError(f"До {spot.genitive} надо дойти: это делают на месте.")
    rows = await survey(db, moment)
    here = next(one for one in rows if one.spot.code == code)
    if action_for(here.owner, mine, here.guarded) != FREE:
        raise TurfError(
            f"У {spot.genitive} есть кому стоять: без боя дом не отдадут."
            if here.owner.kind == CLAN
            else f"{spot.title} и так не за семьёй."
        )
    from bot.bonus_service import accrue, note_spot

    # Часы семьи дописываем, пока строка точки ещё на месте: с её
    # удалением считать будет уже нечего
    await accrue(db, moment)
    await db.free_turf(code)
    await note_spot(db, mine, moment)
    logger.info("Боец %s освободил точку %s", player.user_id, code)
    return f"{spot.title} освобождён: дом снова под городской охраной."


async def garrison(
    db: Database, owner: Owner, levels: list[int], now: int | None = None,
    code: str = "",
) -> list[Fighter]:
    """Кто защищает точку: выставленные нанятые и городская охрана.

    Живые бойцы владельца тут больше не стоят, и это не упрощение, а
    смена правила. Раньше точку держала сама семья — и держала даром,
    во сне, всем составом. Теперь охрану покупают: наёмник стоит денег,
    гибнет насовсем и после каждого проигранного боя ставится заново.

    **Охрана города стоит там, где не хозяйничает семья.** Ничья точка
    и точка под городом — обе за тремя охранниками четвёртого уровня.
    Семья их вырезает и ставит своих.

    **За семью город не дерётся.** Точка, взятая семьёй и брошенная без
    наёмника, стоит пустая: входи и бери. Это та же мысль, что и с
    доходом — точка принадлежит тому, кто её держит, а не тому, кто
    однажды на ней подрался. Раньше сюда подставлялась городская
    охрана, и выходило, что город стережёт дом мафии от другой мафии.
    """
    from bot.hire_service import guards, posted

    out: list[Fighter] = []
    if code:
        out.extend(await posted(db, code))
    if owner.kind != CLAN:
        out.extend(guards())
    # Номера раздаём сквозные и отрицательные: по ним защитников не
    # спутать с бойцами отряда, а судья ищет бойца именно по номеру —
    # два одинаковых в одном гарнизоне обрушили бы ему поиск
    for number, one in enumerate(out):
        one.user_id = -(number + 1)
    return out


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
        from bot.bonus_service import accrue, note_spot

        await accrue(db, moment)
        await db.take_turf(code, mine.kind, mine.key, moment)
        await note_spot(db, mine, moment)
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
    "owner_badge",
    "owner_title",
    "release",
    "survey",
    "vault_of",
]
