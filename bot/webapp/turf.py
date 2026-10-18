"""Территории глазами мини-аппа: карта точек и что с каждой.

Правил тут нет — их держат `bot.game.turf` и `bot.turf_service`. Здесь
перевод в json.

**Чужой владелец виден целиком.** Кто держит точку, сколько она приносит
и когда за неё дрались — это и есть предмет войны, и прятать его не от
кого: территории на то и территории, чтобы про них знали все.

**Кнопка штурма считается на сервере.** Можно ли идти, решает служба:
своя точка, отдых после боя, потолок в четыре точки, то, воюет ли этот
боец вообще, — и то, стоит ли он у самого дома. Страница только рисует
ответ.

**В особняке — доска, у дома — кнопка.** Список точек с владельцами и
доходом смотрят откуда угодно: это сводка, а не пульт. Созвать отряд
можно только там, где боец стоит ногами, и `here` в строке точки — это
ровно оно.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.classes import BLOCK_WIDTH
from bot.game.hirelings import MAX_POSTED
from bot.game.turf import (
    ABANDON_HOURS,
    ARREST,
    FREE,
    GUARD,
    MAX_GARRISON,
    MAX_SPOTS_PER_OWNER,
    SEIZE,
    SERVICE,
    SERVICE_SHARE,
    TOTAL_SPOTS,
    Owner,
    action_for,
)
from bot.turf_service import MAX_WAVES
from bot.models import Player


# Сколько разменов журнала уходит в панель. Штурм двух полных отрядов
# — это до сорока волн по пять ударов, и слать все двести в каждом
# опросе незачем: читают последние, а не первые
LOG_TAIL = 60

# Отметка владельца в списке: по ней видно, чья точка, не читая имени.
# Своя — флаг, чужая семья — шляпа, служба — щит, ничья — круг
OWN_MARK = "🚩"
CLAN_MARK = "🎩"
SERVICE_MARK = "🛡"
NOBODY_MARK = "⚪️"


# Как зовётся дело на кнопке. Слова разные нарочно: город не
# присваивает дом, он выводит с него мафию
ACTION_TITLES: dict[str, str] = {
    SEIZE: "Захват",
    ARREST: "Задержание",
    FREE: "Освобождение",
    GUARD: "Охрана",
}


def house_mark(owner: Owner) -> str:
    """Чем помечен дом на карте города: кто его держит.

    Своей точки тут нет нарочно: на карте важно не «чья она», а «её
    вообще берут» — круг у ничьей, шляпа у семьи, фуражка у службы.
    Прибыль на карту не идёт вовсе: её смотрят в особняке, а под
    вывеской она была лишней строкой на каждом доме.
    """
    if owner.nobody:
        return NOBODY_MARK
    if owner.kind == SERVICE:
        return service_mark(owner.key)
    return CLAN_MARK


def service_mark(key: str) -> str:
    """Значок службы — её собственный: у полиции фуражка."""
    from bot.game.services import BY_CODE

    service = BY_CODE.get(key)
    return service.emoji if service else SERVICE_MARK


def spot_mark(owner: Owner, ours: bool) -> str:
    """Чем помечена точка в списке."""
    if ours:
        return OWN_MARK
    if owner.nobody:
        return NOBODY_MARK
    return SERVICE_MARK if owner.kind == SERVICE else CLAN_MARK


async def spot_row(
    db, holding, mine: Owner, moment: int, here: str = "",
    posted: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Строка точки: дом, доход, владелец и можно ли идти на штурм."""
    from bot.turf_service import owner_badge, owner_title

    spot = holding.spot
    owner = holding.owner
    ours = owner.same(mine) and not mine.nobody
    standing = spot.code == here
    # Пустую семейную точку город снимает без боя: драться там не с кем
    deed = action_for(owner, mine, holding.guarded)
    return {
        "code": spot.code,
        "title": spot.title,
        "income": spot.income,
        "note": spot.note,
        "owner": {
            "kind": owner.kind,
            "key": owner.key,
            "title": await owner_title(db, owner),
            "nobody": owner.nobody,
        },
        "mark": spot_mark(owner, ours),
        # Отметка для карты города: там «чья» важнее «своя ли»
        "sign": house_mark(owner),
        # Жетон владельца: знак семьи или значок службы. Нарисованный
        # знак говорит то, чего не скажет шляпа, — какая именно семья
        "badge": await owner_badge(db, owner),
        "mine": ours,
        # Сколько нанятых на точке и сколько их туда ещё влезет
        "posted": (posted or {}).get(spot.code, 0),
        "max_posted": MAX_POSTED,
        # Платит ли точка. Брошенная не платит: это дом с вывеской
        "guarded": holding.guarded,
        # Боец стоит у этого дома. Без этого кнопки сбора не бывает:
        # точку берут ногами
        "here": standing,
        "taken_at": club_moment(holding.taken_at) if holding.taken_at else "",
        # Что эта сторона делает на этой точке: берёт боем, выводит с
        # неё мафию или ставит охрану. Решает правило, а не кнопка
        "action": deed,
        "action_title": ACTION_TITLES.get(deed, ""),
        # Отдыха у точки нет: взяли её минуту назад — можно идти и
        # брать назад. Нужно только стоять у дома и иметь там дело
        "can_storm": bool(standing and deed in (SEIZE, ARREST)),
        "can_free": bool(standing and deed == FREE),
    }


async def build_turf(
    db, player: Player, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Карта территорий целиком: все точки и что с ними у этого бойца."""
    from bot.turf_service import owner_of, owner_title, survey, vault_of

    moment = now_ts() if now is None else now
    mine = owner_of(player)
    here = player.where(moment)
    # Война — дело семей и служб. Одиночке она не говорит ничего: точку
    # ему не взять и доли не получить, — и знать про неё незачем.
    # Поэтому не «карта с серыми кнопками», а пустой экран с одной
    # строкой: это не секрет, это чужое дело
    if mine.nobody:
        return _shut_board(here, said)
    rows = await survey(db, moment)
    standing = await db.posted_counts()
    spots = [
        await spot_row(db, one, mine, moment, here, standing) for one in rows
    ]
    held = [one for one in spots if one["mine"]]
    return {
        "title": "Территории",
        "spots": spots,
        # Где боец стоит сейчас: по этому экран и решает, рисовать ли
        # кнопку сбора хоть у одной строки
        "here": here,
        "at_spot": any(one["here"] for one in spots),
        "total": TOTAL_SPOTS,
        # За кого воюет этот боец. Пусто — ни за кого: одиночкам войны
        # за территории не положено
        "side": {
            "kind": mine.kind,
            "key": mine.key,
            "title": await owner_title(db, mine) if not mine.nobody else "",
            "nobody": mine.nobody,
        },
        "held": len(held),
        "income": sum(
            int(one["income"] * (SERVICE_SHARE if mine.kind == SERVICE else 1))
            for one in held
            if one["guarded"]
        ),
        "vault": await vault_of(db, mine),
        "ceiling": MAX_SPOTS_PER_OWNER,
        "squad": MAX_GARRISON,
        "abandon_hours": ABANDON_HOURS,
        "said": said,
        # Нанятые: что на руках, что предлагают и кого можно выставить
        "hire": await _hire_rows(db, player, here, spots),
        # Точка под ногами целиком: кто её держит и кто на ней стоит
        "here_spot": await _spot_panel(
            db, next((one for one in spots if one["here"]), None)
        ),
        "note": _note(mine, len(held), any(one["here"] for one in spots)),
    }


def _shut_board(here: str, said: str) -> dict[str, Any]:
    """Карта войны для того, кто в ней не участвует: пустая."""
    return {
        "title": "Территории",
        "spots": [],
        "here": here,
        "at_spot": False,
        "total": TOTAL_SPOTS,
        "side": {"kind": "", "key": "", "title": "", "nobody": True},
        "held": 0,
        "income": 0,
        "vault": 0,
        "ceiling": MAX_SPOTS_PER_OWNER,
        "squad": MAX_GARRISON,
        "abandon_hours": ABANDON_HOURS,
        "said": said,
        "hire": {"side": "", "cap": 0, "hired": 0, "left": 0, "mine": [],
                 "grades": [], "spot": "", "spot_title": "",
                 "can_post": False, "why": ""},
        "here_spot": None,
        "note": (
            "За территории воюют семьи и службы. Вступи в клан или иди "
            "на службу — тогда и поговорим."
        ),
    }


async def _spot_panel(db, row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Точка под ногами: кто её держит и кто на ней стоит.

    Нужна затем, что «охрана: 1 из 10» в списке — это число, а не
    ответ: кого именно поставили, чей он и сколько там городской
    охраны, из него не видно. А решать, ставить ли ещё одного, надо
    именно по этому.
    """
    from bot.game.classes import FIGHTER_CLASSES
    from bot.game.hirelings import (
        GUARD_LEVEL,
        GUARD_SIZE,
        get_grade,
        guard_picture,
    )
    from bot.game.turf import CLAN

    if row is None:
        return None
    kept = []
    for one in await db.hirelings_at(row["code"]):
        grade = get_grade(str(one["grade"]))
        if grade is None:  # pragma: no cover - ступень из будущей версии
            continue
        keeper = await db.get_player(int(one["owner_id"]))
        kept.append(
            {
                "id": int(one["id"]),
                "title": grade.title,
                "level": grade.level,
                "fclass": FIGHTER_CLASSES[grade.fclass].emoji,
                "picture": grade.picture,
                "side": grade.side,
                "owner": keeper.nickname if keeper else "боец",
                "owner_id": int(one["owner_id"]),
            }
        )
    # Городская охрана стоит там, где не хозяйничает семья. Её не
    # ставят и не считают в чьи-то места — она просто есть
    guards = 0 if row["owner"]["kind"] == CLAN else GUARD_SIZE
    return {
        "code": row["code"],
        "title": row["title"],
        "income": row["income"],
        "owner": row["owner"],
        "mark": row["sign"],
        "badge": row["badge"],
        "mine": row["mine"],
        "guarded": row["guarded"],
        "posted": kept,
        "max_posted": row["max_posted"],
        "guards": guards,
        "guard_level": GUARD_LEVEL,
        "guard_picture": guard_picture() if guards else "",
        # Что тут делают: берут боем, выводят мафию или ставят охрану
        "action": row["action"],
        "action_title": row["action_title"],
        "can_storm": row["can_storm"],
        "can_free": row["can_free"],
        # За точку уже дерутся — второй отряд на неё не собирают.
        # Ставит это `build_war`: кто дерётся, знает война, а не база
        "busy": False,
    }


async def _hire_rows(
    db, player: Player, here: str, spots: list[dict[str, Any]]
) -> dict[str, Any]:
    """Наём глазами экрана: счёт, прилавок и кого ставить здесь.

    Охрану ставят там, где у стороны дело «охрана»: на своей точке — и
    город ещё на ничьей, которую он и так патрулирует. Решает служба, а
    страница рисует ответ.

    **Кнопка не прячется, а гаснет.** От одного бойца на точке стоит
    один человек: второго он поставить не может, и сказать об этом
    надо словами, а не пустым местом.
    """
    from bot.hire_service import roster

    body = await roster(db, player)
    row = next((one for one in spots if one["code"] == here), None)
    body["spot"] = here if row else ""
    body["spot_title"] = row["title"] if row else ""
    guarding = bool(row and row["action"] == GUARD)
    free = bool(guarding and row["posted"] < MAX_POSTED)
    ours = [one for one in body["mine"] if one["spot"] == here]
    body["can_post"] = bool(free and not ours)
    # Место под кнопку есть везде, где дело — охрана: даже когда
    # нажимать её нельзя
    body["guarding"] = guarding
    body["why"] = ""
    if row and not body["can_post"]:
        if not guarding:
            body["why"] = "Охрану ставят на своей точке."
        elif ours:
            body["why"] = "От тебя здесь уже стоит человек."
        else:
            body["why"] = f"На точке уже {MAX_POSTED} — больше не ставят."
    return body


def _note(mine: Owner, held: int, at_spot: bool) -> str:
    if mine.nobody:
        return (
            "За территории воюют семьи и службы. Вступи в клан или иди на "
            "службу — тогда и поговорим."
        )
    if not at_spot:
        # Доска, а не пульт: отсюда видно, кто чем владеет, но взять
        # точку можно только придя к ней
        tail = (
            f"Точек у вас {held} из {MAX_SPOTS_PER_OWNER}."
            if held
            else "Своих точек пока нет."
        )
        return f"{tail} Захват берут на месте: дойди до дома и созови отряд."
    if not held:
        return (
            "Ты на месте. Ничьи точки тоже под охраной: брать их придётся "
            "боем."
        )
    return (
        f"Точек у вас {held} из {MAX_SPOTS_PER_OWNER}. Доход капает раз в "
        "сутки и только с тех, на которых кто-то стоит."
    )


# ---------- живая осада ----------


def fighter_row(one, viewer_id: int = 0, acted: bool = False) -> dict[str, Any]:
    """Боец в панели: имя, запас, жив ли он ещё и сделал ли ход.

    `acted` — только у своих: гарнизон ведёт бросок, и «ждём его хода»
    про него было бы неправдой.
    """
    return {
        "user_id": one.user_id,
        "name": one.name,
        "hp": max(0, one.hp),
        "max_hp": one.max_hp,
        "percent": max(0, round(one.hp / one.max_hp * 100)) if one.max_hp else 0,
        "alive": one.alive,
        "level": one.level,
        "fclass": one.fclass.emoji,
        # Лицо есть у нанятых и охраны, у игрока его нет
        "picture": one.picture,
        "is_self": one.user_id == viewer_id,
        "acted": acted,
    }


def muster_payload(muster, viewer_id: int, seconds: int) -> dict[str, Any]:
    """Сбор отряда: куда идут, кто записан и сколько ждать."""
    return {
        "id": muster.id,
        "spot": muster.spot.code,
        "spot_title": muster.spot.title,
        "income": muster.spot.income,
        "leader": muster.leader,
        "mine": viewer_id in muster.members,
        "is_leader": viewer_id == muster.leader,
        "members": [
            {"user_id": one, "name": name, "is_self": one == viewer_id}
            for one, name in muster.members.items()
        ],
        "total": len(muster.members),
        "max": MAX_GARRISON,
        "seconds_left": muster.seconds_left(seconds),
    }


def siege_payload(siege, viewer_id: int) -> dict[str, Any]:
    """Панель штурма: гарнизон, отряд, что нажато и чем кончилось."""
    from bot.webapp.fight import (
        ATTACK_BUTTONS,
        abilities_payload,
        block_buttons,
        hands_payload,
    )

    mine = siege.choices.get(viewer_id)
    fighter = siege.party.get(viewer_id)
    foe = siege.foe_of(viewer_id)
    return {
        "id": siege.id,
        "spot": siege.spot.code,
        "spot_title": siege.spot.title,
        "income": siege.spot.income,
        "defender": siege.defender_title,
        "wave": siege.wave,
        "max_waves": MAX_WAVES,
        "finished": siege.finished,
        "won": siege.won,
        "summary": siege.summary,
        "hands": hands_payload(fighter),
        # Кнопки зон: сколько рук, столько столбцов удара, плюс блок
        "attacks": [dict(row) for row in ATTACK_BUTTONS],
        "blocks": block_buttons(fighter.block_width if fighter else BLOCK_WIDTH),
        # Весь гарнизон по порядку: по нему видно, сколько ещё стоит
        "wall": [fighter_row(one) for one in siege.wall.values()],
        "party": [
            fighter_row(one, viewer_id, user_id in siege.acted)
            for user_id, one in siege.party.items()
        ],
        # Кого смотрящий бьёт сейчас. Пусто — некого: его защитника
        # добили, а следующий достанется со следующего размена
        "foe": fighter_row(foe) if foe is not None else None,
        "yours": viewer_id in siege.party,
        "alive": bool(fighter and fighter.alive),
        "acted": viewer_id in siege.acted,
        "seconds_left": siege.seconds_left(),
        "chosen": {
            "attacks": {
                str(hand): zone.value
                for hand, zone in (mine.attacks.items() if mine else ())
            },
            "block": mine.block[0].value if mine and mine.block else None,
        },
        # Приёмы — только свои: чужая заготовка перестаёт быть
        # неожиданностью, если её видно
        "abilities": abilities_payload(fighter),
        "log": siege.rounds[-LOG_TAIL:],
    }


async def build_war(
    db, player: Player, war, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Карта войны вместе с тем, что у бойца идёт прямо сейчас.

    Ответ всегда полный: панель опрашивает ручку раз в пару секунд, и по
    нему видно, что рисовать — карту, сбор, волну или итог.
    """
    body = await build_turf(db, player, now, said)
    if war is None:
        # Войны может не быть: карту точек собирают и без неё — так её
        # берут снимки и проверки, которым до кнопок дела нет
        body.update({"muster": None, "siege": None, "result": None})
        for row in body["spots"]:
            row["busy"] = False
        return body
    muster = war.muster_of_user(player.user_id)
    siege = war.siege_of_user(player.user_id)
    result = war.result_of_user(player.user_id)
    body["muster"] = (
        muster_payload(muster, player.user_id, war.lobby_seconds)
        if muster is not None
        else None
    )
    body["siege"] = siege_payload(siege, player.user_id) if siege else None
    body["result"] = siege_payload(result, player.user_id) if result else None
    # Где уже дерутся: на такую точку второй отряд не собирают
    busy = {one.spot.code for one in war.musters()} | {
        one.spot.code for one in war.sieges()
    }
    for row in body["spots"]:
        if row["code"] in busy:
            row["can_storm"] = False
            row["busy"] = True
        else:
            row["busy"] = False
    # Точка под ногами — та же строка, и знать она должна то же самое:
    # иначе панель объясняла бы погасшую кнопку не тем
    panel = body.get("here_spot")
    if panel is not None:
        panel["busy"] = panel["code"] in busy
        if panel["busy"]:
            panel["can_storm"] = False
    return body


# ---------- отметки войны на карте города ----------


def map_marks(body: dict[str, Any]) -> dict[str, Any]:
    """Война глазами карты: отметка у вывески и плашка под ногами.

    Считается из готового ответа войны, а не заново: два списка одних и
    тех же точек однажды разошлись бы, и карта показала бы одного
    владельца, а особняк другого.

    Под вывеску идёт одна отметка и больше ничего. Владелец и доход
    стояли там строкой на каждом доме — карта от этого читалась как
    ведомость; всё это есть в особняке, а на карте довольно знать, что
    дом вообще берут.
    """
    spots = {
        row["code"]: {
            "mark": row["sign"],
            "badge": row["badge"],
            "mine": row["mine"],
        }
        for row in body["spots"]
    }
    row = next((one for one in body["spots"] if one["here"]), None)
    band = _band(body, row) if row else None
    # Плашки без кнопки не бывает: нажимать нечего, а место на карте
    # она занимает и дверь закрывает. «Держит такой-то» — ответ на
    # вопрос, которого у двери не задавали
    return {"spots": spots, "here": band if band and band["button"] else None}


def _band(body: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Плашка у дома: чей он, сколько приносит и что можно нажать."""
    in_hand = bool(body.get("muster") or body.get("siege") or body.get("result"))
    band = {
        "code": row["code"],
        "title": row["title"],
        "mark": row["mark"],
        "badge": row["badge"],
        "owner": row["owner"]["title"],
        "income": row["income"],
        "mine": row["mine"],
        "in_hand": in_hand,
    }
    if in_hand:
        band["button"] = "open"
        band["title"] = "Открыть"
        band["text"] = (
            "Штурм идёт." if body.get("siege") else
            "Итог штурма ждёт." if body.get("result") else
            "Отряд собирается."
        )
    elif row["can_storm"]:
        # Захват у семьи, задержание у города: слово на кнопке — то же,
        # что на экране точки
        band["button"] = row["action"]
        band["title"] = row["action_title"]
        band["text"] = f"Держит: {row['owner']['title']}."
    elif row["can_free"]:
        # Пустой дом семьи город снимает без боя — и кнопка об этом
        # говорит словом, а не «захватом»
        band["button"] = FREE
        band["title"] = ACTION_TITLES[FREE]
        band["text"] = f"Держит: {row['owner']['title']}. Охраны нет."
    elif row["action"] == GUARD:
        # Охрана: воевать тут не с кем, а посмотреть, кто на точке
        # стоит, и поставить ещё одного — надо
        band["button"] = GUARD
        band["title"] = ACTION_TITLES[GUARD]
        band["text"] = ""
    else:
        band["button"] = ""
        band["title"] = ""
        band["text"] = _why_not(body, row)
    return band


def _why_not(body: dict[str, Any], row: dict[str, Any]) -> str:
    """Почему кнопки сбора нет. Молча гасить её — значит врать."""
    if row["mine"]:
        return "Точка и так ваша."
    if row.get("busy"):
        return "За точку уже дерутся."
    if body["side"]["nobody"]:
        return "Воевать за территории тебе пока не за кого."
    return f"Держит: {row['owner']['title']}."


__all__ = [
    "build_turf",
    "build_war",
    "fighter_row",
    "house_mark",
    "map_marks",
    "muster_payload",
    "siege_payload",
    "spot_row",
]
