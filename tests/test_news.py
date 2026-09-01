"""Доска объявлений: что бот сам приносит игрокам и почему только один раз."""

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from bot.game.changelog import RELEASES, Release
from bot.news_service import announce, catch_up, pending, publish_pending

CHAT = -1001
THREAD = 42

def _probe() -> SendMessage:
    """Метод-заглушка: aiogram требует его для своих исключений."""
    return SendMessage(chat_id=CHAT, text="")


ONE = Release(code="test-one", title="Первое", lines=("одна строка",))
TWO = Release(code="test-two", title="Второе", lines=("другая строка",))


class Poster:
    """Бот, который помнит, куда писал и что закрывал."""

    def __init__(self, closed_topic: bool = False) -> None:
        self.sent: list[tuple[int, int | None, str]] = []
        self.closed: list[int] = []
        self.reopened: list[int] = []
        self.closed_topic = closed_topic

    async def send_message(self, chat_id, text, message_thread_id=None, **kwargs):
        if self.closed_topic:
            self.closed_topic = False
            raise TelegramBadRequest(method=_probe(), message="TOPIC_CLOSED")
        self.sent.append((chat_id, message_thread_id, text))
        return object()

    async def close_forum_topic(self, chat_id, message_thread_id):
        self.closed.append(message_thread_id)

    async def reopen_forum_topic(self, chat_id, message_thread_id):
        self.reopened.append(message_thread_id)


# ---------- сам список изменений ----------


def test_the_changelog_speaks_to_players_not_to_developers():
    """В объявлениях нет ни файлов, ни функций, ни процентов из кода."""
    banned = ("рефактор", "коммит", "тест", "функци", ".py", "класс Fighter")
    for release in RELEASES:
        body = release.render().lower()
        assert release.code and release.title
        assert release.lines
        for word in banned:
            assert word not in body, f"{release.code}: техника в тексте — {word}"


def test_the_changelog_never_tells_the_group_to_type_a_command():
    """Команды слушает личка бота: в ветке группы они не сработают.

    «Проверить своё — /card» в новостях выглядит как подсказка, а на деле
    отправляет человека нажимать то, на что здесь никто не ответит.
    """
    for release in RELEASES:
        body = release.render()
        assert "/" not in body.replace("</b>", "").replace("<b>", ""), (
            f"{release.code}: в объявлении осталась команда"
        )


def test_every_release_code_is_unique():
    """Код — ключ отправленного: два одинаковых означали бы потерянное объявление."""
    codes = [release.code for release in RELEASES]
    assert len(codes) == len(set(codes))


# ---------- объявление ----------


async def test_the_announcement_lands_and_the_topic_gets_closed(db):
    bot = Poster()
    await db.set_noticeboard(CHAT, THREAD)

    posted = await announce(bot, db, CHAT, THREAD, (ONE,))

    assert [release.code for release in posted] == ["test-one"]
    chat_id, thread_id, text = bot.sent[0]
    assert (chat_id, thread_id) == (CHAT, THREAD)
    assert "Первое" in text and "одна строка" in text
    assert bot.closed == [THREAD]  # обсуждать тут нечего


async def test_the_same_change_is_never_announced_twice(db):
    """Перезапуск бота не заваливает ветку повторами."""
    bot = Poster()
    await db.set_noticeboard(CHAT, THREAD)
    await announce(bot, db, CHAT, THREAD, (ONE,))

    assert ONE not in await pending(db, CHAT)
    assert await publish_pending(bot, db) == len(RELEASES)  # только настоящие
    assert not any("Первое" in text for _, _, text in bot.sent[1:])


async def test_a_closed_topic_is_opened_for_one_message_and_closed_again(db):
    """Ветка закрыта с прошлого раза — бот открывает её ровно на объявление."""
    bot = Poster(closed_topic=True)
    await db.set_noticeboard(CHAT, THREAD)

    await announce(bot, db, CHAT, THREAD, (ONE,))

    assert bot.reopened == [THREAD]
    assert len(bot.sent) == 1
    assert bot.closed == [THREAD]


async def test_a_failed_announcement_is_tried_again_next_time(db):
    """Не ушло — не помечаем отправленным, иначе изменение потеряется молча."""

    class Broken(Poster):
        async def send_message(self, *args, **kwargs):
            raise TelegramBadRequest(
                method=_probe(), message="Bad Request: chat not found"
            )

    await db.set_noticeboard(CHAT, THREAD)
    assert await announce(Broken(), db, CHAT, THREAD, (ONE,)) == []
    assert "test-one" not in await db.announced(CHAT)

    bot = Poster()
    assert await announce(bot, db, CHAT, THREAD, (ONE,)) == [ONE]


async def test_the_second_announcement_does_not_drag_the_first_along(db):
    """Упало первое — второе не уходит: порядок объявлений не ломаем."""

    class OnlyOnce(Poster):
        async def send_message(self, chat_id, text, message_thread_id=None, **kwargs):
            if "Второе" in text:
                raise TelegramBadRequest(method=_probe(), message="Bad Request: flood")
            return await super().send_message(chat_id, text, message_thread_id)

    await db.set_noticeboard(CHAT, THREAD)
    posted = await announce(OnlyOnce(), db, CHAT, THREAD, (ONE, TWO))

    assert [release.code for release in posted] == ["test-one"]
    assert "test-two" not in await db.announced(CHAT)


# ---------- обход всех групп ----------


async def test_only_marked_groups_get_the_news(db):
    """Без /updates бот в группу ничего не носит."""
    bot = Poster()
    assert await publish_pending(bot, db) == 0
    assert bot.sent == []


async def test_a_fresh_board_gets_the_last_change_not_the_whole_archive(db):
    """Новую ветку не заваливаем историей: показываем последнее изменение."""
    bot = Poster()
    await db.set_noticeboard(CHAT, THREAD)

    shown = await catch_up(bot, db, CHAT, THREAD)

    assert shown == 1
    assert len(bot.sent) == 1
    assert RELEASES[-1].title in bot.sent[0][2]
    # а всё остальное считается прочитанным и заново не всплывёт
    assert await pending(db, CHAT) == ()


async def test_a_chat_without_topics_is_not_closed(db):
    """В обычном чате закрывать нечего — объявление просто выходит."""
    bot = Poster()
    await db.set_noticeboard(CHAT, None)

    await announce(bot, db, CHAT, None, (ONE,))

    assert bot.sent[0][1] is None
    assert bot.closed == []


@pytest.mark.parametrize("code", [release.code for release in RELEASES])
async def test_every_real_release_renders(code, db):
    """Каждая запись доходит до ветки целиком, с заголовком и строками."""
    from bot.game.changelog import by_code

    bot = Poster()
    release = by_code(code)
    await announce(bot, db, CHAT, THREAD, (release,))

    text = bot.sent[0][2]
    assert release.title in text
    assert all(line in text for line in release.lines)
