"""Доска объявлений клуба: бот сам приносит туда, что изменилось.

Ветка отмечается командой /updates, дальше бот справляется без людей: при
каждом запуске он сверяет список изменений с тем, что уже отправлял в эту
группу, и публикует только новое. Перезапуск ничего не задваивает.

Ветка новостей — не место для разговора, поэтому после объявления бот её
закрывает. Отвечать там некому: обсуждают в общем чате, а здесь только
читают. Закрытая ветка не мешает боту писать в неё дальше — у него есть
права на управление темами; если Telegram всё же откажет, бот откроет
ветку, напишет и закроет снова.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.database import Database
from bot.game.changelog import RELEASES, Release

logger = logging.getLogger(__name__)


class NewsError(Exception):
    """Не удалось объявить — с текстом, который не стыдно показать человеку."""


async def _post(bot: Bot, chat_id: int, thread_id: int | None, text: str):
    """Написать в ветку, при необходимости открыв её на одно сообщение."""
    try:
        return await bot.send_message(
            chat_id, text, message_thread_id=thread_id, disable_web_page_preview=True
        )
    except TelegramAPIError as error:
        if thread_id is None or "TOPIC_CLOSED" not in str(error).upper():
            raise
        logger.info("Ветка новостей закрыта — открываем на одно объявление")
        await bot.reopen_forum_topic(chat_id, thread_id)
        return await bot.send_message(
            chat_id, text, message_thread_id=thread_id, disable_web_page_preview=True
        )


async def _close(bot: Bot, chat_id: int, thread_id: int | None) -> bool:
    """Закрыть ветку для ответов. В общем чате закрывать нечего."""
    if thread_id is None:
        return False
    try:
        await bot.close_forum_topic(chat_id, thread_id)
        return True
    except TelegramAPIError as error:
        # Объявление уже вышло — молчать об этом нельзя, но и падать незачем
        logger.warning("Не удалось закрыть ветку новостей: %s", error)
        return False


async def announce(
    bot: Bot,
    db: Database,
    chat_id: int,
    thread_id: int | None,
    releases: tuple[Release, ...],
) -> list[Release]:
    """Объявить эти изменения в ветке и закрыть её. Вернуть, что вышло.

    Помечаем объявление отправленным только после того, как оно ушло:
    сорвалась отправка — в следующий раз попробуем снова.
    """
    posted: list[Release] = []
    for release in releases:
        try:
            await _post(bot, chat_id, thread_id, release.render())
        except TelegramAPIError as error:
            logger.warning("Объявление %s не ушло: %s", release.code, error)
            break
        await db.mark_announced(chat_id, release.code)
        posted.append(release)
        logger.info("Объявлено в %s: %s", chat_id, release.code)

    if posted:
        await _close(bot, chat_id, thread_id)
    return posted


async def pending(db: Database, chat_id: int) -> tuple[Release, ...]:
    """Что этой группе ещё не объявляли — по порядку."""
    done = await db.announced(chat_id)
    return tuple(release for release in RELEASES if release.code not in done)


async def publish_pending(bot: Bot, db: Database) -> int:
    """Обойти доски всех групп и объявить новое. Вернуть число объявлений."""
    total = 0
    for chat_id, thread_id, _ in await db.list_noticeboards():
        fresh = await pending(db, chat_id)
        if not fresh:
            continue
        posted = await announce(bot, db, chat_id, thread_id, fresh)
        total += len(posted)
    if total:
        logger.info("Свежих объявлений разослано: %s", total)
    return total


async def catch_up(bot: Bot, db: Database, chat_id: int, thread_id: int | None) -> int:
    """Новая доска объявлений: показать последнее изменение, не весь архив.

    Вываливать в свежую ветку всю историю клуба незачем — читать это никто
    не станет. Остальное просто помечаем объявленным.
    """
    fresh = await pending(db, chat_id)
    if not fresh:
        return 0
    for release in fresh[:-1]:
        await db.mark_announced(chat_id, release.code)
    return len(await announce(bot, db, chat_id, thread_id, fresh[-1:]))
