"""Ссылки на карточку бойца.

Тексты судьи собираются далеко от настроек, поэтому адрес мини-аппа держим
здесь: он задаётся один раз при старте бота и дальше просто используется.

Открыть карточку из чата можно тремя способами, и они честно перебираются
сверху вниз — каждый следующий работает при более скромной настройке:

1. `t.me/бот/имя?startapp=id` — именованный мини-апп (BotFather → /newapp).
   Открывается сразу поверх чата. Важно: если приложения с таким коротким
   именем в BotFather нет, Telegram молча уводит человека в чат с ботом —
   ссылка выглядит рабочей, а карточка не открывается.
2. `t.me/бот?startapp=id` — главный мини-апп бота (BotFather → Configure Mini
   App). Отдельное имя не нужно, поведение то же.
3. `t.me/бот?start=card_id` — обычная диплинк-ссылка. Уводит в чат с ботом,
   но бот тут же присылает карточку этого бойца с кнопкой. Не так красиво,
   зато работает всегда и никогда не заканчивается пустым чатом.
"""

from __future__ import annotations

from dataclasses import dataclass

# Префикс диплинка: t.me/бот?start=card_12345
CARD_PAYLOAD = "card_"


def card_target(payload: str) -> int | None:
    """Чью карточку просят в диплинке. None — payload не про карточки."""
    if not payload.startswith(CARD_PAYLOAD):
        return None
    tail = payload[len(CARD_PAYLOAD) :]
    return int(tail) if tail.isdigit() else None


def short_name(value: str) -> str:
    """Короткое имя мини-аппа из того, что написали в MINIAPP_NAME.

    В переменную регулярно кладут не имя, а всю ссылку из BotFather
    (`https://t.me/бот/card`) или имя со собачкой. Ссылка с таким «именем»
    внутри выглядит рабочей, а Telegram по ней молча открывает чат с ботом —
    поэтому берём последний кусок пути и отрезаем хвост запроса.
    """
    name = value.strip().split("?", 1)[0].split("#", 1)[0].strip("/")
    if not name:
        return ""
    return name.rsplit("/", 1)[-1].lstrip("@")


@dataclass
class CardLinks:
    """Как превратить бойца в кликабельное имя."""

    bot_username: str = ""
    miniapp_name: str = ""
    main_app: bool = False

    @property
    def enabled(self) -> bool:
        """Открывается ли карточка одним касанием, без захода в чат бота."""
        return bool(self.bot_username and (self.miniapp_name or self.main_app))

    def card_url(self, user_id: int) -> str | None:
        """Прямая ссылка на мини-апп — открывается всплывающим окном Telegram."""
        if not self.bot_username:
            return None
        if self.miniapp_name:
            return (
                f"https://t.me/{self.bot_username}/{self.miniapp_name}"
                f"?startapp={user_id}"
            )
        if self.main_app:
            return f"https://t.me/{self.bot_username}?startapp={user_id}"
        return None

    def start_url(self, user_id: int) -> str | None:
        """Запасной путь: бот сам пришлёт карточку в личке."""
        if not self.bot_username:
            return None
        return f"https://t.me/{self.bot_username}?start={CARD_PAYLOAD}{user_id}"

    def href(self, user_id: int) -> str:
        """Куда вести имя бойца: в карточку, если она есть, иначе в профиль."""
        return (
            self.card_url(user_id)
            or self.start_url(user_id)
            or f"tg://user?id={user_id}"
        )

    def configure(
        self, bot_username: str, miniapp_name: str, main_app: bool = False
    ) -> None:
        self.bot_username = bot_username.lstrip("@")
        self.miniapp_name = short_name(miniapp_name)
        self.main_app = main_app


# Общий на весь процесс: настраивается в bot/main.py при запуске
links = CardLinks()
