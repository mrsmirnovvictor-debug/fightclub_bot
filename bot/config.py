"""Настройки бота, читаются из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str = "fightclub.db"
    turn_timeout: int = 30
    challenge_timeout: int = 180
    # Сколько ждём состав на групповой бой; набрался раньше — начинаем сразу
    lobby_timeout: int = 180
    # Сколько идёт запись на турнир: по умолчанию сутки
    tournament_registration: int = 24 * 60 * 60
    # Публичный HTTPS-адрес мини-аппа. Пусто — карточка не поднимается.
    webapp_url: str = ""
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080
    # Короткое имя мини-аппа из BotFather: по нему строятся ссылки на карточку
    miniapp_name: str = ""

    @property
    def webapp_enabled(self) -> bool:
        return bool(self.webapp_url)


def _webapp_url() -> str:
    """Публичный адрес мини-аппа.

    На хостингах вроде Railway домен выдаётся автоматически и приезжает
    в окружении — тогда прописывать его руками не нужно.
    """
    url = os.getenv("WEBAPP_URL", "").strip()
    if not url:
        domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if domain:
            url = f"https://{domain}"
    return url.rstrip("/")


def _webapp_port() -> int:
    """Порт мини-аппа. PORT задаёт хостинг, WEBAPP_PORT — мы сами."""
    for name in ("PORT", "WEBAPP_PORT"):
        value = os.getenv(name, "").strip()
        if value.isdigit():
            return int(value)
    return 8080


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Скопируй .env.example в .env и вставь токен от @BotFather."
        )
    return Config(
        bot_token=token,
        db_path=os.getenv("DB_PATH", "fightclub.db").strip() or "fightclub.db",
        turn_timeout=int(os.getenv("TURN_TIMEOUT", "30")),
        challenge_timeout=int(os.getenv("CHALLENGE_TIMEOUT", "180")),
        lobby_timeout=int(os.getenv("LOBBY_TIMEOUT", "180")),
        tournament_registration=int(
            os.getenv("TOURNAMENT_REGISTRATION", str(24 * 60 * 60))
        ),
        webapp_url=_webapp_url(),
        webapp_host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip(),
        webapp_port=_webapp_port(),
        miniapp_name=os.getenv("MINIAPP_NAME", "").strip(),
    )
