"""Живая проверка витрины в браузере: вкладки и фильтры магазина.

Тест поднимает настоящий мини-апп и открывает его Chromium'ом, подменяя
только ответы API. Если браузера в системе нет — тест пропускается: остальной
прогон от этого не зависит.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from bot.game.classes import Stats
from bot.game.equipment import CATALOGUE, OwnedItem, Slot
from bot.game.health import now_ts
from bot.game.potions import ActiveEffect
from bot.game.store import PACKS
from bot.models import Player
from bot.game.locations import Service
from bot.webapp.card import build_card, build_magic, build_shop, build_topup
from bot.webapp.server import create_app
from tests.test_webapp import TOKEN

async_playwright = pytest.importorskip(
    "playwright.async_api", reason="playwright не установлен"
).async_playwright


def find_chromium() -> str | None:
    """Плейрайт в этом окружении держит браузеры отдельно от пакета."""
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), "/opt/pw-browsers"]
    for root in filter(None, roots):
        for chrome in sorted(Path(root).glob("chromium-*/chrome-linux/chrome")):
            return str(chrome)
    return None


CHROMIUM = find_chromium()
# Всё, что рисуется картинкой: у лавки клуба jpeg, у мага png
IMAGES = "**/*.{jpeg,jpg,png}"
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="Chromium не найден")


def make_player() -> Player:
    stats = Stats(strength=14, agility=8, intuition=8, endurance=13)
    player = Player(
        user_id=42,
        nickname="Растафарайчик",
        class_code="warrior",
        level=5,
        credits=214,
        **stats.as_dict(),
    )
    player.gear = [OwnedItem(item=CATALOGUE["pipe"], id=1, wear=3, slot=Slot.WEAPON)]
    return player


# Пустой ринг: никто никого не вызвал, драться не с кем
EMPTY_RING = {
    "attacks": [{"zone": "head", "title": "Голова"}],
    "blocks": [{"zone": "head", "title": "Голова + Корпус"}],
    "modes": [{"code": "fist", "title": "кулачный бой", "emoji": "🥊"}],
    "duel": None,
    "challenge": None,
    "challenges": [],
    "can_fight": True,
}


# Никто ещё не дрался
EMPTY_HISTORY = {
    "user_id": 42, "name": "Растафарайчик", "days": [], "total": 0,
    "counts": {"win": 0, "loss": 0, "draw": 0}, "before": None,
}


EMPTY_MARKET = {
    "credits": 0, "fee": 5, "sections": [], "mine": [], "sellable": [],
}

BOSS_CARD = {
    "code": "cellar_boss", "title": "Босс подпольного казино", "emoji": "🩸",
    "image": "", "raid_name": "Ограбление Босса подпольного казино",
    "tagline": "Он тут всё построил и всех похоронил.", "live": False,
    "levels_above": 4, "level": 9, "fclass": "Танк", "fclass_emoji": "🛡️",
    "max_hp": 304, "weapon": "Кувалда", "weapon_icon": "🔨", "damage": [15, 25],
    "stats": {"strength": 16, "agility": 2, "intuition": 11, "endurance": 33},
    "combat": {
        "crit_chance": 15, "crit_power": 1.94, "anticrit": 50,
        "dodge_chance": 4, "accuracy": 16, "counter_chance": 3,
        "resist": 31, "penetration": 2, "block_hold": 35,
    },
    "armor": [
        {"zone": "head", "title": "Голова", "emoji": "🤕", "min": 3, "max": 5},
        {"zone": "legs", "title": "Ноги", "emoji": "🦵", "min": 0, "max": 0},
    ],
    "kit": [
        {"slot": "weapon", "title": "Кувалда", "emoji": "🔨"},
        {"slot": "head", "title": "Мотошлем", "emoji": "🪖"},
    ],
    "name": "Босс Подвала",
    "avatar": {"url": "", "emoji": "🩸"},
    "slots": {
        "left": [
            {
                "slot": "head", "title": "головной убор", "placeholder": "🎩",
                "placeholder_image": "",
                "item": {
                    "id": 0, "code": "moto_helmet", "title": "Мотошлем",
                    "icon": "🪖", "image": "", "bonus": "🛡3–5", "in_hands": "",
                    "wear": 0, "max_wear": 20,
                },
            },
            {
                "slot": "weapon", "title": "оружие", "placeholder": "🔪",
                "placeholder_image": "",
                "item": {
                    "id": 0, "code": "sledge", "title": "Кувалда", "icon": "🔨",
                    "image": "", "bonus": "👊8–10", "in_hands": "",
                    "wear": 0, "max_wear": 20,
                },
            },
        ],
        "right": [
            {
                "slot": "gloves", "title": "перчатки", "placeholder": "🥊",
                "placeholder_image": "", "item": None,
            },
        ],
    },
}

EMPTY_RAID = {
    "attacks": [
        {"zone": "head", "title": "Голова"},
        {"zone": "chest", "title": "Корпус"},
    ],
    "blocks": [
        {"zone": "head", "title": "Голова + Корпус"},
        {"zone": "chest", "title": "Корпус + Живот"},
    ],
    "min_party": 1, "max_party": 10, "can_fight": True,
    "raid": None, "lobby": None, "lobbies": [], "boss": BOSS_CARD,
    "gate": {
        "pass_code": "raid_pass", "pass_title": "Рейд-пасс", "pass_price": 10,
        "pass_emoji": "🎟", "passes": 2, "schedule": "0–2, 8–10, 12–14, 16–18, 20–22 мск",
        "open": True, "window": "с 20:00 до 22:00 мск",
        "next_window": "с 00:00 до 02:00 мск", "won": False, "spent": False,
        "can_afford": True,
    },
}


EMPTY_BATTLE = {
    "attacks": [
        {"zone": "head", "title": "Голова"},
        {"zone": "chest", "title": "Корпус"},
    ],
    "blocks": [
        {"zone": "head", "title": "Голова + Корпус"},
        {"zone": "chest", "title": "Корпус + Живот"},
    ],
    "kinds": [
        {"code": "team", "title": "Командный бой", "emoji": "🤝", "min": 2, "max": 5},
        {"code": "royale", "title": "Королевская битва", "emoji": "🌪",
         "min": 3, "max": 8},
    ],
    "can_fight": True,
    "battle": None, "lobby": None, "lobbies": [],
}


class FakeBot:  # pragma: no cover - аватар в этом тесте не трогаем
    async def get_file(self, file_id):
        raise AssertionError

    async def download_file(self, path):
        raise AssertionError


def city_map(here: str = "fight_club", road: dict | None = None) -> dict:
    """Карта города так, как её отдаёт сервер."""
    from bot.game.classes import get_class
    from bot.models import Player
    from bot.webapp.citymap import build_map

    fclass = get_class("warrior")
    walker = Player(
        user_id=42, nickname="Тайлер", class_code="warrior", location=here,
        **fclass.base_stats.as_dict(),
    )
    body = build_map(walker)
    if road:
        body["road"] = {**body["road"], **road}
    return body


async def open_page(
    pw, server, card, shop=None, query="", topup=None, looks=None, club=None,
    magic=None, fights=None, history=None, fight_log=None, raid=None, market=None,
    battle=None, city=None,
):
    """Открыть мини-апп с подменёнными ответами API."""
    def canned(payload):
        return lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)
        )

    browser = await pw.chromium.launch(
        executable_path=CHROMIUM, args=["--no-proxy-server"]
    )
    page = await browser.new_page(viewport={"width": 420, "height": 900})
    await page.route("**/api/card*", canned(card))
    await page.route("**/api/shop*", canned(shop or {}))
    await page.route("**/api/topup*", canned(topup or {"credits": 0, "packs": []}))
    await page.route("**/api/looks*", canned(looks or {"looks": [], "credits": 0}))
    await page.route("**/api/club*", canned(club or {"fighters": [], "total": 0}))
    await page.route("**/api/magic*", canned(magic or {"items": [], "credits": 0}))
    await page.route("**/api/fights*", canned(fights or EMPTY_RING))
    await page.route("**/api/history*", canned(history or EMPTY_HISTORY))
    await page.route("**/api/raid*", canned(raid or EMPTY_RAID))
    await page.route("**/api/market*", canned(market or EMPTY_MARKET))
    await page.route("**/api/battle*", canned(battle or EMPTY_BATTLE))
    await page.route("**/api/map*", canned(city or city_map()))
    if fight_log is not None:
        await page.route("**/api/fight/*", canned(fight_log))
    await page.route("https://telegram.org/**", lambda route: route.fulfill(
        status=200, content_type="application/javascript", body=""
    ))
    await page.route(IMAGES, lambda route: route.abort())
    await page.goto(f"{server.make_url('/')}{query}")
    return browser, page


@pytest.fixture
async def shop_page(db):
    """Прилавок магазина одежды: на нём всё носимое, кроме оружия."""
    async for page in shop_screen(db, Service.CLOTHES):
        yield page


@pytest.fixture
async def weapon_page(db):
    """Прилавок оружейника: только то, что берут в руки."""
    async for page in shop_screen(db, Service.WEAPONS):
        yield page


@pytest.fixture
async def pharmacy_page(db):
    """Прилавок аптеки: склянки."""
    async for page in shop_screen(db, Service.POTIONS):
        yield page


async def shop_screen(db, service: Service):
    """Страница мини-аппа с подменёнными ответами API.

    Магазин теперь не один: у оружейника, одёжника и аптеки свои
    прилавки, и открывается тот, в чьей локации боец стоит.
    """
    from bot.config import Config

    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    shop = build_shop(player, service)

    server = TestServer(create_app(FakeBot(), db, Config(bot_token=TOKEN)))
    await server.start_server()

    def canned(payload):
        return lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROMIUM, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 420, "height": 900})
        await page.route("**/api/card*", canned(card))
        await page.route("**/api/shop*", canned(shop))
        # телеграмовский скрипт из сети не тянем, картинки предметов тоже
        await page.route("https://telegram.org/**", lambda route: route.fulfill(
            status=200, content_type="application/javascript", body=""
        ))
        await page.route(IMAGES, lambda route: route.abort())

        await page.route("**/api/club*", canned({"fighters": [], "total": 0}))
        await page.route("**/api/magic*", canned({"items": [], "credits": 0}))
        await page.route("**/api/fights*", canned(EMPTY_RING))
        await page.goto(f"{server.make_url('/')}")
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "shop")
        await page.wait_for_selector(".shelf")
        yield page
        await browser.close()
    await server.close()


async def open_screen(page, name: str) -> None:
    """Открыть экран, минуя карту.

    Лавки теперь дома на карте, и человек приходит в них ногами. Тесты
    самой дороги ходят этим путём целиком; остальным она не предмет, и
    им короче открыть экран напрямую.
    """
    await page.evaluate(f"showTab('{name}')")


async def shelves(page) -> list[str]:
    return await page.locator(".shelf-head").all_inner_texts()


async def visible_titles(page) -> list[str]:
    return await page.locator(".shelf-list:not(.hidden) .thing-title").all_inner_texts()


async def test_the_clothes_shop_holds_everything_but_weapons(shop_page):
    """У одёжника семь полок: всё носимое, кроме того, что берут в руки."""
    heads = await shelves(shop_page)
    assert len(heads) == 7
    assert not [head for head in heads if "Оружие" in head or "Щиты" in head]

    titles = await visible_titles(shop_page)
    assert "Кастет" not in titles, "оружие торгуют у оружейника"

    # футболки на прилавке: пять штук, часть открыта по уровню
    shirts = next(head for head in heads if "Футболки" in head)
    assert "из 5" in shirts
    assert "Майка-алкоголичка" in titles


async def test_the_weapon_shop_holds_only_what_you_hold(weapon_page):
    """У оружейника две полки: оружие и щиты."""
    heads = await shelves(weapon_page)
    assert [head.split("\n")[0] for head in heads] == ["Оружие", "Щиты"]

    titles = await visible_titles(weapon_page)
    assert "Кастет" in titles  # открыто по уровню
    assert "Бита" not in titles  # закрыто, лежит под кнопкой


async def test_an_empty_shelf_says_the_goods_are_coming(server):
    """Пустой раздел с прилавка не пропадает: видно, что его готовят."""
    empty = {
        "credits": 100,
        "level": 3,
        "fclass": {"code": "warrior", "title": "Воин"},
        "sections": [
            {"slot": "shirt", "title": "Футболки", "emoji": "👕", "open": 0,
             "items": []}
        ],
    }
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(make_player(), TOKEN, viewer_id=42), shop=empty
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "shop")
        await page.wait_for_selector(".shelf")

        assert "открыто 0 из 0" in (await shelves(page))[0]
        assert await page.get_by_text("Скоро завезут.").is_visible()
        await browser.close()


async def test_type_filter_leaves_one_shelf(weapon_page):
    await weapon_page.get_by_role("button", name="Оружие", exact=True).click()

    # значков в заголовках полок нет: тип и так назван словом
    assert [head.split("\n")[0] for head in await shelves(weapon_page)] == ["Оружие"]
    assert all(
        title
        in ("Кастет", "Деревянная бита", "Выкидуха", "Строительный нож",
            "Монтировка", "Нож")
        for title in await visible_titles(weapon_page)
    )


async def test_buying_asks_before_it_spends(shop_page):
    """«Купить» показывает окно с названием и ценой — и слушается отказа."""
    asked, sent = [], []

    def on_dialog(dialog):
        asked.append(dialog.message)
        asyncio.ensure_future(dialog.dismiss())

    async def catch(route):
        sent.append(route.request.post_data_json)
        await route.fulfill(
            status=200, content_type="application/json", body=json.dumps({})
        )

    shop_page.on("dialog", on_dialog)
    await shop_page.route("**/api/buy", catch)
    buy = shop_page.get_by_role("button", name="Купить").first
    label = await buy.inner_text()
    price = label.split("·")[1].split()[0]

    await buy.click()
    await shop_page.wait_for_timeout(200)

    assert sent == []  # отказались — кредиты на месте
    assert len(asked) == 1
    assert "Вы приобретаете предмет" in asked[0]
    assert f"за {price} кредитов" in asked[0]


async def test_a_thing_can_be_handed_back_to_the_shop(server):
    """Сдача в лавку: своё окно, свой текст, свой адрес."""
    from bot.game.market import buyback

    player = make_player()
    # вещь в рюкзаке, разбитая почти в труху: цена сдачи от этого не зависит
    player.gear = [OwnedItem(item=CATALOGUE["knuckles"], id=7, wear=19)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    paid = buyback(CATALOGUE["knuckles"])
    asked, sent = [], []

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        def on_dialog(dialog):
            asked.append(dialog.message)
            asyncio.ensure_future(dialog.accept())

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "card": card,
                        "shop": build_shop(player),
                        "handin": {
                            "title": "Кастет", "paid": paid, "credits": 223,
                        },
                    }
                ),
            )

        page.on("dialog", on_dialog)
        await page.route("**/api/handin", catch)
        await page.locator("#tab-bag").click()
        await page.wait_for_selector("#bag:not(.hidden)")

        hand = page.get_by_role("button", name="Сдать").first
        assert await hand.inner_text() == f"Сдать · {paid} 💰"

        await hand.click()
        await page.wait_for_timeout(300)

        assert "Вы сдаете Кастет в Лавку клуба" in asked[0]
        assert f"получите за это {paid} кредитов" in asked[0]
        assert sent == [{"item_id": 7}]
        await browser.close()


MARKET = {
    "credits": 300,
    "fee": 5,
    "sections": [
        {
            "slot": "weapon", "title": "Оружие", "emoji": "🔪", "open": 2,
            "items": [
                {
                    "id": 11, "code": "knife", "title": "Нож", "icon": "🔪",
                    "image": "", "slot": "weapon", "slot_title": "Оружие",
                    "price": 200, "payout": 190, "fee": 10, "seller_id": 43,
                    "seller": "Марла", "mine": False, "wear": 6, "max_wear": 20,
                    "wear_text": "6 из 20", "affordable": True, "can_equip": True,
                    "requirements": [], "bonuses": [], "shop_price": 110,
                },
                {
                    "id": 12, "code": "pipe", "title": "Деревянная бита",
                    "icon": "🏏", "image": "", "slot": "weapon",
                    "slot_title": "Оружие", "price": 400, "payout": 380,
                    "fee": 20, "seller_id": 42, "seller": "Растафарайчик",
                    "mine": True, "wear": 0, "max_wear": 20, "wear_text": "новая",
                    "affordable": False, "can_equip": True, "requirements": [],
                    "bonuses": [], "shop_price": 150,
                },
            ],
        }
    ],
    "mine": [],
    "sellable": [
        {
            "id": 21, "code": "bandana", "title": "Бандана", "icon": "🧢",
            "image": "", "slot": "head", "slot_title": "Голова", "wear": 2,
            "max_wear": 20, "wear_text": "2 из 20", "min_price": 20,
            "max_price": 120, "hint": "От 20 до 120 💰", "shop_price": 40,
        }
    ],
}


async def open_market(pw, server, market=None):
    """Открыть комиссионку — она теперь свой дом на карте."""
    player = make_player()
    browser, page = await open_page(
        pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
        build_shop(player, Service.CLOTHES), market=market or MARKET,
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.evaluate("showTab('shop'); pickShopSection('market')")
    await page.wait_for_selector("#shop-market:not(.hidden)")
    return browser, page


async def test_a_shop_screen_says_whose_counter_it_is(server):
    """Лавка одна не бывает: у каждой своё имя, и оно в заголовке.

    Раньше на вкладке «Магазины» лежали лавка клуба и комиссионка, и их
    переключали пузырями. Теперь это разные дома города, и попасть в них
    можно только ногами — переключать нечего.
    """
    async with async_playwright() as pw:
        player = make_player()
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player, Service.WEAPONS),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "shop")
        await page.wait_for_selector(".shelf")

        assert "Оружейный магазин" in await page.locator("#shop-title").inner_text()
        assert await page.locator("#shop-sections").count() == 0
        assert await page.locator("#shop-club").is_visible()
        assert await page.locator("#shop-market").is_hidden()

        # комиссионка — другой дом, и открывается она из него
        await page.evaluate("pickShopSection('market'); showTab('shop')")
        await page.wait_for_selector("#shop-market:not(.hidden)")

        assert await page.locator("#shop-club").is_hidden()
        assert "Комиссионный магазин" in await page.locator("#shop-title").inner_text()
        await browser.close()


async def test_the_market_shows_lots_on_shelves_by_type(server):
    """Чужие вещи лежат по полкам, и видно, кто их выставил."""
    async with async_playwright() as pw:
        browser, page = await open_market(pw, server)

        assert "Клуб берёт 5%" in await page.locator("#market-note").inner_text()
        shelves = await page.locator("#market-body .shelf-head").all_inner_texts()
        assert "Выставить своё" in shelves[0]
        assert "Оружие" in shelves[1] and "лотов 2" in shelves[1]

        lots = await page.locator("#market-body .shelf").nth(1).locator(
            ".thing"
        ).all_inner_texts()
        assert "Продаёт: Марла" in lots[0]
        assert "🔧 Износ: 6 из 20" in lots[0]
        assert "200 💰 · в лавке 110 💰" in lots[0]
        # свой лот подписан по-своему и снимается, а не покупается
        assert "Твой лот" in lots[1]
        assert "придёт 380 💰" in lots[1]
        await browser.close()


async def test_a_lot_is_bought_by_its_number(server):
    """Покупка в комиссионке тоже спрашивает согласия — деньги-то те же."""
    sent, asked = [], []

    async with async_playwright() as pw:
        browser, page = await open_market(pw, server)

        async def catch(route):
            # GET и POST у комиссионки один адрес: считаем только действия
            if route.request.method == "POST":
                sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({**MARKET, "sections": []}),
            )

        def on_dialog(dialog):
            asked.append(dialog.message)
            asyncio.ensure_future(dialog.dismiss())

        await page.route("**/api/market", catch)
        page.on("dialog", on_dialog)

        # отказ — денег не тратим
        await page.get_by_role("button", name="Купить · 200 💰").click()
        await page.wait_for_timeout(200)
        assert sent == []
        assert "Вы приобретаете предмет Нож за 200 кредитов" in asked[0]

        page.remove_listener("dialog", on_dialog)
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        await page.get_by_role("button", name="Купить · 200 💰").click()
        await page.wait_for_timeout(200)

        assert sent == [{"action": "buy", "lot_id": 11}]
        await browser.close()


async def test_your_own_lot_is_taken_back_not_bought(server):
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_market(pw, server)

        async def catch(route):
            # GET и POST у комиссионки один адрес: считаем только действия
            if route.request.method == "POST":
                sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({**MARKET, "sections": []}),
            )

        await page.route("**/api/market", catch)
        await page.get_by_role("button", name="Снять с продажи").click()
        await page.wait_for_timeout(200)

        assert sent == [{"action": "withdraw", "lot_id": 12}]
        await browser.close()


async def test_your_gear_goes_on_sale_with_a_price(server):
    """Выставить можно то, что лежит в рюкзаке, и только в рамках цены."""
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_market(pw, server)

        card = page.locator("#market-body .shelf").first
        assert "От 20 до 120 💰" in await card.inner_text()
        price = card.locator(".sell-price")
        assert await price.get_attribute("min") == "20"
        assert await price.get_attribute("max") == "120"
        assert await price.input_value() == "20"

        async def catch(route):
            if route.request.method == "POST":
                sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({**MARKET, "sellable": []}),
            )

        await page.route("**/api/market", catch)
        await price.fill("90")
        await page.get_by_role("button", name="Выставить").click()
        await page.wait_for_timeout(200)

        assert sent == [{"action": "sell", "item_id": 21, "price": 90}]
        await browser.close()


async def test_the_market_rereads_the_backpack_when_you_come_back(server):
    """Комиссионка не показывает вещь, которой в рюкзаке уже нет.

    Экран грузился один раз за сеанс: наденешь вещь или продай её — а в
    списке «выставить своё» она висела как живая, и Victor видел два бинта
    при одном в рюкзаке.
    """
    listings = [
        {**MARKET},
        {**MARKET, "sellable": []},  # бинт надели, выставлять больше нечего
    ]

    async def market_route(route):
        body = listings.pop(0) if len(listings) > 1 else listings[0]
        await route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    async with async_playwright() as pw:
        player = make_player()
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player, Service.CLOTHES),
        )
        await page.route("**/api/market*", market_route)
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.evaluate("showTab('shop'); pickShopSection('market')")
        await page.wait_for_selector("#shop-market:not(.hidden)")

        assert "Бандана" in await page.locator("#market-body").inner_text()

        # ушли на другую вкладку и вернулись — список перечитан
        await page.locator("#tab-hero").click()
        await open_screen(page, "shop")  # комиссионка помнит, что открыта она
        await page.wait_for_selector("text=В рюкзаке пусто")

        body = await page.locator("#market-body").inner_text()
        assert "Бандана" not in body
        # и это по-прежнему комиссионка, а не лавка клуба
        assert await page.locator("#shop-market").is_visible()
        await browser.close()


async def test_an_empty_market_says_so(server):
    async with async_playwright() as pw:
        browser, page = await open_market(
            pw, server, {**EMPTY_MARKET, "credits": 100}
        )

        assert "На комиссии пусто" in await page.locator("#market-note").inner_text()
        assert "В рюкзаке пусто" in await page.locator("#market-body").inner_text()
        await browser.close()


async def test_the_counter_has_no_level_filter_any_more(shop_page):
    """Фильтр остался один — тип вещи, и он не лента, а пузыри в несколько строк."""
    # именно фильтры прилавка: пузыри с разделами клуба живут своей жизнью
    labels = await shop_page.locator("#filter-type .chip").all_inner_texts()

    assert labels[0] == "Все"
    assert "Футболки" in labels
    assert not [label for label in labels if any(ch > "\u2000" for ch in label)], (
        "в фильтрах остались значки"
    )
    assert not [label for label in labels if "ур." in label], "уровни всё ещё в фильтрах"
    assert await shop_page.locator(".filters").count() == 0


async def test_locked_goods_stay_folded_at_the_end_of_the_shelf(weapon_page):
    """Закрытое по уровню видно только под кнопкой — это не фильтр, а раскладка."""
    titles = await visible_titles(weapon_page)
    assert "Кастет" in titles  # открыто по уровню
    assert "Бита" not in titles  # закрыто

    text = await weapon_page.locator("#shop-list").inner_text()
    assert "Показать закрытые" in text


# ---------- чужая карточка из чата боя ----------


@pytest.fixture
async def server(db):
    from bot.config import Config

    server = TestServer(create_app(FakeBot(), db, Config(bot_token=TOKEN)))
    await server.start_server()
    yield server
    await server.close()


async def test_stranger_card_has_no_backpack_and_no_shop(server):
    """Имя бойца в чате открывает его карточку — без инвентаря и лавки."""
    stranger = make_player()
    stranger.credits = 777
    card = build_card(stranger, TOKEN, viewer_id=999)  # смотрит кто-то другой

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, query="?user_id=42")
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#bar").is_hidden(), "чужому видна панель вкладок"
        assert await page.locator("#bag").is_hidden(), "чужому виден инвентарь"
        assert await page.locator("#shop").is_hidden(), "чужому видна лавка"

        # зато боец и его характеристики на месте
        assert await page.locator("#hero-name").inner_text() == stranger.nickname
        rows = await page.locator("#stats li").all_inner_texts()
        assert any("Сила" in row for row in rows)
        combat = await page.locator("#combat").inner_text()
        for line in ("Урон", "Крит", "Уворот", "Сопротивление"):
            assert line in combat
        # кошелёк соседа не показываем
        assert "777" not in await page.locator("#record").inner_text()
        await browser.close()


async def test_the_bottom_bar_switches_four_screens(server):
    """Панель снизу: клуб, карта, инвентарь, персонаж.

    Магазинов на панели больше нет — они дома на карте, и лавка мага тоже
    (теперь это элитный магазин). Панель стала короче ровно настолько.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#bar").is_visible()
        assert await page.locator(".bar-tab").count() == 4
        # открывается карточка персонажа, её вкладка и подсвечена
        assert await page.locator("#tab-hero").get_attribute("class") == "bar-tab active"

        for tab in ("club", "map", "bag", "hero"):
            await page.locator("#tab-" + tab).click()
            await page.wait_for_selector("#" + tab + ":not(.hidden)")
            shown = [
                screen
                for screen in ("club", "map", "shop", "magic", "bag", "hero")
                if await page.locator("#" + screen).is_visible()
            ]
            assert shown == [tab], f"вместе с {tab} открыто {shown}"

        assert "Прилавок пуст" in await page.locator("#magic").inner_text()
        await browser.close()


# ---------- касса ----------


async def test_plus_next_to_the_credits_opens_the_cashdesk(server):
    """Кнопка «+» рядом с кредитами ведёт в кассу и возвращает обратно."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), topup=build_topup(player)
        )
        await page.wait_for_selector("#hero:not(.hidden)")

        assert "214" in await page.locator("#record").inner_text()
        await page.locator("#record .plus").click()

        await page.wait_for_selector("#topup:not(.hidden)")
        assert await page.locator("#card").is_hidden()
        titles = await page.locator(".pack-title").all_inner_texts()
        assert len(titles) == len(PACKS)
        assert PACKS[0].title in titles[0]
        # цена стоит на кнопке, выгода — рядом с названием
        assert f"{PACKS[-1].stars} ⭐" in await page.locator(".pack").last.inner_text()
        assert await page.locator(".pack-profit").count() == len(PACKS) - 1

        await page.get_by_role("button", name="← Назад").click()
        await page.wait_for_selector("#hero:not(.hidden)")
        await browser.close()


async def test_the_shop_purse_has_the_same_plus(server):
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), topup=build_topup(player)
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "shop")
        await page.wait_for_selector("#shop:not(.hidden)")

        await page.locator("#shop-purse .plus").click()
        await page.wait_for_selector("#topup:not(.hidden)")

        # «назад» из кассы возвращает в лавку, а не на карточку
        await page.get_by_role("button", name="← Назад").click()
        await page.wait_for_selector("#shop:not(.hidden)")
        await browser.close()


async def test_a_stranger_sees_no_plus(server):
    stranger = make_player()
    card = build_card(stranger, TOKEN, viewer_id=999)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, query="?user_id=42")
        await page.wait_for_selector("#hero:not(.hidden)")
        assert await page.locator(".plus").count() == 0
        await browser.close()


# ---------- образ и снятие вещей ----------


def wardrobe(current: str = "rookie") -> dict:
    from bot.game.looks import LOOKS

    return {
        "credits": 1200,
        "looks": [
            {
                "code": look.code,
                "title": look.title,
                "emoji": look.emoji,
                "image": "",
                "gender": look.gender,
                "price": look.price,
                "note": look.note,
                "owned": not look.paid,
                "current": look.code == current,
                "affordable": True,
            }
            for look in LOOKS
            if not look.pro  # образ подписки виден только своему хозяину
        ],
    }


async def test_tapping_the_avatar_opens_the_wardrobe(server):
    """По аватару открывается выбор образа: шесть своих и шесть за кредиты."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), looks=wardrobe()
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        assert await page.locator("#sheet").is_hidden()

        await page.locator("#hero-avatar").click()
        await page.wait_for_selector("#sheet:not(.hidden)")

        assert await page.locator(".look").count() == 12
        assert await page.locator(".look.current .look-title").inner_text() == "Новичок"
        # платные подписаны ценой, свои — словом
        tags = await page.locator(".look-tag").all_inner_texts()
        assert sum(1 for tag in tags if "💰" in tag) == 6
        assert await page.locator(".look-group").count() == 2

        await page.locator("#sheet-close").click()
        assert await page.locator("#sheet").is_hidden()
        await browser.close()


async def test_a_stranger_cannot_change_your_look(server):
    stranger = make_player()
    card = build_card(stranger, TOKEN, viewer_id=999)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, query="?user_id=42")
        await page.wait_for_selector("#hero:not(.hidden)")

        await page.locator("#hero-avatar").click()
        assert await page.locator("#sheet").is_hidden(), "чужой открыл гардероб"
        await browser.close()


async def test_taking_a_worn_item_off_asks_first(server):
    """Промахнуться по слоту легко, поэтому вещь снимается только с ответом «да»."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-bag").click()

        calls = []
        await page.route("**/api/unequip", lambda route: calls.append(route.request.url))

        asked = []

        def on_dialog(dialog):
            asked.append(dialog.message)
            asyncio.ensure_future(dialog.dismiss())

        page.on("dialog", on_dialog)
        await page.locator("#slots-left .slot:not(.empty)").first.click()
        await page.wait_for_timeout(200)

        assert asked and "снять предмет" in asked[0]
        assert "Обрезок трубы" in asked[0] or "бита" in asked[0].lower()
        assert not calls, "вещь сняли, хотя ответили «нет»"
        await browser.close()


async def test_the_body_cell_holds_the_jacket_and_the_shirt_under_it(server):
    """Одна клетка «тело»: картинкой верхняя одежда, в подсказке обе вещи."""
    player = make_player()
    player.gear = [
        OwnedItem(item=CATALOGUE["leather_jacket"], id=1, slot=Slot.JACKET),
        OwnedItem(item=CATALOGUE["club_tee"], id=2, slot=Slot.SHIRT),
    ]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        cells = page.locator("#hero-slots-left .slot")
        body = cells.nth(2)  # голова, оружие, тело, пояс
        hint = await body.get_attribute("title")

        assert "Косуха — верхняя одежда" in hint
        assert "Клубная футболка — футболка" in hint  # обе вещи, хоть видно одну
        assert "empty" not in (await body.get_attribute("class"))

        # клетка без вещей называет место, а не вещь
        empty = await page.locator("#hero-slots-right .slot").nth(1).get_attribute(
            "title"
        )
        assert empty == "Пусто: вторая рука"
        await browser.close()


async def test_only_the_shirt_still_fills_the_body_cell(server):
    """Футболка без куртки — клетка занята ею, и снимается тоже она."""
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["club_tee"], id=2, slot=Slot.SHIRT)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    asked = []

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        def on_dialog(dialog):
            asked.append(dialog.message)
            asyncio.ensure_future(dialog.dismiss())

        page.on("dialog", on_dialog)
        body = page.locator("#hero-slots-left .slot").nth(2)

        assert "empty" not in (await body.get_attribute("class"))
        assert "Клубная футболка — футболка" in await body.get_attribute("title")

        await body.click()
        await page.wait_for_timeout(200)

        assert asked and "Клубная футболка" in asked[0]
        await browser.close()


async def test_the_hero_screen_reads_without_extra_icons(server):
    """Характеристики без значков, броня по строке на зону, «До улучшения»."""
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["moto_helmet"], id=1, slot=Slot.HEAD)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        stats = await page.locator("#stats li .label").all_inner_texts()
        assert stats == ["Сила", "Ловкость", "Интуиция", "Выносливость"]

        progress = await page.locator("#progress").inner_text()
        assert "До улучшения" in progress and "До апа" not in progress

        combat = await page.locator("#combat li .label").all_inner_texts()
        assert "🩸 Крит" in combat  # крит помечен каплей крови
        assert "🛡💥 Пробивание" in combat
        # броня — по строке на зону, без значков и без общей строки «Броня»
        head = page.locator("#combat li").filter(has_text="Голова").first
        low, high = CATALOGUE["moto_helmet"].armor_min, CATALOGUE["moto_helmet"].armor_max
        assert await head.inner_text() == f"Голова\n{low}–{high}"
        assert "🛡 Броня" not in await page.locator("#combat").inner_text()
        await browser.close()


async def test_a_percentage_says_when_the_ceiling_cut_it(server):
    """Вещи дают уворота выше потолка, в строке потолок — карточка объясняет."""
    from bot.game.combat import MAX_DODGE_CHANCE
    from bot.game.stats import MAX_CRIT_CHANCE, NO_LIMITS

    if NO_LIMITS:
        pytest.skip("потолки сняты в bot/game/stats.py")
    ceiling = round(MAX_DODGE_CHANCE * 100)
    player = make_player()
    player.gear = [
        OwnedItem(item=CATALOGUE["lightsaber"], id=1, slot=Slot.WEAPON),
        OwnedItem(item=CATALOGUE["test_wraps"], id=2, slot=Slot.GLOVES),
        OwnedItem(item=CATALOGUE["test_sneakers"], id=3, slot=Slot.BOOTS),
        OwnedItem(item=CATALOGUE["shadow_coat"], id=4, slot=Slot.JACKET),
        OwnedItem(item=CATALOGUE["sheath_pants"], id=5, slot=Slot.PANTS),
    ]
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    gear = round(sum(owned.item.dodge for owned in player.gear) * 100)
    assert card["combat"]["caps"]["dodge_chance"]["gear"] == gear > ceiling

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-hero").click()

        dodge = page.locator("#combat li").filter(has_text="Уворот").first
        # срезанный процент подписью не помечают — его красят золотом
        assert await dodge.inner_text() == f"🌀 Уворот\n{ceiling}%"
        assert await dodge.locator(".value.capped").count() == 1
        assert f"вещи {gear}%" in await dodge.get_attribute("title")
        assert f"но выше {ceiling}% не растёт" in (
            await dodge.get_attribute("title")
        )

        # непотолочная строка объясняет то же самое, но золотом не горит
        crit = page.locator("#combat li").filter(has_text="Крит").first
        assert await crit.locator(".value.capped").count() == 0
        assert f"потолок {round(MAX_CRIT_CHANCE * 100)}%" in (
            await crit.get_attribute("title")
        )
        await browser.close()


async def test_a_percentage_says_when_there_is_no_ceiling(server):
    """Пока потолки сняты, строка не выдумывает предел, которого нет."""
    from bot.game.stats import NO_LIMITS

    if not NO_LIMITS:
        pytest.skip("потолки на месте — эта строка про их отсутствие")
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["sneakers"], id=1, slot=Slot.BOOTS)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-hero").click()

        dodge = page.locator("#combat li").filter(has_text="Уворот").first

        assert "потолок" not in await dodge.inner_text()
        assert "потолков сейчас нет" in await dodge.get_attribute("title")
        await browser.close()


async def test_the_card_catches_up_with_a_level_taken_in_a_fight(server):
    """Уровень взяли в бою — карточка догоняет сама, без перезапуска аппа.

    Раньше её читали один раз за сеанс: новый уровень и свободные очки
    появлялись только после того, как мини-апп закроют и откроют заново.
    """
    player = make_player()
    grown = make_player()
    grown.level = player.level + 1
    grown.free_points = 3
    before = build_card(player, TOKEN, viewer_id=player.user_id)
    after = build_card(grown, TOKEN, viewer_id=grown.user_id)
    # Что отдаёт сервер прямо сейчас. Очередью это делать нельзя: карточка
    # перечитывает себя ещё и по таймеру, и лишний запрос съедал бы ответ.
    served = [before]

    async def card_route(route):
        await route.fulfill(
            status=200, content_type="application/json", body=json.dumps(served[0])
        )

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, before, build_shop(player))
        await page.route("**/api/card*", card_route)
        await page.wait_for_selector("#hero:not(.hidden)")

        level = await page.locator("#hero-level").inner_text()
        assert await page.locator("#upgrade").is_hidden()

        # ушли в чат, подрались, вернулись
        served[0] = after
        await open_screen(page, "shop")
        await page.locator("#tab-hero").click()
        await page.wait_for_selector("#upgrade:not(.hidden)")

        assert await page.locator("#hero-level").inner_text() != level
        assert "Свободных очков: 3" in await page.locator("#upgrade").inner_text()
        await browser.close()


async def test_a_slot_tells_what_is_worn_when_you_hover_it(server):
    """Наведение на слот: что надето и что оно даёт, а не просто «оружие»."""
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["pipe"], id=1, wear=3, slot=Slot.WEAPON)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        worn = page.locator("#hero-slots-left .slot:not(.empty)").first
        hint = await worn.get_attribute("title")

        assert hint.startswith("Деревянная бита — оружие")
        assert "👊4–7" in hint  # урон вещи
        assert "У воина в руках" in hint  # и что с ним делает класс

        empty = await page.locator("#hero-slots-left .slot.empty").first.get_attribute(
            "title"
        )
        assert empty.startswith("Пусто: ")
        await browser.close()


async def test_an_empty_slot_falls_back_to_its_icon(server):
    """Подложка не доехала — слот гаснет и показывает значок, как раньше."""
    player = make_player()
    player.gear = []  # всё снято, все восемь слотов пустые
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        # картинки в этом тесте не отдаются: маршрут IMAGES их обрывает
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-bag").click()
        await page.wait_for_timeout(300)

        empty = page.locator("#bag .slot.empty")
        assert await empty.count() == 8
        assert await page.locator("#bag .slot.empty.no-art").count() == 8
        assert "🎩" in await page.locator("#slots-left .slot").first.inner_text()
        await browser.close()


# ---------- бойцовский клуб ----------


def club_of(*fighters) -> dict:
    from bot.webapp.card import build_club

    return build_club(list(fighters), 42)


async def test_the_club_lists_everyone_and_opens_a_card(server):
    """Список клуба: ник, уровень и значок ℹ️ с карточкой соседа."""
    me = make_player()
    rival = make_player()
    rival.user_id = 43
    rival.nickname = "Марла"
    rival.level = 7
    rival.birthplace = "Клуб на Вязов"
    card = build_card(me, TOKEN, viewer_id=me.user_id)
    rival_card = build_card(rival, TOKEN, viewer_id=me.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(me), club=club_of(me, rival)
        )
        # карточку соседа отдаём отдельно: маршруты примеряются с конца
        await page.route(
            "**/api/card?user_id=43",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(rival_card),
            ),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-club").click()
        # вкладка открывается на боях: за списком идём в «Игроки»
        await page.get_by_role("button", name="Игроки", exact=True).click()
        await page.wait_for_selector(".fighter")

        assert await page.locator(".fighter").count() == 2
        assert "2 бойца" in await page.locator("#club-count").inner_text()
        names = await page.locator(".fighter-name").all_inner_texts()
        assert names == ["Растафарайчик", "Марла"]
        levels = await page.locator(".fighter-level").all_inner_texts()
        assert levels == ["[5]", "[7]"]
        assert await page.locator(".fighter.me .fighter-name").inner_text() == names[0]

        await page.locator(".fighter").nth(1).locator(".fighter-info").click()
        await page.wait_for_selector(".sheet-doll")

        assert "Марла [7]" in await page.locator("#sheet-title").inner_text()
        # в карточке соседа есть и аватар, и все восемь слотов
        assert await page.locator(".sheet-doll .avatar").count() == 1
        assert await page.locator(".sheet-doll .slot").count() == 8

        # и ничего никуда не наезжает: рамка аватара кончается там, где
        # начинается правый ряд слотов
        face = await page.locator(".sheet-doll .avatar").bounding_box()
        right = await page.locator(".sheet-doll .slots").nth(1).bounding_box()
        assert face["x"] + face["width"] <= right["x"] + 0.5

        card_text = await page.locator("#sheet-list").inner_text()
        for line in ("Сила", "Ловкость", "Интуиция", "Выносливость"):
            assert line in card_text
        for line in ("Уровень", "Опыт", "Побед", "Поражений", "Ничьих",
                     "Рейды", "Рейтинг"):
            assert line in card_text
        assert "Клуб на Вязов" in card_text
        assert "День рождения персонажа" in card_text
        # чужой кошелёк в карточке не показываем
        assert "Кредиты" not in card_text
        await browser.close()


async def test_a_fighter_row_has_one_way_in_and_it_is_the_card(server):
    """В строке списка одна кнопка — значок ℹ️. Статистика живёт в карточке.

    Раньше кнопок было две, и они делили строку: значок вёл в карточку,
    таблица — сразу в статистику. Выбирать между ними приходилось, ни
    разу не увидев бойца, а список от этого был вдвое длиннее.
    """
    me = make_player()
    rival = make_player()
    rival.user_id = 43
    rival.nickname = "Марла"

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(me, TOKEN, viewer_id=me.user_id),
            build_shop(me), club=club_of(me, rival),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-club").click()
        await page.get_by_role("button", name="Игроки", exact=True).click()
        await page.wait_for_selector(".fighter")

        row = page.locator(".fighter").first
        assert await row.locator("button").count() == 1
        assert await row.locator(".fighter-info").inner_text() == "ℹ️"
        assert await page.locator(".fighter-stats").count() == 0

        # и строки стоят плотно: список читают, а не листают
        first = await page.locator(".fighter").nth(0).bounding_box()
        second = await page.locator(".fighter").nth(1).bounding_box()
        assert first["height"] <= 40, f"строка выросла до {first['height']}"
        assert second["y"] - (first["y"] + first["height"]) <= 6
        await browser.close()


async def test_a_fighter_card_shows_health_and_leads_to_the_stats(server):
    """В карточке бойца видно здоровье, а кнопка ведёт в его статистику."""
    me = make_player()
    rival = make_player()
    rival.user_id = 43
    rival.nickname = "Марла"
    rival.set_hp(rival.max_hp // 3)  # отлёживается после боя
    rival_card = build_card(rival, TOKEN, viewer_id=me.user_id)
    assert rival_card["hp"]["percent"] < 100, "боец должен быть побит"

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(me, TOKEN, viewer_id=me.user_id),
            build_shop(me), club=club_of(me, rival),
        )
        await page.route(
            "**/api/card?user_id=43",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(rival_card),
            ),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-club").click()
        await page.get_by_role("button", name="Игроки", exact=True).click()
        await page.locator(".fighter").nth(1).locator(".fighter-info").click()
        await page.wait_for_selector(".sheet-doll")

        bar = page.locator("#sheet-list .hp")
        assert await bar.count() == 1
        assert await bar.locator(".hp-text").inner_text() == "{} / {}".format(
            rival_card["hp"]["current"], rival_card["hp"]["max"]
        )
        # полоска налита ровно на столько, сколько здоровья осталось
        width = await bar.locator(".hp-fill").evaluate("node => node.style.width")
        assert width == f"{rival_card['hp']['percent']}%"

        # кнопка уводит в статистику именно этого бойца
        await page.get_by_role("button", name="📊 Статистика боёв").click()
        assert await page.locator("#sheet.hidden").count() == 1
        await page.wait_for_selector("#club-stats:not(.hidden)")
        await browser.close()


async def test_the_hero_screen_shows_the_slots_too(server):
    """На «Персонаже» рядом с портретом стоят те же восемь слотов."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#hero .slot").count() == 8
        assert await page.locator("#hero-avatar").is_visible()
        # надетая вещь видна и здесь, и в инвентаре
        assert await page.locator("#hero .slot:not(.empty)").count() == 1
        await browser.close()


async def test_the_bag_pours_a_potion_and_the_counter_sells_them(server):
    """Склянка стоит своей полкой в рюкзаке, и у неё одна кнопка — выпить."""
    player = make_player()
    player.potions = {"heal_small": 2}
    player.effects = [
        ActiveEffect(code="boost_strength", until=now_ts() + 3600 + 47 * 60)
    ]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player, Service.POTIONS)
        )
        drunk = []

        async def pour(route):
            drunk.append(route.request.post_data)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "card": card,
                        "used": {
                            "code": "heal_small",
                            "title": "Эликсир восстановления",
                            "healed": 30,
                            "extended": False,
                            "seconds_left": 0,
                            "left": 1,
                        },
                    }
                ),
            )

        await page.route("**/api/use", pour)
        await page.wait_for_selector("#hero:not(.hidden)")

        # действующий эффект висит и на «Персонаже», и в инвентаре
        chips = await page.locator("#hero-effects .effect").all_inner_texts()
        assert chips == ["💪 Эликсир силы · 1 ч 47 мин"]

        await page.locator("#tab-bag").click()
        box = page.locator("#potion-box")
        assert await box.is_visible()
        assert "В рюкзаке: 2 шт." in await box.inner_text()
        # склянку не надевают и не чинят — только пьют
        buttons = await box.locator(".btn").all_inner_texts()
        assert buttons == ["Использовать"]

        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        await box.locator(".btn").click()
        await page.wait_for_timeout(200)
        assert json.loads(drunk[0]) == {"code": "heal_small"}
        # ответ дошёл: карточка перерисовалась, полка склянок на месте
        assert await page.locator("#potion-box").is_visible()

        # на прилавке аптеки склянки лежат под своим фильтром
        await open_screen(page, "shop")
        await page.wait_for_selector(".shelf")
        await page.get_by_role("button", name="Прочее", exact=True).click()
        titles = await page.locator(
            ".shelf-list:not(.hidden) .thing-title"
        ).all_inner_texts()
        assert "Эликсир восстановления" in titles
        assert "Кастет" not in titles
        await browser.close()


async def test_the_mage_sells_for_stars_and_never_for_credits(server):
    """Прилавок мага: цена в звёздах, кнопка ведёт в счёт Telegram."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), magic=build_magic(player)
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "magic")
        await page.wait_for_selector("#magic .thing")

        counter = page.locator("#magic")
        text = await counter.inner_text()
        assert "Световой меч" in text
        assert "250 ⭐" in text
        assert "💰" not in text, "у мага кредитами не торгуют"
        assert "Прилавок пуст" not in text

        # свойства меча видно прямо на прилавке
        for line in ("👊 Урон: 7–15", "🌀 Уворот: 35%", "🔄 Контрудар: 25%"):
            assert line in text

        # первая кнопка — подписка, она всегда стоит сверху
        buttons = await counter.locator(".btn").all_inner_texts()
        assert buttons[-1] == "Купить · 250 ⭐"
        await browser.close()


async def test_the_pro_card_always_leads_the_mage_counter(server):
    """Подписка стоит первой, показывает акцию и забирается одной кнопкой."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    magic = build_magic(player)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), magic=magic
        )
        taken = []

        async def give(route):
            taken.append(route.request.post_data)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "card": card,
                        "magic": magic,
                        "pro": {
                            "days": 7,
                            "renewed": False,
                            "blade": True,
                            "look": True,
                            "seconds_left": 7 * 24 * 3600,
                        },
                    }
                ),
            )

        await page.route("**/api/pro", give)
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "magic")
        await page.wait_for_selector("#pro-card .thing")

        pro = page.locator("#pro-card .thing")
        text = await pro.inner_text()
        assert "Подписка PRO" in text
        assert "Полуторный опыт за каждый бой" in text
        assert "Клинок ассасина в инвентарь — навсегда" in text

        # подписка идёт раньше любого товара прилавка
        first = page.locator("#magic .thing").first
        assert "Подписка PRO" in await first.inner_text()

        if magic["pro"]["promo"]:
            assert "Бесплатно · 7 дней" in text
            assert "До 1 сентября" in text
            assert await pro.locator(".btn").inner_text() == "Забрать бесплатно"

            page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
            await pro.locator(".btn").click()
            await page.wait_for_timeout(200)
            assert taken == ["{}"]
        await browser.close()


async def test_the_bag_warns_before_one_elixir_puts_out_another(server):
    """Другой временный эликсир гасит нынешний: спрашиваем до глотка."""
    player = make_player()
    player.potions = {"boost_agility": 1}
    player.effects = [ActiveEffect(code="boost_strength", until=now_ts() + 3600)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        poured = []

        async def pour(route):
            poured.append(route.request.post_data)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "card": card,
                        "used": {
                            "code": "boost_agility",
                            "title": "Эликсир ловкости",
                            "healed": 0,
                            "extended": False,
                            "seconds_left": 7200,
                            "left": 0,
                            "replaced": ["Эликсир силы"],
                        },
                    }
                ),
            )

        await page.route("**/api/use", pour)
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-bag").click()
        box = page.locator("#potion-box")

        # предупреждение видно прямо на склянке, ещё до нажатия
        assert "⚠️ Вытеснит «Эликсир силы»" in await box.inner_text()

        # отказ ничего не тратит
        page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        await box.locator(".btn").click()
        await page.wait_for_timeout(200)
        assert poured == []

        # согласие — пьём
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        await box.locator(".btn").click()
        await page.wait_for_timeout(250)
        assert json.loads(poured[0]) == {"code": "boost_agility"}
        await browser.close()


async def test_the_bag_explains_what_the_weapon_does_in_your_hands(server):
    """Рядом с уроном вещи стоит то, во что он превращается у этого класса."""
    from bot.game.equipment import CATALOGUE

    saber = CATALOGUE["lightsaber"]
    player = make_player()  # воин, множитель 0.9
    # один меч надет, второй лежит в рюкзаке: обе надписи видно разом
    player.gear = [
        OwnedItem(item=saber, id=9, slot=Slot.WEAPON),
        OwnedItem(item=saber, id=10),
    ]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-bag").click()
        await page.wait_for_selector("#bag-list .thing")

        bag = await page.locator("#bag-list").inner_text()
        assert "👊 Урон: 7–15 (у воина 6–14)" in bag

        # а в боевых показателях стоит отдельная строка про оружие
        hero = await page.locator("#combat").inner_text()
        # у оружия одна характеристика — реальный урон в этих руках
        assert "Световой меч🗡6–14" in hero.replace("\n", "")
        assert "7–15" not in hero  # своё число вещи живёт в рюкзаке
        await browser.close()


async def test_free_points_are_handed_out_right_on_the_hero_screen(server):
    """Плюсы копят черновик, кнопка отправляет его одним разом."""
    player = make_player()
    player.free_points = 3
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        sent = []

        async def upgrade(route):
            spent = json.loads(route.request.post_data)
            sent.append(spent)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"card": card, "spent": spent, "left": 0}),
            )

        await page.route("**/api/upgrade", upgrade)
        await page.wait_for_selector("#hero:not(.hidden)")

        box = page.locator("#upgrade")
        assert await box.is_visible()
        assert "Свободных очков: 3 из 3" in await box.inner_text()

        # пока ничего не разложено, сохранять нечего
        save = box.get_by_role("button", name="Сохранить")
        assert await save.is_disabled()
        assert "После сохранения поменять выбор будет уже нельзя" in (
            await box.inner_text()
        )

        # два очка в силу, одно в интуицию
        plus = box.locator(".up-row .step:last-child")
        await plus.nth(0).click()
        await plus.nth(0).click()
        await plus.nth(2).click()
        assert "Свободных очков: 0 из 3" in await box.inner_text()
        assert "14 + 2" in await box.inner_text()  # своя сила плюс черновик

        # больше очков нет — плюсы погасли
        assert await plus.nth(1).is_disabled()

        # минус возвращает очко в черновик, сервер об этом не знает
        await box.locator(".up-row .step:first-of-type").nth(0).click()
        assert "Свободных очков: 1 из 3" in await box.inner_text()
        assert sent == []

        # окно называет сам выбор, а не число очков
        asked = []

        def agree(dialog):
            asked.append(dialog.message)
            asyncio.ensure_future(dialog.accept())

        page.on("dialog", agree)
        assert await save.is_enabled()
        await save.click()
        await page.wait_for_timeout(150)
        assert asked and "Сохранить выбор:" in asked[0]
        assert "+1 к силе" in asked[0] and "+1 к интуиции" in asked[0]
        assert "Поменять его будет уже нельзя" in asked[0]
        await page.wait_for_timeout(250)

        assert sent == [{"strength": 1, "intuition": 1}]
        await browser.close()


async def test_a_fighter_without_points_sees_no_panel(server):
    player = make_player()
    player.free_points = 0
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#upgrade").is_hidden()
        await browser.close()


# ---------- ринг в мини-аппе ----------


def ring_with_duel(chosen=None) -> dict:
    """Ответ ринга: идёт бой, ход первый, боец кое-что уже нажал."""
    return {
        "attacks": [
            {"zone": "head", "title": "Голова"},
            {"zone": "chest", "title": "Корпус"},
        ],
        "blocks": [
            {"zone": "head", "title": "Голова + Корпус"},
            {"zone": "chest", "title": "Корпус + Живот"},
        ],
        "modes": [{"code": "fist", "title": "кулачный бой", "emoji": "🥊"}],
        "challenge": None,
        "challenges": [],
        "can_fight": True,
        "duel": {
            "id": 1,
            "mode": {"code": "fist", "title": "кулачный бой", "emoji": "🥊"},
            "in_app": True,
            "started": True,
            "challenger_id": 42,
            "yours_to_start": True,
            "finished": False,
            "summary": [],
            "round": 1,
            "turn": 2,
            "turns_per_round": 3,
            "rounds": 6,
            "resting": False,
            "yours": True,
            "chosen": chosen or {"attack": None, "block": None},
            "fighters": [
                {
                    "user_id": 42, "name": "Растафарайчик", "level": 5,
                    "emoji": "⚔️", "fclass": "Воин", "hp": 40, "max_hp": 100,
                    "percent": 40, "damage_dealt": 30, "ready": True, "you": True,
                    "weapon": "кулаком", "weapon_icon": "👊",
                },
                {
                    "user_id": 43, "name": "Марла", "level": 4,
                    "emoji": "🗡️", "fclass": "Ассасин", "hp": 90, "max_hp": 95,
                    "percent": 95, "damage_dealt": 60, "ready": False, "you": False,
                    "weapon": "ножом", "weapon_icon": "🔪",
                },
            ],
            "log": [
                {
                    "number": 1, "round": 1, "turn": 1, "finished": False,
                    "winner_id": None, "hp_after": {"42": 40, "43": 90},
                    "lines": [
                        "👊 Растафарайчик вкладывается кулаком в живот, "
                        "Марла не отбивает, −5 [90/95]",
                        "🩸 Марла ловит момент и лупит ножом в голову, "
                        "Растафарайчик едва держится, −60 [40/100]",
                    ],
                    "strikes": [
                        {
                            "attacker_id": 42, "defender_id": 43, "zone": "belly",
                            "zone_title": "Живот", "zone_where": "в живот",
                            "outcome": "hit", "emoji": "👊",
                            "title": "попал", "weapon": "кулаком", "damage": 5,
                            "counter": 0, "armor": 0, "hp_after": 90,
                            "missed_turn": False,
                        },
                        {
                            "attacker_id": 43, "defender_id": 42, "zone": "head",
                            "zone_title": "Голова", "zone_where": "в голову",
                            "outcome": "crit", "emoji": "🩸",
                            "title": "крит", "weapon": "ножом", "damage": 60,
                            "counter": 0, "armor": 0, "hp_after": 40,
                            "missed_turn": False,
                        },
                    ],
                }
            ],
        },
    }


async def open_ring(pw, server, fights):
    """Открыть вкладку клуба на разделе боёв."""
    browser, page = await open_page(
        pw, server, build_card(make_player(), TOKEN, viewer_id=42), fights=fights
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.locator("#tab-club").click()
    await page.wait_for_selector("#club:not(.hidden)")
    return browser, page


async def test_the_club_tab_opens_on_the_ring_and_switches_to_players(server):
    """Два раздела на одной вкладке: бои и игроки."""
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, None)

        sections = await page.locator("#club-sections .chip").all_inner_texts()
        # рейда среди пузырей нет: в подвал спускаются из казино
        assert sections == ["Бои", "Отряд", "Игроки", "Статистика"]
        assert await page.locator("#club-fights").is_visible()
        assert await page.locator("#club-players").is_hidden()

        await page.get_by_role("button", name="Игроки", exact=True).click()
        assert await page.locator("#club-players").is_visible()
        assert await page.locator("#club-fights").is_hidden()
        await browser.close()


async def test_an_empty_ring_offers_to_throw_a_challenge(server):
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, None)

        body = await page.locator("#fights-body").inner_text()
        assert "Вызвать на кулачный бой" in body
        assert "Брось вызов" in await page.locator("#fights-note").inner_text()
        await browser.close()


async def test_the_fight_panel_shows_the_board_and_two_columns_of_choices(server):
    """Панель боя: табло сверху, под ним два столбца радиокнопок."""
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, ring_with_duel())

        assert "Раунд 1 из 6, удар 2 из 3" in await page.locator(
            ".fight-round"
        ).inner_text()
        board = await page.locator(".fight-board").inner_text()
        for line in ("Растафарайчик", "VS.", "Марла", "40/100", "✅ Готов", "⏳ Думает"):
            assert line in board

        heads = await page.locator(".zone-head").all_inner_texts()
        # заголовок короткий, а чем бьёт эта рука — в подсказке
        assert heads == ["👊 Удар", "🛡 Блок"]
        columns = page.locator(".zone-list")
        assert await columns.nth(0).locator(".zone").all_inner_texts() == [
            "Голова", "Корпус"
        ]
        assert await columns.nth(1).locator(".zone").all_inner_texts() == [
            "Голова + Корпус", "Корпус + Живот"
        ]
        # пока ничего не выбрано, отправлять нечего
        assert await page.locator("#turn-go").is_disabled()
        await browser.close()


async def test_the_buttons_line_up_under_headers_of_the_same_height(server):
    """Длинное название оружия переносит заголовки во всех столбцах сразу.

    Заголовки и списки лежат двумя рядами одной сетки, поэтому кнопки во
    всех столбцах начинаются на одной высоте — иначе один перенос сдвигал
    бы свой столбец вниз, а соседние оставлял на месте.
    """
    long_named = ring_with_duel({
        "hands": [
            {"hand": 0, "icon": "🔪", "title": "Стилет ассасина"},
            {"hand": 1, "icon": "🔩", "title": "Кастет"},
        ],
    })
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, long_named)

        heads = await page.locator(".zone-head").evaluate_all(
            "nodes => nodes.map(one => one.getBoundingClientRect().height)"
        )
        assert len(set(round(height) for height in heads)) == 1

        tops = await page.locator(".zone-list").evaluate_all(
            "nodes => nodes.map(one => Math.round("
            "one.getBoundingClientRect().top))"
        )
        assert len(set(tops)) == 1, "кнопки в столбцах разъехались по высоте"
        await browser.close()


async def test_the_turn_panel_fits_the_screen_without_wrapping(server):
    """Каждая надпись — одна строка, целиком, и панель не шире экрана.

    Наборов ровно два, и третьего быть не может: щит занимает вторую руку,
    поэтому «три столбца» и «блок в три зоны» вместе не встречаются.

    * два оружия — три столбца, блок в две зоны;
    * щит — два столбца, зато блок в три зоны и надписи длиннее.
    """
    two_weapons = ring_with_duel()
    two_weapons["duel"]["hands"] = [
        {"hand": 0, "icon": "🔪", "title": "Стилет ассасина", "label": "Удар 1"},
        {"hand": 1, "icon": "🔩", "title": "Кастет", "label": "Удар 2"},
    ]
    two_weapons["duel"]["blocks"] = [
        {"zone": "head", "title": "Голова+Корпус"},
        {"zone": "chest", "title": "Корпус+Живот"},
    ]

    with_shield = ring_with_duel()
    with_shield["duel"]["hands"] = [
        {"hand": 0, "icon": "🔨", "title": "Кувалда", "label": "Удар"},
    ]
    with_shield["duel"]["blocks"] = [
        {"zone": "head", "title": "Голова+Корпус+Живот"},
        {"zone": "chest", "title": "Корпус+Живот+Пояс"},
    ]

    async with async_playwright() as pw:
        for name, ring in (("два оружия", two_weapons), ("щит", with_shield)):
            for width in (320, 360, 420):
                browser, page = await open_ring(pw, server, ring)
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(120)
                where = f"{name}, {width}px"

                # Перенос — это две строки текста, то есть два разных верхних
                # края у его прямоугольников. Считать сами прямоугольники
                # нельзя: шрифт разбивает строку на куски и на одной строке
                tall = await page.locator(".zone-head, .zone span").evaluate_all(
                    "nodes => nodes.filter(one => {"
                    "  const range = document.createRange();"
                    "  range.selectNodeContents(one);"
                    "  const tops = Array.from(range.getClientRects())"
                    "    .map(box => Math.round(box.top));"
                    "  return new Set(tops).size > 1;"
                    "}).map(one => one.textContent)"
                )
                assert tall == [], f"{where}: перенеслись {tall}"

                # панель уместилась в экран, а не вылезла вбок
                spill = await page.locator(".zone-columns").evaluate(
                    "node => node.scrollWidth - node.clientWidth"
                )
                assert spill <= 1, f"{where}: панель шире экрана на {spill}px"

                # и надписи видно целиком, а не обрезанными многоточием
                cut = await page.locator(".zone-head, .zone span").evaluate_all(
                    "nodes => nodes.filter("
                    "one => one.scrollWidth > one.clientWidth + 1"
                    ").map(one => one.textContent)"
                )
                assert cut == [], f"{where}: обрезались {cut}"
                await browser.close()


async def test_the_turn_goes_to_the_judge_in_one_press(server):
    """Выбор живёт на странице, судья узнаёт о нём один раз — по «Вперёд!»."""
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, ring_with_duel())

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(ring_with_duel({"attack": "head", "block": "chest"})),
            )

        await page.route("**/api/fight", catch)

        # выбрали удар — отправлять всё ещё рано, блока нет
        await page.locator(".zone-list").nth(0).get_by_text("Голова").click()
        assert await page.locator("#turn-go").is_disabled()
        assert sent == []

        await page.locator(".zone-list").nth(1).get_by_text("Корпус + Живот").click()
        assert await page.locator("#turn-go").is_enabled()
        assert sent == []  # до нажатия «Вперёд!» судья ничего не знает

        await page.locator("#turn-go").click()
        await page.wait_for_selector("#turn-go", state="detached")

        assert sent == [{"action": "turn", "attacks": {"0": "head"}, "block": "chest"}]
        # выбор принят: вместо кнопок ожидание соперника
        assert "Ждём соперника" in await page.locator("#fights-body").inner_text()
        await browser.close()


async def test_two_weapons_give_two_columns_and_a_shield_widens_the_block(server):
    """Набор кнопок идёт от снаряжения: две руки — два удара, щит — блок шире."""
    armed = ring_with_duel()
    armed["duel"]["hands"] = [
        {"hand": 0, "icon": "🔪", "title": "Нож"},
        {"hand": 1, "icon": "🔩", "title": "Кастет"},
    ]
    armed["duel"]["blocks"] = [
        {"zone": "head", "title": "Голова + Корпус (+живот 🛡)"},
        {"zone": "chest", "title": "Корпус + Живот (+пояс 🛡)"},
    ]
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, armed)

        heads = page.locator(".zone-head")
        assert await heads.all_inner_texts() == ["🔪 Удар 1", "🔩 Удар 2", "🛡 Блок"]
        # название оружия ушло в подсказку: в заголовок оно не помещается
        assert await heads.nth(0).get_attribute("title") == "Нож"
        assert await heads.nth(1).get_attribute("title") == "Кастет"
        assert await page.locator(".zone-columns.three").count() == 1
        assert "(+живот 🛡)" in await page.locator(".zone-list").nth(2).inner_text()

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(ring_with_duel({"chosen": {
                    "attacks": {"0": "head", "1": "chest"}, "attack": "head",
                    "block": "head",
                }})),
            )

        await page.route("**/api/fight", catch)

        # пока выбрана только одна рука, отправлять нельзя
        await page.locator(".zone-list").nth(0).get_by_text("Голова").click()
        await page.locator(".zone-list").nth(2).get_by_text(
            "Голова + Корпус (+живот 🛡)"
        ).click()
        assert await page.locator("#turn-go").is_disabled()

        await page.locator(".zone-list").nth(1).get_by_text("Корпус").click()
        assert await page.locator("#turn-go").is_enabled()

        await page.locator("#turn-go").click()
        await page.wait_for_timeout(200)

        assert sent == [{
            "action": "turn",
            "attacks": {"0": "head", "1": "chest"},
            "block": "head",
        }]
        await browser.close()


async def test_the_choice_can_be_changed_before_it_is_sent(server):
    """Передумать можно сколько угодно: пока не нажали «Вперёд!», выбор свой."""
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, ring_with_duel())
        column = page.locator(".zone-list").nth(0)

        await column.get_by_text("Голова").click()
        await column.get_by_text("Корпус").click()

        lit = await page.locator(".zone-list").nth(0).locator(".zone.on").all_inner_texts()
        assert lit == ["Корпус"]  # горит одно, последнее
        await browser.close()


async def test_the_log_speaks_the_words_of_the_judge(server):
    """В аппе тот же комментарий, что в ветке, — сплошным текстом, без раундов."""
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, ring_with_duel())

        log = await page.locator(".fight-log").inner_text()
        assert "Ход боя" in log
        assert "Растафарайчик вкладывается кулаком в живот" in log
        assert "Марла ловит момент" in log
        assert "Раунд 1" not in log  # раунды в аппе не считаем
        await browser.close()


async def test_the_damage_is_coloured_by_the_kind_of_strike(server):
    """Обычный урон синим, критический — красным: размен видно с ходу."""
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, ring_with_duel())

        marks = page.locator(".fight-log .dmg")
        assert await marks.all_inner_texts() == ["−5", "−60"]
        assert await marks.nth(0).get_attribute("class") == "dmg"
        assert await marks.nth(1).get_attribute("class") == "dmg crit"

        colours = [
            await marks.nth(index).evaluate("node => getComputedStyle(node).color")
            for index in range(2)
        ]
        assert colours[0] != colours[1]  # цвет и правда разный, а не только класс

        # строка от подсветки не рассыпалась
        log = await page.locator(".fight-log").inner_text()
        assert "Марла ловит момент и лупит ножом в голову" in log
        assert "−60 [40/100]" in log
        await browser.close()


async def test_the_corner_break_hides_the_buttons(server):
    """В перерыве бить некуда: панель ждёт вместе с бойцами."""
    resting = ring_with_duel()
    resting["duel"]["resting"] = True

    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, resting)

        assert await page.locator(".zone").count() == 0
        assert "по углам" in await page.locator("#fights-body").inner_text()
        await browser.close()


async def test_the_ring_waits_for_the_gong_of_the_one_who_called(server):
    """Соперник вышел — бой ждёт: гонг даёт тот, кто звал."""
    waiting = ring_with_duel()
    waiting["duel"]["started"] = False
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, waiting)

        assert await page.locator(".zone").count() == 0  # бить ещё нечем
        body = await page.locator("#fights-body").inner_text()
        assert "Гонга ещё не было" in body and "Соперник вышел" in body

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(ring_with_duel()),
            )

        await page.route("**/api/fight", catch)
        await page.get_by_role("button", name="🥊 Выйти на ринг").click()
        await page.wait_for_selector("#turn-go")

        assert sent == [{"action": "go"}]
        await browser.close()


async def test_the_one_who_was_called_only_waits(server):
    """Второму бойцу решать нечего: он может только уйти."""
    waiting = ring_with_duel()
    waiting["duel"]["started"] = False
    waiting["duel"]["yours_to_start"] = False

    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, waiting)

        body = await page.locator("#fights-body").inner_text()
        assert "Ждём, пока вызвавший даст гонг" in body
        assert await page.get_by_role("button", name="🥊 Выйти на ринг").count() == 0
        assert await page.get_by_role("button", name="Отказаться").count() == 1
        await browser.close()


async def test_the_end_of_the_fight_shows_the_result(server):
    """Бой кончился — на экране итог теми же словами, что и в ветке."""
    over = ring_with_duel()
    over["duel"]["finished"] = True
    over["duel"]["summary"] = [
        "🏆 Победа: Растафарайчик (Воин)",
        "",
        "📊 Итоги",
        "💀 Растафарайчик: Нанесено урона 133, получено +114 опыта, "
        "+20 💰, рейтинг 1054 (+20)",
        "🥷 Марла: Нанесено урона 61, получено 0 опыта, рейтинг 993 (−20)",
    ]
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, over)

        assert "Бой окончен" in await page.locator(".fight-round").inner_text()
        assert await page.locator(".zone").count() == 0  # драться уже нечем
        card = await page.locator(".fight-finish").inner_text()
        assert "🏆 Победа: Растафарайчик" in card
        assert "Нанесено урона 133, получено +114 опыта" in card
        assert "рейтинг 993 (−20)" in card

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({**ring_with_duel(), "duel": None}),
            )

        await page.route("**/api/fight", catch)
        await page.get_by_role("button", name="Завершить бой").click()
        await page.wait_for_selector(".fight-finish", state="detached")

        assert sent == [{"action": "done"}]
        assert "Брось вызов" in await page.locator("#fights-note").inner_text()
        await browser.close()


# ---------- рейд ----------


def raid_with_wave(over=None) -> dict:
    """Ответ подвала: идёт волна, один боец уже отработал."""
    raid = {
        "id": 1, "wave": 2, "in_app": True, "resting": False,
        "finished": False, "summary": [],
        "boss": {
            "code": "cellar_boss", "title": "Босс Подвала", "emoji": "🩸",
            "image": "", "level": 9, "hp": 180, "max_hp": 300, "percent": 60,
            "weapon": "кувалдой",
        },
        "party": [
            {
                "user_id": 42, "name": "Растафарайчик", "level": 5, "emoji": "⚔️",
                "hp": 70, "max_hp": 100, "percent": 70, "damage_dealt": 45,
                "alive": True, "acted": False, "you": True,
            },
            {
                "user_id": 43, "name": "Марла", "level": 4, "emoji": "🗡️",
                "hp": 20, "max_hp": 95, "percent": 21, "damage_dealt": 60,
                "alive": True, "acted": True, "you": False,
            },
            {
                "user_id": 44, "name": "Зевака", "level": 3, "emoji": "🛡️",
                "hp": 0, "max_hp": 90, "percent": 0, "damage_dealt": 10,
                "alive": False, "acted": True, "you": False,
            },
        ],
        "yours": True, "alive": True, "acted": False,
        "chosen": {"attack": None, "block": None},
        "log": [
            {
                "number": 1, "round": 1, "turn": 1, "finished": False,
                "winner_id": None, "hp_after": {"42": 70, "-1": 180},
                "lines": [
                    "👊 Растафарайчик вламывает кулаком в живот, "
                    "Босс Подвала оседает, −45 [180/300]",
                ],
                "strikes": [
                    {
                        "attacker_id": 42, "defender_id": -1, "zone": "belly",
                        "zone_title": "Живот", "zone_where": "в живот",
                        "outcome": "hit", "emoji": "👊", "title": "попал",
                        "weapon": "кулаком", "damage": 45, "counter": 0,
                        "armor": 0, "hp_after": 180, "missed_turn": False,
                    }
                ],
            }
        ],
    }
    raid.update(over or {})
    return {**EMPTY_RAID, "raid": raid, "boss": {**BOSS_CARD, "live": True}}


async def open_raid(pw, server, raid=None):
    """Открыть подвал.

    Пузыря «Рейд» среди разделов клуба больше нет: в подвал спускаются из
    казино на карте. Сам раздел жив, и тесты рейда — про него, а не про
    дорогу до казино; её проверяет test_locations_app.
    """
    browser, page = await open_page(
        pw, server, build_card(make_player(), TOKEN, viewer_id=42), raid=raid
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.evaluate("showTab('club'); pickClubSection('raid')")
    await page.wait_for_selector("#club-raid:not(.hidden)")
    return browser, page


async def test_the_raid_names_the_boss_and_opens_his_numbers(server):
    """Заголовок раздела и кнопка «i»: под ней всё, с чем босс выйдет."""
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server)

        head = await page.locator(".raid-head").inner_text()
        assert "Ограбление Босса подпольного казино" in head
        assert await page.locator(".boss-stats").count() == 0

        await page.locator("#boss-info").click()
        await page.wait_for_selector(".boss-stats")

        card = await page.locator(".boss-stats").inner_text()
        assert "Он тут всё построил" in card
        assert "на 4 уровня выше отряда" in card  # прикидка, а не живой босс
        for line in ("Уровень", "304", "Кувалда", "15–25", "🪨 Сопротивление", "31%"):
            assert line in card
        assert "Броня: Голова" in card and "Броня: Ноги" not in card  # нулевую не пишем

        # босс стоит куклой, как боец: надетое по слотам, пустые — тенью
        doll = page.locator(".boss-stats .sheet-doll")
        assert await doll.locator(".slot").count() == 3
        assert await doll.locator(".slot.empty").count() == 1
        assert await doll.locator(".avatar").count() == 1
        # тап по слоту рассказывает, что там надето
        await doll.locator(".slot").first.click()

        # вторым нажатием карточка закрывается
        await page.locator("#boss-info").click()
        await page.wait_for_selector(".boss-stats", state="detached")
        await browser.close()


async def test_the_boss_card_of_a_live_raid_says_so(server):
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, raid_with_wave())

        await page.locator("#boss-info").click()
        await page.wait_for_selector(".boss-stats")

        assert "босс идущего рейда" in await page.locator(".boss-stats").inner_text()
        await browser.close()


async def test_an_empty_cellar_asks_for_a_pass(server):
    """Размер отряда не спрашивают — спрашивают пропуск."""
    sent, asked = [], []

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server)

        body = await page.locator("#raid-body").inner_text()
        assert "Подвал открыт с 20:00 до 22:00 мск" in body
        assert "В инвентаре: 2 шт." in body
        assert await page.locator("#raid-size").count() == 0

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(raid_with_wave()),
            )

        def on_dialog(dialog):
            asked.append(dialog.message)
            asyncio.ensure_future(dialog.dismiss())

        await page.route("**/api/raid", catch)
        page.on("dialog", on_dialog)

        # отказ — пропуск на месте
        await page.locator("#raid-open").click()
        await page.wait_for_timeout(200)
        assert sent == []
        assert "Вы используете Рейд-пасс из инвентаря" in asked[0]
        assert "Останется: 1" in asked[0]

        page.remove_listener("dialog", on_dialog)
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        await page.locator("#raid-open").click()
        await page.wait_for_selector("#raid-go")

        assert sent == [{"action": "open", "buy": False}]
        await browser.close()


async def test_an_empty_pocket_offers_to_buy_a_pass(server):
    """Пропусков нет — предлагаем купить в том же окне, одним согласием."""
    empty = {**EMPTY_RAID, "gate": {**EMPTY_RAID["gate"], "passes": 0}}
    sent, asked = [], []

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, empty)

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(raid_with_wave()),
            )

        await page.route("**/api/raid", catch)
        page.on("dialog", lambda dialog: (
            asked.append(dialog.message), asyncio.ensure_future(dialog.accept())
        ))
        await page.locator("#raid-open").click()
        await page.wait_for_selector("#raid-go")

        assert "Купить Рейд-пасс за 10 кредитов и войти?" in asked[0]
        assert sent == [{"action": "open", "buy": True}]
        await browser.close()


async def test_the_wave_puts_a_vs_between_the_boss_and_the_party(server):
    """Кто против кого: карточка босса, «VS», отряд."""
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, raid_with_wave())

        body = page.locator("#raid-body")
        assert await body.locator(".versus").inner_text() == "VS"
        # порядок на экране: сперва босс, потом «VS», потом отряд
        order = await body.evaluate(
            "node => Array.from(node.querySelectorAll("
            "'.boss-card, .versus, .raid-party')).map(one => one.className)"
        )
        assert order == ["boss-card", "versus", "raid-party"]
        await browser.close()


async def test_the_opener_starts_the_raid_by_that_name(server):
    """Кнопка созвавшего называется «Начать сейчас» и не липнет к соседней."""
    mine = {
        **EMPTY_RAID,
        "lobby": {
            "id": 9, "size": 10, "total": 2, "mine": True, "joined": True,
            "in_app": True, "seconds_left": 65, "timeout": 120,
            "can_start": True,
            "boss": {
                "code": "cellar_boss", "title": "Босс подпольного казино",
                "emoji": "🩸", "image": "", "tagline": "",
            },
            "members": [
                {"user_id": 42, "name": "Растафарайчик", "level": 5},
                {"user_id": 43, "name": "Марла", "level": 4},
            ],
        },
    }
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, mine)

        start = page.locator("#raid-start-now")
        assert await start.inner_text() == "⚔️ Начать сейчас"
        gap = await start.evaluate(
            "node => parseFloat(getComputedStyle(node).marginBottom)"
        )
        assert gap > 0, "кнопка «Начать сейчас» наезжает на «Выйти из отряда»"
        await browser.close()


async def test_a_beaten_boss_closes_the_window(server):
    """Победил в это окно — кнопки нет, и сказано почему."""
    done = {
        **EMPTY_RAID,
        "gate": {**EMPTY_RAID["gate"], "won": True, "spent": True},
    }
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, done)

        body = await page.locator("#raid-body").inner_text()
        assert "Босс повержен: в это окно ты своё взял" in body
        assert "с 00:00 до 02:00 мск" in body
        assert await page.locator("#raid-open").is_disabled()
        await browser.close()


async def test_a_shut_cellar_says_when_it_opens(server):
    shut = {
        **EMPTY_RAID,
        "gate": {**EMPTY_RAID["gate"], "open": False, "window": ""},
    }
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, shut)

        body = await page.locator("#raid-body").inner_text()
        assert "Подвал закрыт. Босса бьют 0–2, 8–10, 12–14, 16–18, 20–22 мск." in body
        assert "Ближайшее окно с 00:00 до 02:00 мск." in body
        assert await page.locator("#raid-open").is_disabled()
        await browser.close()


async def test_a_gathering_party_can_be_joined(server):
    lobby = {
        **EMPTY_RAID,
        "lobbies": [
            {
                "id": 5, "size": 4, "total": 2, "mine": False, "joined": False,
                "in_app": True, "seconds_left": 125, "timeout": 600,
                "can_start": True,
                "boss": {
                    "code": "cellar_boss", "title": "Босс Подвала",
                    "emoji": "🩸", "image": "", "tagline": "",
                },
                "members": [
                    {"user_id": 43, "name": "Марла", "level": 4},
                    {"user_id": 44, "name": "Зевака", "level": 3},
                ],
            }
        ],
    }
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, lobby)

        card = await page.locator(".fight-card").inner_text()
        assert "Босс Подвала — отряд 2/4" in card
        assert "Марла [4]" in card
        assert "Выходим через 2:0" in card  # часы тикают, секунды не ловим
        # вывести отряд может только тот, кто его собрал
        assert await page.locator("#raid-start-now").count() == 0

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(raid_with_wave()),
            )

        await page.route("**/api/raid", catch)
        # чужой сбор — тот же пропуск и то же согласие, что и на свой
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        await page.get_by_role("button", name="🩸 В отряд").click()
        await page.wait_for_selector("#raid-go")

        assert sent == [{"action": "join", "lobby_id": 5, "buy": False}]
        await browser.close()


async def test_the_opener_sees_the_clock_and_the_early_start(server):
    """Свой сбор: обратный отсчёт тикает, а рядом кнопка «Выходим сейчас»."""
    mine = {
        **EMPTY_RAID,
        "lobby": {
            "id": 7, "size": 4, "total": 2, "mine": True, "joined": True,
            "in_app": True, "seconds_left": 65, "timeout": 600,
            "can_start": True,
            "boss": {
                "code": "cellar_boss", "title": "Босс Подвала",
                "emoji": "🩸", "image": "", "tagline": "",
            },
            "members": [
                {"user_id": 42, "name": "Растафарайчик", "level": 5},
                {"user_id": 43, "name": "Марла", "level": 4},
            ],
        },
    }
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, mine)

        clock = await page.locator(".raid-clock").inner_text()
        assert clock.startswith("⏳ Выходим через 1:0")

        # секунда прошла — на экране это видно, без нового ответа сервера
        await page.wait_for_function(
            "() => document.querySelector('.raid-clock')"
            ".textContent !== " + json.dumps(clock)
        )

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(raid_with_wave()),
            )

        await page.route("**/api/raid", catch)
        await page.locator("#raid-start-now").click()
        await page.wait_for_selector("#raid-go")

        assert sent == [{"action": "go"}]
        await browser.close()


async def test_a_lonely_gathering_has_nothing_to_start(server):
    """Один в подвал не ходит: кнопки ранней отправки нет."""
    alone = {
        **EMPTY_RAID,
        "lobby": {
            "id": 8, "size": 4, "total": 1, "mine": True, "joined": True,
            "in_app": True, "seconds_left": 590, "timeout": 600,
            "can_start": False,
            "boss": {
                "code": "cellar_boss", "title": "Босс Подвала",
                "emoji": "🩸", "image": "", "tagline": "",
            },
            "members": [{"user_id": 42, "name": "Растафарайчик", "level": 5}],
        },
    }

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, alone)

        assert await page.locator("#raid-start-now").count() == 0
        assert "Выходим через 9:5" in await page.locator(".raid-clock").inner_text()
        await browser.close()


async def test_the_wave_shows_the_boss_and_the_whole_party(server):
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, raid_with_wave())

        assert "Волна 2" in await page.locator(".fight-round").inner_text()
        boss = await page.locator(".boss-card").inner_text()
        assert "Босс Подвала [9]" in boss and "180/300" in boss

        members = await page.locator(".raid-member").all_inner_texts()
        assert "⏳ ⚔️ Растафарайчик [5] — ты" in members[0]
        assert "✅" in members[1]  # Марла отработала волну
        assert "💀" in members[2]  # Зеваку вынесли
        assert await page.locator(".raid-member.down").count() == 1
        await browser.close()


async def test_the_raid_turn_goes_in_one_press(server):
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, raid_with_wave())

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(raid_with_wave({"acted": True})),
            )

        await page.route("**/api/raid", catch)
        assert await page.locator("#raid-go").is_disabled()

        await page.locator("#club-raid .zone-list").nth(0).get_by_text(
            "Голова"
        ).click()
        await page.locator("#club-raid .zone-list").nth(1).get_by_text(
            "Корпус + Живот"
        ).click()
        assert sent == []  # до «Вперёд!» судья ничего не знает

        await page.locator("#raid-go").click()
        await page.wait_for_selector("#raid-go", state="detached")

        assert sent == [{"action": "turn", "attacks": {"0": "head"}, "block": "chest"}]
        assert "Ждём остальных" in await page.locator("#raid-body").inner_text()
        await browser.close()


async def test_the_fallen_watch_from_the_side(server):
    down = raid_with_wave({"alive": False})
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, down)

        assert await page.locator(".zone").count() == 0
        assert "Тебя вынесли" in await page.locator("#raid-body").inner_text()
        await browser.close()


async def test_the_end_of_the_raid_shows_the_result(server):
    over = raid_with_wave({
        "finished": True,
        "summary": [
            "🏆 Босс повержен",
            "",
            "📊 Кто сколько набил",
            "1. 🗡️ Марла — урона 60, приз: 🔪 Нож",
            "2. ⚔️ Растафарайчик — урона 45",
            "",
            "💰 Каждому по 50 💰.",
        ],
    })
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, over)

        assert "Рейд окончен" in await page.locator(".fight-round").inner_text()
        card = await page.locator(".fight-finish").inner_text()
        assert "Босс повержен" in card and "приз: 🔪 Нож" in card

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(EMPTY_RAID)
            )

        await page.route("**/api/raid", catch)
        await page.get_by_role("button", name="Завершить рейд").click()
        await page.wait_for_selector(".fight-finish", state="detached")

        assert sent == [{"action": "done"}]
        await browser.close()


async def test_the_raid_log_speaks_the_words_of_the_judge(server):
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, raid_with_wave())

        log = await page.locator("#club-raid .fight-log").inner_text()
        assert "Ход рейда" in log
        assert "Босс Подвала оседает" in log
        marks = page.locator("#club-raid .fight-log .dmg")
        assert await marks.all_inner_texts() == ["−45"]
        await browser.close()


# ---------- статистика боёв ----------


HISTORY = {
    "user_id": 42,
    "name": "Растафарайчик",
    "total": 4,
    "counts": {"win": 2, "loss": 2, "draw": 0},
    "before": 7,
    "days": [
        {
            "date": "2026-09-03",
            "fights": [
                {
                    "id": 9, "rival_id": 43, "rival": "Марла", "result": "win",
                    "emoji": "🏆", "result_title": "Победа", "rounds": 4,
                    "caption": "Победа — бой против Марла",
                    "mode": {"code": "fist", "title": "кулачный бой", "emoji": "🥊"},
                    "in_app": True, "created_at": "2026-09-03 20:30:00",
                    "date": "2026-09-03",
                },
                {
                    "id": 8, "rival_id": 44, "rival": "Тайлер", "result": "loss",
                    "emoji": "❌", "result_title": "Поражение", "rounds": 9,
                    "caption": "Поражение — бой против Тайлер",
                    "mode": {"code": "armed", "title": "бой с оружием", "emoji": "⚔️"},
                    "in_app": False, "created_at": "2026-09-03 19:00:00",
                    "date": "2026-09-03",
                },
            ],
        },
        {
            "date": "2026-09-02",
            "fights": [
                {
                    "kind": "raid", "id": 3, "boss": "Босс Подвала",
                    "boss_emoji": "🩸", "emoji": "❌", "result": "loss",
                    "result_title": "Поражение",
                    "caption": "Поражение (с Марла) — рейд против Босса Подвала",
                    "verdict": "Отряд не вышел из подвала", "allies": "Марла",
                    "boss_level": 9, "waves": 5, "damage": 120, "alive": False,
                    "prize": None, "created_at": "2026-09-02 21:00:00",
                    "date": "2026-09-02",
                }
            ],
        },
        {
            "date": "2026-09-01",
            "fights": [
                {
                    "id": 7, "rival_id": 43, "rival": "Марла", "result": "win",
                    "emoji": "🏆", "result_title": "Победа", "rounds": 6,
                    "caption": "Победа — бой против Марла",
                    "mode": {"code": "fist", "title": "кулачный бой", "emoji": "🥊"},
                    "in_app": False, "created_at": "2026-09-01 12:00:00",
                    "date": "2026-09-01",
                }
            ],
        },
    ],
}

FIGHT_LOG = {
    "fight": HISTORY["days"][0]["fights"][0],
    "names": {"42": "Растафарайчик", "43": "Марла"},
    "sides": [
        {"user_id": 42, "name": "Растафарайчик", "you": True},
        {"user_id": 43, "name": "Марла", "you": False},
    ],
    "has_log": True,
    "turns": [
        {
            "number": 1, "round": 1, "turn": 1, "finished": False,
            "winner_id": None, "hp_after": {"42": 70, "43": 60},
            "lines": [
                "👊 Растафарайчик вламывает кулаком по поясу, "
                "Марла теряет равновесие, −12 [60/95]",
                "🛡 Удар ножом по ногам от Марла вязнет в блоке Растафарайчик",
            ],
            "strikes": [
                {
                    "attacker_id": 42, "defender_id": 43, "zone": "belt",
                    "zone_title": "Пояс", "zone_where": "по поясу",
                    "outcome": "hit", "emoji": "👊", "title": "попал",
                    "weapon": "кулаком", "damage": 12, "counter": 0, "armor": 0,
                    "hp_after": 60, "missed_turn": False,
                },
                {
                    "attacker_id": 43, "defender_id": 42, "zone": "legs",
                    "zone_title": "Ноги", "zone_where": "по ногам",
                    "outcome": "block", "emoji": "🛡", "title": "в блок",
                    "weapon": "ножом", "damage": 0, "counter": 0, "armor": 0,
                    "hp_after": 70, "missed_turn": False,
                },
            ],
        }
    ],
}


async def open_stats(pw, server, history=None, fight_log=None):
    browser, page = await open_page(
        pw, server, build_card(make_player(), TOKEN, viewer_id=42),
        history=history, fight_log=fight_log,
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.locator("#tab-club").click()
    await page.get_by_role("button", name="Статистика", exact=True).click()
    await page.wait_for_selector("#club-stats:not(.hidden)")
    # История приходит запросом: ждём, пока «Открываем...» сменится ответом
    await page.wait_for_function(
        "!document.getElementById('stats-note').textContent.includes('Открываем')"
    )
    return browser, page


async def test_the_statistics_section_lists_fights_by_day(server):
    """Бои разложены по дням, свежий день сверху, дата — по-человечески."""
    async with async_playwright() as pw:
        browser, page = await open_stats(pw, server, HISTORY)

        note = await page.locator("#stats-note").inner_text()
        assert "4 боя" in note and "2 побед" in note

        days = await page.locator("#club-stats .shelf-head").all_inner_texts()
        assert days == ["3 сентября", "2 сентября", "1 сентября"]

        rows = await page.locator(".fight-row").all_inner_texts()
        # «Победа — Марла» читалось так, будто победила Марла
        assert "Победа — бой против Марла" in rows[0]
        assert "кулачный бой, раундов 4" in rows[0]
        assert "Поражение — бой против Тайлер" in rows[1]
        await browser.close()


async def test_a_fight_opens_into_the_words_of_the_judge(server):
    """Тап по бою проваливает в разбор — теми же словами, что были в ветке."""
    async with async_playwright() as pw:
        browser, page = await open_stats(pw, server, HISTORY, FIGHT_LOG)

        await page.locator(".fight-row").first.click()
        await page.wait_for_selector(".log-line")

        log = await page.locator("#stats-body").inner_text()
        assert "Растафарайчик вламывает кулаком по поясу" in log
        assert "вязнет в блоке" in log
        assert "Раунд 1" not in log

        # и обратно к списку
        await page.get_by_role("button", name="← К списку боёв").click()
        await page.wait_for_selector(".fight-row")
        assert await page.locator(".fight-row").count() == 4
        await browser.close()


# ---------- групповой бой в карточке ----------


def battle_with_round(over=None) -> dict:
    """Ответ клуба: идёт раунд, один боец уже отработал."""
    battle = {
        "id": 1, "kind": "team", "kind_title": "Командный бой", "emoji": "🤝",
        "mode": {"code": "armed", "title": "бой с оружием", "emoji": "⚔️"},
        "round": 2, "in_app": True, "finished": False, "summary": [],
        "hands": [{"hand": 0, "icon": "👊", "title": "Кулаки"}],
        "blocks": [
            {"zone": "head", "title": "Голова + Корпус"},
            {"zone": "chest", "title": "Корпус + Живот"},
        ],
        "party": [
            {
                "user_id": 42, "name": "Растафарайчик", "level": 5, "emoji": "⚔️",
                "hp": 70, "max_hp": 100, "percent": 70, "damage_dealt": 45,
                "alive": True, "team": 0, "team_title": "Красные",
                "rival_id": 43, "rival": "Марла", "ready": False, "you": True,
            },
            {
                "user_id": 43, "name": "Марла", "level": 4, "emoji": "🗡️",
                "hp": 20, "max_hp": 95, "percent": 21, "damage_dealt": 60,
                "alive": True, "team": 1, "team_title": "Синие",
                "rival_id": 42, "rival": "Растафарайчик", "ready": True,
                "you": False,
            },
        ],
        "yours": True, "alive": True, "fighting": True, "acted": False,
        "chosen": {"attacks": {}, "block": None},
        "log": [
            {
                "number": 1, "round": 1, "turn": 1, "finished": False,
                "winner_id": None, "hp_after": {"42": 70, "43": 20},
                "lines": [
                    "👊 Растафарайчик вламывает кулаком в живот, "
                    "Марла оседает, −45 [20/95]",
                ],
                "strikes": [
                    {
                        "attacker_id": 42, "defender_id": 43, "zone": "belly",
                        "zone_title": "Живот", "zone_where": "в живот",
                        "outcome": "hit", "emoji": "👊", "title": "попал",
                        "weapon": "кулаком", "damage": 45, "counter": 0,
                        "armor": 0, "hp_after": 20, "missed_turn": False,
                    }
                ],
            }
        ],
    }
    battle.update(over or {})
    return {**EMPTY_BATTLE, "battle": battle}


async def open_squad(pw, server, battle=None):
    """Открыть вкладку клуба на разделе отряда."""
    browser, page = await open_page(
        pw, server, build_card(make_player(), TOKEN, viewer_id=42), battle=battle
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.locator("#tab-club").click()
    await page.get_by_role("button", name="Отряд", exact=True).click()
    await page.wait_for_selector("#club-battle:not(.hidden)")
    return browser, page


async def test_the_squad_section_offers_both_kinds_of_group_fight(server):
    async with async_playwright() as pw:
        browser, page = await open_squad(pw, server)

        body = await page.locator("#battle-body").inner_text()
        assert "Командный бой на 2" in body
        assert "Королевская битва на 8" in body
        assert "Собери состав" in await page.locator("#battle-note").inner_text()
        await browser.close()


async def test_the_group_round_shows_the_board_and_the_pair(server):
    async with async_playwright() as pw:
        browser, page = await open_squad(pw, server, battle_with_round())

        head = await page.locator(".fight-round").inner_text()
        assert "Командный бой — раунд 2" in head
        board = await page.locator(".raid-party").inner_text()
        assert "Растафарайчик" in board and "против Марла" in board
        assert "Красные" in board and "Синие" in board
        # столбцов два: одна рука и блок
        assert await page.locator("#club-battle .zone-list").count() == 2
        await browser.close()


async def test_the_group_turn_goes_in_one_press(server):
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_squad(pw, server, battle_with_round())

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(battle_with_round({"acted": True})),
            )

        await page.route("**/api/battle", catch)
        assert await page.locator("#battle-go").is_disabled()

        await page.locator("#club-battle .zone-list").nth(0).get_by_text(
            "Голова"
        ).click()
        await page.locator("#club-battle .zone-list").nth(1).get_by_text(
            "Корпус + Живот"
        ).click()
        assert sent == []  # до «Вперёд!» судья ничего не знает

        await page.locator("#battle-go").click()
        await page.wait_for_selector("#battle-go", state="detached")

        assert sent == [{"action": "turn", "attacks": {"0": "head"}, "block": "chest"}]
        assert "Ждём остальных" in await page.locator("#battle-body").inner_text()
        await browser.close()


async def test_without_a_pair_there_are_no_buttons_this_round(server):
    idle = battle_with_round({"fighting": False})
    async with async_playwright() as pw:
        browser, page = await open_squad(pw, server, idle)

        assert await page.locator("#club-battle .zone").count() == 0
        assert "пары не досталось" in await page.locator("#battle-body").inner_text()
        await browser.close()


async def test_the_end_of_the_group_fight_shows_the_result(server):
    over = battle_with_round({
        "finished": True,
        "summary": [
            "🏆 Красные берут бой",
            "",
            "📊 Итоги",
            "⚔️ Растафарайчик: нанесено урона 45, +114 опыта",
        ],
    })
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_squad(pw, server, over)

        assert "Бой окончен" in await page.locator(".fight-round").inner_text()
        card = await page.locator(".fight-finish").inner_text()
        assert "Красные берут бой" in card

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(EMPTY_BATTLE),
            )

        await page.route("**/api/battle", catch)
        await page.get_by_role("button", name="Завершить бой").click()
        await page.wait_for_selector(".fight-finish", state="detached")

        assert sent == [{"action": "done"}]
        await browser.close()


async def test_a_raid_stands_in_the_list_of_fights(server):
    """Рейд читается как бой: исход, с кем ходили и против кого."""
    async with async_playwright() as pw:
        browser, page = await open_stats(pw, server, HISTORY)

        rows = await page.locator(".fight-row").all_inner_texts()
        raid = next(row for row in rows if "рейд" in row)
        assert "❌ Поражение (с Марла) — рейд против Босса Подвала" in raid
        assert "🩸 Босс Подвала, 9 ур. · волн 5 · урона 120" in raid

        # тап открывает итог рейда, а не разбор по ходам: его там нет
        await page.get_by_text("рейд против Босса Подвала").click()
        await page.wait_for_selector(".boss-rows")

        card = await page.locator("#stats-body").inner_text()
        assert "Отряд не вышел из подвала" in card
        assert "Нанесено урона" in card and "120" in card
        assert "Ходили вместе" in card and "Марла" in card
        assert "Разбор по ходам в рейде не ведётся" in card
        await browser.close()


async def test_an_old_fight_says_it_has_no_log(server):
    """Бои до этой версии писались одним итогом — экран честно об этом говорит."""
    old = dict(FIGHT_LOG, has_log=False, turns=[])

    async with async_playwright() as pw:
        browser, page = await open_stats(pw, server, HISTORY, old)

        await page.locator(".fight-row").first.click()
        await page.wait_for_selector("#stats-body .screen-note")

        assert "начал вести разбор" in await page.locator("#stats-body").inner_text()
        assert await page.locator(".log-line").count() == 0
        await browser.close()


async def test_an_empty_history_says_so(server):
    async with async_playwright() as pw:
        browser, page = await open_stats(pw, server)

        assert "ещё не дрался" in await page.locator("#stats-note").inner_text()
        assert await page.locator(".fight-row").count() == 0
        await browser.close()


async def test_raids_are_counted_apart_from_fights_with_people(server):
    """Подвал стоит своей строкой: «4 / 10» походов, а победы — только людские.

    Босса валят отрядом, и он не человек. Пока рейды писались в общий
    счёт, десять заходов раздували победы, а неудачный поход портил
    репутацию бойца — по такому счёту нельзя было понять, кого он бил.
    """
    player = make_player()
    player.wins, player.losses, player.draws = 4, 2, 1
    player.raid_wins, player.raid_fights = 4, 10
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-hero").click()

        rows = page.locator("#record li")
        assert await rows.filter(has_text="Побед").first.inner_text() == "Побед\n4"
        assert await rows.filter(has_text="Рейды").first.inner_text() == "Рейды\n4 / 10"
        await browser.close()


@pytest.mark.parametrize(
    "screen,section",
    [("ring", "fights"), ("raid", "raid"), ("shop", None)],
)
async def test_a_link_from_the_chat_opens_the_screen_it_promised(
    server, screen, section
):
    """Объявление в чате ведёт не «в приложение», а на нужный экран.

    Иначе зовущая ссылка высаживает человека на карточке персонажа, и
    искать бой, на который его позвали, он идёт сам.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), query=f"?view={screen}"
        )
        # Ждём не «Персонажа»: по такой ссылке карточка как раз уступает
        # место тому экрану, ради которого человек и пришёл
        await page.wait_for_selector("#bar:not(.hidden)")

        if section is None:
            assert await page.locator("#shop:not(.hidden)").count() == 1
        else:
            assert await page.locator("#club:not(.hidden)").count() == 1
            assert await page.locator(f"#club-{section}:not(.hidden)").count() == 1
        await browser.close()


# ---------- карта города ----------


async def open_map(pw, server, city=None, card=None):
    """Открыть вкладку карты."""
    player = make_player()
    browser, page = await open_page(
        pw, server, card or build_card(player, TOKEN, viewer_id=player.user_id),
        build_shop(player, Service.CLOTHES), city=city,
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.locator("#tab-map").click()
    await page.wait_for_selector("#map:not(.hidden)")
    await page.wait_for_selector(".zone-house")
    return browser, page


async def test_the_map_opens_on_the_district_you_stand_in(server):
    """Карта открывается там, где боец: искать себя по городу не надо."""
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("pharmacy"))

        assert "Аптека" in await page.locator("#map-here").inner_text()
        # шесть районов пузырями, открыт тот, где боец
        chips = await page.locator("#map-districts .chip").all_inner_texts()
        assert len(chips) == 6
        assert await page.locator("#map-pic").get_attribute("src") is not None
        houses = await page.locator(".zone-house").all_inner_texts()
        assert "📍 Аптека" in houses and "Магазин одежды" in houses
        await browser.close()


async def test_houses_lie_where_the_picture_lies(server):
    """Дома кладутся по нарисованной картинке, а не по размеру окна.

    Карта показывается целиком, и на экране другого сложения сверху и
    снизу появляются поля. Считать зоны от окна значит сдвинуть все дома
    на высоту этих полей — и человек будет попадать мимо.
    """
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server)

        drawn = await page.evaluate(
            """() => {
                const pic = document.getElementById('map-pic');
                const box = pic.getBoundingClientRect();
                // Картинки в тестах не грузятся, и своего размера они не
                // называют — как и в приложении, берём размер карт
                const ratio = pic.naturalWidth && pic.naturalHeight
                    ? pic.naturalWidth / pic.naturalHeight
                    : 941 / 1672;
                const width = Math.min(box.width, box.height * ratio);
                return {
                    left: box.left + (box.width - width) / 2,
                    top: box.top + (box.height - width / ratio) / 2,
                    width, height: width / ratio,
                };
            }"""
        )
        club = await page.locator(".zone-house").first.bounding_box()

        # клуб стоит на 24.4% ширины и 6% высоты самой картинки
        assert abs(club["x"] - (drawn["left"] + 0.244 * drawn["width"])) < 1.5
        assert abs(club["y"] - (drawn["top"] + 0.060 * drawn["height"])) < 1.5
        assert abs(club["width"] - 0.542 * drawn["width"]) < 1.5
        await browser.close()


async def test_walking_asks_first_and_then_counts_down(server):
    """Дорога занимает время: сначала спрашивают, потом идёт отсчёт."""
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server)
        asked = []

        async def road(route):
            asked.append(route.request.post_data)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({
                    "map": city_map("fight_club", {
                        "going": True, "to": "pharmacy", "to_title": "Аптека",
                        "seconds_left": 20, "text": "В пути до аптеки — 20 сек",
                    }),
                    "card": build_card(make_player(), TOKEN, viewer_id=42),
                }),
            )

        await page.route("**/api/travel", road)

        # сначала спрашивают, и в вопросе стоит цена дороги
        questions = []

        async def answer(dialog):
            questions.append(dialog.message)
            await dialog.accept()

        page.on("dialog", answer)
        await page.locator(".zone-house").filter(has_text="Мастерская").click()
        await page.wait_for_selector("#map-road:not(.hidden)")

        assert questions and "10 сек" in questions[0]

        assert json.loads(asked[0]) == {"to": "workshop"}
        assert "20 сек" in await page.locator("#map-road").inner_text()
        await browser.close()


async def test_a_house_without_a_trade_says_when_it_opens(server):
    """Банк на карте есть, зайти можно, а услуги пока нет."""
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("bank"))
        await page.get_by_role("button", name="Деловой квартал ·").click()

        said = []
        page.on("dialog", lambda dialog: said.append(dialog.message) or
                asyncio.ensure_future(dialog.dismiss()))
        await page.locator(".zone-house").filter(has_text="Банк").click()
        await page.wait_for_timeout(300)

        assert said and "Скоро" in said[0] and "хранение денег" in said[0]
        await browser.close()


async def test_the_card_says_where_the_fighter_stands(server):
    """Строка под куклой: город и дом. В пути — дорога вместо дома."""
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player, Service.CLOTHES),
        )
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#hero-city").inner_text() == (
            "Vegas City · 📍 Бойцовский клуб VEGAS"
        )

        walker = make_player()
        walker.set_out("pharmacy", 20)
        moving = build_card(walker, TOKEN, viewer_id=walker.user_id)
        await page.evaluate("card => render(card)", moving)

        line = await page.locator("#hero-city").inner_text()
        assert "В пути до дома «Аптека»" in line
        await browser.close()
