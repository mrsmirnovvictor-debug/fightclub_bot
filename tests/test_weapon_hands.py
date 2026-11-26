"""Правила рук: что куда надевается и что при этом уезжает в рюкзак.

Прежде хвата у оружия не было вовсе: `slots` разрешал каждому оружию обе
руки, и двуручная кувалда уживалась со щитом. Теперь рука у вещи своя, и
связь между руками односторонняя — вторая живёт при основной.

Надетое по старым правилам эти проверки не трогают: правило спрашивают,
когда надевают, а не когда смотрят на бойца.
"""

import pytest

from bot.database import Database
from bot.game.classes import Stats
from bot.game.equipment import CATALOGUE, Item, ItemKind, Slot, WeaponHand
from bot.game.gear import hold_required, missing_mastery
from bot.game.mastery import HoldType, WeaponFamily
from bot.inventory_service import InventoryError, equip, unequip
from bot.models import Player

SWORDS = WeaponFamily.SWORDS.value
KNIVES = WeaponFamily.KNIVES.value


def a_weapon(code: str, hand: WeaponHand, level: int = 1, family: str = SWORDS) -> Item:
    """Оружие нужного хвата: каталог к этому этапу ещё не размечен."""
    return Item(
        code,
        f"Пробное {code}",
        Slot.WEAPON,
        "⚔️",
        kind=ItemKind.WEAPON,
        instrumental="клинком",
        damage_min=5,
        damage_max=9,
        family=family,
        hand=hand,
        level_required=level,
    )


@pytest.fixture
def hands(monkeypatch) -> dict[str, Item]:
    """Три хвата и щит, подложенные в каталог на время теста."""
    kit = {
        "probe_main": a_weapon("probe_main", WeaponHand.MAIN),
        "probe_second": a_weapon("probe_second", WeaponHand.OFFHAND, family=KNIVES),
        "probe_both": a_weapon("probe_both", WeaponHand.TWO_HANDED),
    }
    for code, item in kit.items():
        monkeypatch.setitem(CATALOGUE, code, item)
    return kit


async def a_fighter(db: Database) -> Player:
    player = Player(
        user_id=42,
        nickname="Тайлер",
        class_code="assassin",
        level=10,
        **Stats(strength=20, agility=20, intuition=20, endurance=20).as_dict(),
    )
    await db.save_player(player)
    return player


async def give(db: Database, player: Player, code: str):
    owned = await db.add_gear(player.user_id, code)
    player.gear = await db.list_gear(player.user_id)
    return player.find_gear(owned.id)


# ---------- куда что надевается ----------


def test_the_hand_decides_the_cell(hands):
    """Место у оружия одно, и выбирает его хват.

    Основное — в основную руку, второе — во вторую, двуручное — в
    основную: вторую оно занимает собой, и клетки для него там нет.
    """
    assert hands["probe_main"].slots == (Slot.WEAPON,)
    # Лёгкое оружие годится в обе руки: нож остаётся ножом в любой, и
    # запретить держать его одного значило бы оставить целое семейство
    # без основного оружия
    assert hands["probe_second"].slots == (Slot.WEAPON, Slot.OFFHAND)
    assert hands["probe_both"].slots == (Slot.WEAPON,)
    assert hands["probe_both"].two_handed
    assert not hands["probe_main"].two_handed


async def test_a_main_hand_weapon_does_not_go_into_the_second(db, hands):
    """Основное оружие во вторую руку не надевается вовсе."""
    player = await a_fighter(db)
    owned = await give(db, player, "probe_main")

    with pytest.raises(InventoryError) as oops:
        await equip(db, player, owned.id, Slot.OFFHAND)
    assert "не надевается" in str(oops.value)


async def test_a_second_weapon_waits_for_the_first(db, hands):
    """Во вторую руку оружие берут в пару к первому, а не вместо него.

    Само по себе лёгкое оружие — обычное оружие своего вида: не назвали
    клетку — оно идёт в основную руку.
    """
    player = await a_fighter(db)
    second = await give(db, player, "probe_second")

    with pytest.raises(InventoryError) as oops:
        await equip(db, player, second.id, Slot.OFFHAND)
    assert "основная рука пуста" in str(oops.value)

    # Без названной клетки — в основную руку, как всякое оружие
    await equip(db, player, second.id)
    assert player.gear_in_slot(Slot.WEAPON).code == "probe_second"

    main = await give(db, player, "probe_main")
    await equip(db, player, main.id)
    await equip(db, player, second.id, Slot.OFFHAND)

    assert player.gear_in_slot(Slot.WEAPON).code == "probe_main"
    assert player.gear_in_slot(Slot.OFFHAND).code == "probe_second"
    assert player.equipment.second_weapon is not None


# ---------- двуручное ----------


async def test_two_handed_clears_the_second_hand(db, hands):
    """Двуручное занимает обе руки: из второй вещь уезжает в рюкзак.

    Молча отказать было бы хуже: боец выбрал оружие, и выбор надо
    исполнить, а не объяснять, почему он невозможен.
    """
    player = await a_fighter(db)
    main = await give(db, player, "probe_main")
    second = await give(db, player, "probe_second")
    await equip(db, player, main.id)
    await equip(db, player, second.id)

    both = await give(db, player, "probe_both")
    await equip(db, player, both.id)

    assert player.gear_in_slot(Slot.WEAPON).code == "probe_both"
    assert player.gear_in_slot(Slot.OFFHAND) is None
    assert player.equipment.two_handed
    # Вещь не пропала: она в рюкзаке, и её можно надеть снова
    assert any(one.code == "probe_second" for one in player.backpack)


async def test_nothing_stands_beside_a_two_handed_weapon(db, hands):
    """Рядом с двуручным не встаёт ни щит, ни второе оружие."""
    player = await a_fighter(db)
    both = await give(db, player, "probe_both")
    await equip(db, player, both.id)

    shield = await give(db, player, "set_sport_shield")
    second = await give(db, player, "probe_second")

    for owned in (shield, second):
        with pytest.raises(InventoryError) as oops:
            await equip(db, player, owned.id, Slot.OFFHAND)
        assert "обе руки" in str(oops.value), owned.code


# ---------- снятие ----------


async def test_taking_off_the_main_hand_takes_the_second_with_it(db, hands):
    """Второе оружие живёт при первом: сняли первое — ушло и второе.

    Запрет на снятие держал бы бойца в чужой сборке, поэтому зависимую
    вещь безопасно уводим в рюкзак.
    """
    player = await a_fighter(db)
    main = await give(db, player, "probe_main")
    second = await give(db, player, "probe_second")
    await equip(db, player, main.id)
    await equip(db, player, second.id)

    await unequip(db, player, Slot.WEAPON)

    assert player.gear_in_slot(Slot.WEAPON) is None
    assert player.gear_in_slot(Slot.OFFHAND) is None
    assert {one.code for one in player.backpack} >= {"probe_main", "probe_second"}


async def test_a_shield_stays_when_the_weapon_goes(db, hands):
    """Щит на второй руке сам по себе: без оружия он держится.

    Он не второе оружие, а защита: боец с одним щитом — это боец,
    который закрылся, а не сломанная сборка.
    """
    player = await a_fighter(db)
    main = await give(db, player, "probe_main")
    shield = await give(db, player, "set_sport_shield")
    await equip(db, player, main.id)
    await equip(db, player, shield.id)

    await unequip(db, player, Slot.WEAPON)

    assert player.gear_in_slot(Slot.OFFHAND) is shield


# ---------- требование владения ----------


def test_the_threshold_is_the_level_of_the_thing(hands):
    """Требуемое владение равно уровню вещи и считается, а не хранится."""
    sword = a_weapon("probe_level", WeaponHand.MAIN, level=7)

    assert sword.mastery_required == 7
    assert hold_required(sword) == ""
    both = a_weapon("probe_two", WeaponHand.TWO_HANDED, level=3)
    assert hold_required(both) == HoldType.TWO_HANDED.value
    assert hold_required(CATALOGUE["set_sport_shield"]) == HoldType.SHIELD.value
    # Хват зависит от клетки: лёгкое оружие в первой руке — обычное
    # оружие своего вида, во второй — уже «Второе оружие»
    light = a_weapon("probe_light", WeaponHand.OFFHAND, level=3, family=KNIVES)
    assert hold_required(light, Slot.WEAPON) == ""
    assert hold_required(light, Slot.OFFHAND) == HoldType.OFFHAND.value


def test_the_gaps_in_mastery_are_named_one_by_one(hands):
    """Отказ разбирается на части: мало вида — одно, мало хвата — другое.

    Экран показывает их порознь, поэтому и правило отвечает кодами, а
    не одним «нельзя».
    """
    both = a_weapon("probe_heavy", WeaponHand.TWO_HANDED, level=5)

    assert missing_mastery(both, {}, {}) == ["family", "hold"]
    assert missing_mastery(both, {SWORDS: 5}, {}) == ["hold"]
    assert missing_mastery(both, {}, {HoldType.TWO_HANDED.value: 5}) == ["family"]
    assert missing_mastery(both, {SWORDS: 5}, {HoldType.TWO_HANDED.value: 5}) == []
    # Чужой вид не помогает: владение мечами не держит молот
    assert missing_mastery(both, {KNIVES: 9}, {HoldType.TWO_HANDED.value: 5}) == [
        "family"
    ]


def test_a_thing_without_a_family_asks_for_no_mastery(hands):
    """Одежде и огнестрелу владение не требуется: вида у них нет."""
    assert CATALOGUE["club_tee"].mastery_required == 0
    assert missing_mastery(CATALOGUE["club_tee"], {}, {}) == []
    firearm = next(
        one for one in CATALOGUE.values() if one.kind is ItemKind.FIREARM
    )
    assert firearm.mastery_required == 0
    assert missing_mastery(firearm, {}, {}) == []


# ---------- разметка каталога ----------


def test_every_weapon_in_the_catalogue_knows_its_family():
    """Оружия без вида в каталоге нет: новая вещь обязана попасть в таблицу.

    Таблица лежит в `bot.content.arming` и правится руками. Вещь,
    которую туда забыли вписать, без этой проверки молча осталась бы
    без вида — и требовала бы нулевого владения, то есть ничего.
    """
    from bot.game.equipment import CATALOGUE

    armed = [one for one in CATALOGUE.values() if one.kind is ItemKind.WEAPON]
    assert armed, "каталог оружия пуст"
    assert [one.code for one in armed if not one.family] == []
    # И у каждого семейства есть кем драться на разных уровнях
    families = {one.family for one in armed}
    assert len(families) == 4, families


def test_heavy_weapons_do_not_fit_the_second_hand():
    """Второй рукой берут только лёгкое: кувалда в ней не держится."""
    from bot.game.equipment import CATALOGUE

    for code in ("sledge", "viking_axe", "bat", "katana", "greatsword"):
        assert CATALOGUE[code].slots == (Slot.WEAPON,), code
    for code in ("knife", "knuckles", "razor", "shiv"):
        assert CATALOGUE[code].slots == (Slot.WEAPON, Slot.OFFHAND), code


def test_the_two_handed_are_named_by_the_table():
    """Двуручных пока двое, и обоих выдаёт собственное название."""
    from bot.content.arming import TWO_HANDED
    from bot.game.equipment import CATALOGUE

    assert TWO_HANDED == {"greatsword", "set_hero_weapon"}
    for code in TWO_HANDED:
        assert CATALOGUE[code].two_handed, code
        assert CATALOGUE[code].family == SWORDS, code
