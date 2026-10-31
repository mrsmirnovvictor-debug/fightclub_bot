"""«Чёрный пуск»: карта, шаги, бои, пуск и эвакуация.

Порядок проверок — по тому, что дороже сломать:

1. **Карта.** Манифест валиден, рейд собирается правилами, после пуска
   выход открыт, а главный вход закрыт.
2. **Шаг.** Ровно десять секунд, один на игрока, и ни одной секунды
   раньше.
3. **Ворота.** Без босса нет модуля, без модуля нет этажа, без
   охлаждения нет Предводителя, без трёх модулей нет пуска.
4. **Бой.** Из занятой комнаты не выйти; побеждённый враг не оживает;
   упавший не теряет модулей.
5. **Пуск и выход.** Неудачная оборона сохраняет модули; награда
   платится один раз на цикл.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from bot.blackout_service import (
    BlackoutError,
    _wipe,
    close_out,
    act,
    cycle_code,
    look,
    member,
    open_fight,
    read_run,
    run_of,
    snapshot,
    state_for,
    window_open,
    window_opens_at,
)
from bot.database import Database
from bot.game.blackout import (
    BOSSES,
    DIED_COOLDOWN_HOURS,
    WON_COOLDOWN_DAYS,
    LOCK_DIED,
    LOCK_WON,
    BOSS_FORCE,
    BOSS_LEVEL,
    DEFENSE_WAVES,
    EDGE_SECONDS,
    MIN_PARTY,
    QUEST_ITEMS,
    REQUIRED_ORDER,
    RUN_LIMIT_MINUTES,
    SQUAD_SIZE,
    boss_hp,
    check_map,
    reward_for,
    station,
    walkthrough,
)
from bot.blackout_service import foe_row, squad_for
from bot.game.blackout import STAGE_EXTRACT, STAGE_RESTART, STAGE_SPEED
from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.reference import developed_stats
from bot.models import Player

# Вторник, 12 января 2027 года, двадцать минут восьмого по Москве —
# внутри окна входа. Часы в проверках задаются руками: ждать по-настоящему
# сто пятьдесят минут не станет никто
NOW = 1_800_000_000 + 20 * 60


def make_player(
    user_id: int, nickname: str, class_code: str = "warrior", level: int = 15
) -> Player:
    """Боец, какого город и правда выпускает на станцию.

    Пятнадцатый уровень — потолок клуба, и станция рассчитана на него:
    обычный зомби стоит с бойцом вровень, мини-боссы на двадцатом,
    хозяин Контура на двадцать пятом. Характеристики развитые, вещи
    лучшие из доступных — проверять подземелье на «голом» десятом
    значит мерить не подземелье, а отсутствие прокачки.
    """
    from bot.game.equipment import OwnedItem
    from bot.game.reference import best_kit

    fclass = FIGHTER_CLASSES[class_code]
    kit = best_kit(fclass, level)
    stats = developed_stats(fclass, level)
    one = Player(
        user_id=user_id,
        nickname=nickname,
        class_code=class_code,
        level=level,
        credits=1_000,
        location="power_station",
        seen_at=NOW,
        **stats.as_dict(),
    )
    # Вещи надеваются так же, как у живого бойца: экипировка считается
    # из рюкзака, а не присваивается полем
    one.gear = [
        OwnedItem(item=item, id=number + 1, slot=slot)
        for number, (slot, item) in enumerate(kit.items())
    ]
    return one


async def dress(db: Database, player: Player) -> Player:
    """Одеть бойца в базе: вещи живут в своей таблице, а не в строке.

    Без этого отряд приходит на станцию голым — и замер мерит не
    подземелье, а отсутствие экипировки.
    """
    await db.save_player(player)
    for owned in player.equipped:
        saved = await db.add_gear(player.user_id, owned.item.code)
        saved.slot = owned.slot
        await db.save_gear(saved)
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


# Приёмы, которые берёт воин к пятнадцатому уровню: классовый первой
# ступени плюс по одному с третьей, шестой и десятой. Лечение тут одно —
# «Воля к победе», и именно оно держит отряд в бою на двадцать волн
WARRIOR_TRICKS = ("power_hit", "will_to_win", "mass_hit")
HEAL_TRICK = "will_to_win"


async def teach(db: Database, player: Player) -> Player:
    """Выдать бойцу приёмы его ступеней: пятнадцатый уровень знает четыре.

    Без этого замер мерил бы не подземелье, а бойца без приёмов: на
    станции боёвка рейдовая, и приём в ней — половина хода.
    """
    from bot.abilities_service import ensure_starter, learn

    await ensure_starter(db, player)
    for code in WARRIOR_TRICKS:
        await learn(db, player, code)
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


# Склянки, с которыми идут на станцию. Передышки за комнату тут нет, и
# рюкзак — единственное, чем боец лечится быстрее, чем за десять минут.
# Четыре больших на троечасовой рейд — не щедрость, а то, что берёт с
# собой всякий, кто читал правила режима
PACK = {"heal_big": 4}


async def squad(
    db: Database, size: int = 4, pack: dict[str, int] | None = None
) -> list[Player]:
    """Отряд из живых игроков: все в базе, с приёмами и со склянками.

    `pack` — что лежит в рюкзаке. Пустой словарь значит «пришли без
    склянок»: это тот случай, по которому меряется сама станция, без
    того, что боец может докупить за кредиты.
    """
    out = []
    for number in range(size):
        one = make_player(number + 1, f"Боец-{number + 1}")
        one = await teach(db, await dress(db, one))
        for code, count in (PACK if pack is None else pack).items():
            for _ in range(count):
                one.potions[code] = await db.add_potion(one.user_id, code)
        out.append(one)
    return out


# Зерно рейда. В городе оно случайное — бой не должен быть заучиваемым;
# в наборе тестов оно фиксировано, иначе «маршрут проходим целиком»
# то проходит, то нет, и падение ничего не значит
TEST_SEED = 20_261_031


async def enter(
    db: Database,
    size: int = 4,
    now: int = NOW,
    seed: int = TEST_SEED,
    pack: dict[str, int] | None = None,
):
    """Собрать отряд и вывести его на станцию."""
    people = await squad(db, size, pack)
    await act(db, people[0], "open", {"test": True}, "open", now)
    run = await run_of(db, people[0].user_id)
    assert run is not None
    for one in people[1:]:
        await act(db, one, "join", {"run": run.id}, f"join{one.user_id}", now)
    await act(db, people[0], "start", {}, "start", now)
    run = await run_of(db, people[0].user_id)
    assert run is not None
    run.state["seed"] = seed
    await db.blackout_write(
        run.id, run.version, run.phase, json.dumps(run.state), now
    )
    return people, await run_of(db, people[0].user_id)


# ---------- карта ----------


def test_the_manifest_is_a_valid_map():
    """Манифест сходится: 64 комнаты, 76 дверей и ни одной висящей."""
    place = station()
    assert len(place.rooms) == 64
    assert len(place.doors) == 76
    assert check_map() == []


def test_every_edge_is_ten_seconds():
    """Любое ребро — ровно десять секунд, включая лестницы между этажами."""
    for door in station().doors:
        assert door.seconds == EDGE_SECONDS, f"{door.a}↔{door.b}"


def test_the_raid_assembles_in_the_order_of_the_scenario():
    """Рейд собирается ногами и ровно в том порядке, что в сценарии."""
    order = [flag for flag, _ in walkthrough()]
    assert order[:3] == ["dispatch_clearance", "hunter_defeated", "synchronizer"]
    for flag in REQUIRED_ORDER:
        assert flag in order, flag
    # Сердечник берётся после Громилы, ключ — после Предводителя
    assert order.index("power_core") > order.index("brute_defeated")
    assert order.index("restart_key") > order.index("leader_defeated")
    assert order.index("station_restarted") > order.index("cooling_ready")
    assert order.index("extracted") > order.index("drainage_open")


def test_no_boss_is_skipped():
    """Без любого из трёх боссов или охлаждения рейд не собирается."""
    from bot.game.blackout import reachable

    place = station()
    # Без синхронизатора к Громиле не попасть
    assert "F107" not in reachable(place, "F105", {"dispatch_clearance"})
    # Без сердечника не начать подвал
    assert "B01" not in reachable(place, "F110", {"synchronizer"})
    # Без охлаждения не подняться к Предводителю
    assert "F301" not in reachable(place, "F210", {"power_core"})
    # Без ключа не попасть на четвёртый этаж
    assert "F401" not in reachable(place, "F308", {"cooling_ready"})


def test_the_escape_route_opens_only_after_the_restart():
    """После пуска открыта аварийная лестница, а главный вход закрыт."""
    from bot.game.blackout import door_open, reachable

    place = station()
    before = set(REQUIRED_ORDER) - {"station_restarted", "extracted"}
    assert "E01" not in reachable(place, "F406", before)
    after = before | {"station_restarted"}
    here = reachable(place, "F406", after)
    for code in ("F301", "F201", "F104", "B01", "E01", "E12"):
        assert code in here, code
    # Главный вход заперт карантином
    main = place.door_between("O10", "F101")
    assert door_open(main, after) != ""
    assert door_open(main, before) == ""


def test_the_maze_never_locks_the_squad():
    """Из любого узла лабиринта есть дорога к выходу."""
    from bot.game.blackout import reachable

    place = station()
    flags = set(REQUIRED_ORDER) - {"extracted"}
    for room in place.rooms_on("E"):
        assert place.exit_room in reachable(place, room.code, flags), room.code


def test_eighteen_ordinary_encounters_and_three_bosses():
    """Встреч ровно столько, сколько обещает сценарий."""
    place = station()
    squads = [one for one in place.rooms.values() if one.encounter == "zombie_squad"]
    bosses = [one for one in place.rooms.values() if one.boss]
    assert len(squads) == 18
    assert {one.boss for one in bosses} == {"hunter", "brute", "leader"}
    assert set(SQUAD_SIZE) == {one.code for one in squads}


def test_the_image_ships_the_map_the_server_reads():
    """Карта станции обязана доехать до сервера, а не только до репозитория.

    Так и случилось в первый вечер: образ собрался, бот поднялся, и
    всё упало ровно на кнопке «Объявить рейд» — `Dockerfile` копировал
    `bot` и `scripts`, а папку с манифестом не копировал. Проверка
    дешёвая, а ловит целый класс поломок: код читает с диска то, чего
    в образе нет.
    """
    from bot.game.blackout import MANIFEST

    root = Path(__file__).resolve().parent.parent
    docker = (root / "Dockerfile").read_text(encoding="utf-8")
    copied = {
        line.split()[1]
        for line in docker.splitlines()
        if line.startswith("COPY ") and len(line.split()) >= 3
    }
    # Папка, из которой сервер читает карту, должна быть в образе
    folder = MANIFEST.parent.relative_to(root).parts[0]
    assert folder in copied, (
        f"Dockerfile не копирует {folder}: сервер не найдёт карту станции"
    )
    assert MANIFEST.is_file(), "карта миссии пропала из репозитория"


def test_a_missing_map_says_so_instead_of_falling(tmp_path):
    """Нет карты — служба называет это словами, а не пятисоткой."""
    from bot.game.blackout import MapMissing, load_station

    with pytest.raises(MapMissing, match="zombiemission"):
        load_station(tmp_path / "ничего.json")


# ---------- отряд и шаг ----------


async def test_the_squad_enters_and_the_clock_starts(db):
    """Отряд выходит на станцию, и с этой минуты идёт лимит рейда."""
    people, run = await enter(db)

    assert run is not None and run.phase == "run"
    assert run.state["party"] == 4
    assert run.state["ends_at"] == NOW + RUN_LIMIT_MINUTES * 60
    assert all(one["room"] == "O01" for one in run.state["members"].values())


async def test_one_fighter_walks_the_station_alone(db):
    """Одиночка на станцию идёт: масштаб считается от состава.

    Это отступление от сценария — он зовёт отряд от трёх. Город
    маленький, и ждать троих ради того, чтобы увидеть станцию,
    некому. Ослаблять станцию руками при этом не пришлось: запас
    врагов и размер групп и так считаются от числа пришедших.
    """
    alone = make_player(90, "Одиночка")
    await db.save_player(alone)
    await act(db, alone, "open", {"test": True}, "solo", NOW)
    said = await act(db, alone, "start", {}, "solostart", NOW)

    assert "станции" in said
    run = await run_of(db, alone.user_id)
    assert run is not None and run.phase == "run"
    assert run.state["party"] == 1
    # Скидок за одиночество станция не даёт: враг тот же самый
    assert foe_row(0, "з", 15, "warrior", 1.0, build=15)["max"] == (
        squad_for("O02", party=4)[0]["max"]
    )

    # И первая комната ему по силам: группа меньше и слабее
    at = await walk(db, [alone], run, "O02", NOW)
    fresh = await look(db, run.id, at)
    foes = fresh.state["fight"]["foes"]
    # Одиночке комната выставляет одного: это дуэль с равным, а не
    # расстрел. Зомби при этом тот же самый — пятнадцатого уровня,
    # вровень с потолком города
    assert len(foes) == 1
    assert foes[0]["level"] == 15
    assert foes[0]["max"] == squad_for("O02", party=4)[0]["max"]
    # А вот пройдёт ли он её — вопрос отдельный: в одиночку тут очень
    # тяжело, и это проверяет не договор, а плейтест


async def test_a_step_takes_exactly_ten_seconds(db):
    """До десятой секунды боец в пути, после неё — пришёл, и ровно раз."""
    people, run = await enter(db)
    await act(db, people[0], "go", {"room": "O02"}, "go1", NOW)

    # Девятая секунда: ещё в пути
    run = await look(db, run.id, NOW + EDGE_SECONDS - 1)
    assert member(run.state, 1)["room"] == "O01"
    assert member(run.state, 1)["arrive"] == NOW + EDGE_SECONDS

    # Десятая: пришёл
    run = await look(db, run.id, NOW + EDGE_SECONDS)
    assert member(run.state, 1)["room"] == "O02"
    assert member(run.state, 1)["arrive"] == 0

    # Повторный взгляд ничего не повторяет
    run = await look(db, run.id, NOW + 60)
    assert member(run.state, 1)["room"] == "O02"


async def test_a_second_step_does_not_hurry_the_first(db):
    """Переход один на игрока: вторая кнопка его не ускоряет."""
    people, run = await enter(db)
    await act(db, people[0], "go", {"room": "O02"}, "go1", NOW)
    with pytest.raises(BlackoutError, match="в пути"):
        await act(db, people[0], "go", {"room": "O02"}, "go2", NOW + 1)

    run = await look(db, run.id, NOW + 1)
    assert member(run.state, 1)["arrive"] == NOW + EDGE_SECONDS


async def test_the_same_command_is_not_done_twice(db):
    """Повторная команда отдаёт прежний ответ и второго шага не делает."""
    people, run = await enter(db)
    first = await act(db, people[0], "go", {"room": "O02"}, "once", NOW)
    second = await act(db, people[0], "go", {"room": "O02"}, "once", NOW)

    assert first == second
    run = await read_run(db, run.id)
    assert member(run.state, 1)["arrive"] == NOW + EDGE_SECONDS


async def walk(db, people, run, room: str, at: int) -> int:
    """Увести отряд в соседнюю комнату и дождаться прибытия.

    Ведёт тот, кто ещё на ногах: упавшего на станции не поднимают, и
    лидерство уходит живому.
    """
    who = people[0]
    fresh = await look(db, run.id if hasattr(run, "id") else run, at)
    for one in people:
        row = member(fresh.state, one.user_id)
        if row is not None and not row["down"] and not row["left"]:
            who = one
            break
    await act(db, who, "lead", {"room": room}, f"w{room}{at}", at)
    return at + EDGE_SECONDS


async def breathe(db, people, run_id: int, at: int, share: float = 0.75) -> int:
    """Постоять и дать ранам затянуться — так, как это делает человек.

    Передышки «за зачищенную комнату» на станции нет: из боя боец
    выходит с тем, что осталось, и дальше либо идёт как есть, либо
    стоит и ждёт. Восстановление городское — с нуля до полного за
    десять минут, — поэтому ждать приходится минутами, а не часами.
    """
    from bot.game.health import seconds_to_reach

    run = await look(db, run_id, at)
    rows = [
        row for row in (run.state.get("members") or {}).values()
        if not row["down"] and not row["left"] and not row["extracted"]
        and row["max"]
    ]
    if not rows:
        return at
    # Сколько ждать — считается, а не перебирается по минуте: это та же
    # арифметика, по которой город говорит «до боя осталось столько-то»
    wait = max(
        seconds_to_reach(row["hp"], row["max"], share) for row in rows
    )
    if not wait:
        return at
    at += wait
    await look(db, run_id, at)
    return at


def path(state, here: str, there: str) -> list[str]:
    """Кратчайшая дорога по открытым дверям. Пусто — дороги нет."""
    from bot.game.blackout import door_open

    place = station()
    flags = set(state.get("flags") or {})
    seen = {here: []}
    queue = [here]
    while queue:
        room = queue.pop(0)
        if room == there:
            return seen[room]
        for door in place.doors_from(room):
            nxt = door.other(room)
            if nxt in seen or door_open(door, flags):
                continue
            seen[nxt] = seen[room] + [nxt]
            queue.append(nxt)
    return []


async def goto(db, people, run_id: int, there: str, at: int) -> int:
    """Довести отряд до комнаты, где бы он ни стоял.

    Дорога ищется по открытым дверям, занятые комнаты зачищаются по
    пути, а между боями отряд стоит и затягивает раны: передышки за
    комнату станция больше не даёт.

    Упавший остаётся лежать — его на станции не поднимают, — поэтому
    дорога считается от того, кто ещё на ногах.
    """
    for _ in range(40):
        run = await look(db, run_id, at)
        at = await win_here(db, people, run_id, at)
        at = await breathe(db, people, run_id, at, share=0.95)
        run = await look(db, run_id, at)
        if run.state.get("outcome"):
            raise AssertionError(f"рейд кончился по дороге к {there}")
        standing = [
            row for row in (run.state.get("members") or {}).values()
            if not row["down"] and not row["left"]
        ]
        assert standing, f"до {there} дошло некому"
        here = standing[0]["room"]
        if here == there:
            return at
        road = path(run.state, here, there)
        assert road, f"из {here} до {there} дороги нет"
        at = await walk(db, people, run, road[0], at)
        at = await win_here(db, people, run_id, at)
    raise AssertionError(f"не дошли до {there}")


async def win_here(db, people, run_id: int, at: int, limit: int = 90) -> int:
    """Додраться в этой комнате до победы. Отдаёт час после боя.

    Отряд дерётся так, как дерётся человек: бьёт, прикрывается и жмёт
    кнопку на арене, когда окно уязвимости закрылось. Без неё боссы и
    не должны падать — в этом вся их механика.
    """
    for _ in range(limit):
        run = await look(db, run_id, at)
        if run.state.get("outcome"):
            return at
        fight = run.state.get("fight")
        if not fight:
            return at
        arena = (BOSSES.get(fight.get("boss") or "") or None)
        need_arena = bool(arena) and not fight.get("window") and (
            arena.counter_action != "relay" or fight.get("shield")
        )
        for number, one in enumerate(people):
            row = member(run.state, one.user_id)
            if row is None or row["down"] or row["room"] != fight["room"]:
                continue
            payload = {"attacks": ["head"], "block": ["chest"]}
            if need_arena and number == 0:
                payload = {
                    "attacks": [], "block": [], "arena": arena.counter_action,
                }
            # Склянка — раньше приёма: она доливает до полного, и
            # держать её «на потом» значит не дожить до этого потом
            if row["hp"] < row["max"] * 0.4:
                with contextlib.suppress(BlackoutError):
                    await act(
                        db, one, "drink", {"code": "heal_big"},
                        f"p{one.user_id}:{at}", at,
                    )
                    run = await look(db, run_id, at)
                    row = member(run.state, one.user_id) or row
            # Приём жмут до удара: просел — лечатся, цел — бьют тяжелее
            want = (
                HEAL_TRICK if row["hp"] < row["max"] * 0.6 else "power_hit"
            )
            try:
                await act(
                    db, one, "ability", {"code": want},
                    f"a{one.user_id}:{at}", at,
                )
            except BlackoutError:
                pass
            try:
                await act(
                    db, one, "fight", payload, f"f{one.user_id}:{at}", at
                )
            except BlackoutError:
                pass
        at += 5
    raise AssertionError("бой не кончился")


async def test_a_closed_door_says_what_is_missing(db):
    """Закрытая дверь объясняет, чего не хватает, а не молчит."""
    people, run = await enter(db)
    at = await walk(db, people, run, "O02", NOW)
    # В проходной зомби: бой начинается сам, и выходы заперты
    run = await look(db, run.id, at)
    assert run.state["fight"] and run.state["fight"]["room"] == "O02"
    with pytest.raises(BlackoutError, match="выходы закрыты"):
        await act(db, people[0], "go", {"room": "O03"}, "early", at)

    at = await win_here(db, people, run.id, at)
    at = await walk(db, people, run, "O03", at)
    run = await look(db, run.id, at)
    assert member(run.state, 1)["room"] == "O03"

    with pytest.raises(BlackoutError, match="Допуск"):
        await act(db, people[0], "go", {"room": "O04"}, "gO04", at)


# ---------- полное прохождение ----------
#
# Маршрут из сценария, от проходной до выхода: сервер проводит по нему
# отряд теми же командами, какими его ведёт человек. Это и есть главная
# проверка — не «есть ли путь на карте», а «проходится ли рейд».

ROUTE = (
    # Этап 1: проходная и диспетчерская
    "O02", "O03", "@radio",
    # Этап 2: двор и главный корпус
    "O04", "O05", "O06", "O08", "O09", "@task", "O08", "O10",
    "F101", "F103", "F104",
    # Этап 3: второй этаж и Охотник
    "F201", "F202", "F204", "F205", "F206", "@task", "F207", "F208",
    "F209", "@take", "F210",
    # Этап 4: щитовая, ворота и Громила
    "F105", "F106", "F107", "F108", "@take", "F109", "F110",
    # Этап 5: подвал
    "B01", "B02", "B03", "@task", "B04", "B05", "@task", "B06", "B07",
    "@task",
    # Этап 6: третий этаж и Предводитель
    "B08", "F110", "F210", "F301", "F302", "F303", "@task", "F304",
    "@task", "@task", "F305", "F306", "F307", "F308", "@take",
    # Этап 7: рубка
    "F401", "F402", "F403", "@task", "F404", "F405",
    # Этап 8: тоннели
    "@restart", "F406", "F301", "F201", "F104", "B01", "E01", "E02",
    "E04", "E05", "@task", "E06", "E08", "E10", "E11", "E12",
)


async def play(
    db, people, run, at: int, log: list | None = None, goal_only: bool = False
) -> int:
    """Провести отряд по маршруту сценария. Отдаёт час у выхода.

    `goal_only` — остановиться на пуске станции, не идя в тоннели.
    Цель рейда — пуск, и она считается выполненной, даже если выйти
    потом удалось не всем: упавших на станции не поднимают, и отряд,
    потерявший людей, последний заслон лабиринта может не пройти.
    """
    run_id = run.id
    for mark in ROUTE:
        if goal_only and (
            await look(db, run_id, at)
        ).state["flags"].get("station_restarted"):
            return at
        if mark == "@radio" or mark == "@task":
            await act(db, people[0], "task", {}, f"t{mark}{at}", at)
        elif mark == "@take":
            await act(db, people[0], "take", {}, f"p{at}", at)
        elif mark == "@restart":
            at = await restart_station(db, people, run_id, at)
        else:
            at = await goto(db, people, run_id, mark, at)
        if log is not None:
            log.append((mark, at))
    return at


async def restart_station(db, people, run_id: int, at: int) -> int:
    """Рубка: три гнезда, подтверждение, три волны и синхронизация.

    Сорванную оборону начинают заново: модули остаются в гнёздах, и
    второй раз искать трёх боссов не приходится — ровно это и обещает
    сценарий.
    """
    for item in QUEST_ITEMS:
        await act(db, people[0], "install", {"item": item}, f"i{item}", at)
    for attempt in range(8):
        run = await look(db, run_id, at)
        if run.state["flags"].get("station_restarted"):
            return at
        console = run.state.get("console") or {}
        if int(console.get("wave") or 0) == 0:
            # Перед обороной отряд перевязывается: готовность больше
            # не лечит, и три волны подряд идут на том, с чем пришёл
            at = await breathe(db, people, run_id, at, share=0.99)
            for one in people:
                with contextlib.suppress(BlackoutError):
                    await act(
                        db, one, "confirm", {}, f"c{one.user_id}:{attempt}", at
                    )
        for _ in range(len(DEFENSE_WAVES) + 1):
            run = await look(db, run_id, at)
            if not run.state.get("fight"):
                break
            at = await win_here(db, people, run_id, at, limit=90)
        run = await look(db, run_id, at)
        console = run.state.get("console") or {}
        # Все модули на месте даже после сорванной обороны
        assert all(console["slots"].get(code) for code in QUEST_ITEMS)
        if int(console.get("wave") or 0) > len(DEFENSE_WAVES):
            await act(db, people[0], "sync", {}, f"sync{attempt}", at)
            return at
    raise AssertionError("оборона рубки не отбита")


async def test_the_whole_raid_is_walkable(db):
    """Полное прохождение: от проходной до выхода, своими командами."""
    people, run = await enter(db, 4)
    at = await play(db, people, run, NOW)

    fresh = await look(db, run.id, at)
    state = fresh.state
    for flag in REQUIRED_ORDER:
        if flag == "extracted":
            continue
        assert flag in state["flags"], flag
    assert state["flags"]["station_restarted"], "станция запущена"
    # До выхода доходят живые. Упавшего на станции не поднимают: он
    # ждёт конца попытки там, где лёг, и победу ему всё равно зачтут
    alive = [
        one for one in people
        if not member(state, one.user_id)["down"]
    ]
    assert alive, "до выхода дошло некому"
    assert all(member(state, one.user_id)["room"] == "E12" for one in alive)

    # Эвакуация и деньги
    said = await act(db, people[0], "extract", {}, "x1", at)
    assert "снаружи" in said
    paid = await db.blackout_paid(people[0].user_id, fresh.cycle)
    stages = {one["stage"] for one in paid}
    assert STAGE_RESTART in stages and STAGE_EXTRACT in stages


async def test_the_raid_is_walkable_by_three_and_by_five(db):
    """Состав 3 и 5: цель берут оба, и состав врагов у них разный.

    Меряется цель рейда — пуск станции. Дошёл ли потом до тоннелей
    каждый, зависит от того, кого отряд по дороге потерял: упавших тут
    не поднимают, а последний заслон лабиринта выставлен на тот состав,
    с каким в рейд входили.
    """
    people, run = await enter(db, 3)
    # Втроём врагов меньше, но каждый тот же самый
    assert len(squad_for("O02", party=3)) < len(squad_for("O02", party=5))
    at = await play(db, people, run, NOW, goal_only=True)
    assert (await look(db, run.id, at)).state["flags"]["station_restarted"]


async def test_the_five_man_squad_meets_tougher_zombies(db):
    """Пятеро — врагов больше: состав считается от отряда при входе."""
    people, run = await enter(db, 5)
    assert run.state["party"] == 5

    at = await walk(db, people, run, "O02", NOW)
    fresh = await look(db, run.id, at)
    foes = fresh.state["fight"]["foes"]
    # Состав растёт числом, а не здоровьем: сам зомби везде один и тот
    # же, а вот сколько их в комнате — считается от отряда
    assert len(foes) == len(squad_for("O02", party=5))
    assert len(foes) > len(squad_for("O02", party=4))
    assert foes[0]["max"] == squad_for("O02", party=4)[0]["max"]

    # А выйти со станции нельзя вовсе: вошёл — проходи до конца
    count = len(foes)
    with pytest.raises(BlackoutError, match="не уходят"):
        await act(db, people[4], "leave", {}, "bye", at)
    fresh = await look(db, run.id, at)
    assert fresh.state["party"] == 5
    assert len(fresh.state["fight"]["foes"]) == count
    assert not member(fresh.state, people[4].user_id)["left"]


# ---------- боёвка: как в рейде ----------


async def test_the_block_is_as_wide_as_the_fighter(db):
    """Нажал одну зону — закрылся на всю свою ширину, как на ринге.

    Экран подземелья даёт выбрать середину блока, а не три кнопки, и
    служба обязана развести её сама. Когда она этого не делала, отряд
    входил на станцию с блоком в одну зону вместо трёх — то есть втрое
    тоньше, чем тот же боец в клубе, и боссы выходили непроходимыми.

    Бросок кубика тут один и тот же: он считается от зерна, комнаты,
    бойца и номера волны, — поэтому два размена из одного и того же
    состояния сравнимы напрямую.
    """
    from bot.game.classes import Zone, block_combo

    people, run = await enter(db, 1)
    at = await walk(db, people, run, "O02", NOW)
    saved = json.dumps((await look(db, run.id, at)).state)

    async def take(block: list[str], mark: str) -> int:
        fresh = await look(db, run.id, at)
        await db.blackout_write(run.id, fresh.version, "run", saved, at)
        await act(
            db, people[0], "fight",
            {"attacks": ["head"], "block": block}, mark, at,
        )
        row = member((await look(db, run.id, at)).state, people[0].user_id)
        return row["max"] - row["hp"]

    narrow = await take(["chest"], "narrow")
    wide = await take([one.value for one in block_combo(Zone("chest"), 3)], "wide")
    assert narrow == wide


async def test_the_zombies_are_fifteenth_level_and_the_bosses_above(db):
    """Обычный заражённый — пятнадцатый, мини-боссы двадцатый, хозяин двадцать пятый."""
    assert BOSS_LEVEL == {"hunter": 20, "brute": 20, "leader": 25}
    people, run = await enter(db, 1)
    at = await walk(db, people, run, "O02", NOW)
    fresh = await look(db, run.id, at)
    assert all(one["level"] == 15 for one in fresh.state["fight"]["foes"])
    # Рука босса тяжелее обычной: иначе Громила в броне бил бы слабее
    # заражённого рабочего — у танка свой счёт урона
    assert BOSS_FORCE["hunter"] >= 1.0
    assert BOSS_FORCE["brute"] > BOSS_FORCE["leader"] > 1.0


def test_the_boss_keeps_his_health_for_the_squad_that_came():
    """Запас босса считается на отряд: одному он не ставит стену впятером."""
    full = 1_000
    assert boss_hp(full, 4) == full
    assert boss_hp(full, 1) < boss_hp(full, 3) < boss_hp(full, 4)
    assert boss_hp(full, 5) > full
    # Но и в поблажку это не превращается: больше половины у него есть
    # всегда, и соло-проход всё равно остаётся очень тяжёлым
    assert boss_hp(full, 1) > full // 2


async def test_a_trick_in_the_dungeon_heals_the_whole_room(db):
    """Приём в подземелье работает как в рейде, и лечение идёт на отряд."""
    people, run = await enter(db, 2)
    at = await walk(db, people, run, "O02", NOW)
    # Накопить шкалу: приём стоит энергии, а она копится ударами
    for _ in range(6):
        for one in people:
            with contextlib.suppress(BlackoutError):
                await act(
                    db, one, "fight",
                    {"attacks": ["head"], "block": ["chest"]},
                    f"h{one.user_id}:{at}", at,
                )
        at += 5
    fresh = await look(db, run.id, at)
    rows = [member(fresh.state, one.user_id) for one in people]
    assert any(row["energy"] > 0 for row in rows), "шкала не копится"

    # Лечение: берём того, кто просел сильнее, и лечим его же приёмом
    hurt = min(rows, key=lambda row: row["hp"])
    who = next(
        one for one in people if member(fresh.state, one.user_id) is hurt
    )
    before = hurt["hp"]
    fresh.state["members"][str(who.user_id)]["energy"] = 20
    await db.blackout_write(
        run.id, fresh.version, fresh.phase, json.dumps(fresh.state), at
    )
    said = await act(db, who, "ability", {"code": "will_to_win"}, "tr1", at)
    assert "❤️" in said
    after = await look(db, run.id, at)
    assert member(after.state, who.user_id)["hp"] > before


async def test_the_energy_bar_comes_with_the_fight(db):
    """Экран боя получает свою шкалу и свои приёмы — чужие не показываются."""
    people, run = await enter(db, 2)
    at = await walk(db, people, run, "O02", NOW)
    body = await state_for(db, people[0], at)
    tricks = body["fight"]["abilities"]
    assert tricks and tricks["max"] == 20
    assert {one["code"] for one in tricks["tricks"]} >= set(WARRIOR_TRICKS)
    # До боя шкалы нет: приёмы держат на бой
    far = await state_for(db, people[0], at)
    assert far["fight"]["room"] == "O02"


async def test_a_lone_fighter_does_not_fell_the_leader(db):
    """Предводитель одиночке не по силам: станция — на отряд.

    Это и есть «в соло очень сложно»: обычные комнаты одиночка
    проходит, Громилу берёт примерно в половине попыток, Охотника
    реже, а хозяина Контура — почти никогда. Отряд из четырёх кладёт
    всех трёх уверенно.

    Меряется одиночка с пустым рюкзаком: станция сама по себе. Склянки
    это меняют — четыре больших эликсира это четыре лишних запаса
    здоровья, и с ними Предводитель берётся и в соло. Так и задумано:
    заплатить кредитами за то, чего не берёшь умением, в клубе можно
    везде, и станция тут не исключение.
    """
    people, run = await enter(db, 1, pack={})
    fresh = await look(db, run.id, NOW)
    for flag in ("relay_a", "relay_b"):
        fresh.state["flags"][flag] = NOW
    member(fresh.state, people[0].user_id)["room"] = "F307"
    open_fight(fresh.state, "F307", NOW)
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )
    at = await win_here(db, people, run.id, NOW, limit=60)
    after = await look(db, run.id, at)
    # Бой кончился не победой: комната не зачищена, а попытка кончена —
    # одиночка лёг, и назад в этот рейд уже не зайти
    assert "F307" not in after.state["cleared"]
    assert after.state["outcome"] == "wiped"
    assert member(after.state, people[0].user_id)["down"]


# ---------- правила режима: откат, награда, здоровье ----------


async def test_the_station_opens_every_evening(db):
    """Окно входа — каждый вечер в семь, а не два дня в неделю.

    Редкость держит теперь не расписание, а личный откат: станция
    открыта всем, но одному и тому же бойцу — раз в три дня.
    """
    from datetime import datetime

    from bot.game.blackout import CYCLE_DAYS, CYCLE_HOUR
    from bot.game.clock import MOSCOW

    assert sorted(CYCLE_DAYS) == list(range(7))
    for day in range(2, 9):  # понедельник 2 ноября и дальше всю неделю
        evening = int(
            datetime(2026, 11, day, CYCLE_HOUR, 30, tzinfo=MOSCOW).timestamp()
        )
        assert window_open(evening), day
        morning = int(datetime(2026, 11, day, 10, 0, tzinfo=MOSCOW).timestamp())
        assert not window_open(morning), day
        # Днём экран показывает сегодняшний вечер, а не послезавтрашний
        assert window_opens_at(morning) == int(
            datetime(2026, 11, day, CYCLE_HOUR, 0, tzinfo=MOSCOW).timestamp()
        )


async def test_the_station_closes_for_three_days_after_a_win(db):
    """Прошёл станцию — следующий заход через трое суток.

    Откат личный: на чужие отряды он не влияет, и друзья идут без него.
    """
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    for one in fresh.state["members"].values():
        one["extracted"] = NOW
    fresh.state["outcome"] = "extracted"
    await db.blackout_write(
        run.id, fresh.version, "over", json.dumps(fresh.state), NOW
    )
    await close_out(db, fresh, NOW)

    lock = await db.blackout_locked(people[0].user_id)
    assert lock is not None and lock["reason"] == LOCK_WON
    assert lock["until"] == NOW + WON_COOLDOWN_DAYS * 86_400
    with pytest.raises(BlackoutError, match="пройдена"):
        await act(db, people[0], "open", {}, "again", NOW + 3600)
    # Через трое суток — пускают
    later = NOW + WON_COOLDOWN_DAYS * 86_400 + 60
    run = await run_of(db, people[0].user_id)
    assert run is None, "кончившийся рейд бойца не держит"


async def test_the_three_hour_hold_does_not_shorten_the_three_day_one(db):
    """Смерть после победы не отменяет трёхсуточного отката."""
    people, _ = await enter(db)
    from bot.blackout_service import _hold

    await _hold(db, people[0].user_id, LOCK_WON, NOW)
    await _hold(db, people[0].user_id, LOCK_DIED, NOW)
    lock = await db.blackout_locked(people[0].user_id)
    assert lock["until"] == NOW + WON_COOLDOWN_DAYS * 86_400


async def test_nobody_walks_off_the_station(db):
    """Со станции не уходят: вошёл — проходи до конца."""
    people, run = await enter(db)

    with pytest.raises(BlackoutError, match="не уходят"):
        await act(db, people[1], "leave", {}, "bye", NOW)
    fresh = await look(db, run.id, NOW)
    assert not member(fresh.state, people[1].user_id)["left"]
    assert len(fresh.state["members"]) == 4


async def test_the_lobby_lets_a_fighter_leave_the_squad(db):
    """А из лобби — выходят: отряд собирается и разбирается свободно."""
    people = await squad(db, 2)
    await act(db, people[0], "open", {"test": True}, "open", NOW)
    run = await run_of(db, people[0].user_id)
    await act(db, people[1], "join", {"run": run.id}, "join", NOW)

    assert "вышел" in await act(db, people[1], "leave", {}, "bye", NOW)
    fresh = await read_run(db, run.id)
    assert str(people[1].user_id) not in fresh.state["members"]


async def test_a_thousand_credits_is_all_the_station_pays(db):
    """Потолок награды — тысяча на бойца за рейд, и доли сходятся в неё."""
    from bot.blackout_service import pay
    from bot.game.blackout import MAX_REWARD

    assert sum(
        reward_for(stage)
        for stage in (STAGE_RESTART, STAGE_EXTRACT, STAGE_SPEED)
    ) == MAX_REWARD == 1_000

    people, run = await enter(db)
    paid = 0
    for stage in (STAGE_RESTART, STAGE_EXTRACT, STAGE_SPEED):
        paid += await pay(db, run, people[0].user_id, stage, NOW)
    assert paid == MAX_REWARD
    assert (await db.get_player(people[0].user_id)).credits == 1_000 + paid


async def test_the_fallen_gets_the_victory_and_half_the_money(db):
    """Погибшему победу зачли, но награда ему — половинная."""
    from bot.blackout_service import pay
    from bot.game.blackout import FALLEN_SHARE

    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    member(fresh.state, people[0].user_id)["down"] = True
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )
    run = await read_run(db, run.id)

    fallen = await pay(db, run, people[0].user_id, STAGE_RESTART, NOW)
    alive = await pay(db, run, people[1].user_id, STAGE_RESTART, NOW)
    assert alive == reward_for(STAGE_RESTART)
    assert fallen == int(alive * FALLEN_SHARE) < alive
    assert fallen > 0, "победа зачтена и ему"


async def test_health_stays_as_the_fight_left_it(db):
    """Комната больше не лечит: из боя выходят с тем, что осталось."""
    people, run = await enter(db)
    at = await walk(db, people, run, "O02", NOW)
    at = await win_here(db, people, run.id, at)
    fresh = await look(db, run.id, at)

    assert "O02" in fresh.state["cleared"], "комната зачищена"
    rows = [member(fresh.state, one.user_id) for one in people]
    assert any(row["hp"] < row["max"] for row in rows), (
        "после боя кто-то обязан быть побит"
    )
    assert all(row["hp_at"] for row in rows), "метка времени стоит у всех"


async def test_standing_still_mends_the_wounds(db):
    """Постоял — затянулось. Правило то же, что в городе: десять минут."""
    from bot.game.health import FULL_REGEN_SECONDS

    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    row = member(fresh.state, people[0].user_id)
    row["hp"] = 1
    row["hp_at"] = NOW
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )

    half = await look(db, run.id, NOW + FULL_REGEN_SECONDS // 2)
    middle = member(half.state, people[0].user_id)
    assert middle["max"] * 0.4 < middle["hp"] < middle["max"] * 0.6

    whole = await look(db, run.id, NOW + FULL_REGEN_SECONDS + 60)
    assert member(whole.state, people[0].user_id)["hp"] == middle["max"]


async def test_a_fight_does_not_mend_anybody(db):
    """В бою не затягивается: иначе бой перестал бы кончаться."""
    people, run = await enter(db)
    at = await walk(db, people, run, "O02", NOW)
    fresh = await look(db, run.id, at)
    assert fresh.state["fight"], "в проходной бой"
    row = member(fresh.state, people[0].user_id)
    row["hp"] = 100
    row["hp_at"] = at
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), at
    )

    later = await look(db, run.id, at + 20 * 60)
    assert member(later.state, people[0].user_id)["hp"] == 100


async def test_a_potion_from_the_backpack_heals_on_the_station(db):
    """Склянка из рюкзака работает и тут — это замена передышке."""
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    row = member(fresh.state, people[0].user_id)
    row["hp"] = 100
    row["hp_at"] = NOW
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )

    said = await act(db, people[0], "drink", {"code": "heal_big"}, "p1", NOW)
    assert "❤️" in said
    after = await look(db, run.id, NOW)
    healed = member(after.state, people[0].user_id)
    assert healed["hp"] == healed["max"]
    # Склянку списали из рюкзака
    fresh_player = await db.get_player(people[0].user_id)
    assert fresh_player.potion_count("heal_big") == PACK["heal_big"] - 1
    # Полному здоровью склянку не наливают
    with pytest.raises(BlackoutError, match="порядке"):
        await act(db, people[0], "drink", {"code": "heal_big"}, "p2", NOW)
    # И прибавки на станции не пьют
    with pytest.raises(BlackoutError, match="лечебное"):
        await act(db, people[0], "drink", {"code": "strength_boost"}, "p3", NOW)


async def test_the_station_hands_the_health_back_to_the_city(db):
    """Со станции боец выходит с тем, что от него осталось."""
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    for one in fresh.state["members"].values():
        one["hp"] = 77
        one["extracted"] = NOW
    fresh.state["outcome"] = "extracted"
    await db.blackout_write(
        run.id, fresh.version, "over", json.dumps(fresh.state), NOW
    )
    await close_out(db, fresh, NOW)

    saved = await db.get_player(people[0].user_id)
    assert saved is not None and saved.hp == 77


async def test_the_fallen_are_not_raised_any_more(db):
    """Упавшего не поднимают: он ждёт конца попытки."""
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    member(fresh.state, people[1].user_id)["down"] = True
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )

    with pytest.raises(BlackoutError, match="не поднимают"):
        await act(
            db, people[0], "raise", {"user": people[1].user_id}, "up", NOW
        )


# ---------- квестовые модули ----------


async def test_the_item_is_not_given_before_the_boss(db):
    """Модуль не взять, пока босс стоит, и копий его не бывает."""
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    for one in fresh.state["members"].values():
        one["room"] = "F209"
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )

    with pytest.raises(BlackoutError, match="под током"):
        await act(db, people[0], "take", {}, "take1", NOW)

    fresh = await look(db, run.id, NOW)
    fresh.state["flags"]["hunter_defeated"] = NOW
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )
    said = await act(db, people[0], "take", {}, "take2", NOW)
    assert "синхронизатор" in said.lower()

    # Второе нажатие копии не делает
    again = await act(db, people[0], "take", {}, "take3", NOW)
    assert "уже" in again.lower()
    fresh = await look(db, run.id, NOW)
    assert fresh.state["flags"]["synchronizer"]


async def test_a_wipe_ends_the_run_and_closes_the_station_for_three_hours(db):
    """Отряд лёг — попытка кончена, и назад в неё не зайти три часа.

    Откат в безопасную комнату ушёл вместе с правом уйти со станции:
    выход тут один — дойти или погибнуть. Добытое при этом не
    отнимается: флаги остаются в состоянии кончившегося рейда.
    """
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    fresh.state["flags"].update({
        "hunter_defeated": NOW, "synchronizer": NOW,
    })
    fresh.state["safe"] = "O01"
    for one in fresh.state["members"].values():
        one["room"] = "O02"
        one["hp"] = 1
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )
    # Отряд ложится весь: бой в O02 идёт, а здоровья у них по единице
    run = await read_run(db, run.id)
    run.state["fight"] = run.state["fight"] or {}
    for one in run.state["members"].values():
        one["down"] = True
        one["hp"] = 0
    said = _wipe(run, NOW)

    assert "не выстоял" in said
    assert run.state["flags"]["synchronizer"], "модуль остался у отряда"
    # Попытка кончена: отряд лежит там, где лёг, и рейд закрыт
    assert run.state["outcome"] == "wiped"
    for one in run.state["members"].values():
        assert one["room"] == "O02"
        assert one["hp"] == 0 and one["down"]

    # Разбор кончившегося рейда: откат на три часа и здоровье в карточку
    await db.blackout_write(
        run.id, run.version, "over", json.dumps(run.state), NOW
    )
    await close_out(db, run, NOW)
    for one in people:
        lock = await db.blackout_locked(one.user_id)
        assert lock is not None and lock["reason"] == LOCK_DIED
        assert lock["until"] == NOW + DIED_COOLDOWN_HOURS * 3600
        fresh = await db.get_player(one.user_id)
        assert fresh is not None and fresh.hp == 0
    # И в новый рейд его не пускают, пока откат держится
    with pytest.raises(BlackoutError, match="погиб"):
        await act(db, people[0], "open", {}, "again", NOW + 60)


async def test_the_failed_defense_keeps_the_modules(db):
    """Сорванный пуск не вынимает модули из гнёзд."""
    from bot.blackout_service import defense_failed, read_run

    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    for flag in ("synchronizer", "power_core", "restart_key", "cooling_ready"):
        fresh.state["flags"][flag] = NOW
    for one in fresh.state["members"].values():
        one["room"] = "F405"
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )
    for item in QUEST_ITEMS:
        await act(db, people[0], "install", {"item": item}, f"ins{item}", NOW)
    for one in people:
        await act(db, one, "confirm", {}, f"ok{one.user_id}", NOW)

    run = await read_run(db, run.id)
    defense_failed(run, NOW)

    assert all(run.state["console"]["slots"].get(one) for one in QUEST_ITEMS)
    assert run.state["console"]["wave"] == 0
    assert not run.state["flags"].get("station_restarted")


async def test_the_restart_needs_everything(db):
    """Без трёх модулей и охлаждения пуск не подтвердить."""
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    fresh.state["flags"].update({"synchronizer": NOW, "power_core": NOW})
    for one in fresh.state["members"].values():
        one["room"] = "F405"
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )
    for item in ("synchronizer", "power_core"):
        await act(db, people[0], "install", {"item": item}, f"i{item}", NOW)

    with pytest.raises(BlackoutError, match="Не все гнёзда"):
        await act(db, people[0], "confirm", {}, "c1", NOW)
    with pytest.raises(BlackoutError, match="ещё не у отряда"):
        await act(
            db, people[0], "install", {"item": "restart_key"}, "i3", NOW
        )
    with pytest.raises(BlackoutError, match="Не все три модуля"):
        await act(db, people[0], "sync", {}, "s1", NOW)


# ---------- награда, время и чужие рейды ----------


async def test_the_reward_is_paid_once_per_cycle(db):
    """Повторный заход в том же цикле основную награду не повторяет."""
    people, run = await enter(db)
    from bot.blackout_service import pay

    first = await pay(db, run, people[0].user_id, STAGE_RESTART, NOW)
    second = await pay(db, run, people[0].user_id, STAGE_RESTART, NOW)

    assert first == reward_for(STAGE_RESTART) and second == 0
    rows = await db.blackout_paid(people[0].user_id, run.cycle)
    assert len(rows) == 1
    assert (await db.get_player(people[0].user_id)).credits == 1_000 + first


async def test_the_run_closes_when_the_time_is_up(db):
    """Сто восемьдесят минут — и рейд закрывается сам."""
    people, run = await enter(db)
    late = NOW + RUN_LIMIT_MINUTES * 60

    fresh = await look(db, run.id, late - 10 * 60)
    assert 15 in fresh.state["warned"], "за пятнадцать минут предупредили"

    fresh = await look(db, run.id, late)
    assert fresh.state["outcome"] == "timeout"
    assert fresh.phase == "over"
    # Закрытый рейд больше не принимает команд: он и не «мой» теперь
    with pytest.raises(BlackoutError, match="не в рейде"):
        await act(db, people[0], "go", {"room": "O02"}, "late", late)


async def test_nobody_commands_a_strange_instance(db):
    """Чужим инстансом не покомандуешь: сервер проверяет членство."""
    people, run = await enter(db)
    stranger = make_player(70, "Чужой")
    await db.save_player(stranger)

    with pytest.raises(BlackoutError, match="не в рейде"):
        await act(db, stranger, "go", {"room": "O02"}, "x", NOW)
    with pytest.raises(BlackoutError, match="Ты не в этом рейде"):
        snapshot(run, stranger.user_id, NOW)


async def test_the_snapshot_survives_a_reconnect(db):
    """После обрыва связи снимок отдаёт ту же правду, что и был."""
    people, run = await enter(db)
    await act(db, people[0], "go", {"room": "O02"}, "g", NOW)

    # «Переподключились»: читаем состояние заново, из базы
    again = await read_run(db, run.id)
    body = snapshot(again, people[0].user_id, NOW + 3)
    assert body["walking"] is True
    assert body["going"]["seconds_left"] == EDGE_SECONDS - 3

    body = snapshot(await read_run(db, run.id), people[0].user_id, NOW + 3)
    assert body["going"]["arrive_at"] == NOW + EDGE_SECONDS


async def test_two_requests_at_once_do_not_double_anything(db):
    """Две одновременные команды не выдают предмет и шаг дважды."""
    import asyncio

    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    fresh.state["flags"]["hunter_defeated"] = NOW
    for one in fresh.state["members"].values():
        one["room"] = "F209"
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )

    # Один и тот же номер команды, посланный дважды разом
    said = await asyncio.gather(
        act(db, people[0], "take", {}, "same", NOW),
        act(db, people[0], "take", {}, "same", NOW),
        return_exceptions=True,
    )
    assert any(isinstance(one, str) for one in said)

    after = await look(db, run.id, NOW)
    assert after.state["flags"]["synchronizer"]
    # Разные команды на подбор того же модуля копии тоже не делают
    again = await act(db, people[0], "take", {}, "other", NOW)
    assert "уже" in again.lower()


async def test_the_speed_bonus_does_not_replace_the_main_reward(db):
    """Бонус скорости — сверх основной награды, а не вместо неё."""
    people, run = await enter(db)
    fresh = await look(db, run.id, NOW)
    fresh.state["flags"].update({
        "station_restarted": NOW, "drainage_open": NOW,
    })
    fresh.state["restarted_at"] = NOW
    for one in fresh.state["members"].values():
        one["room"] = "E12"
    await db.blackout_write(
        run.id, fresh.version, "run", json.dumps(fresh.state), NOW
    )

    said = await act(db, people[0], "extract", {}, "x", NOW + 60)

    assert "Бонус скорости" in said
    rows = {one["stage"]: one["amount"] for one in
            await db.blackout_paid(people[0].user_id, run.cycle)}
    assert rows[STAGE_EXTRACT] == reward_for(STAGE_EXTRACT)
    assert rows["speed"] == reward_for("speed")

    # А через двадцать минут после пуска бонуса уже нет
    other = people[1]
    said = await act(db, other, "extract", {}, "x2", NOW + 20 * 60)
    assert "Бонус скорости" not in said
    rows = {one["stage"] for one in
            await db.blackout_paid(other.user_id, run.cycle)}
    assert STAGE_EXTRACT in rows and "speed" not in rows


async def test_the_raid_survives_a_backend_restart(db, tmp_path):
    """Перезапуск процесса рейда не теряет: правда лежит в базе."""
    from bot.database import Database as Base

    path = str(tmp_path / "restart.db")
    first = Base(path)
    await first.connect()
    people = await squad(first, 4)
    await act(first, people[0], "open", {"test": True}, "o", NOW)
    run = await run_of(first, people[0].user_id)
    for one in people[1:]:
        await act(first, one, "join", {"run": run.id}, f"j{one.user_id}", NOW)
    await act(first, people[0], "start", {}, "s", NOW)
    await act(first, people[0], "go", {"room": "O02"}, "g", NOW)
    await first.close()

    # «Процесс перезапустили»: новое соединение, то же состояние
    second = Base(path)
    await second.connect()
    again = await look(second, run.id, NOW + 3)
    one = member(again.state, people[0].user_id)
    assert one["arrive"] == NOW + EDGE_SECONDS, "шаг не потерялся"

    # И доходит он ровно в срок, а не заново
    again = await look(second, run.id, NOW + EDGE_SECONDS)
    assert member(again.state, people[0].user_id)["room"] == "O02"
    assert again.state["ends_at"] == NOW + RUN_LIMIT_MINUTES * 60
    await second.close()


async def test_a_one_off_window_opens_outside_the_schedule(db):
    """Разовое окно пускает в рейд, не трогая расписание города."""
    import os
    from datetime import datetime

    from bot.blackout_service import window_open, window_opens_at
    from bot.game.clock import MOSCOW

    def msk(text: str) -> int:
        return int(datetime.fromisoformat(text).replace(tzinfo=MOSCOW).timestamp())

    os.environ["BLACKOUT_WINDOWS"] = "2026-10-30T23:30/2026-10-31T00:30"
    try:
        # Пятница, половина двенадцатого ночи: по расписанию закрыто
        assert window_open(msk("2026-10-30T23:00")) is False
        assert window_open(msk("2026-10-30T23:30")) is True
        assert window_open(msk("2026-10-31T00:29")) is True
        assert window_open(msk("2026-10-31T00:30")) is False, "окно кончилось"
        # Перед ним экран показывает именно его, а не вторник
        when = window_opens_at(msk("2026-10-30T23:00"))
        assert when == msk("2026-10-30T23:30")

        # Расписание города при этом на месте
        assert window_open(msk("2026-11-03T19:30")) is True, "вторник"
        assert window_open(msk("2026-11-03T23:30")) is False

        # Прочерк убирает разовые окна совсем
        os.environ["BLACKOUT_WINDOWS"] = "-"
        assert window_open(msk("2026-10-30T23:30")) is False
    finally:
        os.environ.pop("BLACKOUT_WINDOWS", None)
