"""Журнал ELO, закрытие недели и выплаты по местам.

Правила счёта — в `tests/test_ladder.py`, здесь база: что попадает в
журнал, каким выходит снимок периода и как платят по нему деньги.
Проверки идут по списку приёмки карточки, и каждая названа тем, что
она держит.
"""

import asyncio

import pytest

from bot.game.classes import Stats
from bot.game.clock import WEEK_SECONDS, week_key, week_opens
from bot.game.economy import RATING_START
from bot.game.ladder import QUALIFY, prize_for
from bot.ladder_service import live_week, my_week, pay_week, settle, settle_due
from bot.models import Player
from tests.test_ladder import msk

# Неделя, в которой идут почти все проверки: 8 ноября 2026 — воскресенье
WEEK = msk(2026, 11, 8, 12)
NEXT = WEEK + WEEK_SECONDS
# Какой-нибудь будний час внутри этой недели
MIDWEEK = msk(2026, 11, 11, 20)


def make_player(user_id: int, **kwargs) -> Player:
    stats = Stats(strength=12, agility=8, intuition=8, endurance=12)
    return Player(
        user_id=user_id,
        nickname=kwargs.pop("nickname", f"Боец-{user_id}"),
        class_code="warrior",
        level=kwargs.pop("level", 5),
        **stats.as_dict(),
        **kwargs,
    )


async def fight(db, user_id, outcome="win", delta=0, at=MIDWEEK, key=None, kind="duel"):
    """Положить в журнал один бой и подвинуть рейтинг бойца, как в игре."""
    player = await db.get_player(user_id)
    was = player.rating
    player.apply_rating(delta)
    await db.save_player(player)
    return await db.log_rating(
        user_id,
        kind,
        key or f"{user_id}-{at}-{outcome}-{delta}",
        outcome,
        player.rating - was,
        player.rating,
        at,
    )


async def many_fights(db, user_id, count, **kwargs):
    """Несколько боёв подряд: нужно, чтобы перешагнуть порог."""
    for number in range(count):
        await fight(db, user_id, key=f"{user_id}-{number}-{kwargs}", **kwargs)


# ---------- порог: критерий 1 ----------


async def test_nine_fights_leave_a_fighter_out_and_the_tenth_lets_him_in(db):
    """С девятью боями бойца в рейтинге нет, с десятью — есть.

    Критерий приёмки 1. Высокий ELO порога не отменяет: без него
    воскресную выплату получал бы тот, кто однажды поднялся и с тех пор
    не дрался.
    """
    await db.save_player(make_player(1, rating=2500))
    await many_fights(db, 1, QUALIFY - 1, outcome="draw")

    assert await live_week(db, MIDWEEK) == []
    assert await my_week(db, 1, MIDWEEK) == 9

    await fight(db, 1, outcome="draw", key="the-tenth")

    places = await live_week(db, MIDWEEK)
    assert [(place, one.user_id) for place, one in places] == [(1, 1)]
    assert await my_week(db, 1, MIDWEEK) == 10


async def test_a_fighter_below_the_threshold_does_not_shift_anybody(db):
    """Непрошедший не занимает место и никого не сдвигает."""
    for user_id, rating in ((1, 2500), (2, 1500), (3, 1400)):
        await db.save_player(make_player(user_id, rating=rating))
    await many_fights(db, 1, QUALIFY - 1, outcome="draw")
    await many_fights(db, 2, QUALIFY, outcome="draw")
    await many_fights(db, 3, QUALIFY, outcome="draw")

    places = await live_week(db, MIDWEEK)
    assert [(place, one.user_id) for place, one in places] == [(1, 2), (2, 3)]


# ---------- что считается боем: критерий 2 ----------


def test_only_a_fight_between_players_moves_the_rating():
    """Рейтинг двигают ровно две службы: дуэль и групповой бой.

    Критерий приёмки 2 — про то, что бои с NPC, рейды и уличные
    нападения в недельную статистику не входят. Держится он не
    запретом, а устройством игры: в недельный счётчик попадает то, что
    записано в журнал ELO, а записывают туда только там, где ELO и
    меняется. Эта проверка не даёт третьему месту появиться незаметно:
    добавит кто-нибудь `apply_rating` в рейд — и она упадёт, а вместе с
    ней встанет вопрос, идёт ли рейд в недельный зачёт.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "bot"
    movers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "apply_rating(" in path.read_text()
    )
    assert movers == ["battle_service.py", "duel_service.py", "models.py"]

    # И журнал ведут обе — иначе половина боёв в рейтинг не попадала бы
    for name in ("duel_service.py", "battle_service.py"):
        assert "log_rating(" in (root / name).read_text(), name


async def test_a_raid_leaves_the_weekly_count_untouched(db):
    """Рейд рейтинга не трогает — значит, и недельных боёв не прибавляет."""
    from bot.raid_service import RaidService  # noqa: F401 — важен сам импорт

    await db.save_player(make_player(1))
    # Ни одного боя в журнале: рейд в него не пишет и писать не умеет
    assert await my_week(db, 1, MIDWEEK) == 0
    assert await db.rating_log_of(1) == []


# ---------- границы недели: критерий 6 ----------


async def test_a_fight_that_ends_before_noon_belongs_to_the_old_week(db):
    """11:59:59 воскресенья — старая неделя, 12:00:00 — новая.

    Критерий приёмки 6. Время начала боя роли не играет вовсе: в журнал
    попадает миг, в который итог зафиксирован, и только он.
    """
    await db.save_player(make_player(1))
    last = NEXT - 1
    first = NEXT

    await fight(db, 1, outcome="draw", at=last, key="last-of-the-old")
    await fight(db, 1, outcome="draw", at=first, key="first-of-the-new")

    assert await my_week(db, 1, MIDWEEK) == 1
    assert await my_week(db, 1, first) == 1
    assert week_key(last) == "2026-11-08"
    assert week_key(first) == "2026-11-15"


async def test_a_closed_week_does_not_notice_the_fights_of_the_next(db):
    """Бои новой недели в закрытый итог старой не попадают.

    Критерий приёмки 11: подсчёт может опоздать, но считать он обязан
    по состоянию на границу, а не на миг подсчёта.
    """
    await db.save_player(make_player(1, rating=1000))
    await many_fights(db, 1, QUALIFY, outcome="win", delta=10)
    elo_at_border = (await db.get_player(1)).rating

    # Новая неделя: тот же боец дрался ещё и поднялся выше
    await many_fights(db, 1, QUALIFY, outcome="win", delta=10, at=NEXT + 3600)
    assert (await db.get_player(1)).rating > elo_at_border

    # И только теперь, с опозданием на неделю, закрываем старую
    await settle(db, "2026-11-08", NEXT + 2 * 24 * 3600)

    place = await db.place_of("2026-11-08", 1)
    assert place["rating"] == elo_at_border, "заплатили по сегодняшнему ELO"
    assert place["fights"] == QUALIFY, "в старую неделю затекли новые бои"
    assert place["wins"] == QUALIFY


async def test_a_new_week_starts_the_count_over_and_keeps_the_elo(db):
    """Критерий приёмки 3: ELO остаётся, счётчик боёв начинается заново."""
    await db.save_player(make_player(1, rating=1000))
    await many_fights(db, 1, QUALIFY, outcome="win", delta=20)
    earned = (await db.get_player(1)).rating
    assert earned == 1000 + 20 * QUALIFY

    # Та же неделя — рейтинг есть. Следующая — боёв ноль, ELO то же
    assert len(await live_week(db, MIDWEEK)) == 1
    assert await live_week(db, NEXT + 3600) == []
    assert await my_week(db, 1, NEXT + 3600) == 0
    assert (await db.get_player(1)).rating == earned

    # А история прошлой недели читается: по ней и считают с опозданием
    rows = await db.week_standings(WEEK, NEXT, least=QUALIFY)
    assert len(rows) == 1 and rows[0]["fights"] == QUALIFY


# ---------- порядок: критерии 4 и 5 ----------


async def test_the_higher_overall_elo_takes_the_higher_place(db):
    """Критерий приёмки 4: место даёт общий ELO, а не прирост недели."""
    await db.save_player(make_player(1, rating=1000))
    await db.save_player(make_player(2, rating=2000))
    # Первый за неделю поднялся сильно, второй — почти никак
    await many_fights(db, 1, QUALIFY, outcome="win", delta=30)
    await many_fights(db, 2, QUALIFY, outcome="win", delta=1)

    places = await live_week(db, MIDWEEK)
    assert [one.user_id for _, one in places] == [2, 1]


async def test_the_moment_the_elo_was_reached_comes_from_the_journal(db):
    """Четвёртый ключ равенства берётся из журнала, а не выдумывается.

    Критерий приёмки 5, последний уровень перед номером бойца: оба
    бойца пришли к одному и тому же ELO, но один раньше другого.
    """
    await db.save_player(make_player(1, rating=1000))
    await db.save_player(make_player(2, rating=1000))
    # Оба поднялись на 100 и дальше только ничьи — но первый поднялся
    # в начале недели, а второй в конце
    await fight(db, 1, outcome="win", delta=100, at=WEEK + 3600, key="one-up")
    await fight(db, 2, outcome="win", delta=100, at=NEXT - 3600, key="two-up")
    await many_fights(db, 1, QUALIFY - 1, outcome="draw", at=WEEK + 7200)
    await many_fights(db, 2, QUALIFY - 1, outcome="draw", at=WEEK + 7200)

    places = await live_week(db, MIDWEEK)
    assert [one.user_id for _, one in places] == [1, 2]
    first, second = (one for _, one in places)
    assert first.rating == second.rating == 1100
    assert first.reached < second.reached


async def test_an_elo_that_came_back_to_itself_counts_from_the_return(db):
    """Вернувшийся к прежнему значению ELO считается от возвращения.

    Так сказано в постановке: «если ELO изменился и затем вернулся к
    прежнему значению, используется новое время возвращения». Ничья
    эту дату не обновляет — она рейтинга не двигает вовсе.
    """
    await db.save_player(make_player(1, rating=1000))
    await fight(db, 1, outcome="win", delta=40, at=WEEK + 60, key="up")
    await fight(db, 1, outcome="loss", delta=-40, at=WEEK + 120, key="down")
    await many_fights(db, 1, QUALIFY, outcome="draw", at=WEEK + 600)

    places = await live_week(db, MIDWEEK)
    (_, one), = places
    assert one.rating == 1000, "рейтинг вернулся к прежнему"
    assert one.reached == WEEK + 120, "дата не от возвращения"


async def test_a_fighter_whose_elo_never_moved_holds_it_from_the_start(db):
    """Не двигавшийся ELO держится «с начала журнала» — и это ноль.

    Выдумывать такому бойцу дату мы не станем: ноль честно означает
    «раньше любой записи», и в равенстве он стоит выше поднявшихся
    позже. Это ограничение журнала, а не правило игры, и записано оно
    здесь, чтобы его не приняли за случайность.
    """
    await db.save_player(make_player(1, rating=RATING_START))
    await many_fights(db, 1, QUALIFY, outcome="draw")

    places = await live_week(db, MIDWEEK)
    (_, one), = places
    assert one.rating == RATING_START
    assert one.reached == 0


# ---------- повторная обработка: критерий 7 ----------


async def test_the_same_fight_delivered_twice_is_counted_once(db):
    """Критерий приёмки 7: повтор не двигает ни счётчик, ни ELO.

    Держит это уникальный ключ боя: отметка и проверка — один запрос, и
    второй заход упирается в индекс, не успев ничего прибавить.
    """
    await db.save_player(make_player(1, rating=1000))

    assert await db.log_rating(1, "duel", "the-same", "win", 25, 1025, MIDWEEK)
    assert not await db.log_rating(1, "duel", "the-same", "win", 25, 1050, MIDWEEK)

    assert await my_week(db, 1, MIDWEEK) == 1
    rows = await db.rating_log_of(1)
    assert len(rows) == 1 and rows[0]["rating"] == 1025


async def test_two_sides_of_one_fight_are_two_rows_not_one(db):
    """Ключ боя общий, а запись у каждого бойца своя.

    Иначе уникальный ключ отбросил бы второго бойца того же боя — и
    половина боёв в рейтинг не попадала бы.
    """
    await db.save_player(make_player(1))
    await db.save_player(make_player(2))

    assert await db.log_rating(1, "duel", "one-fight", "win", 25, 1025, MIDWEEK)
    assert await db.log_rating(2, "duel", "one-fight", "loss", -25, 975, MIDWEEK)

    assert await my_week(db, 1, MIDWEEK) == 1
    assert await my_week(db, 2, MIDWEEK) == 1


async def test_a_duel_and_a_group_fight_may_share_a_number(db):
    """Род боя входит в ключ: номера у дуэлей и отрядов свои.

    Оба счётчика живут в памяти и после перезапуска начинают с единицы.
    Без рода боя дуэль №1 и групповой бой №1 оказались бы одним боем.
    """
    await db.save_player(make_player(1))

    assert await db.log_rating(1, "duel", "1", "win", 25, 1025, MIDWEEK)
    assert await db.log_rating(1, "battle", "1", "win", 25, 1050, MIDWEEK)
    assert await my_week(db, 1, MIDWEEK) == 2


# ---------- выплаты: критерии 8, 9, 12 ----------


async def test_the_prizes_are_paid_by_place_down_to_the_fiftieth(db):
    """Критерий приёмки 8: проверены места 1–5, 9, 10, 19, 20, 50 и 51.

    Шестьдесят бойцов с заведомо разным ELO: место каждого известно
    заранее, и сверять можно прямо с призовой таблицей.
    """
    for number in range(1, 61):
        # ELO убывает с номером: боец №1 первый, №60 последний
        await db.save_player(make_player(number, rating=5000 - number))
        await many_fights(db, number, QUALIFY, outcome="draw")

    said = await settle(db, "2026-11-08", NEXT + 60)
    assert "участников 60" in said

    for place in (1, 2, 3, 4, 5, 9, 10, 19, 20, 50):
        row = await db.place_of("2026-11-08", place)
        assert row["place"] == place
        assert row["prize"] == prize_for(place), place
        assert row["paid_at"], f"место {place} не оплачено"
        assert (await db.get_player(place)).credits == prize_for(place)

    # Пятьдесят первое и ниже — в снимке есть, а денег нет
    for place in (51, 60):
        row = await db.place_of("2026-11-08", place)
        assert row["place"] == place and row["prize"] == 0
        assert row["paid_at"] is None, "за нулевой приз поставили отметку"
        assert (await db.get_player(place)).credits == 0


async def test_the_sum_paid_matches_the_snapshot(db):
    """Критерий приёмки 12: начислено ровно столько, сколько в снимке."""
    for number in range(1, 8):
        await db.save_player(make_player(number, rating=2000 - number))
        await many_fights(db, number, QUALIFY, outcome="draw")

    await settle(db, "2026-11-08", NEXT + 60)

    week = await db.settled_week("2026-11-08")
    places = await db.week_places("2026-11-08", limit=100)
    assert week["prize"] == sum(one["prize"] for one in places)
    paid = 0
    for one in places:
        paid += (await db.get_player(one["user_id"])).credits
    assert paid == week["prize"]


@pytest.mark.parametrize("crowd", [0, 1, 7, 55])
async def test_a_week_with_any_number_of_qualified_fighters_settles(db, crowd):
    """Критерий приёмки 9: ноль, один, меньше пятидесяти и больше.

    Пустые места не оплачиваются и призы не перераспределяются: трое
    прошедших получат премии за первое, второе и третье, а деньги за
    остальные сорок семь мест просто не выйдут из кассы.
    """
    for number in range(1, crowd + 1):
        await db.save_player(make_player(number, rating=5000 - number))
        await many_fights(db, number, QUALIFY, outcome="draw")
    # И один, кто порога не прошёл: он не должен попасть никуда
    await db.save_player(make_player(900, rating=9000))
    await many_fights(db, 900, QUALIFY - 1, outcome="draw")

    await settle(db, "2026-11-08", NEXT + 60)

    week = await db.settled_week("2026-11-08")
    assert week is not None, "период не закрыт — его будут считать снова"
    assert week["players"] == crowd
    assert week["prize"] == sum(prize_for(place) for place in range(1, crowd + 1))
    assert await db.place_of("2026-11-08", 900) is None
    assert (await db.get_player(900)).credits == 0


# ---------- повтор и обрыв: критерий 10 ----------


async def test_settling_the_same_week_twice_pays_once(db):
    """Критерий приёмки 10: повторный подсчёт не задваивает ничего."""
    await db.save_player(make_player(1, rating=1500))
    await many_fights(db, 1, QUALIFY, outcome="draw")

    await settle(db, "2026-11-08", NEXT + 60)
    assert (await db.get_player(1)).credits == prize_for(1)

    # Трижды подряд, и ещё раз через сутки
    await settle(db, "2026-11-08", NEXT + 120)
    await settle(db, "2026-11-08", NEXT + 180)
    await settle(db, "2026-11-08", NEXT + 24 * 3600)
    assert (await db.get_player(1)).credits == prize_for(1)
    assert (await db.settled_week("2026-11-08"))["players"] == 1


async def test_two_handlers_at_once_do_not_pay_twice(db):
    """Конкурентный запуск: платит тот, кто первым поставил отметку."""
    for number in range(1, 6):
        await db.save_player(make_player(number, rating=2000 - number))
        await many_fights(db, number, QUALIFY, outcome="draw")

    await asyncio.gather(
        settle_due(db, NEXT + 60),
        settle_due(db, NEXT + 60),
        settle_due(db, NEXT + 60),
    )

    for place in range(1, 6):
        assert (await db.get_player(place)).credits == prize_for(place), place


async def test_a_break_in_the_middle_leaves_the_rest_to_be_paid_later(db):
    """Обрыв посередине выплат доплачивается следующим заходом.

    Оплаченные места второй раз не платятся, неоплаченные доплачиваются
    — и ровно они.
    """
    for number in range(1, 6):
        await db.save_player(make_player(number, rating=2000 - number))
        await many_fights(db, number, QUALIFY, outcome="draw")

    whole = db.pay_place
    done = 0

    async def breaks_on_the_third(period, user_id, at):
        nonlocal done
        if done >= 2:
            raise RuntimeError("касса закрылась посередине")
        done += 1
        return await whole(period, user_id, at)

    db.pay_place = breaks_on_the_third
    with pytest.raises(RuntimeError):
        await settle(db, "2026-11-08", NEXT + 60)
    db.pay_place = whole

    # Двое получили, трое ждут
    assert (await db.get_player(1)).credits == prize_for(1)
    assert (await db.get_player(2)).credits == prize_for(2)
    assert (await db.get_player(3)).credits == 0
    owed = await db.owed_places("2026-11-08")
    assert [one["place"] for one in owed] == [3, 4, 5]

    # Следующий взгляд доплачивает — и только недоплаченное
    said = await settle_due(db, NEXT + 7200)
    assert said, "долг не заметили"
    for place in range(1, 6):
        assert (await db.get_player(place)).credits == prize_for(place), place
    assert await db.owed_places("2026-11-08") == []


async def test_the_snapshot_is_not_counted_again_after_a_break(db):
    """Снимок, записанный до обрыва, вторым заходом не пересчитывается.

    Это и держит обещание «закрытый период не меняется»: доплата идёт
    по записанным местам, а не по новому подсчёту.
    """
    await db.save_player(make_player(1, rating=1500))
    await many_fights(db, 1, QUALIFY, outcome="draw")
    await db.close_week(
        "2026-11-08",
        WEEK,
        NEXT,
        NEXT + 60,
        [
            {
                "user_id": 1,
                "place": 1,
                "rating": 1500,
                "fights": QUALIFY,
                "wins": 0,
                "losses": 0,
                "reached": 0,
                "prize": prize_for(1),
            }
        ],
    )
    # После снимка боец ещё подрался и поднялся — итог это не трогает
    await many_fights(db, 1, QUALIFY, outcome="win", delta=50)

    await pay_week(db, "2026-11-08", NEXT + 120)

    row = await db.place_of("2026-11-08", 1)
    assert row["rating"] == 1500
    assert row["fights"] == QUALIFY


async def test_an_unfinished_week_is_not_closed_early(db):
    """Идущая неделя снимком не становится: её итог ещё не итог."""
    await db.save_player(make_player(1, rating=1500))
    await many_fights(db, 1, QUALIFY, outcome="draw")

    assert await settle(db, "2026-11-08", MIDWEEK) == ""
    assert await db.settled_week("2026-11-08") is None
    assert (await db.get_player(1)).credits == 0

    # И общий проход её тоже не трогает
    assert await settle_due(db, MIDWEEK) == []
    assert await db.settled_week("2026-11-08") is None


# ---------- простой: доплата по возвращении ----------


async def test_weeks_missed_while_the_bot_was_down_are_settled_on_return(db):
    """Бота не было три недели — все три закрываются по возвращении.

    Каждая по состоянию на свою границу: подсчёт опоздал, а итог тот
    же, что был бы вовремя.
    """
    await db.save_player(make_player(1, rating=1000))
    for number in range(3):
        at = WEEK + number * WEEK_SECONDS + 3600
        await many_fights(db, 1, QUALIFY, outcome="win", delta=10, at=at)

    # Возвращение на четвёртой неделе
    back = WEEK + 3 * WEEK_SECONDS + 3600
    said = await settle_due(db, back)

    assert len(said) == 3, said
    for number in range(3):
        period = week_key(WEEK + number * WEEK_SECONDS)
        row = await db.place_of(period, 1)
        assert row is not None, period
        assert row["place"] == 1
        # ELO растёт от недели к неделе: каждая закрыта по своей границе
        assert row["rating"] == 1000 + 10 * QUALIFY * (number + 1), period
    assert (await db.get_player(1)).credits == prize_for(1) * 3

    # Второй заход не находит ничего
    assert await settle_due(db, back) == []


async def test_a_week_without_fights_is_skipped_without_a_trace(db):
    """В неделе, в которой не дрались, закрывать нечего.

    Строки о ней нет нарочно: пустой снимок говорил бы, что период
    посчитан, а считать в нём было нечего.
    """
    await db.save_player(make_player(1, rating=1000))
    await many_fights(db, 1, QUALIFY, outcome="draw", at=WEEK + 3600)
    # Вторую неделю пропускаем, в третьей снова дерутся
    third = WEEK + 2 * WEEK_SECONDS + 3600
    await many_fights(db, 1, QUALIFY, outcome="draw", at=third)

    await settle_due(db, WEEK + 3 * WEEK_SECONDS + 60)

    assert await db.settled_week(week_key(WEEK)) is not None
    assert await db.settled_week(week_key(WEEK + WEEK_SECONDS)) is None
    assert await db.settled_week(week_key(third)) is not None


async def test_the_search_for_unsettled_weeks_starts_from_the_last_closed(db):
    """Посчитанные недели заново не перебираются.

    Иначе каждый взгляд на рейтинг просматривал бы журнал с начала
    времён — а зовут этот проход с каждым открытием экрана.
    """
    await db.save_player(make_player(1, rating=1000))
    await many_fights(db, 1, QUALIFY, outcome="draw", at=WEEK + 3600)
    assert await db.last_closed_week() == 0

    await settle_due(db, NEXT + 60)
    assert await db.last_closed_week() == NEXT
    assert await db.first_logged_fight(NEXT, NEXT + WEEK_SECONDS) is None


# ---------- снимок как учётная запись ----------


async def test_the_snapshot_keeps_the_grounds_the_period_and_the_place(db):
    """Снимок и есть учётная запись выплаты.

    Отдельного журнала наличных в игре нет нарочно — наличные ходят из
    рук в руки и следа не оставляют, — поэтому основание, период и место
    хранит сама строка снимка. В ней больше, чем было бы в строке
    журнала: ещё и ELO, с которым закрыли, и вся недельная статистика.
    """
    from bot.game.ladder import PAYOUT_REASON

    await db.save_player(make_player(1, rating=1777))
    await many_fights(db, 1, QUALIFY, outcome="win", delta=0)

    await settle(db, "2026-11-08", NEXT + 60)

    row = await db.place_of("2026-11-08", 1)
    assert row["period"] == "2026-11-08"
    assert row["place"] == 1
    assert row["prize"] == prize_for(1)
    assert row["rating"] == 1777
    assert row["fights"] == QUALIFY and row["wins"] == QUALIFY
    assert row["paid_at"] == NEXT + 60
    assert PAYOUT_REASON == "Недельный рейтинг"

    week = await db.settled_week("2026-11-08")
    assert week["opened_at"] == WEEK and week["closed_at"] == NEXT
    assert week["settled_at"] == NEXT + 60


async def test_the_week_key_is_the_sunday_that_opened_it(db):
    """Ключ периода — дата его воскресенья: по нему ищут глазами."""
    assert week_key(MIDWEEK) == "2026-11-08"
    assert week_opens(MIDWEEK) == WEEK
