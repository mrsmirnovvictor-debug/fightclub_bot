"""Точка входа: поднимает бота, базу и судейский сервис."""

from __future__ import annotations

import asyncio
import logging
import sys

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
from bot.game.links import links
from bot.handlers import build_router
from bot.webapp import run_webapp

logger = logging.getLogger(__name__)

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Создать бойца"),
    BotCommand(command="card", description="Карточка бойца"),
    BotCommand(command="profile", description="Профиль текстом"),
    BotCommand(command="upgrade", description="Раскидать свободные очки"),
    BotCommand(command="shop", description="Кредиты и траты"),
    BotCommand(command="buy", description="Лавка клуба: оружие и броня"),
    BotCommand(command="respec", description="Пересобрать характеристики"),
    BotCommand(command="class", description="Сменить класс"),
    BotCommand(command="rename", description="Сменить прозвище"),
    BotCommand(command="avatar", description="Сменить аватарку"),
    BotCommand(command="classes", description="Описание классов"),
    BotCommand(command="top", description="Чемпионы клуба"),
    BotCommand(command="reset", description="Начать заново"),
    BotCommand(command="help", description="Как всё устроено"),
]

GROUP_COMMANDS = [
    BotCommand(command="duel", description="Вызов на кулаках"),
    BotCommand(command="fight", description="Вызов с оружием"),
    BotCommand(command="card", description="Карточка бойца"),
    BotCommand(command="rings", description="Ринги клуба и что свободно"),
    BotCommand(command="arena1", description="Отметить кулачный ринг (админы)"),
    BotCommand(command="arena_gear", description="Отметить ринг с оружием (админы)"),
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
    links.configure(me.username or "", config.miniapp_name)
    if config.miniapp_name and not config.webapp_enabled:
        logger.warning(
            "MINIAPP_NAME задан, а WEBAPP_URL — нет: ссылки на карточку никуда не ведут"
        )
    logger.info(
        "Клуб открыт: @%s, карточка %s",
        me.username,
        "включена" if config.webapp_enabled else "выключена",
    )

    runner = (
        await run_webapp(bot, db, config, duels) if config.webapp_enabled else None
    )
    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await duels.shutdown()
        if runner is not None:
            await runner.cleanup()
        await db.close()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        # По умолчанию logging пишет в stderr, и хостинги красят весь лог
        # в красное. Обычные сообщения должны идти в stdout.
        stream=sys.stdout,
    )
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Клуб закрывается.")


if __name__ == "__main__":
    main()
