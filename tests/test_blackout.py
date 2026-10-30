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

import json

import pytest

from bot.blackout_service import (
    BlackoutError,
    _wipe,
    act,
    cycle_code,
    look,
    member,
    read_run,
    resolve,
    run_of,
    snapshot,
    state_for,
    window_open,
)
from bot.database import Database
from bot.game.blackout import (
    BOSSES,
    DEFENSE_WAVES,
    EDGE_SECONDS,
    MIN_PARTY,
    QUEST_ITEMS,
    REQUIRED_ORDER,
    RUN_LIMIT_MINUTES,
    SQUAD_SIZE,
    check_map,
    reward_for,
    station,
    walkthrough,
)
from bot.game.blackout import STAGE_EXTRACT, STAGE_RESTART
from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.reference import developed_stats
from bot.models import Player

# Вторник, 12 января 2027 года, двадцать минут восьмого по Москве —
# внутри окна входа. Часы в проверках задаются руками: ждать по-настоящему
# сто пятьдесят минут не станет никто
NOW = 1_800_000_000 + 20 * 60


def make_player(
    user_id: int, nickname: str, class_code: str = "warrior", level: int = 10
) -> Player:
    """Боец, какого город и правда выпускает на станцию.

    Характеристики развитые, по уровню: проверять рейд на «голом»
    бойце — значит мерить не рейд, а отсутствие прокачки.
    """
    stats = developed_stats(FIGHTER_CLASSES[class_code], level)
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code=class_code,
        level=level,
        credits=1_000,
        location="power_station",
        seen_at=NOW,
        **stats.as_dict(),
    )


async def squad(db: Database, size: int = 4) -> list[Player]:
    """Отряд из живых игроков: все записаны в базу."""
    out = []
    for number in range(size):
        one = make_player(number + 1, f"Боец-{number + 1}")
        await db.save_player(one)
        out.append(one)
    return out


async def enter(db: Database, size: int = 4, now: int = NOW):
    """Собрать отряд и вывести его на станцию."""
    people = await squad(db, size)
    await act(db, people[0], "open", {"test": True}, "open", now)
    run = await run_of(db, people[0].user_id)
    assert run is not None
    for one in people[1:]:
        await act(db, one, "join", {"run": run.id}, f"join{one.user_id}", now)
    await act(db, people[0], "start", {}, "start", now)
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


# ---------- отряд и шаг ----------


async def test_the_squad_enters_and_the_clock_starts(db):
    """Отряд выходит на станцию, и с этой минуты идёт лимит рейда."""
    people, run = await enter(db)

    assert run is not None and run.phase == "run"
    assert run.state["party"] == 4
    assert run.state["ends_at"] == NOW + RUN_LIMIT_MINUTES * 60
    assert all(one["room"] == "O01" for one in run.state["members"].values())
    # Меньше трёх на станцию не идут
    alone = make_player(90, "Одиночка")
    await db.save_player(alone)
    await act(db, alone, "open", {"test": True}, "solo", NOW)
    with pytest.raises(BlackoutError, match="от 3"):
        await act(db, alone, "start", {}, "solostart", NOW)


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
    """Увести отряд в соседнюю комнату и дождаться прибытия."""
    await act(db, people[0], "lead", {"room": room}, f"w{room}{at}", at)
    return at + EDGE_SECONDS


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
    пути. После полного поражения отряд откатывается в безопасную
    комнату — и просто идёт заново, как и говорит сценарий.
    """
    for _ in range(40):
        run = await look(db, run_id, at)
        at = await win_here(db, people, run_id, at)
        run = await look(db, run_id, at)
        here = member(run.state, people[0].user_id)["room"]
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
            payload = {"attacks": ["head"], "block": ["chest", "belly"]}
            if need_arena and number == 0:
                payload = {
                    "attacks": [], "block": [], "arena": arena.counter_action,
                }
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


async def play(db, people, run, at: int, log: list | None = None) -> int:
    """Провести отряд по маршруту сценария. Отдаёт час у выхода."""
    run_id = run.id
    for mark in ROUTE:
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
            for one in people:
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
    assert all(member(state, one.user_id)["room"] == "E12" for one in people)

    # Эвакуация и деньги
    said = await act(db, people[0], "extract", {}, "x1", at)
    assert "снаружи" in said
    paid = await db.blackout_paid(people[0].user_id, fresh.cycle)
    stages = {one["stage"] for one in paid}
    assert STAGE_RESTART in stages and STAGE_EXTRACT in stages


async def test_the_raid_is_walkable_by_three_and_by_five(db):
    """Состав 3 и 5: рейд проходится обоими, и масштаб врагов разный."""
    people, run = await enter(db, 3)
    assert run.state["hp_scale"] == pytest.approx(0.78)
    at = await play(db, people, run, NOW)
    assert (await look(db, run.id, at)).state["flags"]["station_restarted"]


async def test_the_five_man_squad_meets_tougher_zombies(db):
    """Пятеро — враги крепче: масштаб считается от состава при входе."""
    from bot.blackout_service import squad_for

    people, run = await enter(db, 5)
    assert run.state["party"] == 5
    assert run.state["hp_scale"] == pytest.approx(1.22)
    assert run.state["dmg_scale"] == pytest.approx(1.08)

    at = await walk(db, people, run, "O02", NOW)
    fresh = await look(db, run.id, at)
    big = fresh.state["fight"]["foes"][0]["max"]
    # Тот же зомби при составе в четверо и в трое — слабее
    assert big > squad_for("O02", 1.0)[0]["max"]
    assert squad_for("O02", 1.0)[0]["max"] > squad_for("O02", 0.78)[0]["max"]

    # Отключение игрока масштаб не двигает: он зафиксирован входом
    await act(db, people[4], "leave", {}, "bye", at)
    fresh = await look(db, run.id, at)
    assert fresh.state["party"] == 5
    assert fresh.state["fight"]["foes"][0]["max"] == big


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


async def test_the_module_survives_a_wipe(db):
    """Полное поражение откатывает попытку, но не отнимает добытое."""
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
    # Отряд отброшен в безопасную комнату и цел
    for one in run.state["members"].values():
        assert one["room"] == "O01"
        assert one["hp"] == one["max"]
        assert not one["down"]


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
