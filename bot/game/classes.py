"""Классы бойцов, характеристики и зоны удара."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Zone(str, Enum):
    """Зона удара / блока. Как в классическом бойцовском клубе — пять зон."""

    HEAD = "head"
    CHEST = "chest"
    BELLY = "belly"
    BELT = "belt"
    LEGS = "legs"

    @property
    def title(self) -> str:
        return ZONE_TITLES[self]

    @property
    def emoji(self) -> str:
        return ZONE_EMOJI[self]

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


ZONE_TITLES: dict[Zone, str] = {
    Zone.HEAD: "голова",
    Zone.CHEST: "грудь",
    Zone.BELLY: "живот",
    Zone.BELT: "пояс",
    Zone.LEGS: "ноги",
}

ZONE_EMOJI: dict[Zone, str] = {
    Zone.HEAD: "🧠",
    Zone.CHEST: "🫁",
    Zone.BELLY: "🍺",
    Zone.BELT: "🥋",
    Zone.LEGS: "🦵",
}

ZONE_PREPOSITIONAL: dict[Zone, str] = {
    Zone.HEAD: "в голову",
    Zone.CHEST: "в грудь",
    Zone.BELLY: "в живот",
    Zone.BELT: "по поясу",
    Zone.LEGS: "по ногам",
}

# Зоны идут кольцом: за ногами снова начинается голова.
# Блок закрывает только смежные зоны, поэтому порядок здесь — часть правил.
ALL_ZONES: tuple[Zone, ...] = tuple(Zone)

# Сколько зон закрывает обычный блок и блок со щитом
BLOCK_WIDTH = 2
SHIELD_BLOCK_WIDTH = 3


def block_combo(start: Zone, width: int = BLOCK_WIDTH) -> tuple[Zone, ...]:
    """Блок, начинающийся с этой зоны: она и соседние по кольцу."""
    zones = ALL_ZONES
    count = len(zones)
    width = max(1, min(width, count))
    first = zones.index(start)
    return tuple(zones[(first + step) % count] for step in range(width))


def block_combos(width: int = BLOCK_WIDTH) -> tuple[tuple[Zone, ...], ...]:
    """Все допустимые блоки заданной ширины — по одному на каждую зону."""
    return tuple(block_combo(zone, width) for zone in ALL_ZONES)


def block_title(combo: tuple[Zone, ...]) -> str:
    """«Голова + грудь» — первая зона с большой буквы, остальные с маленькой."""
    if not combo:
        return "—"
    titles = [combo[0].title.capitalize()]
    titles += [zone.title for zone in combo[1:]]
    return " + ".join(titles)


class Stat(str, Enum):
    STRENGTH = "strength"
    AGILITY = "agility"
    INTUITION = "intuition"
    ENDURANCE = "endurance"

    @property
    def title(self) -> str:
        return STAT_TITLES[self]

    @property
    def emoji(self) -> str:
        return STAT_EMOJI[self]

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


STAT_TITLES: dict[Stat, str] = {
    Stat.STRENGTH: "сила",
    Stat.AGILITY: "ловкость",
    Stat.INTUITION: "интуиция",
    Stat.ENDURANCE: "выносливость",
}

STAT_EMOJI: dict[Stat, str] = {
    Stat.STRENGTH: "💪",
    Stat.AGILITY: "🤸",
    Stat.INTUITION: "🔮",
    Stat.ENDURANCE: "🫀",
}

ALL_STATS: tuple[Stat, ...] = tuple(Stat)


@dataclass(frozen=True)
class Stats:
    """Первичные характеристики бойца."""

    strength: int = 0
    agility: int = 0
    intuition: int = 0
    endurance: int = 0

    def get(self, stat: Stat) -> int:
        return getattr(self, stat.value)

    def plus(self, stat: Stat, amount: int = 1) -> "Stats":
        return Stats(**{**self.as_dict(), stat.value: self.get(stat) + amount})

    def merge(self, other: "Stats") -> "Stats":
        return Stats(
            strength=self.strength + other.strength,
            agility=self.agility + other.agility,
            intuition=self.intuition + other.intuition,
            endurance=self.endurance + other.endurance,
        )

    def total(self) -> int:
        return self.strength + self.agility + self.intuition + self.endurance

    def as_dict(self) -> dict[str, int]:
        return {
            "strength": self.strength,
            "agility": self.agility,
            "intuition": self.intuition,
            "endurance": self.endurance,
        }


@dataclass(frozen=True)
class FighterClass:
    """Описание класса бойца: стартовые статы и боевые особенности."""

    code: str
    title: str
    emoji: str
    tagline: str
    description: str
    base_stats: Stats
    block_zones: int = 2
    hp_base: int = 45
    hp_per_endurance: int = 6
    damage_mult: float = 1.0
    crit_bonus: float = 0.0
    crit_power_bonus: float = 0.0
    dodge_bonus: float = 0.0
    counter_bonus: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


WARRIOR = FighterClass(
    code="warrior",
    title="Воин",
    emoji="⚔️",
    tagline="бьёт тяжело и держит удар",
    description=(
        "Крепкий кулачный боец. Самый ровный класс: хороший урон, "
        "неплохое здоровье, никаких слабых мест."
    ),
    base_stats=Stats(strength=4, agility=3, intuition=3, endurance=4),
    block_zones=2,
    hp_per_endurance=7,
    damage_mult=1.10,
    crit_bonus=0.02,
    counter_bonus=0.02,
)

ROGUE = FighterClass(
    code="rogue",
    title="Ловкач",
    emoji="🤸",
    tagline="уходит от ударов и наказывает за промах",
    description=(
        "Скользкий тип. Уходит с линии удара чаще всех и почти всегда "
        "отвечает контрударом. Ставка на ловкость и интуицию."
    ),
    base_stats=Stats(strength=3, agility=5, intuition=3, endurance=3),
    block_zones=2,
    hp_per_endurance=7,
    damage_mult=1.0,
    dodge_bonus=0.26,
    counter_bonus=0.25,
)

ASSASSIN = FighterClass(
    code="assassin",
    title="Ассасин",
    emoji="🗡️",
    tagline="ловит момент и бьёт насмерть",
    description=(
        "Мастер точного удара. Огромный шанс крита и страшная критическая "
        "мощь — может снести половину здоровья одним попаданием."
    ),
    base_stats=Stats(strength=4, agility=4, intuition=4, endurance=2),
    block_zones=2,
    hp_per_endurance=7,
    damage_mult=1.05,
    crit_bonus=0.20,
    crit_power_bonus=0.5,
    dodge_bonus=0.02,
)

TANK = FighterClass(
    code="tank",
    title="Танк",
    emoji="🛡️",
    tagline="закрывает три зоны из пяти",
    description=(
        "Живая стена. Единственный класс, который держит блок сразу на трёх "
        "зонах из пяти: пробить его тяжелее всех. Бьёт при этом слабее всех."
    ),
    base_stats=Stats(strength=4, agility=2, intuition=2, endurance=6),
    block_zones=3,
    hp_base=30,
    hp_per_endurance=6,
    damage_mult=0.80,
)

FIGHTER_CLASSES: dict[str, FighterClass] = {
    c.code: c for c in (WARRIOR, ROGUE, ASSASSIN, TANK)
}

# Сколько очков боец распределяет при создании персонажа
START_POINTS = 6
# Больше этого в один стат при создании не вложить — чтобы не было вырожденных билдов
START_POINTS_PER_STAT_CAP = 4
# Очки характеристик выдаются за апы — см. bot/game/economy.py


def get_class(code: str) -> FighterClass:
    try:
        return FIGHTER_CLASSES[code]
    except KeyError as exc:  # pragma: no cover - защита от битых данных в БД
        raise ValueError(f"Неизвестный класс бойца: {code!r}") from exc
