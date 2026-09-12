"""Кто сейчас в клубе, а кого давно не видели."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.game.classes import get_class
from bot.game.health import now_ts
from bot.game.presence import ONLINE_SECONDS, is_online, presence_text, rough_time
from bot.models import Player
from bot.webapp.card import build_card
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(user_id: int = 42, seen_at: int = 0) -> Player:
    fclass = get_class("warrior")
    return Player(
        user_id=user_id, nickname="Тайлер", class_code="warrior", level=5,
        seen_at=seen_at, **fclass.base_stats.as_dict(),
    )


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------- сами правила ----------


def test_a_fighter_stays_in_the_club_for_two_minutes_after_his_last_move():
    """Приложение молчит — значит, ушёл. Другого способа узнать нет."""
    now = 1_000_000

    assert is_online(now - 1, now)
    assert is_online(now - ONLINE_SECONDS, now), "две минуты ещё в клубе"
    assert not is_online(now - ONLINE_SECONDS - 1, now)
    assert not is_online(0, now), "ни разу не заходил"


@pytest.mark.parametrize(
    "ago,text",
    [
        (0, "🟢 В клубе"),
        (119, "🟢 В клубе"),
        (121, "Не был в клубе 2 минуты"),
        (20 * 60, "Не был в клубе 20 минут"),
        (60 * 60, "Не был в клубе 1 час"),
        (25 * 60 * 60, "Не был в клубе 1 день"),
    ],
)
def test_the_line_says_how_long_he_has_been_away(ago, text):
    now = 1_000_000
    assert presence_text(now - ago, now) == text


def test_a_fighter_nobody_ever_saw_is_named_so():
    """Ноль — это не «был вчера», это «не заходил вовсе»."""
    assert presence_text(0, 1_000_000) == "Ни разу не заходил"


@pytest.mark.parametrize(
    "seconds,text",
    [
        (30, "1 минуту"), (2 * 60, "2 минуты"), (5 * 60, "5 минут"),
        (61 * 60, "1 час"), (3 * 60 * 60, "3 часа"), (7 * 60 * 60, "7 часов"),
        (2 * 24 * 60 * 60, "2 дня"), (11 * 24 * 60 * 60, "11 дней"),
    ],
)
def test_the_time_is_rounded_to_something_a_person_would_say(seconds, text):
    """«20 минут», а не «20 минут 14 секунд»: строка про «давно ли»."""
    assert rough_time(seconds) == text


# ---------- отметка ставится сама ----------


async def test_opening_the_card_marks_you_as_in_the_club(client, db):
    """Любой запрос мини-аппа — и есть отметка «был в клубе»."""
    await db.save_player(make_player())
    assert (await db.get_player(42)).seen_at == 0

    await client.get("/api/card", headers=headers(42))

    seen = (await db.get_player(42)).seen_at
    assert abs(seen - now_ts()) <= 5


async def test_the_mark_is_not_written_on_every_poll(client, db):
    """Экран боя опрашивают раз в две секунды — в базу так часто не пишем."""
    await db.save_player(make_player())

    await client.get("/api/card", headers=headers(42))
    first = (await db.get_player(42)).seen_at
    await db.mark_seen(42, first - 999)  # как будто отметка старая
    await client.get("/api/card", headers=headers(42))

    assert (await db.get_player(42)).seen_at == first - 999, (
        "отметку переписали, хотя прошли не полминуты"
    )


async def test_a_stranger_sees_when_you_were_here_last(client, db):
    """Строку видит любой, кто открыл карточку: по ней и решают, звать ли."""
    await db.save_player(make_player(seen_at=now_ts() - 20 * 60))
    stranger = make_player(user_id=43)
    await db.save_player(stranger)

    body = await (await client.get(
        "/api/card?user_id=42", headers=headers(43)
    )).json()

    assert body["seen"]["online"] is False
    assert body["seen"]["text"] == "Не был в клубе 20 минут"


def test_the_card_carries_the_line_for_the_sheet():
    """Карточка отдаёт и признак, и готовую строку: рисовать нечего считать."""
    now = now_ts()
    card = build_card(make_player(seen_at=now - 10), TOKEN, viewer_id=42)

    assert card["seen"] == {"online": True, "text": "🟢 В клубе"}
