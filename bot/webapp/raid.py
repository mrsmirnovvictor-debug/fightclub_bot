"""Рейд глазами мини-аппа: то же состояние, что в ветке, только данными.

Правил здесь нет: волны считает `RaidService`, а тут перевод живого рейда
в json, который умеет нарисовать страница. Экран опрашивает ручку раз в
пару секунд, поэтому ответ всегда полный — по нему видно, что рисовать:
сбор отряда, идущую волну или итог.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from bot.game.clock import club_date
from bot.game.classes import ALL_ZONES, BLOCK_WIDTH
from bot.game.combat import (
    Fighter,
    total_accuracy,
    total_anticrit,
    total_block_hold,
    total_counter,
    total_crit,
    total_dodge,
)
from bot.game.equipment import LEFT_SLOTS, RIGHT_SLOTS
from bot.game.health import now_ts
from bot.game.raid import (
    RAID_SOON,
    LEVELS_ABOVE,
    Boss,
    CELLAR_RAID,
    RAID_KINDS,
    RaidKind,
    boss_fighter,
    kind_of_boss,
)
from bot.game.potions import get_potion
from bot.game.scout import Move, habits_of_temper, moves_of, trend
from bot.models import Player
from bot.webapp.fight import abilities_payload
from bot.raid_service import RaidLobby, RaidService, RaidSession
from bot.webapp.card import slot_payload
from bot.webapp.fight import ATTACK_BUTTONS, BLOCK_BUTTONS, block_buttons, hands_payload


def boss_scout(
    session: RaidSession, viewer_id: int, pro: bool
) -> dict[str, Any] | None:
    """Что аналитик говорит подписчику про босса. None — молчит.

    У живого соперника привычки считают по его прошлым боям. У босса
    считать нечего — он не игрок, боёв за ним не записано, — зато у него
    есть характер, заданный весами, и аналитик читает ровно те числа, по
    которым босс кидает кости. Ни в одну сторону разойтись они не могут:
    веса лежат в одном месте, у самого босса.

    Про прошлый ход берётся то же, что в дуэли: законченные размены и
    ничего сверх. Что босс нажал прямо сейчас, сюда не попадает.

    Разбор до первого размена идёт через `trend` с пустым ходом, а не
    через `opening`: у босса нет ритуала на первый удар, и говорить «в
    первом ходу он блокирует так-то» значило бы обещать особенность,
    которой нет.

    Стойку босс меняет каждую волну, поэтому и разбор каждую волну свой.
    В этом всё умение: заучить его нельзя, за ним можно только следить.
    """
    fighter = session.fighters.get(viewer_id)
    if not pro or fighter is None or session.finished:
        return None
    # Читаем того, с кем этот боец стоит, а не «босса рейда»: в банде у
    # каждого свои повадки, и совет про чужого противника — это не
    # аналитика, а враньё
    number = session.aim.get(viewer_id)
    enemy = session.enemies.get(number) if number is not None else None
    if enemy is None:
        return None
    template = session.template_of(number)
    temper = session.temper_of(number)
    habits = habits_of_temper(
        temper.swings,
        # Сколько зон он закрывает разом, решает его снаряжение: со щитом
        # блок шире, и доли «закрыта ли эта зона» считаются от него
        temper.covers(enemy.block_width),
    )
    moves = moves_of(session.rounds, number)
    # Ширина блока — того, кто читает: со щитом он держит три зоны, и
    # советовать ему пару значило бы советовать меньше, чем он нажмёт
    advice = trend(habits, moves[-1] if moves else Move(number=0), fighter.block_width)
    return replace(
        advice,
        title=(
            f"Волна {session.wave}: стойка {enemy.name}. {template.manner}"
        ).strip(),
    ).as_dict()


def foe_payload(
    session: RaidSession, number: int, viewer_id: int = 0
) -> dict[str, Any]:
    """Один противник на табло: кто он, сколько в нём осталось и с кем стоит."""
    enemy = session.enemies[number]
    template = session.template_of(number)
    against = next(
        (
            user_id
            for user_id, mate in session.aim.items()
            if mate == number and session.fighters[user_id].alive
        ),
        None,
    )
    return {
        "number": number,
        "code": template.code,
        "title": enemy.name,
        "emoji": template.emoji,
        "image": template.image,
        "fclass": enemy.fclass.title,
        "fclass_emoji": enemy.fclass.emoji,
        "level": enemy.level,
        "hp": enemy.hp,
        "max_hp": enemy.max_hp,
        "percent": round(enemy.hp_percent * 100),
        "alive": enemy.alive,
        "weapon": enemy.weapon,
        # С кем он стоит прямо сейчас: по этой подписи боец находит
        # своего на табло из пяти
        "against": against,
        # Твой ли это противник: на табло из пяти своего надо найти глазом
        "yours": against is not None and against == viewer_id,
    }


def boss_payload(session: RaidSession, viewer_id: int = 0) -> dict[str, Any]:
    """Главный противник для шапки: в казино босс, в банде — её лидер."""
    return foe_payload(session, session.order[0], viewer_id)


def member_payload(session: RaidSession, user_id: int, viewer_id: int) -> dict[str, Any]:
    fighter = session.fighters[user_id]
    return {
        "user_id": user_id,
        "name": fighter.name,
        "level": fighter.level,
        "emoji": fighter.fclass.emoji,
        "hp": fighter.hp,
        "max_hp": fighter.max_hp,
        "percent": round(fighter.hp_percent * 100),
        "damage_dealt": fighter.damage_dealt,
        "alive": fighter.alive,
        # Отработал в этой волне: ждать его больше не надо
        "acted": user_id in session.acted,
        "you": user_id == viewer_id,
    }


def boss_card(
    enemy: Fighter, boss: Boss, live: bool, kind: RaidKind = CELLAR_RAID
) -> dict[str, Any]:
    """Всё про противника, что показывает кнопка «i».

    `live` — это настоящий противник идущего рейда. Иначе прикидка: каким
    он выйдет к бойцу, который смотрит, если тот соберёт отряд сейчас.
    """
    derived, equipment = enemy.derived, enemy.equipment
    return {
        "code": boss.code,
        "title": enemy.name,
        # Как называется сам рейд: заголовок раздела берётся отсюда, а не
        # склеивается на странице — склонять имена там нечем
        "raid_name": kind.title,
        # Кукла босса собирается тем же кодом, что и карточка бойца: те же
        # слоты, те же подложки под пустыми, тот же аватар в середине
        "name": enemy.name,
        "avatar": {"url": boss.image, "emoji": boss.emoji},
        "slots": {
            "left": [
                slot_payload(equipment, slot, enemy.fclass) for slot in LEFT_SLOTS
            ],
            "right": [
                slot_payload(equipment, slot, enemy.fclass) for slot in RIGHT_SLOTS
            ],
        },
        "emoji": boss.emoji,
        "image": boss.image,
        "tagline": boss.tagline,
        "live": live,
        # Один он против отряда или в банде: страница этим и объясняет,
        # откуда в рейде сложность
        "alone": kind.one_on_one,
        "levels_above": LEVELS_ABOVE,
        "level": enemy.level,
        "fclass": enemy.fclass.title,
        "fclass_emoji": enemy.fclass.emoji,
        "max_hp": enemy.max_hp,
        "weapon": equipment.weapon_title or enemy.weapon,
        "weapon_icon": equipment.weapon_icon,
        "damage": [derived.damage_min, derived.damage_max],
        "stats": {
            "strength": enemy.stats.strength,
            "agility": enemy.stats.agility,
            "intuition": enemy.stats.intuition,
            "endurance": enemy.stats.endurance,
        },
        # Проценты считаем той же арифметикой, что и ринг: в карточке босса
        # стоит ровно то, с чем он выйдет драться
        "combat": {
            "crit_chance": round(total_crit(derived.crit_chance, equipment.crit) * 100),
            "crit_power": derived.crit_power,
            "anticrit": round(
                total_anticrit(derived.anticrit, equipment.anticrit) * 100
            ),
            "dodge_chance": round(
                total_dodge(derived.dodge_chance, equipment.dodge) * 100
            ),
            "accuracy": round(total_accuracy(derived.accuracy, equipment.accuracy) * 100),
            "counter_chance": round(
                total_counter(derived.counter_chance, equipment.counter) * 100
            ),
            "resist": round(derived.resist * 100),
            "penetration": round(derived.penetration * 100),
            "block_hold": round(total_block_hold(derived.block_hold) * 100),
        },
        "armor": [
            {
                "zone": zone.value,
                "title": zone.title.capitalize(),
                "emoji": zone.emoji,
                "min": low,
                "max": high,
            }
            for zone, (low, high) in (
                (zone, equipment.armor_range(zone)) for zone in ALL_ZONES
            )
        ],
        "kit": [
            {"slot": slot.value, "title": owned.title, "emoji": owned.emoji}
            for slot, owned in equipment.items.items()
        ],
    }


def lobby_payload(
    lobby: RaidLobby, viewer_id: int, timeout: int = 0
) -> dict[str, Any]:
    return {
        "id": lobby.id,
        "size": lobby.size,
        "total": lobby.total,
        "mine": lobby.opener_id == viewer_id,
        "joined": viewer_id in lobby.members,
        "in_app": lobby.chat_id is None,
        # Сколько ещё ждут отставших. Страница тикает сама, а сервер
        # поправляет её на каждом опросе — часы у всех одни.
        "seconds_left": lobby.seconds_left(timeout),
        "timeout": timeout,
        # Отряд уже боеспособен: созвавший может не ждать отсчёта
        "can_start": lobby.can_start,
        "min_party": lobby.kind.min_party,
        "raid": kind_row(lobby.kind),
        "boss": {
            "code": lobby.boss.code,
            "title": lobby.kind.title,
            "emoji": lobby.kind.emoji,
            "image": lobby.boss.image,
            "tagline": lobby.kind.tagline,
        },
        "members": [
            {"user_id": user_id, "name": name, "level": lobby.levels.get(user_id, 1)}
            for user_id, name in lobby.members.items()
        ],
    }


def raid_payload(
    session: RaidSession, viewer_id: int, pro: bool = False
) -> dict[str, Any]:
    """Панель рейда: босс, отряд, что уже нажато и чем всё кончилось."""
    mine = session.choices.get(viewer_id)
    fighter = session.fighters.get(viewer_id)
    return {
        # Набор кнопок у каждого свой: второе оружие даёт второй столбец
        # ударов, щит — блок в три зоны
        "hands": hands_payload(fighter),
        "blocks": block_buttons(fighter.block_width if fighter else BLOCK_WIDTH),
        "id": session.id,
        "wave": session.wave,
        "in_app": session.chat_id is None,
        "resting": session.resting,
        "finished": session.finished,
        "summary": session.summary,
        "raid": kind_row(session.kind),
        "boss": boss_payload(session, viewer_id),
        # Вся банда по порядку: по ней и видно, сколько ещё стоит на
        # ногах. В казино в этом списке один человек — сам босс
        "gang": [
            foe_payload(session, number, viewer_id) for number in session.order
        ],
        # Против кого стоит смотрящий. Пусто — ни против кого: его
        # противника добили, а новый выйдет со следующей волной
        # Кого смотрящий бьёт сейчас. Пусто — некого: круг обошёл всех,
        # и все лежат. В казино тоже пусто: там босс один, и говорить
        # «бьём его» незачем — он и так на всю колонку
        "foe": (
            foe_payload(session, session.aim[viewer_id], viewer_id)
            if session.gang and viewer_id in session.aim
            else None
        ),
        "party": [
            member_payload(session, user_id, viewer_id) for user_id in session.fighters
        ],
        "yours": viewer_id in session.fighters,
        "alive": bool(fighter and fighter.alive),
        # Отработал в этой волне — кнопки прячем до следующей
        "acted": viewer_id in session.acted,
        "chosen": {
            "attacks": {
                str(hand): zone.value
                for hand, zone in (mine.attacks.items() if mine else ())
            },
            "attack": mine.attack.value if mine and mine.attack else None,
            "block": mine.block[0].value if mine and mine.block else None,
        },
        # Приёмы и шкала — только свои: чужие заготовки соперник видеть не
        # должен, иначе приём перестаёт быть неожиданностью
        "abilities": abilities_payload(fighter),
        # Аналитик — только подписчику и только про босса. Ответ у
        # каждого свой: панель мини-апп собирает под зрителя
        "scout": boss_scout(session, viewer_id, pro),
        "log": session.rounds,
    }


BLANK_PLATE = {"state": "", "text": "", "seconds_left": 0}


async def plate_payload(
    player: Player,
    service: RaidService | None,
    moment: int | None = None,
    kind: RaidKind = CELLAR_RAID,
) -> dict[str, Any]:
    """Плашка рейда на карте: скоро, идёт или пройден.

    Висит под вывеской своего дома и живёт по расписанию: за час до окна
    — отсчёт до начала, в окне — отсчёт до конца, а тому, кто своё уже
    взял, вместо часов «Рейд завершён». Вне этих часов плашки нет вовсе:
    карта не место для расписания на неделю вперёд.
    """
    moment = now_ts() if moment is None else moment
    if service is None:  # pragma: no cover - бот без рейдов не живёт
        return dict(BLANK_PLATE)

    window = service.window_now(moment, kind)
    if window is not None:
        seen = await service.db.raid_window(
            player.user_id, window.start, kind.code
        )
        if seen and seen["won"]:
            return {"state": "done", "text": "Рейд завершён", "seconds_left": 0}
        return {
            "state": "open",
            "text": "Рейд закончится через",
            "seconds_left": window.seconds_left(moment),
        }

    soon = kind.next_window(moment)
    left = soon.start - moment
    if left > RAID_SOON:
        return dict(BLANK_PLATE)
    return {
        "state": "soon",
        "text": "Рейд начнётся через",
        "seconds_left": max(left, 0),
    }


async def plates_payload(
    player: Player, service: RaidService | None, moment: int | None = None
) -> dict[str, dict[str, Any]]:
    """Плашки всех рейдов города — по дому на запись.

    Рейдов два, и расписания у них свои: одна плашка на карту означала бы,
    что отсчёт под стадионом показывает часы казино.
    """
    return {
        kind.house: await plate_payload(player, service, moment, kind)
        for kind in RAID_KINDS
    }


def kind_row(kind: RaidKind) -> dict[str, Any]:
    """Сам рейд: как называется, где идёт и чем встречает."""
    gang = kind.roster(kind.min_party)
    return {
        "code": kind.code,
        "title": kind.title,
        "emoji": kind.emoji,
        "tagline": kind.tagline,
        "house": kind.house,
        "min_party": kind.min_party,
        "max_party": kind.max_party,
        "purse": kind.purse,
        "split": kind.split,
        "alone": kind.one_on_one,
        # Сколько противников выходит на минимальный отряд и растёт ли их
        # число с отрядом: этим рейд и объясняет свою сложность
        "foes": len(gang),
        "grows": bool(kind.reserve),
        "foe_level": kind.foe_level,
        "schedule": kind.schedule_text(),
        "next_window": kind.next_window().title,
    }


async def gate_payload(
    player: Player, service: RaidService | None, kind: RaidKind = CELLAR_RAID
) -> dict[str, Any]:
    """Пускают ли бойца в этот рейд прямо сейчас и на что.

    Отсюда страница знает, что показать в окне согласия: тратить пропуск
    из рюкзака, покупать его или вовсе не звать — своё уже взято.
    """
    ticket = get_potion(kind.pass_code)
    body: dict[str, Any] = {
        "pass_code": kind.pass_code,
        "pass_title": ticket.title,
        "pass_price": ticket.price,
        "pass_emoji": ticket.emoji,
        "passes": player.potion_count(kind.pass_code),
        "schedule": kind.schedule_text(),
        "open": False,
        "window": "",
        "next_window": kind.next_window().title,
        "won": False,
        # Пропуск за это окно уже отдан: заходить можно сколько угодно
        "spent": False,
        "can_afford": player.can_afford(ticket.price),
    }
    if service is None:  # pragma: no cover - бот без рейдов не живёт
        return body

    window = service.window_now(kind=kind)
    if window is None:
        return body
    seen = await service.db.raid_window(player.user_id, window.start, kind.code)
    body.update(
        open=True,
        window=window.title,
        won=bool(seen and seen["won"]),
        spent=bool(seen),
    )
    return body


def preview_cards(player: Player, kind: RaidKind) -> list[dict[str, Any]]:
    """Кого боец встретит, если соберёт отряд прямо сейчас.

    Прикидка, а не настоящая банда: состав берётся на минимальный отряд.
    Здоровье босса казино тут за одного — с каждым лишним бойцом он
    крепче; у банды наоборот, лишний боец приводит лишнего гопника.
    """
    levels = [player.level] * max(1, kind.min_party)
    return [
        boss_card(
            boss_fighter(
                boss,
                [player.level] if kind.one_on_one else levels,
                kind.hp_share,
                level=kind.foe_level,
            ),
            boss,
            live=False,
            kind=kind,
        )
        for boss in kind.roster(len(levels))
    ]


def build_raid(
    player: Player,
    service: RaidService | None,
    timeout: int = 0,
    kind: RaidKind = CELLAR_RAID,
) -> dict[str, Any]:
    """Всё, что нужно разделу «Рейд», одним ответом.

    Какой это рейд, решает дом, в котором боец стоит, — а если он уже
    записан, то тот рейд, в который записан. Выбирать из списка не
    приходится: на стадион и в казино ходят ногами.
    """
    body: dict[str, Any] = {
        "attacks": [dict(row) for row in ATTACK_BUTTONS],
        "blocks": [dict(row) for row in BLOCK_BUTTONS],
        "kind": kind_row(kind),
        "min_party": kind.min_party,
        "max_party": kind.max_party,
        "can_fight": player.can_fight(),
        "raid": None,
        "lobby": None,
        "lobbies": [],
        "roster": preview_cards(player, kind),
    }
    body["boss"] = body["roster"][0]
    if service is None:  # pragma: no cover - бот без рейдов не живёт
        return body

    session = service.raid_of_user(player.user_id) or service.result_of_user(
        player.user_id
    )
    if session is not None:
        body["kind"] = kind_row(session.kind)
        body["raid"] = raid_payload(session, player.user_id, player.is_pro())
        body["roster"] = [
            boss_card(
                session.enemies[number],
                session.template_of(number),
                live=True,
                kind=session.kind,
            )
            for number in session.order
        ]
        body["boss"] = body["roster"][0]
        return body

    own = service.lobby_of_user(player.user_id)
    if own is not None:
        body["lobby"] = lobby_payload(own, player.user_id, timeout)
    # Чужие сборы — только того же рейда: на стадионе незачем видеть, что
    # кто-то собирается в казино, туда отсюда всё равно не записаться
    body["lobbies"] = [
        lobby_payload(lobby, player.user_id, timeout)
        for lobby in service.open_lobbies()
        if player.user_id not in lobby.members and lobby.kind.code == kind.code
    ]
    return body


# Как исход рейда называется в списке боёв: там важно не «босс повержен», а
# что вышло у тебя — рядом с победами и поражениями в дуэлях
HISTORY_TITLES = {"win": "Победа", "draw": "Ничья", "loss": "Поражение"}
HISTORY_MARKS = {"win": "🏆", "draw": "🤝", "loss": "❌"}


def raid_row(row: dict[str, Any]) -> dict[str, Any]:
    """Строка истории рейдов: с кем ходили, чем кончилось и что унесли."""
    from bot.game.raid import RAID_END_TITLES, RaidEnd, get_boss

    from bot.game.raid import prize_of

    end = RaidEnd(row["outcome"])
    boss = get_boss(row["boss"])
    kind = kind_of_boss(row["boss"])
    # Приз бывает и вещью, и склянкой: раньше здесь спрашивали только
    # вещь, и склянка подвала в историю не попадала вовсе
    prize = prize_of(row["prize"]) if row["prize"] else None
    allies = row.get("allies") or ""
    with_whom = f" (с {allies})" if allies else ""
    return {
        "kind": "raid",
        "id": row["id"],
        "boss": boss.title,
        "boss_emoji": boss.emoji,
        "emoji": HISTORY_MARKS[end.value],
        "result": end.value,
        "result_title": HISTORY_TITLES[end.value],
        # «Поражение (с Марлой) — Ограбление Босса Казино»
        "caption": f"{HISTORY_TITLES[end.value]}{with_whom} — {kind.title}",
        "raid_title": kind.title,
        "verdict": RAID_END_TITLES[end.value],
        "allies": allies,
        "boss_level": row["boss_level"],
        "waves": row["waves"],
        "damage": row["damage"],
        "alive": bool(row["alive"]),
        "prize": prize[1] if prize else None,
        "prize_emoji": prize[0] if prize else "",
        "created_at": row["created_at"],
        # День московский: бой в час ночи — это уже новые сутки, а метка в
        # базе лежит в UTC и сама по себе указала бы на вчера
        "date": club_date(row["created_at"]),
    }


__all__ = [
    "boss_card",
    "build_raid",
    "foe_payload",
    "gate_payload",
    "kind_row",
    "plate_payload",
    "plates_payload",
    "preview_cards",
    "lobby_payload",
    "plate_payload",
    "boss_scout",
    "raid_payload",
    "raid_row",
]
