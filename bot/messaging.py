"""Отправка сообщений боя, устойчивая к лимитам Telegram.

Ход боя держится на этих сообщениях: если Telegram просит подождать, ждём
и пробуем ещё раз, а не роняем бой. Косметические правки на лимите просто
пропускаем — панель обновится следующим нажатием.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

logger = logging.getLogger(__name__)

# Сколько раз пробуем отправить сообщение и сколько максимум ждём
SEND_ATTEMPTS = 3
MAX_FLOOD_WAIT = 30


class Announcer:
    """Тот, кто говорит в ветку боя."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, chat_id: int, thread_id: int | None, text: str, **kwargs):
        for attempt in range(SEND_ATTEMPTS):
            try:
                return await self.bot.send_message(
                    chat_id, text, message_thread_id=thread_id, **kwargs
                )
            except TelegramRetryAfter as error:
                delay = min(error.retry_after, MAX_FLOOD_WAIT)
                logger.warning(
                    "Telegram просит подождать %s сек (попытка %s)", delay, attempt + 1
                )
                await asyncio.sleep(delay)
            except TelegramBadRequest as error:  # pragma: no cover - битый чат
                logger.warning("Не удалось отправить сообщение: %s", error)
                return None
        logger.error("Сообщение так и не ушло после %s попыток", SEND_ATTEMPTS)
        return None

    async def edit(self, chat_id: int, message_id: int | None, text: str, **kwargs):
        if message_id is None:
            return None
        try:
            return await self.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, **kwargs
            )
        except TelegramRetryAfter as error:
            logger.info(
                "Правка пропущена: Telegram просит подождать %s сек", error.retry_after
            )
            return None
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                logger.warning("Не удалось отредактировать сообщение: %s", error)
            return None
