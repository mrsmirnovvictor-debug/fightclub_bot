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
    # И занятие потрачено: вернуться и доработать его нельзя
    player.location = GYM
    await db.save_player(player)
    with pytest.raises(GymError, match="уже отработал"):
        await join(db, player, now + TRAINING_SECONDS + 60)


async def test_walking_out_early_costs_the_slot(db):
    """Ушёл — занятие потрачено: ни очка, ни второй попытки в том же слоте."""
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await join(db, player, now)

    await leave(db, player)

    assert await visit_of(db, player) is None
    rows = await progress_of(db, player)
    assert sum(row.points for row in rows.values()) == 0
    # И заново в это же занятие не встать
    with pytest.raises(GymError, match="уже отработал"):
        await join(db, player, now + 60)


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
                     None, {}, {}, 0, now)

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
                     await visit_of(db, player), {visit.slot: False}, {}, 1, now)

    # Боец стоит на занятии прямо сейчас — это не «отработано»
    assert body["schedule"][0]["slots"][0]["state"] == "training"
    assert body["visit"]["seconds_left"] == TRAINING_SECONDS
    assert not body["visit"]["over"]


async def test_the_board_says_when_joining_is_too_late(db):
    now = at_slot(0)
    player = await with_pass(db, now)
    late = slot_now(now + 60).ends - 60

    body = build_gym(player, await db.gym_pass_of(42), await progress_of(db, player),
                     None, {}, {}, 0, late)

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


# ---------- абонемент по подписке ----------


def pro_player(days: int = 30, credits: int = 0, now: int | None = None) -> Player:
    """Боец с живой подпиской PRO."""
    from bot.game.health import now_ts

    player = make_player(credits=credits)
    player.pro_until = (now_ts() if now is None else now) + days * 24 * 60 * 60
    return player


async def test_a_subscription_opens_the_pass_for_its_own_term(db):
    """«На время, пока действует PRO, у игрока есть абонемент»."""
    from bot.gym_service import cover_pass

    now = at_slot(0)
    player = await stand(db, pro_player(now=now))

    said = await cover_pass(db, player, now)

    assert "по подписке" in said
    assert player.credits == 0, "абонемент по подписке не стоит кредитов"
    # Срок абонемента — ровно срок подписки, а не месяц
    assert await db.gym_pass_of(player.user_id) == player.pro_until


async def test_a_subscriber_walks_into_a_training_without_paying(db):
    """Абонемент открывается сам — и в зал пускают."""
    now = at_slot(0, shift=60)
    player = await stand(db, pro_player(now=now, credits=0))

    said = await settle(db, player, now)
    visit = await join(db, player, now)

    assert "по подписке" in said
    assert visit.stat == slot_now(now).training.stat
    assert player.credits == 0


async def test_a_subscriber_cannot_stack_free_passes(db):
    """Подписка держит срок, а не выдаёт месяцы."""
    from bot.gym_service import cover_pass

    now = at_slot(0)
    player = await stand(db, pro_player(now=now))

    first = await cover_pass(db, player, now)
    again = [await cover_pass(db, player, now) for _ in range(9)]

    assert first, "первый раз абонемент всё-таки открывают"
    assert again == [""] * 9, "дальше подписке нечего прибавить"
    assert await db.gym_pass_of(player.user_id) == player.pro_until


async def test_the_pass_dies_with_the_subscription(db):
    from bot.gym_service import cover_pass, has_pass

    now = at_slot(0)
    player = await stand(db, pro_player(days=1, now=now))
    await cover_pass(db, player, now)
    after = player.pro_until + 1

    assert not has_pass(await db.gym_pass_of(player.user_id), after)
    # И сам собой не продлевается: за него ни разу не платили
    assert await cover_pass(db, player, after) == ""


async def test_a_paid_pass_longer_than_the_subscription_is_not_cut(db):
    from bot.gym_service import cover_pass

    now = at_slot(0)
    player = await stand(db, pro_player(days=1, credits=10_000, now=now))
    await buy_pass(db, player, "year", now)
    paid = await db.gym_pass_of(player.user_id)

    assert await cover_pass(db, player, now) == ""
    assert await db.gym_pass_of(player.user_id) == paid


async def test_a_subscriber_who_pays_gets_time_after_the_subscription(db):
    """Подписчик покупает не абонемент, который у него есть, а время после."""
    now = at_slot(0)
    player = await stand(db, pro_player(days=30, credits=600, now=now))
    await settle(db, player, now)
    pro_end = player.pro_until

    ticket = await buy_pass(db, player, "month", now)

    assert ticket.price == 500 and player.credits == 100
    assert await db.gym_pass_of(player.user_id) == pro_end + 30 * 24 * 60 * 60


async def test_extending_the_subscription_extends_the_pass_with_it(db):
    from bot.game.pro import paid_offer
    from bot.pro_service import grant_pro

    now = at_slot(0)
    player = await stand(db, pro_player(days=1, now=now))
    await settle(db, player, now)
    first = await db.gym_pass_of(player.user_id)

    grant = await grant_pro(db, player, paid_offer(), now)

    assert grant.gym, "выдача подписки должна дотянуть абонемент"
    assert await db.gym_pass_of(player.user_id) == player.pro_until > first


async def test_the_gym_screen_says_the_pass_came_with_the_subscription(client, db):
    """Путь целиком: подписчик открывает зал и уже с абонементом."""
    now = at_slot(0)
    await stand(db, pro_player(now=now, credits=0))

    response = await client.get("/api/gym", headers=headers())
    body = await response.json()

    assert response.status == 200
    assert body["pass"]["active"]
    assert "по подписке" in body["said"]


# ---------- цена дня ----------


def test_the_first_training_a_day_is_free_and_the_rest_cost():
    from bot.game.gym import DAY_PRICES, VISITS_PER_DAY, day_is_full, price_of_visit

    assert DAY_PRICES == (0, 50, 50, 100, 100, 200)
    assert VISITS_PER_DAY == 6
    assert [price_of_visit(n) for n in range(6)] == [0, 50, 50, 100, 100, 200]
    assert day_is_full(6) and not day_is_full(5)


async def test_the_first_training_costs_nothing(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    player.credits = 0
    await db.save_player(player)

    visit = await join(db, player, now)

    assert visit.price == 0 and player.credits == 0


async def test_the_second_training_of_the_day_costs_fifty(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    player.credits = 500
    await db.save_player(player)
    await join(db, player, now)
    await settle(db, player, now + TRAINING_SECONDS)

    later = at_slot(1, shift=60)
    visit = await join(db, player, later)

    assert visit.price == 50
    assert (await db.get_player(42)).credits == 450


async def test_the_day_ladder_is_walked_price_by_price(db):
    """50, 50, 100, 100, 200 — за вторую и дальше."""
    from bot.game.gym import DAY_PRICES

    player = await with_pass(db, at_slot(0))
    player.credits = 10_000
    await db.save_player(player)

    prices = []
    for index in range(6):
        moment = at_slot(index, shift=60)
        visit = await join(db, player, moment)
        prices.append(visit.price)
        await settle(db, player, moment + TRAINING_SECONDS)

    assert prices == list(DAY_PRICES)
    assert (await db.get_player(42)).credits == 10_000 - sum(DAY_PRICES)


async def test_a_seventh_training_a_day_is_refused(db):
    """Шесть слотов — шесть занятий, и это потолок суток."""
    player = await with_pass(db, at_slot(0))
    player.credits = 10_000
    await db.save_player(player)
    for index in range(6):
        moment = at_slot(index, shift=60)
        await join(db, player, moment)
        await settle(db, player, moment + TRAINING_SECONDS)

    # Седьмого слота в сутках нет вовсе — зал закрыт после восьми вечера
    assert slot_now(at_slot(5, shift=SLOT_SECONDS + 60)) is None


async def test_an_empty_purse_buys_no_second_training(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    player.credits = 10
    await db.save_player(player)
    await join(db, player, now)
    await settle(db, player, now + TRAINING_SECONDS)

    with pytest.raises(GymError, match="сверх абонемента стоит 50"):
        await join(db, player, at_slot(1, shift=60))

    assert (await db.get_player(42)).credits == 10


async def test_the_price_counts_trainings_not_slot_numbers(db):
    """Пропустивший утро платит за вторую столько же, сколько и не пропустивший."""
    player = await with_pass(db, at_slot(0))
    player.credits = 1000
    await db.save_player(player)
    # Первое занятие — вечернее, четвёртое по счёту слотов
    late = at_slot(3, shift=60)
    first = await join(db, player, late)
    await settle(db, player, late + TRAINING_SECONDS)

    second = await join(db, player, at_slot(4, shift=60))

    assert first.price == 0 and second.price == 50


async def test_a_new_day_brings_the_free_training_back(db):
    player = await with_pass(db, at_slot(0))
    player.credits = 1000
    await db.save_player(player)
    now = at_slot(0, shift=60)
    await join(db, player, now)
    await settle(db, player, now + TRAINING_SECONDS)

    # Следующие сутки — счёт с нуля
    tomorrow = now + 24 * 60 * 60
    visit = await join(db, player, tomorrow)

    assert visit.price == 0


async def test_an_abandoned_training_still_counts_against_the_day(db):
    """Место в дне занято и брошенным занятием: слот потрачен."""
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    player.credits = 1000
    await db.save_player(player)
    await join(db, player, now)
    await settle(db, player, now + TRAINING_SECONDS)

    # Второе занятие бросаем, не достояв
    await join(db, player, at_slot(1, shift=60))
    await leave(db, player)

    # Третье стоит как третье, а не как второе
    third = await join(db, player, at_slot(2, shift=60))
    assert third.price == 50
    from bot.gym_service import day_count

    assert await day_count(db, player, "2026-10-05") == 3


# ---------- пять мест ----------


async def crowd_the_slot(db: Database, now: int, how_many: int) -> None:
    """Посадить в идущее занятие столько чужих бойцов."""
    slot = slot_now(now)
    for user_id in range(100, 100 + how_many):
        await db.start_gym_visit(
            user_id, slot.id, slot.training.stat.value, now + TRAINING_SECONDS
        )


async def test_a_slot_holds_five_fighters(db):
    from bot.game.gym import SLOT_LIMIT

    assert SLOT_LIMIT == 5
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await crowd_the_slot(db, now, 4)

    visit = await join(db, player, now)

    assert visit.stat is not None
    assert await db.gym_slot_crowd(slot_now(now).id) == 5


async def test_the_sixth_fighter_is_turned_away(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await crowd_the_slot(db, now, 5)

    with pytest.raises(GymError, match="мест заняты"):
        await join(db, player, now)

    assert await db.gym_slot_crowd(slot_now(now).id) == 5
    assert await visit_of(db, player) is None


async def test_a_full_slot_takes_no_money(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    player.credits = 1000
    await db.save_player(player)
    await join(db, player, now)
    await settle(db, player, now + TRAINING_SECONDS)
    # Второе занятие платное — и набито битком
    later = at_slot(1, shift=60)
    await crowd_the_slot(db, later, 5)

    with pytest.raises(GymError, match="мест заняты"):
        await join(db, player, later)

    assert (await db.get_player(42)).credits == 1000, "за отказ не берут"


async def test_the_last_seat_is_not_given_out_twice(db):
    """Пятое место достаётся одному: считаем после записи, а не до."""
    now = at_slot(0, shift=60)
    first = await with_pass(db, now)
    second = await stand(db, Player(
        user_id=43, nickname="Марла", class_code="warrior", level=8,
        credits=0, location=GYM,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    ))
    await db.set_gym_pass(43, now + 10_000)
    await crowd_the_slot(db, now, 4)

    await join(db, first, now)
    with pytest.raises(GymError, match="мест"):
        await join(db, second, now)

    assert await db.gym_slot_crowd(slot_now(now).id) == 5


async def test_the_board_says_how_many_seats_are_taken(db):
    now = at_slot(0, shift=60)
    player = await with_pass(db, now)
    await crowd_the_slot(db, now, 5)
    slots = [slot.id for slot in schedule_from(now)]

    body = build_gym(
        player, await db.gym_pass_of(42), await progress_of(db, player), None,
        await db.gym_slots_taken(42, slots), await db.gym_slots_crowd(slots), 0, now,
    )

    first = body["schedule"][0]["slots"][0]
    assert first["taken"] == 5 and first["limit"] == 5 and first["full"]
    assert first["state"] == "full"
    assert body["now"]["state"] == "full"


# ---------- абонемент в документах ----------


async def test_the_pass_shows_up_in_the_documents(db):
    from bot.webapp.documents import build_documents

    now = at_slot(0)
    player = await stand(db, make_player(credits=600))
    await buy_pass(db, player, "month", now)
    fresh = await db.get_player(42)

    body = build_documents(fresh, now)

    paper = next(one for one in body["documents"] if one["kind"] == "gym")
    assert paper["title"] == "Абонемент в тренажёрный зал"
    assert paper["holder"] == "Тайлер" and paper["holder_title"] == "Владелец"
    assert paper["active"] and paper["state"] == "Действует"
    assert any("бесплатно" in line for line in paper["gives"])
    # Номера у абонемента нет: его заводят на входе, а не выписывают
    assert paper["number"] == ""


async def test_an_expired_pass_stays_a_document(db):
    from bot.webapp.documents import build_documents

    now = at_slot(0)
    player = await stand(db, make_player(credits=600))
    await buy_pass(db, player, "month", now)
    fresh = await db.get_player(42)
    later = fresh.gym_until + 60

    paper = next(
        one for one in build_documents(fresh, later)["documents"]
        if one["kind"] == "gym"
    )

    assert not paper["active"] and paper["state"] == "Срок вышел"


async def test_without_a_pass_there_is_no_gym_document(db):
    from bot.webapp.documents import build_documents

    player = await stand(db, make_player())

    body = build_documents(player, at_slot(0))

    assert not [one for one in body["documents"] if one["kind"] == "gym"]


async def test_the_seat_check_is_made_after_the_record_not_before(db):
    """Место считается по записи, а не перед ней.

    Проверка до вставки пропустила бы шестого, пока пятый ещё не
    записался. Здесь она обходится напрямую, мимо `join`: сам `join`
    отказал бы раньше, и до этой защиты дело бы не дошло.
    """
    from bot.game.gym import SLOT_LIMIT

    now = at_slot(0, shift=60)
    slot = slot_now(now)
    for user_id in range(100, 100 + SLOT_LIMIT):
        assert await db.start_gym_visit(
            user_id, slot.id, "strength", now + 900, limit=SLOT_LIMIT
        )

    late = await db.start_gym_visit(
        42, slot.id, "strength", now + 900, limit=SLOT_LIMIT
    )

    assert not late, "шестого не записывают"
    # И его запись не осталась в занятии
    assert await db.gym_slot_crowd(slot.id) == SLOT_LIMIT


async def test_without_a_limit_the_record_is_kept(db):
    """Ноль мест — значит ограничения нет: так зовут не из зала."""
    now = at_slot(0, shift=60)
    slot = slot_now(now)
    for user_id in range(100, 110):
        await db.start_gym_visit(user_id, slot.id, "strength", now + 900)

    assert await db.gym_slot_crowd(slot.id) == 10
