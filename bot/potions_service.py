"""Эликсиры: покупка и применение.

Правила короткие. Купленная склянка ложится в рюкзак стопкой. Выпитая
исчезает: восстановление доливает здоровье, но не выше потолка, а временный
эликсир заводит эффект на два часа.

Временный эликсир на бойце всегда один. Выпить второй такой же — продлить
срок, а не удвоить прибавку: +10 остаётся +10, зато не пропадает даром.
Выпить другой — сменить эффект: прежний гаснет сразу, и боец должен узнать
об этом до глотка, а не после.

Восстановления это не касается: их пьют сколько угодно и подряд, они ничего
не держат и никому не мешают.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bot.database import Database
from bot.game.health import now_ts
from bot.game.potions import Potion, PotionKind, get_potion
from bot.models import Player

logger = logging.getLogger(__name__)


class PotionError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class PotionResult:
    """Чем кончился глоток: сколько долили, до какого часа держится эффект."""

    potion: Potion
    healed: int = 0  # сколько здоровья реально долили
    until: int = 0  # до какого момента держится эффект
    extended: bool = False  # эффект не завели, а продлили
    left: int = 0  # сколько таких склянок осталось в рюкзаке
    # Что погасло, уступив место новому эликсиру
    replaced: list[Potion] = field(default_factory=list)

    def seconds_left(self, now: int | None = None) -> int:
        return max(0, self.until - (now_ts() if now is None else now))


def running_boost(player: Player, now: int | None = None) -> Potion | None:
    """Временный эликсир, который сейчас на бойце. Он всегда один."""
    for effect in player.active_effects(now):
        potion = effect.potion
        if potion is not None and potion.is_boost:
            return potion
    return None


def would_replace(player: Player, code: str, now: int | None = None) -> Potion | None:
    """Что погаснет, если выпить этот эликсир. None — ничего не погаснет.

    Спрашивают до глотка: и мини-апп, и лавка в личке показывают этим
    предупреждение, а решает всё равно человек.
    """
    potion = get_potion(code)
    if potion is None or not potion.is_boost:
        return None
    running = running_boost(player, now)
    return running if running is not None and running.code != potion.code else None


def _find(code: str) -> Potion:
    potion = get_potion(code)
    if potion is None:
        raise PotionError("Такого эликсира в лавке нет.")
    return potion


async def buy_potion(db: Database, player: Player, code: str) -> Potion:
    """Купить склянку. Пить — когда понадобится, хоть через неделю."""
    potion = _find(code)
    if player.level < potion.level_required:
        raise PotionError(
            f"«{potion.title}» открывается на {potion.level_required} уровне, "
            f"а у тебя {player.level}."
        )
    if not player.can_afford(potion.price):
        raise PotionError(
            f"Не хватает кредитов: «{potion.title}» стоит {potion.price} 💰, "
            f"а на счету {player.credits} 💰."
        )

    player.pay(potion.price)
    await db.save_player(player)
    player.potions[potion.code] = await db.add_potion(player.user_id, potion.code)
    return potion


async def use_potion(
    db: Database, player: Player, code: str, now: int | None = None
) -> PotionResult:
    """Выпить склянку из рюкзака."""
    potion = _find(code)
    moment = now_ts() if now is None else now
    if player.potion_count(potion.code) <= 0:
        raise PotionError(f"«{potion.title}» в рюкзаке нет.")

    if potion.kind is PotionKind.HEAL:
        current = player.current_hp(moment)
        if current >= player.max_hp:
            # Склянку не тратим: здоровье и так полное, выливать её незачем
            raise PotionError("Ты и так в полном порядке — склянку побереги.")
        healed = min(potion.heal, player.max_hp - current)
        player.set_hp(current + healed, moment)
        await db.save_player(player)
        result = PotionResult(potion, healed=healed)
    else:
        # Временный эликсир на бойце один. Чужой гасим, свой продлеваем:
        # тот же эликсир поверх действующего добавляет срок, а не прибавку —
        # иначе стопка склянок делала бы из бойца бога.
        dropped = await _drop_other_boosts(db, player, potion, moment)
        running = player.effect_of(potion.code, moment)
        start = running.until if running else moment
        until = start + potion.seconds
        await db.set_effect(player.user_id, potion.code, until)
        player.effects = await db.list_effects(player.user_id)
        result = PotionResult(
            potion, until=until, extended=bool(running), replaced=dropped
        )

    await db.take_potion(player.user_id, potion.code)
    left = max(0, player.potion_count(potion.code) - 1)
    if left:
        player.potions[potion.code] = left
    else:
        player.potions.pop(potion.code, None)
    result.left = left
    logger.info("Боец %s выпил %s", player.user_id, potion.code)
    return result


async def _drop_other_boosts(
    db: Database, player: Player, potion: Potion, now: int
) -> list[Potion]:
    """Погасить все прочие временные эффекты. Вернуть, что погасили.

    Список, а не один: пока действовало прежнее правило, боец мог набрать
    несколько эффектов разом — этих старожилов тоже надо снять.
    """
    dropped: list[Potion] = []
    for effect in player.active_effects(now):
        running = effect.potion
        if running is None or not running.is_boost or running.code == potion.code:
            continue
        await db.drop_effect(player.user_id, running.code)
        dropped.append(running)
    return dropped


__all__ = [
    "PotionError",
    "PotionResult",
    "buy_potion",
    "running_boost",
    "use_potion",
    "would_replace",
]
