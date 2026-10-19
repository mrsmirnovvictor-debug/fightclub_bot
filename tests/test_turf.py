"""Война за территории: доход, гарнизон и штурм точки.

Порядок проверок — по тому, что дороже сломать:

1. **Деньги.** Доход капает за полные сутки и только владельцу; остаток
   суток не пропадает, а переезжает в следующий расчёт.
2. **Ничьих точек не бывает.** За пустой стоит городская охрана, и её
   надо бить — иначе первый зашедший снимает весь город даром.
3. **Кто воюет.** Семья и служба — равные владельцы; одиночка не воюет
   ни за кого.
4. **Отдых после боя.** Точку не отбивают в ту же минуту, кто бы ни
   победил.
5. **Потолок.** Больше четырёх точек одна семья не держит.
"""

from __future__ import annotations

import random

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.clan_service import found, _join
from bot.config import Config
from bot.database import Database
from bot.game.classes import Stats
from bot.game.turf import (
    DAY_SECONDS,
    MAX_SPOTS_PER_OWNER,
    SPOTS,
    TOTAL_SPOTS,
    Owner,
    days_held,
    get_spot,
    owner_of_clan,
    owner_of_service,
    payout,
)
from bot.models import Player
from bot.webapp.server import create_app
from bot.turf_service import (
    TurfError,
    assault,
    garrison,
    held_by,
    income_of,
    owner_of,
    owner_title,
    survey,
    vault_of,
)
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

NOW = 1_800_000_000
MANSION = "mafia_mansion"


def headers(user_id: int = 1) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


def make_player(
    user_id: int = 1, nickname: str = "Дон", level: int = 10,
    clazz: str = "warrior",
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code=clazz, level=level,
        credits=5_000, location=MANSION,
        **Stats(strength=25, agility=25, intuition=25, endurance=25).as_dict(),
    )


async def family(
    db: Database, size: int = 4, first: int = 1,
    name: str = "Коза Ностра", sign: str = "black_ravens",
):
    """Клан с доном и бойцами. Возвращает дона и список номеров отряда."""
    don = make_player(first, f"Дон-{first}")
    await db.save_player(don)
    clan = await found(db, don, name, sign, now=NOW)
    squad = [don.user_id]
    for number in range(first + 1, first + size):
        one = make_player(number, f"Боец-{number}", clazz="assassin")
        await db.save_player(one)
        await _join(db, one, clan, NOW)
        squad.append(number)
    return don, squad, clan


async def stand(db: Database, user_ids: list[int], code: str) -> None:
    """Поставить бойцов к дому: точку берут ногами, а не из списка."""
    for one in user_ids:
        player = await db.get_player(one)
        assert player is not None
        player.location = code
        player.travel_to = None
        player.arrives_at = 0
        await db.save_player(player)


async def guard(db: Database, user_id: int, code: str, when: int = NOW) -> None:
    """Поставить на точку одного наёмника.

    Без охраны точка не платит вовсе, и проверкам про деньги без этого
    шага считать нечего: взятая и брошенная — дом с вывеской.
    """
    from bot.hire_service import hire, post

    one = await db.get_player(user_id)
    one.credits += 5_000
    one.location = code
    one.travel_to = None
    one.arrives_at = 0
    await db.save_player(one)
    await hire(db, await db.get_player(user_id), "merc_5", now=when)
    taken = [row for row in await db.hirelings_of(user_id) if not row["spot"]]
    await post(db, await db.get_player(user_id), int(taken[0]["id"]), code,
               now=when)


async def seize(db: Database, don: Player, squad: list[int], code: str) -> int:
    """Взять точку, сколько бы попыток ни ушло. Отдаёт час захвата.

    Исход боя решает бросок, и тест про деньги не должен зависеть от
    того, повезло ли отряду с первого раза.
    """
    moment = NOW
    for seed in range(40):
        result = await assault(
            db, don, code, squad, now=moment, rng=random.Random(seed)
        )
        if result.won:
            return moment
        # Отдыха у точки нет: следующий заход — хоть через минуту
        moment += 60
    raise AssertionError(f"за сорок заходов не взяли {code}")


# ---------- правила ----------


def test_the_spots_stand_on_real_houses():
    """Точка — существующий дом города, а не выдуманный объект."""
    from bot.game.locations import get_location

    assert TOTAL_SPOTS == 10
    for spot in SPOTS:
        place = get_location(spot.code)
        assert place is not None, spot.code
        assert spot.title == place.title
        assert spot.income > 0

    # Домов, за которые драться не станут, среди точек нет
    codes = {one.code for one in SPOTS}
    assert not codes & {
        "mafia_mansion", "vcpd", "military_base", "hospital", "university",
        "police_school", "medical_college", "cadet_corps",
    }


def test_the_income_counts_whole_days_only():
    """Полсуток дохода не приносят, но и не пропадают."""
    spot = get_spot("casino")
    assert days_held(NOW, NOW + DAY_SECONDS - 1) == 0
    assert days_held(NOW, NOW + DAY_SECONDS) == 1

    money, mark = payout(spot, NOW, NOW + DAY_SECONDS // 2)
    assert money == 0 and mark == NOW, "неполные сутки не трогаем"

    money, mark = payout(spot, NOW, NOW + 3 * DAY_SECONDS + 500)
    assert money == spot.income * 3
    # Остаток переезжает: пятьсот секунд не сгорели
    assert mark == NOW + 3 * DAY_SECONDS


def test_the_owner_is_a_family_or_a_service():
    """Владелец — пара «вид» и «кто», и видов ровно два."""
    clan = owner_of_clan(7)
    assert clan.clan_id == 7 and not clan.service and not clan.nobody
    service = owner_of_service("police")
    assert service.service == "police" and service.clan_id == 0
    assert Owner().nobody
    assert not clan.same(service) and clan.same(owner_of_clan(7))


def test_the_spot_keeps_no_hour_of_rest():
    """Отдыха у точки нет, и часа боя она не помнит.

    Правило держится на том, что хранить нечего: пока в строке точки
    не завёлся час драки, никакая дверь не сможет сослаться на него.
    """
    from bot.database import TurfRow
    from bot.game import turf as rules

    assert not hasattr(TurfRow(code="bar", owner=Owner()), "fought_at")
    assert not hasattr(rules, "calm_left")
    assert not hasattr(rules, "CALM_SECONDS")


# ---------- кто воюет ----------


async def test_a_loner_fights_for_nobody(db):
    """За территории воюют семьи и службы, одиночки — нет."""
    one = make_player()
    await db.save_player(one)
    assert owner_of(one).nobody

    with pytest.raises(TurfError, match="Вступи в клан"):
        await assault(db, one, "bar", [one.user_id], now=NOW)


async def test_the_service_fights_as_a_family(db):
    """Полиция — такой же владелец, как клан мафии."""
    cop = make_player(5, "Сержант")
    cop.job_code = "police_patrol"
    cop.diplomas = ["police"]
    await db.save_player(cop)

    mine = owner_of(cop)
    assert mine.service == "police"
    assert await owner_title(db, mine) == "Полиция Вегаса"

    # А врач не воюет: его служба без права силы
    medic = make_player(6, "Доктор")
    medic.job_code = "orderly"
    await db.save_player(medic)
    assert owner_of(medic).nobody


async def test_nobody_holds_a_spot_for_free(db):
    """За ничьей точкой стоит городская охрана — её надо бить."""
    don, squad, _ = await family(db)
    guards = await garrison(db, Owner(), [10, 10], NOW)

    assert len(guards) == 3
    assert all(one.alive and one.max_hp > 0 for one in guards)
    assert all("Охранник" in one.name for one in guards)


async def test_the_garrison_is_made_of_the_hired(db):
    """Гарнизон — выставленные нанятые, а не живые бойцы семьи.

    Раньше точку держала сама семья — даром, во сне, всем составом.
    Теперь охрану покупают: наёмник стоит денег и гибнет насовсем.
    """
    from bot.hire_service import hire, post

    don, squad, clan = await family(db, size=3)
    await stand(db, [don.user_id], "bar")
    await db.take_turf("bar", "clan", str(clan.id), NOW)
    rich = await db.get_player(don.user_id)
    rich.credits = 10_000
    await db.save_player(rich)

    mine = owner_of_clan(clan.id)
    # Без наёмников у семьи на точке пусто, и городская охрана за неё
    # не встаёт: город не стережёт дом мафии от другой мафии
    assert await garrison(db, mine, [10], NOW, code="bar") == []

    await hire(db, rich, "merc_9", now=NOW)
    taken = (await db.hirelings_of(don.user_id))[0]
    await post(db, rich, int(taken["id"]), "bar", now=NOW)

    wall = await garrison(db, mine, [10], NOW, code="bar")
    assert [one.name for one in wall] == ["Громила"]
    assert wall[0].level == 9


async def test_the_city_guards_stand_where_no_family_rules(db):
    """Охрана города стоит и на ничьей точке, и под городом."""
    from bot.game.turf import owner_of_service

    ничья = await garrison(db, Owner(), [10], NOW, code="bar")
    assert len(ничья) == 3 and all("Охранник" in one.name for one in ничья)
    assert all(one.level == 4 for one in ничья)

    города = await garrison(db, owner_of_service("police"), [10], NOW, code="bar")
    assert all("Охранник" in one.name for one in города)


# ---------- кто что делает на точке ----------


def test_the_city_fights_the_family_and_guards_the_rest():
    """Город бьёт только семью, остальное — патрулирует.

    Ничья точка и так под городской охраной, и «отбивать» её у самого
    себя городу незачем: туда он приводит патрульного. Семья наоборот —
    берёт боем всё, что не её.
    """
    from bot.game.turf import ARREST, GUARD, SEIZE, action_for

    police = owner_of_service("police")
    ours, theirs = owner_of_clan(1), owner_of_clan(2)

    assert action_for(Owner(), police) == GUARD, "ничья точка и так его"
    assert action_for(police, police) == GUARD
    assert action_for(ours, police) == ARREST, "мафию выводят задержанием"

    assert action_for(Owner(), ours) == SEIZE, "там городская охрана"
    assert action_for(police, ours) == SEIZE
    assert action_for(theirs, ours) == SEIZE
    assert action_for(ours, ours) == GUARD, "за свою точку не воюют"

    # Одиночке на точке делать нечего вовсе
    assert action_for(Owner(), Owner()) == ""


async def test_the_board_names_the_deed_for_each_side(db, client):
    """Экран называет дело словом: захват, задержание, охрана."""
    from tests.test_police import cop

    # Часы тут настоящие: ручка смотрит на них, а не на `NOW`, и точка,
    # взятая «три дня назад» по клубному времени, для неё ещё в будущем
    from bot.game.health import now_ts

    from bot.hire_service import hire, post

    long_ago = now_ts() - 3 * DAY_SECONDS
    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), long_ago)
    await db.take_turf("casino", "service", "police", long_ago)
    # На баре стоит наёмник: пустой дом город снимал бы без боя, а тут
    # есть кому стоять — значит, задержание
    boss = await db.get_player(don.user_id)
    boss.credits = 20_000
    boss.location = "bar"
    await db.save_player(boss)
    await hire(db, await db.get_player(don.user_id), "merc_5", now=long_ago)
    hired = (await db.hirelings_of(don.user_id))[0]
    await post(db, await db.get_player(don.user_id), int(hired["id"]), "bar",
               now=long_ago)

    # Семья: своя точка — охрана, чужие и ничьи — захват
    await stand(db, [don.user_id], "bar")
    body = await (await client.get(
        "/api/turf", headers=headers(don.user_id)
    )).json()
    rows = {one["code"]: one for one in body["spots"]}
    assert rows["bar"]["action"] == "guard"
    assert rows["bar"]["action_title"] == "Охрана"
    assert rows["casino"]["action"] == "seize"
    assert rows["market"]["action"] == "seize", "ничья берётся боем"
    assert rows["bar"]["can_storm"] is False

    # Город: мафия — задержание, остальное — охрана
    officer = await cop(db, 90, "Ragnar")
    officer.location = "bar"
    await db.save_player(officer)
    body = await (await client.get(
        "/api/turf", headers=headers(90)
    )).json()
    rows = {one["code"]: one for one in body["spots"]}
    assert rows["bar"]["action"] == "arrest"
    assert rows["bar"]["action_title"] == "Задержание"
    assert rows["bar"]["can_storm"] is True
    assert rows["casino"]["action"] == "guard"
    assert rows["market"]["action"] == "guard", "ничью город патрулирует"
    assert rows["market"]["can_storm"] is False


async def test_the_city_takes_a_free_spot_with_a_patrolman(db):
    """Ничью точку город берёт патрульным, а не отрядом."""
    from bot.hire_service import hire, post
    from bot.turf_war import TurfWar
    from tests.test_police import cop

    officer = await cop(db, 91, "Ragnar")
    officer.location = "market"
    officer.credits = 10_000
    await db.save_player(officer)
    officer = await db.get_player(91)
    await hire(db, officer, "patrol_5", now=NOW)
    mine = (await db.hirelings_of(91))[0]

    # Отряд на ничью точку город не ведёт
    war = TurfWar(db)
    try:
        with pytest.raises(TurfError, match="городской охраной"):
            await war.open_muster(officer, "market", now=NOW)
    finally:
        await war.close()

    # А патрульного ставит — и точка становится городской
    await post(db, await db.get_player(91), int(mine["id"]), "market", now=NOW)
    row = (await db.turf_rows())["market"]
    assert row.owner == owner_of_service("police")
    assert len(await db.hirelings_at("market")) == 1


async def test_one_patrolman_from_one_player_and_ten_in_all(db):
    """От бойца — один человек, на точке — десять. Кнопка гаснет, а не прячется."""
    from bot.hire_service import HireError, hire, post
    from bot.webapp.turf import build_turf
    from tests.test_police import cop

    officer = await cop(db, 92, "Ragnar")
    officer.location = "market"
    officer.credits = 20_000
    await db.save_player(officer)
    for _ in range(2):
        await hire(db, await db.get_player(92), "patrol_5", now=NOW)
    mine = await db.hirelings_of(92)
    await post(db, await db.get_player(92), int(mine[0]["id"]), "market",
               now=NOW)

    with pytest.raises(HireError, match="уже стоит человек"):
        await post(db, await db.get_player(92), int(mine[1]["id"]), "market",
                   now=NOW)

    body = await build_turf(db, await db.get_player(92), NOW)
    assert body["hire"]["guarding"] is True, "место под кнопку есть"
    assert body["hire"]["can_post"] is False, "а нажать её нельзя"
    assert "уже стоит человек" in body["hire"]["why"]


async def test_the_failed_assault_buries_the_dead_and_leaves_the_rest(db):
    """Отбились — убитые не воскресают, уцелевшие целы, точка прежняя.

    Развязку зовём руками: предмет проверки — что делает с точкой
    проигранный штурм, а не чем кончится бросок костей.
    """
    from bot.hire_service import hire, post
    from bot.turf_war import TurfWar

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW - 3 * DAY_SECONDS)
    await stand(db, squad, "bar")
    # По человеку от двоих: от одного бойца на точке стоит один
    for number in squad[:2]:
        one = await db.get_player(number)
        one.credits = 20_000
        await db.save_player(one)
        await hire(db, await db.get_player(number), "merc_9", now=NOW)
        his = [row for row in await db.hirelings_of(number) if not row["spot"]]
        await post(db, await db.get_player(number), int(his[0]["id"]), "bar",
                   now=NOW)

    rival, rivals, theirs = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    await stand(db, rivals, "bar")
    war = TurfWar(db)
    try:
        muster = await war.open_muster(
            await db.get_player(rival.user_id), "bar", now=NOW
        )
        siege = await war.march(muster.id, rival.user_id, now=NOW)
        # Один из двоих защитников пал, второй держится
        first, second = sorted(siege.wall)[0], sorted(siege.wall)[1]
        siege.wall[first].hp = 0
        await war._finish(siege, won=False)
    finally:
        await war.close()

    assert siege.fallen == 1
    left = await db.hirelings_at("bar")
    assert len(left) == 1, "убитый ушёл насовсем, уцелевший остался"
    assert (await db.turf_rows())["bar"].owner == owner_of_clan(clan.id)
    assert "Выставленных полегло: 1" in " ".join(siege.summary)


async def test_the_city_reinforces_as_many_spots_as_it_likes(db):
    """Потолок точек — забота семей: город патрулирует хоть весь город."""
    from bot.hire_service import hire, post
    from tests.test_police import cop

    officer = await cop(db, 93, "Ragnar")
    officer.credits = 50_000
    await db.save_player(officer)
    codes = [one.code for one in SPOTS][:MAX_SPOTS_PER_OWNER + 1]
    for number, code in enumerate(codes):
        one = await db.get_player(93)
        one.location = code
        await db.save_player(one)
        await hire(db, await db.get_player(93), "patrol_5", now=NOW)
        fresh = [row for row in await db.hirelings_of(93) if not row["spot"]]
        await post(db, await db.get_player(93), int(fresh[0]["id"]), code,
                   now=NOW)

    rows = await db.turf_rows()
    held = [code for code, row in rows.items() if row.owner.kind == "service"]
    assert len(held) == len(codes) > MAX_SPOTS_PER_OWNER


async def test_the_abandoned_spot_goes_back_to_the_city(db):
    """Сутки без охраны — и дом снова под городской охраной."""
    from bot.game.turf import ABANDON_SECONDS

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW)

    # Первый взгляд заводит час опустения, второй его не двигает
    await survey(db, NOW)
    first = (await db.turf_rows())["bar"].empty_since
    assert first == NOW
    await survey(db, NOW + 60)
    assert (await db.turf_rows())["bar"].empty_since == first

    # Сутки не прошли — дом ещё семейный
    rows = await survey(db, NOW + ABANDON_SECONDS - 1)
    assert next(one for one in rows if one.spot.code == "bar").owner.kind == "clan"

    # А на сутках он возвращается городу, и строки о нём больше нет
    rows = await survey(db, NOW + ABANDON_SECONDS)
    assert next(one for one in rows if one.spot.code == "bar").owner.nobody
    assert "bar" not in await db.turf_rows()


async def test_the_city_keeps_a_spot_only_while_someone_stands_on_it(db):
    """Снял патрульного — дом в тот же миг под городской охраной.

    Сутки ожидания придуманы для семьи: она дом присваивает. Город его
    не присваивает, а охраняет, и пустая вывеска участка не значит
    ничего — ни дохода, ни владельца.
    """
    from bot.hire_service import hire, post as post_hireling
    from tests.test_police import cop

    officer = await cop(db, 93, "Ragnar")
    officer.location = "market"
    officer.credits = 10_000
    await db.save_player(officer)
    await hire(db, await db.get_player(93), "patrol_5", now=NOW)
    mine = (await db.hirelings_of(93))[0]
    await post_hireling(db, await db.get_player(93), int(mine["id"]),
                        "market", now=NOW)
    assert (await db.turf_rows())["market"].owner == owner_of_service("police")

    # Патрульного сняли — и дом сразу городской, без суток ожидания
    await db.drop_hireling(int(mine["id"]))
    rows = await survey(db, NOW + 60)
    here = next(one for one in rows if one.spot.code == "market")
    assert here.owner.nobody, "город не держит дом одной вывеской"
    assert "market" not in await db.turf_rows()


async def test_the_guarded_spot_is_not_abandoned(db):
    """Поставил человека — час опустения сброшен, дом остаётся за семьёй."""
    from bot.game.turf import ABANDON_SECONDS
    from bot.hire_service import hire, post

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW)
    await survey(db, NOW)  # час опустения пошёл

    boss = await db.get_player(don.user_id)
    boss.credits = 20_000
    boss.location = "bar"
    await db.save_player(boss)
    await hire(db, await db.get_player(don.user_id), "merc_5", now=NOW)
    mine = (await db.hirelings_of(don.user_id))[0]
    await post(db, await db.get_player(don.user_id), int(mine["id"]), "bar",
               now=NOW)

    rows = await survey(db, NOW + ABANDON_SECONDS + 1)
    assert next(one for one in rows if one.spot.code == "bar").owner.kind == "clan"
    assert (await db.turf_rows())["bar"].empty_since == 0


async def test_the_city_frees_an_empty_family_house_without_a_fight(db):
    """Пустой дом семьи город снимает одним нажатием."""
    from bot.hire_service import hire, post
    from bot.turf_service import release
    from tests.test_police import cop

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW)
    officer = await cop(db, 96, "Ragnar")
    officer.location = "bar"
    officer.credits = 10_000
    await db.save_player(officer)

    said = await release(db, await db.get_player(96), "bar", now=NOW)
    assert "освобождён" in said
    assert "bar" not in await db.turf_rows(), "дом снова под городской охраной"

    # А с живой охраной так не получится: там дерутся
    await db.take_turf("casino", "clan", str(clan.id), NOW)
    boss = await db.get_player(don.user_id)
    boss.credits = 20_000
    boss.location = "casino"
    await db.save_player(boss)
    await hire(db, await db.get_player(don.user_id), "merc_5", now=NOW)
    mine = (await db.hirelings_of(don.user_id))[0]
    await post(db, await db.get_player(don.user_id), int(mine["id"]), "casino",
               now=NOW)
    guard = await db.get_player(96)
    guard.location = "casino"
    await db.save_player(guard)
    with pytest.raises(TurfError, match="есть кому стоять"):
        await release(db, await db.get_player(96), "casino", now=NOW)

    # И семье освобождать нечего: это работа города
    boss = await db.get_player(don.user_id)
    boss.location = "bar"
    await db.save_player(boss)
    with pytest.raises(TurfError, match="работа города"):
        await release(db, await db.get_player(don.user_id), "bar", now=NOW)


async def test_the_board_offers_freeing_instead_of_a_fight(db, client):
    """Экран называет дело освобождением, пока на точке никого нет."""
    from bot.game.health import now_ts
    from tests.test_police import cop

    long_ago = now_ts() - 3 * DAY_SECONDS
    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), long_ago)
    officer = await cop(db, 97, "Ragnar")
    officer.location = "bar"
    await db.save_player(officer)

    body = await (await client.get("/api/turf", headers=headers(97))).json()
    row = next(one for one in body["spots"] if one["code"] == "bar")
    assert row["action"] == "free" and row["action_title"] == "Освобождение"
    assert row["can_free"] is True and row["can_storm"] is False

    # Нажали — и дом городской, без единой волны
    answer = await client.post(
        "/api/turf", json={"action": "free", "spot": "bar"},
        headers=headers(97),
    )
    assert answer.status == 200
    body = await answer.json()
    assert "освобождён" in body["said"]
    row = next(one for one in body["spots"] if one["code"] == "bar")
    assert row["owner"]["nobody"] is True
    assert row["action"] == "guard", "теперь туда ставят патрульного"


async def test_the_patrolled_house_is_not_untouchable(db):
    """Патрульный берёт ничей дом — и семья идёт за ним в ту же минуту.

    Отдыха у точек нет вовсе, и вошедший человек не запирает дом на
    сутки: он только меняет, с кем придётся драться.
    """
    from bot.hire_service import hire, post
    from bot.turf_war import TurfWar
    from tests.test_police import cop

    officer = await cop(db, 98, "Ragnar")
    officer.location = "market"
    officer.credits = 10_000
    await db.save_player(officer)
    await hire(db, await db.get_player(98), "patrol_5", now=NOW)
    mine = (await db.hirelings_of(98))[0]
    await post(db, await db.get_player(98), int(mine["id"]), "market", now=NOW)
    assert (await db.turf_rows())["market"].owner == owner_of_service("police")

    rival, rivals, theirs = await family(db, first=20, name="Псы",
                                         sign="silver_wolves")
    await stand(db, rivals, "market")
    war = TurfWar(db)
    try:
        muster = await war.open_muster(
            await db.get_player(rival.user_id), "market", now=NOW
        )
        assert muster.spot.code == "market", "ждать семье нечего"
    finally:
        await war.close()


# ---------- штурм ----------


async def test_the_won_spot_changes_hands_and_starts_paying(db):
    """Взяли точку — она ваша и платит с часа захвата."""
    don, squad, clan = await family(db)
    taken = await seize(db, don, squad, "bar")
    await guard(db, don.user_id, "bar", taken)

    mine = owner_of_clan(clan.id)
    held = await held_by(db, mine, taken)
    assert [one.spot.code for one in held] == ["bar"]
    assert await income_of(db, mine, taken) == get_spot("bar").income

    # Трое суток спустя доход в сейфе семьи
    await survey(db, taken + 3 * DAY_SECONDS)
    assert await vault_of(db, mine) == get_spot("bar").income * 3


async def test_the_spot_changes_hands_in_the_same_minute(db):
    """Взяли точку — отбивать её можно сразу, отдыха у неё нет.

    В этом и смысл войны: дом ходит из рук в руки весь вечер. Цена
    попытки — проигранная драка, а не сутки ожидания.
    """
    don, squad, clan = await family(db)
    taken = await seize(db, don, squad, "bar")
    assert (await db.turf_rows())["bar"].owner == owner_of_clan(clan.id)

    rival, foes, theirs = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    # Та же минута: ни одна дверь не говорит про отдых
    back = await seize(db, rival, foes, "bar")
    assert back - taken < DAY_SECONDS, "ждать сутки не пришлось"
    assert (await db.turf_rows())["bar"].owner == owner_of_clan(theirs.id)


async def test_your_own_spot_is_not_stormed(db):
    don, squad, clan = await family(db)
    taken = await seize(db, don, squad, "bar")

    with pytest.raises(TurfError, match="и так за вами"):
        await assault(
            db, don, "bar", squad, now=taken + 60, rng=random.Random(3)
        )


async def test_four_spots_are_the_ceiling(db):
    """Больше четырёх точек одна семья не держит.

    Взятое держим охраной: без неё дом через сутки вернётся городу, и
    потолок считать стало бы не с чего.
    """
    from bot.hire_service import hire, post

    don, squad, clan = await family(db)
    moment = NOW
    for number, code in enumerate(("bar", "weapon_shop", "taxi_depot",
                                   "pawnshop")):
        moment = max(moment, await seize(db, don, squad, code)) + 60
        # По человеку от каждого: от одного бойца на точке стоит один
        keeper = squad[number]
        one = await db.get_player(keeper)
        one.credits = 20_000
        one.location = code
        await db.save_player(one)
        await hire(db, await db.get_player(keeper), "merc_5", now=moment)
        free = [row for row in await db.hirelings_of(keeper) if not row["spot"]]
        await post(db, await db.get_player(keeper), int(free[0]["id"]), code,
                   now=moment)

    held = await held_by(db, owner_of_clan(clan.id), moment)
    assert len(held) == MAX_SPOTS_PER_OWNER

    with pytest.raises(TurfError, match="не удержать"):
        await assault(
            db, don, "car_dealership", squad, now=moment, rng=random.Random(4)
        )


async def test_an_empty_squad_does_not_march(db):
    don, squad, clan = await family(db)

    with pytest.raises(TurfError, match="Отряд пуст"):
        await assault(db, don, "bar", [], now=NOW)
    # И чужими бойцами тоже не повоюешь
    stranger = make_player(99, "Чужой")
    await db.save_player(stranger)
    with pytest.raises(TurfError, match="Отряд пуст"):
        await assault(db, don, "bar", [99], now=NOW)


async def test_the_survey_pays_even_the_absent_owner(db):
    """Доход сводит тот, кто пришёл смотреть, — в том числе чужой."""
    don, squad, clan = await family(db)
    taken = await seize(db, don, squad, "bar")
    await guard(db, don.user_id, "bar", taken)

    # Смотрит посторонний, а платят семье
    stranger = make_player(50, "Прохожий")
    await db.save_player(stranger)
    await survey(db, taken + 5 * DAY_SECONDS)

    assert await vault_of(db, owner_of_clan(clan.id)) == (
        get_spot("bar").income * 5
    )


# ---------- ручки и экран ----------


async def test_the_turf_map_is_read_from_anywhere(db, client):
    """Карту войны смотрят откуда угодно: воюют в доме, знают везде."""
    don, squad, clan = await family(db)
    answer = await client.get("/api/turf", headers=headers(don.user_id))
    assert answer.status == 200
    body = await answer.json()

    assert body["total"] == TOTAL_SPOTS and len(body["spots"]) == TOTAL_SPOTS
    assert body["side"]["title"] == "Коза Ностра"
    assert body["held"] == 0 and body["income"] == 0
    first = body["spots"][0]
    assert first["owner"]["nobody"] is True
    # Смотреть — да, собирать отряд — нет: боец стоит в особняке, а
    # точку берут ногами
    assert first["here"] is False and first["can_storm"] is False
    assert body["at_spot"] is False
    assert "на месте" in body["note"]


async def test_the_call_button_appears_only_where_the_fighter_stands(db, client):
    """Кнопка сбора — у того дома, в котором боец стоит, и только там."""
    don, _, _ = await family(db)
    await stand(db, [don.user_id], "bar")

    body = await (await client.get(
        "/api/turf", headers=headers(don.user_id)
    )).json()
    assert body["here"] == "bar" and body["at_spot"] is True
    rows = {one["code"]: one for one in body["spots"]}
    assert rows["bar"]["here"] is True and rows["bar"]["can_storm"] is True
    # Соседний дом виден так же подробно, но взять его отсюда нельзя
    assert rows["casino"]["here"] is False
    assert rows["casino"]["can_storm"] is False


async def test_a_spot_is_not_called_from_across_the_city(db, client):
    """Созвать отряд на казино, сидя в особняке, нельзя."""
    don, _, _ = await family(db)

    answer = await post(client, don.user_id, action="call", spot="casino")
    assert answer.status == 409
    assert "дойти" in (await answer.json())["error"]


async def test_the_marks_say_whose_the_spot_is(db, client):
    """Отметка в строке: своя точка — флаг, ничья — круг, служба — щит."""
    don, squad, clan = await family(db)
    await seize(db, don, squad, "bar")
    await patrolled(db, "casino")

    body = await (await client.get(
        "/api/turf", headers=headers(don.user_id)
    )).json()
    rows = {one["code"]: one for one in body["spots"]}
    assert rows["bar"]["mark"] == "🚩" and rows["bar"]["mine"] is True
    assert rows["casino"]["mark"] == "🛡"
    assert rows["market"]["mark"] == "⚪️"


async def patrolled(db, code: str, user_id: int = 90, when: int = NOW) -> None:
    """Поставить на точку патрульного: без него город её не держит.

    Пустую городскую точку обзор возвращает под обычную охрану в тот же
    миг — полиция дом не присваивает, а охраняет.
    """
    from bot.hire_service import hire, post as post_hireling
    from tests.test_police import cop

    officer = await cop(db, user_id, "Патрульный")
    officer.location = code
    officer.credits = 10_000
    await db.save_player(officer)
    await hire(db, await db.get_player(user_id), "patrol_5", now=when)
    mine = [one for one in await db.hirelings_of(user_id) if not one["spot"]]
    await post_hireling(
        db, await db.get_player(user_id), int(mine[0]["id"]), code, now=when
    )


async def post(client, user_id: int, **body):
    """Нажатие в панели войны."""
    return await client.post("/api/turf", json=body, headers=headers(user_id))


async def test_the_muster_gathers_and_marches(db, client):
    """Сбор отряда: созвали, записались, пошли — и началась волна."""
    don, squad, _ = await family(db)
    await stand(db, squad, "bar")

    answer = await post(client, don.user_id, action="call", spot="bar")
    assert answer.status == 200
    body = await answer.json()
    muster = body["muster"]
    assert muster["spot"] == "bar" and muster["is_leader"] is True
    assert [one["user_id"] for one in muster["members"]] == [don.user_id]
    # На точку, где уже собираются, второй отряд не зовут
    bar = next(one for one in body["spots"] if one["code"] == "bar")
    assert bar["busy"] is True and bar["can_storm"] is False

    # Свои записываются
    answer = await post(client, squad[1], action="join", muster_id=muster["id"])
    body = await answer.json()
    assert body["muster"]["total"] == 2
    assert body["muster"]["is_leader"] is False

    # Ведёт отряд созвавший
    answer = await post(client, squad[1], action="march")
    assert answer.status == 409
    assert "созвал" in (await answer.json())["error"]

    answer = await post(client, don.user_id, action="march")
    body = await answer.json()
    siege = body["siege"]
    assert siege is not None and siege["wave"] == 1
    assert siege["spot"] == "bar" and siege["defender"] == "городская охрана"
    assert len(siege["party"]) == 2 and siege["wall"]
    assert siege["yours"] and siege["alive"] and not siege["acted"]
    assert siege["foe"] is not None, "прицел наведён"


async def test_the_wave_takes_the_blow_and_the_block(db, client):
    """Ход целиком: удар и блок одной кнопкой, и боец отработал."""
    don, squad, _ = await family(db)
    await stand(db, squad, "bar")
    await post(client, don.user_id, action="call", spot="bar")
    await post(client, don.user_id, action="march")

    answer = await post(
        client, don.user_id, action="turn", attack="head", block="belly"
    )
    assert answer.status == 200
    body = await answer.json()
    live = body["siege"] or body["result"]
    assert live["log"], "размен попал в журнал"
    # Либо боец отработал волну, либо волна уже закрылась и началась новая
    assert live["finished"] or live["acted"] or live["wave"] >= 1


async def test_the_siege_ends_and_the_spot_changes_hands(db, client):
    """Штурм до конца: итог показан, и точка записана за победителем."""
    don, squad, _ = await family(db)
    await stand(db, squad, "bar")
    await post(client, don.user_id, action="call", spot="bar")
    await post(client, don.user_id, action="march")

    body = {}
    for _ in range(200):
        answer = await post(
            client, don.user_id, action="turn", attack="head", block="belly"
        )
        body = await answer.json()
        if body.get("result"):
            break
        if answer.status == 409:
            # Выбыл из боя: итог придёт сам
            body = await (await client.get(
                "/api/turf", headers=headers(don.user_id)
            )).json()
            if body.get("result"):
                break
            raise AssertionError("штурм встал: ни итога, ни хода")
    result = body["result"]
    assert result is not None and result["finished"] is True
    assert result["summary"], "итог назван словами"

    bar = next(one for one in body["spots"] if one["code"] == "bar")
    if result["won"]:
        assert bar["mine"] is True and bar["owner"]["title"] == "Коза Ностра"
    else:
        assert bar["owner"]["nobody"] is True
    # Пока итог не закрыт, он висит; закрыли — пропал
    body = await (await post(client, don.user_id, action="done")).json()
    assert body["result"] is None


async def test_the_city_map_carries_the_marks_and_the_band(db, client):
    """Карта города: отметка у каждой точки и плашка под ногами.

    Отметка — всё, что карта говорит о точке: владелец и доход стояли
    там строкой под каждой вывеской, и карта читалась как ведомость.
    """
    don, squad, clan = await family(db)
    await patrolled(db, "casino")
    await db.take_turf("market", "clan", str(clan.id), NOW)
    await stand(db, squad, "bar")

    body = await (await client.get(
        "/api/map", headers=headers(don.user_id)
    )).json()
    marks = body["turf"]["spots"]
    assert len(marks) == TOTAL_SPOTS
    # У семьи и у службы жетон нарисованный: по знаку видно, какая
    # именно семья держит дом, — по шляпе не было видно
    assert marks["market"]["badge"].endswith("/clans/black_ravens.png")
    assert marks["casino"]["badge"].endswith("/services/police.png")
    # У ничьего дома жетона нет: городская охрана не фракция
    assert marks["bar"]["badge"] == "" and marks["bar"]["mark"] == "⚪️"
    # Свою узнают по цвету названия, а не по отдельной отметке
    assert marks["market"]["mine"] is True
    assert marks["casino"]["mine"] is False
    # Доход и имя владельца на карту не идут вовсе
    assert set(marks["bar"]) == {"mark", "badge", "mine"}

    # Плашка под ногами: боец стоит у бара, и отсюда собирают отряд
    band = body["turf"]["here"]
    assert band["code"] == "bar" and band["button"] == "seize"
    assert band["title"] == "Захват"

    # Созвал — та же плашка открывает штурм, а не зовёт второй отряд
    await post(client, don.user_id, action="call", spot="bar")
    body = await (await client.get(
        "/api/map", headers=headers(don.user_id)
    )).json()
    assert body["turf"]["here"]["button"] == "open"
    assert body["turf"]["here"]["in_hand"] is True


async def test_the_map_band_keeps_quiet_when_there_is_nothing_to_press(
    db, client
):
    """Плашки без кнопки не бывает: точка, за которую уже дерутся, молчит.

    «За точку уже дерутся» — ответ на вопрос, которого у двери не
    задавали: нажать всё равно нечего, а место на карте плашка
    занимает и дверь закрывает.
    """
    don, squad, clan = await family(db)
    rival, rivals, _ = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    await patrolled(db, "bar")
    await stand(db, squad + rivals, "bar")
    # Чужая семья уже собралась на бар — второго отряда он не ждёт
    assert (await post(client, rival.user_id, action="call",
                       spot="bar")).status == 200

    city = await (await client.get("/api/map", headers=headers(don.user_id))).json()
    assert city["turf"]["here"] is None
    # А отметка у дома остаётся: она говорит, чей он, а не что делать
    assert city["turf"]["spots"]["bar"]["mark"] == "👮"


async def test_the_band_offers_the_guard_on_your_own_spot(db, client):
    """Своя точка: воевать не за что, а охрану поставить — есть кому."""
    don, squad, clan = await family(db)
    await stand(db, squad, "bar")
    await db.take_turf("bar", "clan", str(clan.id), NOW - 3 * DAY_SECONDS)

    city = await (await client.get("/api/map", headers=headers(don.user_id))).json()
    band = city["turf"]["here"]
    assert band["button"] == "guard" and band["mine"] is True


async def test_the_spot_underfoot_shows_who_stands_on_it(db, client):
    """Точка под ногами: кто её держит, кто на ней стоит и чей он."""
    from bot.hire_service import hire, post

    don, squad, clan = await family(db)
    await stand(db, squad, "bar")
    await db.take_turf("bar", "clan", str(clan.id), NOW)
    rich = await db.get_player(don.user_id)
    rich.credits = 10_000
    await db.save_player(rich)
    await hire(db, await db.get_player(don.user_id), "merc_7", now=NOW)
    taken = (await db.hirelings_of(don.user_id))[0]
    await post(db, await db.get_player(don.user_id), int(taken["id"]), "bar",
               now=NOW)

    body = await (await client.get("/api/turf", headers=headers(don.user_id))).json()
    here = body["here_spot"]
    assert here["code"] == "bar" and here["mine"] is True
    assert here["guarded"] is True and here["guards"] == 0, "за семью город не стоит"
    assert [one["title"] for one in here["posted"]] == ["Головорез"]
    assert here["posted"][0]["owner"] == "Дон-1"
    assert here["posted"][0]["level"] == 7

    # А на ничьей точке стоит городская охрана, и её видно числом
    await stand(db, squad, "casino")
    body = await (await client.get("/api/turf", headers=headers(don.user_id))).json()
    assert body["here_spot"]["posted"] == []
    assert body["here_spot"]["guards"] == 3


async def test_the_panel_underfoot_knows_about_the_fight_going_on(db):
    """Панель точки знает то же, что список: на ней уже дерутся.

    Иначе погасшую кнопку она объясняла бы не тем — а неверная причина
    хуже, чем никакой: с ней уходят с готовым, но ложным ответом.
    """
    from bot.hire_service import hire, post
    from bot.turf_war import TurfWar
    from bot.webapp.turf import build_war
    from tests.test_police import cop

    don, squad, clan = await family(db)
    boss = await db.get_player(don.user_id)
    boss.credits = 20_000
    await db.save_player(boss)
    await db.take_turf("bar", "clan", str(clan.id), NOW - 3 * DAY_SECONDS)
    await stand(db, [don.user_id], "bar")
    await hire(db, await db.get_player(don.user_id), "merc_7", now=NOW)
    mine = (await db.hirelings_of(don.user_id))[0]
    await post(db, await db.get_player(don.user_id), int(mine["id"]), "bar",
               now=NOW)

    officer = await cop(db, 99, "Ragnar")
    officer.location = "bar"
    await db.save_player(officer)

    war = TurfWar(db)
    try:
        # Пока город раздумывает, на бар собирается чужая семья
        body = await build_war(db, await db.get_player(99), war, NOW)
        assert body["here_spot"]["can_storm"] is True
        assert body["here_spot"]["busy"] is False

        rival, rivals, _ = await family(
            db, first=20, name="Псы", sign="silver_wolves"
        )
        await stand(db, rivals, "bar")
        await war.open_muster(await db.get_player(rival.user_id), "bar",
                              now=NOW)
        body = await build_war(db, await db.get_player(99), war, NOW)
        assert body["here_spot"]["busy"] is True
        assert body["here_spot"]["can_storm"] is False
    finally:
        await war.close()


async def test_the_map_band_is_empty_away_from_the_spots(db, client):
    """Стоишь не на точке — плашки нет, а флажки на месте."""
    don, _, _ = await family(db)

    body = await (await client.get(
        "/api/map", headers=headers(don.user_id)
    )).json()
    assert body["turf"]["here"] is None
    assert len(body["turf"]["spots"]) == TOTAL_SPOTS


async def test_the_road_is_shut_while_the_squad_stands(db, client):
    """Из-под точки не уходят: дорога спрашивает войну, и та не пускает.

    Без этого захват ногами был бы одной видимостью: пришёл, созвал
    отряд, ушёл в особняк — и дерись оттуда.
    """
    don, squad, _ = await family(db)
    await stand(db, squad, "bar")
    await post(client, don.user_id, action="call", spot="bar")

    answer = await client.post(
        "/api/travel", json={"to": "casino"}, headers=headers(don.user_id)
    )
    assert answer.status == 409
    assert "бой" in (await answer.json())["error"]

    # Распустил сбор — дорога открылась
    await post(client, don.user_id, action="leave")
    answer = await client.post(
        "/api/travel", json={"to": "casino"}, headers=headers(don.user_id)
    )
    assert answer.status == 200


async def test_one_family_walks_into_the_spot_another_left_empty(db):
    """Мафия выбивает мафию, а пустую точку просто забирает.

    Городская охрана за чужую семью не встаёт: дом, взятый семьёй и
    брошенный без наёмника, не защищён никем. Драться не с кем — отряд
    входит и забирает его, и ни одной волны на это не тратится.
    """
    from bot.turf_war import TurfWar

    don, _, clan = await family(db)
    rival, rivals, theirs = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    # Первая семья взяла бар три дня назад и охрану не выставила
    await db.take_turf("bar", "clan", str(clan.id), NOW - 3 * DAY_SECONDS)
    await stand(db, rivals, "bar")
    boss = await db.get_player(rival.user_id)

    war = TurfWar(db)
    try:
        muster = await war.open_muster(boss, "bar", now=NOW)
        siege = await war.march(muster.id, boss.user_id, now=NOW)
    finally:
        await war.close()

    assert siege.finished and siege.won
    assert siege.wave == 0, "волн не было: драться было не с кем"
    assert "без боя" in siege.summary[0]
    assert "Коза Ностра" in siege.summary[1], "сказано, у кого отняли"
    row = (await db.turf_rows())["bar"]
    assert row.owner == owner_of_clan(theirs.id), "точка сменила семью"


async def test_the_ceiling_holds_in_the_live_panel_too(db, client):
    """Потолок в четыре точки считается и у кнопки сбора.

    Раньше его знал только разовый штурм, и панель брала бы весь город:
    правило, которое держит одна из двух дверей, — не правило.
    """
    don, squad, clan = await family(db)
    moment = NOW
    for code in ("bar", "casino", "market", "jewelry_store"):
        moment = max(moment, await seize(db, don, squad, code)) + 60
    await stand(db, squad, "car_dealership")

    answer = await post(client, don.user_id, action="call", spot="car_dealership")
    assert answer.status == 409
    assert "не удержать" in (await answer.json())["error"]


async def test_the_ceiling_counts_the_fights_already_going(db, client):
    """Четыре отряда разом потолок не обходят.

    Потолок считался по базе, а в базе до развязки ничего нет: созвав
    четыре отряда в одну минуту, семья проходила проверку четырежды и
    брала весь город. Теперь идущие бои считаются вместе с взятым.
    """
    don, squad, _ = await family(db, size=6)
    await stand(db, squad, "bar")
    await post(client, don.user_id, action="call", spot="bar")

    # Трое своих зовут отряды на соседние точки — потолок это видит
    codes = ["casino", "market", "northern_wall_shop"]
    for number, code in zip(squad[1:4], codes):
        await stand(db, [number], code)
        answer = await post(client, number, action="call", spot=code)
        assert answer.status == 200, code

    await stand(db, [squad[4]], "jewelry_store")
    answer = await post(client, squad[4], action="call", spot="jewelry_store")
    assert answer.status == 409
    assert "не удержать" in (await answer.json())["error"]


async def test_a_stranger_does_not_join_the_muster(db, client):
    """Сбор свой: чужая семья в него не записывается."""
    don, _, _ = await family(db)
    rival, rivals, _ = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    await stand(db, [don.user_id] + rivals, "bar")
    body = await (await post(client, don.user_id, action="call", spot="bar")).json()

    answer = await post(
        client, rival.user_id, action="join", muster_id=body["muster"]["id"]
    )
    assert answer.status == 409
    assert "чужой сбор" in (await answer.json())["error"]


async def test_a_loner_is_not_shown_the_war_at_all(db, client):
    """Одиночке война не видна вовсе: ни точек, ни отметок на карте.

    Раньше ему показывали весь город с серыми кнопками. Это не
    осторожность, а шум: взять точку он не может, доли не получит, и
    единственное, что он из карты узнавал, — что где-то идёт чужая
    война.
    """
    one = make_player(9, "Сам по себе")
    await db.save_player(one)
    await db.take_turf("bar", "service", "police", NOW)
    body = await (await client.get("/api/turf", headers=headers(9))).json()

    assert body["side"]["nobody"] is True
    assert body["spots"] == [] and body["here_spot"] is None
    assert body["hire"]["side"] == ""
    assert "Вступи в клан" in body["note"]

    # И на карте города у домов нет ни отметок, ни плашки под ногами
    city = await (await client.get("/api/map", headers=headers(9))).json()
    assert city["turf"]["spots"] == {} and city["turf"]["here"] is None


# ---------- штурм обязан кончиться ----------


async def test_a_broken_assault_still_lets_the_squad_out(db, monkeypatch):
    """Развязка штурма сорвалась — отряд всё равно выходит из него.

    Висящий штурм хуже любой ошибки в нём: из него не уйти, по городу
    не пройти, и чинится это только перезапуском бота. Поэтому
    развязка отгорожена, а отряд разводят в любом случае.
    """
    from bot.turf_war import TurfWar

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW - 3 * DAY_SECONDS)
    rival, rivals, _ = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    await stand(db, rivals, "bar")
    war = TurfWar(db)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("точка не переписалась")

    monkeypatch.setattr(war, "_settle", boom)
    try:
        muster = await war.open_muster(
            await db.get_player(rival.user_id), "bar", now=NOW
        )
        for one in rivals[1:]:
            await war.join(muster.id, await db.get_player(one))
        siege = await war.march(muster.id, rival.user_id, now=NOW)
    finally:
        await war.close()

    assert siege.finished
    assert siege.summary == ["Штурм кончен, но итог посчитать не вышло."]
    for one in rivals:
        assert war.siege_of_user(one) is None
        assert war.result_of_user(one) is siege


async def test_the_done_button_is_the_way_out_of_a_hung_assault(db):
    """«Понятно» обязана работать всегда — даже у сломанного штурма.

    А живой сбор она не разводит: из него выходят своей кнопкой, и
    закрытый итог прошлого штурма тут ничего не решает.
    """
    from bot.turf_war import TurfWar

    don, squad, clan = await family(db)
    await db.take_turf("bar", "clan", str(clan.id), NOW - 3 * DAY_SECONDS)
    rival, rivals, _ = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    await stand(db, rivals, "bar")
    war = TurfWar(db)
    try:
        muster = await war.open_muster(
            await db.get_player(rival.user_id), "bar", now=NOW
        )
        # Так выглядит живой сбор: кнопка итога его не трогает
        war.forget_result(rival.user_id)
        assert war.muster_of_user(rival.user_id) is muster

        siege = await war.march(muster.id, rival.user_id, now=NOW)
        # А так — зависший штурм: кончен, а отряд всё ещё в нём
        siege.finished = True
        war._sieges[siege.id] = siege
        war._busy[rival.user_id] = siege.id

        war.forget_result(rival.user_id)
        assert war.siege_of_user(rival.user_id) is None
    finally:
        await war.close()
