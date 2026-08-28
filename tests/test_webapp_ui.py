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
from bot.game.store import PACKS
from bot.models import Player
from bot.webapp.card import build_card, build_shop, build_topup
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


async def open_page(
    pw, server, card, shop=None, query="", topup=None, looks=None, club=None
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

        await page.route("**/api/club*", canned({"fighters": [], "total": 0}))
        await page.goto(f"{server.make_url('/')}")
        await page.wait_for_selector("#hero:not(.hidden)")
        await page.locator("#tab-shop").click()
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
    await shop_page.get_by_role("button", name="Оружие", exact=True).click()

    assert [head.split("\n")[0] for head in await shelves(shop_page)] == ["🔪 Оружие"]
    assert all(
        title
        in ("Кастет", "Деревянная бита", "Выкидуха", "Строительный нож",
            "Монтировка", "Нож")
        for title in await visible_titles(shop_page)
    )


async def test_the_counter_has_no_level_filter_any_more(shop_page):
    """Фильтр остался один — тип вещи, и он не лента, а пузыри в несколько строк."""
    labels = await shop_page.locator(".bubbles .chip").all_inner_texts()

    assert labels[0] == "Все"
    assert "Оружие" in labels
    assert not [label for label in labels if any(ch > "\u2000" for ch in label)], (
        "в фильтрах остались значки"
    )
    assert not [label for label in labels if "ур." in label], "уровни всё ещё в фильтрах"
    assert await shop_page.locator(".filters").count() == 0


async def test_locked_goods_stay_folded_at_the_end_of_the_shelf(shop_page):
    """Закрытое по уровню видно только под кнопкой — это не фильтр, а раскладка."""
    titles = await visible_titles(shop_page)
    assert "Кастет" in titles  # открыто по уровню
    assert "Бита" not in titles  # закрыто

    text = await shop_page.locator("#shop-list").inner_text()
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


async def test_the_bottom_bar_switches_five_screens(server):
    """Панель снизу: клуб, магазин, лавка мага, инвентарь, персонаж."""
    player = make_player()
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
        await page.wait_for_selector("#hero:not(.hidden)")

        assert await page.locator("#bar").is_visible()
        assert await page.locator(".bar-tab").count() == 5
        # открывается карточка персонажа, её вкладка и подсвечена
        assert await page.locator("#tab-hero").get_attribute("class") == "bar-tab active"

        for tab in ("club", "shop", "magic", "bag", "hero"):
            await page.locator("#tab-" + tab).click()
            await page.wait_for_selector("#" + tab + ":not(.hidden)")
            shown = [
                screen
                for screen in ("club", "shop", "magic", "bag", "hero")
                if await page.locator("#" + screen).is_visible()
            ]
            assert shown == [tab], f"вместе с {tab} открыто {shown}"

        assert "в разработке" in await page.locator("#magic").inner_text()
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
        await page.locator("#tab-shop").click()
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


async def test_an_empty_slot_falls_back_to_its_icon(server):
    """Подложка не доехала — слот гаснет и показывает значок, как раньше."""
    player = make_player()
    player.gear = []  # всё снято, все восемь слотов пустые
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        # картинки в этом тесте не отдаются: маршрут .jpeg их обрывает
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
    """Список клуба: ник, уровень и кнопка «i» с карточкой соседа."""
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

        card_text = await page.locator("#sheet-list").inner_text()
        for line in ("Сила", "Ловкость", "Интуиция", "Выносливость"):
            assert line in card_text
        for line in ("Уровень", "Опыт", "Побед", "Поражений", "Ничьих", "Рейтинг"):
            assert line in card_text
        assert "Клуб на Вязов" in card_text
        assert "День рождения персонажа" in card_text
        # чужой кошелёк в карточке не показываем
        assert "Кредиты" not in card_text
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
