"""Инвентарь: покупка, надевание, износ в боях и починка."""

import random

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import FIGHTER_CLASSES, Stats, Zone, get_class
from bot.game.modes import FightMode
from bot.game.economy import credits_per_level
from bot.game.equipment import (
    ALL_SLOTS,
    CATALOGUE,
    MAGIC_ITEMS,
    SHOWCASE,
    EARLY_LEVELS,
    EARLY_SHARE_CAP,
    LATE_SHARE_CAP,
    MAX_WEAR,
    Equipment,
    Item,
    ItemKind,
    OwnedItem,
    Slot,
    apply_fight_wear,
    can_equip,
    describe_requirements,
    items_unlocked_at,
    repair,
    shop_sections,
)
from bot.inventory_service import InventoryError, buy, equip, repair_item, unequip
from bot.inventory_service import wear_after_fight
from bot.models import Player
from bot.webapp.card import build_card, build_shop
from bot.webapp.server import create_app
from tests.test_webapp import TOKEN, make_init_data

ARMED = FightMode.ARMED
KNUCKLES = CATALOGUE["knuckles"]
SNEAKERS = CATALOGUE["sneakers"]
WRAPS = CATALOGUE["wraps"]


class Dice:
    """Кости с заранее известным исходом: сколько значений задали, столько и выпадет."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def random(self) -> float:
        return self.values.pop(0) if self.values else 1.0


def make_player(user_id: int = 1, credits: int = 500, **kwargs) -> Player:
    fclass = get_class(kwargs.pop("class_code", "warrior"))
    stats = kwargs.pop("stats", Stats(strength=8, agility=8, intuition=8, endurance=8))
    return Player(
        user_id=user_id,
        nickname=kwargs.pop("nickname", "Тайлер"),
        class_code=fclass.code,
        strength=stats.strength,
        agility=stats.agility,
        intuition=stats.intuition,
        endurance=stats.endurance,
        level=kwargs.pop("level", 5),
        credits=credits,
        **kwargs,
    )


# ---------- правила износа ----------


def test_new_item_is_pristine():
    owned = OwnedItem(item=SNEAKERS)
    assert owned.describe_wear() == f"0/{MAX_WEAR}"
    assert owned.repair_price == 0
    assert not owned.is_worn_out


def test_loser_wears_gear_far_more_often_than_winner():
    rng = random.Random(7)
    losses = sum(apply_fight_wear([OwnedItem(item=SNEAKERS)], False, rng)[0] != [] for _ in range(2000))
    wins = sum(apply_fight_wear([OwnedItem(item=SNEAKERS)], True, rng)[0] != [] for _ in range(2000))
    assert 0.70 < losses / 2000 < 0.80
    assert 0.06 < wins / 2000 < 0.14


def test_the_last_point_of_wear_turns_the_item_into_dust():
    owned = OwnedItem(item=SNEAKERS, wear=MAX_WEAR - 1)
    damaged, broken = apply_fight_wear([owned], won=False, rng=Dice(0.0))
    assert damaged == [owned] and broken == [owned]
    assert owned.wear == MAX_WEAR
    assert owned.is_worn_out


def test_repair_costs_a_credit_per_point():
    owned = OwnedItem(item=SNEAKERS, wear=19)
    assert owned.repair_price == 19
    result = repair(owned, 19, Dice(0.99))  # прочность уцелела
    assert (result.points, result.price) == (19, 19)
    assert owned.wear == 0
    assert owned.max_wear == MAX_WEAR
    assert not result.degraded


def test_each_repair_risks_a_point_of_durability():
    owned = OwnedItem(item=SNEAKERS, wear=19)
    result = repair(owned, 19, Dice(0.0))
    assert result.degraded
    assert owned.max_wear == MAX_WEAR - 1


def test_repairing_bit_by_bit_kills_the_item_faster():
    """Пять походов к мастеру — пять бросков на прочность, один поход — один."""
    at_once = OwnedItem(item=SNEAKERS, wear=5)
    repair(at_once, 5, Dice(0.0))
    piecemeal = OwnedItem(item=SNEAKERS, wear=5)
    for _ in range(5):
        repair(piecemeal, 1, Dice(0.0))

    assert at_once.max_wear == MAX_WEAR - 1
    assert piecemeal.max_wear == MAX_WEAR - 5
    assert at_once.wear == piecemeal.wear == 0


def test_item_crumbles_when_durability_runs_out():
    owned = OwnedItem(item=SNEAKERS, wear=1, max_wear=1)
    result = repair(owned, 1, Dice(0.0))
    assert result.destroyed
    assert owned.max_wear == 0


# ---------- требования ----------


def test_requirements_are_counted_by_own_stats():
    weak = Stats(strength=5, agility=5, intuition=5, endurance=5)
    strong = Stats(strength=6, agility=5, intuition=5, endurance=5)
    assert not can_equip(KNUCKLES, level=5, stats=weak)  # не хватает силы
    assert not can_equip(KNUCKLES, level=2, stats=strong)  # не хватает уровня
    assert can_equip(KNUCKLES, level=3, stats=strong)
    assert describe_requirements(KNUCKLES) == "уровень 3, сила 6"


# ---------- лавка и инвентарь ----------


async def test_buying_moves_credits_into_the_backpack(db):
    player = make_player(credits=100)
    await db.save_player(player)

    owned = await buy(db, player, "sneakers")
    assert player.credits == 100 - SNEAKERS.price
    assert owned.id > 0

    saved = await db.get_player(player.user_id)
    assert [item.code for item in saved.backpack] == ["sneakers"]
    assert saved.credits == 100 - SNEAKERS.price
    assert saved.equipment.bonus == Stats()  # лежит в рюкзаке, статов не даёт


async def test_shop_refuses_when_credits_run_short(db):
    player = make_player(credits=10)
    await db.save_player(player)
    with pytest.raises(InventoryError, match="Не хватает кредитов"):
        await buy(db, player, "sneakers")
    assert player.credits == 10
    assert (await db.get_player(player.user_id)).gear == []


async def test_gear_must_be_earned_before_it_is_worn(db):
    player = make_player(level=3, stats=Stats(strength=4, agility=4, intuition=4, endurance=4))
    await db.save_player(player)
    owned = await buy(db, player, "knuckles")  # купить можно, надеть — пока нет

    with pytest.raises(InventoryError, match="Пока не по плечу"):
        await equip(db, player, owned.id)
    assert (await db.get_player(player.user_id)).equipped == []


async def test_equipping_moves_the_item_from_the_backpack_into_the_slot(db):
    player = make_player()
    await db.save_player(player)
    owned = await buy(db, player, "knuckles")
    before = player.max_hp

    await equip(db, player, owned.id)

    saved = await db.get_player(player.user_id)
    assert saved.backpack == []
    assert saved.gear_in_slot(Slot.WEAPON).code == "knuckles"
    assert saved.stats.strength == player.base_stats.strength + KNUCKLES.strength
    assert saved.equipment.weapon_names == ("кастетом",)
    assert saved.max_hp >= before


async def test_the_slot_holds_one_thing_at_a_time(db):
    player = make_player()
    await db.save_player(player)
    first = await buy(db, player, "knuckles")
    second = await buy(db, player, "knuckles")

    await equip(db, player, first.id)
    await equip(db, player, second.id)

    saved = await db.get_player(player.user_id)
    assert saved.gear_in_slot(Slot.WEAPON).id == second.id
    assert [item.id for item in saved.backpack] == [first.id]


async def test_a_second_weapon_goes_into_the_shield_hand(db):
    player = make_player()
    await db.save_player(player)
    weapon = await buy(db, player, "knuckles")
    offhand = await buy(db, player, "knuckles")

    await equip(db, player, weapon.id)
    await equip(db, player, offhand.id, Slot.SHIELD)

    saved = await db.get_player(player.user_id)
    assert saved.equipment.second_weapon is not None
    assert not saved.equipment.has_shield

    boots = await buy(db, player, "sneakers")
    with pytest.raises(InventoryError, match="в этот слот не надевается"):
        await equip(db, player, boots.id, Slot.HEAD)


async def test_taking_the_item_off_returns_it_to_the_backpack(db):
    player = make_player()
    await db.save_player(player)
    owned = await buy(db, player, "sneakers")
    await equip(db, player, owned.id)

    await unequip(db, player, Slot.BOOTS)

    saved = await db.get_player(player.user_id)
    assert [item.id for item in saved.backpack] == [owned.id]
    assert saved.equipment.bonus == Stats()
    with pytest.raises(InventoryError, match="Слот и так пуст"):
        await unequip(db, player, Slot.BOOTS)


async def test_repair_charges_the_credits_and_saves_the_result(db):
    player = make_player(credits=100)
    await db.save_player(player)
    owned = await buy(db, player, "sneakers")
    owned.wear = 7
    await db.save_gear(owned)
    paid = player.credits

    result = await repair_item(db, player, owned.id, rng=Dice(0.99))

    assert (result.points, result.price) == (7, 7)
    saved = await db.get_player(player.user_id)
    assert saved.credits == paid - 7
    assert saved.backpack[0].wear == 0


async def test_repair_needs_credits_and_something_to_repair(db):
    player = make_player(credits=100)
    await db.save_player(player)
    owned = await buy(db, player, "wraps")
    player.credits = 3  # кредиты кончились после покупки
    await db.save_player(player)

    with pytest.raises(InventoryError, match="и так как новая"):
        await repair_item(db, player, owned.id)

    owned.wear = 10
    await db.save_gear(owned)
    with pytest.raises(InventoryError, match="Не хватает кредитов"):
        await repair_item(db, player, owned.id)
    # частичная починка на то, что есть, проходит
    result = await repair_item(db, player, owned.id, points=3, rng=Dice(0.99))
    assert (result.points, result.price) == (3, 3)
    assert player.credits == 0


async def test_a_finished_item_disappears_from_the_bag_for_good(db):
    player = make_player()
    await db.save_player(player)
    owned = await buy(db, player, "sneakers")
    owned.wear, owned.max_wear = 1, 1
    await db.save_gear(owned)

    result = await repair_item(db, player, owned.id, rng=Dice(0.0))

    assert result.destroyed
    assert (await db.get_player(player.user_id)).gear == []


async def test_wear_after_the_fight_reaches_the_database(db):
    player = make_player()
    await db.save_player(player)
    owned = await buy(db, player, "sneakers")
    await equip(db, player, owned.id)

    broken = await wear_after_fight(db, player, won=False, rng=Dice(0.0))

    assert broken == []
    assert (await db.get_player(player.user_id)).equipped[0].wear == 1


async def test_gear_worn_to_dust_leaves_both_the_slot_and_the_bag(db):
    player = make_player()
    await db.save_player(player)
    owned = await buy(db, player, "sneakers")
    await equip(db, player, owned.id)
    owned.wear = MAX_WEAR - 1
    await db.save_gear(owned)

    broken = await wear_after_fight(db, player, won=False, rng=Dice(0.0))

    assert [item.code for item in broken] == ["sneakers"]
    saved = await db.get_player(player.user_id)
    assert saved.gear == []
    assert saved.equipment.get(Slot.BOOTS) is None


async def test_deleting_a_fighter_takes_the_inventory_with_him(db):
    player = make_player()
    await db.save_player(player)
    await buy(db, player, "sneakers")

    await db.delete_player(player.user_id)

    assert await db.list_gear(player.user_id) == []


# ---------- карточка ----------


def test_inventory_rows_carry_everything_the_screen_draws():
    player = make_player(level=1, stats=Stats(strength=4, agility=4, intuition=4, endurance=4))
    player.gear = [OwnedItem(item=KNUCKLES, id=11, wear=3)]

    card = build_card(player, TOKEN, viewer_id=player.user_id)
    row = card["inventory"][0]

    assert row["title"] == "Кастет"
    assert row["slot_title"] == "Оружие"
    assert row["wear_text"] == f"3/{MAX_WEAR}"
    assert row["repair_price"] == 3
    assert row["can_equip"] is False
    gain = {line["title"]: line for line in row["bonuses"]}
    assert gain["Сила"]["value"] == CATALOGUE["knuckles"].strength
    # у оружия в свойствах стоит диапазон урона, а не прибавка со знаком плюс
    assert gain["Урон"]["text"] == CATALOGUE["knuckles"].describe_damage()
    # уровень и сила не дотягивают — обе строки требований помечены красным
    assert [need["ok"] for need in row["requirements"]] == [False, False]
    # оружие можно взять и во вторую руку
    assert [slot["slot"] for slot in row["slots"]] == ["weapon", "shield"]


def test_strangers_do_not_see_the_backpack():
    player = make_player()
    player.gear = [OwnedItem(item=KNUCKLES, id=11)]
    assert build_card(player, TOKEN, viewer_id=999)["inventory"] == []
    assert build_card(player, TOKEN, viewer_id=player.user_id)["inventory"]


def test_worn_gear_shows_its_wear_in_the_slot():
    player = make_player()
    player.gear = [OwnedItem(item=SNEAKERS, id=11, wear=4, slot=Slot.BOOTS)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    boots = next(s for s in card["slots"]["right"] if s["slot"] == "boots")
    assert boots["item"]["wear"] == 4
    assert boots["item"]["image"] == SNEAKERS.image


# ---------- ручки мини-аппа ----------


class FakeBot:
    async def get_file(self, file_id):  # pragma: no cover - аватар тут не трогаем
        raise AssertionError

    async def download_file(self, path):  # pragma: no cover
        raise AssertionError


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    app = create_app(FakeBot(), db, config)
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
def bot_and_db(db):
    """Фейковый бот дуэльных тестов вместе со свежей базой."""
    from tests.test_duel_flow import FakeBot

    return FakeBot(), db


def headers(user_id: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


async def test_mini_app_dresses_and_undresses_the_fighter(client, db):
    player = make_player(user_id=42)
    await db.save_player(player)
    owned = await buy(db, player, "sneakers")

    response = await client.post(
        "/api/equip", json={"item_id": owned.id}, headers=headers(42)
    )
    assert response.status == 200
    card = await response.json()
    assert card["inventory"] == []
    boots = next(s for s in card["slots"]["right"] if s["slot"] == "boots")
    assert boots["item"]["title"] == "Кеды"

    response = await client.post(
        "/api/unequip", json={"slot": "boots"}, headers=headers(42)
    )
    card = await response.json()
    assert [item["title"] for item in card["inventory"]] == ["Кеды"]


async def test_mini_app_explains_why_the_button_is_grey(client, db):
    player = make_player(user_id=42, level=3, stats=Stats(strength=4, agility=4, intuition=4, endurance=4))
    await db.save_player(player)
    owned = await buy(db, player, "knuckles")

    response = await client.post(
        "/api/equip", json={"item_id": owned.id}, headers=headers(42)
    )
    assert response.status == 409
    assert "Пока не по плечу" in (await response.json())["error"]


async def test_mini_app_repairs_for_credits(client, db):
    player = make_player(user_id=42, credits=200)
    await db.save_player(player)
    owned = await buy(db, player, "sneakers")
    owned.wear = 5
    await db.save_gear(owned)

    response = await client.post(
        "/api/repair", json={"item_id": owned.id}, headers=headers(42)
    )
    body = await response.json()

    assert body["repair"]["points"] == 5
    assert body["repair"]["price"] == 5
    assert body["card"]["inventory"][0]["wear"] == 0
    assert body["card"]["record"]["credits"] == 200 - SNEAKERS.price - 5


async def test_nobody_touches_a_stranger_backpack(client, db):
    owner = make_player(user_id=42)
    await db.save_player(owner)
    owned = await buy(db, owner, "sneakers")
    await db.save_player(make_player(user_id=43, nickname="Марла"))

    response = await client.post(
        "/api/equip", json={"item_id": owned.id}, headers=headers(43)
    )
    assert response.status == 409
    assert "Такой вещи в инвентаре нет" in (await response.json())["error"]

    response = await client.post("/api/equip", json={"item_id": owned.id})
    assert response.status == 401


def test_catalogue_items_know_their_slot_and_price():
    for item in CATALOGUE.values():
        # За что вещь берут: кредиты в лавке клуба, звёзды у мага — или
        # никак, если это награда: её выдают, а не продают.
        assert item.price > 0 or item.stars > 0 or item.reward
        assert not (item.price and item.stars), f"{item.title}: и кредиты, и звёзды"
        assert not (item.reward and (item.price or item.stars)), (
            f"{item.title}: награда с ценником"
        )
        assert item.slot in item.slots
        assert isinstance(item, Item)
        if item.kind is ItemKind.SHIELD:
            assert item.slot is Slot.SHIELD


def test_the_magic_counter_is_kept_out_of_the_club_shop():
    """Звёздный товар не лежит на прилавке за кредиты и не путается с ним."""
    assert MAGIC_ITEMS, "у мага пусто"
    for item in MAGIC_ITEMS:
        assert item.stars > 0 and item.price == 0
        assert item not in SHOWCASE
    assert all(not item.is_magic for item in SHOWCASE)


# ---------- износ в настоящем бою ----------


async def test_gear_wears_out_over_real_fights(bot_and_db):
    """Пара боёв подряд — и на экипировке видны следы."""
    from tests.test_duel_flow import fight_to_the_end, heal_everyone, make_service

    fake_bot, db = bot_and_db
    service = make_service(fake_bot, db)
    for user_id, name in ((1, "Тайлер"), (2, "Марла")):
        player = make_player(user_id=user_id, nickname=name, credits=500)
        await db.save_player(player)
        owned = await buy(db, player, "sneakers")
        await equip(db, player, owned.id)

    for _ in range(3):
        await heal_everyone(db, 1, 2)
        session = await service.start_duel(
            -100, 7, await db.get_player(1), await db.get_player(2), mode=ARMED
        )
        await fight_to_the_end(service, session)

    first, second = await db.get_player(1), await db.get_player(2)
    total = sum(item.wear for item in first.gear + second.gear)
    assert total >= 1, "за три боя обувь не получила ни пункта износа"
    assert all(item.max_wear == MAX_WEAR for item in first.gear + second.gear)


async def test_dust_is_announced_in_the_thread(bot_and_db):
    """Вещь на последнем издыхании рассыпается, и судья об этом говорит."""
    from tests.test_duel_flow import fight_to_the_end, heal_everyone, make_service

    fake_bot, db = bot_and_db
    service = make_service(fake_bot, db)
    for user_id, name in ((1, "Тайлер"), (2, "Марла")):
        player = make_player(user_id=user_id, nickname=name, credits=500)
        await db.save_player(player)
        owned = await buy(db, player, "sneakers")
        await equip(db, player, owned.id)
        owned.wear = MAX_WEAR - 1
        await db.save_gear(owned)

    for _ in range(4):
        await heal_everyone(db, 1, 2)
        session = await service.start_duel(
            -100, 7, await db.get_player(1), await db.get_player(2), mode=ARMED
        )
        await fight_to_the_end(service, session)
        if any("рассыпалась в труху" in text for text in fake_bot.texts):
            break
    else:  # pragma: no cover - за четыре боя износ обязан добить обувь
        raise AssertionError("обувь пережила четыре боя на последнем пункте")

    survivors = (await db.get_player(1)).gear + (await db.get_player(2)).gear
    assert len(survivors) < 2


async def test_nobody_changes_clothes_in_the_middle_of_a_fight(db):
    """Снять вещь посреди боя, чтобы уберечь её от износа, не выйдет."""
    from tests.test_duel_flow import FakeBot, make_service

    service = make_service(FakeBot(), db)
    for user_id, name in ((42, "Тайлер"), (43, "Марла")):
        player = make_player(user_id=user_id, nickname=name)
        await db.save_player(player)
        owned = await buy(db, player, "sneakers")
        await equip(db, player, owned.id)

    await service.start_duel(-100, 7, await db.get_player(42), await db.get_player(43))

    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    app = create_app(FakeBot(), db, config, service)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/unequip", json={"slot": "boots"}, headers=headers(42)
        )
        assert response.status == 409
        assert "Ты на ринге" in (await response.json())["error"]

    assert (await db.get_player(42)).equipped[0].code == "sneakers"
    await service.shutdown()


# ---------- магазин ----------


def test_every_tier_has_something_for_every_class():
    """В каждой партии товара есть вещь под каждый класс."""
    for level in (4, 5, 6, 7, 8, 9):
        covered = {code for item in items_unlocked_at(level) for code in item.for_classes}
        assert covered == set(FIGHTER_CLASSES), f"{level} уровень обошли: {covered}"


def test_a_tier_never_fits_into_one_level_of_income():
    """Развилка: за уровень партию не выкупить, но что-то из неё по карману."""
    income = credits_per_level()
    for level in (4, 5, 6, 7, 8):
        items = items_unlocked_at(level)
        cheapest_per_slot: dict = {}
        for item in items:
            best = cheapest_per_slot.get(item.slot)
            if best is None or item.price < best.price:
                cheapest_per_slot[item.slot] = item
        full_set = sum(item.price for item in cheapest_per_slot.values())
        assert full_set > income, f"{level} уровень: партия за {full_set} — не выбор"
        assert min(item.price for item in items) <= income * 4, (
            f"{level} уровень: даже самое дешёвое копить вечность"
        )


def test_goods_are_sorted_by_type_and_level():
    sections = shop_sections()
    assert [slot for slot, _ in sections] == list(ALL_SLOTS)
    for _, items in sections:
        levels = [item.level_required for item in items]
        assert levels == sorted(levels)
        assert all(item.slot is items[0].slot for item in items)


def test_shop_marks_what_is_locked_owned_and_affordable():
    player = make_player(level=5, credits=100, stats=Stats(strength=13, agility=8, intuition=8, endurance=13))
    player.gear = [OwnedItem(item=CATALOGUE["pipe"], id=1, slot=Slot.WEAPON)]

    shop = build_shop(player)
    weapons = next(s for s in shop["sections"] if s["slot"] == "weapon")
    goods = {row["code"]: row for row in weapons["items"]}

    assert shop["credits"] == 100
    assert goods["pipe"]["owned"] == 1
    assert goods["pipe"]["unlocked"] is True
    assert goods["pipe"]["affordable"] is False  # труба стоит 150
    assert goods["knuckles"]["affordable"] is True
    assert goods["bat"]["unlocked"] is False  # бита — с 6 уровня
    assert goods["bat"]["level_required"] == 6
    assert [c["code"] for c in goods["bat"]["suits"]] == ["warrior"]
    # требования показываются и в лавке: сила у трубы уже есть, уровень биты — нет
    assert all(need["ok"] for need in goods["pipe"]["requirements"])
    assert weapons["open"] < len(weapons["items"])


async def test_the_counter_refuses_goods_above_your_level(db):
    player = make_player(level=5, credits=500, stats=Stats(strength=16, agility=8, intuition=8, endurance=13))
    await db.save_player(player)

    with pytest.raises(InventoryError, match="открывается на 6 уровне"):
        await buy(db, player, "bat")
    assert player.credits == 500

    player.level = 6
    bought = await buy(db, player, "bat")
    assert bought.code == "bat"
    assert player.credits == 500 - CATALOGUE["bat"].price


async def test_mini_app_serves_the_shop_and_takes_the_money(client, db):
    player = make_player(user_id=42, level=4, credits=200, stats=Stats(strength=12, agility=8, intuition=8, endurance=10))
    await db.save_player(player)

    response = await client.get("/api/shop", headers=headers(42))
    assert response.status == 200
    shop = await response.json()
    assert [section["slot"] for section in shop["sections"]] == [
        s.value for s in ALL_SLOTS
    ] + ["misc"]  # эликсиры идут последними: слота у них нет

    response = await client.post("/api/buy", json={"code": "pipe"}, headers=headers(42))
    body = await response.json()

    assert body["bought"] == {
        "code": "pipe",
        "title": "Деревянная бита",
        "price": 150,
        "can_equip": True,
        "consumable": False,
    }
    assert body["shop"]["credits"] == 50
    assert [item["title"] for item in body["card"]["inventory"]] == ["Деревянная бита"]
    weapons = next(s for s in body["shop"]["sections"] if s["slot"] == "weapon")
    assert next(row for row in weapons["items"] if row["code"] == "pipe")["owned"] == 1


async def test_mini_app_shop_needs_your_own_init_data(client, db):
    await db.save_player(make_player(user_id=42))
    assert (await client.get("/api/shop")).status == 401
    assert (await client.post("/api/buy", json={"code": "pipe"})).status == 401


# ---------- числа предметов ----------


def test_percent_bonuses_stay_within_their_caps():
    """Проценты растут со ступенью, но не настолько, чтобы стирать класс.

    Потолок держит лавку клуба — то, что берут за кредиты и что определяет
    баланс между классами. Товар мага живёт по своим правилам: он и должен
    быть заметно сильнее, иначе за него не платили бы звёздами.
    """
    for item in SHOWCASE:
        shares = (item.accuracy, item.dodge, item.crit, item.anticrit, item.counter)
        cap = EARLY_SHARE_CAP if item.level_required <= EARLY_LEVELS else LATE_SHARE_CAP
        assert max(shares) <= cap + 1e-9, f"{item.title}: {max(shares):.0%} > {cap:.0%}"


def test_every_weapon_adds_damage_and_it_grows_with_the_tier():
    """Лестница ступеней — про лавку клуба: у мага своя цена и свой отсчёт."""
    weapons = [item for item in SHOWCASE if item.is_weapon]
    assert weapons
    by_level: dict[int, list[float]] = {}
    for item in weapons:
        assert item.damage_min > 0 and item.damage_max >= item.damage_min
        by_level.setdefault(item.level_required, []).append(
            (item.damage_min + item.damage_max) / 2
        )
    levels = sorted(by_level)
    for lower, upper in zip(levels, levels[1:]):
        assert max(by_level[lower]) < min(by_level[upper]), (
            f"оружие {upper} уровня не сильнее оружия {lower}"
        )


def test_weapon_spread_matches_the_character_of_its_class():
    """У ассасина оружие рвано́е, у танка ровное, у воина с трикстером середина."""
    def spread(code: str) -> float:
        item = CATALOGUE[code]
        return (item.damage_max - item.damage_min) / (item.damage_min + item.damage_max)

    for tier in (
        ("pipe", "switchblade", "awl", "crowbar"),
        ("bat", "machete", "stiletto", "sledge"),
        ("fire_axe", "balisong", "ice_pick", "chain"),
        ("cleaver", "razor", "needle", "pry_bar"),
    ):
        warrior, rogue, assassin, tank = (spread(code) for code in tier)
        assert assassin > warrior > tank, tier
        assert assassin > rogue > tank, tier
        # среднее у всех четверых одно: разводим разброс, а не силу
        averages = {
            (CATALOGUE[code].damage_min + CATALOGUE[code].damage_max) / 2
            for code in tier
        }
        assert len(averages) == 1, f"{tier}: средний урон разъехался — {averages}"


def test_armour_covers_the_zone_it_is_worn_on():
    coverage = {
        "moto_helmet": (Zone.HEAD,),
        "biker_jacket": (Zone.CHEST, Zone.BELLY),
        "buckle_belt": (Zone.BELT,),
        "padded_pants": (Zone.BELT, Zone.LEGS),
        "army_boots": (Zone.LEGS,),
    }
    for code, zones in coverage.items():
        assert CATALOGUE[code].zones == zones, code
    # щит держит всё сразу, перчатки и оружие брони не дают вовсе
    assert set(CATALOGUE["road_sign"].zones) == set(Zone)
    assert CATALOGUE["battered_gloves"].zones == ()
    assert CATALOGUE["cleaver"].zones == ()


def test_items_never_hand_out_endurance():
    """Выносливость растят только руками — вещи дают лишь запас здоровья."""
    for item in CATALOGUE.values():
        assert item.bonus.endurance == 0, item.title
    assert any(item.hp for item in CATALOGUE.values())


def test_gear_percentages_reach_the_fighter():
    """Проценты с вещей складываются и доходят до бойца целиком."""
    equipment = Equipment.from_codes({"weapon": "razor", "gloves": "fingerless_gloves"})
    assert equipment.dodge == pytest.approx(
        CATALOGUE["razor"].dodge + CATALOGUE["fingerless_gloves"].dodge
    )
    tank = Equipment.from_codes({"weapon": "pry_bar", "head": "moto_helmet"})
    assert tank.accuracy == pytest.approx(CATALOGUE["pry_bar"].accuracy)
    assert tank.anticrit == pytest.approx(
        CATALOGUE["pry_bar"].anticrit + CATALOGUE["moto_helmet"].anticrit
    )


def test_pictures_are_wired_to_the_right_bucket():
    """Картинки предметов лежат в R2 и не повторяются у разных вещей."""
    from bot.game.equipment import ART, SHOWCASE

    pictures = [item.image for item in SHOWCASE if item.image]
    assert pictures, "картинок нет вовсе"
    assert len(set(pictures)) == len(pictures), "две вещи делят одну картинку"
    assert all(picture.startswith("https://") for picture in pictures)

    for item in SHOWCASE:
        if item.is_weapon or item.is_shield:
            assert item.image.startswith(ART), f"{item.code}: не из бакета клуба"


def test_the_whole_catalogue_is_drawn():
    """Каждая вещь на прилавке нарисована — значков-заглушек не осталось."""
    from bot.game.equipment import SHOWCASE

    naked = [item.code for item in SHOWCASE if not item.image]
    assert not naked, f"без картинки: {naked}"


def test_pictures_are_not_shared_between_items():
    """Одна картинка на две вещи — почти всегда промах при раскладке файлов."""
    from bot.game.equipment import SHOWCASE

    seen: dict[str, str] = {}
    for item in SHOWCASE:
        twin = seen.setdefault(item.image, item.code)
        assert twin == item.code, f"{item.code} и {twin} делят картинку"


# ---------- образы ----------


async def test_wardrobe_shows_what_is_worn_and_what_is_for_sale(db):
    from bot.game.looks import LOOKS, LOOK_PRICE
    from bot.looks_service import wardrobe

    player = make_player(credits=0)
    await db.save_player(player)

    rows = await wardrobe(db, player)
    # Образ подписки в гардеробе не висит, пока его не выдали
    assert len(rows) == len([look for look in LOOKS if not look.pro])
    assert sum(1 for row in rows if row["price"] == 0) == 6
    assert sum(1 for row in rows if row["price"] == LOOK_PRICE) == 6
    assert {row["gender"] for row in rows} == {"male", "female"}

    # бесплатные свои, платные — нет, и без кредитов они не по карману
    assert all(row["owned"] for row in rows if not row["price"])
    assert not any(row["owned"] for row in rows if row["price"])
    assert not any(row["affordable"] for row in rows if row["price"])

    # пока образ не выбирали, ни один не отмечен: в рамке значок бойца
    assert not any(row["current"] for row in rows)

    player.look = "worker"
    assert [row["code"] for row in await wardrobe(db, player) if row["current"]] == [
        "worker"
    ]


async def test_a_free_look_is_just_put_on(db):
    from bot.looks_service import choose_look

    player = make_player(credits=50)
    await db.save_player(player)

    choice = await choose_look(db, player, "barmaid")

    assert not choice.bought and choice.credits == 50
    assert (await db.get_player(player.user_id)).look == "barmaid"
    assert not await db.owned_looks(player.user_id)  # платить было не за что


async def test_a_paid_look_is_bought_once_and_stays_forever(db):
    from bot.game.looks import LOOK_PRICE
    from bot.looks_service import choose_look

    player = make_player(credits=LOOK_PRICE + 200)
    await db.save_player(player)

    bought = await choose_look(db, player, "queen")
    assert bought.bought and bought.credits == 200
    assert await db.owned_looks(player.user_id) == {"queen"}

    # ушёл на бесплатный и вернулся — второй раз не платит
    await choose_look(db, player, "rookie")
    again = await choose_look(db, player, "queen")
    assert not again.bought
    assert (await db.get_player(player.user_id)).credits == 200


async def test_a_look_you_cannot_afford_is_refused(db):
    from bot.looks_service import LookError, choose_look

    player = make_player(credits=999)
    await db.save_player(player)

    with pytest.raises(LookError, match="на счету"):
        await choose_look(db, player, "ghost")
    with pytest.raises(LookError, match="нет"):
        await choose_look(db, player, "император")

    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 999 and not fresh.look
    assert not await db.owned_looks(player.user_id)


async def test_choosing_a_look_takes_the_photo_off(db):
    """Образ и фото — одна рамка: выбрал образ, значит фото убрали."""
    from bot.looks_service import choose_look

    player = make_player()
    player.avatar_file_id = "file-123"
    await db.save_player(player)

    await choose_look(db, player, "racer")

    fresh = await db.get_player(player.user_id)
    assert fresh.look == "racer" and fresh.avatar_file_id is None


async def test_mini_app_serves_the_wardrobe_and_charges_for_a_look(client, db):
    from bot.game.looks import LOOK_PRICE

    player = make_player(credits=LOOK_PRICE)
    await db.save_player(player)

    listing = await client.get("/api/looks", headers=headers(1))
    body = await listing.json()
    assert listing.status == 200 and len(body["looks"]) == 12

    response = await client.post(
        "/api/look", json={"code": "veteran"}, headers=headers(1)
    )
    result = await response.json()
    assert response.status == 200
    assert result["chosen"]["bought"] and result["chosen"]["credits"] == 0
    assert result["card"]["avatar"]["look"] == "veteran"
    assert [row["current"] for row in result["looks"] if row["code"] == "veteran"] == [True]

    # второй платный уже не по карману
    refused = await client.post(
        "/api/look", json={"code": "boss"}, headers=headers(1)
    )
    assert refused.status == 409
    assert "на счету" in (await refused.json())["error"]


async def test_the_wardrobe_is_yours_alone(client):
    assert (await client.get("/api/looks")).status == 401
    assert (await client.post("/api/look", json={"code": "queen"})).status == 401


def test_shop_sections_are_named_after_body_parts():
    """В лавке разделы называются частью тела, а не самой вещью."""
    from bot.game.equipment import ALL_SLOTS, Slot

    assert [slot.section for slot in ALL_SLOTS] == [
        "голова",
        "оружие",
        "тело",
        "пояс",
        "перчатки",
        "щиты",
        "ноги",
        "обувь",
    ]
    # в предложении слот по-прежнему называется вещью: «сюда надевается щит»
    assert Slot.SHIELD.title == "щит"
    assert Slot.PANTS.title == "штаны"
    assert build_shop(make_player())["sections"][0]["title"] == "Голова"


def test_every_look_points_at_its_own_picture():
    """Картинка образа лежит под его кодом — переименовали, и всё поехало."""
    from bot.game.art import AVATARS
    from bot.game.looks import LOOKS

    pictures = [look.picture for look in LOOKS]
    assert len(set(pictures)) == len(LOOKS)
    for look in LOOKS:
        assert look.picture == f"{AVATARS}/{look.code}.jpeg"


def test_a_look_can_override_its_picture():
    """Поле image оставлено на случай файла с другим именем."""
    from bot.game.looks import Look

    custom = Look("x", "Тест", "🥊", "male", image="https://example.com/one.png")
    assert custom.picture == "https://example.com/one.png"


def test_empty_slots_carry_their_own_placeholder():
    """У пустого слота своя подложка — по коду слота, как у аватаров."""
    from bot.game.art import SLOTS
    from bot.game.equipment import ALL_SLOTS

    card = build_card(make_player(), TOKEN, viewer_id=1)
    rows = card["slots"]["left"] + card["slots"]["right"]

    assert len(rows) == len(ALL_SLOTS)
    for row in rows:
        assert row["placeholder_image"] == f"{SLOTS}/{row['slot']}.jpeg"
        assert row["placeholder"], "значок остаётся запасным вариантом"


def test_the_two_pairs_of_canvas_trousers_do_not_share_a_picture():
    """Брезентовые и накладные разъезжались: у каждой своя картинка."""
    plain = CATALOGUE["canvas_pants"]
    padded = CATALOGUE["padded_pants"]

    assert plain.image != padded.image
    assert plain.image.endswith("Folded_work_trousers_game_icon_202608281512.jpeg")
    assert padded.image.endswith("Canvas_trousers_game_inventory_icon_202608281512.jpeg")


def test_the_whole_catalogue_lives_in_one_bucket():
    """Первого бакета больше нет: всё лежит в общем, включая кеды."""
    from bot.game.equipment import ART

    for item in CATALOGUE.values():
        assert item.image.startswith(ART), f"{item.code}: не из общего бакета"
