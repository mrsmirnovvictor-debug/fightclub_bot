"""Школа боевых искусств: приём за дела в бою.

Уровень даёт приём из трёх и даёт сразу; школа даёт любой, но за работу.
Проверяем пять вещей и в таком порядке важности:

1. **Приём копится своим делом.** Удар — точными ударами, уворот —
   уворотами, крит — критическими, лечение и парирование — блоками.
   Деление то же, что у энергии, и держится оно классовыми приёмами.
2. **Дела пишет бой.** Все бои города, и считается сделанное самим
   бойцом: уворот от приёма в зачёт не идёт.
3. **Зачёт идёт от записи.** Ведомость пожизненная, но учится боец с той
   отметки, на которой записался, — иначе ветеран выходил бы из школы
   готовым приёмом.
4. **Цена приёма в школе наименьшая.** Приём учится на своей ступени, а
   не на той, до которой дорос боец.
5. **Место освобождает приём с той же ценой** — и забывается навсегда.
"""

from __future__ import annotations

import random

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.content.abilities import ABILITIES, STARTER, get_ability
from bot.database import Database
from bot.dojo_service import (
    DojoError,
    catalogue,
    count_deeds,
    forget_for,
    learn,
    learning,
    start,
    started,
    stop,
    totals,
    waiting,
)
from bot.game.abilities import (
    MAX_ABILITIES,
    TIER_COST,
    TIERS,
    Loadout,
    Source,
    energy_gain,
)
from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.combat import Action, Fighter, Zone, resolve_round
from bot.game.dojo import (
    BRANCHES,
    DEEDS,
    DEED_OF,
    branch_of,
    deed_name,
    deed_of,
    needed,
    price,
)
from bot.game.locations import Service, get_location, where_to
from bot.models import Player
from bot.webapp.dojo import build_dojo
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

NOW = 1_800_000_000
SCHOOL = "martial_arts_school"
CLUB = "fight_club"

# Путь воина: по приёму на каждой ступени, все четыре слота заняты
WARRIOR_PATH: tuple[tuple[str, int], ...] = (
    ("strong_hit", 1), ("power_hit", 3), ("crushing_hit", 6), ("mass_hit", 10),
)

HEAD, CHEST, BELT, LEGS = Zone.HEAD, Zone.CHEST, Zone.BELT, Zone.LEGS


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


def make_player(
    user_id: int = 42, nickname: str = "Тайлер", where: str = SCHOOL,
    class_code: str = "warrior", level: int = 10,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code=class_code, level=level,
        credits=1_000, location=where, seen_at=NOW,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def student(
    db: Database, path: tuple[tuple[str, int], ...] = WARRIOR_PATH, **kwargs
) -> Player:
    """Боец, прошедший свой путь: четыре приёма, по одному на ступень."""
    player = make_player(**kwargs)
    await db.save_player(player)
    kit = Loadout()
    for code, tier in path:
        kit.learn(code, tier)
    await db.save_abilities(player.user_id, kit)
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


def fighter(fclass="warrior", user_id: int = 1) -> Fighter:
    one = FIGHTER_CLASSES[fclass]
    return Fighter(
        user_id=user_id, name="Боец", fclass=one, stats=one.base_stats
    )


# ---------- дом и услуга ----------


def test_the_school_is_a_house_with_a_trade_of_its_own():
    """Школа перестала быть обещанием: в ней учат приёмам."""
    place = get_location(SCHOOL)

    assert place.allows(Service.DOJO)
    assert where_to(Service.DOJO).code == SCHOOL
    assert place.works and not place.soon, "дом обжит — обещать нечего"


# ---------- чей приём и каким делом копится ----------


def test_every_trick_belongs_to_a_branch():
    """У каждого приёма есть ветка, и копится он её делом."""
    for ability in ABILITIES:
        assert branch_of(ability) in DEED_OF
        assert deed_of(ability) in Source


def test_the_branch_of_a_class_holds_its_own_starter():
    """Делёж не на глаз: классовый приём лежит в ветке своего класса.

    Это и есть проверка всей таблицы: если «Восстановление» окажется не
    у танка, а у кого-то ещё, значит деление разошлось с игрой.
    """
    for class_code, code in STARTER.items():
        ability = get_ability(code)
        assert branch_of(ability) == class_code, ability.title


def test_each_branch_counts_the_work_its_class_is_good_at():
    """Удар — ударами, уворот — уворотами, крит — критами, лечение — блоками."""
    assert DEED_OF["warrior"] is Source.HIT
    assert DEED_OF["rogue"] is Source.DODGE
    assert DEED_OF["assassin"] is Source.CRIT
    assert DEED_OF["tank"] is Source.BLOCK
    # Парирование — приём танка, и копится оно блоками, а не уворотами:
    # танку уворачиваться нечем, в этом и смысл развилки
    assert deed_of(get_ability("parry")) is Source.BLOCK


def test_the_price_in_deeds_grows_with_the_step():
    """Пятьсот дел за первую ступень и по пятьсот за каждую следующую."""
    assert [DEEDS[tier] for tier in TIERS] == [500, 1000, 1500, 2000]
    assert all(needed(one) == DEEDS[one.tier] for one in ABILITIES)


def test_the_school_asks_the_lowest_possible_price_in_energy():
    """Приём из школы стоит столько, сколько у своего класса.

    На развилке уровня тот же приём обошёлся бы дороже: там платят за
    ступень, на которой научился, а не за приём. Школа учит приём на его
    собственной ступени — дешевле не бывает.
    """
    strong = get_ability("strong_hit")

    assert price(strong) == TIER_COST[1] == 3
    # Взятый кросс-классом на десятой ступени он стоил бы вчетверо
    assert TIER_COST[10] == 12
    assert all(price(one) == TIER_COST[one.tier] for one in ABILITIES)
    assert all(price(one) <= TIER_COST[max(TIERS)] for one in ABILITIES)


def test_the_school_holds_every_trick_in_the_club():
    """В школе лежат все приёмы клуба — и свои, и чужих классов."""
    player = make_player()

    shelf = catalogue(player)

    assert len(shelf) == len(ABILITIES) == 16
    assert {one.code for one in shelf} == {one.code for one in ABILITIES}
    # Порядок — по ветвям, внутри ветви по ступеням: приём выбирают от
    # дела, которым за него платить, и четыре лестницы рядом читаются
    assert [branch_of(one) for one in shelf[:4]] == ["warrior"] * 4
    assert [one.tier for one in shelf[:4]] == [1, 3, 6, 10]
    assert [branch_of(one) for one in shelf[::4]] == list(BRANCHES)


# ---------- дела пишет бой ----------


def test_a_round_writes_down_what_each_fighter_did():
    """Точный удар и удержанный блок ложатся бойцу в память боя.

    Размен один, а дел в нём два, и оба достались одному: он попал в
    незакрытую голову и сам закрылся от удара в пояс.
    """
    attacker, defender = fighter(user_id=1), fighter("tank", user_id=2)

    resolve_round(attacker, Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  defender, Action(attacks=(BELT,), block=(BELT, LEGS)), 1,
                  random.Random(4))

    assert attacker.deeds[Source.HIT] == 1
    assert attacker.deeds[Source.BLOCK] == 1
    assert defender.deeds == {}, "он не попал и ничего не удержал"


def test_a_deed_is_counted_for_every_class_not_only_its_own():
    """Воину уворот энергии не приносит, а в ведомость идёт.

    Иначе школа была бы бессмысленной: воин, который хочет уворот
    трикстера, не смог бы набрать на него ни одного дела.
    """
    attacker, dodger = fighter(user_id=1), fighter("rogue", user_id=2)

    resolve_round(attacker, Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  dodger, Action(attacks=(BELT,), block=(BELT, LEGS)), 1,
                  random.Random(1))

    assert dodger.deeds[Source.DODGE] == 1, "уворот зачтён"
    # Воин в этом же размене удержал удар в пояс. Энергии ему за это не
    # положено — он копит ударами, — а дело записано: ведомость пишет
    # сделанное, а не свойственное
    assert attacker.deeds[Source.BLOCK] == 1
    assert energy_gain("warrior", Source.BLOCK) == 0
    assert attacker.energy == 0, "за блок воину не платят"


def test_a_trick_does_not_count_as_a_deed():
    """Уворот от приёма в зачёт не идёт: приём себя не кормит.

    Правило то же, что у энергии, и причина та же: иначе школа стала бы
    кольцом, в котором приём учит приёму.
    """
    attacker, rogue = fighter(user_id=1), fighter("rogue", user_id=2)
    rogue.loadout.learn("nimble", 1)
    rogue.gain_energy(TIER_COST[1])
    rogue.use("nimble")

    resolve_round(attacker, Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  rogue, Action(attacks=(BELT,), block=(BELT, LEGS)), 1,
                  random.Random(13))

    assert rogue.deeds.get(Source.DODGE, 0) == 0


async def test_the_service_drains_the_round_into_the_ledger(db):
    """Служба боя сливает дела раунда в базу — и забирает их у бойца."""
    one = fighter(user_id=42)
    one.deeds[Source.HIT] = 3
    one.deeds[Source.DODGE] = 1

    assert await count_deeds(db, {42: one}) == 4
    assert not one.deeds, "дела слиты и у бойца их больше нет"
    # Второй слив того же раунда ничего не добавит
    assert await count_deeds(db, {42: one}) == 0

    ledger = await totals(db, 42)
    assert ledger[Source.HIT] == 3 and ledger[Source.DODGE] == 1
    assert ledger[Source.CRIT] == 0


async def test_the_ledger_belongs_to_people_only(db):
    """У боссов и наёмных ведомости нет: их номера отрицательные."""
    boss = fighter(user_id=-1)
    boss.deeds[Source.HIT] = 10

    assert await count_deeds(db, {-1: boss}) == 0
    assert (await totals(db, -1))[Source.HIT] == 0


# ---------- обучение ----------


async def test_the_count_starts_from_the_day_you_signed_up(db):
    """Зачёт идёт от записи: прошлые дела в него не входят.

    Ведомость пожизненная, и она никуда не девается — но «взять в
    обучение» должно что-то значить, иначе ветеран приходил бы в школу и
    выходил из неё готовым приёмом.
    """
    player = await student(db)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 400})

    said = await start(db, player, "nimble", NOW)

    assert "500 уворотов" in said
    state = await started(db, player, NOW)
    assert state["done"] == 0 and state["needed"] == 500
    assert state["ready"] is False

    await db.add_deeds(player.user_id, {Source.DODGE.value: 500})
    assert await started(db, player, NOW) is None, "набранный не учится"
    state = (await waiting(db, player, NOW))[0]
    assert state["done"] == 500 and state["ready"] is True


async def test_only_one_trick_is_learned_at_a_time(db):
    """Второй приём в обучение не берут, пока первый не доучен."""
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 200})

    with pytest.raises(DojoError, match="Разом учат один приём"):
        await start(db, player, "crit_hit", NOW)

    state = await started(db, player, NOW)
    assert state["ability"].code == "nimble" and state["done"] == 200
    # И второй раз на тот же приём не записывают
    with pytest.raises(DojoError, match="и так в обучении"):
        await start(db, player, "nimble", NOW)


async def test_a_full_bar_frees_the_place_for_the_next_trick(db):
    """Набранный приём ждёт сколько угодно, а боец учит следующий.

    «Отменить» в окне подтверждения прогресса не отменяет: приём висит
    набранным, пока его не подтвердят, и место в обучении не занимает.
    """
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 500})

    # Первый набран — и место свободно
    said = await start(db, player, "crit_hit", NOW)

    assert "500 критических ударов" in said
    assert (await started(db, player, NOW))["ability"].code == "crit_hit"
    hanging = await waiting(db, player, NOW)
    assert [one["ability"].code for one in hanging] == ["nimble"]
    assert len(await learning(db, player, NOW)) == 2

    # И ждёт он действительно сколько угодно: набрали второй, а первый
    # всё там же
    await db.add_deeds(player.user_id, {Source.CRIT.value: 500})
    assert await started(db, player, NOW) is None
    assert {one["ability"].code for one in await waiting(db, player, NOW)} == {
        "nimble", "crit_hit",
    }

    # Подтверждают их по одному, и каждый забирает своё место
    await learn(db, player, "crit_hit", NOW)
    assert "crit_hit" in player.loadout and "strong_hit" not in player.loadout
    assert [one["ability"].code for one in await waiting(db, player, NOW)] == [
        "nimble"
    ]


async def test_a_ready_trick_is_not_thrown_away(db):
    """Набранный приём подтверждают, а не бросают."""
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 500})

    with pytest.raises(DojoError, match="подтверждают, а не"):
        await stop(db, player, "nimble", NOW)

    assert len(await waiting(db, player, NOW)) == 1


async def test_a_learned_trick_leaves_the_school(db):
    """Выученный приём из школы уходит: второй раз его не учат."""
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 500})
    await learn(db, player, "nimble", NOW)

    assert "nimble" not in {one.code for one in catalogue(player)}
    with pytest.raises(DojoError, match="и так знает"):
        await start(db, player, "nimble", NOW)


async def test_the_school_does_not_teach_what_you_know(db):
    """Выученному приёму школа не учит: учить нечему."""
    player = await student(db)

    with pytest.raises(DojoError, match="и так знает"):
        await start(db, player, "strong_hit", NOW)


async def test_the_school_knows_only_its_own_tricks(db):
    """Приёма, которого нет в клубе, нет и в школе."""
    player = await student(db)

    with pytest.raises(DojoError, match="Такого приёма"):
        await start(db, player, "ninja_vanish", NOW)


async def test_training_is_dropped_by_hand_too(db):
    """Бросить обучение можно и просто так — дела при этом пропадают."""
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 300})

    said = await stop(db, player, "nimble", NOW)

    assert "брошен" in said
    assert await started(db, player, NOW) is None
    with pytest.raises(DojoError, match="ни на что не записан"):
        await stop(db, player, "nimble", NOW)


# ---------- выучить ----------


async def test_the_trick_is_not_given_before_the_work_is_done(db):
    """Рано — школа говорит, сколько осталось."""
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 499})

    with pytest.raises(DojoError, match="осталось 1 уворот"):
        await learn(db, player, "nimble", NOW)

    with pytest.raises(DojoError, match="не записывался"):
        await learn(db, player, "crit_hit", NOW)
    with pytest.raises(DojoError, match="Набранных приёмов нет"):
        await learn(db, await student(db, user_id=7, nickname="Марла"), now=NOW)


async def test_the_learned_trick_costs_the_least_and_eats_its_twin(db):
    """Выучил — приём встал по своей цене, а прежний с той же ценой забыт."""
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 500})

    ability = await learn(db, player, "nimble", NOW)

    assert ability.code == "nimble"
    assert "nimble" in player.loadout
    assert player.loadout.tier_of("nimble") == 1
    assert player.loadout.cost_of("nimble") == 3, "цена наименьшая"
    # Место освободил приём с той же ценой — и забыт он навсегда
    assert "strong_hit" not in player.loadout
    assert len(player.loadout) == MAX_ABILITIES
    # Записи больше нет: учиться дальше — заново
    assert await learning(db, player, NOW) == []
    fresh = await db.get_player(player.user_id)
    assert "nimble" in fresh.loadout and "strong_hit" not in fresh.loadout


async def test_what_will_be_forgotten_is_known_before_the_work(db):
    """Что забудется, школа говорит до записи, а не после пятисот ударов."""
    player = await student(db)

    assert forget_for(player, get_ability("nimble")).code == "strong_hit"
    assert forget_for(player, get_ability("parry")).code == "crushing_hit"
    assert forget_for(player, get_ability("blood_call")).code == "mass_hit"


async def test_a_free_slot_eats_nobody(db):
    """Слот свободен — забывать нечего: приём просто встаёт на место.

    Так бывает у молодого бойца: ступени он ещё не прошёл, и место под
    приём с этой ценой у него пустое. Школа уровня не спрашивает —
    спрашивает дела.
    """
    player = await student(db, path=(("strong_hit", 1),), level=1)
    await start(db, player, "breach", NOW)
    await db.add_deeds(player.user_id, {Source.CRIT.value: 1000})

    assert forget_for(player, get_ability("breach")) is None
    await learn(db, player, "breach", NOW)

    assert "strong_hit" in player.loadout and "breach" in player.loadout
    assert player.loadout.cost_of("breach") == TIER_COST[3]
    assert len(player.loadout) == 2


# ---------- экран ----------


async def test_the_school_screen_shows_the_ledger_and_the_shelf(db):
    """Экран школы: ведомость, полка на шестнадцать приёмов и цены."""
    player = await student(db)
    await db.add_deeds(player.user_id, {Source.HIT.value: 1200})

    body = await build_dojo(db, player, NOW)

    assert body["title"] == get_location(SCHOOL).title
    assert body["started"] is None and body["ready"] == []
    # Вкладка «Ваши приёмы»: четыре своих, с ценой по своей ступени
    assert [one["code"] for one in body["known"]] == [
        code for code, _ in WARRIOR_PATH
    ]
    assert [one["price"] for one in body["known"]] == [3, 6, 9, 12]
    hits = next(one for one in body["deeds"] if one["deed"] == "hit")
    assert hits["count"] == 1200 and hits["branch_title"] == "Воин"
    # Вкладка «Обучение»: двенадцать невыученных и четыре цены для фильтра
    assert len(body["shelf"]) == 16 - MAX_ABILITIES == 12
    assert body["costs"] == [3, 6, 9, 12]
    assert {one["price"] for one in body["shelf"]} == {3, 6, 9, 12}
    nimble = next(one for one in body["shelf"] if one["code"] == "nimble")
    assert nimble["price"] == 3 and nimble["needed"] == 500
    assert nimble["branch_title"] == "Трикстер"
    assert nimble["deed_name"] == "уворотов"
    assert nimble["deed_act"] == "увернуться"
    assert deed_name(Source.DODGE, 1) == "уворот"
    assert nimble["taken"] is False
    assert nimble["forget"]["title"] == get_ability("strong_hit").title
    assert nimble["forget"]["image"], "забываемый приём приходит с картинкой"
    # Выученные в каталог не идут вовсе: второй раз их не учат
    assert "strong_hit" not in {one["code"] for one in body["shelf"]}
    assert "дешевле всего" in body["note"]


async def test_the_screen_shows_the_training_and_the_bar(db):
    """Обучение: сколько набрано, сколько осталось и что забудется."""
    player = await student(db)
    await start(db, player, "nimble", NOW)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 120})

    body = await build_dojo(db, player, NOW)

    state = body["started"]
    assert state["code"] == "nimble" and state["done"] == 120
    assert state["needed"] == 500 and state["left"] == 380
    assert state["ready"] is False
    assert state["deed_title"] == "Уворотов"
    assert state["forget"]["code"] == "strong_hit"
    assert "120 из 500" in body["note"]
    # Пока учится один — выбирать нечего: каталог пуст
    assert body["shelf"] == []

    await db.add_deeds(player.user_id, {Source.DODGE.value: 400})
    ready = await build_dojo(db, player, NOW)
    assert ready["started"] is None, "набранный больше не учится"
    assert [one["code"] for one in ready["ready"]] == ["nimble"]
    assert ready["ready"][0]["ready"] is True
    assert "Набрано и ждёт" in ready["note"]
    # И каталог вернулся: место в обучении свободно
    assert len(ready["shelf"]) == 12


async def test_the_school_opens_only_inside_it(db, client):
    """Записываются в школе: спрятанная кнопка обходится запросом."""
    player = await student(db)
    await db.add_deeds(player.user_id, {Source.DODGE.value: 500})

    answer = await client.get("/api/dojo", headers=headers())
    assert answer.status == 200
    assert len((await answer.json())["shelf"]) == 12

    answer = await client.post(
        "/api/dojo", json={"action": "start", "code": "nimble"},
        headers=headers(),
    )
    assert answer.status == 200
    assert "в обучении" in (await answer.json())["said"]

    answer = await client.post(
        "/api/dojo", json={"action": "кувырок"}, headers=headers()
    )
    assert answer.status == 400

    away = await db.get_player(player.user_id)
    away.location = CLUB
    await db.save_player(away)
    assert (await client.get("/api/dojo", headers=headers())).status == 409
