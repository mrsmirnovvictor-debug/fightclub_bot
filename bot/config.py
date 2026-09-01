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
    # Минута отдыха между раундами: судья разводит бойцов по углам.
    # Ноль — драться без перерывов (так гоняют тесты и симулятор).
    round_break: int = 60
    webapp_url: str = ""
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080
    # Куда звать новичка после создания бойца: приглашение в группу клуба
    club_url: str = "https://t.me/+7oXCRY4E4WAzZWJi"
    club_title: str = "Бойцовский клуб Вегас"
    # Короткое имя мини-аппа из BotFather: по нему строятся ссылки на карточку
    miniapp_name: str = ""
    # Мини-апп включён главным (BotFather → Configure Mini App): тогда карточка
    # открывается по t.me/бот?startapp=id и короткое имя не нужно
    miniapp_main: bool = False

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


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on", "да"}


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
        round_break=int(os.getenv("ROUND_BREAK", str(Config.round_break))),
        webapp_url=_webapp_url(),
        webapp_host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip(),
        webapp_port=_webapp_port(),
        club_url=os.getenv("CLUB_URL", "").strip() or Config.club_url,
        club_title=os.getenv("CLUB_TITLE", "").strip() or Config.club_title,
        miniapp_name=os.getenv("MINIAPP_NAME", "").strip(),
        miniapp_main=_flag("MINIAPP_MAIN"),
    )
