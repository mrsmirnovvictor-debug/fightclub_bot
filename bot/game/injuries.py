"""Травмы: чем боец расплачивается за проигранный бой.

Добивающий крит иногда ломает что-нибудь всерьёз. Травма висит на бойце
часами, отнимает характеристику и не даёт надеть то, на что её больше не
хватает. Лечится временем или больницей.

Три правила, из которых собрано всё остальное.

**Травму даёт только добивающий крит.** Не любой удар и не любое
поражение: боец должен упасть, и упасть от крита. Так травма остаётся
редкой и всегда понятно, за что она.

**Тяжесть решает всё.** Срок, размер потери и цена лечения зависят
только от неё, а не от вида травмы: вывих плеча и ушиб ноги — это одна
и та же лёгкая травма, просто в разные места.

**Куда ударили, там и сломалось.** Зона последнего удара выбирает, какая
характеристика просядет: в голову — интуиция, в корпус — сила, ниже
пояса — ловкость. Отсюда и название травмы.

Отрицательная характеристика не даёт драться вовсе — это правило живёт в
`bot.models.Player.can_fight`, а здесь только цифры, на которые она
падает.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum

from bot.game.classes import Stat, Stats, Zone


class Hurt(str, Enum):
    """Тяжесть травмы. От неё и срок, и потеря, и цена лечения."""

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"

    @property
    def title(self) -> str:
        return HURT_TITLES[self]

    @property
    def hours(self) -> int:
        return HURT_HOURS[self]

    @property
    def seconds(self) -> int:
        return self.hours * 3600

    @property
    def penalty(self) -> int:
        """На сколько просядет характеристика. Число положительное."""
        return HURT_PENALTY[self]

    @property
    def price(self) -> int:
        """Во сколько обойдётся лечение в больнице."""
        return HURT_PRICE[self]

    @property
    def cure_seconds(self) -> int:
        """Сколько после лечения ещё лежать. Не мгновенно, но и не часы."""
        return HURT_CURE_MINUTES[self] * 60


HURT_TITLES: dict[Hurt, str] = {
    Hurt.LIGHT: "лёгкая травма",
    Hurt.MEDIUM: "средняя травма",
    Hurt.HEAVY: "тяжёлая травма",
}
HURT_HOURS: dict[Hurt, int] = {Hurt.LIGHT: 2, Hurt.MEDIUM: 6, Hurt.HEAVY: 12}
HURT_PENALTY: dict[Hurt, int] = {Hurt.LIGHT: 10, Hurt.MEDIUM: 15, Hurt.HEAVY: 20}
HURT_PRICE: dict[Hurt, int] = {Hurt.LIGHT: 100, Hurt.MEDIUM: 200, Hurt.HEAVY: 300}
HURT_CURE_MINUTES: dict[Hurt, int] = {Hurt.LIGHT: 10, Hurt.MEDIUM: 15, Hurt.HEAVY: 20}

# Во сколько раз дольше идёт по городу травмированный боец
LIMP_TIMES = 2

# Шанс травмы на добивающем крите. Четверть — чтобы травма осталась
# событием, а не платой за каждое поражение: добивающий крит и сам не
# каждый бой, а вместе выходит примерно один бой из двадцати
INJURY_CHANCE = 0.25

# Чем тяжелее травма, тем реже она случается
HURT_ODDS: tuple[tuple[Hurt, float], ...] = (
    (Hurt.LIGHT, 0.60),
    (Hurt.MEDIUM, 0.30),
    (Hurt.HEAVY, 0.10),
)

# Куда ударили — то и сломалось. Живот отнесён к корпусу: рёбра и пресс
# — это про силу, а не про ловкость
ZONE_STATS: dict[Zone, Stat] = {
    Zone.HEAD: Stat.INTUITION,
    Zone.CHEST: Stat.STRENGTH,
    Zone.BELLY: Stat.STRENGTH,
    Zone.BELT: Stat.AGILITY,
    Zone.LEGS: Stat.AGILITY,
}


@dataclass(frozen=True)
class Injury:
    """Вид травмы: как называется, куда пришлась и что отнимает."""

    code: str
    title: str
    hurt: Hurt
    stat: Stat

    @property
    def penalty(self) -> int:
        return self.hurt.penalty

    @property
    def seconds(self) -> int:
        return self.hurt.seconds

    @property
    def loss(self) -> Stats:
        """Потеря характеристик — отрицательной прибавкой, как у эликсира."""
        return Stats(**{self.stat.value: -self.penalty})

    def describe(self) -> str:
        """«тяжёлая травма: 💪 сила −20» — одной строкой."""
        return (
            f"{self.hurt.title}: {self.stat.emoji} {self.stat.title} "
            f"−{self.penalty}"
        )


INJURIES: tuple[Injury, ...] = (
    # ---------- лёгкие: два часа ----------
    Injury("shoulder", "вывих плеча", Hurt.LIGHT, Stat.STRENGTH),
    Injury("leg_bruise", "ушиб ноги", Hurt.LIGHT, Stat.AGILITY),
    Injury("black_eye", "фингал под глазом", Hurt.LIGHT, Stat.INTUITION),
    # ---------- средние: шесть часов ----------
    Injury("sprain", "растяжение связок", Hurt.MEDIUM, Stat.STRENGTH),
    Injury("tailbone", "перелом копчика", Hurt.MEDIUM, Stat.AGILITY),
    Injury("broken_nose", "сломан нос", Hurt.MEDIUM, Stat.INTUITION),
    # ---------- тяжёлые: полсуток ----------
    Injury("broken_arm", "перелом руки", Hurt.HEAVY, Stat.STRENGTH),
    Injury("broken_leg", "перелом ноги", Hurt.HEAVY, Stat.AGILITY),
    Injury("spine", "травма позвоночника", Hurt.HEAVY, Stat.INTUITION),
)

BY_CODE: dict[str, Injury] = {one.code: one for one in INJURIES}


def get_injury(code: str | None) -> Injury | None:
    return BY_CODE.get(code or "")


def injury_for(zone: Zone | None, hurt: Hurt) -> Injury:
    """Какая травма выйдет от удара в эту зону при такой тяжести."""
    stat = ZONE_STATS.get(zone or Zone.HEAD, Stat.INTUITION)
    return next(
        one for one in INJURIES if one.hurt is hurt and one.stat is stat
    )


def roll_hurt(rng: random.Random | None = None) -> Hurt:
    """Насколько сильно не повезло."""
    rng = rng or random
    point = rng.random()
    for hurt, share in HURT_ODDS:
        if point < share:
            return hurt
        point -= share
    return HURT_ODDS[-1][0]  # pragma: no cover - доли складываются в единицу


def roll_injury(zone: Zone | None, rng: random.Random | None = None) -> Injury | None:
    """Бросок на травму от добивающего крита. None — обошлось."""
    rng = rng or random
    if rng.random() >= INJURY_CHANCE:
        return None
    return injury_for(zone, roll_hurt(rng))


@dataclass(frozen=True)
class ActiveInjury:
    """Травма, которая сейчас на бойце: какая и до какого часа.

    Устроена как действующий эликсир: код да срок. Разница в том, что
    эликсир прибавляет, а травма отнимает, и снять её досрочно можно
    только в больнице.
    """

    code: str
    until: int

    @property
    def injury(self) -> Injury | None:
        return get_injury(self.code)

    def seconds_left(self, now: int | None = None) -> int:
        moment = int(time.time()) if now is None else now
        return max(0, self.until - moment)

    def is_active(self, now: int | None = None) -> bool:
        return self.seconds_left(now) > 0

    @property
    def loss(self) -> Stats:
        """Сколько отнимает. Пустые характеристики — кода нет в справочнике."""
        injury = self.injury
        return injury.loss if injury else Stats()

    def describe(self, now: int | None = None) -> str:
        """«Тяжёлая травма: перелом руки. Ещё 10 часов 15 минут.»"""
        injury = self.injury
        if injury is None:  # pragma: no cover - код из будущей версии
            return ""
        left = long_duration(self.seconds_left(now))
        return (
            f"{injury.hurt.title.capitalize()}: {injury.title}. Ещё {left}."
        )


def injury_loss(injury: "ActiveInjury | None", now: int | None = None) -> Stats:
    """Потеря характеристик от травмы. Нет травмы — пустые характеристики."""
    if injury is None or not injury.is_active(now):
        return Stats()
    return injury.loss


# ---------- сколько осталось ----------

HOUR_WORDS = ("час", "часа", "часов")
MINUTE_WORDS = ("минута", "минуты", "минут")


def _word(count: int, words: tuple[str, str, str]) -> str:
    last, pair = count % 10, count % 100
    if 11 <= pair <= 14:
        return words[2]
    if last == 1:
        return words[0]
    if 2 <= last <= 4:
        return words[1]
    return words[2]


def long_duration(seconds: int) -> str:
    """«10 часов 15 минут» — словами, как это читают в карточке."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    parts = []
    if hours:
        parts.append(f"{hours} {_word(hours, HOUR_WORDS)}")
    if minutes:
        parts.append(f"{minutes} {_word(minutes, MINUTE_WORDS)}")
    if not parts:
        return "меньше минуты"
    return " ".join(parts)


__all__ = [
    "ActiveInjury",
    "BY_CODE",
    "HURT_ODDS",
    "INJURIES",
    "INJURY_CHANCE",
    "Hurt",
    "Injury",
    "LIMP_TIMES",
    "ZONE_STATS",
    "get_injury",
    "injury_for",
    "injury_loss",
    "long_duration",
    "roll_hurt",
    "roll_injury",
]
