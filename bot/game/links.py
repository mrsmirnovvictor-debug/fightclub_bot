"""Ссылки на карточку бойца.

Тексты судьи собираются далеко от настроек, поэтому адрес мини-аппа держим
здесь: он задаётся один раз при старте бота и дальше просто используется.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CardLinks:
    """Как превратить бойца в кликабельное имя."""

    bot_username: str = ""
    miniapp_name: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_username and self.miniapp_name)

    def card_url(self, user_id: int) -> str | None:
        """Прямая ссылка на мини-апп — открывается всплывающим окном Telegram."""
        if not self.enabled:
            return None
        return f"https://t.me/{self.bot_username}/{self.miniapp_name}?startapp={user_id}"

    def href(self, user_id: int) -> str:
        """Куда вести имя бойца: в карточку, если она есть, иначе в профиль."""
        return self.card_url(user_id) or f"tg://user?id={user_id}"

    def configure(self, bot_username: str, miniapp_name: str) -> None:
        self.bot_username = bot_username.lstrip("@")
        self.miniapp_name = miniapp_name


# Общий на весь процесс: настраивается в bot/main.py при запуске
links = CardLinks()
