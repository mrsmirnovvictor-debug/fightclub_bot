"""Общежитие: комната студенту, пока он учится.

Проверяем пять вещей и в таком порядке важности:

1. **Комнату даёт учёба.** Не записан на курс — не дадут; записан —
   дадут бесплатно и без срока в сутках.
2. **Права не считаются.** Автошкола не училище: билет сдают за один
   заход, а комнату дают тому, кто ходит на занятия неделями.
3. **Вещи не пропадают.** Учёба кончилась — комната заперта, а всё из
   неё ждёт хозяина у администрации и отдаётся разом.
4. **Четыре места и сейф.** Пятую вещь в комнату не положить.
5. **Место.** Комнату берут и сдают в общежитии: спрятанная кнопка
   обходится запросом мимо интерфейса.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.dorm_service import (
    DormError,
    check_in,
    collect,
    deposit,
    stash,
    sweep,
    take,
    withdraw,
)
from bot.game.classes import Stats
from bot.game.dorm import DORM, ROOM_SLOTS, room_number, room_title
from bot.game.driving import School
from bot.game.study import get_course
from bot.models import Player
from bot.webapp.dorm import build_dorm
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

NOW = 1_800_000_000
CLUB = "fight_club"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


def make_player(
    user_id: int = 42, nickname: str = "Студент", where: str = DORM,
    credits: int = 1_000,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=where, seen_at=NOW,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def student(
    db: Database, user_id: int = 42, course: str = "it", where: str = DORM
) -> Player:
    """Боец, записанный на курс. Саму запись проверяет учёба, не мы."""
    player = make_player(user_id, where=where)
    player.study_code = course
    player.study_since = NOW
    await db.save_player(player)
    fresh = await db.get_player(user_id)
    assert fresh is not None and fresh.studies
    return fresh


async def thing(db: Database, player: Player, code: str = "bandana") -> int:
    """Положить бойцу вещь в рюкзак и вернуть её номер."""
    owned = await db.add_gear(player.user_id, code)
    player.gear = await db.list_gear(player.user_id)
    return owned.id


# ---------- номера ----------


def test_the_room_number_is_the_smallest_free_one():
    """Номер — имя, а не ключ: берём наименьший свободный."""
    assert room_number([]) == 1
    assert room_number([1, 2, 3]) == 4
    # Второй отучился и выехал — его номер снова свободен
    assert room_number([1, 3, 4]) == 2
    assert room_title(7) == "комната №7"


def test_the_room_holds_four_things():
    """Четыре места: общежитие не квартира, и шкаф в нём меньше."""
    from bot.game.estate import FLAT_SLOTS

    assert ROOM_SLOTS == 4
    assert ROOM_SLOTS < min(FLAT_SLOTS.values()), "комната больше однушки"


# ---------- комнату даёт учёба ----------


async def test_a_student_gets_a_room_for_free(db):
    """Записан на курс — комната твоя, бесплатно и на всё время учёбы."""
    player = await student(db)

    said = await check_in(db, player, NOW)

    assert "комната №1" in said and "платить не надо" in said.lower()
    room = await db.dorm_room(42)
    assert room is not None and room.number == 1
    assert room.course == "it" and not room.closed
    assert room.vault == 0
    # Денег не взяли ни копейки
    assert (await db.get_player(42)).credits == 1_000


async def test_a_loafer_gets_nothing(db):
    """Не учишься — не студент: комнату не дают."""
    player = make_player()
    await db.save_player(player)

    with pytest.raises(DormError, match="Комнату дают студенту"):
        await check_in(db, player, NOW)
    assert await db.dorm_room(42) is None


async def test_a_driving_licence_is_not_a_course(db):
    """Права не считаются: автошкола не училище.

    Удостоверение сдают за один заход по билету, и жить при школе
    незачем. Комната — тому, кто ходит на занятия неделями.
    """
    player = make_player()
    await db.save_player(player)
    # Права на руках, курса нет
    await db.set_school(42, School(passed_at=NOW, licence_at=NOW))
    licensed = await db.get_player(42)
    assert licensed.school.has_licence

    with pytest.raises(DormError, match="Права не считаются"):
        await check_in(db, licensed, NOW)


async def test_two_rooms_are_not_given(db):
    """Комната одна: вторую не дают, пока держишь первую."""
    player = await student(db)
    await check_in(db, player, NOW)

    with pytest.raises(DormError, match="уже есть"):
        await check_in(db, player, NOW)


async def test_the_room_is_taken_at_the_dorm(db):
    """Комнату берут в общежитии, а не из клуба."""
    player = await student(db, where=CLUB)

    with pytest.raises(DormError, match="в общежитии"):
        await check_in(db, player, NOW)


# ---------- сейф и полки ----------


async def test_the_room_keeps_four_things_and_the_money(db):
    """Полки на четыре вещи и сейф: как в квартире, только меньше."""
    player = await student(db)
    await check_in(db, player, NOW)
    ids = [await thing(db, player, "bandana") for _ in range(ROOM_SLOTS + 1)]

    for item_id in ids[:ROOM_SLOTS]:
        await stash(db, player, item_id, NOW)

    assert len(await db.dorm_items(42)) == ROOM_SLOTS
    with pytest.raises(DormError, match=f"{ROOM_SLOTS} места"):
        await stash(db, player, ids[ROOM_SLOTS], NOW)

    # Сейф: внесли и сняли
    assert "В сейфе 300" in await deposit(db, player, 300, NOW)
    assert (await db.get_player(42)).credits == 700
    assert "100 💰 наличными" in await withdraw(db, player, 100, NOW)
    assert (await db.dorm_room(42)).vault == 200
    with pytest.raises(DormError, match="столько не взять"):
        await withdraw(db, player, 500, NOW)

    # Вещь возвращается в рюкзак
    kept = await db.dorm_items(42)
    said = await take(db, player, kept[0].id, NOW)
    assert "в рюкзаке" in said
    assert len(await db.dorm_items(42)) == ROOM_SLOTS - 1


async def test_the_worn_thing_is_not_stashed(db):
    """Надетое в комнату не кладут: сперва снимают в инвентаре."""
    player = await student(db)
    await check_in(db, player, NOW)
    item_id = await thing(db, player, "bandana")
    from bot.inventory_service import equip

    await equip(db, player, item_id)

    with pytest.raises(DormError, match="Сперва сними"):
        await stash(db, player, item_id, NOW)


# ---------- учёба кончилась ----------


async def test_the_room_locks_when_the_study_ends(db):
    """Отучился — комната заперта, и войти в неё больше нельзя."""
    player = await student(db)
    await check_in(db, player, NOW)
    item_id = await thing(db, player)
    await stash(db, player, item_id, NOW)
    await deposit(db, player, 500, NOW)

    # Сдал экзамен: курса больше нет
    player.study_code = ""
    await db.save_player(player)
    room = await sweep(db, player, NOW + 60)

    assert room is not None and room.closed
    assert room.closed_at == NOW + 60
    # Запертая комната не пускает в себя никого
    for action in (
        lambda: stash(db, player, item_id, NOW + 60),
        lambda: take(db, player, 1, NOW + 60),
        lambda: deposit(db, player, 10, NOW + 60),
        lambda: withdraw(db, player, 10, NOW + 60),
    ):
        with pytest.raises(DormError, match="заперта"):
            await action()
    # А вещи и деньги целы: они ждут хозяина
    assert len(await db.dorm_items(42)) == 1
    assert (await db.dorm_room(42)).vault == 500


async def test_the_administration_gives_everything_back_at_once(db):
    """Своё забирают у администрации разом: вещи в рюкзак, деньги наличными."""
    player = await student(db)
    await check_in(db, player, NOW)
    await stash(db, player, await thing(db, player, "bandana"), NOW)
    await stash(db, player, await thing(db, player, "wraps"), NOW)
    await deposit(db, player, 400, NOW)
    player.study_code = ""
    await db.save_player(player)
    before = (await db.get_player(42)).credits

    said = await collect(db, player, NOW + 60)

    assert "сдана" in said and "Вещей забрано: 2" in said
    assert "400 💰 наличными" in said
    assert await db.dorm_room(42) is None
    assert await db.dorm_items(42) == []
    assert (await db.get_player(42)).credits == before + 400
    assert len(await db.list_gear(42)) == 2


async def test_a_live_room_is_not_collected(db):
    """Пока учишься, забирать у администрации нечего — комната при тебе."""
    player = await student(db)
    await check_in(db, player, NOW)

    with pytest.raises(DormError, match="при тебе"):
        await collect(db, player, NOW)


async def test_a_new_room_waits_for_the_old_one_to_be_emptied(db):
    """Новую комнату дают, только когда забрано из прежней."""
    player = await student(db)
    await check_in(db, player, NOW)
    await stash(db, player, await thing(db, player), NOW)
    player.study_code = ""
    await db.save_player(player)
    await sweep(db, player, NOW + 60)

    # Записался снова — но прежняя комната ещё не сдана
    player.study_code = "banking"
    await db.save_player(player)
    with pytest.raises(DormError, match="забери из неё своё"):
        await check_in(db, player, NOW + 120)

    await collect(db, player, NOW + 120)
    said = await check_in(db, player, NOW + 180)
    assert "комната" in said
    assert (await db.dorm_room(42)).course == "banking"


async def test_nothing_is_owed_to_a_fighter_without_a_room(db):
    """Администрации нечего отдавать тому, у кого комнаты и не было."""
    player = make_player()
    await db.save_player(player)

    with pytest.raises(DormError, match="нечего тебе отдать"):
        await collect(db, player, NOW)


# ---------- экран ----------


async def test_the_screen_tells_a_student_what_he_gets(db):
    """Студенту экран обещает комнату, бездельнику — рассказывает правило."""
    player = await student(db)

    body = await build_dorm(db, player, NOW)

    assert body["studies"] is True and body["can_take"] is True
    assert body["room"]["mine"] is False
    assert body["slots"] == ROOM_SLOTS
    assert "бесплатно" in body["note"] and get_course("it").title in body["note"]

    idle = make_player(43, "Прохожий")
    await db.save_player(idle)
    body = await build_dorm(db, idle, NOW)
    assert body["can_take"] is False
    assert "Права не считаются" in body["note"]


async def test_the_screen_shows_the_room_and_then_the_lock(db):
    """Экран комнаты: полки и сейф, а после учёбы — одна кнопка выезда."""
    player = await student(db)
    await check_in(db, player, NOW)
    await stash(db, player, await thing(db, player), NOW)

    body = await build_dorm(db, player, NOW)
    room = body["room"]
    assert room["mine"] is True and room["closed"] is False
    assert room["title"] == "комната №1" and room["course_title"] == (
        get_course("it").title
    )
    assert room["free"] == ROOM_SLOTS - 1 and len(room["items"]) == 1
    assert "живёшь, пока учишься" in body["note"]

    player.study_code = ""
    await db.save_player(player)
    body = await build_dorm(db, player, NOW + 60)
    assert body["room"]["closed"] is True
    assert "забирают у администрации" in body["note"]


async def test_the_dorm_opens_only_inside_it(db, client):
    """Экран общежития открывают изнутри: дверь спрашивает место."""
    await student(db)

    answer = await client.get("/api/dorm", headers=headers())
    assert answer.status == 200
    assert (await answer.json())["can_take"] is True

    answer = await client.post(
        "/api/dorm", json={"action": "room"}, headers=headers()
    )
    assert answer.status == 200
    assert "комната №1" in (await answer.json())["said"]

    away = await db.get_player(42)
    away.location = CLUB
    await db.save_player(away)
    assert (await client.get("/api/dorm", headers=headers())).status == 409
