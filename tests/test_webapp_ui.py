"""Живая проверка витрины в браузере: вкладки и фильтры магазина.

Тест поднимает настоящий мини-апп и открывает его Chromium'ом, подменяя
только ответы API. Если браузера в системе нет — тест пропускается: остальной
прогон от этого не зависит.
"""

import asyncio
import base64
import json
import os
import re
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

# Однопиксельный png для тех тестов, где картинка должна загрузиться. По
# умолчанию страница картинок не грузит вовсе — до бакета из тестов не
# дотянуться, — и вёрстку с картинками иначе было бы не проверить
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="Chromium не найден")


def make_player(location: str = "fight_club") -> Player:
    stats = Stats(strength=14, agility=8, intuition=8, endurance=13)
    player = Player(
        user_id=42,
        nickname="Растафарайчик",
        class_code="warrior",
        level=5,
        credits=214,
        location=location,
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


# Мастерская без вещей: прилавок модификаторов на месте, чинить нечего
EMPTY_WORKSHOP = {
    "credits": 0,
    "repair": [],
    "shop": [{"kind": "weapon", "title": "Заточка оружия", "icon": "🗡", "items": []}],
    "mods": [],
    "targets": [],
}


def hospital_state(hp: int = 40, max_hp: int = 300, credits: int = 200) -> dict:
    """Приёмный покой, как его отдаёт сервер."""
    from bot.game.hospital import CURES

    # Потолок здоровья задаём числом: экран его не считает, а берёт из
    # ответа, и привязывать тест к формуле здоровья незачем
    missing = max(0, max_hp - hp)
    return {
        "credits": credits,
        "hp": {
            "current": hp, "max": max_hp, "percent": round(hp / max_hp * 100),
            "regen_seconds": 600, "missing": missing,
        },
        "cures": [
            {
                "code": cure.code, "title": cure.title, "price": cure.price,
                "note": cure.note,
                "healed": cure.healed(hp, max_hp),
                "affordable": credits >= cure.price,
                "useful": cure.healed(hp, max_hp) > 0,
            }
            for cure in CURES
        ],
    }


EMPTY_HOSPITAL = hospital_state()


# Никто ещё не дрался
EMPTY_HISTORY = {
    "user_id": 42, "name": "Растафарайчик", "days": [], "total": 0,
    "counts": {"win": 0, "loss": 0, "draw": 0}, "before": None,
}


EMPTY_MARKET = {
    "credits": 0, "fee": 5, "sections": [], "mine": [], "sellable": [],
}

BOSS_CARD = {
    "code": "cellar_boss", "title": "Босс Казино", "emoji": "🩸",
    "image": "", "raid_name": "Ограбление Босса Казино",
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


def to_rgb(hexed: str) -> str:
    """Цвет так, как его возвращает браузер: «rgb(31, 111, 235)»."""
    red, green, blue = (int(hexed[step:step + 2], 16) for step in (1, 3, 5))
    return f"rgb({red}, {green}, {blue})"


def city_map(
    here: str = "fight_club", road: dict | None = None, raid: dict | None = None
) -> dict:
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
    if raid:
        body["raid"] = {**body["raid"], **raid}
    return body


async def open_page(
    pw, server, card, shop=None, query="", topup=None, looks=None, club=None,
    magic=None, fights=None, history=None, fight_log=None, raid=None, market=None,
    battle=None, city=None, workshop=None, hospital=None, images=False,
    telegram="",
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
    await page.route("**/api/workshop*", canned(workshop or EMPTY_WORKSHOP))
    await page.route("**/api/hospital*", canned(hospital or EMPTY_HOSPITAL))
    if fight_log is not None:
        await page.route("**/api/fight/*", canned(fight_log))
    # Обычно телеграмовского скрипта нет вовсе — страница умеет и без него.
    # Тесту про старый клиент нужен свой: он подсовывается сюда же
    await page.route("https://telegram.org/**", lambda route: route.fulfill(
        status=200, content_type="application/javascript", body=telegram
    ))
    # Картинки по умолчанию не грузим: до бакета из тестов не дотянуться,
    # и каждая была бы секундой ожидания. Кому нужна настоящая — просит
    # `images=True` и получает пиксель
    if images:
        await page.route(IMAGES, lambda route: route.fulfill(
            status=200, content_type="image/png", body=PIXEL
        ))
    else:
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
async def fan_page(db):
    """Прилавок фанатского магазина: линия своей команды целиком."""
    async for page in shop_screen(db, Service.FAN):
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


async def test_the_fan_shop_shows_its_own_line(fan_page):
    """«Северный Вал» одевает целиком: бита и кроссовки одной команды."""
    heads = await shelves(fan_page)
    # восемь полок: своей линии нет только в перчатках, и пустой полки тут нет
    assert [head.split("\n")[0] for head in heads] == [
        "Голова", "Оружие", "Щиты", "Футболки", "Пояс",
        "Верхняя одежда", "Ноги", "Обувь",
    ]
    assert await fan_page.locator("#shop-title").inner_text() == (
        "🧣 Магазин «Северный Вал»"
    )
    # товар открывается десятым уровнем, а боец пятого — значит, под замком
    note = await fan_page.locator("#shop-note").inner_text()
    assert "десятый уровень" in note
    assert "Бита с гвоздями" not in await visible_titles(fan_page)


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
            "Монтировка", "Нож", "Дубинка бойца", "Трость шулера",
            "Кувалда вышибалы")
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

        # Снимают в инвентаре: на экране персонажа клетка только рассказывает
        await page.locator("#tab-bag").click()
        await page.locator("#slots-left .slot").nth(2).click()
        await page.wait_for_timeout(200)

        assert asked and "Клубная футболка" in asked[0]
        await browser.close()


async def test_the_character_doll_tells_about_a_thing_instead_of_undressing(server):
    """На экране персонажа клетка рассказывает о вещи, а не снимает её.

    Снять вещь нажатием там, куда заходят посмотреть характеристики, —
    из тех потерь, за которые игра получает своё «опять слетело».
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    asked, calls = [], []

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.route("**/api/unequip", lambda route: calls.append(route.request.url))
        page.on("dialog", lambda dialog: asked.append(dialog.message))

        await page.locator("#hero-slots-left .slot:not(.empty)").first.click()
        await page.wait_for_selector("#sheet:not(.hidden)")

        said = await page.locator("#sheet").inner_text()
        assert "Деревянная бита" in said
        assert "Даёт надетой" in said and "Урон" in said, "свойств не показали"
        assert "Снять — в инвентаре" in said, "не сказали, где снимают"
        assert not asked, "экран персонажа спросил про снятие"
        assert not calls, "вещь сняли с экрана персонажа"
        await browser.close()


async def test_a_thing_on_its_last_legs_is_visible_in_the_doll(server):
    """Запаса осталось на три боя — клетка светится и носит ключ.

    Вещь рассыпается надетой и посреди боя, а рюкзак открывают не каждый
    день. Поэтому предупреждение живёт там, куда боец и так смотрит.
    """
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["pipe"], id=1, wear=17, slot=Slot.WEAPON)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        cell = page.locator("#hero-slots-left .slot:not(.empty)").first
        assert "aging" in (await cell.get_attribute("class"))
        assert await cell.locator(".slot-wear").count() == 1
        assert "Износ: 17/20" in await cell.get_attribute("title")
        await browser.close()


async def test_the_last_fight_of_a_thing_is_said_out_loud(server):
    """Последний пункт запаса — красная клетка и прямые слова."""
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["pipe"], id=1, wear=19, slot=Slot.WEAPON)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        cell = page.locator("#hero-slots-left .slot:not(.empty)").first
        assert "dying" in (await cell.get_attribute("class"))
        assert "ещё один бой" in await cell.get_attribute("title")
        await browser.close()


async def test_a_thing_with_a_long_life_ahead_says_nothing(server):
    """Целая вещь не кричит: предупреждение стоит только под конец."""
    player = make_player()  # обрезок трубы с износом 3 из 20
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        cell = page.locator("#hero-slots-left .slot:not(.empty)").first
        classes = await cell.get_attribute("class")
        assert "aging" not in classes and "dying" not in classes
        assert await cell.locator(".slot-wear").count() == 0
        await browser.close()


async def test_the_bag_counts_the_fights_a_thing_has_left(server):
    """В рюкзаке у доживающей вещи написано, сколько ей осталось."""
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["pipe"], id=1, wear=18)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-bag").click()

        wear = page.locator("#bag-list .thing-wear").first
        said = await wear.inner_text()
        assert "18/20" in said and "в запасе 2 боя" in said
        assert "aging" in (await wear.get_attribute("class"))
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
    """Вещи дали выше потолка — карточка это объясняет, а не молчит.

    Режущих потолков осталось два: контрудар и стойкость блока. У пар
    «уворот — точность» и «крит — антикрит» потолок итога намеренно
    поставлен выше всего, что можно собрать: они спорят вычитанием, и
    резать итог значило бы решать бой потолком.
    """
    from bot.game.stats import MAX_COUNTER_CHANCE, MAX_DODGE_TOTAL, NO_LIMITS

    if NO_LIMITS:
        pytest.skip("потолки сняты в bot/game/stats.py")
    ceiling = round(MAX_COUNTER_CHANCE * 100)
    player = make_player()
    player.agility = 80
    player.gear = [OwnedItem(item=CATALOGUE["lightsaber"], id=1, slot=Slot.WEAPON)]
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    assert card["combat"]["caps"]["counter_chance"]["capped"]

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-hero").click()

        counter = page.locator("#combat li").filter(has_text="Контрудар").first
        # срезанный процент подписью не помечают — его красят золотом
        assert await counter.inner_text() == f"🔄 Контрудар\n{ceiling}%"
        assert await counter.locator(".value.capped").count() == 1
        assert f"но выше {ceiling}% не растёт" in (
            await counter.get_attribute("title")
        )

        # а уворот золотом не горит: его потолок выше всего собираемого
        dodge = page.locator("#combat li").filter(has_text="Уворот").first
        assert await dodge.locator(".value.capped").count() == 0
        assert MAX_DODGE_TOTAL >= 1.0
        assert "потолков сейчас нет" in await dodge.get_attribute("title")
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
    rival.seen_at = now_ts() - 20 * 60  # и заходил давно
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

        # под шкалой — не «готов к бою», а в клубе ли он сейчас
        note = await page.locator("#sheet-list .sheet-hp-note").inner_text()
        assert "Не был в клубе 20 минут" in note
        assert "готов к бою" not in note.lower()
        # и сколько ему до строя: этого по шкале не понять
        assert "в строю через" in note

        # и видно, где он сейчас: вызывать есть смысл только того, кто в клубе
        assert "📍 Бойцовский клуб VEGAS" in await page.locator(
            "#sheet-list .sheet-place"
        ).inner_text()

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
            # Подсказки аналитика приходят только подписчику; в этой
            # заготовке их нет — тест аналитика кладёт их сам
            "scout": None,
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


async def open_raid(pw, server, raid=None, telegram=""):
    """Открыть подвал.

    Пузыря «Рейд» среди разделов клуба больше нет: в подвал спускаются из
    казино на карте. Сам раздел жив, и тесты рейда — про него, а не про
    дорогу до казино; её проверяет test_locations_app.
    """
    # Подвал открывает дверь казино, и только она: кнопка внизу — клуб,
    # где бы боец ни стоял. Потому и идём сюда через карту
    browser, page = await open_page(
        pw, server, build_card(make_player("casino"), TOKEN, viewer_id=42),
        raid=raid, city=city_map("casino"), telegram=telegram,
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.locator("#tab-map").click()
    await page.wait_for_selector(".zone-house")
    await page.locator(".zone-house").filter(has_text="Казино").click()
    await page.wait_for_selector("#club-raid:not(.hidden)")
    return browser, page


async def test_the_raid_names_the_boss_and_opens_his_numbers(server):
    """Заголовок раздела и кнопка «i»: под ней всё, с чем босс выйдет."""
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server)

        head = await page.locator(".raid-head").inner_text()
        assert "Ограбление Босса Казино" in head
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
                "code": "cellar_boss", "title": "Босс Казино",
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


# Старый настольный клиент: объект вибрации в SDK есть, а вызов бросает.
# Так ведёт себя Telegram, когда версия клиента ниже той, в которой метод
# появился, — проверка «а есть ли HapticFeedback» такой клиент проходит
OLD_CLIENT = """
window.Telegram = {
  WebApp: {
    initData: "",
    ready() {},
    expand() {},
    HapticFeedback: {
      impactOccurred() { throw new Error("WebAppMethodUnsupported"); },
      selectionChanged() { throw new Error("WebAppMethodUnsupported"); },
      notificationOccurred() { throw new Error("WebAppMethodUnsupported"); },
    },
  },
};
"""


async def test_a_client_without_vibration_still_fights(server):
    """Клиент без вибрации не должен терять удары.

    Вибрация вызывалась до `try`, и на старом настольном клиенте бросок
    оставлял флаг «занято» поднятым навсегда: первый удар уходил или не
    уходил, а дальше кнопки молчали — без единого слова на экране.
    """
    sent = []

    async with async_playwright() as pw:
        browser, page = await open_raid(
            pw, server, raid_with_wave(), telegram=OLD_CLIENT
        )

        async def catch(route):
            sent.append(route.request.post_data_json)
            await route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(raid_with_wave()),  # волна идёт, ход снова доступен
            )

        await page.route("**/api/raid", catch)

        async def swing():
            await page.locator("#club-raid .zone-list").nth(0).get_by_text(
                "Голова"
            ).click()
            await page.locator("#club-raid .zone-list").nth(1).get_by_text(
                "Корпус + Живот"
            ).click()
            await page.locator("#raid-go").click()

        await swing()
        assert len(sent) == 1, "первый удар не ушёл"

        # И второй тоже: флаг «занято» обязан опуститься
        await page.wait_for_selector("#raid-go")
        await swing()
        assert len(sent) == 2, "после первого удара кнопки замолчали"
        await browser.close()


async def test_a_pressed_trick_shows_up_in_the_raid_at_once(server):
    """Нажал приём — обводка зелёная, энергия меньше. Сразу, а не потом.

    Раздел перерисовывается только когда что-то поменялось, и в список
    «что-то» приёмы не входили. Сервер честно списывал энергию и клал
    заготовку, а на экране не менялось ничего: ни волна, ни здоровье, ни
    длина лога от нажатия не двигаются. Игрок видел прежнюю шкалу и
    несветящуюся плитку до самой следующей волны.
    """
    before = raid_with_wave()
    before["raid"]["abilities"] = tricks_state(energy=9)
    for trick in before["raid"]["abilities"]["tricks"]:
        trick["armed"] = False  # начинаем с чистого стола
    # Ответ на нажатие: энергия ушла, заготовка легла. Всё остальное — то же
    after = json.loads(json.dumps(before))
    armed = after["raid"]["abilities"]
    armed["energy"] = 6
    armed["left"] = 2
    armed["tricks"][0]["armed"] = True

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, before)

        panel = page.locator("#club-raid .tricks")
        assert "9 / 20" in await panel.inner_text()
        assert await panel.locator(".trick.armed").count() == 0

        await page.route("**/api/raid", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(after)
        ))
        await panel.locator(".trick").first.click()

        # Плитка светится зелёным, и энергии стало меньше
        await page.wait_for_selector("#club-raid .trick.armed")
        said = await page.locator("#club-raid .tricks").inner_text()
        assert "6 / 20" in said, f"шкала не обновилась: {said}"
        assert "осталось приёмов: 2" in said
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


# ---------- аналитик в подвале ----------


BOSS_SCOUT = {
    "title": "Волна 2: стойка Босса Казино. Бьёт кувалдой сверху.",
    "attack": "Вообще он чаще закрывает Живот — 66% и Корпус — 63%.",
    "block": "Вообще он чаще бьёт Корпус — 23% и Голову — 23%.",
    "attack_tip": {"move": "Бей в Ноги",
                   "why": "он закроет его с вероятностью 52%"},
    "block_tip": {"move": "Закрывай Голову+Корпус",
                  "why": "вероятность отбить удар 46%"},
}


async def test_the_analyst_speaks_in_the_cellar_too(server):
    """Подписчик видит в подвале тот же разбор, что и в дуэли."""
    async with async_playwright() as pw:
        browser, page = await open_raid(
            pw, server, raid_with_wave({"scout": BOSS_SCOUT})
        )
        await page.wait_for_selector(".zone-columns")

        # Разбор свёрнут: заголовок виден, строки — по нажатию
        scout = page.locator("#raid-body .scout")
        assert "Босса Казино" in await scout.inner_text()
        await scout.locator("summary").click()

        said = await scout.inner_text()
        assert "Босса Казино" in said
        assert "Живот" in said and "Корпус" in said
        await browser.close()


async def test_the_cellar_tips_stand_over_their_own_buttons(server):
    """Совет по удару — над ударами, совет по блоку — над блоком."""
    async with async_playwright() as pw:
        browser, page = await open_raid(
            pw, server, raid_with_wave({"scout": BOSS_SCOUT})
        )
        await page.wait_for_selector(".zone-columns")

        tips = page.locator("#raid-body .zone-tip")
        assert await tips.count() == 2
        assert "Бей в Ноги" in await tips.nth(0).inner_text()
        assert "Закрывай Голову+Корпус" in await tips.nth(1).inner_text()

        strike = await tips.nth(0).bounding_box()
        guard = await tips.nth(1).bounding_box()
        heads = page.locator("#raid-body .zone-head")
        for tip, head in (
            (strike, await heads.nth(0).bounding_box()),
            (guard, await heads.last.bounding_box()),
        ):
            assert tip["y"] + tip["height"] <= head["y"] + 0.5, "совет не над кнопками"
        assert strike["x"] + strike["width"] <= guard["x"] + 0.5
        await browser.close()


async def test_the_analyst_starts_folded_and_leaves_the_bars_in_view(server):
    """Разбор свёрнут: развёрнутый он выталкивает шкалы здоровья с экрана.

    Совет при этом остаётся на виду — он в клетках над кнопками, а не в
    разборе: подписчику должно хватать одного взгляда, а не чтения.
    """
    async with async_playwright() as pw:
        browser, page = await open_raid(
            pw, server, raid_with_wave({"scout": BOSS_SCOUT})
        )
        await page.wait_for_selector(".zone-columns")

        scout = page.locator("#raid-body .scout")
        assert await scout.get_attribute("open") is None, "разбор развёрнут"
        # Строки разбора спрятаны, заголовок и советы — нет
        assert "чаще закрывает" not in await scout.inner_text()
        tips = page.locator("#raid-body .zone-tip")
        assert "Бей в Ноги" in await tips.nth(0).inner_text()
        assert await tips.nth(1).is_visible()
        await browser.close()


async def test_an_unfolded_analyst_stays_unfolded_through_a_repaint(server):
    """Развернул — читает: экран подвала перерисовывается каждые две секунды.

    Без памяти о нажатии разбор захлопывался бы на глазах, и прочесть его
    до конца было бы нельзя.
    """
    first = raid_with_wave({"scout": BOSS_SCOUT})
    second = raid_with_wave({
        "scout": {**BOSS_SCOUT, "attack": "Теперь он чаще закрывает Голову — 71%."}
    })
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, first)
        await page.wait_for_selector(".zone-columns")
        await page.locator("#raid-body .scout summary").click()
        assert "чаще закрывает" in await page.locator("#raid-body .scout").inner_text()

        await page.route("**/api/raid*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(second),
        ))
        await page.wait_for_function(
            "() => {"
            "  const box = document.querySelector('#raid-body .scout');"
            "  return box && box.open && box.innerText.includes('Голову — 71%');"
            "}",
            timeout=8000,
        )
        await browser.close()


async def test_without_a_subscription_the_cellar_says_nothing(server):
    """Аналитик — умение подписки: без неё панели в подвале нет."""
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, raid_with_wave({"scout": None}))
        await page.wait_for_selector(".zone-columns")

        assert await page.locator("#raid-body .scout").count() == 0
        assert await page.locator("#raid-body .zone-tip").count() == 0
        await browser.close()


async def test_a_new_stance_repaints_the_advice(server):
    """Стойка сменилась — совет обязан смениться на экране.

    Тот самый случай, на котором уже обжигались: подпись экрана рейда не
    видела заготовок, и нажатый приём не доезжал до глаз. Здесь то же
    место: кроме слов аналитика, от смены стойки не меняется ничего, и
    без них в подписи подписчик до конца волны читал бы прошлый совет.
    """
    first = raid_with_wave({"scout": BOSS_SCOUT})
    second = raid_with_wave({
        "scout": {
            **BOSS_SCOUT,
            "attack_tip": {"move": "Бей в Голову", "why": "он закроет его с 48%"},
        }
    })
    answers = [first, second]
    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, first)
        await page.wait_for_selector(".zone-columns")
        assert "Бей в Ноги" in await page.locator("#raid-body .zone-tip").first.inner_text()

        await page.route("**/api/raid*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(answers.pop() if len(answers) > 1 else second),
        ))
        await page.wait_for_function(
            "document.querySelector('#raid-body .zone-tip')"
            ".textContent.includes('Бей в Голову')",
            timeout=8000,
        )
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
    "screen,section,where",
    [("ring", "fights", "fight_club"), ("raid", "raid", "casino"),
     ("shop", None, "clothes_shop")],
)
async def test_a_link_from_the_chat_opens_the_screen_it_promised(
    server, screen, section, where
):
    """Объявление в чате ведёт не «в приложение», а на нужный экран.

    Иначе зовущая ссылка высаживает человека на карточке персонажа, и
    искать бой, на который его позвали, он идёт сам. Экран при этом
    открывается тот, что доступен на месте: подвал — из казино.
    """
    player = make_player(where)
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


async def open_map(pw, server, city=None, card=None, images=False, hospital=None):
    """Открыть вкладку карты."""
    player = make_player()
    browser, page = await open_page(
        pw, server, card or build_card(player, TOKEN, viewer_id=player.user_id),
        build_shop(player, Service.CLOTHES), city=city, images=images,
        hospital=hospital,
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await page.locator("#tab-map").click()
    await page.wait_for_selector("#map:not(.hidden)")
    await page.wait_for_selector(".zone-house")
    return browser, page


async def test_four_doors_stand_on_the_quarter_and_their_signs_fit(server):
    """Жилой квартал: четыре дома, четыре двери, и ни одной подписи за рамкой.

    Дома в квартале стоят по углам карты — на прежних картах все двери
    были посередине. Подпись шире двери и висит под её серединой, так
    что у края она подпирает рамку: дому слева снизу её пришлось
    подвинуть внутрь. Проверяем и то, что все четыре целиком на
    картинке, и то, что подвинутая действительно сдвинута.
    """
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("residential_apartment"))

        houses = page.locator(".zone-house")
        assert await houses.count() == 4
        titles = await page.locator(".zone-sign").evaluate_all(
            "nodes => nodes.map(node => node.textContent)"
        )
        assert [name.replace("📍 ", "") for name in titles] == [
            "Жилой дом №1", "Жилой дом №2", "Жилой дом №3", "Жилой дом №4",
        ]

        # Каждая подпись целиком внутри картинки: меряем в долях самой
        # карты, а не экрана, — холст растянут по ней
        boxes = await page.locator(".zone-sign").evaluate_all(
            "nodes => nodes.map(node => {"
            "  const box = node.getBBox();"
            "  return [box.x, box.x + box.width];"
            "})"
        )
        for left, right in boxes:
            assert left >= 0 and right <= 941, f"подпись за рамкой: {left}–{right}"

        # Дом слева снизу: подпись стоит правее середины своей двери,
        # иначе она подпирала бы край картинки
        from bot.game.locations import get_location

        third = get_location("residential_apartment_3").bounds
        middle = (third.x + third.w / 2) * 941
        anchor = await page.locator(".zone-sign").nth(2).get_attribute("x")
        assert float(anchor) > middle, "подпись у края не подвинули"
        await browser.close()


async def test_the_map_opens_on_the_district_you_stand_in(server):
    """Карта открывается там, где боец: искать себя по городу не надо."""
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("pharmacy"))

        assert await page.locator("#map-pic").get_attribute("src") is not None
        # Ни заголовка, ни панели над картой: экран занимает сама карта
        assert await page.locator("#map .screen-head").count() == 0
        houses = await page.locator(".zone-house .zone-sign").all_text_contents()
        assert "📍 Аптека" in houses
        assert "Магазин одежды" in houses
        await browser.close()


async def test_the_highlight_sits_on_the_door(server):
    """Подсвечена дверь, а касание ловит рамка с запасом вокруг неё.

    Дом занимает полкарты, войти в него можно в одном месте — туда и
    целятся. Но дверь на телефоне с ноготь, поэтому область касания шире
    двери; подсветка при этом остаётся ровно на ней, иначе на рисунке
    загорится кусок стены.
    """
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server)

        club = page.locator(".zone-house").first
        points = await club.locator(".zone-line").get_attribute("points")
        corners = [pair.split(",") for pair in points.split(" ")]

        assert len(corners) == 4, "вход — четырёхугольник"
        # Числом угол не проверяем: разметку дверей правят руками по
        # картам, и такой тест ломался бы на каждой правке, ничего при
        # этом не сторожа. Сторожим другое — что клиент рисует ровно то,
        # что прислал сервер, и в единицах самой карты
        from bot.game.locations import get_location

        door = get_location("fight_club").entrance
        for (x, y), (drawn_x, drawn_y) in zip(door, corners):
            assert abs(float(drawn_x) - x * 941) < 0.01
            assert abs(float(drawn_y) - y * 1672) < 0.01

        door = await club.locator(".zone-line").bounding_box()
        touch = await club.locator(".zone-touch").bounding_box()

        assert touch["width"] > door["width"], "под палец не расширили"
        assert touch["height"] > door["height"]
        # и расширили именно вокруг двери, а не куда попало
        assert touch["x"] < door["x"] and touch["y"] < door["y"]
        assert touch["x"] + touch["width"] > door["x"] + door["width"]
        await browser.close()


@pytest.mark.parametrize(
    "district,where",
    [("main_hub", "fight_club"), ("pawnshop_casino", "pawnshop"),
     ("stadium_bar", "bar")],
)
async def test_the_sign_hangs_under_the_door_and_stays_on_the_map(
    server, district, where
):
    """Подпись висит под дверью — над ней нарисована вывеска самого дома.

    Снизу тесно: у комиссионки и бара двери у самой земли, а нижним краем
    карта уходит под панель вкладок. Поэтому проверяем не только «под
    дверью», но и что подпись видно целиком — панель её не срезала.
    """
    async with async_playwright() as pw:
        # карта открывается там, где боец, — значит, его дом уже на экране
        browser, page = await open_map(pw, server, city_map(where))

        house = page.locator(".zone-house.here")
        door = await house.locator(".zone-line").bounding_box()
        sign = await house.locator(".zone-sign").bounding_box()
        bar = await page.locator("#bar").bounding_box()

        assert sign["y"] > door["y"] + door["height"], "подпись налезла на вывеску"
        assert sign["y"] + sign["height"] <= bar["y"] + 0.5, (
            f"{district}: подпись ушла под панель вкладок"
        )
        await browser.close()


async def test_the_map_canvas_follows_the_picture(server):
    """Холст с домами вписывается тем же правилом, что и картинка.

    Карта показывается целиком, и на экране другого сложения по краям
    появляются поля. Считать зоны от окна значит сдвинуть все дома на
    высоту этих полей — и человек будет попадать мимо.
    """
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server)

        same = await page.evaluate(
            """() => {
                const pic = document.getElementById('map-pic');
                const svg = document.querySelector('.map-svg');
                const one = pic.getBoundingClientRect();
                const two = svg.getBoundingClientRect();
                return {
                    fit: svg.getAttribute('preserveAspectRatio'),
                    view: svg.getAttribute('viewBox'),
                    same: Math.abs(one.width - two.width) < 0.5
                        && Math.abs(one.height - two.height) < 0.5
                        && Math.abs(one.left - two.left) < 0.5
                        && Math.abs(one.top - two.top) < 0.5,
                };
            }"""
        )

        assert same["view"] == "0 0 941 1672"
        # то же правило, что и object-fit: contain у картинки
        assert same["fit"] == "xMidYMid meet"
        assert same["same"], "холст лёг не так, как картинка"
        await browser.close()


async def test_walking_starts_at_once_and_charges_like_a_battery(server):
    """Дорога начинается сразу, а её отсчёт идёт батарейкой в углу.

    Согласия не спрашиваем: дорога занимает секунды, и окно «идём?» на
    каждый шаг превращает город в анкету.
    """
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
                        "seconds_left": 20, "seconds": 20,
                        "text": "В пути до аптеки — 20 сек",
                    }),
                    "card": build_card(make_player(), TOKEN, viewer_id=42),
                }),
            )

        await page.route("**/api/travel", road)

        questions = []
        page.on("dialog", lambda dialog: questions.append(dialog.message) or
                asyncio.ensure_future(dialog.dismiss()))
        await page.locator(".zone-house").filter(has_text="Мастерская").click()
        await page.wait_for_selector("#map-road:not(.hidden)")

        assert not questions, "о дороге спросили, хотя не должны были"
        assert json.loads(asked[0]) == {"to": "workshop"}
        # секунды — числом, а пройденное — зелёными клетками батарейки
        assert await page.locator("#road-clock").inner_text() == "00:20"
        cells = page.locator("#road-cells .road-cell")
        assert await cells.count() == 12, "батарейка длиннее дюжины клеток"
        assert await page.locator("#road-cells .road-cell.on").count() == 0

        # прошла треть дороги — заполнена треть батарейки
        await page.evaluate(
            "() => { mapData.road.seconds_left = 13; paintRoad(); }"
        )
        assert await page.locator("#road-clock").inner_text() == "00:13"
        lit = await page.locator("#road-cells .road-cell.on").count()
        assert lit == 4, f"горит {lit} клеток из двенадцати"

        # батарейка стоит в правом нижнем углу, над панелью вкладок
        box = await page.locator("#map-road").bounding_box()
        frame = await page.locator(".map-frame").bounding_box()
        bar = await page.locator("#bar").bounding_box()
        assert box["x"] > frame["x"] + frame["width"] / 2
        assert box["y"] + box["height"] <= bar["y"] + 0.5
        await browser.close()


async def test_a_map_that_never_arrived_still_lets_you_walk(server):
    """Картинка района не доехала — по городу всё равно ходят.

    Двери лежат поверх рамки и считаются от неё, а не от картинки, так
    что нажимаются они и без карты. Показывать при этом битую картинку
    без единого слова нельзя: боец решит, что сломалось приложение.
    """
    async with async_playwright() as pw:
        # `images=False`: до бакета из теста не дотянуться, карта падает
        browser, page = await open_map(pw, server, city_map("pharmacy"))

        await page.wait_for_selector("#map-blank:not(.hidden)")

        said = await page.locator("#map-blank").inner_text()
        assert "Торговый квартал" in said and "не загрузилась" in said
        assert await page.locator("#map-pic.blank").count() == 1
        # А дома на месте и нажимаются
        assert await page.locator(".zone-house").count() == 2
        await browser.close()


async def test_a_map_that_arrived_says_nothing(server):
    """Карта на месте — записки нет, и картинку она не закрывает."""
    async with async_playwright() as pw:
        browser, page = await open_map(
            pw, server, city_map("pharmacy"), images=True
        )
        await page.wait_for_selector(".zone-house")

        assert await page.locator("#map-blank.hidden").count() == 1
        assert await page.locator("#map-pic:not(.blank)").count() == 1
        await browser.close()


async def test_the_arrows_lead_to_the_neighbouring_districts(server):
    """По городу ходят стрелками: вверх, вниз, влево, вправо.

    Города целиком не видно, и без стрелок шестнадцать карт остаются
    шестнадцатью картинками. Стрелка показывает только туда, куда из
    района есть ход.
    """
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server)

        # центр: вверх Северный Вал, вправо Торговый квартал, влево Старый
        # город, а со второй очередью города — ещё и вниз, в управление
        sides = await page.locator(".map-arrow").evaluate_all(
            "nodes => nodes.map(one => one.dataset.side + ':' + one.dataset.to)"
        )
        assert sorted(sides) == sorted([
            "up:northern_wall_premium", "right:clothes_pharmacy",
            "left:pawnshop_casino", "down:vcpd_hospital_district",
        ])

        # шагнули вверх — сменилась картинка и дома под ней
        before = await page.locator("#map-pic").get_attribute("src")
        await page.locator(".map-arrow.up").click()
        await page.wait_for_selector(".map-arrow.down")

        assert await page.locator("#map-pic").get_attribute("src") != before
        houses = await page.locator(".zone-house .zone-sign").all_text_contents()
        assert any("Элитный" in one or "Вал" in one for one in houses), houses
        # обратный ход с Северного Вала — вниз, в центр
        back = await page.locator(".map-arrow.down").get_attribute("data-to")
        assert back == "main_hub"
        await browser.close()


async def test_a_house_without_a_trade_says_when_it_opens(server):
    """Банк на карте есть, зайти можно, а услуги пока нет.

    Раньше банк отвечал всплывашкой, и боец оставался на карте — то есть
    внутрь не заходил вовсе. Теперь у дома свой экран: вид изнутри и
    записка о том, чего тут ждать.
    """
    walker = make_player(location="bank")
    card = build_card(walker, TOKEN, viewer_id=walker.user_id)
    async with async_playwright() as pw:
        browser, page = await open_map(
            pw, server, city_map("bank"), card, images=True
        )

        await page.locator(".zone-house").filter(has_text="Банк").click()
        await page.wait_for_selector("#house:not(.hidden)")

        assert await page.locator("#house-title").inner_text() == "Банк"
        note = await page.locator("#house-soon").inner_text()
        assert "Скоро" in note and "хранение денег" in note
        # Пока в доме стоишь, на панели горит «Карта»: оттуда и пришли
        assert "active" in (await page.locator("#tab-map").get_attribute("class"))
        # Обратно — на карту, кнопкой в углу
        await page.locator("#house-back").click()
        await page.wait_for_selector("#map:not(.hidden)")
        await browser.close()


# ---------- больница ----------


async def test_the_hospital_opens_from_the_map_with_its_price_list(server):
    """Дверь больницы ведёт на свой экран: полоса здоровья и две цены."""
    walker = make_player(location="hospital")
    card = build_card(walker, TOKEN, viewer_id=walker.user_id)
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("hospital"), card)

        await page.locator(".zone-house").filter(has_text="Больница").click()
        await page.wait_for_selector("#hospital:not(.hidden)")
        # Прайс приходит своей ручкой: экран открывается раньше, чем ответ
        await page.wait_for_selector(".cure")

        cures = page.locator(".cure")
        assert await cures.count() == 2
        first = await cures.nth(0).inner_text()
        assert "Полное выздоровление" in first and "Лечиться · 50 💰" in first
        second = await cures.nth(1).inner_text()
        assert "Перевязка" in second and "Лечиться · 25 💰" in second
        # Сколько дольют именно этому бойцу — числом на карточке
        assert "Дольют 260" in first and "Дольют 100" in second
        # Пока в доме стоишь, на панели горит «Карта»: оттуда и пришли
        assert "active" in (await page.locator("#tab-map").get_attribute("class"))
        await page.locator("#hospital-back").click()
        await page.wait_for_selector("#map:not(.hidden)")
        await browser.close()


async def test_the_hospital_shows_the_price_even_without_the_money(server):
    """Кредитов мало — кнопка серая, но цена на ней стоит.

    «У вас недостаточно кредитов» вместо числа не говорит, сколько
    копить, — то же правило, что и в мастерской.
    """
    walker = make_player(location="hospital")
    card = build_card(walker, TOKEN, viewer_id=walker.user_id)
    async with async_playwright() as pw:
        browser, page = await open_map(
            pw, server, city_map("hospital"), card,
            hospital=hospital_state(credits=30),
        )
        await page.locator(".zone-house").filter(has_text="Больница").click()
        await page.wait_for_selector("#hospital:not(.hidden)")
        # Прайс приходит своей ручкой: экран открывается раньше, чем ответ
        await page.wait_for_selector(".cure")

        buttons = page.locator(".cure .btn")
        assert "Лечиться · 50 💰" in await buttons.nth(0).inner_text()
        assert await buttons.nth(0).is_disabled(), "лечение не по карману"
        assert not await buttons.nth(1).is_disabled(), "на перевязку хватает"
        await browser.close()


async def test_a_whole_fighter_is_told_there_is_nothing_to_treat(server):
    """Целому здесь делать нечего — и обе кнопки серые."""
    walker = make_player(location="hospital")
    card = build_card(walker, TOKEN, viewer_id=walker.user_id)
    async with async_playwright() as pw:
        browser, page = await open_map(
            pw, server, city_map("hospital"), card,
            hospital=hospital_state(hp=300),
        )
        await page.locator(".zone-house").filter(has_text="Больница").click()
        await page.wait_for_selector("#hospital:not(.hidden)")
        # Прайс приходит своей ручкой: экран открывается раньше, чем ответ
        await page.wait_for_selector(".cure")

        assert "лечить нечего" in await page.locator("#hospital-note").inner_text()
        buttons = page.locator(".cure .btn")
        assert await buttons.nth(0).is_disabled()
        assert await buttons.nth(1).is_disabled()
        assert "Доливать нечего" in await page.locator(".cure").first.inner_text()
        await browser.close()


async def test_the_cheaper_cure_is_named_when_it_pours_the_same(server):
    """Царапина: полное выздоровление дольёт столько же, а стоит вдвое.

    Молча брать за то же самое вдвое — способ потерять доверие к лавке.
    """
    walker = make_player(location="hospital")
    card = build_card(walker, TOKEN, viewer_id=walker.user_id)
    async with async_playwright() as pw:
        browser, page = await open_map(
            pw, server, city_map("hospital"), card,
            hospital=hospital_state(hp=260),  # не хватает сорока из трёхсот
        )
        await page.locator(".zone-house").filter(has_text="Больница").click()
        await page.wait_for_selector("#hospital:not(.hidden)")
        # Прайс приходит своей ручкой: экран открывается раньше, чем ответ
        await page.wait_for_selector(".cure")

        first = await page.locator(".cure").nth(0).inner_text()
        assert "Столько же дольют за 25 💰" in first
        second = await page.locator(".cure").nth(1).inner_text()
        assert "Столько же" not in second, "дешёвое не должно ссылаться само на себя"
        await browser.close()


async def test_healing_pays_and_repaints_without_a_second_question(server):
    """Нажал — списали, долили и перерисовали: и карточку, и прайс."""
    walker = make_player(location="hospital")
    card = build_card(walker, TOKEN, viewer_id=walker.user_id)
    asked = []

    async def cure(route):
        asked.append(route.request.post_data)
        await route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "card": build_card(walker, TOKEN, viewer_id=42),
                "hospital": hospital_state(hp=140, credits=175),
                "done": {"title": "Перевязка", "healed": 100, "price": 25},
            }),
        )

    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("hospital"), card)
        await page.route("**/api/heal", cure)
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))

        await page.locator(".zone-house").filter(has_text="Больница").click()
        await page.wait_for_selector("#hospital:not(.hidden)")
        # Прайс приходит своей ручкой: экран открывается раньше, чем ответ
        await page.wait_for_selector(".cure")
        await page.locator(".cure .btn").nth(1).click()
        await page.wait_for_function(
            "() => document.querySelector('#shop-purse-hospital')"
            ".textContent.includes('175')",
            timeout=5000,
        )

        assert json.loads(asked[0]) == {"cure": "patch"}
        await browser.close()


async def test_the_price_list_keeps_up_with_the_healing_bar(server):
    """Здоровье затягивается само — прайс не должен от него отставать.

    Полоса тикает в самой странице, а «дольют столько-то» приходит с
    сервера. Без обновления боец через минуту читал бы вчерашнее число.
    """
    walker = make_player(location="hospital")
    card = build_card(walker, TOKEN, viewer_id=walker.user_id)
    answers = [hospital_state(hp=40), hospital_state(hp=200)]

    async def desk(route):
        await route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(answers[0] if len(answers) == 1 else answers.pop(0)),
        )

    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("hospital"), card)
        await page.route("**/api/hospital*", desk)

        await page.locator(".zone-house").filter(has_text="Больница").click()
        await page.wait_for_selector("#hospital:not(.hidden)")
        # Прайс приходит своей ручкой: экран открывается раньше, чем ответ
        await page.wait_for_selector(".cure")
        assert "Дольют 260" in await page.locator(".cure").first.inner_text()

        # Сердцебиение карточки — тем же ударом обновляется и прайс
        await page.evaluate("() => catchUp()")
        await page.wait_for_function(
            "() => document.querySelector('.cure').innerText.includes('Дольют 100')",
            timeout=5000,
        )
        await browser.close()


# ---------- вид изнутри ----------


async def open_inside(pw, server, where: str, service=Service.CLOTHES):
    """Открыть мини-апп бойцом, который стоит в этом доме."""
    player = make_player(location=where)
    browser, page = await open_page(
        pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
        build_shop(player, service), city=city_map(where), images=True,
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    return browser, page


async def interior_src(page, screen: str) -> str:
    return await page.locator(f"#{screen}-pic").get_attribute("src")


async def test_the_shop_hangs_the_view_of_the_house_you_stand_in(server):
    """Прилавков пять, экран один: картинку вешает локация, а не вёрстка."""
    async with async_playwright() as pw:
        browser, page = await open_inside(pw, server, "pharmacy", Service.POTIONS)
        await open_screen(page, "shop")

        assert await page.locator("#shop-interior:not(.hidden)").count() == 1
        assert (await interior_src(page, "shop")).endswith(
            "locations/interiors/pharmacy_interior.jpeg"
        )
        await browser.close()


async def test_the_view_hangs_above_everything_on_the_screen(server):
    """Картинка закреплена сверху: заголовок и прилавок идут под ней."""
    async with async_playwright() as pw:
        browser, page = await open_inside(pw, server, "weapon_shop", Service.WEAPONS)
        await open_screen(page, "shop")
        await page.wait_for_selector("#shop-interior:not(.hidden)")

        view = await page.locator("#shop-interior").bounding_box()
        head = await page.locator("#shop .screen-head").bounding_box()

        assert view["y"] + view["height"] <= head["y"] + 1
        # И от края до края: поля карточки картинке не мешают
        width = await page.evaluate("document.documentElement.clientWidth")
        assert view["x"] <= 0 and view["width"] >= width
        await browser.close()


async def test_the_casino_and_the_club_share_a_screen_but_not_a_view(server):
    """Один экран на два дома — и у каждого своя картинка."""
    async with async_playwright() as pw:
        browser, page = await open_inside(pw, server, "casino")
        await page.evaluate("openCasino()")

        assert (await interior_src(page, "club")).endswith(
            "locations/interiors/underground_casino_interior.jpeg"
        )

        # Вышли в клуб — и вид сменился вместе с домом
        fighter = make_player(location="fight_club")
        await page.evaluate(
            "card => render(card, true)",
            build_card(fighter, TOKEN, viewer_id=fighter.user_id),
        )
        await page.wait_for_function(
            "document.getElementById('club-pic').src.includes('fight_club')"
        )
        await browser.close()


async def test_on_the_road_there_is_no_view_at_all(server):
    """В пути боец ни в старом доме, ни в новом — показывать нечего."""
    async with async_playwright() as pw:
        browser, page = await open_inside(pw, server, "pharmacy", Service.POTIONS)
        await open_screen(page, "shop")
        await page.wait_for_selector("#shop-interior:not(.hidden)")

        walker = make_player(location="pharmacy")
        walker.set_out("clothes_shop", 20)
        await page.evaluate(
            "card => render(card, true)",
            build_card(walker, TOKEN, viewer_id=walker.user_id),
        )

        await page.wait_for_selector("#shop-interior", state="hidden")
        await browser.close()


async def test_a_view_that_never_arrived_leaves_no_empty_strip(server):
    """Файл не доехал — рамка убирается целиком, а не зияет полосой."""
    async with async_playwright() as pw:
        # `images=False`: бакет из теста недоступен, все картинки падают
        player = make_player(location="workshop")
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player, Service.CLOTHES), city=city_map("workshop"),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "workshop")

        await page.wait_for_selector("#workshop-interior", state="hidden")
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


async def test_the_info_card_says_where_the_fighter_is_walking(server):
    """Соперник в пути — в его карточке дорога, а не дом."""
    me = make_player()
    rival = make_player()
    rival.user_id = 43
    rival.nickname = "Марла"
    rival.set_out("pharmacy", 20)
    rival_card = build_card(rival, TOKEN, viewer_id=me.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(me, TOKEN, viewer_id=me.user_id),
            build_shop(me, Service.CLOTHES), club=club_of(me, rival),
        )
        await page.route(
            "**/api/card?user_id=43",
            lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(rival_card),
            ),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-club").click()
        await page.get_by_role("button", name="Игроки", exact=True).click()
        await page.locator(".fighter").nth(1).locator(".fighter-info").click()
        await page.wait_for_selector(".sheet-doll")

        line = await page.locator("#sheet-list .sheet-place").inner_text()
        assert "В пути до дома «Аптека»" in line
        await browser.close()


async def test_a_link_to_the_raid_sends_you_walking_if_you_are_not_there(server):
    """Позвали в подвал, а боец не в казино — ссылка ведёт на карту."""
    player = make_player("pharmacy")
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player, Service.POTIONS), query="?view=raid",
        )
        await page.wait_for_selector("#bar:not(.hidden)")

        assert await page.locator("#map:not(.hidden)").count() == 1
        assert await page.locator("#club").is_hidden()
        await browser.close()


@pytest.mark.parametrize(
    "state,words,shade",
    [
        ("soon", "Рейд начнётся через", "#1f6feb"),
        ("open", "Рейд закончится через", "#d29200"),
    ],
)
async def test_the_casino_counts_the_raid_down_on_the_map(
    server, state, words, shade
):
    """Под вывеской казино — плашка с отсчётом: скоро или уже идёт."""
    async with async_playwright() as pw:
        browser, page = await open_map(
            pw, server,
            city_map("casino", raid={
                "state": state, "text": words, "seconds_left": 2064,
            }),
            card=build_card(make_player("casino"), TOKEN, viewer_id=42),
        )

        plate = page.locator(".zone-plate")
        assert await plate.count() == 1, "плашка висит только у казино"
        assert state in (await plate.get_attribute("class"))
        assert await plate.locator(".plate-word").text_content() == words
        # часы идут часами: 00:34:24, а не 34:24
        assert await page.locator("#raid-clock").text_content() == "00:34:24"

        # плашка стоит под подписью дома, а не поверх неё
        sign = await page.locator(".zone-house.here .zone-sign").bounding_box()
        box = await plate.locator(".plate-box").bounding_box()
        assert box["y"] > sign["y"] + sign["height"]

        # обе строки стоят по центру бокса — и вдоль, и поперёк
        word = await plate.locator(".plate-word").bounding_box()
        clock = await page.locator("#raid-clock").bounding_box()
        middle = box["x"] + box["width"] / 2
        for line in (word, clock):
            assert abs(line["x"] + line["width"] / 2 - middle) < 1
            assert line["width"] < box["width"], "строка не помещается в плашку"
        # середина между строками — середина плашки. Считаем по центрам
        # самих строк: высота букв у слова и у часов разная, и по краям
        # они симметричными не выглядят
        pair = [line["y"] + line["height"] / 2 for line in (word, clock)]
        assert abs(sum(pair) / 2 - (box["y"] + box["height"] / 2)) < 1

        # и цвет говорит то же, что и слова
        fill = await plate.locator(".plate-box").evaluate(
            "node => getComputedStyle(node).fill"
        )
        assert fill == to_rgb(shade), fill

        # секунды тикают сами, без нового запроса
        await page.wait_for_timeout(1100)
        assert await page.locator("#raid-clock").text_content() == "00:34:23"
        await browser.close()


async def test_a_finished_raid_shows_green_without_a_clock(server):
    """Своё взял — вместо часов «Рейд завершён»: ждать больше нечего."""
    async with async_playwright() as pw:
        browser, page = await open_map(
            pw, server,
            city_map("casino", raid={
                "state": "done", "text": "Рейд завершён", "seconds_left": 0,
            }),
            card=build_card(make_player("casino"), TOKEN, viewer_id=42),
        )

        plate = page.locator(".zone-plate")
        assert "done" in (await plate.get_attribute("class"))
        assert await plate.locator(".plate-word").text_content() == "Рейд завершён"
        assert await page.locator("#raid-clock").count() == 0
        # одна строка — ровно посередине плашки
        box = await plate.locator(".plate-box").bounding_box()
        word = await plate.locator(".plate-word").bounding_box()
        assert abs(word["y"] + word["height"] / 2 - (box["y"] + box["height"] / 2)) < 1
        assert abs(word["x"] + word["width"] / 2 - (box["x"] + box["width"] / 2)) < 1
        fill = await plate.locator(".plate-box").evaluate(
            "node => getComputedStyle(node).fill"
        )
        assert fill == to_rgb("#2ea043")
        await browser.close()


async def test_without_a_window_the_map_says_nothing_about_the_raid(server):
    """Вне окна и часа перед ним плашки нет: карта — не расписание на сутки."""
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("casino"),
                                       card=build_card(make_player("casino"),
                                                       TOKEN, viewer_id=42))

        assert await page.locator(".zone-plate").count() == 0
        await browser.close()


async def test_the_analyst_speaks_above_the_buttons(server):
    """Разбор соперника стоит над кнопками хода, а не под ними."""
    ring = ring_with_duel()
    ring["duel"]["scout"] = {
        "title": "Разбор соперника: 10 боёв, 70 ходов.",
        "attack": "По статистике в первом ходу соперник реже всего блокирует "
                  "Пояс — 8%, Ноги — 14% и Голову — 22%.",
        "block": "По статистике соперник чаще всего наносит первый удар в "
                 "Голову — 45%, Ноги — 20% и Пояс — 10%.",
        "attack_tip": {"move": "Бей в Корпус",
                       "why": "он закроет его с вероятностью 25%"},
        "block_tip": {"move": "Закрывай Ноги+Голова",
                      "why": "вероятность отбить удар 56%"},
    }
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".zone-columns")

        scout = page.locator(".scout")
        assert await scout.count() == 1
        await scout.locator("summary").click()
        said = await scout.inner_text()
        assert "Аналитик" in said and "10 боёв" in said
        assert "реже всего блокирует" in said and "первый удар в Голову" in said

        # обе строки — выше кнопок выбора зоны
        panel = await scout.bounding_box()
        columns = await page.locator(".zone-columns").bounding_box()
        assert panel["y"] + panel["height"] <= columns["y"] + 0.5
        await browser.close()


async def test_each_tip_stands_over_the_buttons_it_talks_about(server):
    """Совет по удару — над ударами, совет по блоку — над блоком.

    Разбор читать между ходами успевает не каждый, и совет должен
    находиться там, где рука уже тянется нажимать.
    """
    ring = ring_with_duel()
    ring["duel"]["scout"] = {
        "title": "Разбор соперника: 10 боёв, 70 ходов.",
        "attack": "После таких он обычно закрывает Ноги и Голову (34%).",
        "block": "После таких он обычно бьёт в Ноги (44%).",
        "attack_tip": {"move": "Бей в Корпус",
                       "why": "он закроет его с вероятностью 25%"},
        "block_tip": {"move": "Закрывай Ноги+Голова",
                      "why": "вероятность отбить удар 56%"},
    }
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".zone-columns")

        tips = page.locator("#club-fights .zone-tip")
        assert await tips.count() == 2
        assert "Бей в Корпус" in await tips.nth(0).inner_text()
        assert "25%" in await tips.nth(0).inner_text()
        assert "Закрывай Ноги+Голова" in await tips.nth(1).inner_text()

        # Совет по удару стоит над столбцом удара, по блоку — над блоком:
        # сверяем не порядок в разметке, а то, где они на экране
        strike = await tips.nth(0).bounding_box()
        guard = await tips.nth(1).bounding_box()
        heads = page.locator("#club-fights .zone-head")
        attack_head = await heads.nth(0).bounding_box()
        block_head = await heads.last.bounding_box()

        assert "Удар" in await heads.nth(0).inner_text()
        assert "Блок" in await heads.last.inner_text()
        # каждый совет — над своим заголовком и в его колонке
        for tip, head in ((strike, attack_head), (guard, block_head)):
            assert tip["y"] + tip["height"] <= head["y"] + 0.5, "совет не над кнопками"
            middle = head["x"] + head["width"] / 2
            assert tip["x"] - 1 <= middle <= tip["x"] + tip["width"] + 1

        # и они не налезают друг на друга: это два разных столбца
        assert strike["x"] + strike["width"] <= guard["x"] + 0.5
        await browser.close()


async def test_the_tips_wear_their_own_colour(server):
    """Совет выделен цветом: иначе он тонет в разборе и кнопках."""
    ring = ring_with_duel()
    ring["duel"]["scout"] = {
        "title": "Разбор соперника: 10 боёв, 70 ходов.",
        "attack": "После таких он обычно закрывает Ноги и Голову (34%).",
        "block": "После таких он обычно бьёт в Ноги (44%).",
        "attack_tip": {"move": "Бей в Корпус", "why": "закроет 25%"},
        "block_tip": {"move": "Закрывай Ноги+Голова", "why": "отобьёшь 56%"},
    }
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".zone-tip")

        def ink(selector):
            return page.locator(selector).first.evaluate(
                "node => getComputedStyle(node).color"
            )

        tip = await ink("#club-fights .zone-tip")
        line = await ink("#club-fights .scout-line")
        head = await ink("#club-fights .zone-head")

        assert tip != line, "совет того же цвета, что и разбор"
        assert tip != head, "совет того же цвета, что и кнопки"
        await browser.close()


async def test_without_a_tip_the_columns_stand_as_before(server):
    """Нечего советовать — и клеток совета нет: пустых мест не оставляем."""
    ring = ring_with_duel()
    ring["duel"]["scout"] = {
        "title": "Соперник новичок: разбирать пока нечего.",
        "attack": "", "block": "",
        "attack_tip": {"move": "", "why": ""},
        "block_tip": {"move": "", "why": ""},
    }
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".zone-columns")

        assert await page.locator("#club-fights .zone-tip").count() == 0
        await browser.close()


async def test_without_a_subscription_the_analyst_is_silent(server):
    """Без подписки панели нет вовсе: это платная подсказка."""
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring_with_duel(),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".zone-columns")

        assert await page.locator(".scout").count() == 0
        await browser.close()


# ---------- мастерская ----------


def workshop_state(credits: int = 9000) -> dict:
    """Мастерская, как её отдаёт сервер: и чинить есть что, и точить."""
    from bot.content.mods import MODS
    from bot.webapp.card import item_payload
    from bot.webapp.workshop import mod_payload, target_payload

    player = make_player("workshop")
    player.credits = credits
    worn = OwnedItem(item=CATALOGUE["bat"], id=7, wear=4, slot=None)
    player.gear = [worn]
    mine = {"sharpen_weapon_2": 1}
    return {
        "credits": player.credits,
        "repair": [item_payload(player, worn)],
        "shop": [
            {
                "kind": "weapon", "title": "Заточка оружия", "icon": "🗡",
                "items": [
                    mod_payload(mod, player, mine)
                    for mod in MODS if mod.kind.value == "weapon"
                ],
            }
        ],
        "mods": [
            mod_payload(mod, player, mine) for mod in MODS if mine.get(mod.code)
        ],
        "targets": [target_payload(player, worn)],
    }


async def open_workshop(pw, server, state=None):
    player = make_player("workshop")
    browser, page = await open_page(
        pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
        build_shop(player), workshop=state or workshop_state(),
    )
    await page.wait_for_selector("#hero:not(.hidden)")
    await open_screen(page, "workshop")
    await page.wait_for_selector("#workshop-tabs .chip")
    return browser, page


async def test_the_workshop_has_three_tabs(server):
    """Починка, прилавок модификаторов и мастер — три вкладки одной двери."""
    async with async_playwright() as pw:
        browser, page = await open_workshop(pw, server)

        tabs = await page.locator("#workshop-tabs .chip").all_inner_texts()
        assert tabs == ["🔧 Ремонт", "🛒 Модификаторы", "✨ Мастер"]

        # открыт ремонт: снятая вещь и ровно одна кнопка — «Чинить».
        # Ни надеть, ни продать у мастера нельзя: сюда приходят чиниться
        assert await page.locator("#workshop-repair:not(.hidden)").count() == 1
        repair = page.locator("#repair-list .thing").first
        assert "Бита" in await repair.inner_text()
        buttons = await repair.locator("button").all_inner_texts()
        assert len(buttons) == 1 and buttons[0].startswith("Чинить"), buttons
        await browser.close()


async def test_the_repair_button_left_the_bag(server):
    """В рюкзаке кнопки починки больше нет: чинят у мастера."""
    player = make_player()
    player.gear = [OwnedItem(item=CATALOGUE["bat"], id=7, wear=5, slot=None)]
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-bag").click()
        await page.wait_for_selector("#bag-list .thing")

        buttons = await page.locator("#bag-list .thing button").all_inner_texts()
        assert not [one for one in buttons if "Чинить" in one]
        await browser.close()


async def test_the_rival_sees_the_ring_on_a_modified_thing(server):
    """Обводка ступени горит на надетой вещи — и в чужой карточке тоже.

    В этом и смысл переноса метки с прилавка на вещь: соперник, открывший
    карточку перед боем, должен видеть, что оружие не простое, а какое —
    сказать по цвету.
    """
    rival = make_player()
    rival.gear[0].modify("sharpen_weapon_4", 12)
    card = build_card(rival, TOKEN, viewer_id=999)  # смотрит соперник

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, query="?user_id=42")
        await page.wait_for_selector("#hero:not(.hidden)")

        # кукла на странице нарисована дважды — в шапке и ниже; обводка
        # ступени стоит в обеих
        marked = page.locator("#hero-slots-left .slot.tier")
        assert await marked.count() == 1
        assert "lvl4" in await marked.get_attribute("class"), "цвет не той ступени"
        assert await page.locator(".slot.tier").count() == 2
        # обводка нарисована поверх картинки и внутри рамки клетки
        ring = await marked.evaluate(
            "box => getComputedStyle(box, '::after').borderTopColor"
        )
        assert ring == "rgb(164, 77, 214)", ring
        # и подсказка клетки говорит, что именно дала модификация
        hint = await marked.get_attribute("title")
        assert "Мастерская заточка оружия" in hint and "урон +12" in hint
        await browser.close()


async def test_the_counter_sells_five_steps(server):
    """Прилавок: пять ступеней со своей полосой и ценой.

    Точки ступени на прилавке нет: она принадлежит вещи, а не заточке.
    Ступень здесь называют словом («Простая», «Элитная») и показывают
    цветом кромки карточки.
    """
    async with async_playwright() as pw:
        browser, page = await open_workshop(pw, server)
        await page.locator("#workshop-tabs .chip").nth(1).click()

        rows = page.locator("#mods-list .mod")
        assert await rows.count() == 5
        first = await rows.first.inner_text()
        assert "Простая заточка оружия" in first and "500" in first
        assert "урон +1…+5" in first
        assert await page.locator("#mods-list .mod-star").count() == 0
        # ступень различима кромкой: у каждой карточки свой класс
        assert [
            await row.get_attribute("class") for row in await rows.all()
        ] == ["mod lvl1", "mod lvl2", "mod lvl3", "mod lvl4", "mod lvl5"]
        await browser.close()


async def test_the_counter_keeps_the_price_on_the_button_when_money_is_short(
    server,
):
    """Кнопка называет цену всегда, а пустой кошелёк показывает серым.

    Раньше вместо цены на ней стояло «Не хватает кредитов», и прилавок
    переставал отвечать на единственный вопрос, ради которого на него
    смотрят: сколько это стоит.
    """
    async with async_playwright() as pw:
        browser, page = await open_workshop(pw, server, workshop_state(credits=1))
        await page.locator("#workshop-tabs .chip").nth(1).click()

        buy = page.locator("#mods-list .mod").first.locator(".btn")

        assert await buy.inner_text() == "Купить · 500 💰"
        assert await buy.is_disabled()
        assert "Не хватает" not in await page.locator("#mods-list").inner_text()
        # Серая — та же кромка, что у всех недоступных кнопок клуба
        grey = await buy.evaluate("box => getComputedStyle(box).backgroundColor")
        lit = await page.locator("#workshop-tabs .chip").first.evaluate(
            "box => getComputedStyle(box).backgroundColor"
        )
        assert grey != lit
        await browser.close()


async def test_the_master_needs_both_slots(server):
    """Кнопка молчит, пока в слотах не окажутся и вещь, и модификатор."""
    async with async_playwright() as pw:
        browser, page = await open_workshop(pw, server)
        await page.locator("#workshop-tabs .chip").nth(2).click()

        go = page.locator("#master-go")
        assert await go.is_disabled()

        # кладём вещь. В выборе карточка — сама кнопка, и других на ней
        # нет: «Надеть» или «Сдать» здесь означали бы промах мимо выбора
        await page.locator("#master-item").click()
        pick = page.locator("#master-picker .thing").first
        assert await pick.locator("button").count() == 0
        await pick.click()
        assert await go.is_disabled(), "одной вещи мало"

        # и модификатор
        await page.locator("#master-mod").click()
        await page.locator("#master-picker .mod").first.click()

        assert await go.is_enabled()
        assert "Бита" in await page.locator("#master-item").inner_text()
        assert "заточка" in (await page.locator("#master-mod").inner_text()).lower()
        await browser.close()


async def test_the_counter_shows_the_art_of_every_modifier(server):
    """У каждого модификатора на прилавке своя картинка и звёздочка на ней.

    Картинки в тестах режет маршрут, и вместо не доехавшего файла страница
    честно оставляет значок вида — проверяем и это: прилавок не должен
    рассыпаться, если одна картинка не открылась.
    """
    async with async_playwright() as pw:
        browser, page = await open_workshop(pw, server)
        await page.locator("#workshop-tabs .chip").nth(1).click()

        pics = page.locator("#workshop-shop .mod .mod-pic")
        assert await pics.count() == 5, "пять ступеней заточки оружия"
        first = pics.first
        assert "🗡" in await first.inner_text(), "картинка не доехала — виден значок"
        box = await first.bounding_box()
        assert box["width"] >= 96, f"картинка мелковата: {box['width']}"
        await browser.close()


async def test_a_repaired_thing_leaves_the_bench_at_once(server):
    """Починили — вещь уходит из списка сразу, а не после переоткрытия двери.

    Список ремонта приходит тем же ответом, что и починка: целой вещи на
    вкладке делать нечего, и ждать, пока игрок сам закроет и откроет
    мастерскую, чтобы это увидеть, он не должен.
    """
    state = workshop_state()
    async with async_playwright() as pw:
        browser, page = await open_workshop(pw, server, state)

        await page.route("**/api/repair", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "card": build_card(make_player("workshop"), TOKEN, viewer_id=42),
                "workshop": {**state, "repair": []},
                "repair": {
                    "points": 4, "price": 4, "degraded": False, "destroyed": False,
                },
            }),
        ))
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))

        assert await page.locator("#repair-list .thing").count() == 1
        await page.locator("#repair-list button").first.click()

        await page.wait_for_selector("#repair-list .thing", state="detached")
        assert "Чинить нечего" in await page.locator("#repair-note").inner_text()
        await browser.close()


async def test_the_master_burns_and_leaves_a_star(server):
    """Нажали «Модифицировать» — слоты сошлись, вспыхнуло, звёздочка осталась."""
    state = workshop_state()
    async with async_playwright() as pw:
        browser, page = await open_workshop(pw, server, state)

        done = dict(state)
        done_item = json.loads(json.dumps(state["targets"][0]))
        done_item["mod"] = {
            "code": "sharpen_weapon_2", "title": "Улучшенная заточка оружия",
            "level": 2, "star": "🟡", "gain": "урон +5",
        }
        done = {**state, "targets": [], "repair": [done_item], "mods": []}
        await page.route("**/api/mod", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "card": build_card(make_player("workshop"), TOKEN, viewer_id=42),
                "workshop": done,
                "done": {
                    "title": "Бита", "mod": "Улучшенная заточка оружия",
                    "level": 2, "star": "🟡", "value": 5, "gain": "урон +5",
                    "item_id": 7,
                },
            }),
        ))
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))

        await page.locator("#workshop-tabs .chip").nth(2).click()
        await page.locator("#master-item").click()
        await page.locator("#master-picker .thing").first.click()
        await page.locator("#master-mod").click()
        await page.locator("#master-picker .mod").first.click()
        await page.locator("#master-go").click()

        # слоты сходятся и гремит взрыв
        await page.wait_for_selector(".master.going")
        await page.wait_for_selector(".master.boom")
        await page.wait_for_selector(".master:not(.going)")

        # на вещи осталась метка своей ступени: точка у названия и
        # обводка того же цвета вокруг картинки
        await page.locator("#workshop-tabs .chip").first.click()
        star = page.locator("#repair-list .thing-star").first
        assert await star.inner_text() == "🟡"
        pic = page.locator("#repair-list .thing-pic").first
        assert "lvl2" in await pic.get_attribute("class")
        await browser.close()


async def test_the_bag_shows_what_the_master_added(server):
    """В рюкзаке рядом с итогом стоит прибавка мастера: «12–16 (+5)».

    Число в строке уже посчитано с модификацией, и без подписи заточенную
    биту не отличить от той, что такой и продавалась.
    """
    from bot.webapp.card import item_payload

    player = make_player()
    bat = OwnedItem(item=CATALOGUE["bat"], id=7, wear=0, slot=None)
    bat.modify("sharpen_weapon_2", 5)
    player.gear = [bat]
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    assert item_payload(player, bat)["bonuses"][0]["plus"] == "+5"

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        damage = page.locator("#bag-list .thing-gain li").first
        said = await damage.inner_text()
        # сначала итог, потом прибавка мастера, и только потом — что из
        # этого выйдет в руках класса: три числа, и каждое о своём
        assert said == "👊 Урон: " + bat.real.describe_damage() + " (+5) (у воина 11–14)"
        assert await damage.locator(".gain-plus").inner_text() == " (+5)"
        await browser.close()


# ---------- клуб и казино делят экран ----------


async def test_the_casino_opens_only_by_its_own_door(server):
    """Казино открывает дверь на карте, а не вкладка внизу.

    Вкладка «Клуб» — всегда клуб: боец, стоящий в казино, приходит по ней
    в клуб, и подвал за ней не прячется.
    """
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("casino"),
                                       card=build_card(make_player("casino"),
                                                       TOKEN, viewer_id=42))
        # кнопка внизу зовётся «Клуб» и ведёт в клуб — даже отсюда
        assert await page.locator("#tab-club .bar-label").inner_text() == "Клуб"
        await page.locator("#tab-club").click()
        await page.wait_for_selector("#club:not(.hidden)")

        assert await page.locator("#club-title").inner_text() == "🥊 Бойцовский клуб"
        assert await page.locator("#club-raid").is_hidden(), "подвал открылся сам"

        # а дверь казино — открывает, и в нём одно дело: рейд
        await page.locator("#tab-map").click()
        await page.locator(".zone-house").filter(has_text="Казино").click()
        await page.wait_for_selector("#club-raid:not(.hidden)")

        assert await page.locator("#club-title").inner_text() == "🎲 Казино"
        assert await page.locator("#club-sections .chip").count() == 0
        assert await page.locator("#tab-club .bar-label").inner_text() == "Клуб"
        assert await page.locator("#club-fights").is_hidden()
        await browser.close()


async def test_the_club_keeps_all_its_sections_away_from_the_ring(server):
    """Разделы клуба видны везде, но драться зовут туда, где дерутся."""
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(make_player("pharmacy"), TOKEN, viewer_id=42),
            build_shop(make_player("pharmacy"), Service.POTIONS),
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-club").click()
        await page.wait_for_selector("#club:not(.hidden)")

        chips = await page.locator("#club-sections .chip").all_inner_texts()
        assert chips == ["Бои", "Отряд", "Игроки", "Статистика"]
        # и стоят они по центру экрана, а не прижаты к левому краю
        box = await page.locator("#club-sections").bounding_box()
        first = await page.locator("#club-sections .chip").first.bounding_box()
        last = await page.locator("#club-sections .chip").last.bounding_box()
        left = first["x"] - box["x"]
        right = box["x"] + box["width"] - (last["x"] + last["width"])
        assert abs(left - right) < 2, f"слева {left:.0f}, справа {right:.0f}"

        note = "Бои между игроками недоступны в данной локации."
        assert await page.locator("#club-fights .club-locked").inner_text() == note
        await page.locator("#club-sections .chip").nth(1).click()
        assert await page.locator("#club-battle .club-locked").inner_text() == note
        # звать драться отсюда нечем: кнопок ринга здесь нет
        assert await page.locator("#club-battle button").count() == 0
        await browser.close()


async def test_leaving_the_casino_leaves_the_raid_behind(server):
    """Ушёл в клуб — раздел рейда не едет следом.

    Он оставался открытым и после перехода: рейд из него всё равно не
    начать, а выглядело так, будто подвал доступен откуда угодно.
    """
    async with async_playwright() as pw:
        browser, page = await open_map(pw, server, city_map("casino"),
                                       card=build_card(make_player("casino"),
                                                       TOKEN, viewer_id=42))
        await page.locator(".zone-house").filter(has_text="Казино").click()
        await page.wait_for_selector("#club-raid:not(.hidden)")

        # боец дошёл до клуба — карточка перерисовалась
        await page.evaluate(
            "card => render(card, true)",
            build_card(make_player("fight_club"), TOKEN, viewer_id=42),
        )
        await page.locator("#tab-club").click()

        # ждём именно раздел, а не его наполнение: список боёв приезжает
        # отдельным запросом и до него секция пуста
        await page.wait_for_selector("#club-fights:not(.hidden)")
        assert await page.locator("#club-raid").is_hidden(), "рейд уехал следом"
        assert await page.locator("#club-title").inner_text() == "🥊 Бойцовский клуб"
        await browser.close()


# ---------- приёмы ----------


def tricks_state(energy: int = 9, count: int = 4, left: int = 3) -> dict:
    """Шкала и приёмы так, как их отдаёт сервер."""
    return {
        "energy": energy,
        "max": 20,
        "source": "+3 за точный удар",
        "left": left,
        "per_turn": 3,
        "tricks": [
            {"code": "strong_hit", "title": "Сильный удар", "icon": "👊",
             "image": "https://example.test/items/strong_hit.jpeg",
             "note": "+15 урона.", "cost": 3, "ready": energy >= 3, "armed": False},
            {"code": "power_hit", "title": "Мощный удар", "icon": "👊",
             "image": "https://example.test/items/power_hit.jpeg",
             "note": "+30 урона.", "cost": 6, "ready": energy >= 6, "armed": False},
            {"code": "crushing_hit", "title": "Сокрушительный удар", "icon": "👊",
             "image": "https://example.test/items/crushing_hit.jpeg",
             "note": "+45 урона.", "cost": 9, "ready": energy >= 9, "armed": True},
            {"code": "mass_hit", "title": "Массовый удар", "icon": "💢",
             "image": "https://example.test/items/mass_hit.jpeg",
             "note": "+60 урона.", "cost": 12, "ready": energy >= 12, "armed": False},
        ][:count],
    }


async def test_the_tricks_stand_above_the_turn_buttons(server):
    """Приёмы жмут до удара и блока — и стоят на экране выше их."""
    ring = ring_with_duel()
    ring["duel"]["abilities"] = tricks_state()
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".zone-columns")

        tricks = page.locator(".tricks")
        assert await tricks.count() == 1
        assert await tricks.locator(".trick").count() == 4

        panel = await tricks.bounding_box()
        columns = await page.locator(".zone-columns").bounding_box()
        assert panel["y"] + panel["height"] <= columns["y"] + 0.5
        await browser.close()


async def test_a_trick_is_grey_until_the_bar_fills(server):
    """Не хватает энергии — приём чёрно-белый и не нажимается."""
    ring = ring_with_duel()
    ring["duel"]["abilities"] = tricks_state(energy=4)
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".trick")

        tricks = page.locator(".trick")
        # первый по карману, остальные — нет
        assert "cold" not in await tricks.nth(0).get_attribute("class")
        assert await tricks.nth(0).is_enabled()
        assert "cold" in await tricks.nth(1).get_attribute("class")
        assert await tricks.nth(1).is_disabled()

        # серым приём делает именно фильтр, а не просто прозрачность
        grey = await tricks.nth(1).locator(".trick-pic").evaluate(
            "box => getComputedStyle(box).filter"
        )
        assert "grayscale" in grey
        colour = await tricks.nth(0).locator(".trick-pic").evaluate(
            "box => getComputedStyle(box).filter"
        )
        assert colour == "none", "доступный приём должен быть цветным"
        await browser.close()


async def test_a_pressed_trick_shows_it_is_waiting(server):
    """Нажатая заготовка светится: она ждёт своего момента, а не пропала."""
    ring = ring_with_duel()
    ring["duel"]["abilities"] = tricks_state()
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".trick")

        armed = page.locator(".trick.armed")
        assert await armed.count() == 1
        # Про заготовку говорит обводка, а не подпись: цена остаётся ценой
        assert "наготове" not in await armed.inner_text()
        assert "9 ⚡" in await armed.inner_text()
        assert await armed.is_disabled(), "дважды одну заготовку не кладут"
        await browser.close()


async def test_the_energy_bar_shows_what_it_counts(server):
    ring = ring_with_duel()
    ring["duel"]["abilities"] = tricks_state(energy=9)
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".energy")

        assert "9 / 20" in await page.locator(".energy-label").inner_text()
        said = await page.locator(".energy-note").inner_text()
        assert "+3 за точный удар" in said, "ставка должна стоять числом"
        assert "осталось приёмов: 3" in said
        # полоса налита ровно на долю накопленного
        width = await page.locator(".energy-fill").evaluate(
            "fill => fill.style.width"
        )
        assert width == "45%"
        await browser.close()


async def test_the_tricks_live_on_the_character_screen(server):
    """Приёмы — про бойца, а не про поклажу: их место в персонаже.

    В инвентаре их видел только хозяин карточки — чужой рюкзак не
    показывают вовсе, — а приёмы соперника стоит знать до боя.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["abilities"] = {
        "known": [
            {"code": "strong_hit", "title": "Сильный удар", "icon": "👊",
             "image": "", "note": "+15 урона.", "tier": 1, "cost": 3},
        ],
        "slots": 4, "choice": None, "next_tier": 3,
    }
    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#hero #skills-box").count() == 1
        assert await page.locator("#bag #skills-box").count() == 0
        # И видно их на самом экране персонажа, а не только в разметке
        assert await page.locator("#skills-box").is_visible()
        await browser.close()


async def test_the_card_lists_what_the_fighter_knows(server):
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["abilities"] = {
        "known": [
            {"code": "strong_hit", "title": "Сильный удар", "icon": "👊",
             "image": "https://example.test/items/strong_hit.jpeg",
             "note": "+15 урона.", "tier": 1, "cost": 3},
        ],
        "slots": 4, "choice": None, "next_tier": 3,
    }
    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        box = page.locator("#skills-box")
        assert await box.locator(".skill").count() == 1
        assert "1 из 4" in await page.locator("#skills-count").inner_text()
        assert "на 3 уровне" in await page.locator("#skills-note").inner_text()
        await browser.close()


async def test_the_fork_is_impossible_to_miss(server):
    """Дорос до ступени — развилка стоит в карточке и предупреждает."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["abilities"] = {
        "known": [], "slots": 4, "next_tier": 6,
        "choice": {
            "tier": 3,
            "options": [
                {"code": "power_hit", "title": "Мощный удар", "icon": "👊",
                 "image": "https://example.test/items/power_hit.jpeg",
                 "note": "+30 урона.", "cost": 6, "own": True},
                {"code": "nimble", "title": "Проворность", "icon": "🌀",
                 "image": "https://example.test/items/nimble.jpeg",
                 "note": "Уворот наверняка.", "cost": 6, "own": False},
                {"code": "crit_hit", "title": "Критический удар", "icon": "💥",
                 "image": "https://example.test/items/crit_hit.jpeg",
                 "note": "Крит без проверки.", "cost": 6, "own": False},
            ],
        },
    }
    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        fork = page.locator(".fork")
        assert await fork.count() == 1
        said = await fork.inner_text()
        assert "3 уровень" in said and "навсегда" in said
        assert await fork.locator(".skill").count() == 3
        # классовый приём помечен
        assert await fork.locator(".fork-own").inner_text() == "свой"
        await browser.close()


# ---------- награда за вход ----------


def daily_state(days=3, waiting=True, fresh=True, month="2026-09") -> dict:
    """Окно входа так, как его отдаёт сервер.

    Собираем настоящим `daily_payload`, а не руками: календарь считается
    по месяцу, и выдуманная лестница из трёх строк молча разошлась бы с
    тем, что видит игрок.
    """
    from bot.content.daily import next_milestone, unclaimed
    from bot.daily_service import VisitState
    from bot.webapp.server import daily_payload

    return daily_payload(
        VisitState(
            days=days,
            month=month,
            fresh=fresh,
            waiting=unclaimed(days, 0 if waiting else days, month),
            next_day=next_milestone(days, month),
            resets_at=0,
        )
    )


async def test_the_daily_window_pops_up_on_the_first_look(server):
    """Первый за сутки вход — и окно само встаёт поверх карточки."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state()

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        veil = page.locator("#daily-veil")
        assert await veil.is_visible()
        said = await veil.inner_text()
        assert "день 3" in said and "Забирайте" in said
        # клетка на каждый день сентября, и ни одной лишней
        assert await veil.locator(".gift").count() == 30
        await browser.close()


async def test_the_calendar_stands_seven_cells_to_a_row(server):
    """Семь в ряд — и ряды не наползают друг на друга.

    Проверяем геометрией, а не классами: имя `.step` однажды уже было
    занято кнопками прокачки, клетки унаследовали чужой размер и вёрстка
    рассыпалась — на классах такое не видно.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state()

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        cells = page.locator("#daily-ladder .gift")
        boxes = [await cells.nth(i).bounding_box() for i in range(await cells.count())]

        # первый ряд — ровно семь клеток на одной высоте
        top = boxes[0]["y"]
        first_row = [one for one in boxes if abs(one["y"] - top) < 1]
        assert len(first_row) == 7, "в ряду должно стоять семь клеток"
        # восьмая ушла на следующую строку и не налезла на первую
        assert boxes[7]["y"] >= top + boxes[0]["height"] - 0.5

        # клетка квадратная и не схлопнулась
        assert boxes[0]["width"] > 20
        assert abs(boxes[0]["width"] - boxes[0]["height"]) < 3
        # и в строку клетки не вылезают за окно
        box = await page.locator("#daily-box").bounding_box()
        assert first_row[-1]["x"] + first_row[-1]["width"] <= box["x"] + box["width"] + 1
        await browser.close()


async def test_a_short_month_gets_a_short_calendar(server):
    """В феврале клеток двадцать восемь — календарь считает по месяцу."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state(month="2026-02")

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        assert await page.locator("#daily-ladder .gift").count() == 28
        await browser.close()


async def test_the_border_says_what_to_do_with_the_day(server):
    """Кромка отвечает на один вопрос: что с этим днём делать.

    Зелёная — забрано. Синяя — вот оно, забирайте. Серая — ещё расти. И
    больше кромку не красит ничто: веха, до которой не дошли, обязана
    оставаться серой, иначе она обещает то, чего не даёт.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    # первые два дня забраны, третий — сегодняшний, ждёт в руках
    card["daily"] = daily_state(days=3, waiting=True)
    card["daily"]["ladder"][0].update(done=True, ready=False)
    card["daily"]["ladder"][1].update(done=True, ready=False)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        cells = page.locator("#daily-ladder .gift")

        def edge(index):
            return cells.nth(index).evaluate(
                "node => getComputedStyle(node).borderTopColor"
            )

        def hue(colour):
            return [int(one) for one in re.findall(r"\d+", colour)[:3]]

        taken = hue(await edge(0))
        assert taken[1] > taken[0] and taken[1] > taken[2], f"забранное не зелёное: {taken}"

        ours = hue(await edge(2))
        assert ours[2] > ours[0] and ours[2] > ours[1], f"сегодняшнее не синее: {ours}"

        # веха двадцать первого дня — впереди, и кромка у неё та же, что у
        # соседнего рейд-пасса: серая
        assert "big" in await cells.nth(20).get_attribute("class")
        assert await edge(20) == await edge(9), "веха впереди красится не как будни"
        grey = hue(await edge(20))
        assert max(grey) - min(grey) < 30, f"предстоящее не серое: {grey}"

        # галочка стоит только на забранном и остаётся зелёной
        assert await cells.nth(0).locator(".gift-mark").inner_text() == "✔"
        assert await cells.nth(2).locator(".gift-mark").count() == 0
        mark = hue(
            await cells.nth(0).locator(".gift-mark").evaluate(
                "node => getComputedStyle(node).backgroundColor"
            )
        )
        assert mark[1] > mark[0] and mark[1] > mark[2], f"галочка не зелёная: {mark}"
        await browser.close()


async def test_the_day_taken_today_turns_green_too(server):
    """Сегодняшняя забранная — такая же зелёная, как вчерашние.

    Пока награда в руках, клетка синяя; забрали — и она встаёт в общий
    зелёный ряд, а не остаётся выделенной до завтра.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state(days=3, waiting=False)  # всё забрано, включая сегодня

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        cells = page.locator("#daily-ladder .gift")
        today = cells.nth(2)
        assert "done" in await today.get_attribute("class")
        assert "ready" not in await today.get_attribute("class")

        colour = await today.evaluate("node => getComputedStyle(node).borderTopColor")
        red, green, blue = [int(one) for one in re.findall(r"\d+", colour)[:3]]
        assert green > red and green > blue, f"сегодняшняя забранная не зелёная: {colour}"
        # и ни одна клетка не осталась синей: забирать нечего
        assert await page.locator("#daily-ladder .gift.ready").count() == 0
        await browser.close()


async def test_a_cell_tells_what_lies_in_it(server):
    """Тридцать значков сами за себя не скажут — клетка отвечает на нажатие."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state()

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        # второй день — будни: там лежит рейд-пасс
        await page.locator("#daily-ladder .gift").nth(1).click()
        said = await page.locator("#daily-pick").inner_text()
        assert "2-й день" in said and "Рейд-пасс" in said

        # последний день месяца — заточка
        await page.locator("#daily-ladder .gift").nth(29).click()
        said = await page.locator("#daily-pick").inner_text()
        assert "30-й день" in said and "заточка" in said.lower()
        await browser.close()


async def test_the_bag_shows_the_pass_wear_and_not_undefined(server):
    """У пропуска в рюкзаке стоит «Износ: 0/1», а у склянки строки нет.

    Пропуск рисует та же карточка, что и оружие, и она печатает износ
    всему, что не пьётся. Ключей износа у склянок не было вовсе — и в
    рюкзаке у талона стояло «Износ: undefined».
    """
    from bot.game.potions import RAID_PASS

    player = make_player()
    player.potions = {RAID_PASS: 2, "heal_small": 1}
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-bag").click()
        await page.wait_for_selector("#bag:not(.hidden)")

        shelf = page.locator("#potion-list .thing")
        said = await shelf.first.inner_text()
        assert "Износ: 0/1" in said
        assert "undefined" not in (await page.locator("#potion-list").inner_text())
        assert "один рейд" in said, "не сказано, на сколько талона хватает"
        # и сколько талонов на руках: износ «0/1» иначе говорил бы, что он один
        assert "В рюкзаке: 2 шт." in said
        # и талон не грозит рассыпаться: он отрабатывает своё, а не ветшает
        assert "рассыплется" not in said

        # у склянки полосы износа нет вовсе
        assert await shelf.nth(1).locator(".thing-wear").count() == 0
        await browser.close()


async def test_a_gift_cell_shows_the_thing_itself(server):
    """Где лежит вещь — там её картинка, а где кредиты — значок.

    «🧪» на все склянки разом не говорит, какая именно ждёт. Картинка
    берётся от кода вещи — та же, что потом окажется в рюкзаке.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state()

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), images=True
        )
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        cells = page.locator("#daily-ladder .gift")
        # первый день — кредиты: вещи нет, стоит значок
        assert await cells.nth(0).locator(".gift-pic").count() == 0
        assert await cells.nth(0).locator(".gift-icon").inner_text() == "💰"

        # третий — эликсир восстановления: тут картинка вместо значка
        potion = cells.nth(2)
        assert "has-pic" in await potion.get_attribute("class")
        assert "potions/heal_small" in await potion.locator(".gift-pic").get_attribute(
            "src"
        )
        assert await potion.locator(".gift-icon").is_hidden(), "значок под картинкой"

        # последний день — заточка, и она из другой папки
        stone = cells.nth(29)
        assert "items/sharpen_weapon_1" in await stone.locator(
            ".gift-pic"
        ).get_attribute("src")
        await browser.close()


async def test_every_cell_looks_the_same_whatever_lies_in_it(server):
    """Клетка с мешком денег и клетка с вещью — одной масти.

    Фон у картинок инвентаря залит одним цветом, и клетка с кредитами
    красится им же. Номер дня у всех в левом верхнем углу: иначе в ряду
    мешок сидел бы по центру, а склянка — в углу, и ряд разъезжался бы.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state()

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), images=True
        )
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        cells = page.locator("#daily-ladder .gift")
        money = cells.nth(0)  # первый день — кредиты
        thing = cells.nth(2)  # третий — склянка
        assert await money.locator(".gift-pic").count() == 0
        assert await thing.locator(".gift-pic").count() == 1

        # Фон — тот самый, что залит в картинках инвентаря
        back = await money.evaluate("node => getComputedStyle(node).backgroundColor")
        assert back == "rgb(96, 101, 107)", f"фон клетки с кредитами чужой: {back}"
        assert back == await thing.evaluate(
            "node => getComputedStyle(node).backgroundColor"
        )

        # Номер дня у обеих — в левом верхнем углу, на одной высоте от края
        def corner(cell):
            return cell.evaluate(
                "node => {"
                "  const box = node.getBoundingClientRect();"
                "  const day = node.querySelector('.gift-day').getBoundingClientRect();"
                "  return [Math.round(day.x - box.x), Math.round(day.y - box.y)];"
                "}"
            )

        assert await corner(money) == await corner(thing)
        # и это действительно угол, а не середина
        left, top = await corner(money)
        box = await money.bounding_box()
        assert left < box["width"] / 3 and top < box["height"] / 3
        await browser.close()


async def test_the_picture_fills_the_cell_and_leaves_the_marks_visible(server):
    """Картинка занимает клетку целиком, но день и галочку не прячет."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state(days=3, waiting=True)
    card["daily"]["ladder"][2].update(done=True, ready=False)

    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, card, build_shop(player), images=True
        )
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        cell = page.locator("#daily-ladder .gift").nth(2)
        box = await cell.bounding_box()
        pic = await cell.locator(".gift-pic").bounding_box()

        # картинка кроет клетку, а не жмётся значком в середине
        assert pic["width"] >= box["width"] - 3
        assert pic["height"] >= box["height"] - 3

        # номер дня читается поверх неё, а не спрятан под ней
        assert await cell.locator(".gift-day").is_visible()
        day = await cell.locator(".gift-day").bounding_box()
        assert day["x"] >= box["x"] - 1 and day["y"] >= box["y"] - 1

        # и зелёная галочка забранного дня не срезана краем клетки
        mark = cell.locator(".gift-mark")
        assert await mark.is_visible(), "галочку съела клетка с картинкой"
        spot = await mark.bounding_box()
        assert spot["width"] > 5 and spot["height"] > 5
        await browser.close()


async def test_a_missing_picture_falls_back_to_the_icon(server):
    """Не доехал файл — в клетке остаётся значок, а не дыра."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state()
    for step in card["daily"]["ladder"]:
        if step["image"]:
            step["image"] = "https://example.invalid/нет-такого.jpeg"

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        cell = page.locator("#daily-ladder .gift").nth(2)
        # ждём, пока картинка сдастся и клетка вернётся к значку
        await page.wait_for_selector("#daily-ladder .gift:nth-child(3):not(.has-pic)")

        assert await cell.locator(".gift-pic").count() == 0
        assert await cell.locator(".gift-icon").is_visible()
        assert await cell.locator(".gift-icon").inner_text() == "🧪"
        await browser.close()


async def test_the_calendar_button_is_dressed_like_the_panel(server):
    """Кнопка одета как таблица под ней: тот же фон, кромка и цвет текста.

    Синяя кнопка посреди спокойной карточки читается как чужая, поэтому
    сверяем не класс, а посчитанные браузером цвета — они и решают.
    """
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state(days=2, waiting=False, fresh=False)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        def looks(selector):
            return page.locator(selector).evaluate(
                "node => {"
                "  const style = getComputedStyle(node);"
                "  return {"
                "    back: style.backgroundColor,"
                "    ink: style.color,"
                "    edge: style.borderTopColor,"
                "    width: style.borderTopWidth,"
                "    round: style.borderTopLeftRadius,"
                "  };"
                "}"
            )

        gate = await looks("#hero-daily")
        panel = await looks("#hero .panel")

        assert gate == panel, f"кнопка выбивается из карточки: {gate} против {panel}"
        await browser.close()


async def test_the_hero_tab_opens_the_calendar_on_demand(server):
    """Кнопка «Ежедневные награды» открывает окно, когда игрок сам захочет."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    # окно само не всплывает: день засчитан, забирать нечего
    card["daily"] = daily_state(days=2, waiting=False, fresh=False)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")
        assert await page.locator("#daily-veil").is_hidden()

        gate = page.locator("#hero-daily")
        assert await gate.is_visible()
        assert "Ежедневные награды" in await gate.inner_text()

        await gate.click()

        await page.wait_for_selector("#daily-veil:not(.hidden)")
        assert await page.locator("#daily-ladder .gift").count() == 30
        await browser.close()


async def test_nothing_to_take_means_no_claim_button(server):
    """Пустой день окно показывает, но забирать не предлагает."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state(days=2, waiting=False)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")

        buttons = await page.locator("#daily-buttons button").all_inner_texts()
        assert [one.strip() for one in buttons] == ["Закрыть"]
        assert "Сегодня награда получена. Приходите завтра." in await page.locator(
            "#daily-note"
        ).inner_text()
        await browser.close()


async def test_an_unclaimed_gift_keeps_the_window_coming_back(server):
    """Не забрал — окно всплывёт снова: невзятое не прячут."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    # день уже засчитан раньше (fresh=False), но награда так и ждёт
    card["daily"] = daily_state(fresh=False)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#daily-veil").is_visible()
        await browser.close()


async def test_a_quiet_day_does_not_nag(server):
    """День засчитан, забирать нечего — окно больше не лезет."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state(days=2, waiting=False, fresh=False)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#daily-veil").is_hidden()
        await browser.close()


async def test_taking_the_gift_closes_the_window(server):
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    card["daily"] = daily_state()
    done = json.loads(json.dumps(card))
    done["daily"] = daily_state(waiting=False, fresh=False)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#daily-veil:not(.hidden)")
        await page.route("**/api/daily", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "card": done,
                "done": {
                    "credits": 0, "potions": ["heal_small"],
                    "rewards": [{"title": "Эликсир восстановления",
                                 "icon": "🧪", "day": 3}],
                },
            }),
        ))
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))

        await page.locator("#daily-buttons button").first.click()

        # Ждём именно скрытия: `wait_for_selector` по умолчанию ждёт
        # видимый элемент и скрытого не дождётся никогда
        await page.wait_for_selector("#daily-veil", state="hidden")
        assert "hidden" in await page.locator("#daily-veil").get_attribute("class")
        await browser.close()


async def test_the_energy_bar_stays_while_the_squad_finishes(server):
    """Ход сделан — шкала остаётся на виду, но нажимать нечего.

    В рейде между нажатием приёма и разменом проходит вся волна. Раньше
    панель после «Вперёд» пропадала целиком, и боец так и не видел, куда
    делась энергия и сработала ли заготовка.
    """
    state = raid_with_wave({"acted": True})
    state["raid"]["abilities"] = tricks_state(energy=9)

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, state)

        said = await page.locator("#raid-body").inner_text()
        assert "Ждём остальных" in said

        panel = page.locator("#raid-body .tricks")
        assert await panel.count() == 1, "шкала пропала вместе с кнопками"
        assert "watching" in await panel.get_attribute("class")
        assert "9 / 20" in await panel.inner_text()
        # заготовка видна: по ней и понятно, за что ушла энергия
        assert await panel.locator(".trick.armed").count() == 1

        # но нажать ничего нельзя: приём жмут перед ударом, а не после
        cards = panel.locator(".trick")
        for index in range(await cards.count()):
            assert await cards.nth(index).is_disabled()
        # и обещания «осталось приёмов» тут нет — оно было бы неправдой
        assert "осталось приёмов" not in await panel.inner_text()
        await browser.close()


async def test_the_bar_is_there_between_the_waves_too(server):
    """Отряд переводит дух — шкала всё равно на виду."""
    state = raid_with_wave({"resting": True})
    state["raid"]["abilities"] = tricks_state(energy=12)

    async with async_playwright() as pw:
        browser, page = await open_raid(pw, server, state)

        assert "переводит дух" in await page.locator("#raid-body").inner_text()
        assert await page.locator("#raid-body .tricks").count() == 1
        assert "12 / 20" in await page.locator("#raid-body .tricks").inner_text()
        await browser.close()


async def test_all_four_tricks_fit_in_one_row(server):
    """Четыре приёма обязаны поместиться в строку, не перенесясь."""
    ring = ring_with_duel()
    ring["duel"]["abilities"] = tricks_state(energy=20)
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".trick")

        tiles = page.locator(".trick")
        assert await tiles.count() == 4
        boxes = [await tiles.nth(i).bounding_box() for i in range(4)]
        # все на одной строке: верхние края совпадают
        assert len({round(box["y"]) for box in boxes}) == 1, "плашки перенеслись"
        # и строка не вылезла за экран
        row = await page.locator(".trick-row").bounding_box()
        assert row["x"] >= -0.5 and row["x"] + row["width"] <= 420.5
        await browser.close()


@pytest.mark.parametrize("count", [1, 2, 3])
async def test_fewer_tricks_stand_in_the_middle(server, count):
    """Меньше четырёх — строка собирается по центру, а не липнет к краю."""
    ring = ring_with_duel()
    ring["duel"]["abilities"] = tricks_state(energy=20, count=count)
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".trick")

        tiles = page.locator(".trick")
        assert await tiles.count() == count
        row = await page.locator(".trick-row").bounding_box()
        first = await tiles.first.bounding_box()
        last = await tiles.nth(count - 1).bounding_box()
        left = first["x"] - row["x"]
        right = row["x"] + row["width"] - (last["x"] + last["width"])
        assert abs(left - right) <= 1.5, f"поля разъехались: {left} и {right}"
        # и плашки не растянулись на всю ширину
        assert first["width"] <= 92.5
        await browser.close()


async def test_a_pressed_trick_wears_a_green_ring(server):
    """Нажал — плашка в зелёной обводке, и энергия уже списана."""
    ring = ring_with_duel()
    state = tricks_state(energy=20)
    state["tricks"][2]["armed"] = True
    ring["duel"]["abilities"] = state
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".trick")

        armed = page.locator(".trick.armed")
        assert await armed.count() == 1
        ring_colour = await armed.evaluate("box => getComputedStyle(box).boxShadow")
        border = await armed.evaluate("box => getComputedStyle(box).borderTopColor")
        # зелёный — тот же, каким горит здоровье
        green = await page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--hp-green')"
        )
        assert "rgb" in ring_colour and border.startswith("rgb")
        assert green.strip(), "токен зелёного должен существовать"
        # соседняя плашка обводки не носит
        plain = page.locator(".trick:not(.armed)").first
        assert "none" in await plain.evaluate("b => getComputedStyle(b).boxShadow")
        await browser.close()


async def test_when_the_turn_norm_is_spent_the_panel_says_so(server):
    ring = ring_with_duel()
    ring["duel"]["abilities"] = tricks_state(energy=20, left=0)
    player = make_player()
    async with async_playwright() as pw:
        browser, page = await open_page(
            pw, server, build_card(player, TOKEN, viewer_id=player.user_id),
            build_shop(player), fights=ring,
        )
        await page.wait_for_selector("#hero:not(.hidden)")
        await open_screen(page, "club")
        await page.wait_for_selector(".energy-note")

        assert "кончились" in await page.locator(".energy-note").inner_text()
        await browser.close()
