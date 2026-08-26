"""Проверка подписи Telegram Mini App.

Telegram отдаёт мини-аппу initData, подписанную ключом, производным от токена
бота. Проверяем подпись сами — только так можно доверять user.id, который
приходит из браузера.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

# Дольше суток initData не принимаем — Telegram советует так же
MAX_AUTH_AGE = 24 * 60 * 60


@dataclass(frozen=True)
class WebAppUser:
    """Кто открыл мини-апп и с каким параметром."""

    user_id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    start_param: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)


class AuthError(Exception):
    """initData не прошла проверку."""


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def check_signature(init_data: str, bot_token: str) -> dict[str, str]:
    """Проверить подпись и вернуть разобранные поля initData."""
    if not init_data:
        raise AuthError("initData пустая")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        raise AuthError("в initData нет подписи")

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    expected = hmac.new(
        _secret_key(bot_token), check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("подпись не совпала")
    return pairs


def parse_init_data(
    init_data: str, bot_token: str, max_age: int = MAX_AUTH_AGE, now: int | None = None
) -> WebAppUser:
    """Проверить initData и достать из неё пользователя."""
    pairs = check_signature(init_data, bot_token)

    auth_date = int(pairs.get("auth_date", "0") or 0)
    moment = int(time.time()) if now is None else now
    if max_age and (moment - auth_date) > max_age:
        raise AuthError("initData устарела, переоткрой карточку")

    try:
        user = json.loads(pairs.get("user", "{}"))
    except ValueError as error:  # pragma: no cover - Telegram шлёт валидный JSON
        raise AuthError("не разобрать пользователя") from error
    if not user.get("id"):
        raise AuthError("в initData нет пользователя")

    return WebAppUser(
        user_id=int(user["id"]),
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        username=user.get("username", ""),
        language_code=user.get("language_code", ""),
        start_param=pairs.get("start_param", ""),
    )


def sign_avatar(user_id: int, bot_token: str, expires_at: int) -> str:
    """Короткая подпись для ссылки на аватар — чтобы её нельзя было перебрать."""
    message = f"avatar:{user_id}:{expires_at}".encode()
    return hmac.new(_secret_key(bot_token), message, hashlib.sha256).hexdigest()[:32]


def check_avatar_token(
    user_id: int, bot_token: str, expires_at: int, token: str, now: int | None = None
) -> bool:
    moment = int(time.time()) if now is None else now
    if expires_at < moment:
        return False
    return hmac.compare_digest(sign_avatar(user_id, bot_token, expires_at), token)
