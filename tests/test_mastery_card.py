"""Владение оружием на экранах: блок карточки, строка вещи, подсказка отказа.

Правила лежат в `bot.game.mastery`, раздача — в `bot.mastery_service`;
здесь проверяется только то, что видит игрок. А видеть он должен три
вещи: сколько у него очков и куда они вложены, чем берут вещь на
прилавке и чего ему не хватает — порознь по каждой нехватке, а не одним
«нельзя».

Городских это не касается вовсе: очков владения у них нет, оружие
приходит вместе с ними, — и показывать им долг было бы показывать то,
чего не бывает.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats
from bot.game.equipment import CATALOGUE
from bot.game.mastery import HoldType, WeaponFamily
from bot.mastery_service import invest
from bot.models import Player
from bot.webapp.card import (
    arming_payload,
    build_card,
    mastery_payload,
    requirements_payload,
)
from bot.webapp.server import create_app
from tests.test_webapp import FakeBot, TOKEN, make_init_data

SLASHING = WeaponFamily.SLASHING.value
PIERCING = WeaponFamily.PIERCING.value
SHIELD = HoldType.SHIELD.value
TWO_HANDED = HoldType.TWO_HANDED.value


def a_fighter(user_id: int = 42, level: int = 5, **kwargs) -> Player:
    stats = Stats(strength=20, agility=20, intuition=20, endurance=20)
    return Player(
        user_id=user_id,
        nickname="Тайлер",
        class_code="assassin",
        level=level,
        **stats.as_dict(),
        **kwargs,
    )


@pytest.fixture
async def client(db):
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    async with TestClient(TestServer(create_app(FakeBot(), db, config))) as one:
        yield one


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id=user_id)}


# ---------- блок карточки ----------


def test_the_card_shows_both_pools_and_marks_what_the_class_is_advised():
    """Четыре вида, три хвата, остаток порознь и подсветка своего.

    Подсветка — совет, а не запрет: отмеченным приезжает своё класса, но
    остальные направления остаются в списке и открыты для вложений.
    """
    player = a_fighter()
    block = mastery_payload(player)

    assert block["free"] == {"family": 5, "hold": 5}
    assert len(block["families"]) == 4 and len(block["holds"]) == 3
    advised = {
        one["code"] for one in block["families"] + block["holds"] if one["advised"]
    }
    # Ассасину советуют мечи, второе оружие и двуручный хват
    assert advised == {SLASHING, HoldType.OFFHAND.value, TWO_HANDED}
    assert all(one["title"] for one in block["families"])
    # Значка направления в столбцах нет: подпись и число, как у атаки с
    # защитой. Поле, которое экран не читает, в ответе не шлём
    assert "emoji" not in block["families"][0]


async def test_what_is_invested_stands_in_the_card(db):
    """Вложенное видно в блоке, и остаток пула уменьшается на столько же."""
    player = a_fighter()
    await db.save_player(player)
    await invest(db, player, SLASHING, 3)
    await invest(db, player, SHIELD, 1)

    block = mastery_payload(player)
    levels = {one["code"]: one["level"] for one in block["families"]}

    assert levels[SLASHING] == 3 and levels[PIERCING] == 0
    assert block["free"] == {"family": 2, "hold": 4}


def test_a_townsman_gets_no_mastery_block_at_all():
    """У городского блока владения нет: раздавать ему нечего."""
    npc = a_fighter(user_id=-1, level=5)

    assert build_card(npc, TOKEN, viewer_id=-1)["mastery"] is None
    assert build_card(a_fighter(), TOKEN, viewer_id=42)["mastery"] is not None


# ---------- строка вещи ----------


def test_an_item_card_names_the_family_the_hand_and_the_threshold():
    """Чем берут вещь: вид, хват и требуемое владение — всё в строке.

    Это свойство самой вещи, одинаковое на прилавке, в рюкзаке и на
    рынке, поэтому бойца строка не спрашивает вовсе.
    """
    sword = CATALOGUE["greatsword"]
    line = arming_payload(sword)

    assert line["family"] == SLASHING
    assert line["family_title"] == WeaponFamily.SLASHING.title
    assert line["hand"] == "two_handed" and line["hand_title"] == "обе руки"
    assert line["hold"] == TWO_HANDED
    assert line["mastery"] == sword.level_required

    # Лёгкое оружие годится в любую руку, и своего хвата в основной не просит
    knife = arming_payload(CATALOGUE["knife"])
    assert knife["family"] == PIERCING and knife["hand_title"] == "любая рука"
    assert knife["hold"] == "" and knife["mastery"] == CATALOGUE["knife"].level_required


def test_a_shield_asks_for_a_hold_and_names_no_hand():
    """Щит — это хват без вида: «основная рука» в его строке была бы неправдой."""
    line = arming_payload(CATALOGUE["set_sport_shield"])

    assert line["family"] == "" and line["hold"] == SHIELD
    assert line["hold_title"] == HoldType.SHIELD.title
    assert line["hand"] == "" and line["hand_title"] == ""
    assert line["mastery"] == CATALOGUE["set_sport_shield"].level_required


def test_clothes_and_firearms_have_no_arming_line():
    """Владения им не нужно, и строки о нём у них нет вовсе."""
    assert arming_payload(CATALOGUE["club_tee"]) == {}
    firearm = next(
        one for one in CATALOGUE.values() if one.kind.value == "firearm"
    )
    assert arming_payload(firearm) == {}


# ---------- подсказка отказа ----------


def code_rows(player: Player, code: str) -> list[dict]:
    weapon = CATALOGUE[code]
    return requirements_payload(player, weapon)


async def test_the_requirements_name_the_family_and_the_hold_apart(db):
    """Нехватка разбирается на части: вид отдельно, хват отдельно.

    Одной строкой «нельзя» игрок не узнал бы, что докупать, а вид и
    хват докупаются из разных пулов — смешивать их нельзя и в показе.
    """
    player = a_fighter(level=10)
    await db.save_player(player)

    rows = {one["code"]: one for one in code_rows(player, "greatsword")}
    need = CATALOGUE["greatsword"].level_required

    assert rows["family"]["need"] == need and rows["family"]["have"] == 0
    assert rows["family"]["title"] == WeaponFamily.SLASHING.title
    assert not rows["family"]["ok"]
    assert rows["hold"]["need"] == need and not rows["hold"]["ok"]
    assert rows["hold"]["title"] == HoldType.TWO_HANDED.title

    # Вложили в вид — закрылась строка вида, строка хвата осталась
    await invest(db, player, SLASHING, need)
    rows = {one["code"]: one for one in code_rows(player, "greatsword")}
    assert rows["family"]["ok"] and rows["family"]["have"] == need
    assert not rows["hold"]["ok"]

    await invest(db, player, TWO_HANDED, need)
    rows = {one["code"]: one for one in code_rows(player, "greatsword")}
    assert rows["family"]["ok"] and rows["hold"]["ok"]


def test_the_level_and_the_stats_keep_their_own_rows():
    """Строки роста владение не тронуло: уровень и статы на месте."""
    player = a_fighter(level=1)
    codes = [one["code"] for one in code_rows(player, "greatsword")]

    assert codes[0] == "level"
    assert codes[-2:] == ["family", "hold"], "владение идёт последним"
    assert "strength" in codes


def test_a_weapon_in_the_main_hand_asks_for_no_hold():
    """Строки хвата у обычного оружия нет: в первой руке его держит всякий."""
    codes = [one["code"] for one in code_rows(a_fighter(), "katana")]

    assert "family" in codes and "hold" not in codes


def test_a_townsman_owes_no_mastery_in_his_requirements():
    """Городскому строк владения не пишем: очков у него не бывает."""
    codes = [one["code"] for one in code_rows(a_fighter(user_id=-7), "greatsword")]

    assert "family" not in codes and "hold" not in codes
    assert "level" in codes, "уровень и характеристики у него обычные"


# ---------- мини-апп ----------


async def test_the_mini_app_lays_out_the_points_and_returns_a_fresh_card(client, db):
    """Заявка приходит пачкой, и в ответ — пересчитанная карточка."""
    await db.save_player(a_fighter(level=5))

    response = await client.post(
        "/api/mastery", json={SLASHING: 3, SHIELD: 2}, headers=headers()
    )
    body = await response.json()

    assert response.status == 200
    assert body["spent"] == {SLASHING: 3, SHIELD: 2}
    block = body["card"]["mastery"]
    assert block["free"] == {"family": 2, "hold": 3}
    levels = {one["code"]: one["level"] for one in block["families"]}
    assert levels[SLASHING] == 3
    saved = await db.get_player(42)
    assert saved.mastery == {SLASHING: 3} and saved.holds == {SHIELD: 2}


async def test_the_mini_app_counts_the_two_pools_apart(client, db):
    """Перебор в видах не трогает хваты: пулы считаются порознь."""
    await db.save_player(a_fighter(level=2))

    response = await client.post(
        "/api/mastery", json={SLASHING: 3, SHIELD: 1}, headers=headers()
    )
    body = await response.json()

    assert response.status == 409
    assert "вида оружия" in body["error"]
    saved = await db.get_player(42)
    assert saved.mastery == {} and saved.holds == {}, "заявка целиком не прошла"

    ok = await client.post(
        "/api/mastery", json={SLASHING: 2, SHIELD: 2}, headers=headers()
    )
    assert ok.status == 200
    saved = await db.get_player(42)
    assert saved.mastery == {SLASHING: 2} and saved.holds == {SHIELD: 2}


async def test_the_mini_app_refuses_a_nonsense_draft(client, db):
    """Чужой код, минус и пустая заявка меняют ровно ничего."""
    await db.save_player(a_fighter(level=5))

    for draft in ({"лазерный меч": 1}, {SLASHING: -2}, {}, {SLASHING: "много"}):
        response = await client.post(
            "/api/mastery", json=draft, headers=headers()
        )
        assert response.status == 409, draft
        await response.json()

    saved = await db.get_player(42)
    assert saved.mastery == {} and saved.holds == {}
