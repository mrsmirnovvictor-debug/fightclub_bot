"""Эликсиры: то, что выпивают, а не надевают.

Эликсир не занимает слот и не снашивается — он расходуется. Их два рода:

* восстановление — доливает здоровье прямо сейчас, но не выше потолка;
* временный эффект — держит прибавку два часа и сам сходит на нет.

Эффект хранится сроком, а не тикающим счётчиком: в базе лежит момент, до
которого он живёт. Так он переживает перезапуск бота и не требует фоновых
задач — ровно как отдых между боями.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.game.art import potion as potion_art
from bot.game.classes import ALL_STATS, Stats
from bot.game.health import now_ts

# Сколько держится временный эффект
EFFECT_SECONDS = 2 * 60 * 60

# Ярлык раздела на витрине: эликсиры лежат отдельно от того, что надевают
SECTION_CODE = "misc"
SECTION_TITLE = "Прочее"
SECTION_EMOJI = "🧪"


class PotionKind(str, Enum):
    HEAL = "heal"  # доливает здоровье сразу
    BOOST = "boost"  # держит прибавку два часа


@dataclass(frozen=True)
class Potion:
    """Эликсир: что делает, сколько стоит и с какого уровня продаётся."""

    code: str
    title: str
    emoji: str
    kind: PotionKind
    note: str = ""
    image: str = ""
    heal: int = 0  # сколько здоровья долить сразу
    strength: int = 0
    agility: int = 0
    intuition: int = 0
    hp: int = 0  # прибавка к запасу здоровья на время действия
    seconds: int = 0
    level_required: int = 1
    price: int = 0

    @property
    def picture(self) -> str:
        """Картинка склянки лежит под её кодом: potions/heal_small.png.

        Поле `image` остаётся на случай файла с другим именем.
        """
        return self.image or potion_art(self.code)

    @property
    def bonus(self) -> Stats:
        return Stats(
            strength=self.strength,
            agility=self.agility,
            intuition=self.intuition,
        )

    @property
    def is_boost(self) -> bool:
        return self.kind is PotionKind.BOOST

    def describe(self) -> str:
        """Короткая строка для списка: «💪 +10 на 2 ч»."""
        parts = []
        if self.heal:
            parts.append(f"❤️ +{self.heal} сразу")
        for stat in ALL_STATS:
            value = self.bonus.get(stat)
            if value:
                parts.append(f"{stat.emoji} +{value}")
        if self.hp:
            parts.append(f"❤️ +{self.hp}")
        line = ", ".join(parts)
        if self.seconds:
            line += f" на {spell_duration(self.seconds)}"
        return line


def spell_duration(seconds: int) -> str:
    """«2 ч», «1 ч 47 мин», «12 мин» — то, что читается вслух."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    if minutes:
        return f"{minutes} мин"
    return f"{seconds} сек"


POTIONS: tuple[Potion, ...] = (
    # ---------- восстановление: доливает здоровье прямо сейчас ----------
    Potion(
        "heal_small",
        "Эликсир восстановления",
        "🧪",
        PotionKind.HEAL,
        note="Мутная склянка из-под чего-то крепкого. Ставит на ноги.",
        heal=30,
        price=30,
    ),
    Potion(
        "heal_big",
        "Большой эликсир восстановления",
        "⚗️",
        PotionKind.HEAL,
        note="Та же дрянь, только бутылка вдвое больше.",
        heal=60,
        level_required=3,
        price=55,
    ),
    # ---------- временные: держат прибавку два часа ----------
    Potion(
        "boost_strength",
        "Эликсир силы",
        "💪",
        PotionKind.BOOST,
        note="Два часа кулак весит вдвое больше.",
        image=potion_art("power"),
        strength=10,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=200,
    ),
    Potion(
        "boost_agility",
        "Эликсир ловкости",
        "🤸",
        PotionKind.BOOST,
        note="Два часа ноги сами уводят из-под удара.",
        image=potion_art("agile"),
        agility=10,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=200,
    ),
    Potion(
        "boost_intuition",
        "Эликсир интуиции",
        "🔮",
        PotionKind.BOOST,
        note="Два часа знаешь, куда он ударит, раньше него самого.",
        image=potion_art("int"),
        intuition=10,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=200,
    ),
    Potion(
        "boost_hp",
        "Эликсир жизней",
        "❤️",
        PotionKind.BOOST,
        note="Два часа держишь на шестьдесят ударов больше.",
        image=potion_art("health"),
        hp=60,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=150,
    ),
)

BY_CODE: dict[str, Potion] = {potion.code: potion for potion in POTIONS}


def get_potion(code: str) -> Potion | None:
    return BY_CODE.get(code)


def potions_unlocked_at(level: int) -> tuple[Potion, ...]:
    return tuple(potion for potion in POTIONS if potion.level_required == level)


@dataclass
class ActiveEffect:
    """Эффект, который сейчас на бойце: какой эликсир и до какого момента."""

    code: str
    until: int

    @property
    def potion(self) -> Potion | None:
        return get_potion(self.code)

    def seconds_left(self, now: int | None = None) -> int:
        moment = now_ts() if now is None else now
        return max(0, self.until - moment)

    def is_active(self, now: int | None = None) -> bool:
        return self.seconds_left(now) > 0


def effects_bonus(effects: list[ActiveEffect], now: int | None = None) -> Stats:
    """Сколько характеристик добавляют действующие эффекты."""
    total = Stats()
    for effect in effects:
        potion = effect.potion
        if potion is not None and effect.is_active(now):
            total = total.merge(potion.bonus)
    return total


def effects_hp(effects: list[ActiveEffect], now: int | None = None) -> int:
    """Прибавка к запасу здоровья от действующих эффектов."""
    return sum(
        effect.potion.hp
        for effect in effects
        if effect.potion is not None and effect.is_active(now)
    )


__all__ = [
    "BY_CODE",
    "EFFECT_SECONDS",
    "POTIONS",
    "SECTION_CODE",
    "SECTION_EMOJI",
    "SECTION_TITLE",
    "ActiveEffect",
    "Potion",
    "PotionKind",
    "effects_bonus",
    "effects_hp",
    "get_potion",
    "potions_unlocked_at",
    "spell_duration",
]
