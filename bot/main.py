"""Точка входа: поднимает бота, базу и судейский сервис."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)

from bot.config import Config, load_config
from bot.database import Database
from bot.duel_service import DuelService
from bot.handlers import build_router

logger = logging.getLogger(__name__)

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Создать бойца"),
    BotCommand(command="profile", description="Карточка бойца"),
    BotCommand(command="upgrade", description="Раскидать свободные очки"),
    BotCommand(command="classes", description="Описание классов"),
    BotCommand(command="top", description="Чемпионы клуба"),
    BotCommand(command="reset", description="Начать заново"),
    BotCommand(command="help", description="Как всё устроено"),
]

GROUP_COMMANDS = [
    BotCommand(command="duel", description="Бросить вызов на кулаках"),
    BotCommand(command="arena", description="Отметить ветку как ринг (админы)"),
    BotCommand(command="giveup", description="Сдаться в текущем бою"),
    BotCommand(command="top", description="Чемпионы клуба"),
    BotCommand(command="history", description="Последние бои"),
    BotCommand(command="help", description="Как всё устроено"),
]


async def setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())


async def run(config: Config | None = None) -> None:
    config = config or load_config()
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    db = Database(config.db_path)
    await db.connect()
    duels = DuelService(bot=bot, db=db, config=config)

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["db"] = db
    dispatcher["duels"] = duels
    dispatcher["config"] = config
    dispatcher.include_router(build_router())

    await setup_commands(bot)
    me = await bot.get_me()
    logger.info("Клуб открыт: @%s", me.username)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await duels.shutdown()
        await db.close()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Клуб закрывается.")


if __name__ == "__main__":
    main()
