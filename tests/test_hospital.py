"""Больница: здоровье за кредиты.

Проверяем три вещи: прайс (сколько дольют), службу (сколько спишут и
кому откажут) и дверь — лечиться можно только в больнице и только не
посреди боя. Спрятанная кнопка обходится запросом мимо интерфейса, и
без серверной проверки лечиться можно было бы прямо с ринга.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import Stats
from bot.game.health import now_ts
from bot.game.hospital import CURES, FULL_PRICE, PATCH_HEAL, PATCH_PRICE, get_cure
from bot.hospital_service import HospitalError, heal
from bot.models import Player
from bot.webapp.hospital import build_hospital
from bot.webapp.server import TRAVEL_KEY, create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(location: str = "hospital", credits: int = 200) -> Player:
    """Боец с запасом здоровья больше сотни.

    Выносливость здесь не для красоты: у новичка потолок здоровья ниже
    ста, и перевязка упиралась бы в него в каждом тесте — а проверять
    надо, что она доливает ровно свою сотню.
    """
    return Player(
        user_id=42, nickname="Тайлер", class_code="warrior", level=8,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=30).as_dict(),
    )


def beaten(player: Player, hp: int) -> Player:
    """Боец после боя: здоровья столько, сколько сказано."""
    player.set_hp(hp)
    return player


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- прайс ----------


def test_the_price_list_has_two_lines_and_the_cheap_one_is_half():
    """Полное выздоровление и перевязка: вторая вдвое дешевле."""
    full, patch = CURES

    assert (full.code, full.price) == ("full", 50)
    assert (patch.code, patch.price, patch.heal) == ("patch", 25, 100)
    assert patch.price * 2 == full.price
    assert get_cure("нет такого") is None


def test_the_full_cure_does_not_count_how_much_it_poured():
    """Полное выздоровление доливает сколько нужно, перевязка — сотню."""
    full, patch = CURES

    # Избит сильно: полное лечит всё, перевязка — свою сотню
    assert full.healed(current=40, max_hp=300) == 260
    assert patch.healed(current=40, max_hp=300) == PATCH_HEAL
    # Царапина: обе доливают одинаково, до потолка и ни единицей больше
    assert full.healed(current=290, max_hp=300) == 10
    assert patch.healed(current=290, max_hp=300) == 10
    # Целому не доливают вовсе
    assert full.healed(current=300, max_hp=300) == 0
    assert patch.healed(current=300, max_hp=300) == 0


# ---------- приём ----------


async def test_the_full_cure_fills_the_bar_and_takes_fifty(db):
    player = beaten(make_player(), 10)
    await db.save_player(player)

    result = await heal(db, player, "full")

    assert player.current_hp() == player.max_hp
    assert player.credits == 200 - FULL_PRICE
    assert result.healed == player.max_hp - 10 and result.price == FULL_PRICE
    # И это доехало до базы, а не осталось в памяти
    saved = await db.get_player(42)
    assert saved.credits == 200 - FULL_PRICE
    assert saved.current_hp() >= saved.max_hp


async def test_the_patch_pours_a_hundred_and_takes_twenty_five(db):
    player = beaten(make_player(), 10)
    await db.save_player(player)

    result = await heal(db, player, "patch")

    assert result.healed == PATCH_HEAL
    assert player.current_hp() == 10 + PATCH_HEAL
    assert player.credits == 200 - PATCH_PRICE


async def test_nobody_pours_over_the_ceiling(db):
    """Перевязка упирается в потолок: лишнее не дольют и не спишут дважды."""
    player = make_player()
    await db.save_player(player)
    player = beaten(player, player.max_hp - 5)

    result = await heal(db, player, "patch")

    assert result.healed == 5
    assert player.current_hp() == player.max_hp
    assert player.credits == 200 - PATCH_PRICE


async def test_a_whole_fighter_keeps_his_money(db):
    """Целому лечиться нечего — и кредиты остаются при нём."""
    player = make_player()
    await db.save_player(player)

    with pytest.raises(HospitalError, match="целый"):
        await heal(db, player, "full")

    assert player.credits == 200


async def test_without_credits_there_is_no_cure(db):
    """Не хватает — говорим цену и сколько есть, и здоровья не даём."""
    player = beaten(make_player(credits=10), 10)
    await db.save_player(player)

    with pytest.raises(HospitalError) as failed:
        await heal(db, player, "patch")

    assert str(PATCH_PRICE) in str(failed.value) and "10" in str(failed.value)
    assert player.current_hp() == 10 and player.credits == 10


async def test_a_fighter_in_a_fight_is_not_treated(db):
    """Из боя не лечатся: здоровье боя всё равно ляжет поверх.

    Дорога в больницу заперта, пока боец занят, но втянуть его в бой
    могут и на месте, из группы. Без этой проверки он платил бы полста и
    получал бы ту же рану обратно.
    """
    player = beaten(make_player(), 10)
    await db.save_player(player)

    with pytest.raises(HospitalError, match="бою"):
        await heal(db, player, "full", busy=True)

    assert player.credits == 200 and player.current_hp() == 10


async def test_an_unknown_cure_is_refused(db):
    player = beaten(make_player(), 10)
    await db.save_player(player)

    with pytest.raises(HospitalError):
        await heal(db, player, "пересадка сердца")

    assert player.credits == 200


# ---------- дверь ----------


async def test_the_price_list_opens_only_in_the_hospital(client, db):
    """Прайс — в больнице. С ринга его не видно."""
    await db.save_player(beaten(make_player(location="fight_club"), 10))

    answer = await client.get("/api/hospital", headers=headers())

    assert answer.status == 409
    assert "Больница" in (await answer.json())["error"]


async def test_healing_from_the_ring_is_refused(client, db):
    """Лечиться запросом мимо карты нельзя: сервер смотрит, где боец."""
    player = beaten(make_player(location="fight_club"), 10)
    await db.save_player(player)

    answer = await client.post(
        "/api/heal", json={"cure": "full"}, headers=headers()
    )

    assert answer.status == 409
    saved = await db.get_player(42)
    assert saved.credits == 200 and saved.current_hp() < saved.max_hp


async def test_the_hospital_answers_with_the_price_and_the_bar(client, db):
    await db.save_player(beaten(make_player(), 10))

    body = await (await client.get("/api/hospital", headers=headers())).json()

    assert body["credits"] == 200
    assert body["hp"]["current"] == 10 and body["hp"]["missing"] > 0
    prices = {cure["code"]: cure["price"] for cure in body["cures"]}
    assert prices == {"full": FULL_PRICE, "patch": PATCH_PRICE}
    assert all(cure["useful"] and cure["affordable"] for cure in body["cures"])


async def test_healing_answers_with_a_fresh_card_and_price_list(client, db):
    """Ответ несёт и карточку, и прайс: экран не перезапрашивает их сам."""
    await db.save_player(beaten(make_player(), 10))

    body = await (
        await client.post("/api/heal", json={"cure": "patch"}, headers=headers())
    ).json()

    assert body["done"] == {
        "title": "Перевязка", "healed": PATCH_HEAL, "price": PATCH_PRICE,
    }
    assert body["card"]["hp"]["current"] == 10 + PATCH_HEAL
    assert body["card"]["record"]["credits"] == 200 - PATCH_PRICE
    assert body["hospital"]["credits"] == 200 - PATCH_PRICE


async def test_a_poor_fighter_gets_a_refusal_not_a_cure(client, db):
    await db.save_player(beaten(make_player(credits=1), 10))

    answer = await client.post(
        "/api/heal", json={"cure": "full"}, headers=headers()
    )

    assert answer.status == 409
    assert "не хватает" in (await answer.json())["error"].lower()
    assert (await db.get_player(42)).current_hp() == 10


async def test_the_server_asks_the_road_whether_the_fighter_is_busy(client, db):
    """Занятого бойца больница не принимает — и знает об этом от дороги."""
    await db.save_player(beaten(make_player(), 10))

    class Fight:
        def is_busy(self, user_id: int) -> bool:
            return True

    client.app[TRAVEL_KEY].watch(Fight())
    answer = await client.post(
        "/api/heal", json={"cure": "full"}, headers=headers()
    )

    assert answer.status == 409
    assert "бою" in (await answer.json())["error"]
    assert (await db.get_player(42)).credits == 200


def test_the_bar_knows_how_much_is_missing():
    """Карточке больницы хватает своего ответа, чтобы нарисовать полосу."""
    player = beaten(make_player(), 10)
    body = build_hospital(player, now_ts())

    assert body["hp"]["missing"] == player.max_hp - 10
    assert body["hp"]["max"] == player.max_hp
    assert body["hp"]["regen_seconds"] > 0
