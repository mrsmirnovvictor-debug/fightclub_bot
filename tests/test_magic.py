"""Лавка мага: товар за звёзды, выдача в рюкзак, возврат и тестовая выдача."""

import pytest
from aiogram.types import SuccessfulPayment
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game import art
from bot.game.classes import Stats
from bot.game.combat import Fighter
from bot.game.equipment import (
    MAGIC_ITEMS,
    SHOWCASE,
    CATALOGUE,
    Equipment,
    OwnedItem,
    Slot,
)
from bot.game.store import parse_payload, relic_payload
from bot.inventory_service import equip
from bot.models import Player
from bot.seed import GIFT_ID, TEST_FIGHTER, TEST_RELIC, grant_test_relic
from bot.store_service import StoreError, StoreService
from bot.webapp.card import build_card, build_magic, build_shop
from bot.webapp.server import create_app
from tests.test_store import FakeBot, TOKEN
from tests.test_webapp import FakeBot as AvatarBot, make_init_data

SABER = CATALOGUE["lightsaber"]


def make_player(user_id: int = 42, level: int = 5, **kwargs) -> Player:
    stats = Stats(strength=12, agility=8, intuition=8, endurance=12)
    return Player(
        user_id=user_id,
        nickname=kwargs.pop("nickname", "Тайлер"),
        class_code="warrior",
        level=level,
        **stats.as_dict(),
        **kwargs,
    )


def paid(code: str = "lightsaber", charge_id: str = "ch-magic") -> SuccessfulPayment:
    item = CATALOGUE[code]
    return SuccessfulPayment(
        currency="XTR",
        total_amount=item.stars,
        invoice_payload=relic_payload(code, 42),
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="",
    )


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
async def store(bot, db):
    return StoreService(bot=bot, db=db, config=Config(bot_token="test"))


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    app = create_app(AvatarBot(), db, config)
    async with TestClient(TestServer(app)) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


# ---------- сам меч ----------


def test_the_lightsaber_is_exactly_what_was_ordered():
    assert SABER.stars == 250 and SABER.price == 0
    assert SABER.level_required == 2
    assert SABER.requires.strength == 10
    assert (SABER.damage_min, SABER.damage_max) == (7, 15)
    assert SABER.intuition == 5
    assert SABER.dodge == 0.35
    assert SABER.counter == 0.25
    assert SABER.is_weapon and SABER.slot is Slot.WEAPON
    assert SABER.describe_bonus() == "👊7–15 🔮+5 🌀+35% 🔄+25%"


def test_the_saber_is_drawn_and_lives_in_the_mage_folder():
    """Расширение — часть адреса: у мага картинка в png, а не в jpeg."""
    assert SABER.image == f"{art.MAGIC}/lightsaber.png"
    assert SABER.image.startswith("https://")


def test_the_saber_is_sold_by_the_mage_and_nowhere_else():
    assert SABER in MAGIC_ITEMS
    assert SABER not in SHOWCASE
    shop = build_shop(make_player())
    on_the_counter = [
        row["code"] for section in shop["sections"] for row in section["items"]
    ]
    assert "lightsaber" not in on_the_counter


def test_a_worn_saber_lifts_dodge_and_counter():
    """Проценты вещи складываются со своими, а не подменяют их."""
    player = make_player()
    bare = Fighter.from_player(player, armed=True)
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]
    armed = Fighter.from_player(player, armed=True)

    assert armed.dodge == pytest.approx(bare.dodge + 0.35)
    assert armed.counter == pytest.approx(bare.counter + 0.25)
    assert Equipment.from_owned(player.gear).counter == 0.25


def test_the_counter_bonus_still_respects_its_ceiling():
    """Даже с мечом контрудар не уходит выше общего потолка."""
    from bot.game.stats import MAX_COUNTER_CHANCE

    player = make_player()
    player.agility = 60
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]

    assert Fighter.from_player(player, armed=True).counter <= MAX_COUNTER_CHANCE


# ---------- покупка за звёзды ----------


def test_payload_tells_a_relic_from_a_pack():
    assert parse_payload(relic_payload("lightsaber", 7)) == ("relic", "lightsaber")
    assert parse_payload("pack:case:7") == ("pack", "case")
    assert parse_payload("мусор") == ("", "")


def test_the_till_knows_the_saber(store):
    assert store.check(relic_payload("lightsaber", 42)) is SABER
    with pytest.raises(StoreError, match="у мага больше нет"):
        store.check("relic:knuckles:42")  # вещь за кредиты магом не торгуется
    with pytest.raises(StoreError):
        store.check("relic:нетакого:42")


async def test_an_invoice_for_the_saber_is_priced_in_stars(store, bot):
    link = await store.invoice_link(SABER, 42)

    assert link.endswith("relic:lightsaber:42")
    invoice = bot.invoices[-1]
    assert invoice["currency"] == "XTR"
    assert invoice["prices"][0].amount == 250
    assert "Световой меч" in invoice["description"]


async def test_paid_stars_put_the_saber_in_the_backpack(store, db):
    await db.save_player(make_player())

    grant = await store.grant(42, paid())

    assert grant.is_relic and grant.credits == 0
    assert "Световой меч" in grant.label
    gear = await db.list_gear(42)
    assert [owned.code for owned in gear] == ["lightsaber"]
    assert gear[0].slot is None  # лежит в рюкзаке, надевать — руками


async def test_the_same_payment_does_not_hand_out_two_sabers(store, db):
    await db.save_player(make_player())

    await store.grant(42, paid())
    again = await store.grant(42, paid())

    assert again.already
    assert len(await db.list_gear(42)) == 1


async def test_buying_the_saber_leaves_the_purse_alone(store, db):
    await db.save_player(make_player(credits=300))

    await store.grant(42, paid())

    assert (await db.get_player(42)).credits == 300


# ---------- возврат ----------


async def test_a_refund_takes_the_saber_back(store, db, bot):
    await db.save_player(make_player())
    await store.grant(42, paid())

    row = await store.refund(42)

    assert bot.refunds == [(42, "ch-magic")]
    assert row["stars"] == 250
    assert await db.list_gear(42) == []


async def test_a_saber_that_is_gone_cannot_be_refunded(store, db):
    """Износил меч в труху — звёзды остались у клуба."""
    await db.save_player(make_player())
    await store.grant(42, paid())
    gear = await db.list_gear(42)
    await db.delete_gear(gear[0].id)

    with pytest.raises(StoreError, match="больше нет"):
        await store.refund(42)


async def test_a_worn_saber_still_goes_back(store, db):
    """Надетый меч тоже возвращается — он цел, значит его есть чем вернуть."""
    await db.save_player(make_player())
    await store.grant(42, paid())
    player = await db.get_player(42)
    await equip(db, player, player.gear[0].id)

    await store.refund(42)

    assert await db.list_gear(42) == []


# ---------- мини-апп ----------


def test_the_mage_counter_prices_in_stars_not_credits():
    magic = build_magic(make_player(credits=0))
    row = magic["items"][0]

    assert row["code"] == "lightsaber"
    assert row["stars"] == 250 and row["price"] == 0
    assert row["magic"] and row["affordable"]  # платит Telegram, а не кошелёк
    gains = {gain["title"]: gain.get("text") for gain in row["bonuses"]}
    assert gains["Урон"] == "7–15"
    assert gains["Уворот"] == "35%"
    assert gains["Контрудар"] == "25%"


async def test_the_mini_app_serves_the_mage_counter(client, db):
    await db.save_player(make_player())

    response = await client.get("/api/magic", headers=headers())
    body = await response.json()

    assert response.status == 200
    assert [row["code"] for row in body["items"]] == ["lightsaber"]


async def test_the_mini_app_asks_for_an_invoice_by_kind(client, db, bot):
    from bot.webapp.server import STORE_KEY

    await db.save_player(make_player())
    client.app[STORE_KEY] = StoreService(
        bot=bot, db=db, config=Config(bot_token=TOKEN)
    )

    response = await client.post(
        "/api/invoice", json={"code": "lightsaber", "kind": "relic"}, headers=headers()
    )
    body = await response.json()

    assert response.status == 200
    assert body["stars"] == 250
    assert bot.invoices[-1]["payload"] == "relic:lightsaber:42"


async def test_the_mage_will_not_sell_a_club_item_for_stars(client, db, bot):
    from bot.webapp.server import STORE_KEY

    await db.save_player(make_player())
    client.app[STORE_KEY] = StoreService(
        bot=bot, db=db, config=Config(bot_token=TOKEN)
    )

    response = await client.post(
        "/api/invoice", json={"code": "knuckles", "kind": "relic"}, headers=headers()
    )

    assert response.status == 409
    assert not bot.invoices


def test_the_saber_shows_its_counter_bonus_on_the_card():
    player = make_player()
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]

    card = build_card(player, TOKEN, viewer_id=player.user_id)
    bare = build_card(make_player(), TOKEN, viewer_id=42)

    assert card["combat"]["counter_chance"] == bare["combat"]["counter_chance"] + 25


# ---------- тестовая выдача ----------


async def test_victor_gets_a_saber_once_and_only_once(db):
    await db.save_player(make_player(user_id=7, nickname=TEST_FIGHTER))

    assert await grant_test_relic(db) is True
    assert await grant_test_relic(db) is False

    gear = await db.list_gear(7)
    assert [owned.code for owned in gear] == [TEST_RELIC]


async def test_the_test_gift_waits_until_the_fighter_exists(db):
    assert await grant_test_relic(db) is False
    await db.save_player(make_player(user_id=7, nickname=TEST_FIGHTER.lower()))
    assert await grant_test_relic(db) is True  # прозвище ищем без учёта регистра


async def test_a_gift_is_neither_a_purchase_nor_a_refund(db, store):
    await db.save_player(make_player(user_id=7, nickname=TEST_FIGHTER))
    await grant_test_relic(db)

    assert await db.purchases_of(7) == []
    with pytest.raises(StoreError, match="подарок"):
        await store.refund(7, GIFT_ID)


# ---------- карточка не должна расходиться с рингом ----------


def test_the_card_shows_every_percent_the_ring_will_use():
    """Проценты с вещей видны все до одной.

    Уворот и крит на карточке считались без экипировки, пока ни одна вещь их
    не давала: меч дал +35% уворота, и карточка молча показывала голое
    значение — контрудар прибавился, а уворот нет.
    """
    player = make_player()
    bare = build_card(player, TOKEN, viewer_id=player.user_id)["combat"]
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]
    armed = build_card(player, TOKEN, viewer_id=player.user_id)["combat"]

    assert armed["dodge_chance"] == bare["dodge_chance"] + 35
    assert armed["counter_chance"] == bare["counter_chance"] + 25

    # и ровно то же число, с которым боец выйдет на ринг
    fighter = Fighter.from_player(player, armed=True)
    assert armed["dodge_chance"] == round(fighter.dodge * 100)
    assert armed["counter_chance"] == round(fighter.counter * 100)
    assert armed["crit_chance"] == round(fighter.crit * 100)
    assert armed["accuracy"] == round(fighter.accuracy * 100)
    assert armed["anticrit"] == round(fighter.anticrit * 100)


def test_the_profile_text_counts_gear_the_same_way():
    from bot.handlers.common import combat_block

    player = make_player()
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]
    fighter = Fighter.from_player(player, armed=True)

    text = combat_block(
        player.fclass, player.stats, player.level, player.equipment
    )

    assert f"🌀 Уворот: <b>{fighter.dodge:.0%}</b>" in text
    assert f"🔄 Контрудар: <b>{fighter.counter:.0%}</b>" in text


def test_the_card_never_promises_more_than_the_ring_allows():
    """Потолок боя виден и на карточке: 60% уворота — предел."""
    player = make_player()
    player.agility = 80
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]

    card = build_card(player, TOKEN, viewer_id=player.user_id)["combat"]
    fighter = Fighter.from_player(player, armed=True)

    assert card["dodge_chance"] == round(fighter.dodge * 100) == 60


# ---------- оружие в руках класса ----------


def test_the_item_card_says_what_the_weapon_becomes_in_these_hands():
    """У меча 7–15, у воина в руках 6–14 — и то и другое видно рядом.

    Числа оба верные: боевой движок правда прогоняет урон оружия через
    множитель класса. Но раньше «7–15» на вещи и «6–14» в ударе стояли на
    одной карточке без всякой связи, и это читалось как ошибка.
    """
    player = make_player()  # воин, множитель 0.9
    player.gear = [OwnedItem(item=SABER, id=1)]

    row = next(
        gain
        for gain in build_card(player, TOKEN, viewer_id=42)["inventory"][0]["bonuses"]
        if gain["title"] == "Урон"
    )

    assert row["text"] == "7–15"
    assert row["hint"] == "у воина 6–14"


def test_the_hint_is_silent_when_the_class_changes_nothing():
    """У танка множитель почти единица: подсказка была бы шумом."""
    player = make_player()
    player.class_code = "tank"
    player.gear = [OwnedItem(item=SABER, id=1)]

    row = next(
        gain
        for gain in build_card(player, TOKEN, viewer_id=42)["inventory"][0]["bonuses"]
        if gain["title"] == "Урон"
    )

    assert row["text"] == "7–15" and "hint" not in row


def test_the_assassin_gets_more_out_of_the_same_blade():
    from bot.webapp.card import weapon_in_hands
    from bot.game.classes import get_class

    assert weapon_in_hands(SABER, get_class("assassin")) == "8–17"
    assert weapon_in_hands(SABER, get_class("warrior")) == "6–14"
    assert weapon_in_hands(SABER, None) == ""
    assert weapon_in_hands(CATALOGUE["wraps"], get_class("warrior")) == ""


def test_the_damage_row_marks_which_part_came_from_the_weapon():
    player = make_player()
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]

    weapon = build_card(player, TOKEN, viewer_id=42)["combat"]["weapon_damage"]

    assert weapon == [
        {
            "min": 6,
            "max": 14,
            "icon": SABER.emoji,
            "title": SABER.title,
            "base": "7–15",
        }
    ]


def test_the_hero_screen_explains_the_weapon_line_where_it_stands():
    """Подсказка нужна там, где стоит число: у надетого меча рюкзака нет.

    Пояснение «(у воина 6–14)» живёт на карточке вещи в рюкзаке, а надетый
    меч в рюкзаке не лежит — на «Персонаже» его не видно вовсе. Поэтому
    строка урона несёт и собственное число оружия, и название.
    """
    player = make_player()
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]

    card = build_card(player, TOKEN, viewer_id=42)
    weapon = card["combat"]["weapon_damage"][0]
    slot = next(s for s in card["slots"]["left"] if s["slot"] == "weapon")

    assert weapon["base"] == "7–15" and (weapon["min"], weapon["max"]) == (6, 14)
    assert weapon["title"] == "Световой меч"
    # и по нажатию на слот куклы говорится то же самое
    assert slot["item"]["in_hands"] == "У воина в руках: 6–14"


def test_the_slot_says_nothing_extra_when_the_class_changes_nothing():
    player = make_player()
    player.class_code = "tank"
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]

    card = build_card(player, TOKEN, viewer_id=42)
    slot = next(s for s in card["slots"]["left"] if s["slot"] == "weapon")

    assert slot["item"]["in_hands"] == ""
    assert card["combat"]["weapon_damage"][0]["base"] == "7–15"


def test_bare_hands_never_add_a_weapon_line():
    card = build_card(make_player(), TOKEN, viewer_id=42)
    assert card["combat"]["weapon_damage"] == []



def test_the_saber_lifts_intuition_and_everything_that_grows_from_it():
    """Интуиция — не украшение: с ней растут крит, его сила и точность."""
    player = make_player()
    bare = Fighter.from_player(player, armed=True)
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]
    armed = Fighter.from_player(player, armed=True)

    assert armed.stats.intuition == bare.stats.intuition + 5
    assert armed.crit > bare.crit
    assert armed.derived.crit_power > bare.derived.crit_power
    assert armed.accuracy > bare.accuracy


def test_the_card_shows_the_intuition_the_saber_adds():
    player = make_player()
    player.gear = [OwnedItem(item=SABER, id=1, slot=Slot.WEAPON)]

    card = build_card(player, TOKEN, viewer_id=42)
    intuition = next(row for row in card["stats"] if row["code"] == "intuition")

    assert (intuition["base"], intuition["bonus"]) == (8, 5)
    assert intuition["total"] == 13
