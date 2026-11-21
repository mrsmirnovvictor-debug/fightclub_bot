"""Тренер в клубе: когда он зовёт на бой и каким выходит против ученика.

Заявка Тренера нигде не хранится. Она не висит в списке вызовов и не
живёт по таймеру — она просто следует из его состояния: Тренер зовёт,
пока цел и свободен. Поэтому её не нужно ни снимать, ни продлевать, ни
восстанавливать после перезапуска бота, и «висит до момента принятия без
таймаута» выходит само собой.

Запись Тренера лежит в той же таблице, что и живые бойцы: так ему
достаётся затягивание здоровья по часам клуба — ровно то, что требуется
от него между боями.
"""

from __future__ import annotations

from dataclasses import replace

from bot.database import Database
from bot.game.trainer import (
    BODY_LEVEL,
    TRAINER_CLASS,
    TRAINER_ID,
    TRAINER_NAME,
    body_stats,
    may_learn,
)
from bot.models import Player

# Номер заявки Тренера. Отрицательный, как и он сам: настоящие вызовы
# нумеруются подряд с единицы, и спутать их нельзя
TRAINER_CHALLENGE_ID = -1


async def ensure_trainer(db: Database) -> Player:
    """Запись Тренера. Заводится один раз, дальше только читается."""
    trainer = await db.get_player(TRAINER_ID)
    if trainer is not None:
        return trainer
    stats = body_stats()
    trainer = Player(
        user_id=TRAINER_ID,
        nickname=TRAINER_NAME,
        class_code=TRAINER_CLASS,
        strength=stats.strength,
        agility=stats.agility,
        intuition=stats.intuition,
        endurance=stats.endurance,
        level=BODY_LEVEL,
    )
    await db.save_player(trainer)
    return trainer


def rested(trainer: Player, now: int | None = None) -> bool:
    """Затянулся ли Тренер настолько, чтобы снова звать на бой.

    Зовёт он только целым. Половина здоровья — это половина урока: такой
    бой кончается раньше, чем ученик успевает что-то понять.
    """
    return trainer.current_hp(now) >= trainer.max_hp


def calls(trainer: Player, busy: bool, now: int | None = None) -> bool:
    """Висит ли сейчас его заявка.

    Занят — значит, уже дерётся с кем-то другим: второго ученика он на
    ринг не возьмёт.
    """
    return not busy and rested(trainer, now)


def health_share(trainer: Player, now: int | None = None) -> float:
    """Какую долю своего запаса Тренер накопил к этому мигу.

    Доля, а не число единиц: на ринг он выходит с телом ученика, и
    запас там каждый раз другой. Побитый Тренер против танка выглядел бы
    целым, если переносить число.
    """
    if trainer.max_hp <= 0:  # pragma: no cover - тела без запаса не бывает
        return 1.0
    return min(1.0, max(0.0, trainer.current_hp(now) / trainer.max_hp))


def shaped_for(trainer: Player, learner: Player, now: int | None = None) -> Player:
    """Тренер, каким он выходит против этого ученика: его отражением.

    Класс, уровень и характеристики — ученика. Бой от этого получается
    ровный: одно и то же тело, тот же урон, тот же уворот, и решает его
    только то, кто куда бил. Половина побед у любого класса выходит не
    подгонкой чисел, а симметрией — свести к половине воина с одной
    заточкой нельзя вовсе, он еда для танка и стена для воина.

    Здоровье переносится долей: накопил половину своего запаса — выйдет
    с половиной запаса ученика.
    """
    share = health_share(trainer, now)
    mirror = replace(
        trainer,
        class_code=learner.class_code,
        level=learner.level,
        strength=learner.strength,
        agility=learner.agility,
        intuition=learner.intuition,
        endurance=learner.endurance,
    )
    mirror.set_hp(round(mirror.max_hp * share), now)
    return mirror


def refuse(learner: Player) -> str:
    """Почему этому бойцу нельзя к Тренеру. Пусто — можно."""
    if not may_learn(learner.level):
        return "Тренер берёт только новичков — с первого по четвёртый уровень."
    return ""


__all__ = [
    "TRAINER_CHALLENGE_ID",
    "calls",
    "health_share",
    "ensure_trainer",
    "refuse",
    "rested",
    "shaped_for",
]
