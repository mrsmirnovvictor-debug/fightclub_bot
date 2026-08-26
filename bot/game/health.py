"""Здоровье между боями: оно не восстанавливается мгновенно.

Оставшееся после боя здоровье сохраняется и затягивается со временем.
Регенерация считается лениво — по метке времени последнего изменения, — поэтому
переживает перезапуск бота и не требует фоновых задач.
"""

from __future__ import annotations

import math
import time
from enum import Enum

# Сколько занимает восстановление с нуля до полного
FULL_REGEN_SECONDS = 10 * 60
# Драться можно начиная с этой доли здоровья
READY_THRESHOLD = 0.8
# Ниже этой доли — «красная зона»
HURT_THRESHOLD = 0.2


class HealthState(str, Enum):
    HURT = "hurt"  # красный
    RECOVERING = "recovering"  # жёлтый
    READY = "ready"  # зелёный

    @property
    def emoji(self) -> str:
        return {"hurt": "🔴", "recovering": "🟡", "ready": "🟢"}[self.value]

    @property
    def title(self) -> str:
        return {
            "hurt": "избит",
            "recovering": "отдыхает",
            "ready": "готов к бою",
        }[self.value]

    @property
    def can_fight(self) -> bool:
        return self is HealthState.READY


def now_ts() -> int:
    return int(time.time())


def health_state(hp: int, max_hp: int) -> HealthState:
    percent = hp_percent(hp, max_hp)
    if percent < HURT_THRESHOLD:
        return HealthState.HURT
    if percent < READY_THRESHOLD:
        return HealthState.RECOVERING
    return HealthState.READY


def hp_percent(hp: int, max_hp: int) -> float:
    if max_hp <= 0:  # pragma: no cover - защита от битых данных
        return 0.0
    return max(0.0, min(1.0, hp / max_hp))


def regen_per_second(max_hp: int) -> float:
    return max_hp / FULL_REGEN_SECONDS


def regenerated_hp(stored_hp: int, max_hp: int, seconds_passed: float) -> int:
    """Сколько здоровья у бойца сейчас, если он отдыхал столько секунд."""
    if seconds_passed <= 0:
        return max(0, min(max_hp, stored_hp))
    healed = stored_hp + regen_per_second(max_hp) * seconds_passed
    return max(0, min(max_hp, int(healed)))


def required_hp(max_hp: int, target_percent: float) -> int:
    """Сколько целых единиц здоровья нужно, чтобы дотянуть до этой доли."""
    return min(max_hp, math.ceil(target_percent * max_hp))


def seconds_to_reach(hp: int, max_hp: int, target_percent: float) -> int:
    """Сколько секунд ждать до нужной доли здоровья (0, если уже хватает).

    Считаем по целым единицам: обратный отсчёт должен заканчиваться ровно
    тогда, когда боец действительно попадает в нужную зону, а не секундой
    раньше — иначе бот обещает бой, а потом отказывает.
    """
    target = required_hp(max_hp, target_percent)
    if hp >= target:
        return 0
    return max(1, math.ceil((target - hp) / regen_per_second(max_hp)))


def seconds_until_ready(hp: int, max_hp: int) -> int:
    return seconds_to_reach(hp, max_hp, READY_THRESHOLD)


def seconds_until_full(hp: int, max_hp: int) -> int:
    return seconds_to_reach(hp, max_hp, 1.0)


def format_duration(seconds: int) -> str:
    """Человеческое «через 2 мин 15 сек»."""
    seconds = max(0, int(seconds))
    minutes, rest = divmod(seconds, 60)
    if minutes and rest:
        return f"{minutes} мин {rest} сек"
    if minutes:
        return f"{minutes} мин"
    return f"{rest} сек"
