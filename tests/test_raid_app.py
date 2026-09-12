"""Рейд в мини-аппе: тот же сервис, второй пульт.

Правил здесь нет — волны считает `RaidService`. Тесты следят за тем, что
апп видит то же состояние, что и ветка, и что рейд, собранный в аппе, идёт
так же, как собранный командой в группе.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.potions import RAID_PASS, get_potion
from bot.game.raid import BOSS_ID, CELLAR_BOSS, MAX_PARTY
from bot.webapp.server import create_app
from tests.test_duel_flow import FakeBot as DuelBot
from tests.test_raid import make_service
from tests.test_fight_app import headers, make_player
from tests.test_webapp import FakeBot, TOKEN


@pytest.fixture
async def cellar(db):
    """Мини-апп и сервис рейдов на одной базе. У всех по пропуску в рюкзаке."""
    raids = make_service(DuelBot(), db)
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    for user_id, nickname in ((42, "Тайлер"), (43, "Марла"), (44, "Зевака")):
        player = make_player(user_id, nickname)
        player.credits = 500
        # В подвал спускаются из казино: рейд теперь дом на карте
        player.location = "casino"
        await db.save_player(player)
        await db.add_potion(user_id, RAID_PASS)
    app = create_app(FakeBot(), db, config, raids=raids)
    async with TestClient(TestServer(app)) as client:
        yield client, raids, db
    await raids.shutdown()


async def state(client, user_id: int) -> dict:
    response = await client.get("/api/raid", headers=headers(user_id))
    assert response.status == 200
    return await response.json()


async def act(client, user_id: int, **payload) -> tuple[int, dict]:
    response = await client.post("/api/raid", json=payload, headers=headers(user_id))
    return response.status, await response.json()


async def start(client, raids, size: int = 2) -> dict:
    """Собрать рейд в аппе и вывести отряд, не дожидаясь гонга."""
    await act(client, 42, action="open")
    lobby = raids.lobby_of_user(42)
    await act(client, 43, action="join", lobby_id=lobby.id)
    _, body = await act(client, 42, action="go")
    return body


# ---------- сбор ----------


async def test_a_raid_is_gathered_without_leaving_the_app(cellar):
    client, raids, db = cellar

    status, mine = await act(client, 42, action="open")

    assert status == 200
    assert mine["lobby"]["mine"] and mine["lobby"]["total"] == 1
    assert mine["lobby"]["size"] == MAX_PARTY  # мест всегда десять
    assert mine["lobby"]["boss"]["title"] == CELLAR_BOSS.title
    assert mine["raid"] is None
    # пропуск ушёл на входе
    assert (await db.get_player(42)).potion_count(RAID_PASS) == 0
    assert mine["gate"]["spent"] is True

    # чужой сбор виден остальным
    theirs = await state(client, 43)
    assert [row["id"] for row in theirs["lobbies"]] == [mine["lobby"]["id"]]

    status, joined = await act(
        client, 43, action="join", lobby_id=mine["lobby"]["id"]
    )

    assert status == 200
    assert joined["lobby"]["total"] == 2
    _, went = await act(client, 42, action="go")
    assert went["raid"]["wave"] == 1
    assert {row["name"] for row in went["raid"]["party"]} == {"Тайлер", "Марла"}


async def test_the_party_can_be_left(cellar):
    client, raids, _ = cellar
    await act(client, 42, action="open")

    status, after = await act(client, 42, action="leave")

    assert status == 200
    assert after["lobby"] is None
    assert raids.lobby_of_user(42) is None


async def test_the_gate_tells_what_it_will_cost(cellar):
    """Экран знает, есть ли пропуск, открыт ли подвал и не побеждён ли босс."""
    client, raids, db = cellar

    gate = (await state(client, 42))["gate"]
    assert gate["passes"] == 1 and gate["spent"] is False
    assert gate["open"] is True and gate["won"] is False
    assert gate["pass_price"] == get_potion(RAID_PASS).price
    # расписание — сегодняшнее: слоты каждый день свои
    from bot.game.raid import schedule_text

    assert gate["schedule"] == schedule_text()

    # без пропуска и без согласия на покупку внутрь не пускают
    await db.take_potion(42, RAID_PASS)
    status, error = await act(client, 42, action="open")
    assert status == 409 and "Рейд-пасс" in error["error"]

    status, body = await act(client, 42, action="open", buy=True)
    assert status == 200 and body["lobby"] is not None
    assert (await db.get_player(42)).credits == 500 - get_potion(RAID_PASS).price


async def test_the_lobby_carries_the_countdown(cellar):
    """Сколько осталось ждать — приезжает с сервера, а не считается на глаз."""
    client, raids, _ = cellar

    _, mine = await act(client, 42, action="open")

    lobby = mine["lobby"]
    # Срок берётся из настроек сервера — тех же, по которым тикает таймер
    assert lobby["timeout"] > 0
    assert 0 < lobby["seconds_left"] <= lobby["timeout"]
    assert lobby["can_start"] is True  # выйти можно и одному

    raids.lobby_of_user(42).opened_at -= lobby["timeout"] - 5
    later = await state(client, 42)
    assert later["lobby"]["seconds_left"] <= 5


async def test_the_opener_goes_without_waiting(cellar):
    client, raids, _ = cellar
    await act(client, 42, action="open")
    lobby = raids.lobby_of_user(42)
    _, joined = await act(client, 43, action="join", lobby_id=lobby.id)
    assert joined["raid"] is None  # гонга ещё не было

    status, body = await act(client, 42, action="go")

    assert status == 200
    assert body["raid"] is not None and len(body["raid"]["party"]) == 2


async def test_only_the_opener_goes_without_waiting(cellar):
    client, raids, _ = cellar
    await act(client, 42, action="open")
    lobby = raids.lobby_of_user(42)
    await act(client, 43, action="join", lobby_id=lobby.id)

    status, error = await act(client, 43, action="go")

    assert status == 409
    assert "кто его собрал" in error["error"]
    assert raids.raid_of_user(42) is None


# ---------- сам рейд ----------


async def test_a_turn_is_sent_as_one_move(cellar):
    """Кнопка «Вперёд!» шлёт удар и блок разом — как в бою."""
    client, raids, _ = cellar
    await start(client, raids)
    before = raids.raid_of_user(42).enemy.hp

    status, body = await act(client, 42, action="turn", attack="head", block="belt")

    assert status == 200
    raid = body["raid"]
    assert raid["acted"] is True
    assert raid["boss"]["hp"] <= before
    # соперник ещё думает: волна не закрыта
    assert [row["acted"] for row in raid["party"]] == [True, False]


async def test_half_a_turn_is_refused(cellar):
    client, raids, _ = cellar
    await start(client, raids)

    status, body = await act(client, 42, action="turn", attack="head")

    assert status == 409
    assert "и удар, и блок" in body["error"]
    assert 42 not in raids.raid_of_user(42).acted


async def test_the_log_says_who_hit_where(cellar):
    client, raids, _ = cellar
    await start(client, raids)

    await act(client, 42, action="turn", attack="head", block="belt")
    turn = (await state(client, 42))["raid"]["log"][0]

    assert turn["lines"], "судья промолчал"
    mine = next(s for s in turn["strikes"] if s["attacker_id"] == 42)
    assert (mine["zone"], mine["zone_title"]) == ("head", "Голова")
    assert any(s["attacker_id"] == BOSS_ID for s in turn["strikes"])


async def test_a_stranger_sees_nothing_and_cannot_press(cellar):
    client, raids, _ = cellar
    await start(client, raids)

    body = await state(client, 44)
    assert body["raid"] is None

    status, error = await act(client, 44, action="turn", attack="head", block="belt")
    assert status == 409
    assert "не в рейде" in error["error"]


async def test_the_result_holds_the_screen_until_it_is_closed(cellar):
    client, raids, _ = cellar
    await start(client, raids)
    session = raids.raid_of_user(42)
    session.enemy.hp = 1

    await act(client, 42, action="turn", attack="head", block="belt")

    raid = (await state(client, 42))["raid"]
    assert raid["finished"] is True
    summary = "\n".join(raid["summary"])
    assert "Босс повержен" in summary and "Кто сколько набил" in summary
    assert "<b>" not in summary

    status, after = await act(client, 42, action="done")

    assert status == 200 and after["raid"] is None


async def test_the_history_of_raids_is_open_to_read(cellar):
    client, raids, db = cellar
    await start(client, raids)
    session = raids.raid_of_user(42)
    session.enemy.hp = 1
    await act(client, 42, action="turn", attack="head", block="belt")

    response = await client.get("/api/raids?user_id=42", headers=headers(43))
    body = await response.json()

    assert response.status == 200
    row = body["raids"][0]
    assert row["boss"] == CELLAR_BOSS.title
    # В списке боёв важно не «босс повержен», а что вышло у тебя
    assert row["result"] == "win" and row["result_title"] == "Победа"
    assert row["caption"] == f"Победа (с Марла) — рейд против {CELLAR_BOSS.whom}"
    assert row["verdict"] == "Босс повержен"
    assert row["damage"] > 0 and row["waves"] == 1


async def test_a_raid_lands_in_the_list_of_fights(cellar):
    """Рейд стоит в статистике рядом с дуэлями и читается так же."""
    client, raids, db = cellar
    await start(client, raids)
    raids.raid_of_user(42).enemy.hp = 1
    await act(client, 42, action="turn", attack="head", block="belt")

    response = await client.get("/api/history?user_id=42", headers=headers(42))
    body = await response.json()

    row = body["days"][0]["fights"][0]
    assert row["kind"] == "raid"
    assert row["caption"] == f"Победа (с Марла) — рейд против {CELLAR_BOSS.whom}"
    assert row["waves"] == 1 and row["damage"] > 0
    # В списке рейд стоит вместе с дуэлями, а в счёте — отдельно от них
    assert body["total"] == 1
    assert body["raids"] == {"wins": 1, "total": 1}
    assert body["counts"] == {"win": 0, "loss": 0, "draw": 0}


async def test_the_boss_card_comes_with_the_section(cellar):
    """Кнопка «i» рисуется по тому, что пришло вместе с разделом."""
    client, raids, _ = cellar

    idle = (await state(client, 42))["boss"]

    assert idle["live"] is False
    assert idle["title"] == CELLAR_BOSS.title
    assert idle["level"] > 0 and idle["max_hp"] > 0
    assert idle["weapon"] == "Кувалда"
    assert len(idle["kit"]) == 9  # девять слотов, включая вторую руку
    assert idle["combat"]["resist"] > 0

    await start(client, raids)
    live = (await state(client, 42))["boss"]

    assert live["live"] is True
    assert live["level"] == raids.raid_of_user(42).enemy.level


async def test_the_boss_stands_in_slots_like_a_fighter(cellar):
    """Кукла босса собирается тем же payload, что и карточка бойца."""
    client, _, _ = cellar

    boss = (await state(client, 42))["boss"]

    assert boss["avatar"]["url"].endswith("bosses/cellar_boss.png")
    left = boss["slots"]["left"]
    right = boss["slots"]["right"]
    assert [row["slot"] for row in left] == ["head", "weapon", "jacket", "belt"]
    assert [row["slot"] for row in right] == ["gloves", "offhand", "pants", "boots"]
    # у босса заняты все клетки, но форма слота та же, что у пустого
    assert all(row["item"] for row in left + right)
    assert all(row["placeholder_image"] for row in left + right)
    assert next(row for row in left if row["slot"] == "weapon")["item"]["title"] == (
        "Кувалда"
    )


# ---------- отсчёт на карте ----------


async def test_the_map_brings_the_raid_countdown(cellar):
    """Плашку под вывеской казино считает сервер: ему видна и база."""
    client, raids, db = cellar

    response = await client.get("/api/map", headers=headers(42))
    body = await response.json()

    assert response.status == 200
    # подвал в этих тестах открыт круглосуточно — значит, идёт
    assert body["raid"]["state"] == "open"
    assert body["raid"]["text"] == "Рейд закончится через"
    assert body["raid"]["seconds_left"] > 0


async def test_the_plate_lights_up_an_hour_before_and_goes_out_after(db):
    """Синяя за час до окна, жёлтая в окне, зелёная тому, кто своё взял."""
    from datetime import date, datetime

    from bot.game.raid import MOSCOW, RAID_SOON, WINDOW_HOURS, slots_on
    from bot.webapp.raid import plate_payload

    raids = make_service(DuelBot(), db, raid_any_time=False)
    player = make_player(42, "Тайлер")
    await db.save_player(player)

    day = date(2026, 9, 8)
    opens = slots_on(day)[0]
    start = int(datetime(2026, 9, 8, opens, tzinfo=MOSCOW).timestamp())

    # задолго до окна карта о рейде молчит
    far = await plate_payload(player, raids, start - RAID_SOON - 60)
    assert far["state"] == ""

    soon = await plate_payload(player, raids, start - 34 * 60)
    assert soon["state"] == "soon" and soon["text"] == "Рейд начнётся через"
    assert soon["seconds_left"] == 34 * 60

    going = await plate_payload(player, raids, start + 60)
    assert going["state"] == "open" and going["text"] == "Рейд закончится через"
    assert going["seconds_left"] == WINDOW_HOURS * 3600 - 60

    # взял своё — вместо часов «Рейд завершён»
    await db.close_raid_window(player.user_id, start)
    done = await plate_payload(player, raids, start + 60)
    assert done == {"state": "done", "text": "Рейд завершён", "seconds_left": 0}


async def test_the_raid_has_no_analyst(cellar):
    """В рейде аналитика нет: разбирают живого соперника, а не босса."""
    client, raids, db = cellar
    await act(client, 42, action="open")
    body = await state(client, 42)

    assert "scout" not in body
    assert "scout" not in (body.get("lobby") or {})
