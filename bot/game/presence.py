"""Кто сейчас в клубе, а кого давно не видели.

Мини-апп не сообщает, что его закрыли, — узнать это можно только по
молчанию. Поэтому «в сети» здесь значит ровно одно: приложение
отзывалось в последние две минуты. Свернул человек Telegram, не закрывая
карточку, — вебвью засыпает, запросы прекращаются, и через две минуты он
числится ушедшим. Для вопроса «стоит ли его сейчас вызывать» это как раз
верный ответ.

Бои в ветке группы сюда не идут: они живут мимо мини-аппа, и «был в
клубе» про них ничего не знает.
"""

from __future__ import annotations

from bot.game.health import now_ts

# Сколько боец числится в сети после последнего действия
ONLINE_SECONDS = 2 * 60

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def plural(count: int, one: str, few: str, many: str) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return many
    last = count % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def is_online(seen_at: int, now: int | None = None) -> bool:
    """Отзывалось ли приложение в последние две минуты."""
    if not seen_at:
        return False
    return (now_ts() if now is None else now) - seen_at <= ONLINE_SECONDS


def away_for(seen_at: int, now: int | None = None) -> int:
    """Сколько секунд прошло с последнего действия. Ноль — не видели вовсе."""
    if not seen_at:
        return 0
    return max(0, (now_ts() if now is None else now) - seen_at)


def rough_time(seconds: int) -> str:
    """«20 минут», «3 часа», «2 дня» — крупными делениями.

    Секунды тут не нужны: строка отвечает на вопрос «давно ли», а не «во
    сколько именно». «Не был в клубе 20 минут 14 секунд» — это точность,
    которой никто не просил.
    """
    if seconds >= DAY:
        days = seconds // DAY
        return f"{days} {plural(days, 'день', 'дня', 'дней')}"
    if seconds >= HOUR:
        hours = seconds // HOUR
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')}"
    minutes = max(1, seconds // MINUTE)
    return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}"


def presence_text(seen_at: int, now: int | None = None) -> str:
    """Строка под шкалой здоровья в карточке бойца."""
    if is_online(seen_at, now):
        return "🟢 В клубе"
    if not seen_at:
        return "Ни разу не заходил"
    return f"Не был в клубе {rough_time(away_for(seen_at, now))}"


__all__ = [
    "ONLINE_SECONDS",
    "away_for",
    "is_online",
    "presence_text",
    "rough_time",
]
