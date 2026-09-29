"""Тренажёрный зал: расписание, абонемент, тренировка и улучшение.

Порядок проверок — по важности того, что можно сломать:

1. **Расписание** — выводится, а не хранится, и потому обязано быть
   одинаковым у всех и после перезапуска.
2. **Двери** — абонемент, время и место. Каждая заперта на сервере, а не
   прятанием кнопки.
3. **Прогресс** — очки копятся по одному, тратятся на улучшение, и пятью
   улучшениями всё кончается.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.game.classes import Stat, Stats
from bot.game.gym import (
    MAX_UPGRADES,
    SLOT_HOURS,
    SLOT_SECONDS,
    TRAINING_MINUTES,
    TRAINING_SECONDS,
    UPGRADE_STEPS,
    day_schedule,
    get_pass,
    moscow_day,
    next_slot,
    schedule_from,
    slot_now,
    slot_start,
    total_for,
    training_for,
    week_schedule,
)
from bot.game.locations import Service, get_location, where_to
from bot.gym_service import GymError, buy_pass, join, leave, progress_of, settle
from bot.gym_service import upgrade, visit_of
from bot.models import Player
from bot.webapp.gym import build_gym
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

GYM = "strength_gym"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(location: str = GYM, credits: int = 10_000) -> Player:
    return Player(
        user_id=42, nickname="Тайлер", class_code="warrior", level=8,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


def at_slot(when: int = 0, shift: int = 0) -> int:
    """Момент внутри слота: `when` — какой по счёту слот сегодня."""
    from datetime import date

    day = date(2026, 10, 5)  # понедельник
    return slot_start(day, SLOT_HOURS[when]) + shift


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- расписание ----------


def test_the_gym_is_the_house_where_fighters_train():
    place = where_to(Service.TRAIN)

    assert place is not None and place.code == GYM
    assert get_location(GYM).allows(Service.TRAIN)
    assert not get_location(GYM).soon


def test_six_slots_a_day_two_hours_each():
    assert SLOT_HOURS == (8, 10, 12, 14, 16, 18)
    assert SLOT_SECONDS == 2 * 60 * 60
    assert TRAINING_MINUTES == 15

    from datetime import date

    slots = day_schedule(date(2026, 10, 5))
    assert [slot.hour for slot in slots] == list(SLOT_HOURS)
    assert [slot.clock for slot in slots][0] == "08:00–10:00"
    # Слоты идут подряд и не налезают друг на друга
    for first, second in zip(slots, slots[1:]):
        assert first.ends == second.starts


def test_the_gym_trains_three_stats_and_not_endurance():
    from bot.game.gym import GYM_STATS

    assert GYM_STATS == (Stat.STRENGTH, Stat.AGILITY, Stat.INTUITION)
    assert training_for(Stat.ENDURANCE) is None
    assert training_for(Stat.STRENGTH).title == "Силовая тренировка"
    assert training_for(Stat.AGILITY).title == "Кардио-тренинг"
    assert training_for(Stat.INTUITION).title == "Кросс-фит"


def test_the_schedule_is_the_same_every_time_it_is_asked_for():
    """Оно выводится, а не хранится: у всех одно и то же и после перезапуска."""
    from datetime import date

    day = date(2026, 10, 7)
    first = [(slot.hour, slot.training.code) for slot in day_schedule(day)]
    again = [(slot.hour, slot.training.code) for slot in day_schedule(day)]

    assert first == again


def test_every_day_holds_two_slots_of_each_training():
    """Поровну — чтобы вечерами всю неделю не выпадало одно и то же."""
    from collections import Counter
    from datetime import date, timedelta

    start = date(2026, 10, 5)
    for shift in range(14):
        day = start + timedelta(days=shift)
        kinds = Counter(slot.training.code for slot in day_schedule(day))
        assert kinds == {"power": 2, "cardio": 2, "crossfit": 2}, day


def test_a_new_week_brings_a_new_draw():
    from datetime import date

    this_week = week_schedule(date(2026, 10, 5))
    next_week = week_schedule(date(2026, 10, 12))

    first = [slot.training.code for slots in this_week.values() for slot in slots]
    second = [slot.training.code for slots in next_week.values() for slot in slots]
    assert first != second, "расписание не должно повторяться неделя в неделю"


def test_the_week_is_the_same_for_every_day_inside_it():
    from datetime import date

    monday = week_schedule(date(2026, 10, 5))
    sunday = week_schedule(date(2026, 10, 11))

    assert monday == sunday


def test_the_board_shows_a_week_ahead_from_today():
    now = at_slot(0)

    slots = schedule_from(now)

    assert len(slots) == 7 * len(SLOT_HOURS)
    assert slots[0].day == moscow_day(now)


def test_the_open_slot_is_the_one_the_clock_is_inside():
    assert slot_now(at_slot(0, shift=60)).hour == 8
    assert slot_now(at_slot(2, shift=60)).hour == 12
    # До восьми утра и после восьми вечера зал закрыт
    assert slot_now(at_slot(0, shift=-60)) is None
    assert slot_now(at_slot(5, shift=SLOT_SECONDS + 60)) is None


def test_a_slot_stops_taking_joiners_fifteen_minutes_before_it_ends():
    slot = slot_now(at_slot(1, shift=60))

    assert slot.takes_joiners(slot.ends - TRAINING_SECONDS - 1)
    assert not slot.takes_joiners(slot.ends - TRAINING_SECONDS)
    assert not slot.takes_joiners(slot.ends)


def test_the_next_slot_is_named_when_the_gym_is_closed():
    coming = next_slot(at_slot(0, shift=-60))

    assert coming is not None and coming.hour == 8


# ---------- абонемент ----------


def test_the_gym_sells_three_tickets():
    assert [(one.code, one.days, one.price) for one in
            (get_pass("month"), get_pass("half"), get_pass("year"))] == [
        ("month", 30, 500), ("half", 182, 2500), ("year", 365, 4000)
    ]


async def test_a_ticket_is_paid_for_and_starts_the_clock(db):
    player = await stand(db, make_player(credits=600))
    now = at_slot(0)

    ticket = await buy_pass(db, player, "month", now)

    assert ticket.price == 500 and player.credits == 100
    until = await db.gym_pass_of(player.user_id)
    assert until == now + 30 * 24 * 60 * 60


async def test_a_second_ticket_stacks_on_top_of_the_first(db):
    player = await stand(db, make_player(credits=10_000))
    now = at_slot(0)
    await buy_pass(db, player, "month", now)

    await buy_pass(db, player, "month", now + 24 * 60 * 60)

    until = await db.gym_pass_of(player.user_id)
    assert until == now + 60 * 24 * 60 * 60, "месяц лёг поверх месяца"


async def test_an_empty_purse_buys_no_ticket(db):
    player = await stand(db, make_player(credits=499))

    with pytest.raises(GymError, match="Не хватает кредитов"):
        await buy_pass(db, player, "month")

    assert player.credits == 499
    assert await db.gym_pass_of(player.user_id) == 0


async def test_an_unknown_ticket_is_refused(db):
    player = await stand(db, make_player())

    with pytest.raises(GymError, match="не продают"):
        await buy_pass(db, player, "вечность")


# ---------- тренировка ----------


async def with_pass(db: Database, now: int) -> Player:
    player = await stand(db, make_player())
    await buy_pass(db, player, "year", now)
    return player


async def test_without_a_ticket_nobody_is_let_in(db):
    player = await stand(db, make_player())

    with pytest.raises(GymError, match="абонемент"):
        await join(db, player, at_slot(0, shift=60))


async def test_training_outside_the_gym_is_refused(db):
    now = at_slot(0, shift=60)
    player = await stand(db, make_player(location="hospital"))
    await db.set_gym_pass(player.user_id, now + 10_000)

    with pytest.raises(GymError, match="Здесь этого не делают"):
        await join(db, player, now)


async def test_there_is_nothing_to_join_when_the_gym_is_closed(db):
    now = at_slot(0, shift=-60)
    player = await with_pass(db, now)

    with pytest.raises(GymError, match="занятий нет"):
        await join(db, player, now)


async def test_joining_too_late_is_refused(db):
    now = at_slot(0)
    player = await with_pass(db, now)
    late = slot_now(now + 60).ends - TRAINING_SECONDS + 1

    with pytest.raises(GymError, match="меньше пятнадцати минут"):
        await join(db, player, late)


async def test_a_training_runs_fifteen_minutes_and_gives_a_point(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)

    visit = await join(db, player, now)

    assert visit.seconds_left(now) == TRAINING_SECONDS
    assert visit.stat == slot_now(now).training.stat
    # Раньше срока очка нет
    assert await settle(db, player, now + TRAINING_SECONDS - 1) == ""
    rows = await progress_of(db, player)
    assert rows[visit.stat].points == 0

    said = await settle(db, player, now + TRAINING_SECONDS)

    assert "отработана" in said
    rows = await progress_of(db, player)
    assert rows[visit.stat].points == 1
    assert await visit_of(db, player) is None


async def test_the_point_is_counted_once(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await join(db, player, now)
    over = now + TRAINING_SECONDS

    await settle(db, player, over)
    assert await settle(db, player, over) == "", "второй раз очка не дают"

    rows = await progress_of(db, player)
    assert sum(row.points for row in rows.values()) == 1


async def test_only_one_training_per_slot(db):
    """Иначе в двухчасовой слот влезает семь подходов."""
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await join(db, player, now)
    await settle(db, player, now + TRAINING_SECONDS)

    with pytest.raises(GymError, match="уже отработал"):
        await join(db, player, now + TRAINING_SECONDS + 60)


async def test_the_next_slot_is_a_fresh_training(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await join(db, player, now)
    await settle(db, player, now + TRAINING_SECONDS)

    later = at_slot(1, shift=60)
    visit = await join(db, player, later)

    assert visit.slot != ""
    await settle(db, player, later + TRAINING_SECONDS)
    rows = await progress_of(db, player)
    assert sum(row.points for row in rows.values()) == 2


async def test_two_trainings_at_once_are_refused(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await join(db, player, now)

    with pytest.raises(GymError, match="уже на тренировке"):
        await join(db, player, now + 60)


async def test_leaving_the_gym_loses_the_training_and_the_slot(db):
    """Пятнадцать минут стоят в зале, а не где придётся."""
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await join(db, player, now)
    player.location = "hospital"
    await db.save_player(player)

    said = await settle(db, player, now + TRAINING_SECONDS)

    assert "не зачтена" in said
    rows = await progress_of(db, player)
    assert sum(row.points for row in rows.values()) == 0
    # И слот потрачен: вернуться и доработать его нельзя
    player.location = GYM
    await db.save_player(player)
    with pytest.raises(GymError, match="меньше пятнадцати минут|уже отработал"):
        await join(db, player, slot_now(now).ends - 60)


async def test_walking_out_early_costs_the_slot(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await join(db, player, now)

    await leave(db, player)

    assert await visit_of(db, player) is None
    rows = await progress_of(db, player)
    assert sum(row.points for row in rows.values()) == 0


# ---------- улучшение ----------


async def train(db: Database, player: Player, stat: Stat, times: int) -> None:
    """Набить очки напрямую: сами тренировки проверены выше."""
    for _ in range(times):
        await db.add_gym_point(player.user_id, stat.value)


def test_the_ladder_is_three_six_twelve_twentyfour_fortyeight():
    assert UPGRADE_STEPS == (3, 6, 12, 24, 48)
    assert MAX_UPGRADES == 5
    assert total_for(MAX_UPGRADES) == 93


async def test_an_upgrade_spends_its_price_and_raises_the_stat(db):
    player = await stand(db, make_player())
    await train(db, player, Stat.STRENGTH, 3)

    grown = await upgrade(db, player, Stat.STRENGTH)

    assert grown.value == 11 and grown.ups == 1 and grown.spent == 3
    assert (await db.get_player(42)).strength == 11
    rows = await progress_of(db, player)
    assert rows[Stat.STRENGTH].points == 0, "очки потрачены"
    assert rows[Stat.STRENGTH].price == 6, "второе улучшение дороже"


async def test_an_upgrade_without_enough_points_is_refused(db):
    player = await stand(db, make_player())
    await train(db, player, Stat.AGILITY, 2)

    with pytest.raises(GymError, match="Не хватает тренировок"):
        await upgrade(db, player, Stat.AGILITY)

    assert (await db.get_player(42)).agility == 10


async def test_the_ladder_gets_steeper_with_every_step(db):
    player = await stand(db, make_player())
    for step, price in enumerate(UPGRADE_STEPS, start=1):
        await train(db, player, Stat.INTUITION, price)
        grown = await upgrade(db, player, Stat.INTUITION)
        assert grown.ups == step and grown.spent == price

    assert (await db.get_player(42)).intuition == 10 + MAX_UPGRADES


async def test_the_sixth_upgrade_is_refused(db):
    player = await stand(db, make_player())
    for price in UPGRADE_STEPS:
        await train(db, player, Stat.STRENGTH, price)
        await upgrade(db, player, Stat.STRENGTH)
    await train(db, player, Stat.STRENGTH, 100)

    with pytest.raises(GymError, match="на потолке"):
        await upgrade(db, player, Stat.STRENGTH)

    assert (await db.get_player(42)).strength == 10 + MAX_UPGRADES


async def test_endurance_is_not_trained_here(db):
    player = await stand(db, make_player())

    with pytest.raises(GymError, match="не качают"):
        await upgrade(db, player, Stat.ENDURANCE)


async def test_points_of_one_stat_do_not_pay_for_another(db):
    player = await stand(db, make_player())
    await train(db, player, Stat.STRENGTH, 3)

    with pytest.raises(GymError, match="Не хватает тренировок"):
        await upgrade(db, player, Stat.AGILITY)


# ---------- табло ----------


async def test_the_board_marks_the_open_slot_and_the_ones_around_it(db):
    now = at_slot(2, shift=60)
    player = await with_pass(db, now)

    body = build_gym(player, await db.gym_pass_of(42), await progress_of(db, player),
                     None, set(), now)

    today = body["schedule"][0]
    assert today["today"] and today["title"] == "Сегодня"
    states = [slot["state"] for slot in today["slots"]]
    assert states[:2] == ["past", "past"], "утренние прошли"
    assert states[2] == "open", "идущий пускает"
    assert states[3:] == ["ahead"] * 3
    assert body["now"]["clock"] == "12:00–14:00"


async def test_the_board_marks_a_slot_already_worked(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    visit = await join(db, player, now)

    body = build_gym(player, await db.gym_pass_of(42), await progress_of(db, player),
                     await visit_of(db, player), {visit.slot}, now)

    assert body["schedule"][0]["slots"][0]["state"] == "done"
    assert body["visit"]["seconds_left"] == TRAINING_SECONDS
    assert not body["visit"]["over"]


async def test_the_board_says_when_joining_is_too_late(db):
    now = at_slot(0)
    player = await with_pass(db, now)
    late = slot_now(now + 60).ends - 60

    body = build_gym(player, await db.gym_pass_of(42), await progress_of(db, player),
                     None, set(), late)

    assert body["now"]["state"] == "late"


# ---------- дверь ----------


async def test_the_gym_screen_is_closed_outside_the_gym(client, db):
    await stand(db, make_player(location="hospital"))

    response = await client.get("/api/gym", headers=headers())

    assert response.status == 409
    assert "Тренажёрный зал" in (await response.json())["error"]


async def test_the_whole_gym_goes_through_the_page(client, db):
    await stand(db, make_player(credits=600))

    bought = await client.post(
        "/api/gym", json={"action": "pass", "code": "month"}, headers=headers()
    )
    body = await bought.json()

    assert bought.status == 200
    assert "Абонемент" in body["said"] and body["pass"]["active"]
    # Счёт зала и карточка говорят одно и то же
    assert body["credits"] == 100
    assert len(body["schedule"]) == 7
    assert len(body["progress"]) == 3


async def test_an_upgrade_through_the_page_moves_the_card(client, db):
    """Характеристика растёт в базе — и в карточке, которую вернул зал."""
    player = await stand(db, make_player())
    for _ in range(3):
        await db.add_gym_point(player.user_id, Stat.STRENGTH.value)

    response = await client.post(
        "/api/gym", json={"action": "upgrade", "stat": "strength"}, headers=headers()
    )
    body = await response.json()

    assert response.status == 200
    assert "Сила выросла до 11" in body["said"]
    strength = next(
        row for row in body["card"]["stats"] if row["code"] == "strength"
    )
    assert strength["base"] == 11 and strength["total"] == 11
    assert (await db.get_player(42)).strength == 11


async def test_an_upgrade_of_a_nonsense_stat_is_refused(client, db):
    await stand(db, make_player())

    response = await client.post(
        "/api/gym", json={"action": "upgrade", "stat": "обаяние"}, headers=headers()
    )

    assert response.status == 409
    assert "Такой характеристики нет" in (await response.json())["error"]


async def test_an_unknown_action_is_refused(client, db):
    await stand(db, make_player())

    response = await client.post(
        "/api/gym", json={"action": "танцевать"}, headers=headers()
    )

    assert response.status == 400
