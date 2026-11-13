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

    from tests.test_turf import patrolled

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW)
    # Городская точка держится патрульным: без него обзор вернёт её
    # городу в тот же миг
    await patrolled(db, "casino", user_id=88, when=NOW)
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


async def test_the_endurance_potion_is_bought_back(db):
    """Снятый эликсир возвращают деньгами, а не отнимают молча.

    За снятое с прилавка платить игроку не за что: склянка уходит из
    рюкзака, а её цена — обратно в карман.
    """
    from tests.test_robbery import make_player

    one = make_player(78, "Боец")
    one.credits = 1_000
    await db.save_player(one)
    # Склянку кладём мимо `add_potion`: кода её в игре уже нет
    await db.conn.execute(
        "INSERT INTO potions (user_id, code, count) VALUES (?,?,?)",
        (78, chores.ENDURANCE_POTION, 3),
    )
    await db.conn.execute(
        "INSERT INTO effects (user_id, code, until) VALUES (?,?,?)",
        (78, chores.ENDURANCE_POTION, NOW + 3600),
    )
    await db.conn.commit()

    done = await chores.run(db, now_ts())
    assert any("эликсир выносливости снят" in line for line in done)

    paid = await db.get_player(78)
    assert paid.credits == 1_000 + 3 * chores.ENDURANCE_PRICE
    assert await db.hands_of_potion(chores.ENDURANCE_POTION) == {}
    assert await db.list_effects(78) == []

    # И второй заход ничего не возвращает: работа сделана один раз
    assert await chores.run(db, now_ts()) == []


async def test_the_old_weekly_licences_carry_over(db):
    """Недогулянные недельные лицензии становятся бессрочными.

    За них заплачено по старому правилу, и сжигать их заодно с ним
    было бы вторым наказанием ни за что.
    """
    from tests.test_robbery import make_player

    one = make_player(79, "Мафиози")
    await db.save_player(one)
    await db.conn.execute(
        "INSERT INTO rob_licences (user_id, week, bought, spent) "
        "VALUES (?,?,?,?)", (79, 1, 10, 4),
    )
    await db.conn.execute(
        "INSERT INTO rob_licences (user_id, week, bought, spent) "
        "VALUES (?,?,?,?)", (79, 2, 3, 3),
    )
    await db.conn.commit()

    done = await chores.run(db, now_ts())
    assert any("лицензии перенесены" in line for line in done)

    left, bought = await db.licences(79)
    assert left == 6, "шесть неистраченных и ни одной сгоревшей"
    assert bought == 0, "пачек по новому правилу он ещё не покупал"
    assert await chores.run(db, now_ts()) == []


async def test_the_mark_is_set_and_checked_in_one_breath(db):
    """Отметка и проверка — один запрос: два захода подряд не считаются."""
    assert await db.chore_done(chores.WIPE_TURF) is False
    assert await db.mark_chore(chores.WIPE_TURF, NOW) is True
    assert await db.mark_chore(chores.WIPE_TURF, NOW) is False
    assert await db.chore_done(chores.WIPE_TURF) is True


async def test_the_quest_journal_is_wiped_for_everyone(db):
    """Журнал заданий сбрасывается целиком: и взятое, и сданное.

    Перенос на главы оставил отметки первой версии как есть, а звались
    они иначе, и новые условия своего в них не находят. Чинить
    поштучно нечего — журнал снимается весь.
    """
    from tests.test_robbery import make_player

    one = make_player(81, "Старожил")
    two = make_player(82, "Новичок")
    await db.save_player(one)
    await db.save_player(two)
    # На руках зависшее: бой пройден, но отметка о нём от первой версии
    await db.take_quest(81, "ring", NOW)
    await db.mark_quest(81, "ring", "fight")
    # И сданное — его сносим тоже
    await db.take_quest(81, "pro", NOW)
    await db.finish_quest(81, "pro", NOW)
    await db.take_quest(82, "ring", NOW)

    done = await chores.run(db, now_ts())
    assert any("журнал заданий бармена сброшен" in line for line in done)
    assert any("снято записей 3" in line for line in done)

    assert await db.quests_of(81) == [], "не осталось ни взятого, ни сданного"
    assert await db.quests_of(82) == []
    # И второй заход ничего не сносит: работа делается один раз, иначе
    # перезапуск бота отбирал бы у игроков дневную работу
    await db.take_quest(81, "ring", NOW)
    assert await chores.run(db, now_ts()) == []
    assert len(await db.quests_of(81)) == 1


async def test_after_the_reset_the_bartender_has_work_again(db):
    """Смысл сброса: бармен снова предлагает дело, а не ждёт зря.

    До него у бойца на руках висит задание со старой отметкой: ринг
    пройден, условие своей отметки не видит, и бармен бесконечно
    напоминает про то, что сделано. После — предлагает первое заново.
    """
    from bot.quest_service import journal
    from bot.webapp.bar import talk_node

    from tests.test_robbery import make_player

    stuck = make_player(83, "Застрявший")
    await db.save_player(stuck)
    await db.take_quest(83, "ring", NOW)
    await db.mark_quest(83, "ring", "fight")
    await db.mark_quest(83, "ring", "old:1")

    before = talk_node(stuck, await journal(db, stuck), NOW)
    assert before["kind"] == "wait", "бармен ждёт боя, который уже был"

    await chores.run(db, now_ts())

    after = talk_node(stuck, await journal(db, stuck), NOW)
    assert after["kind"] == "offer", "дело снова можно взять"
    assert after["quest"]["code"] == "ring"
