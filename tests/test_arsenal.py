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
from bot.webapp.arsenal import DRAWN_INSTEAD, arsenal_payload
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


def test_the_rows_say_where_the_picture_argues_with_the_name():
    """Где картинки нет или она спорит с названием, лист говорит прямо.

    Лист для того и сделан: пока арт не догнал каталог, по этим строкам
    рисуют. Наверх они больше не поднимаются — их тридцать три, и у
    двадцати шести картинки нет вовсе, так что лист из них одних и
    состоял бы в начале. Отбирают их кнопкой, а строка остаётся у
    каждой вещи.
    """
    rows = {row["code"]: row for row in arsenal_payload()["items"]}

    assert set(DRAWN_INSTEAD) <= set(rows)
    for code, drawn in DRAWN_INSTEAD.items():
        assert rows[code]["drawn"] == drawn
        assert rows[code]["title"].lower() != drawn, "иначе спора нет"
    # У остальных строка пустая: пометка — про расхождение, а не про всё
    quiet = [row for row in rows.values() if row["code"] not in DRAWN_INSTEAD]
    assert quiet and not any(row["drawn"] for row in quiet)


def test_the_sheet_runs_in_the_order_of_the_catalogue():
    """Лист идёт как каталог: оружие по видам и ступеням, потом щиты.

    Порядок правил — колющее, рубящее, дробящее, режущее, — и внутри
    вида от младшей ступени к старшей: так видно, похожи ли топоры на
    топоры и растёт ли линия от младшего к старшему. Щиты идут после
    всего оружия, огнестрел последним: он в клубе один.
    """
    from bot.webapp.arsenal import FAMILY_ORDER

    rows = arsenal_payload()["items"]
    heads: list[str] = []
    for row in rows:
        head = row["family"] or row["kind"]
        if not heads or heads[-1] != head:
            heads.append(head)
    assert heads == [
        "Ножи и кастеты",
        "Топоры и секиры",
        "Дубины и молоты",
        "Мечи",
        "Щит",
        "Огнестрел",
    ], heads

    # Внутри вида ступени не убывают
    for family in FAMILY_ORDER:
        mine = [row for row in rows if row["family_code"] == family]
        assert mine, family
        levels = [row["level"] for row in mine]
        assert levels == sorted(levels), family


def test_every_weapon_says_what_kind_of_damage_it_deals():
    """Тип урона есть у каждого оружия: вид и тип урона это одно и то же."""
    kinds = {"колющий", "рубящий", "дробящий", "режущий"}
    for row in arsenal_payload()["items"]:
        if row["kind"] == "Оружие":
            assert row["damage_kind"] in kinds, row["code"]
            # Тяжёлое — двуручное, и у колющих его не бывает
            if row["heavy"]:
                assert row["damage_kind"] != "колющий", row["code"]
        else:
            assert row["damage_kind"] == "", row["code"]


def test_the_counts_add_up():
    """Счётчики сходятся с самим списком, а не считаются отдельно."""
    payload = arsenal_payload()
    rows = payload["items"]
    counts = payload["counts"]

    assert counts["all"] == len(rows)
    assert counts["weapons"] == sum(1 for row in rows if row["kind"] == "Оружие")
    assert counts["shields"] == sum(1 for row in rows if row["kind"] == "Щит")
    assert counts["drawn_apart"] == len(DRAWN_INSTEAD)


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
