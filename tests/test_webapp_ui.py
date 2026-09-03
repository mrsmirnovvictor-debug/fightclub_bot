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


class FakeBot:  # pragma: no cover - аватар в этом тесте не трогаем
    async def get_file(self, file_id):
        raise AssertionError

    async def download_file(self, path):
        raise AssertionError


async def open_page(
    pw, server, card, shop=None, query="", topup=None, looks=None, club=None,
    magic=None, fights=None, history=None, fight_log=None,
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
        await page.route(IMAGES, lambda route: route.abort())

        await page.route("**/api/club*", canned({"fighters": [], "total": 0}))
        await page.route("**/api/magic*", canned({"items": [], "credits": 0}))
        await page.route("**/api/fights*", canned(EMPTY_RING))
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
    # восемь полок с экипировкой плюс «Прочее» — эликсиры
    heads = await shelves(shop_page)
    assert len(heads) == 9
    titles = await visible_titles(shop_page)
    assert "Кастет" in titles  # открыто по уровню
    assert "Бита" not in titles  # закрыто, лежит под кнопкой

    # пустой раздел с прилавка не пропадает: видно, что его готовят
    shirts = next(head for head in heads if "Футболки" in head)
    assert "открыто 0 из 0" in shirts
    assert await shop_page.get_by_text("Скоро завезут.").is_visible()


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
    # именно фильтры прилавка: пузыри с разделами клуба живут своей жизнью
    labels = await shop_page.locator("#filter-type .chip").all_inner_texts()

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


async def test_the_bag_pours_a_potion_and_the_counter_sells_them(server):
    """Склянка стоит своей полкой в рюкзаке, и у неё одна кнопка — выпить."""
    player = make_player()
    player.potions = {"heal_small": 2}
    player.effects = [
        ActiveEffect(code="boost_strength", until=now_ts() + 3600 + 47 * 60)
    ]
    card = build_card(player, TOKEN, viewer_id=player.user_id)

    async with async_playwright() as pw:
        browser, page = await open_page(pw, server, card, build_shop(player))
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

        # на прилавке склянки лежат под своим фильтром
        await page.locator("#tab-shop").click()
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
        await page.locator("#tab-magic").click()
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
        await page.locator("#tab-magic").click()
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
        assert "🗡6–14" in hero
        assert "🗡 Световой меч" in hero
        assert "7–15 → 6–14" in hero
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
        assert sections == ["Бои", "Игроки", "Статистика"]
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
        assert heads == ["Атака", "Защита"]
        columns = page.locator(".zone-column")
        assert await columns.nth(0).locator(".zone").all_inner_texts() == [
            "Голова", "Корпус"
        ]
        assert await columns.nth(1).locator(".zone").all_inner_texts() == [
            "Голова + Корпус", "Корпус + Живот"
        ]
        # пока ничего не выбрано, отправлять нечего
        assert await page.locator("#turn-go").is_disabled()
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
        await page.locator(".zone-column").nth(0).get_by_text("Голова").click()
        assert await page.locator("#turn-go").is_disabled()
        assert sent == []

        await page.locator(".zone-column").nth(1).get_by_text("Корпус + Живот").click()
        assert await page.locator("#turn-go").is_enabled()
        assert sent == []  # до нажатия «Вперёд!» судья ничего не знает

        await page.locator("#turn-go").click()
        await page.wait_for_selector("#turn-go", state="detached")

        assert sent == [{"action": "turn", "attack": "head", "block": "chest"}]
        # выбор принят: вместо кнопок ожидание соперника
        assert "Ждём соперника" in await page.locator("#fights-body").inner_text()
        await browser.close()


async def test_the_choice_can_be_changed_before_it_is_sent(server):
    """Передумать можно сколько угодно: пока не нажали «Вперёд!», выбор свой."""
    async with async_playwright() as pw:
        browser, page = await open_ring(pw, server, ring_with_duel())
        column = page.locator(".zone-column").nth(0)

        await column.get_by_text("Голова").click()
        await column.get_by_text("Корпус").click()

        lit = await page.locator(".zone-column").nth(0).locator(".zone.on").all_inner_texts()
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


# ---------- статистика боёв ----------


HISTORY = {
    "user_id": 42,
    "name": "Растафарайчик",
    "total": 3,
    "counts": {"win": 2, "loss": 1, "draw": 0},
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
        assert "3 боя" in note and "2 побед" in note

        days = await page.locator("#club-stats .shelf-head").all_inner_texts()
        assert days == ["3 сентября", "1 сентября"]

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
        assert await page.locator(".fight-row").count() == 3
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
