"""Две руки: снять вещь из второй и ударить ею же.

Во второй руке держат щит или второе оружие. Оружие при этом знает свой
слот — `weapon`, — но лежит в `offhand`, и это расхождение оказалось
источником бага: кукла снимала вещь по слоту самого предмета, а не по
клетке, в которой он лежит. Нажатие на вторую руку снимало оружие из
первой, второе нажатие отвечало «слот и так пуст», хотя в клетке оружие
было видно.

Здесь же проверяется и сам удар второй рукой: что она бьёт своим оружием
и что модификатор на нём доходит до урона. Это второе, на что жаловались,
— и это оказалось не багом; тест стоит, чтобы так и осталось.
"""

import random

import pytest

from bot.database import Database
from bot.game.classes import Stats, Zone, get_class
from bot.game.combat import Action, Fighter, resolve_round
from bot.game.equipment import CATALOGUE, Equipment, OwnedItem, Slot
from bot.inventory_service import InventoryError, unequip
from bot.models import Player
from bot.webapp.card import slot_payload

SWORD, BLADE = "lightsaber", "hidden_blade"


def make_player(**kwargs) -> Player:
    return Player(
        user_id=42,
        nickname="Victor",
        class_code="assassin",
        level=10,
        **Stats(strength=20, agility=20, intuition=20, endurance=20).as_dict(),
        **kwargs,
    )


async def armed(db: Database) -> tuple[Player, OwnedItem, OwnedItem]:
    """Боец с оружием в обеих руках: меч в правой, клинок во второй."""
    player = make_player()
    await db.save_player(player)
    sword = await db.add_gear(player.user_id, SWORD)
    blade = await db.add_gear(player.user_id, BLADE)
    sword.slot = Slot.WEAPON
    blade.slot = Slot.OFFHAND
    await db.save_gear(sword)
    await db.save_gear(blade)
    player.gear = await db.list_gear(player.user_id)
    return player, player.find_gear(sword.id), player.find_gear(blade.id)


# ---------- снятие ----------


async def test_the_doll_names_the_cell_a_thing_lies_in(db):
    """Оружие во второй руке должно называть `offhand`, а не `weapon`.

    По этому полю страница и снимает вещь. Пока здесь стоял слот самого
    предмета, нажатие на вторую руку снимало оружие из первой.
    """
    player, sword, blade = await armed(db)

    equipment = player.equipment
    right = slot_payload(equipment, Slot.WEAPON)["item"]
    second = slot_payload(equipment, Slot.OFFHAND)["item"]

    assert right["title"] == sword.title and right["slot"] == "weapon"
    assert second["title"] == blade.title and second["slot"] == "offhand"


async def test_a_shirt_under_a_jacket_still_names_its_own_cell(db):
    """Нижняя вещь клетки тела лежит в своём слоте, а не в слоте куртки."""
    player = make_player()
    await db.save_player(player)
    jacket = await db.add_gear(player.user_id, "leather_jacket")
    shirt = await db.add_gear(player.user_id, "club_tee")
    jacket.slot = Slot.JACKET
    shirt.slot = Slot.SHIRT
    await db.save_gear(jacket)
    await db.save_gear(shirt)
    player.gear = await db.list_gear(player.user_id)

    cell = slot_payload(player.equipment, Slot.JACKET)

    assert cell["item"]["slot"] == "jacket"
    assert cell["under"]["slot"] == "shirt"


async def test_taking_the_second_hand_off_leaves_the_first_armed(db):
    """Тот самый баг: снимали вторую руку — уходило оружие из первой."""
    player, sword, blade = await armed(db)

    taken = await unequip(db, player, Slot.OFFHAND)

    assert taken.id == blade.id, "снялась не та вещь"
    fresh = await db.get_player(player.user_id)
    assert fresh.gear_in_slot(Slot.WEAPON) is not None, "меч остался в руке"
    assert fresh.gear_in_slot(Slot.WEAPON).id == sword.id
    assert fresh.gear_in_slot(Slot.OFFHAND) is None


async def test_the_second_hand_can_be_emptied_twice_in_a_row(db):
    """Второе нажатие отвечает про пустую вторую руку, а не про первую."""
    player, sword, _ = await armed(db)
    await unequip(db, player, Slot.OFFHAND)
    player.gear = await db.list_gear(player.user_id)

    with pytest.raises(InventoryError, match="пуст"):
        await unequip(db, player, Slot.OFFHAND)

    # И меч всё ещё на бойце: отказ ничего не снял
    assert player.gear_in_slot(Slot.WEAPON).id == sword.id


async def test_the_page_unequips_exactly_the_cell_it_was_tapped_on(db):
    """Путь целиком: клетка второй руки шлёт свой слот, сервер снимает её."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot.config import Config
    from bot.webapp.server import create_app
    from tests.test_inventory import FakeBot
    from tests.test_webapp import TOKEN, make_init_data

    player, sword, _ = await armed(db)
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    headers = {"X-Telegram-Init-Data": make_init_data(player.user_id)}
    async with TestClient(TestServer(app)) as client:
        card = await (await client.get("/api/card", headers=headers)).json()
        second = next(
            cell for cell in card["slots"]["left"] + card["slots"]["right"]
            if cell["slot"] == "offhand"
        )
        # Страница шлёт ровно то, что пришло в клетке
        response = await client.post(
            "/api/unequip", json={"slot": second["item"]["slot"]}, headers=headers
        )
        body = await response.json()

    assert response.status == 200
    worn = {cell["slot"]: cell["item"] for cell in
            body["slots"]["left"] + body["slots"]["right"]}
    assert worn["offhand"] is None, "вторая рука опустела"
    assert worn["weapon"] is not None, "первая осталась с мечом"
    assert worn["weapon"]["title"] == sword.title


# ---------- удар второй рукой ----------


def two_handed(offhand_mod: tuple[str, int] | None = None) -> Fighter:
    blade = OwnedItem(item=CATALOGUE[BLADE], id=2, slot=Slot.OFFHAND)
    if offhand_mod:
        blade.modify(*offhand_mod)
    return Fighter(
        user_id=1,
        name="Victor",
        fclass=get_class("assassin"),
        stats=Stats(strength=20, agility=20, intuition=20, endurance=20),
        level=10,
        equipment=Equipment(
            items={
                Slot.WEAPON: OwnedItem(item=CATALOGUE[SWORD], id=1, slot=Slot.WEAPON),
                Slot.OFFHAND: blade,
            }
        ),
    )


def test_both_hands_are_armed_and_named_by_their_own_weapon():
    fighter = two_handed()

    assert fighter.attacks_per_round == 2
    assert fighter.weapons == ("световым мечом", "скрытым клинком")
    # Урон считается по своей руке, а не по первой
    assert fighter.equipment.weapon_damages == (
        (CATALOGUE[SWORD].damage_min, CATALOGUE[SWORD].damage_max),
        (CATALOGUE[BLADE].damage_min, CATALOGUE[BLADE].damage_max),
    )


def test_a_modifier_on_the_second_hand_reaches_its_damage():
    """Заточка на клинке во второй руке должна доходить до удара."""
    plain = two_handed().equipment.weapon_damages[1]
    sharp = two_handed(("sharpen_weapon_1", 5)).equipment.weapon_damages[1]

    assert sharp == (plain[0] + 5, plain[1] + 5)
    # И бросок берёт именно её, а не голый предмет
    rng = random.Random(0)
    rolls = [two_handed(("sharpen_weapon_1", 5)).equipment.roll_weapon_damage(1, rng)
             for _ in range(30)]
    assert min(rolls) >= sharp[0] and max(rolls) <= sharp[1]


def test_equal_weapons_hit_equally_from_either_hand():
    """Главная жалоба: второй удар будто без оружия. Меряем.

    Обе руки бьют в одну зону, чтобы броня зоны не путала счёт, и оружие
    подобрано почти равным: меч 7–15 против заточенного клинка 8–14.
    Средние должны сойтись — разница в руке роли не играет.
    """
    victor = two_handed(("sharpen_weapon_1", 5))
    boss = Fighter(
        user_id=2, name="Босс", fclass=get_class("tank"),
        stats=Stats(strength=16, agility=2, intuition=11, endurance=33),
        level=14, hp=10**7,
    )
    rng = random.Random(42)
    hits: dict[str, list[int]] = {}
    for _ in range(600):
        result = resolve_round(
            victor,
            Action(attacks=(Zone.HEAD, Zone.HEAD), block=(Zone.HEAD, Zone.CHEST)),
            boss,
            Action(attacks=(Zone.LEGS,), block=(Zone.LEGS, Zone.BELT)),
            1,
            rng,
        )
        for strike in result.strikes:
            if strike.attacker_id == victor.user_id and strike.damage:
                hits.setdefault(strike.weapon, []).append(strike.damage)
        victor.hp = victor.max_hp
        boss.hp = 10**7

    sword = hits["световым мечом"]
    blade = hits["скрытым клинком"]
    assert len(sword) > 200 and len(blade) > 200, "обе руки должны бить"
    first, second = sum(sword) / len(sword), sum(blade) / len(blade)
    # Оружие почти равное — и средние расходятся в пределах десятой доли
    assert abs(first - second) / first < 0.1, (
        f"руки бьют по-разному: {first:.1f} против {second:.1f}"
    )


def test_an_empty_main_hand_makes_the_first_strike_bare_not_the_second():
    """Если первая рука пуста, кулаком бьёт она, а не вторая.

    Так выглядит боец, у которого баг со снятием увёл оружие из первой
    руки: в логе первая строка будет «кулаком», а не вторая.
    """
    blade = OwnedItem(item=CATALOGUE[BLADE], id=2, slot=Slot.OFFHAND)
    fighter = Fighter(
        user_id=1, name="Victor", fclass=get_class("assassin"),
        stats=Stats(strength=20, agility=20, intuition=20, endurance=20),
        level=10, equipment=Equipment(items={Slot.OFFHAND: blade}),
    )

    assert fighter.attacks_per_round == 2
    assert fighter.weapons == ("кулаком", "скрытым клинком")
    assert fighter.equipment.weapon_damages[0] == (0, 0)
    assert fighter.equipment.weapon_damages[1] != (0, 0)
