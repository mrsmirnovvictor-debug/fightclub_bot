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
    # Публичный HTTPS-адрес мини-аппа. Пусто — карточка не поднимается.
    webapp_url: str = ""
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080
    # Короткое имя мини-аппа из BotFather: по нему строятся ссылки на карточку
    miniapp_name: str = ""

    @property
    def webapp_enabled(self) -> bool:
        return bool(self.webapp_url)


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
        webapp_url=os.getenv("WEBAPP_URL", "").strip().rstrip("/"),
        webapp_host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip(),
        webapp_port=int(os.getenv("WEBAPP_PORT", "8080")),
        miniapp_name=os.getenv("MINIAPP_NAME", "").strip(),
    )
