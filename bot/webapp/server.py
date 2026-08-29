"""HTTP-сервер мини-аппа: страница карточки, её данные и аватары."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from aiohttp import web

from bot.config import Config
from bot.database import Database
from bot.game.equipment import Slot
from bot.game.potions import get_potion
from bot.inventory_service import (
    InventoryError,
    buy,
    equip,
    repair_item,
    unequip,
)
from bot.looks_service import LookError, choose_look, wardrobe
from bot.potions_service import PotionError, buy_potion, use_potion
from bot.pro_service import ProError, claim_free_pro
from bot.store_service import StoreError, StoreService
from bot.webapp.auth import AuthError, check_avatar_token, parse_init_data
from bot.webapp.card import (
    build_card,
    build_club,
    build_magic,
    build_shop,
    build_topup,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Файлы, из которых собирается метка версии страницы
ASSETS = ("card.css", "card.js")

# Типизированные ключи приложения — так рекомендует aiohttp
BOT_KEY: web.AppKey = web.AppKey("bot")
DB_KEY: web.AppKey[Database] = web.AppKey("db", Database)
CONFIG_KEY: web.AppKey[Config] = web.AppKey("config", Config)
# Метка версии статики: без неё Telegram показывает страницу из кеша
STAMP_KEY: web.AppKey[str] = web.AppKey("stamp", str)
# Сервис дуэлей нужен, чтобы не давать переодеваться посреди боя
DUELS_KEY: web.AppKey = web.AppKey("duels")
# Касса: выставляет счета в звёздах для кнопки «+» рядом с кредитами
STORE_KEY: web.AppKey = web.AppKey("store")
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


def asset_stamp() -> str:
    """Короткая метка версии: меняется, как только меняется стиль или скрипт."""
    digest = hashlib.sha1()
    for name in ASSETS:
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:8]


def stamped_page(stamp: str) -> str:
    """Страница со ссылками вида static/card.css?v=метка.

    Telegram держит мини-апп в вебвью и кеширует стили со скриптами намертво:
    после выката человек открывает карточку и видит вчерашнюю вёрстку, а в
    логах сервера — только запрос к API. Метка в адресе делает файл новым, и
    вебвью идёт за ним заново.
    """
    html = (STATIC_DIR / "card.html").read_text(encoding="utf-8")
    for name in ASSETS:
        html = html.replace(f"static/{name}", f"static/{name}?v={stamp}")
    return html


async def index(request: web.Request) -> web.Response:
    return web.Response(
        text=stamped_page(request.app[STAMP_KEY]),
        content_type="text/html",
        charset="utf-8",
        # Саму страницу не кешируем: иначе новые метки до вебвью не доедут
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


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


async def _own_player(request: web.Request):
    """Действия с вещами доступны только хозяину карточки и только вне боя."""
    viewer = await _viewer(request)
    player = await request.app[DB_KEY].get_player(viewer.user_id)
    if player is None:
        raise web.HTTPNotFound(text="У тебя ещё нет персонажа")
    duels = request.app.get(DUELS_KEY)
    if duels is not None and duels.duel_of_user(player.user_id) is not None:
        raise InventoryError("Ты на ринге — переодеваться поздно.")
    return player


async def _payload(request: web.Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _card_response(request: web.Request, player) -> web.Response:
    config = request.app[CONFIG_KEY]
    return web.json_response(build_card(player, config.bot_token, player.user_id))


def _int_field(data: dict, name: str) -> int:
    try:
        return int(data.get(name))
    except (TypeError, ValueError) as error:
        raise web.HTTPBadRequest(text=f"нет поля {name}") from error


def _slot_field(data: dict, name: str) -> Slot | None:
    raw = data.get(name)
    if not raw:
        return None
    try:
        return Slot(str(raw))
    except ValueError as error:
        raise web.HTTPBadRequest(text="неизвестный слот") from error


async def api_equip(request: web.Request) -> web.Response:
    """Надеть вещь из инвентаря."""
    data = await _payload(request)
    try:
        player = await _own_player(request)
        await equip(
            request.app[DB_KEY],
            player,
            _int_field(data, "item_id"),
            _slot_field(data, "slot"),
        )
    except InventoryError as error:
        return web.json_response({"error": str(error)}, status=409)
    return _card_response(request, player)


async def api_unequip(request: web.Request) -> web.Response:
    """Снять вещь со слота — она вернётся в инвентарь."""
    data = await _payload(request)
    slot = _slot_field(data, "slot")
    if slot is None:
        raise web.HTTPBadRequest(text="нет поля slot")
    try:
        player = await _own_player(request)
        await unequip(request.app[DB_KEY], player, slot)
    except InventoryError as error:
        return web.json_response({"error": str(error)}, status=409)
    return _card_response(request, player)


async def api_repair(request: web.Request) -> web.Response:
    """Починить вещь за кредиты."""
    data = await _payload(request)
    points = None if data.get("points") is None else _int_field(data, "points")
    try:
        player = await _own_player(request)
        result = await repair_item(
            request.app[DB_KEY], player, _int_field(data, "item_id"), points
        )
    except InventoryError as error:
        return web.json_response({"error": str(error)}, status=409)

    config = request.app[CONFIG_KEY]
    return web.json_response(
        {
            "card": build_card(player, config.bot_token, player.user_id),
            "repair": {
                "points": result.points,
                "price": result.price,
                "degraded": result.degraded,
                "destroyed": result.destroyed,
            },
        }
    )


async def api_shop(request: web.Request) -> web.Response:
    """Витрина: что продаётся, что уже открыто и что по карману."""
    try:
        player = await _own_player(request)
    except InventoryError as error:  # pragma: no cover - витрина боем не занята
        return web.json_response({"error": str(error)}, status=409)
    return web.json_response(build_shop(player))


async def api_buy(request: web.Request) -> web.Response:
    """Купить вещь: она уходит в инвентарь, надевать — в карточке."""
    data = await _payload(request)
    code = str(data.get("code") or "")
    potion = get_potion(code)
    try:
        player = await _own_player(request)
        if potion is not None:
            await buy_potion(request.app[DB_KEY], player, code)
            # Склянку не надевают — её пьют, поэтому и подсказка другая
            bought = {
                "code": potion.code,
                "title": potion.title,
                "price": potion.price,
                "can_equip": False,
                "consumable": True,
            }
        else:
            item = await buy(request.app[DB_KEY], player, code)
            bought = {
                "code": item.code,
                "title": item.title,
                "price": item.item.price,
                "can_equip": player.can_equip(item.item),
                "consumable": False,
            }
    except (InventoryError, PotionError) as error:
        return web.json_response({"error": str(error)}, status=409)

    config = request.app[CONFIG_KEY]
    return web.json_response(
        {
            "shop": build_shop(player),
            "card": build_card(player, config.bot_token, player.user_id),
            "bought": bought,
        }
    )


async def api_use(request: web.Request) -> web.Response:
    """Выпить эликсир из рюкзака."""
    data = await _payload(request)
    code = str(data.get("code") or "")
    try:
        player = await _own_player(request)
        result = await use_potion(request.app[DB_KEY], player, code)
    except (InventoryError, PotionError) as error:
        return web.json_response({"error": str(error)}, status=409)

    config = request.app[CONFIG_KEY]
    return web.json_response(
        {
            "card": build_card(player, config.bot_token, player.user_id),
            "used": {
                "code": result.potion.code,
                "title": result.potion.title,
                "healed": result.healed,
                "extended": result.extended,
                "seconds_left": result.seconds_left(),
                "left": result.left,
            },
        }
    )


async def api_magic(request: web.Request) -> web.Response:
    """Лавка мага: товар только за звёзды."""
    try:
        player = await _own_player(request)
    except InventoryError as error:  # pragma: no cover - лавка боем не занята
        return web.json_response({"error": str(error)}, status=409)
    return web.json_response(build_magic(player))


async def api_pro(request: web.Request) -> web.Response:
    """Забрать подписку по акции. Идёт ли акция — решает сервер, не страница."""
    try:
        player = await _own_player(request)
        grant = await claim_free_pro(request.app[DB_KEY], player)
    except (InventoryError, ProError) as error:
        return web.json_response({"error": str(error)}, status=409)

    config = request.app[CONFIG_KEY]
    return web.json_response(
        {
            "card": build_card(player, config.bot_token, player.user_id),
            "magic": build_magic(player),
            "pro": {
                "days": grant.offer.days,
                "renewed": grant.renewed,
                "blade": grant.blade,
                "look": grant.look,
                "seconds_left": grant.seconds_left(),
            },
        }
    )


async def api_club(request: web.Request) -> web.Response:
    """Список клуба: все записанные бойцы с короткими карточками."""
    viewer = await _viewer(request)
    players = await request.app[DB_KEY].all_players()
    return web.json_response(build_club(players, viewer.user_id))


async def api_looks(request: web.Request) -> web.Response:
    """Гардероб: все образы, какой надет и что уже куплено."""
    try:
        player = await _own_player(request)
    except InventoryError as error:  # pragma: no cover - гардероб боем не занят
        return web.json_response({"error": str(error)}, status=409)
    return web.json_response(
        {"looks": await wardrobe(request.app[DB_KEY], player), "credits": player.credits}
    )


async def api_look(request: web.Request) -> web.Response:
    """Сменить образ. Платный купится, если кредитов хватает."""
    data = await _payload(request)
    try:
        player = await _own_player(request)
        choice = await choose_look(
            request.app[DB_KEY], player, str(data.get("code") or "")
        )
    except (InventoryError, LookError) as error:
        return web.json_response({"error": str(error)}, status=409)

    config = request.app[CONFIG_KEY]
    return web.json_response(
        {
            "card": build_card(player, config.bot_token, player.user_id),
            "looks": await wardrobe(request.app[DB_KEY], player),
            "chosen": {
                "code": choice.look.code,
                "title": choice.look.title,
                "bought": choice.bought,
                "price": choice.look.price,
                "credits": choice.credits,
            },
        }
    )


async def api_topup(request: web.Request) -> web.Response:
    """Касса: сколько на счету и какие пачки кредитов есть."""
    viewer = await _viewer(request)
    player = await request.app[DB_KEY].get_player(viewer.user_id)
    if player is None:
        raise web.HTTPNotFound(text="У тебя ещё нет персонажа")
    return web.json_response(build_topup(player, request.app.get(STORE_KEY) is not None))


async def api_invoice(request: web.Request) -> web.Response:
    """Счёт на пачку кредитов: мини-апп открывает ссылку через openInvoice."""
    viewer = await _viewer(request)
    store: StoreService | None = request.app.get(STORE_KEY)
    if store is None:
        return web.json_response({"error": "Касса закрыта."}, status=503)

    data = await _payload(request)
    # Касса и лавка мага ходят в одну ручку, различаясь только видом товара
    kind = str(data.get("kind") or "pack")
    try:
        goods = store.check(f"{kind}:{data.get('code') or 'month'}")
        link = await store.invoice_link(goods, viewer.user_id)
    except StoreError as error:
        return web.json_response({"error": str(error)}, status=409)
    except Exception:  # pragma: no cover - Telegram не ответил
        logger.exception("Не удалось выставить счёт")
        return web.json_response(
            {"error": "Касса не отвечает. Попробуй ещё раз."}, status=502
        )
    return web.json_response(
        {
            "link": link,
            "code": getattr(goods, "code", kind),
            "stars": goods.stars,
        }
    )


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


def create_app(
    bot, db: Database, config: Config, duels=None, store=None
) -> web.Application:
    app = web.Application()
    app[BOT_KEY] = bot
    app[DB_KEY] = db
    app[CONFIG_KEY] = config
    app[STAMP_KEY] = asset_stamp()
    if duels is not None:
        app[DUELS_KEY] = duels
    if store is not None:
        app[STORE_KEY] = store
    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/card", api_card),
            web.post("/api/equip", api_equip),
            web.post("/api/unequip", api_unequip),
            web.post("/api/repair", api_repair),
            web.get("/api/shop", api_shop),
            web.post("/api/buy", api_buy),
            web.post("/api/use", api_use),
            web.get("/api/club", api_club),
            web.get("/api/magic", api_magic),
            web.post("/api/pro", api_pro),
            web.get("/api/looks", api_looks),
            web.post("/api/look", api_look),
            web.get("/api/topup", api_topup),
            web.post("/api/invoice", api_invoice),
            web.get("/avatar/{user_id}", avatar),
            web.get("/healthz", healthz),
            web.static("/static", STATIC_DIR),
        ]
    )
    return app


async def run_webapp(
    bot, db: Database, config: Config, duels=None, store=None
) -> web.AppRunner:
    """Поднять сервер мини-аппа рядом с ботом. Вернуть runner для остановки."""
    runner = web.AppRunner(create_app(bot, db, config, duels, store))
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
