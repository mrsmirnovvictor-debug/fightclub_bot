"""Общий стенд для тестов: один Dispatcher aiogram с подменённой сессией.

Роутеры aiogram объявлены на уровне модулей и могут быть прикреплены только к
одному диспетчеру, поэтому бот и диспетчер здесь — единственные на весь прогон.
Чтобы тесты не мешали друг другу, каждому выдаётся свой пользователь, а база
подменяется в workflow-данных перед каждым тестом.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import TelegramMethod
from aiogram.types import CallbackQuery, Chat, ChatMemberOwner, Message, Update, User

from bot.handlers import build_router

TOKEN = "42:TESTTOKEN"
ids = itertools.count(1)
user_ids = itertools.count(5001)

MESSAGE_METHODS = {"SendMessage", "EditMessageText", "SendPhoto"}


class FakeSession(BaseSession):
    """Перехватывает вызовы Bot API и отдаёт правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._message_id = 1000

    async def close(self) -> None:  # pragma: no cover - интерфейс BaseSession
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout=None) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name == "GetChatMember":
            return ChatMemberOwner(
                user=User(id=method.user_id, is_bot=False, first_name="Админ"),
                is_anonymous=False,
                status="creator",
            )
        if name in MESSAGE_METHODS:
            self._message_id += 1
            chat_id = getattr(method, "chat_id", 0) or 0
            return Message(
                message_id=self._message_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
                text=getattr(method, "text", None) or getattr(method, "caption", "фото"),
            ).as_(bot)
        return True

    def method_calls(self, name: str) -> list[TelegramMethod]:
        return [call for call in self.calls if type(call).__name__ == name]

    @property
    def texts(self) -> list[str]:
        return [
            getattr(call, "text", "") or getattr(call, "caption", "")
            for call in self.calls
            if type(call).__name__ in MESSAGE_METHODS
        ]

    @property
    def alerts(self) -> list[str]:
        return [call.text or "" for call in self.method_calls("AnswerCallbackQuery")]


SESSION = FakeSession()
BOT = Bot(
    token=TOKEN,
    session=SESSION,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
DISPATCHER = Dispatcher(storage=MemoryStorage())
DISPATCHER.include_router(build_router())


def new_user(name: str = "Тайлер") -> User:
    return User(id=next(user_ids), is_bot=False, first_name=name)


def private_chat(user: User) -> Chat:
    return Chat(id=user.id, type="private")


def make_message(chat: Chat, text: str, user: User | None = None, **kwargs) -> Message:
    return Message(
        message_id=next(ids),
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
        **kwargs,
    )


async def feed_message(message: Message) -> None:
    await DISPATCHER.feed_update(BOT, Update(update_id=next(ids), message=message))


async def feed_callback(user: User, chat: Chat, data: str, **message_kwargs) -> None:
    callback = CallbackQuery(
        id=str(next(ids)),
        from_user=user,
        chat_instance="test",
        message=make_message(chat, "кнопка", **message_kwargs),
        data=data,
    )
    await DISPATCHER.feed_update(
        BOT, Update(update_id=next(ids), callback_query=callback)
    )
