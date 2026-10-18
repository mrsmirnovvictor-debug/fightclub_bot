"""Разовые работы по живой базе: делаются один раз и не повторяются.

Миграции правят форму, эти работы — данные. Их опасность ровно одна:
повториться при перезапуске бота и снести то, что игроки успели нажить
после первого захода. Это здесь и проверяется.
"""

from __future__ import annotations

from bot import chores
from bot.game.health import now_ts
from tests.test_robbery import NOW, family, stand


async def test_the_wipe_clears_the_city_and_the_hired(db):
    """Сброс города: владельцы сняты, выставленные распущены."""
    from bot.hire_service import hire, post

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW)
    await db.take_turf("casino", "service", "police", NOW)
    await stand(db, [don.user_id], "bar")
    rich = await db.get_player(don.user_id)
    rich.credits = 10_000
    await db.save_player(rich)
    await hire(db, await db.get_player(don.user_id), "merc_5", now=NOW)
    taken = (await db.hirelings_of(don.user_id))[0]
    await post(db, await db.get_player(don.user_id), int(taken["id"]), "bar",
               now=NOW)

    assert len(await db.turf_rows()) == 2
    assert len(await db.hirelings_of(don.user_id)) == 1

    done = await chores.run(db, now_ts())
    assert done and "город сброшен" in done[0]
    assert await db.turf_rows() == {}
    assert await db.hirelings_of(don.user_id) == []


async def test_the_chore_runs_once_and_not_again(db):
    """Второй заход ничего не трогает: бот перезапускается часто."""
    don, _, clan = await family(db)
    assert await chores.run(db, now_ts())

    # После сброса семья берёт точку заново — и новый запуск её не тронет
    await db.take_turf("bar", "clan", str(clan.id), NOW)
    assert await chores.run(db, now_ts()) == []
    assert len(await db.turf_rows()) == 1


async def test_ragnar_takes_the_chief_seat_with_his_diploma(db):
    """Разовая посадка в кресло начальника: с бумагой и с места."""
    from tests.test_robbery import make_player

    ragnar = make_player(77, "Ragnar")
    ragnar.job_code = "bartender"
    ragnar.job_since = NOW
    await db.save_player(ragnar)

    done = await chores.run(db, now_ts())
    assert any("начальник полиции" in one for one in done)

    seated = await db.get_player(77)
    assert seated.job_code == "police_chief"
    assert "police" in seated.diplomas, "без бумаги начальник выглядел бы ошибкой"
    assert list(await db.taken_jobs()) == ["police_chief"]


async def test_the_chief_seat_waits_if_there_is_no_ragnar(db):
    """Бойца с таким прозвищем нет — работа проходит молча и один раз."""
    done = await chores.run(db, now_ts())
    assert any("не найден" in one for one in done)
    assert await chores.run(db, now_ts()) == []


async def test_the_mark_is_set_and_checked_in_one_breath(db):
    """Отметка и проверка — один запрос: два захода подряд не считаются."""
    assert await db.chore_done(chores.WIPE_TURF) is False
    assert await db.mark_chore(chores.WIPE_TURF, NOW) is True
    assert await db.mark_chore(chores.WIPE_TURF, NOW) is False
    assert await db.chore_done(chores.WIPE_TURF) is True
