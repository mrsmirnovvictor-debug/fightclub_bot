"""Рейд: несколько живых бойцов против одного NPC.

Отличий от группового боя два, и оба важные.

**Соперник один на всех.** Босс дерётся с каждым игроком по очереди, а не
разбивается на пары: за волну он успевает разменяться со всеми, кто ещё
стоит на ногах. Поэтому чем больше народу, тем быстрее он падает — рейд и
рассчитан на толпу.

**Ход идёт от игрока.** Нажал — размен посчитан сразу, не нажал за отпущенное
время — пропустил удар, а босс своё всё равно отработает. Ждать всех, как в
дуэли, тут нельзя: десять человек не соберутся одновременно ни разу.

Здесь только правила: кто такой босс, чем он одет, чем кончился рейд и кому
достались призы. Таймеры, сообщения и база — в `bot/raid_service.py`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from bot.game import art
from bot.game.classes import FIGHTER_CLASSES, ALL_ZONES, BLOCK_WIDTH, block_combo
from bot.game.combat import Action, Fighter
from bot.game.equipment import Equipment, OwnedItem, get_item
from bot.game.health import now_ts
from bot.game.reference import best_kit, developed_stats

# Сколько человек идут в рейд. Одному можно — пусть и тяжело: босс всё
# равно подстроится под отряд. Больше десяти не помещается ни в панель,
# ни в лимит сообщений.
MIN_PARTY = 1
MAX_PARTY = 10

# На сколько уровней босс выше отряда
LEVELS_ABOVE = 4

# Насколько босс прибавляет в здоровье с каждым лишним бойцом отряда.
# Он дерётся с каждым по очереди, поэтому без прибавки вдесятером его
# сносят за две волны, а вдвоём не сносят никогда: урон отряда растёт с
# числом бойцов, а его запас — нет. Ноль — босс один и тот же на любую толпу.
BOSS_HP_SHARE = 0.4

# После стольких ударов игроков судья даёт передышку — как гонг в боксе.
# Заодно это держит нас в минутном запасе обращений к чату.
STRIKES_PER_BREAK = 6

# Рейд не может идти вечно: если за столько волн никто никого не добил,
# судья закрывает его поражением отряда — босс остался на ногах.
MAX_WAVES = 30

# Награда за победу: общий кошель на отряд, делится поровну. Вдесятером
# достаётся по десятке, в одиночку — все сто: чем больше народу, тем легче
# бой и тем меньше доля.
RAID_PURSE = 100
# Набившему больше всех с такой вероятностью достаётся эликсир
ELIXIR_CHANCE = 0.35
ELIXIR_PRIZES: tuple[str, ...] = (
    "boost_strength",
    "boost_agility",
    "boost_intuition",
)

# ---------- когда подвал открыт ----------
#
# Босса пускают бить не когда угодно, а по расписанию: пять окон по два
# часа. Время московское и без перевода часов, поэтому смещение постоянное.
MOSCOW = timezone(timedelta(hours=3))
RAID_WINDOWS: tuple[int, ...] = (0, 8, 12, 16, 20)
WINDOW_HOURS = 2


@dataclass(frozen=True)
class Window:
    """Одно окно рейда: с какого момента по какой (в секундах эпохи)."""

    start: int
    end: int

    @property
    def title(self) -> str:
        """«с 20:00 до 22:00» — по московскому времени."""
        first = datetime.fromtimestamp(self.start, MOSCOW)
        last = datetime.fromtimestamp(self.end, MOSCOW)
        return f"с {first:%H:%M} до {last:%H:%M} мск"

    def seconds_left(self, moment: int) -> int:
        return max(0, self.end - moment)


def _window_at(moment: int) -> Window:
    """Окно, которое началось в этот час. Часы берём московские."""
    here = datetime.fromtimestamp(moment, MOSCOW)
    start = here.replace(minute=0, second=0, microsecond=0)
    return Window(
        start=int(start.timestamp()),
        end=int(start.timestamp()) + WINDOW_HOURS * 3600,
    )


def window_of(moment: int | None = None) -> Window | None:
    """Открыт ли подвал прямо сейчас. None — закрыт, ждите следующего окна."""
    moment = now_ts() if moment is None else moment
    hour = datetime.fromtimestamp(moment, MOSCOW).hour
    for opens in RAID_WINDOWS:
        if opens <= hour < opens + WINDOW_HOURS:
            return _window_at(moment - (hour - opens) * 3600)
    return None


def any_window(moment: int | None = None) -> Window:
    """Окно на каждый двухчасовой отрезок суток — для снятого расписания.

    Правило «одна победа на окно» должно работать и когда подвал открыт
    круглосуточно, иначе выключатель заодно снимает и его.
    """
    moment = now_ts() if moment is None else moment
    hour = datetime.fromtimestamp(moment, MOSCOW).hour
    return _window_at(moment - (hour % WINDOW_HOURS) * 3600)


def next_window(moment: int | None = None) -> Window:
    """Ближайшее окно после этого момента — то, которого ждут."""
    moment = now_ts() if moment is None else moment
    here = datetime.fromtimestamp(moment, MOSCOW)
    for opens in RAID_WINDOWS:
        start = here.replace(hour=opens, minute=0, second=0, microsecond=0)
        if start.timestamp() > moment:
            return Window(int(start.timestamp()), int(start.timestamp()) + WINDOW_HOURS * 3600)
    # Все окна дня позади — первое завтрашнее
    tomorrow = (here + timedelta(days=1)).replace(
        hour=RAID_WINDOWS[0], minute=0, second=0, microsecond=0
    )
    return Window(
        int(tomorrow.timestamp()), int(tomorrow.timestamp()) + WINDOW_HOURS * 3600
    )


def schedule_text() -> str:
    """Расписание одной строкой: «8–10, 12–14, 16–18, 20–22, 0–2 мск»."""
    hours = sorted(RAID_WINDOWS)
    return (
        ", ".join(f"{opens}–{opens + WINDOW_HOURS}" for opens in hours) + " мск"
    )


class RaidEnd(str, Enum):
    """Чем кончился рейд."""

    WIN = "win"  # босс мёртв, кто-то из отряда жив
    DRAW = "draw"  # босс мёртв, но и отряд весь полёг
    LOSS = "loss"  # босс на ногах, отряд кончился

    @property
    def title(self) -> str:
        return RAID_END_TITLES[self]

    @property
    def emoji(self) -> str:
        return RAID_END_EMOJI[self]


RAID_END_TITLES = {
    RaidEnd.WIN: "Босс повержен",
    RaidEnd.DRAW: "Разменялись насмерть",
    RaidEnd.LOSS: "Отряд не вышел из подвала",
}
RAID_END_EMOJI = {RaidEnd.WIN: "🏆", RaidEnd.DRAW: "🤝", RaidEnd.LOSS: "💀"}


@dataclass(frozen=True)
class Boss:
    """NPC, против которого идёт рейд.

    `weapon` — код вещи из лавки: им босс и бьёт. Остальные слоты набираются
    лучшим, что вообще открыто к его уровню, — рейд-босс приходит одетым.
    """

    code: str
    title: str
    emoji: str
    class_code: str
    weapon: str
    # «рейд против Босса Подвала»: падеж хранится рядом с именем, а не
    # угадывается по окончанию — прозвища не склоняются по правилам
    genitive: str = ""
    tagline: str = ""

    @property
    def image(self) -> str:
        return art.boss(self.code)

    @property
    def whom(self) -> str:
        """Кого бьём: «рейд против Босса Подвала»."""
        return self.genitive or self.title


BOSSES: tuple[Boss, ...] = (
    Boss(
        code="cellar_boss",
        title="Босс Подвала",
        emoji="🩸",
        class_code="tank",
        weapon="sledge",
        genitive="Босса Подвала",
        tagline="Он тут всё построил и всех похоронил.",
    ),
)

CELLAR_BOSS = BOSSES[0]
BOSS_BY_CODE = {boss.code: boss for boss in BOSSES}
# У босса свой номер: он не игрок, и с чужим user_id путаться не должен
BOSS_ID = -1


def get_boss(code: str) -> Boss:
    return BOSS_BY_CODE.get(code, CELLAR_BOSS)


def boss_level(levels: list[int]) -> int:
    """Босс выше отряда на LEVELS_ABOVE уровней.

    Считаем от среднего, а не от самого сильного: иначе один ветеран в
    компании новичков поднимает босса так, что новичков сносит первым же
    разменом — а звали их драться, а не подавать патроны.
    """
    if not levels:
        return 1 + LEVELS_ABOVE
    return round(sum(levels) / len(levels)) + LEVELS_ABOVE


def boss_kit(boss: Boss, level: int) -> Equipment:
    """Полный комплект босса: лучшее по его уровню, оружие — своё."""
    fclass = FIGHTER_CLASSES[boss.class_code]
    kit = dict(best_kit(fclass, level))
    weapon = get_item(boss.weapon)
    if weapon is not None:
        kit[weapon.slot] = weapon
    return Equipment(
        items={slot: OwnedItem(item=item, slot=slot) for slot, item in kit.items()}
    )


def boss_fighter(
    boss: Boss, levels: list[int], hp_share: float = BOSS_HP_SHARE
) -> Fighter:
    """Собрать босса под этот отряд: уровень по отряду, запас — по толпе."""
    level = boss_level(levels)
    fclass = FIGHTER_CLASSES[boss.class_code]
    equipment = boss_kit(boss, level)
    stats = developed_stats(fclass, level).merge(equipment.bonus)
    plain = Fighter(
        user_id=BOSS_ID,
        name=boss.title,
        fclass=fclass,
        stats=stats,
        level=level,
        equipment=equipment,
    )
    extra = round(plain.max_hp * hp_share * max(0, len(levels) - 1))
    if not extra:
        return plain
    return Fighter(
        user_id=BOSS_ID,
        name=boss.title,
        fclass=fclass,
        stats=stats,
        level=level,
        equipment=equipment,
        extra_hp=extra,
    )


def boss_action(enemy: Fighter | None = None, rng: random.Random | None = None) -> Action:
    """Босс бьёт наугад: ни зону, ни блок он не выбирает с умыслом.

    Рук у него столько же, сколько у любого бойца: со щитом одна, со вторым
    оружием две. Блок он держит той же ширины, что и его снаряжение.
    """
    rng = rng or random
    hands = enemy.attacks_per_round if enemy else 1
    width = enemy.block_width if enemy else BLOCK_WIDTH
    return Action(
        attacks=tuple(rng.choice(ALL_ZONES) for _ in range(hands)),
        block=block_combo(rng.choice(ALL_ZONES), width),
    )


@dataclass
class RaidOutcome:
    """Итог рейда и кто в нём отличился."""

    end: RaidEnd
    survivors: list[int] = field(default_factory=list)
    # Кто сколько набил — по убыванию, первыми трое призёров
    damage: list[tuple[int, int]] = field(default_factory=list)

    @property
    def won(self) -> bool:
        return self.end is RaidEnd.WIN

    @property
    def draw(self) -> bool:
        return self.end is RaidEnd.DRAW

    @property
    def top(self) -> list[int]:
        """Кто набил больше всех. Пусто — не набил никто."""
        best = [user_id for user_id, damage in self.damage[:1] if damage > 0]
        return best


def judge_raid(boss: Fighter, fighters: dict[int, Fighter]) -> RaidOutcome | None:
    """Кончился ли рейд, и если да — чем.

    None значит «дерёмся дальше». Босс мёртв — победа, а если отряд лёг с
    ним в один ход, то ничья: разменялись насмерть.
    """
    alive = [user_id for user_id, fighter in fighters.items() if fighter.alive]
    if boss.alive and not alive:
        return RaidOutcome(end=RaidEnd.LOSS, damage=damage_board(fighters))
    if not boss.alive:
        end = RaidEnd.WIN if alive else RaidEnd.DRAW
        return RaidOutcome(end=end, survivors=alive, damage=damage_board(fighters))
    return None


def damage_board(fighters: dict[int, Fighter]) -> list[tuple[int, int]]:
    """Кто сколько набил боссу — по убыванию."""
    return sorted(
        ((user_id, fighter.damage_dealt) for user_id, fighter in fighters.items()),
        key=lambda row: (-row[1], row[0]),
    )


def elixir_for(rng: random.Random | None = None) -> str | None:
    """Приз лучшему по урону: с некоторой вероятностью — один из эликсиров.

    Вещей за рейд больше не дают: кошелёк делится на всех, а сверх него у
    подвала есть только эта склянка, и та не каждый раз.
    """
    rng = rng or random
    if rng.random() >= ELIXIR_CHANCE:
        return None
    return rng.choice(ELIXIR_PRIZES)


def shares_of(purse: int, party: int) -> list[int]:
    """Как кошель делится на отряд. Остаток от деления уходит первым.

    Иначе вдевятером сто кредитов превращались бы в девяносто: округление
    вниз молча съедало бы разницу.
    """
    if party <= 0:
        return []
    base, extra = divmod(max(0, purse), party)
    return [base + (1 if index < extra else 0) for index in range(party)]


__all__ = [
    "BOSSES",
    "BOSS_HP_SHARE",
    "BOSS_ID",
    "CELLAR_BOSS",
    "LEVELS_ABOVE",
    "MAX_PARTY",
    "MAX_WAVES",
    "ELIXIR_CHANCE",
    "MIN_PARTY",
    "MOSCOW",
    "RAID_PURSE",
    "RAID_WINDOWS",
    "WINDOW_HOURS",
    "Window",
    "STRIKES_PER_BREAK",
    "Boss",
    "RaidEnd",
    "RaidOutcome",
    "boss_action",
    "boss_fighter",
    "boss_kit",
    "boss_level",
    "damage_board",
    "get_boss",
    "judge_raid",
    "any_window",
    "elixir_for",
    "next_window",
    "schedule_text",
    "shares_of",
    "window_of",
]
