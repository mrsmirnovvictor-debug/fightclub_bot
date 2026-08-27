"""Живая проверка витрины в браузере: вкладки и фильтры магазина.

Тест поднимает настоящий мини-апп и открывает его Chromium'ом, подменяя
только ответы API. Если браузера в системе нет — тест пропускается: остальной
прогон от этого не зависит.
"""

import json
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from bot.game.classes import Stats
from bot.game.equipment import CATALOGUE, OwnedItem, Slot
from bot.models import Player
from bot.webapp.card import build_card, build_shop
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


class FakeBot:  # pragma: no cover - аватар в этом тесте не трогаем
    async def get_file(self, file_id):
        raise AssertionError

    async def download_file(self, path):
        raise AssertionError


async def open_page(pw, server, card, shop=None, query=""):
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
    await page.route("https://telegram.org/**", lambda route: route.fulfill(
        status=200, content_type="application/javascript", body=""
    ))
    await page.route("**/*.jpeg", lambda route: route.abort())
    await page.goto(f"{server.make_url('/')}{query}")
    return browser, page


@pytest.fixture
async def shop_page(db):
    """Страница мини-аппа с подменёнными ответами API."""
    from bot.config import Config

    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)
    shop = build_shop(player)

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
        await page.route("**/*.jpeg", lambda route: route.abort())

        await page.goto(f"{server.make_url('/')}?view=shop")
        await page.wait_for_selector(".shelf")
        yield page
        await browser.close()
    await server.close()


async def shelves(page) -> list[str]:
    return await page.locator(".shelf-head").all_inner_texts()


async def visible_titles(page) -> list[str]:
    return await page.locator(".shelf-list:not(.hidden) .thing-title").all_inner_texts()


async def test_shop_opens_with_all_types_on_the_counter(shop_page):
    assert len(await shelves(shop_page)) == 8
    titles = await visible_titles(shop_page)
    assert "Кастет" in titles  # открыто по уровню
    assert "Бита" not in titles  # закрыто, лежит под кнопкой


async def test_type_filter_leaves_one_shelf(shop_page):
    await shop_page.get_by_role("button", name="🔪 Оружие").click()

    assert [head.split("\n")[0] for head in await shelves(shop_page)] == ["🔪 Оружие"]
    assert all(
        title in ("Кастет", "Обрезок трубы", "Выкидуха", "Шило", "Монтировка")
        for title in await visible_titles(shop_page)
    )


async def test_level_filter_shows_the_whole_batch_even_if_it_is_locked(shop_page):
    await shop_page.get_by_role("button", name="🔒 6 ур.").click()

    titles = await visible_titles(shop_page)
    assert sorted(titles) == sorted(
        [
            "Мотошлем",
            "Кепка с козырьком",
            "Бита",
            "Мачете",
            "Стилет",
            "Кувалда",
            "Берцы",
            "Беговые кроссовки",
        ]
    )
    note = await shop_page.locator("#shop-note").inner_text()
    assert "6 уровня откроется" in note
    assert "Купить" not in await shop_page.locator("#shop-list").inner_text()


async def test_available_filter_hides_everything_locked(shop_page):
    await shop_page.get_by_role("button", name="Доступные").click()

    text = await shop_page.locator("#shop-list").inner_text()
    assert "Откроется на" not in text
    assert "Показать закрытые" not in text
    assert "Кастет" in await visible_titles(shop_page)


async def test_filters_can_leave_the_counter_empty(shop_page):
    await shop_page.get_by_role("button", name="🛡 Щит").click()
    await shop_page.get_by_role("button", name="🔒 6 ур.").click()

    assert await shop_page.locator("#shop-empty").is_visible()
    assert await shop_page.locator(".shelf").count() == 0



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
        await page.wait_for_selector("#card:not(.hidden)")

        assert await page.locator("#tabs").is_hidden(), "чужому видны вкладки"
        assert await page.locator("#bag").is_hidden(), "чужому виден инвентарь"
        assert await page.locator("#shop").is_hidden(), "чужому видна лавка"

        # зато боец и его характеристики на месте
        assert await page.locator("#name").inner_text() == stranger.nickname
        rows = await page.locator("#stats li").all_inner_texts()
        assert any("Сила" in row for row in rows)
        combat = await page.locator("#combat").inner_text()
        for line in ("Урон", "Крит", "Уворот", "Сопротивление"):
            assert line in combat
        # кошелёк соседа не показываем
        assert "777" not in await page.locator("#record").inner_text()
        await browser.close()


async def test_own_card_keeps_the_tabs(server):
    """На своей карточке вкладки и рюкзак на месте."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#card:not(.hidden)")

        assert await page.locator("#tabs").is_visible()
        assert await page.locator("#bag").is_visible()
        await page.get_by_role("button", name="🏪 Магазин").click()
        assert await page.locator("#shop").is_visible()
        assert await page.locator("#card").is_hidden()
        await browser.close()
