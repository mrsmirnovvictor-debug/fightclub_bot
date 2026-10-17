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
    CALM_SECONDS,
    DAY_SECONDS,
    MAX_SPOTS_PER_OWNER,
    SPOTS,
    TOTAL_SPOTS,
    Owner,
    calm_left,
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
        # Точка отдыхает — следующий заход за её сроком
        moment += CALM_SECONDS + 60
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


def test_the_calm_holds_a_full_day():
    assert calm_left(NOW, NOW) == CALM_SECONDS
    assert calm_left(NOW, NOW + CALM_SECONDS) == 0
    assert calm_left(0, NOW) == 0, "боя не было — отдыхать не от чего"


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
    # Без наёмников у семьи на точке пусто — и её держит городская охрана
    wall = await garrison(db, mine, [10], NOW, code="bar")
    assert all("Охранник" in one.name for one in wall)

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


async def test_the_spot_rests_after_the_battle(db):
    """За точку подрались — сутки её не трогают, кто бы ни победил.

    Проверяем с чужой стороны: своей точке отвечают «и так за вами», а
    отдых придуман ровно для того, чтобы её не отбивали в ту же минуту.
    """
    don, squad, _ = await family(db)
    await assault(db, don, "bar", squad, now=NOW, rng=random.Random(0))

    rival, foes, _ = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    with pytest.raises(TurfError, match="отдыхает"):
        await assault(db, rival, "bar", foes, now=NOW + 60, rng=random.Random(1))

    # А через сутки — пожалуйста
    await assault(
        db, rival, "bar", foes, now=NOW + CALM_SECONDS, rng=random.Random(2)
    )


async def test_your_own_spot_is_not_stormed(db):
    don, squad, clan = await family(db)
    taken = await seize(db, don, squad, "bar")

    with pytest.raises(TurfError, match="и так за вами"):
        await assault(
            db, don, "bar", squad, now=taken + CALM_SECONDS, rng=random.Random(3)
        )


async def test_four_spots_are_the_ceiling(db):
    """Больше четырёх точек одна семья не держит."""
    don, squad, clan = await family(db)
    moment = NOW
    for code in ("bar", "weapon_shop", "taxi_depot", "pawnshop"):
        moment = max(moment, await seize(db, don, squad, code)) + CALM_SECONDS

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
    await db.take_turf("casino", "service", "police", NOW)

    body = await (await client.get(
        "/api/turf", headers=headers(don.user_id)
    )).json()
    rows = {one["code"]: one for one in body["spots"]}
    assert rows["bar"]["mark"] == "🚩" and rows["bar"]["mine"] is True
    assert rows["casino"]["mark"] == "🛡"
    assert rows["market"]["mark"] == "⚪️"


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
    await db.take_turf("casino", "service", "police", NOW)
    await db.take_turf("market", "clan", str(clan.id), NOW)
    await stand(db, squad, "bar")

    body = await (await client.get(
        "/api/map", headers=headers(don.user_id)
    )).json()
    marks = body["turf"]["spots"]
    assert len(marks) == TOTAL_SPOTS
    # Фуражка у службы, шляпа у семьи, круг у ничьей
    assert marks["casino"]["mark"] == "👮"
    assert marks["market"]["mark"] == "🎩"
    assert marks["bar"]["mark"] == "⚪️"
    # Свою узнают по цвету названия, а не по отдельной отметке
    assert marks["market"]["mine"] is True
    assert marks["casino"]["mine"] is False
    # Доход и имя владельца на карту не идут вовсе
    assert set(marks["bar"]) == {"mark", "mine"}

    # Плашка под ногами: боец стоит у бара, и отсюда собирают отряд
    band = body["turf"]["here"]
    assert band["code"] == "bar" and band["button"] == "call"

    # Созвал — та же плашка открывает штурм, а не зовёт второй отряд
    await post(client, don.user_id, action="call", spot="bar")
    body = await (await client.get(
        "/api/map", headers=headers(don.user_id)
    )).json()
    assert body["turf"]["here"]["button"] == "open"
    assert body["turf"]["here"]["in_hand"] is True


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


async def test_the_ceiling_holds_in_the_live_panel_too(db, client):
    """Потолок в четыре точки считается и у кнопки сбора.

    Раньше его знал только разовый штурм, и панель брала бы весь город:
    правило, которое держит одна из двух дверей, — не правило.
    """
    don, squad, clan = await family(db)
    moment = NOW
    for code in ("bar", "casino", "market", "jewelry_store"):
        moment = max(moment, await seize(db, don, squad, code)) + CALM_SECONDS
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


async def test_a_loner_is_told_he_has_no_side(db, client):
    """Одиночке карта видна, а кнопки штурма у него нет."""
    one = make_player(9, "Сам по себе")
    await db.save_player(one)
    body = await (await client.get("/api/turf", headers=headers(9))).json()

    assert body["side"]["nobody"] is True
    assert "Вступи в клан" in body["note"]
    assert not any(row["can_storm"] for row in body["spots"])
