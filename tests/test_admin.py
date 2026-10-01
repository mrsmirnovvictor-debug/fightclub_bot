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
from bot.game.pro import DAY, LEGACY_LOOK, PRO_DAYS
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


async def test_the_owner_grants_a_month_and_nothing_else(club):
    """Месяц подписки — и только срок: ни вещей, ни образа.

    Выдача от руки не должна оказаться единственной дверью, через которую
    ещё ходит то, что из подписки убрали.
    """
    db, owner, session = club
    before = now_ts()

    await owner.send("/givepro x RED x")

    until = await pro_of(db)
    assert PRO_DAYS * DAY - 60 <= until - before <= PRO_DAYS * DAY + 60
    player = await db.get_player(777)
    assert player.gear == [], "подписка снаряжения не даёт"
    assert LEGACY_LOOK not in await db.owned_looks(777), "и образа тоже"
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


# ---------- кредиты ----------


async def cash_of(db, user_id: int = 777) -> int:
    player = await db.get_player(user_id)
    return player.credits


async def test_a_stranger_cannot_print_money(club):
    """Чужому команды не существует: ни начисления, ни отказа."""
    db, _, session = club
    stranger = Client(db)

    await stranger.send("/givecredits x RED x 500")

    assert session.texts == [], "посторонний не должен знать, что она есть"
    assert await cash_of(db) == 0


async def test_without_an_owner_even_the_owner_prints_nothing(club, caplog):
    """OWNER_ID не задан — команда молчит, но след остаётся в логах."""
    db, owner, session = club
    DISPATCHER["config"] = replace(DISPATCHER["config"], owner_id=0)

    with caplog.at_level("WARNING"):
        await owner.send("/givecredits x RED x 500")

    assert session.texts == []
    assert await cash_of(db) == 0
    assert "OWNER_ID" in caplog.text


async def test_the_owner_hands_out_credits(club):
    """Сумма ложится наличными, и ответ называет и было, и стало."""
    db, owner, session = club

    await owner.send("/givecredits x RED x 500")

    assert await cash_of(db) == 500
    said = session.texts[-1]
    assert "x RED x" in said and "500" in said and "начислено" in said


async def test_credits_land_as_cash_and_not_on_the_bank_account(club):
    """Начисление — такой же доход, как всякий другой: оно наличными.

    Со счётом боец разбирается сам, в банке. Положи команда деньги туда,
    на рынке их нельзя было бы передать, а в зале — заплатить без карты.
    """
    db, owner, _ = club

    await owner.send("/givecredits x RED x 500")

    player = await db.get_player(777)
    assert player.credits == 500
    assert player.account_balance == 0, "деньги ушли на счёт, а не в карман"


async def test_a_grant_adds_to_what_the_fighter_already_had(club):
    """Начисление кладётся сверху, а не заменяет кошелёк."""
    db, owner, _ = club

    await owner.send("/givecredits x RED x 500")
    await owner.send("/givecredits x RED x 300")

    assert await cash_of(db) == 800


async def test_there_is_no_ceiling_on_the_sum(club):
    """«Любое количество» — значит любое: потолка у начисления нет."""
    db, owner, session = club

    await owner.send("/givecredits x RED x 1000000")

    assert await cash_of(db) == 1_000_000
    assert "начислено" in session.texts[-1]


async def test_a_minus_takes_credits_back(club):
    """Лишний ноль в сумме исправляется минусом, а не правкой базы."""
    db, owner, session = club
    await owner.send("/givecredits x RED x 5000")

    await owner.send("/givecredits x RED x -4500")

    assert await cash_of(db) == 500
    assert "снято" in session.texts[-1] and "4500" in session.texts[-1]


async def test_taking_more_than_there_is_empties_the_purse_and_no_more(club):
    """Кошелёк не уходит в минус: долгов в клубе нет."""
    db, owner, _ = club
    await owner.send("/givecredits x RED x 100")

    await owner.send("/givecredits x RED x -1000")

    assert await cash_of(db) == 0


async def test_a_nickname_ending_in_a_number_still_gets_its_credits(club):
    """«Боец 7» — это ник, а сумма стоит после него."""
    db, owner, _ = club
    await db.save_player(make_player(778, "Боец 7"))

    await owner.send("/givecredits Боец 7 500")

    assert await cash_of(db, 778) == 500
    assert await cash_of(db) == 0, "начислили не тому"


async def test_an_unknown_fighter_gets_no_credits(club):
    """Ника нет в клубе — говорим об этом и ничего не трогаем."""
    db, owner, session = club

    await owner.send("/givecredits Кого-то-нет 500")

    assert "нет" in session.texts[-1] and "givecredits" in session.texts[-1]
    assert await cash_of(db) == 0


async def test_a_sum_that_is_not_a_number_is_refused(club):
    """«много» — не сумма; команда объясняет, как её позвать."""
    db, owner, session = club

    await owner.send("/givecredits x RED x много")

    assert "givecredits" in session.texts[-1]
    assert await cash_of(db) == 0


async def test_a_command_without_a_sum_explains_itself(club):
    """Один ник без суммы — тоже не команда: нужны оба слова."""
    db, owner, session = club

    await owner.send("/givecredits x RED x")

    assert "givecredits" in session.texts[-1]
    assert await cash_of(db) == 0


async def test_an_empty_money_command_explains_itself(club):
    """Без имени команда объясняет, как ей пользоваться."""
    db, owner, session = club

    await owner.send("/givecredits")

    assert "givecredits" in session.texts[-1]
    assert await cash_of(db) == 0


async def test_zero_changes_nothing_and_says_so(club):
    """Ноль кредитов — не начисление: пустой ответ бойцу был бы ложью."""
    db, owner, session = club

    await owner.send("/givecredits x RED x 0")

    assert await cash_of(db) == 0
    assert "оль" in session.texts[-1]


async def test_the_grant_stays_out_of_the_paid_history(club):
    """Начисленное — не покупка: в истории оплат ему места нет."""
    db, owner, _ = club

    await owner.send("/givecredits x RED x 500")

    assert await db.purchases_of(777) == []
