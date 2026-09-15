"""Награда за вход: как считаются дни и что за них дают.

Три правила проверяются строже прочих. Сутки кончаются в полночь по
Москве, а не по часам машины. Вход засчитывается один раз в день, сколько
бы раз игрок ни открыл карточку. И невзятая награда не сгорает — иначе
она наказывала бы за то, что человек зашёл и отвлёкся.
"""

from datetime import datetime, timezone

import pytest

from bot.content.daily import (
    LAST_DAY_MOD,
    MILESTONES,
    MONTHLY_POTIONS,
    month_days,
    monthly_potion,
    next_milestone,
    reward_for,
    unclaimed,
)
from bot.game.potions import RAID_PASS
from bot.daily_service import DailyError, check_in, claim, ladder_view
from bot.game.daily import MOSCOW, club_day, month_key, next_reset
from tests.test_inventory import make_player


def msk(year, month, day, hour=12, minute=0) -> float:
    """Момент по московским часам — в том виде, в каком его знает служба."""
    return datetime(year, month, day, hour, minute, tzinfo=MOSCOW).timestamp()


@pytest.fixture
async def fighter(db):
    player = make_player()
    player.credits = 0
    await db.save_player(player)
    return player


# ---------- сутки и месяц ----------


def test_the_day_ends_at_moscow_midnight():
    """Полночь в Москве, а не там, где стоит сервер."""
    # без четверти полночь по Москве — ещё вчерашний день
    assert club_day(msk(2026, 9, 15, 23, 45)).day == 15
    # четверть первого — уже новый
    assert club_day(msk(2026, 9, 16, 0, 15)).day == 16

    # тот же момент в UTC приходится на предыдущие сутки — и это не должно
    # сбивать счёт: сервер живёт по Москве
    late = datetime(2026, 9, 16, 0, 15, tzinfo=MOSCOW)
    assert late.astimezone(timezone.utc).day == 15
    assert club_day(late.timestamp()).day == 16


def test_the_counter_resets_at_the_next_midnight():
    moment = msk(2026, 9, 15, 23, 45)

    resets = next_reset(moment)

    assert club_day(resets).day == 16
    assert datetime.fromtimestamp(resets, MOSCOW).hour == 0
    assert 0 < resets - moment <= 3600


def test_the_month_is_the_calendar_one():
    assert month_key(club_day(msk(2026, 9, 30, 23, 59))) == "2026-09"
    assert month_key(club_day(msk(2026, 10, 1, 0, 1))) == "2026-10"


# ---------- лестница ----------


def test_the_ladder_is_the_one_that_was_ordered():
    assert MILESTONES == (1, 3, 7, 14, 21, 28)
    assert reward_for(1, "2026-09").credits == 25
    assert reward_for(3, "2026-09").potion == "heal_small"
    assert reward_for(7, "2026-09").credits == 50
    assert reward_for(21, "2026-09").credits == 100
    assert reward_for(28, "2026-09").credits == 200
    # вехи видно крупнее прочих клеток
    assert all(reward_for(day, "2026-09").big for day in MILESTONES)


def test_every_other_day_holds_a_raid_pass():
    """Пустых клеток в календаре нет: не веха — значит пропуск в подвал."""
    plain = [day for day in range(1, 30) if day not in MILESTONES]

    assert plain, "между вехами есть будни"
    for day in plain:
        reward = reward_for(day, "2026-09")
        assert reward.potion == RAID_PASS and not reward.big


def test_the_last_day_of_the_month_holds_a_whetstone():
    """Заточка ждёт в последний день — какой бы он в этом месяце ни был."""
    for month, last in (("2026-09", 30), ("2026-01", 31), ("2028-02", 29)):
        assert reward_for(last, month).mod == LAST_DAY_MOD
        assert reward_for(last - 1, month).mod == ""


def test_the_february_last_day_keeps_both_gifts():
    """Двадцать восьмой вход и конец месяца сошлись — отдаём и то, и другое."""
    both = reward_for(28, "2026-02")

    assert both.credits == 200 and both.mod == LAST_DAY_MOD
    assert "200 кредитов" in both.title and "заточка" in both.title


def test_the_calendar_has_a_cell_per_day_and_no_more():
    assert month_days("2026-09") == 30 and month_days("2028-02") == 29
    assert reward_for(30, "2026-09") is not None
    assert reward_for(31, "2026-09") is None, "тридцать первого сентября не бывает"
    assert reward_for(0, "2026-09") is None


def test_the_fourteenth_day_brings_a_different_potion_every_month():
    """Склянка месяца меняется по кругу: за квартал соберёшь все три."""
    taken = [monthly_potion(f"2026-{month:02d}")[0] for month in range(1, 13)]

    assert set(taken) == {code for code, _, _ in MONTHLY_POTIONS}
    # три месяца подряд — три разные склянки
    assert len(set(taken[:3])) == 3
    assert reward_for(14, "2026-09").potion == taken[8]


def test_nothing_is_lost_along_the_way():
    """Невзятое копится: дошёл до третьего дня и отвлёкся — заберёшь позже."""
    assert [one.day for one in unclaimed(8, 0, "2026-09")] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [one.day for one in unclaimed(8, 3, "2026-09")] == [4, 5, 6, 7, 8]
    assert unclaimed(8, 8, "2026-09") == ()


def test_the_count_cannot_run_past_the_month():
    """Входов больше, чем дней в месяце, не бывает — и клеток тоже."""
    assert [one.day for one in unclaimed(40, 28, "2026-09")] == [29, 30]


def test_the_ladder_ends_with_the_month():
    assert next_milestone(8, "2026-09") == 9
    assert next_milestone(29, "2026-09") == 30
    assert next_milestone(30, "2026-09") == 0, "сентябрь пройден"
    assert next_milestone(28, "2026-02") == 0, "февраль короче"


# ---------- счёт входов ----------


async def test_the_first_look_counts_the_day(db, fighter):
    state = await check_in(db, fighter, msk(2026, 9, 15))

    assert state.days == 1 and state.fresh
    assert [one.day for one in state.waiting] == [1]
    assert state.next_day == 2, "награда ждёт каждый день, а не через раз"


async def test_the_same_day_counts_once(db, fighter):
    """Хоть десять раз за вечер — вход всё равно один."""
    await check_in(db, fighter, msk(2026, 9, 15, 9))
    again = await check_in(db, fighter, msk(2026, 9, 15, 23, 59))

    assert again.days == 1
    assert not again.fresh, "второй раз за сутки окно новым не считается"
    # но невзятая награда всё ещё видна: прятать её было бы обманом
    assert again.waiting


async def test_a_skipped_day_does_not_burn_the_count(db, fighter):
    """Счёт не серия: пропустил вторник — просто не прибавилось."""
    await check_in(db, fighter, msk(2026, 9, 1))
    await check_in(db, fighter, msk(2026, 9, 2))
    # пропуск в несколько дней
    state = await check_in(db, fighter, msk(2026, 9, 9))

    assert state.days == 3


async def test_a_new_month_starts_the_ladder_over(db, fighter):
    for day in range(1, 8):
        await check_in(db, fighter, msk(2026, 9, day))
    await claim(db, fighter, msk(2026, 9, 7))
    assert fighter.credits == 75  # 25 за первый и 50 за седьмой

    state = await check_in(db, fighter, msk(2026, 10, 1))

    assert state.days == 1 and state.month == "2026-10"
    assert [one.day for one in state.waiting] == [1], "лестница началась заново"


# ---------- выдача ----------


async def test_credits_and_potions_come_together(db, fighter):
    for day in range(1, 4):
        await check_in(db, fighter, msk(2026, 9, day))

    taken = await claim(db, fighter, msk(2026, 9, 3))

    assert taken.credits == 25
    # первый день — кредиты, второй — пропуск, третий — склянка
    assert sorted(taken.potions) == sorted([RAID_PASS, "heal_small"])
    assert fighter.credits == 25
    potions = await db.list_potions(fighter.user_id)
    assert potions.get("heal_small") == 1 and potions.get(RAID_PASS) == 1
    # и в базе это сохранилось, а не только в памяти
    assert (await db.get_player(fighter.user_id)).credits == 25


async def test_the_whetstone_lands_in_the_workshop_bag(db, fighter):
    """Заточка последнего дня — вещь, и лежит она среди модификаторов."""
    for day in range(1, 31):
        await check_in(db, fighter, msk(2026, 9, day))

    taken = await claim(db, fighter, msk(2026, 9, 30))

    assert taken.mods == [LAST_DAY_MOD]
    assert (await db.list_mods(fighter.user_id)).get(LAST_DAY_MOD) == 1


async def test_a_reward_is_taken_once(db, fighter):
    await check_in(db, fighter, msk(2026, 9, 1))
    await claim(db, fighter, msk(2026, 9, 1))

    with pytest.raises(DailyError, match="Забирать пока нечего"):
        await claim(db, fighter, msk(2026, 9, 1))

    assert fighter.credits == 25, "второй раз не начисляется"


async def test_no_day_of_the_month_is_empty(db, fighter):
    """Второй вход — не веха, но и он не пустой: ждёт рейд-пасс."""
    await check_in(db, fighter, msk(2026, 9, 1))
    await claim(db, fighter, msk(2026, 9, 1))
    state = await check_in(db, fighter, msk(2026, 9, 2))

    assert state.days == 2 and state.has_gift
    taken = await claim(db, fighter, msk(2026, 9, 2))
    assert taken.potions == [RAID_PASS] and taken.credits == 0

    # а вот дважды за один день — уже нечего
    with pytest.raises(DailyError, match="Забирать пока нечего"):
        await claim(db, fighter, msk(2026, 9, 2))


async def test_the_whole_month_pays_what_it_promised(db, fighter):
    """Весь сентябрь подряд — весь календарь до копейки."""
    for day in range(1, 31):
        await check_in(db, fighter, msk(2026, 9, day))
    taken = await claim(db, fighter, msk(2026, 9, 30))

    assert taken.credits == 25 + 50 + 100 + 200
    assert taken.mods == [LAST_DAY_MOD]
    # склянки вех — и пропуск за каждый из оставшихся дней
    assert sorted(taken.potions) == sorted(
        ["heal_small", monthly_potion("2026-09")[0]]
        + [RAID_PASS] * (30 - len(MILESTONES))
    )
    assert [one.day for one in taken.rewards] == list(range(1, 31))


# ---------- окно ----------


async def test_the_window_shows_the_whole_month_at_a_glance(db, fighter):
    for day in range(1, 5):
        await check_in(db, fighter, msk(2026, 9, day))
    await claim(db, fighter, msk(2026, 9, 4))
    state = await check_in(db, fighter, msk(2026, 9, 5))

    rows = ladder_view(state)

    assert [row["day"] for row in rows] == list(range(1, 31)), "клетка на каждый день"
    taken = [row["day"] for row in rows if row["done"]]
    assert taken == [1, 2, 3, 4], "забранное помечено"
    assert [row["day"] for row in rows if row["ready"]] == [5], "пятый в руках"
    # всё, до чего боец ещё не дошёл, ждёт своей очереди
    assert all(not row["done"] and not row["ready"] for row in rows[5:])
    # вехи в сетке отмечены отдельно от будней
    assert [row["day"] for row in rows if row["big"]] == [1, 3, 7, 14, 21, 28, 30]


async def test_the_window_counts_the_cells_by_the_month(db, fighter):
    """В феврале клеток двадцать восемь, в январе — тридцать одна."""
    await check_in(db, fighter, msk(2026, 2, 3))
    short = ladder_view(await check_in(db, fighter, msk(2026, 2, 4)))

    assert len(short) == 28
    assert short[-1]["mod"] == LAST_DAY_MOD and short[-1]["credits"] == 200
