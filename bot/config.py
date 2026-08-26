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
    turn_timeout: int = 60
    challenge_timeout: int = 180


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Скопируй .env.example в .env и вставь токен от @BotFather."
        )
    return Config(
        bot_token=token,
        db_path=os.getenv("DB_PATH", "fightclub.db").strip() or "fightclub.db",
        turn_timeout=int(os.getenv("TURN_TIMEOUT", "60")),
        challenge_timeout=int(os.getenv("CHALLENGE_TIMEOUT", "180")),
    )
