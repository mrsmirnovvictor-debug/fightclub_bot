"""Действия с отрядом: нанять, вырастить, уволить, забрать из больницы.

Правила — в `bot.game.squad`, имена и лица — в `bot.content.recruits`,
хранение — в таблицах `squad`, `recruit_offers` и `recruit_rolls`.

**Боец — это запись в `players` с отрицательным номером.** Поэтому ему
даром достаётся всё, что город уже умеет про бойца: рюкзак, надетые
вещи, износ, здоровье, травмы и сам бой. Своя таблица бойца означала бы
второй инвентарь и второй бой — и вечное «а в отряде это иначе».

**Характеристики не хранятся, а считаются.** В базе лежат класс,
звёздность и уровень; по ним `npc_stats` в любой момент выдаёт те же
четыре числа. Поэтому их пересчитывают после каждого повышения, а не
правят по месту: правка по месту однажды разошлась бы с формулой, и
понять, какое из двух значений верное, было бы нельзя.

**Витрина живёт неделей, а не показом.** В ключе стоит начало недели —
тот же понедельник девяти утра, по которому в городе считают жалованье.
Повторный заход в ту же неделю достаёт тех же троих, и никакой
перезаход их не меняет: для этого есть платный пересмотр.
"""

from __future__ import annotations

import logging
import random

from bot.content.recruits import make_name, pick_face
from bot.database import Database
from bot.game.classes import FIGHTER_CLASSES, get_class
from bot.game.health import now_ts
from bot.game.squad import (
    SHOWCASE_SIZE,
    SQUAD_LIMIT,
    can_hold,
    hire_price,
    npc_stats,
    reroll_price,
    rerolls_left,
    roll_stars,
)
from bot.game.turf import MAX_POSTED, PER_PERSON
from bot.game.work import week_of
from bot.models import Player

logger = logging.getLogger(__name__)


class SquadError(Exception):
    """Отказ по отряду, который показывают игроку как есть."""


def _blank(name: str, class_code: str, level: int, stars: int) -> Player:
    """Новый боец: класс, уровень и характеристики по ним.

    Свободных очков у него нет и не будет: раздавать их некому, а
    оставить их значило бы показать в карточке кнопку, которой не на
    что нажать.
    """
    stats = npc_stats(get_class(class_code), stars, level)
    return Player(
        user_id=0,  # настоящий номер даст база
        nickname=name,
        class_code=class_code,
        level=level,
        strength=stats.strength,
        agility=stats.agility,
        intuition=stats.intuition,
        endurance=stats.endurance,
        free_points=0,
    )


def restat(npc: Player, stars: int) -> Player:
    """Пересчитать характеристики бойца по классу, звёздам и уровню."""
    stats = npc_stats(get_class(npc.class_code), stars, npc.level)
    npc.strength = stats.strength
    npc.agility = stats.agility
    npc.intuition = stats.intuition
    npc.endurance = stats.endurance
    npc.free_points = 0
    return npc


async def fighters(db: Database, owner_id: int) -> list[tuple[dict, Player]]:
    """Отряд игрока: строка отряда и сама карточка бойца.

    Строка без карточки — это сломанная база, а не пустое место:
    пропускаем её молча, но пишем в журнал, иначе вкладка упадёт у
    человека, который ни в чём не виноват.
    """
    out: list[tuple[dict, Player]] = []
    for row in await db.squad_of(owner_id):
        npc = await db.get_player(row["npc_id"])
        if npc is None:
            logger.warning("В отряде %s нет карточки бойца %s", owner_id, row["npc_id"])
            continue
        out.append((row, npc))
    return out


async def _taken_names(db: Database, owner_id: int, week: int) -> tuple[set, set]:
    """Чьи имена и лица уже заняты у этого игрока: отряд и витрина."""
    names: set[str] = set()
    faces: set[int] = set()
    for row, npc in await fighters(db, owner_id):
        names.add(npc.nickname)
        faces.add(row["face"])
    for one in await db.recruit_offers(owner_id, week):
        names.add(one["name"])
        faces.add(one["face"])
    return names, faces


async def _roll_showcase(
    db: Database, owner_id: int, week: int, rng: random.Random | None = None
) -> list[dict]:
    """Бросить трёх кандидатов и выложить их на неделю.

    Имена и лица не повторяются ни между кандидатами, ни с теми, кто
    уже в отряде: три «Марко Отмычки» на одном экране читались бы как
    ошибка игры, а не как совпадение.
    """
    dice = rng or random
    names, faces = await _taken_names(db, owner_id, week)
    rows: list[dict] = []
    for _ in range(SHOWCASE_SIZE):
        name = make_name(names, dice)
        face = pick_face(faces, dice)
        names.add(name)
        faces.add(face)
        rows.append(
            {
                "class_code": dice.choice(list(FIGHTER_CLASSES)),
                "stars": roll_stars(dice),
                "face": face,
                "name": name,
            }
        )
    await db.put_recruit_offers(owner_id, week, rows)
    return await db.recruit_offers(owner_id, week)


async def showcase(
    db: Database,
    player: Player,
    now: int | None = None,
    rng: random.Random | None = None,
) -> dict:
    """Витрина найма: кандидаты недели, цены и остаток пересмотров.

    Витрина бросается при первом заходе на неделе, а дальше только
    читается. Поэтому «посмотреть» ничего не стоит и ничего не меняет:
    платят здесь за второй взгляд, а не за первый.
    """
    moment = now_ts() if now is None else now
    week = week_of(moment)
    rows = await db.recruit_offers(player.user_id, week)
    if not rows:
        rows = await _roll_showcase(db, player.user_id, week, rng)
    done = await db.recruit_rolls(player.user_id, week)
    held = len(await db.squad_of(player.user_id))
    return {
        "week": week,
        "rolls_done": done,
        "rolls_left": rerolls_left(done),
        "roll_price": reroll_price(done),
        "squad": held,
        "limit": SQUAD_LIMIT,
        "cards": [
            dict(one, price=hire_price(one["stars"]), slot=one["slot"])
            for one in rows
        ],
    }


async def reroll(
    db: Database,
    player: Player,
    now: int | None = None,
    rng: random.Random | None = None,
) -> dict:
    """Посмотреть других кандидатов за деньги.

    Цена растёт с каждым пересмотром на неделе и к восьмому доходит до
    цены лучшего бойца. Девятого пересмотра нет вовсе: витрина должна
    оставаться ограничением и для того, у кого много денег.
    """
    moment = now_ts() if now is None else now
    week = week_of(moment)
    done = await db.recruit_rolls(player.user_id, week)
    price = reroll_price(done)
    if not price:
        raise SquadError("На этой неделе пересмотры кончились. Заходи в понедельник.")
    if player.credits < price:
        raise SquadError(f"Пересмотр стоит {price} 💰, а у тебя меньше.")

    # Сперва деньги, потом бросок: иначе упавший бросок оставил бы
    # игрока и без витрины, и без кредитов
    player.credits -= price
    await db.save_player(player)
    await db.bump_recruit_rolls(player.user_id, week)
    await _roll_showcase(db, player.user_id, week, rng)
    logger.info("Боец %s пересмотрел витрину найма за %s", player.user_id, price)
    return await showcase(db, player, moment, rng)


async def hire(
    db: Database, player: Player, slot: int, now: int | None = None
) -> Player:
    """Взять кандидата в отряд.

    Кандидат гасится одним оператором вместе с проверкой: два нажатия
    подряд не должны дать двух бойцов по одной цене. Деньги снимаются
    после гашения — вернуть кредиты проще, чем развидеть бойца.
    """
    moment = now_ts() if now is None else now
    week = week_of(moment)
    rows = await db.recruit_offers(player.user_id, week)
    card = next((one for one in rows if one["slot"] == slot), None)
    if card is None:
        raise SquadError("Этого кандидата на витрине нет.")
    if card["taken"]:
        raise SquadError("Этого бойца уже наняли.")
    held = await db.squad_of(player.user_id)
    if not can_hold(len(held)):
        raise SquadError(f"В отряде уже {SQUAD_LIMIT} бойцов. Сперва освободи место.")
    price = hire_price(card["stars"])
    if player.credits < price:
        raise SquadError(f"Этот боец стоит {price} 💰, а у тебя меньше.")
    if not await db.take_recruit_offer(player.user_id, week, slot):
        raise SquadError("Этого бойца уже наняли.")

    npc = _blank(card["name"], card["class_code"], 1, card["stars"])
    npc.user_id = await db.next_npc_id()
    await db.save_player(npc)
    await db.add_squad(
        npc.user_id, player.user_id, card["stars"], card["face"], moment
    )
    player.credits -= price
    await db.save_player(player)
    logger.info(
        "Боец %s нанял %s (%s⭐ %s) за %s",
        player.user_id,
        npc.user_id,
        card["stars"],
        card["class_code"],
        price,
    )
    return npc


async def dismiss(db: Database, player: Player, npc_id: int) -> Player:
    """Уволить бойца: вещи хозяину, боец из города.

    Возвращается всё, что на нём и при нём: за экипировку платил
    хозяин, и терять её вместе с бойцом он не должен. Компенсации за
    самого бойца нет — это увольнение, а не продажа.
    """
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != player.user_id:
        raise SquadError("Это не твой боец.")
    npc = await db.get_player(npc_id)
    if npc is None:
        await db.drop_squad(npc_id)
        raise SquadError("Этого бойца уже нет.")

    for item in await db.list_gear(npc_id):
        await db.hand_over_gear(item.id, npc_id, player.user_id)
    await db.drop_transfer(npc_id)
    await db.drop_transfer_bids(npc_id)
    await db.drop_squad(npc_id)
    await db.delete_player(npc_id)
    logger.info("Боец %s уволил %s", player.user_id, npc_id)
    return npc


async def to_hospital(db: Database, npc_id: int, now: int | None = None) -> None:
    """Уложить бойца: его вынесли с точки.

    Точка при этом слетает сама: лежачий её не держит, и оставить его
    в гарнизоне значило бы драться бойцом, которого там нет.

    Ноль часа здесь не бывает: нулём в строке записана выписка, и
    принять его за время значило бы выписать вместо того, чтобы
    уложить. Поэтому пустой час читается как «сейчас».
    """
    moment = now or now_ts()
    await db.set_squad_hospital(npc_id, moment)
    logger.info("Боец %s попал в больницу", npc_id)


async def discharge(db: Database, player: Player, npc_id: int) -> Player:
    """Забрать бойца из больницы.

    Бесплатно и сразу: платой за проигранную точку был сам проигрыш —
    и то время, пока она стояла без охраны. Здоровье при выписке
    доливается до полного: держать в отряде битого бойца, которого
    нельзя ни лечить склянкой, ни отправить в зал, незачем.
    """
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != player.user_id:
        raise SquadError("Это не твой боец.")
    if not row["hospital_at"]:
        raise SquadError("Этот боец и так на ногах.")
    npc = await db.get_player(npc_id)
    if npc is None:
        raise SquadError("Этого бойца уже нет.")
    npc.set_hp(npc.max_hp)
    await db.save_player(npc)
    await db.set_squad_hospital(npc_id, 0)
    logger.info("Боец %s забрал %s из больницы", player.user_id, npc_id)
    return npc


async def post(
    db: Database, player: Player, npc_id: int, spot: str, now: int | None = None
) -> dict:
    """Поставить своего бойца на эту точку.

    От одного человека на точке стоит один: десять мест — это десять
    разных игроков семьи, а не один богач со всем отрядом. Правило
    осталось от наёмников, и менять его вместе с ними было незачем —
    оно про семью, а не про то, кого именно ставят.

    Лежачего не ставят, и стоящего на другой точке — тоже: боец занят
    одним делом. Снять его оттуда можно, и тогда та точка останется
    без него — это выбор хозяина, а не наша забота.
    """
    from bot.game.turf import GUARD, action_for, get_spot
    from bot.turf_service import owner_of, survey

    moment = now_ts() if now is None else now
    place = get_spot(spot)
    if place is None:
        raise SquadError("Такой точки в городе нет.")
    if player.where(moment) != spot:
        raise SquadError("Охрану ставят на месте, а не из кармана.")
    mine = owner_of(player)
    if mine.nobody:
        raise SquadError("Охрану ставят за семью или за город.")
    rows = await survey(db, moment)
    here = next(one for one in rows if one.spot.code == spot)
    if action_for(here.owner, mine) != GUARD:
        raise SquadError("Тут сперва дерутся: на чужой точке охрану не ставят.")

    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != player.user_id:
        raise SquadError("Это не твой боец.")
    if row["hospital_at"]:
        raise SquadError("Этот боец в больнице. Сперва забери его.")
    if row["spot"] == spot:
        raise SquadError("Он и так здесь стоит.")
    standing = await db.squad_at(spot)
    if len([one for one in standing if one["owner_id"] == player.user_id]) >= PER_PERSON:
        raise SquadError("От тебя здесь уже стоит человек. Своего второго нельзя.")
    if len(standing) >= MAX_POSTED:
        raise SquadError(f"На точке уже {MAX_POSTED} — больше не ставят.")

    await db.set_squad_spot(npc_id, spot)
    # Ничью точку сторона берёт охраной, а не боем: драться там не с
    # кем. Запись о владельце появляется в тот же миг, иначе точка
    # стояла бы с охраной и без хозяина
    if here.owner.nobody:
        await db.take_turf(spot, mine.kind, mine.key, moment)
    logger.info("Боец %s поставил %s на точку %s", player.user_id, npc_id, spot)
    return {"total": len(standing) + 1, "max": MAX_POSTED, "at": moment}


async def recall(db: Database, player: Player, npc_id: int) -> None:
    """Снять своего бойца с точки: она останется без него."""
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != player.user_id:
        raise SquadError("Это не твой боец.")
    if not row["spot"]:
        raise SquadError("Он нигде не стоит.")
    await db.set_squad_spot(npc_id, "")
    logger.info("Боец %s снял %s с точки", player.user_id, npc_id)


async def posted(
    db: Database, spot: str, start: int = -100, side=None
) -> list:
    """Бойцы отряда, выставленные на эту точку, — как бойцы боя.

    Гарнизон теперь настоящий: у каждого свои характеристики, своё
    оружие и своя броня, за которые платил хозяин. Прежние наёмники
    были ступенью с готовыми числами — их нельзя было ни одеть, ни
    вырастить, и точку они держали одинаково у новичка и у ветерана.

    Номера в бою раздаются отрицательные и сквозные: судья ищет бойца
    по номеру, и два одинаковых в одном гарнизоне обрушили бы поиск.
    Поэтому собственный номер бойца (тоже отрицательный) здесь не
    годится — он может совпасть с номером городской охраны.
    """
    from bot.game.combat import Fighter
    from bot.game.art import recruit_face

    from bot.turf_service import owner_of

    out = []
    for number, row in enumerate(await db.squad_at(spot)):
        # Лежачий точку не держит: его сняли с неё, когда вынесли
        if row["hospital_at"]:
            continue
        npc = await db.get_player(row["npc_id"])
        if npc is None:  # pragma: no cover - сломанная база
            continue
        # Боец воюет за того, за кого воюет его хозяин, и ни за кого
        # больше. Хозяин ушёл из семьи — его человек с точки этой семьи
        # не дерётся, даже если остался на ней записан. Проверяем здесь,
        # а не на выходе из клана: выходов много — ушёл сам, выгнали,
        # уволился со службы, — и каждый пришлось бы помнить
        if side is not None:
            keeper = await db.get_player(int(row["owner_id"]))
            if keeper is None or owner_of(keeper) != side:
                continue
        fighter = Fighter.from_player(npc)
        fighter.user_id = start - number
        fighter.picture = recruit_face(row["face"])
        out.append(fighter)
    return out


async def fallen_on_spot(
    db: Database, spot: str, start: int, wall: dict, now: int | None = None
) -> int:
    """Уложить в больницу тех, кого вынесли в этой драке.

    Номера в стене боя сквозные, а в базе боец свой: находим его по
    тому же смещению, по которому стену и собирали. Городская охрана
    в базе не лежит — её укладывать нечем, и она просто кончается.
    """
    moment = now_ts() if now is None else now
    rows = [one for one in await db.squad_at(spot) if not one["hospital_at"]]
    hurt = 0
    for key, one in wall.items():
        number = start - key
        if one.alive or not 0 <= number < len(rows):
            continue
        await to_hospital(db, int(rows[number]["npc_id"]), moment)
        hurt += 1
    return hurt


async def all_to_hospital(
    db: Database, spot: str, now: int | None = None
) -> int:
    """Снять с точки весь гарнизон: её взяли.

    Бойцы не гибнут — их увозят. Это и есть цена проигранной точки:
    отряд цел, но до больницы за ним надо дойти, а точка уже чужая.
    """
    moment = now_ts() if now is None else now
    hurt = 0
    for row in await db.squad_at(spot):
        if row["hospital_at"]:
            continue
        await to_hospital(db, int(row["npc_id"]), moment)
        hurt += 1
    return hurt


async def pay_the_guard(
    db: Database, spot: str, now: int | None = None
) -> list[tuple[Player, int]]:
    """Начислить опыт тем, кто отстоял точку.

    Платят за отбитый штурм, а не за стояние: доход точки — это плата
    хозяину, а бойцу платят боем. Ставка — своего уровня, та же, по
    которой считают бой игрока: иначе отряд рос бы по отдельной
    лестнице, и объяснить разницу было бы нечем.

    Лежачим не платят: их вынесли, и бой они не отстояли. Выше хозяина
    боец всё равно не поднимется, а лишний опыт у него полежит.
    """
    from bot.game.economy import base_exp

    moment = now_ts() if now is None else now
    paid: list[tuple[Player, int]] = []
    for row in await db.squad_at(spot):
        if row["hospital_at"]:
            continue
        npc = await db.get_player(int(row["npc_id"]))
        owner = await db.get_player(int(row["owner_id"]))
        if npc is None or owner is None:  # pragma: no cover - сломанная база
            continue
        got = base_exp(npc.level)
        await add_exp(db, npc, int(row["stars"]), owner, got)
        paid.append((npc, got))
    if paid:
        logger.info(
            "За оборону точки %s заплачено бойцам: %s", spot, len(paid)
        )
    return paid


async def take_along(db: Database, owner: Player, npc_id: int) -> Player:
    """Взять бойца с собой в бой — в клуб и в рейд.

    Берут одного: прежний выбранный снимается сам. Занятого не берут —
    он держит точку или лежит в больнице, и спор двух режимов за одного
    бойца решается здесь, до боя, а не в середине волны.
    """
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != owner.user_id:
        raise SquadError("Это не твой боец.")
    if row["hospital_at"]:
        raise SquadError("Этот боец в больнице. Сперва забери его.")
    if row["spot"]:
        raise SquadError("Он держит точку. Там он и нужен.")
    npc = await db.get_player(npc_id)
    if npc is None:  # pragma: no cover - сломанная база
        raise SquadError("Этого бойца уже нет.")
    await db.set_squad_party(npc_id, True)
    logger.info("Боец %s берёт с собой %s", owner.user_id, npc_id)
    return npc


async def leave_behind(db: Database, owner: Player, npc_id: int) -> Player:
    """Оставить бойца дома: в бой хозяин идёт один."""
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != owner.user_id:
        raise SquadError("Это не твой боец.")
    npc = await db.get_player(npc_id)
    if npc is None:  # pragma: no cover - сломанная база
        raise SquadError("Этого бойца уже нет.")
    await db.set_squad_party(npc_id, False)
    return npc


async def companion(
    db: Database, owner_id: int, now: int | None = None
) -> tuple[dict, Player] | None:
    """Кто идёт в бой за этим игроком. Пусто — он идёт один.

    Битого не берём: в бой он вышел бы с остатком здоровья и лёг бы в
    первом размене, а лечить его хозяин может только до боя. Так же
    решает и живой игрок — `can_fight` у них общий.
    """
    moment = now_ts() if now is None else now
    row = await db.squad_party(owner_id)
    if row is None:
        return None
    npc = await db.get_player(int(row["npc_id"]))
    if npc is None:  # pragma: no cover - сломанная база
        return None
    if not npc.can_fight(moment):
        return None
    return row, npc


async def add_exp(
    db: Database, npc: Player, stars: int, owner: Player, amount: int
) -> int:
    """Начислить бойцу опыт и поднять уровень, пока можно.

    Выше хозяина боец не растёт, но и опыт сверх потолка не теряет: он
    лежит в карточке и превращается в уровни, как только хозяин сам
    поднимется. Иначе игроку пришлось бы выбирать между «качать себя»
    и «качать отряд», а отряд — это его же сила.

    Возвращаем, сколько уровней взял боец: по этому числу говорят в
    ветке, и по нему же видно, что пересчёт характеристик не напрасен.
    """
    if amount <= 0:
        return 0
    npc.total_exp += amount
    npc.exp += amount
    levels = 0
    # Потолок хозяина держит и сам цикл: без него боец на предельном
    # уровне города крутился бы здесь вечно — там `exp_needed` уже
    # ничего не просит. Второй засов стоит рядом нарочно, чтобы правка
    # первого не подвесила начисление опыта всему городу
    while (
        npc.level < owner.level
        and not npc.at_max_level
        and npc.exp >= npc.exp_needed
    ):
        npc.exp -= npc.exp_needed
        npc.level += 1
        levels += 1
    if levels:
        restat(npc, stars)
    await db.save_player(npc)
    return levels


__all__ = [
    "SquadError",
    "add_exp",
    "companion",
    "leave_behind",
    "take_along",
    "all_to_hospital",
    "fallen_on_spot",
    "pay_the_guard",
    "post",
    "posted",
    "recall",
    "discharge",
    "dismiss",
    "fighters",
    "hire",
    "reroll",
    "restat",
    "showcase",
    "to_hospital",
]
