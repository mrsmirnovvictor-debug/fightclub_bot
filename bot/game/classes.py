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

    @property
    def short(self) -> str:
        """Одна буква для кнопки: в три столбца названия целиком не влезают."""
        return ZONE_SHORT[self]


ZONE_TITLES: dict[Zone, str] = {
    Zone.HEAD: "голова",
    Zone.CHEST: "корпус",
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

# Буква на кнопке. Голова и грудь обе на «г», поэтому грудь идёт корпусом:
# на панели важнее, чтобы буквы не путались между собой.
ZONE_SHORT: dict[Zone, str] = {
    Zone.HEAD: "Г",
    Zone.CHEST: "К",
    Zone.BELLY: "Ж",
    Zone.BELT: "П",
    Zone.LEGS: "Н",
}

ZONE_PREPOSITIONAL: dict[Zone, str] = {
    Zone.HEAD: "в голову",
    Zone.CHEST: "в корпус",
    Zone.BELLY: "в живот",
    Zone.BELT: "по поясу",
    Zone.LEGS: "по ногам",
}

# Зоны идут кольцом: за ногами снова начинается голова.
# Блок закрывает только смежные зоны, поэтому порядок здесь — часть правил.
ALL_ZONES: tuple[Zone, ...] = tuple(Zone)

# Сколько зон закрывает блок. Больше не бывает: щитов в клубе нет,
# и три зоны разом не закрывает никто.
BLOCK_WIDTH = 2


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


def block_button(combo: tuple[Zone, ...]) -> str:
    """Надпись на кнопке блока: «🛡 Голова + Корпус».

    Столбцов на панели два, так что зоны помещаются названиями целиком —
    сокращения нужны были, пока оружий было два.
    """
    if not combo:
        return "🛡 —"
    return "🛡 " + " + ".join(zone.title.capitalize() for zone in combo)


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
    def dative(self) -> str:
        """«к силе», «к ловкости» — для фраз вида «+1 к силе»."""
        return STAT_DATIVE[self]

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


STAT_TITLES: dict[Stat, str] = {
    Stat.STRENGTH: "сила",
    Stat.AGILITY: "ловкость",
    Stat.INTUITION: "интуиция",
    Stat.ENDURANCE: "выносливость",
}

# Дательный падеж: прибавку называют «+1 к силе», а не «+1 сила»
STAT_DATIVE: dict[Stat, str] = {
    Stat.STRENGTH: "силе",
    Stat.AGILITY: "ловкости",
    Stat.INTUITION: "интуиции",
    Stat.ENDURANCE: "выносливости",
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
    hp_base: int = 45
    hp_per_endurance: int = 6
    damage_mult: float = 1.0
    crit_bonus: float = 0.0
    crit_power_bonus: float = 0.0
    dodge_bonus: float = 0.0
    counter_bonus: float = 0.0
    # Профильная характеристика приносит своему классу больше, чем чужому:
    # одно и то же очко силы у воина превращается в больший урон, очко
    # ловкости у трикстера — в больший уворот, и так далее.
    damage_gain: float = 1.0
    dodge_gain: float = 1.0
    crit_gain: float = 1.0
    resist_gain: float = 1.0
    # Своё оружие против своей добычи: ассасину точность против уворота,
    # трикстеру пробивание против сопротивления, танку антикрит против крита.
    # Плоская прибавка держит круг и на первых уровнях, где статы ещё малы.
    accuracy_bonus: float = 0.0
    penetration_bonus: float = 0.0
    anticrit_bonus: float = 0.0
    # Насколько крепко класс держит блок под критическим ударом. Крит,
    # упёршийся в блок, всё равно может его проломить — вот эта доля и
    # вычитается из шанса пробития. Танк держит лучше всех, ассасин хуже
    # всех: он этот удар и наносит.
    block_hold: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


WARRIOR = FighterClass(
    code="warrior",
    title="Воин",
    emoji="⚔️",
    tagline="из его силы выходит больше урона",
    description=(
        "Крепкий кулачный боец. Сила у него весит больше, чем у остальных, "
        "а в круге камень-ножницы-бумага он не участвует: можно качаться "
        "ровно, можно уходить в любую крайность. Блок держит крепко — "
        "хуже танка, но лучше прочих."
    ),
    base_stats=Stats(strength=4, agility=3, intuition=3, endurance=4),
    hp_per_endurance=7,
    damage_mult=0.9,
    damage_gain=1.33,
    crit_bonus=0.02,
    counter_bonus=0.02,
    block_hold=0.22,
)

ROGUE = FighterClass(
    code="rogue",
    title="Трикстер",
    emoji="🤸",
    tagline="уходит от ударов и находит щели в обороне",
    description=(
        "Скользкий тип. Ловкость у него весит больше, чем у остальных: "
        "уходит с линии удара чаще всех, отвечает контрударом и пробивает "
        "чужое сопротивление. Бьёт танка, но сам вязнет против ассасина: "
        "блок у него слабый, и крит его проламывает."
    ),
    base_stats=Stats(strength=3, agility=5, intuition=3, endurance=3),
    hp_per_endurance=4,
    damage_mult=0.91,
    dodge_gain=2.05,
    dodge_bonus=0.25,
    counter_bonus=0.19,
    penetration_bonus=0.08,
    block_hold=0.12,
)

ASSASSIN = FighterClass(
    code="assassin",
    title="Ассасин",
    emoji="🗡️",
    tagline="ловит момент и бьёт насмерть",
    description=(
        "Мастер точного удара. Интуиция у него весит больше, чем у остальных: "
        "огромный шанс крита, страшная критическая мощь и точность, от которой "
        "не увернуться. Его крит и чужой блок проламывает чаще всех — а свой "
        "он держит хуже всех. Бьёт трикстера, но ломается о танка."
    ),
    base_stats=Stats(strength=4, agility=4, intuition=4, endurance=2),
    hp_per_endurance=5,
    damage_mult=1.15,
    crit_gain=1.93,
    crit_bonus=0.12,
    crit_power_bonus=0.57,
    accuracy_bonus=0.08,
    dodge_bonus=0.02,
    block_hold=0.05,
)

TANK = FighterClass(
    code="tank",
    title="Танк",
    emoji="🛡️",
    tagline="держит удар дольше всех",
    description=(
        "Живая стена. Выносливость у него весит больше, чем у остальных: "
        "запас здоровья, сопротивление урону и антикрит. Блок держит крепче "
        "всех: крит его почти не проламывает. Бьёт ассасина, но не успевает "
        "за трикстером."
    ),
    base_stats=Stats(strength=4, agility=2, intuition=2, endurance=6),
    hp_base=43,
    hp_per_endurance=5,
    damage_mult=0.98,
    resist_gain=1.52,
    anticrit_bonus=0.12,
    block_hold=0.35,
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
