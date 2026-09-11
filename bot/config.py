"""Настройки бота, читаются из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


# ---------- рейд без ожиданий ----------
#
# Временно, на время ручных проверок: суточный запрет на свой рейд снят,
# сбор отряда идёт минуту вместо десяти, передышки между волнами нет.
# Вернуть — поставить False: прежние числа стоят рядом, в тех же строках.
# Переменные окружения сильнее этого флага, так что на Railway любое из
# чисел можно поправить и не трогая код.
RAID_NO_WAITS = True


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
    # Отдых между раундами: судья разводит бойцов по углам.
    # Полминуты хватает, чтобы минутный запас обращений к чату успел
    # освободиться. Ноль — драться без перерывов (так гоняют тесты).
    round_break: int = 30
    # ---------- рейды ----------
    # Подвал открыт по расписанию — окна лежат в bot/game/raid.py. Здесь
    # только выключатель: True пускает бить босса когда угодно. От
    # RAID_NO_WAITS он отвязан намеренно: тому флагу место в укорачивании
    # ожиданий, а расписание — правило игры, и снимать его заодно с
    # таймерами не стоит.
    raid_any_time: bool = False
    # Сколько ждём отряд после сбора: три минуты и гонг. Укорачиванию по
    # RAID_NO_WAITS этот срок больше не подчиняется: рейд идёт два раза в
    # сутки, и собрать десятерых за минуту не выйдет даже в тестах
    raid_lobby_timeout: int = 3 * 60
    # Сколько у бойца есть на свой удар в волне; не успел — пропустил
    raid_turn_timeout: int = 30
    # Передышка после того, как отряд отработал столько ударов
    raid_strikes_per_break: int = 6
    raid_break: int = 0 if RAID_NO_WAITS else 30
    # Кошель за победу: делится поровну между всеми, кто дошёл до конца
    raid_purse: int = 100
    # Насколько босс прибавляет в здоровье с каждым лишним бойцом отряда
    raid_boss_hp_share: float = 0.4
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

        raid_lobby_timeout=int(
            os.getenv("RAID_LOBBY_TIMEOUT", str(Config.raid_lobby_timeout))
        ),
        raid_turn_timeout=int(
            os.getenv("RAID_TURN_TIMEOUT", str(Config.raid_turn_timeout))
        ),
        raid_strikes_per_break=int(
            os.getenv("RAID_STRIKES_PER_BREAK", str(Config.raid_strikes_per_break))
        ),
        raid_break=int(os.getenv("RAID_BREAK", str(Config.raid_break))),
        raid_purse=int(os.getenv("RAID_PURSE", str(Config.raid_purse))),
        raid_boss_hp_share=float(
            os.getenv("RAID_BOSS_HP_SHARE", str(Config.raid_boss_hp_share))
        ),
        webapp_url=_webapp_url(),
        webapp_host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip(),
        webapp_port=_webapp_port(),
        club_url=os.getenv("CLUB_URL", "").strip() or Config.club_url,
        club_title=os.getenv("CLUB_TITLE", "").strip() or Config.club_title,
        miniapp_name=os.getenv("MINIAPP_NAME", "").strip(),
        miniapp_main=_flag("MINIAPP_MAIN"),
    )
