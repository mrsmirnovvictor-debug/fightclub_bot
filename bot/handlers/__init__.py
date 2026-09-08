"""Роутеры бота."""

from aiogram import Router

from bot.handlers import creation, group, profile, shop, store


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(creation.router)
    router.include_router(shop.router)
    router.include_router(store.router)
    router.include_router(profile.router)
    router.include_router(group.router)
    return router


__all__ = ["build_router"]
