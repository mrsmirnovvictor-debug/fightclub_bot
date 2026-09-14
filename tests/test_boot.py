"""Запуск клуба: сеть на старте подводит, но это не повод падать.

Первый разговор с Telegram идёт раньше, чем поднимается мини-апп: пока
`api.telegram.org` не ответил, `/healthz` ещё не отвечает никому. Один
сброшенный коннект в этот момент однажды унёс с собой весь деплой —
процесс умер на меню команд, healthcheck не дождался ответа, и выкатка
откатилась на прошлый образ. Поэтому у стартовых вызовов есть повторы, а
у украшательских — ещё и право не получиться вовсе.
"""

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from bot import main


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Паузы между попытками в тестах не нужны — важен их порядок."""
    monkeypatch.setattr(main, "BOOT_PAUSE", 0)


def reset(times: int):
    """Вызов, который `times` раз обрывается сетью, а потом отвечает."""
    tries = {"n": 0}

    async def call():
        tries["n"] += 1
        if tries["n"] <= times:
            raise TelegramNetworkError(
                method=GetMe(), message="Connection reset by peer"
            )
        return "готово"

    call.tries = tries
    return call


async def test_a_dropped_connection_is_retried():
    """Сеть оборвалась — пробуем ещё раз, а не падаем с первого раза."""
    call = reset(2)

    assert await main.boot_call("Проверка", call, required=True) == "готово"
    assert call.tries["n"] == 3


async def test_the_menu_is_not_worth_the_club():
    """Меню команд не получилось — клуб всё равно открывается."""
    call = reset(main.BOOT_TRIES)  # не ответит ни разу

    assert await main.boot_call("Меню команд", call, required=False) is None
    assert call.tries["n"] == main.BOOT_TRIES, "попытки тратятся все"


async def test_what_the_club_cannot_live_without_still_raises():
    """А вот без знакомства с Telegram запускаться нечему — и это видно."""
    call = reset(main.BOOT_TRIES)

    with pytest.raises(TelegramNetworkError):
        await main.boot_call("Знакомство", call, required=True)
