"""Режимы боя: на кулаках и с оружием.

Кулачный бой — спор чистых характеристик: на ринг выходят без вещей, а
значит и снашивать нечего. Бой с оружием идёт в полной экипировке, со всем,
что боец купил и надел.
"""

from __future__ import annotations

from enum import Enum


class FightMode(str, Enum):
    FIST = "fist"
    ARMED = "armed"

    @property
    def armed(self) -> bool:
        return self is FightMode.ARMED

    @property
    def title(self) -> str:
        return MODE_TITLES[self]

    @property
    def emoji(self) -> str:
        return MODE_EMOJI[self]

    @property
    def command(self) -> str:
        """Какой командой здесь вызывают на бой."""
        return MODE_COMMANDS[self]

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


MODE_TITLES: dict[FightMode, str] = {
    FightMode.FIST: "кулачный бой",
    FightMode.ARMED: "бой с оружием",
}

MODE_EMOJI: dict[FightMode, str] = {
    FightMode.FIST: "👊",
    FightMode.ARMED: "⚔️",
}

MODE_COMMANDS: dict[FightMode, str] = {
    FightMode.FIST: "/duel",
    FightMode.ARMED: "/fight",
}

# Сколько кулачных рингов может быть в одной группе: столько же одновременных
# боёв. Ринг с оружием пока один — этого хватает.
FIST_RINGS = 3


def mode_of(value: str | None) -> FightMode:
    """Режим из базы. Незнакомое значение считаем кулачным боем."""
    try:
        return FightMode(value or FightMode.FIST.value)
    except ValueError:  # pragma: no cover - режим из будущей версии
        return FightMode.FIST
