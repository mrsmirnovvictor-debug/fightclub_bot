"""Запись о задании: что боец взял и что город по нему видел.

Отдельным файлом, а не в `bot.game.quests`, по той же причине, по какой
`Lease` лежит в `bot.game.estate`, а не в базе: правила про задания и
запись о задании — разные вещи. Правила знают, что такое «выиграть
бой»; запись знает, что этот боец уже выиграл.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TAKEN = "taken"
DONE = "done"


@dataclass(frozen=True)
class QuestLog:
    """Строка журнала заданий: код, состояние и отметки города."""

    code: str
    state: str = TAKEN
    marks: frozenset[str] = field(default_factory=frozenset)
    taken_at: int = 0
    done_at: int = 0

    @property
    def taken(self) -> bool:
        return self.state == TAKEN

    @property
    def done(self) -> bool:
        return self.state == DONE


__all__ = ["DONE", "TAKEN", "QuestLog"]
