"""Доска объявлений клуба: что началось и куда идти присоединяться.

Бои и рейды переехали в мини-апп, и с этим пропала половина клубной
жизни: боец открывает вызов у себя на экране, а в чате об этом никто не
знает — звать некого, и вызов висит до истечения срока. Раньше эту роль
играло само сообщение о вызове в ветке ринга, но его теперь нет.

Поэтому объявления приносит бот: в ветку своего вида (кулачные бои, бои
с оружием, рейды) и с закрепом, чтобы объявление висело сверху, пока
зовут. Ветки размечает админ группы; ветки нет — объявлять некуда, и
бот молчит.

Закреп снимается, как только звать больше некуда: вызов приняли, отозвали
или он истёк, отряд ушёл в подвал. Иначе шапка чата за вечер зарастает
объявлениями о боях, которые давно кончились.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.database import Database
from bot.messaging import Announcer

logger = logging.getLogger(__name__)

# Виды объявлений. Ровно они же — в командах разметки веток
FIST = "fist"
ARMED = "armed"
RAID = "raid"
KINDS: tuple[str, ...] = (FIST, ARMED, RAID)

KIND_TITLES: dict[str, str] = {
    FIST: "кулачные бои",
    ARMED: "бои с оружием",
    RAID: "рейды",
}


@dataclass(frozen=True)
class Pin:
    """Отправленное объявление: где висит и что снимать."""

    chat_id: int
    message_id: int


class Board:
    """Кто разносит объявления по веткам и снимает их потом."""

    def __init__(self, db: Database, voice: Announcer) -> None:
        self.db = db
        self.voice = voice

    async def announce(
        self,
        kind: str,
        text: str,
        skip: tuple[int | None, int | None] | None = None,
        **kwargs,
    ) -> list[Pin]:
        """Разнести объявление по всем веткам этого вида и закрепить.

        `skip` — ветка, где событие и так на виду: вызов, брошенный прямо
        в ветке ринга, уже стоит там сообщением, и объявлять его туда же
        значит написать одно и то же дважды.
        """
        pins: list[Pin] = []
        for chat_id, thread_id in await self.db.announce_threads(kind):
            if skip is not None and (chat_id, thread_id) == skip:
                continue
            message = await self.voice.send(chat_id, thread_id, text, **kwargs)
            if message is None:
                continue
            await self.voice.pin(chat_id, message.message_id)
            pins.append(Pin(chat_id, message.message_id))
        return pins

    async def close(self, pins: list[Pin], text: str | None = None) -> None:
        """Снять закрепы. С текстом — ещё и переписать объявление на итог."""
        for pin in pins:
            await self.voice.unpin(pin.chat_id, pin.message_id)
            if text is not None:
                await self.voice.edit(pin.chat_id, pin.message_id, text)
        pins.clear()


__all__ = ["ARMED", "Board", "FIST", "KINDS", "KIND_TITLES", "Pin", "RAID"]
