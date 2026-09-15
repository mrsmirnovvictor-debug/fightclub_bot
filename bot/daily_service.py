"""Отметить вход в клуб и выдать то, что за него причитается.

Служба делает ровно две вещи: считает день и отдаёт награду. Считает она
при каждом обращении к карточке, а засчитывается вход один раз в сутки —
день, уже отмеченный, второй раз не идёт.

Награда не выдаётся сама. Её отдают по нажатию, и это не формальность: в
рюкзаке может не быть места под склянку, а игрок должен увидеть, что ему
пришло, — молча начисленные кредиты не замечают.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.content.daily import next_milestone, reward_for, unclaimed
from bot.database import Database
from bot.game.daily import Reward, club_day, month_key, next_reset
from bot.game.health import now_ts
from bot.models import Player


class DailyError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class VisitState:
    """Что с входами у этого бойца прямо сейчас."""

    days: int  # сколько разных дней за месяц
    month: str
    fresh: bool  # этот вход засчитан только что — значит, окно новое
    waiting: tuple[Reward, ...] = ()  # заслужено, но не забрано
    next_day: int = 0  # до какого дня расти дальше; 0 — лестница пройдена
    resets_at: float = 0.0  # когда обновится счётчик

    @property
    def has_gift(self) -> bool:
        return bool(self.waiting)


@dataclass
class Claimed:
    """Что боец только что забрал."""

    rewards: tuple[Reward, ...] = ()
    credits: int = 0
    potions: list[str] = field(default_factory=list)


async def check_in(
    db: Database, player: Player, moment: float | None = None
) -> VisitState:
    """Отметить сегодняшний вход и посмотреть, что причитается.

    Зовётся при каждом открытии карточки: день засчитывается один раз, а
    состояние отдаётся всегда — иначе невзятая награда пропала бы из виду
    до следующих суток.
    """
    moment = now_ts() if moment is None else moment
    day = club_day(moment)
    month = month_key(day)
    row, fresh = await db.count_visit(player.user_id, day.isoformat(), month)
    return VisitState(
        days=row["days"],
        month=month,
        fresh=fresh,
        waiting=unclaimed(row["days"], row["claimed"], month),
        next_day=next_milestone(row["days"]),
        resets_at=next_reset(moment),
    )


async def claim(
    db: Database, player: Player, moment: float | None = None
) -> Claimed:
    """Забрать всё заслуженное разом. Невзятое до этого не сгорает."""
    moment = now_ts() if moment is None else moment
    month = month_key(club_day(moment))
    row = await db.visit_row(player.user_id)
    if row["month"] != month:
        raise DailyError("Месяц закончился, лестница началась заново.")

    waiting = unclaimed(row["days"], row["claimed"], month)
    if not waiting:
        raise DailyError("Забирать пока нечего — загляните завтра.")

    taken = Claimed(rewards=waiting)
    for reward in waiting:
        if reward.credits:
            player.credits += reward.credits
            taken.credits += reward.credits
        if reward.potion:
            await db.add_potion(player.user_id, reward.potion)
            taken.potions.append(reward.potion)
    if taken.credits:
        await db.save_player(player)
    # Отмечаем забранным самый высокий из взятых дней: всё, что ниже, уже
    # учтено, а всё, что выше, боец ещё не заслужил
    await db.save_visit(
        player.user_id, month, row["days"], row["last_day"], waiting[-1].day
    )
    return taken


def ladder_view(state: VisitState) -> list[dict]:
    """Вся лестница месяца для окна: что пройдено, что ждёт, что впереди."""
    from bot.content.daily import MILESTONES

    rows = []
    waiting = {reward.day for reward in state.waiting}
    for day in MILESTONES:
        reward = reward_for(day, state.month)
        if reward is None:  # pragma: no cover - лестница не бывает дырявой
            continue
        rows.append(
            {
                "day": day,
                "title": reward.title,
                "icon": reward.icon,
                "note": reward.note,
                "credits": reward.credits,
                "potion": reward.potion,
                # Три состояния: забрано, ждёт в руках, ещё расти
                "ready": day in waiting,
                "done": day <= state.days and day not in waiting,
            }
        )
    return rows


__all__ = ["Claimed", "DailyError", "VisitState", "check_in", "claim", "ladder_view"]
