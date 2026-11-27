"""Сверочный лист оружейной: страница и выдача каталога.

Лист нужен владельцу, а не игре: по нему сверяют картинки с названиями
и числами. Поэтому в нём весь каталог разом — со снятым с продажи и
наградным, которых на прилавке не бывает, — и ни одного числа про
бойца. Проверяется здесь и это, и то, что бакет в вёрстку не попал:
адреса картинок приходят выдачей, как у сцены боя.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.equipment import CATALOGUE, ItemKind
from bot.webapp.arsenal import WAS_CALLED, arsenal_payload
from bot.webapp.server import STATIC_DIR, create_app
from tests.test_webapp import FakeBot, TOKEN


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    async with TestClient(TestServer(create_app(FakeBot(), db, config))) as one:
        yield one


def test_the_sheet_holds_everything_a_fighter_takes_in_hand():
    """В листе всё, что берут в руки, и ничего, что надевают.

    Прилавок показывает только продающееся; здесь нужно обратное —
    снятое и наградное тоже нарисовано, и разойтись с названием может
    точно так же.
    """
    rows = arsenal_payload()["items"]
    codes = {row["code"] for row in rows}
    hands = {
        code
        for code, item in CATALOGUE.items()
        if item.kind in (ItemKind.WEAPON, ItemKind.SHIELD, ItemKind.FIREARM)
    }

    assert codes == hands
    # Лист не отбирает по витрине: наградное, рейдовое и фанатское в
    # него входит, хотя купить это нельзя — арт у них есть, и
    # разойтись с названием он может точно так же
    from bot.game.equipment import SHOWCASE

    shelf = {item.code for item in SHOWCASE}
    assert codes - shelf, "в листе только прилавок — сверять нечего"
    assert any(row["reward"] for row in rows), "наградное тоже в листе"
    # Одежды и украшений в листе нет: владения они не требуют
    assert "club_tee" not in codes


def test_every_row_carries_its_picture_and_its_numbers():
    """У каждой строки есть адрес картинки, уровень и чем она считается."""
    for row in arsenal_payload()["items"]:
        assert row["image"].startswith("https://"), row["code"]
        assert row["level"] >= 1
        assert row["kind"] in ("Оружие", "Щит", "Огнестрел")
        if row["kind"] == "Оружие":
            assert row["damage"], row["code"]
            assert row["family"], row["code"]
            assert row["hand"], row["code"]
        if row["kind"] == "Щит":
            assert row["armor"], row["code"]
            # Щит держат второй рукой всегда, и хвата руки у него нет
            assert row["hand"] == ""
            assert row["hold"] == "Щиты"


def test_the_renamed_weapons_say_what_is_on_the_old_picture():
    """У переименованных в листе стоит прежнее имя — то, что на картинке.

    Пока арт не перерисован, лист обязан говорить, что именно
    разошлось: иначе сверять не с чем.
    """
    rows = {row["code"]: row for row in arsenal_payload()["items"]}

    assert set(WAS_CALLED) <= set(rows)
    for code, was in WAS_CALLED.items():
        assert rows[code]["was"] == was
        assert rows[code]["title"] != was, "иначе переименования не было"
    # И они идут первыми: лист собран под сверку арта
    first = [row["code"] for row in arsenal_payload()["items"][: len(WAS_CALLED)]]
    assert set(first) == set(WAS_CALLED)


def test_the_counts_add_up():
    """Счётчики сходятся с самим списком, а не считаются отдельно."""
    payload = arsenal_payload()
    rows = payload["items"]
    counts = payload["counts"]

    assert counts["all"] == len(rows)
    assert counts["weapons"] == sum(1 for row in rows if row["kind"] == "Оружие")
    assert counts["shields"] == sum(1 for row in rows if row["kind"] == "Щит")
    assert counts["renamed"] == len(WAS_CALLED)


async def test_the_page_and_the_payload_are_served_to_anyone(client):
    """Лист открывается без подписи Telegram: внутри только каталог."""
    page = await client.get("/arsenal")
    html = await page.text()

    assert page.status == 200
    assert "Арсенал клуба" in html

    answer = await client.get("/api/arsenal")
    body = await answer.json()

    assert answer.status == 200
    assert body["counts"]["all"] == len(body["items"])
    assert body["counts"]["weapons"] > 60


def test_the_page_does_not_know_the_bucket():
    """Адрес бакета в вёрстке не пишется: он приходит выдачей.

    То же правило, что у карточки и сцены боя: все адреса картинок
    живут в `bot.game.art`, а страница берёт их из ответа сервера.
    """
    html = (STATIC_DIR / "arsenal.html").read_text(encoding="utf-8")

    assert "r2.dev" not in html
    assert "api/arsenal" in html
