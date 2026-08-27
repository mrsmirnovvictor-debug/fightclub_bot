"""Модели данных, которые ходят между БД и игровой логикой."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.game.classes import FighterClass, Stat, Stats, get_class
from bot.game.modes import FightMode
from bot.game.equipment import (
    Equipment,
    Item,
    OwnedItem,
    Slot,
    can_equip,
    missing_requirements,
)
from bot.game.economy import (
    LEVEL_CREDITS,
    MAX_LEVEL,
    MICRO_UPS_PER_LEVEL,
    POINTS_PER_UP,
    RATING_START,
    UP_CREDITS,
    exp_per_up,
    exp_to_next_level,
    ups_earned,
)
from bot.game.health import (
    HealthState,
    health_state,
    hp_percent,
    now_ts,
    regenerated_hp,
    seconds_until_full,
    seconds_until_ready,
)
from bot.game.stats import derive
from bot.game.world import DEFAULT_BIRTHPLACE, DEFAULT_CITY


@dataclass
class ProgressReport:
    """Что боец получил после боя: опыт, апы, уровни, кредиты."""

    exp: int = 0
    ups: int = 0
    levels: int = 0
    credits: int = 0
    points: int = 0
    endurance: int = 0  # выносливость, пришедшая с уровнями сама
    rating_delta: int = 0
    capped: bool = False  # опыт пришёл на потолке уровня

    @property
    def is_empty(self) -> bool:
        return not (self.exp or self.credits or self.rating_delta)


@dataclass
class Player:
    user_id: int
    nickname: str
    class_code: str
    avatar: str = "🥊"
    avatar_file_id: str | None = None
    strength: int = 0
    agility: int = 0
    intuition: int = 0
    endurance: int = 0
    free_points: int = 0
    level: int = 1
    exp: int = 0  # опыт внутри текущего уровня
    total_exp: int = 0  # накоплено за всё время, растёт и после потолка
    micro_ups: int = 0  # промежуточных апов взято на текущем уровне
    credits: int = 0
    rating: int = RATING_START
    hp: int | None = None  # здоровье на момент hp_at; None — полное
    hp_at: int = 0  # когда это здоровье зафиксировали (unix-время)
    wins: int = 0
    losses: int = 0
    draws: int = 0
    city: str = DEFAULT_CITY
    birthplace: str | None = None  # группа, где боец начал драться
    created_at: str | None = None  # когда персонаж появился на свет
    # Инвентарь: и надетое, и лежащее в рюкзаке. Подгружается из базы.
    gear: list[OwnedItem] = field(default_factory=list)

    @property
    def base_stats(self) -> Stats:
        """Собственные характеристики бойца, без экипировки."""
        return Stats(
            strength=self.strength,
            agility=self.agility,
            intuition=self.intuition,
            endurance=self.endurance,
        )

    @property
    def equipment(self) -> Equipment:
        return Equipment.from_owned(self.gear)

    @property
    def backpack(self) -> list[OwnedItem]:
        """Вещи, которые лежат в инвентаре и ждут своего часа."""
        return [owned for owned in self.gear if not owned.is_equipped]

    @property
    def equipped(self) -> list[OwnedItem]:
        return [owned for owned in self.gear if owned.is_equipped]

    def find_gear(self, item_id: int) -> OwnedItem | None:
        return next((owned for owned in self.gear if owned.id == item_id), None)

    def gear_in_slot(self, slot: Slot) -> OwnedItem | None:
        return next((owned for owned in self.gear if owned.slot is slot), None)

    def can_equip(self, item: Item) -> bool:
        """Требования считаются по своим характеристикам, без экипировки."""
        return can_equip(item, self.level, self.base_stats)

    def missing_for(self, item: Item) -> list[str]:
        return missing_requirements(item, self.level, self.base_stats)

    def drop_gear(self, owned: OwnedItem) -> None:
        self.gear = [item for item in self.gear if item.id != owned.id]

    @property
    def stats(self) -> Stats:
        """Характеристики с учётом надетого — их и видит боевой движок."""
        return self.base_stats.merge(self.equipment.bonus)

    @property
    def home(self) -> str:
        return self.birthplace or DEFAULT_BIRTHPLACE

    @property
    def fclass(self) -> FighterClass:
        return get_class(self.class_code)

    @property
    def fights(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def max_hp(self) -> int:
        return derive(
            self.fclass, self.stats, self.level, self.equipment.hp_bonus
        ).max_hp

    def current_hp(self, now: int | None = None) -> int:
        """Здоровье с учётом восстановления, прошедшего с последнего боя."""
        if self.hp is None:
            return self.max_hp
        moment = now_ts() if now is None else now
        return regenerated_hp(self.hp, self.max_hp, moment - self.hp_at)

    def hp_percent(self, now: int | None = None) -> float:
        return hp_percent(self.current_hp(now), self.max_hp)

    def health_state(self, now: int | None = None) -> HealthState:
        return health_state(self.current_hp(now), self.max_hp)

    def can_fight(self, now: int | None = None) -> bool:
        return self.health_state(now).can_fight

    def seconds_until_ready(self, now: int | None = None) -> int:
        return seconds_until_ready(self.current_hp(now), self.max_hp)

    def seconds_until_full(self, now: int | None = None) -> int:
        return seconds_until_full(self.current_hp(now), self.max_hp)

    def set_hp(self, value: int, now: int | None = None) -> None:
        """Зафиксировать здоровье после боя — дальше оно начнёт затягиваться."""
        self.hp = max(0, min(self.max_hp, value))
        self.hp_at = now_ts() if now is None else now

    def heal_full(self, now: int | None = None) -> None:
        self.hp = self.max_hp
        self.hp_at = now_ts() if now is None else now

    @property
    def exp_needed(self) -> int:
        return exp_to_next_level(self.level)

    @property
    def at_max_level(self) -> bool:
        return self.level >= MAX_LEVEL

    @property
    def exp_to_next_up(self) -> int:
        """Сколько опыта осталось до ближайшего апа (0 на потолке уровня)."""
        if self.at_max_level:
            return 0
        step = exp_per_up(self.level)
        target = min(self.exp_needed, step * (self.micro_ups + 1))
        return max(0, target - self.exp)

    @property
    def level_endurance(self) -> int:
        """Выносливость, которую уровни выдали сами: по очку за каждый.

        Вещи выносливость не дают вовсе, а эта прибавка не считается
        вложенной — респек и смена класса её не забирают.
        """
        return max(0, self.level - 1)

    @property
    def spent_points(self) -> int:
        """Сколько очков боец вложил сверх базы своего класса (без экипировки)."""
        return (
            self.base_stats.total()
            - self.fclass.base_stats.total()
            - self.level_endurance
        )

    def apply_stats(self, stats: Stats) -> None:
        self.strength = stats.strength
        self.agility = stats.agility
        self.intuition = stats.intuition
        self.endurance = stats.endurance

    def base_with_levels(self, fclass: FighterClass | None = None) -> Stats:
        """База класса плюс выносливость, накопленная уровнями."""
        base = (fclass or self.fclass).base_stats
        return base.plus(Stat.ENDURANCE, self.level_endurance)

    def reset_stats(self) -> int:
        """Снести распределённые очки обратно в свободные. Вернуть их число."""
        returned = max(0, self.spent_points)
        self.apply_stats(self.base_with_levels())
        self.free_points += returned
        return returned

    def switch_class(self, class_code: str) -> int:
        """Сменить класс: база нового класса, все вложенные очки возвращаются."""
        returned = max(0, self.spent_points)
        new_class = get_class(class_code)
        self.apply_stats(self.base_with_levels(new_class))
        self.class_code = class_code
        self.free_points += returned
        return returned

    def grant_exp(self, amount: int) -> ProgressReport:
        """Начислить опыт и выдать всё, что за ним следует.

        Апы приходят на каждой четверти уровня; четвёртый совпадает с самим
        уровнем. На потолке уровня опыт продолжает копиться в total_exp —
        под уровни, которые появятся позже.
        """
        report = ProgressReport(exp=max(0, amount))
        if report.exp == 0:
            return report
        self.total_exp += report.exp

        if self.at_max_level:
            report.capped = True
            return report

        self.exp += report.exp
        while not self.at_max_level and self.exp >= self.exp_needed:
            # Крупная победа может перескочить сразу и четверть, и уровень —
            # добираем апы, недополученные на уходящем уровне.
            while self.micro_ups < MICRO_UPS_PER_LEVEL - 1:
                self.micro_ups += 1
                self._take_up(report)
            self.exp -= self.exp_needed
            self.level += 1
            self.micro_ups = 0
            report.levels += 1
            # Уровень сам добавляет очко выносливости: её нельзя ни надеть,
            # ни выбить из вещей — только вырастить.
            self.endurance += 1
            report.endurance += 1
            self.credits += LEVEL_CREDITS
            report.credits += LEVEL_CREDITS
            self._take_up(report)  # четвёртый ап — сам уровень

        if self.at_max_level:
            self.exp = 0
            report.capped = True
            return report

        while self.micro_ups < ups_earned(self.exp, self.level):
            self.micro_ups += 1
            self._take_up(report)
        return report

    def _take_up(self, report: ProgressReport) -> None:
        self.free_points += POINTS_PER_UP
        self.credits += UP_CREDITS
        report.ups += 1
        report.points += POINTS_PER_UP
        report.credits += UP_CREDITS

    def grant_credits(self, amount: int) -> int:
        self.credits = max(0, self.credits + amount)
        return self.credits

    def can_afford(self, price: int) -> bool:
        return self.credits >= price

    def pay(self, price: int) -> None:
        if not self.can_afford(price):
            raise ValueError("Недостаточно кредитов")
        self.credits -= price

    def apply_rating(self, delta: int) -> int:
        self.rating = max(0, self.rating + delta)
        return self.rating


@dataclass
class Ring:
    """Ветка группы, отведённая под бои.

    Рингов в группе несколько: в каждом идёт свой бой, и режим у каждого
    свой — кулачный или с оружием.
    """

    chat_id: int
    thread_id: int | None
    number: int = 1
    mode: FightMode = FightMode.FIST
    title: str = ""

    @property
    def label(self) -> str:
        name = self.title or f"{self.mode.title}, ринг {self.number}"
        return f"{self.mode.emoji} {name}"

    @property
    def command(self) -> str:
        """Команда, которой этот ринг отмечают в ветке."""
        if self.mode.armed:
            return "/arena_gear"
        return f"/arena{self.number}"
