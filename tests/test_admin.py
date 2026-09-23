"""Команда владельца: выдача подписки PRO руками.

Главное здесь не про подписку, а про дверь: команда, которая раздаёт
платное даром, не должна отвечать никому, кроме владельца, — и в первую
очередь тогда, когда владельца забыли назначить.
"""

from dataclasses import replace

import pytest

from bot.config import Config
from bot.game.classes import get_class
from bot.game.health import now_ts
from bot.game.pro import DAY, PRO_DAYS, PRO_ITEM, PRO_LOOK
from bot.handlers.admin import MAX_GIFT_DAYS, is_owner
from bot.models import Player
from tests.harness import DISPATCHER, Client


def make_player(user_id: int, nickname: str) -> Player:
    stats = get_class("warrior").base_stats
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code="warrior",
        level=5,
        **stats.as_dict(),
    )


@pytest.fixture
async def club(dispatcher_env):
    """Владелец в личке бота и боец «x RED x», которому выдают подписку."""
    db, _, session = dispatcher_env
    owner = Client(db)
    DISPATCHER["config"] = replace(DISPATCHER["config"], owner_id=owner.user.id)
    await db.save_player(make_player(777, "x RED x"))
    session.calls.clear()
    yield db, owner, session


async def pro_of(db, user_id: int = 777) -> int:
    player = await db.get_player(user_id)
    return player.pro_until


# ---------- дверь ----------


def test_without_an_owner_nobody_is_one():
    """Незаданный OWNER_ID не делает владельцем всех подряд."""
    nobody = Config(bot_token="t")

    assert nobody.owner_id == 0
    assert not is_owner(0, nobody) and not is_owner(42, nobody)
    assert is_owner(42, replace(nobody, owner_id=42))
    assert not is_owner(43, replace(nobody, owner_id=42))


def test_rubbish_in_the_variable_names_no_owner(monkeypatch):
    """Мусор в OWNER_ID — это не владелец, а опечатка."""
    from bot.config import _owner_id

    monkeypatch.setenv("OWNER_ID", "не число")
    assert _owner_id() == 0
    monkeypatch.setenv("OWNER_ID", "  ")
    assert _owner_id() == 0
    monkeypatch.setenv("OWNER_ID", " 4242 ")
    assert _owner_id() == 4242


async def test_a_stranger_gets_no_answer_at_all(club):
    """Чужому команды не существует: ни выдачи, ни отказа."""
    db, _, session = club
    stranger = Client(db)

    await stranger.send("/givepro x RED x")

    assert session.texts == [], "посторонний не должен знать, что она есть"
    assert await pro_of(db) == 0


async def test_without_an_owner_even_the_owner_is_refused(club, caplog):
    """OWNER_ID не задан — команда молчит, но след остаётся в логах."""
    db, owner, session = club
    DISPATCHER["config"] = replace(DISPATCHER["config"], owner_id=0)

    with caplog.at_level("WARNING"):
        await owner.send("/givepro x RED x")

    assert session.texts == []
    assert await pro_of(db) == 0
    assert "OWNER_ID" in caplog.text


# ---------- выдача ----------


async def test_the_owner_grants_a_month(club):
    """Месяц подписки, клинок в рюкзак и образ в гардероб — как за звёзды."""
    db, owner, session = club
    before = now_ts()

    await owner.send("/givepro x RED x")

    until = await pro_of(db)
    assert PRO_DAYS * DAY - 60 <= until - before <= PRO_DAYS * DAY + 60
    player = await db.get_player(777)
    assert any(owned.code == PRO_ITEM for owned in player.gear), "клинок не выдали"
    assert PRO_LOOK in await db.owned_looks(777)
    # Ответ называет бойца и до какого часа подписка
    said = session.texts[-1]
    assert "x RED x" in said and "30 дней" in said and "мск" in said


async def test_a_term_can_be_asked_for(club):
    """«/givepro ник 7» — неделя вместо месяца."""
    db, owner, _ = club
    before = now_ts()

    await owner.send("/givepro x RED x 7")

    assert 7 * DAY - 60 <= await pro_of(db) - before <= 7 * DAY + 60


async def test_a_nickname_ending_in_a_number_stays_a_nickname(club):
    """«Боец 7» — это чей-то ник, а не семь дней для «Бойца»."""
    db, owner, _ = club
    await db.save_player(make_player(778, "Боец 7"))
    before = now_ts()

    await owner.send("/givepro Боец 7")

    player = await db.get_player(778)
    assert PRO_DAYS * DAY - 60 <= player.pro_until - before, "продлили не тому"


async def test_an_unknown_fighter_gets_nothing(club):
    """Ника нет в клубе — говорим об этом и ничего не трогаем."""
    db, owner, session = club

    await owner.send("/givepro Кого-то-нет")

    assert "нет" in session.texts[-1] and "givepro" in session.texts[-1]
    assert await pro_of(db) == 0


async def test_a_silly_term_is_refused(club):
    """Срок дольше года — почти наверняка лишняя цифра в числе."""
    db, owner, session = club

    await owner.send(f"/givepro x RED x {MAX_GIFT_DAYS + 1}")

    assert str(MAX_GIFT_DAYS) in session.texts[-1]
    assert await pro_of(db) == 0


async def test_an_empty_command_explains_itself(club):
    """Без имени команда объясняет, как ей пользоваться."""
    db, owner, session = club

    await owner.send("/givepro")

    assert "givepro" in session.texts[-1]
    assert await pro_of(db) == 0


async def test_a_second_grant_adds_to_the_first(club):
    """Продление не отбирает уже оплаченное, а кладётся сверху."""
    db, owner, session = club

    await owner.send("/givepro x RED x 5")
    after_first = await pro_of(db)
    await owner.send("/givepro x RED x 5")

    assert 10 * DAY - 60 <= await pro_of(db) - now_ts() <= 10 * DAY + 60
    assert await pro_of(db) > after_first
    assert "продлена" in session.texts[-1], "второй раз — уже продление"


async def test_the_gift_stays_out_of_the_paid_history(club):
    """Подарок — не покупка: в истории оплат ему места нет."""
    db, owner, _ = club

    await owner.send("/givepro x RED x")
    await owner.send("/givepro x RED x")

    assert await db.purchases_of(777) == [], "подарок попал в историю оплат"
