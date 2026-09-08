"""Запас обращений к чату: почему судья перестал замолкать посреди боя.

Telegram считает всё, что бот делает с чатом, — правки в том числе, — и на
группу даёт около двадцати обращений в минуту. Раньше раунд стоил четыре, и
на пятом раунде бой замирал на минуту. Тесты держат обе половины починки:
сам счётчик и цену раунда.
"""

import pytest

from bot.messaging import (
    CHAT_WRITES_PER_MINUTE,
    Announcer,
    ChatBudget,
)

CHAT = -1001


class Recorder:
    """Бот, который только считает обращения."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[str] = []

    async def send_message(self, chat_id, text, message_thread_id=None, **kwargs):
        self.sent.append(text)
        return object()

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self.edited.append(text)
        return object()


# ---------- счётчик ----------


def test_the_window_forgets_what_happened_a_minute_ago():
    budget = ChatBudget(limit=3, window=60.0)

    for moment in (0.0, 1.0, 2.0):
        budget.spend(CHAT, now=moment)
    assert budget.left(CHAT, now=2.0) == 0

    # минута прошла — первые два обращения уже не в счёт
    assert budget.left(CHAT, now=61.5) == 2


def test_chats_do_not_share_a_budget():
    budget = ChatBudget(limit=2)
    budget.spend(CHAT)
    budget.spend(CHAT)

    assert budget.left(CHAT) == 0
    assert budget.left(-2002) == 2


# ---------- косметика уступает дорогу ----------


async def test_cosmetics_stop_when_the_budget_runs_low():
    voice = Announcer(Recorder())
    for _ in range(CHAT_WRITES_PER_MINUTE // 2 + 1):
        await voice.send(CHAT, None, "обязательное")

    assert await voice.edit(CHAT, 5, "подсветка", cosmetic=True) is None
    assert await voice.send(CHAT, None, "подсветка", cosmetic=True) is None
    assert voice.bot.edited == []


async def test_the_essential_message_goes_even_on_an_empty_budget():
    """Бой не встаёт из-за лимита: обязательное уходит всегда."""
    voice = Announcer(Recorder())
    for _ in range(CHAT_WRITES_PER_MINUTE * 2):
        await voice.send(CHAT, None, "обязательное")

    assert voice.budget.left(CHAT) < 0
    await voice.send(CHAT, None, "итог раунда")
    await voice.edit(CHAT, 5, "итог раунда")

    assert voice.bot.sent[-1] == "итог раунда"
    assert voice.bot.edited == ["итог раунда"]


async def test_a_relaxed_chat_still_gets_its_cosmetics():
    voice = Announcer(Recorder())
    await voice.send(CHAT, None, "первое")

    await voice.edit(CHAT, 5, "подсветка", cosmetic=True)

    assert voice.bot.edited == ["подсветка"]


# ---------- цена раунда ----------


@pytest.fixture
def duel_bits(db):
    from tests.test_duel_flow import (
        CHAT_ID,
        FakeBot,
        THREAD_ID,
        make_player,
        make_service,
        play_round,
    )

    return CHAT_ID, THREAD_ID, FakeBot, make_player, make_service, play_round


async def test_a_duel_round_costs_the_chat_no_more_than_three_writes(db, duel_bits):
    """Пауза между 4 и 5 раундом была здесь: раунд стоил четыре обращения.

    Двадцать обращений в минуту при четырёх за раунд кончались ровно на
    пятом. Теперь итог раунда встаёт на место панели, а подсветка готовности
    уходит первой, когда запас на исходе.
    """
    chat_id, thread_id, FakeBot, make_player, make_service, play_round = duel_bits
    bot = FakeBot()
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "tank"))
    await db.save_player(make_player(2, "Марла", "tank"))
    session = await service.start_duel(
        chat_id, thread_id, await db.get_player(1), await db.get_player(2)
    )

    rounds = 0
    before = len(bot.said)
    while service.duel_in_chat(chat_id, thread_id) is not None and rounds < 6:
        await play_round(service, session)
        rounds += 1

    assert rounds >= 5, "бой кончился раньше, чем дошёл до спорного места"
    assert (len(bot.said) - before) / rounds <= 3

    # и главное: пять раундов укладываются в минутный лимит чата
    assert len(bot.said) <= CHAT_WRITES_PER_MINUTE


async def test_the_round_result_takes_the_place_of_its_own_panel(db, duel_bits):
    """Итог раунда — это та же панель, только без кнопок."""
    chat_id, thread_id, FakeBot, make_player, make_service, play_round = duel_bits
    bot = FakeBot()
    service = make_service(bot, db)
    await db.save_player(make_player(1, "Тайлер", "warrior"))
    await db.save_player(make_player(2, "Марла", "warrior"))
    session = await service.start_duel(
        chat_id, thread_id, await db.get_player(1), await db.get_player(2)
    )
    panel_id = session.prompt_message_id

    await play_round(service, session)

    # последняя правка панели — та, что гасит её итогом раунда
    closed = [m for m in bot.edits if m.message_id == panel_id][-1]
    assert closed.text.startswith("<b>⚔️ Раунд 1, удар 1</b>")
    assert closed.reply_markup.inline_keyboard == []  # ходить в нём уже нельзя
    assert not any("ставки сделаны" in text for text in bot.log)


async def test_a_group_round_no_longer_repaints_the_panel_on_every_press(db):
    """Шестеро в бою — это дюжина нажатий за раунд, и раньше дюжина правок.

    Групповой бой упирался в лимит чата на первом же раунде: панель
    перерисовывалась после каждого нажатия. Теперь подсветка уступает дорогу.
    """
    from bot.game.battle import BLUE, RED, BattleKind
    from tests.test_battle_flow import CHAT_ID, THREAD_ID, choose_all, fill
    from tests.test_battle_flow import make_service as make_battles
    from tests.test_duel_flow import FakeBot

    bot = FakeBot()
    service = make_battles(bot, db)
    players = await fill(db, 6)
    lobby = await service.open_lobby(
        CHAT_ID, THREAD_ID, players[0], BattleKind.TEAM, size=3
    )
    for player, team in zip(players[1:], (RED, RED, BLUE, BLUE, BLUE)):
        await service.join(lobby.id, player, team)
    session = service.battle_in_chat(CHAT_ID, THREAD_ID)

    before, rounds = len(bot.said), 0
    while service.battle_in_chat(CHAT_ID, THREAD_ID) is not None and rounds < 4:
        await choose_all(service, session)
        rounds += 1
    await service.shutdown()

    spent = len(bot.said) - before
    assert rounds == 4
    assert spent <= 4 + 2 * rounds, f"раунд всё ещё дорогой: {spent} обращений"
