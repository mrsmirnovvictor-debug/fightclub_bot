"""Отправка сообщений боя, устойчивая к лимитам Telegram.

Ход боя держится на этих сообщениях: если Telegram просит подождать, ждём
и пробуем ещё раз, а не роняем бой.

Но лучше до просьбы не доводить. Telegram считает **все** обращения к чату,
правки в том числе, и на группу даёт около двадцати в минуту. Быстрый бой
это выбирает за считаные раунды, а дальше судья замолкает на минуту прямо
посреди боя — со стороны выглядит как зависший бот.

Поэтому здесь живёт счётчик обращений на чат за последнюю минуту. Сообщения
делятся на два сорта: обязательные (без них бой не поедет) и косметические —
подсветка готовности, «судья считает». Когда запас подходит к концу,
косметические просто не отправляются: панель обновится следующим нажатием,
зато ход боя не встанет.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

logger = logging.getLogger(__name__)

# Сколько раз пробуем отправить сообщение и сколько максимум ждём
SEND_ATTEMPTS = 3
MAX_FLOOD_WAIT = 30

# Лимит Telegram на группу: обращений к одному чату за минуту
CHAT_WRITES_PER_MINUTE = 20
WINDOW = 60.0
# Половину запаса держим под обязательные сообщения. Порог сам подстраивается
# под темп боя: пока дерутся неспешно, окно пустое и подсветка идёт как
# обычно; как только раунды пошли один за другим — остаются только те
# сообщения, без которых бой не поедет.
COSMETIC_RESERVE = CHAT_WRITES_PER_MINUTE // 2


class ChatBudget:
    """Сколько обращений к чату осталось в текущей минуте."""

    def __init__(
        self, limit: int = CHAT_WRITES_PER_MINUTE, window: float = WINDOW
    ) -> None:
        self.limit = limit
        self.window = window
        self._writes: dict[int, deque[float]] = defaultdict(deque)

    def left(self, chat_id: int, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        writes = self._writes[chat_id]
        while writes and now - writes[0] >= self.window:
            writes.popleft()
        return self.limit - len(writes)

    def spend(self, chat_id: int, now: float | None = None) -> None:
        self._writes[chat_id].append(time.monotonic() if now is None else now)

    def forget(self, chat_id: int) -> None:  # pragma: no cover - уборка
        self._writes.pop(chat_id, None)


class Announcer:
    """Тот, кто говорит в ветку боя."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.budget = ChatBudget()

    def _skip_cosmetic(self, chat_id: int) -> bool:
        """Косметику режем заранее, пока запас не ушёл в ноль."""
        if self.budget.left(chat_id) > COSMETIC_RESERVE:
            return False
        logger.debug("Косметическая правка пропущена: запас чата %s кончается", chat_id)
        return True

    async def send(
        self,
        chat_id: int | None,
        thread_id: int | None,
        text: str,
        cosmetic: bool = False,
        **kwargs,
    ):
        # Чата может не быть вовсе: бой, заведённый в мини-аппе, живёт без
        # ветки, и судье там просто некому говорить вслух.
        if chat_id is None:
            return None
        if cosmetic and self._skip_cosmetic(chat_id):
            return None
        for attempt in range(SEND_ATTEMPTS):
            self.budget.spend(chat_id)
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

    async def edit(
        self,
        chat_id: int | None,
        message_id: int | None,
        text: str,
        cosmetic: bool = False,
        **kwargs,
    ):
        if chat_id is None or message_id is None:
            return None
        if cosmetic and self._skip_cosmetic(chat_id):
            return None
        self.budget.spend(chat_id)
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
