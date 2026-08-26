"""HTTP-сервер мини-аппа: страница карточки, её данные и аватары."""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from bot.config import Config
from bot.database import Database
from bot.webapp.auth import AuthError, check_avatar_token, parse_init_data
from bot.webapp.card import build_card

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Типизированные ключи приложения — так рекомендует aiohttp
BOT_KEY: web.AppKey = web.AppKey("bot")
DB_KEY: web.AppKey[Database] = web.AppKey("db", Database)
CONFIG_KEY: web.AppKey[Config] = web.AppKey("config", Config)
# Кто может смотреть чужие карточки — все: клуб маленький, прятать нечего
INIT_DATA_HEADER = "X-Telegram-Init-Data"


def _init_data(request: web.Request) -> str:
    header = request.headers.get(INIT_DATA_HEADER, "")
    if header:
        return header
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("tma "):
        return authorization[4:]
    return request.query.get("initData", "")


async def _viewer(request: web.Request):
    config = request.app[CONFIG_KEY]
    try:
        return parse_init_data(_init_data(request), config.bot_token)
    except AuthError as error:
        raise web.HTTPUnauthorized(
            text=str(error), content_type="text/plain"
        ) from error


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "card.html")


async def api_card(request: web.Request) -> web.Response:
    viewer = await _viewer(request)
    db = request.app[DB_KEY]
    config = request.app[CONFIG_KEY]

    target_id = viewer.user_id
    requested = request.query.get("user_id") or viewer.start_param
    if requested and requested.lstrip("-").isdigit():
        target_id = int(requested)

    player = await db.get_player(target_id)
    if player is None:
        return web.json_response(
            {
                "error": "no_character",
                "is_self": target_id == viewer.user_id,
                "message": "У этого бойца ещё нет персонажа.",
            },
            status=404,
        )
    return web.json_response(build_card(player, config.bot_token, viewer.user_id))


async def avatar(request: web.Request) -> web.StreamResponse:
    """Отдать фото бойца. Ссылка подписана и живёт час."""
    config = request.app[CONFIG_KEY]
    db = request.app[DB_KEY]
    bot = request.app[BOT_KEY]

    try:
        user_id = int(request.match_info["user_id"])
        expires = int(request.query.get("expires", "0"))
    except ValueError as error:
        raise web.HTTPBadRequest(text="плохая ссылка") from error

    token = request.query.get("token", "")
    if not check_avatar_token(user_id, config.bot_token, expires, token):
        raise web.HTTPForbidden(text="ссылка просрочена")

    player = await db.get_player(user_id)
    if player is None or not player.avatar_file_id:
        raise web.HTTPNotFound(text="аватара нет")

    file = await bot.get_file(player.avatar_file_id)
    stream = await bot.download_file(file.file_path)
    return web.Response(
        body=stream.read(),
        content_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app(bot, db: Database, config: Config) -> web.Application:
    app = web.Application()
    app[BOT_KEY] = bot
    app[DB_KEY] = db
    app[CONFIG_KEY] = config
    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/card", api_card),
            web.get("/avatar/{user_id}", avatar),
            web.get("/healthz", healthz),
            web.static("/static", STATIC_DIR),
        ]
    )
    return app


async def run_webapp(bot, db: Database, config: Config) -> web.AppRunner:
    """Поднять сервер мини-аппа рядом с ботом. Вернуть runner для остановки."""
    runner = web.AppRunner(create_app(bot, db, config))
    await runner.setup()
    site = web.TCPSite(runner, config.webapp_host, config.webapp_port)
    await site.start()
    logger.info(
        "Мини-апп слушает %s:%s, снаружи ожидается %s",
        config.webapp_host,
        config.webapp_port,
        config.webapp_url,
    )
    return runner
