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
from datetime import date, datetime, timedelta, timezone
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

# На сколько волн растянута усталость. Это не потолок рейда: волн может
# быть и больше, и тогда усталость растёт дальше — с ней и урон. Именно
# она и доводит бой до конца, поэтому обрывать рейд по счётчику не нужно.
FATIGUE_WAVES = 30

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
# Босса пускают бить не когда угодно, а дважды в сутки по два часа.
# Слотов пять, из них на день выпадают два — и не те же, что вчера:
# расписание, которое можно выучить наизусть, перестаёт быть событием, а
# одно и то же время изо дня в день отсекает тех, кто в этот час работает.
# Время московское и без перевода часов, поэтому смещение постоянное.
MOSCOW = timezone(timedelta(hours=3))
RAID_SLOTS: tuple[int, ...] = (0, 8, 12, 16, 20)
RAIDS_PER_DAY = 2
WINDOW_HOURS = 2

# За сколько до окна на карте загорается отсчёт
RAID_SOON = 60 * 60

# С какого дня ведётся жребий. День берётся от этой даты, а не от начала
# эпохи: цепочку приходится проходить шагами, и лишние полвека шагов
# ничего не добавляют
SLOT_EPOCH = date(2026, 1, 1)

# Посчитанные дни: жребий — чистая функция от даты, но считается цепочкой
# от SLOT_EPOCH, и без памяти каждый вызов шёл бы этот путь заново
_SLOTS: dict[date, tuple[int, ...]] = {}


def _draw(day: date, choices: tuple[int, ...]) -> tuple[int, ...]:
    """Два слота из предложенных. Жребий заведён датой — он один на всех.

    Одинаковый у бота, мини-аппа и у каждого перезапуска: расписание
    нельзя держать в памяти процесса, иначе после рестарта подвал
    откроется в другое время, чем обещал час назад.
    """
    rng = random.Random(f"vegas-raid:{day.isoformat()}")
    return tuple(sorted(rng.sample(choices, min(RAIDS_PER_DAY, len(choices)))))


def slots_on(day: date) -> tuple[int, ...]:
    """Часы, в которые подвал открыт в этот день.

    Сегодняшние слоты выбираются из тех, которых не было вчера, — поэтому
    два дня подряд подвал не открывается в одно и то же время. Вчерашние
    же берутся тем же правилом, так что цепочка тянется от SLOT_EPOCH;
    пройденное запоминаем, иначе путь считался бы на каждый вопрос.
    """
    if day <= SLOT_EPOCH:
        return _draw(day, RAID_SLOTS)
    known = _SLOTS.get(day)
    if known is not None:
        return known
    # Идём от последнего посчитанного дня, а не от начала: обычно это
    # вчерашний, и шаг выходит один
    step = SLOT_EPOCH
    while step + timedelta(days=1) in _SLOTS and step < day:
        step += timedelta(days=1)
    slots = _SLOTS.get(step) or _draw(step, RAID_SLOTS)
    _SLOTS[step] = slots
    while step < day:
        step += timedelta(days=1)
        free = tuple(hour for hour in RAID_SLOTS if hour not in slots)
        slots = _draw(step, free)
        _SLOTS[step] = slots
    return slots


def _day_of(moment: int) -> date:
    """Московская дата этого момента: сутки расписания считаются по ней."""
    return datetime.fromtimestamp(moment, MOSCOW).date()


def _start_of(day: date, hour: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=MOSCOW).timestamp())


def windows_on(day: date) -> tuple[Window, ...]:
    """Оба окна этого дня, по порядку."""
    return tuple(
        Window(_start_of(day, hour), _start_of(day, hour) + WINDOW_HOURS * 3600)
        for hour in slots_on(day)
    )


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
    """Открыт ли подвал прямо сейчас. None — закрыт, ждите следующего окна.

    Смотрим и вчерашний день: окно с полуночи кончается в два часа ночи,
    и в час ночи открыло его вчерашнее расписание, а не сегодняшнее.
    """
    moment = now_ts() if moment is None else moment
    today = _day_of(moment)
    for day in (today - timedelta(days=1), today):
        for window in windows_on(day):
            if window.start <= moment < window.end:
                return window
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
    day = _day_of(moment)
    # Двух дней хватает: в каждом по два окна, и первое завтрашнее всегда
    # позже сегодняшнего вечера
    for step in (day, day + timedelta(days=1)):
        for window in windows_on(step):
            if window.start > moment:
                return window
    raise AssertionError("в сутках нет окна рейда")  # pragma: no cover


def schedule_text(moment: int | None = None) -> str:
    """Сегодняшнее расписание словами: «с 8:00 до 10:00 и с 16:00 до 18:00 мск».

    Расписание на день, а не общее правило: слоты каждый день свои, и
    список всех пяти сказал бы человеку не то, что будет сегодня.
    """
    moment = now_ts() if moment is None else moment
    hours = slots_on(_day_of(moment))
    return (
        "сегодня "
        + " и ".join(f"с {opens}:00 до {opens + WINDOW_HOURS}:00" for opens in hours)
        + " мск"
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
    # Как называется сам рейд на этого босса. Пусто — «Рейд против кого-то»
    raid_title: str = ""

    @property
    def image(self) -> str:
        return art.boss(self.code)

    @property
    def raid_name(self) -> str:
        """Заголовок рейда: у босса он свой, а не «рейд против такого-то»."""
        return self.raid_title or f"Рейд против {self.whom}"

    @property
    def whom(self) -> str:
        """Кого бьём: «рейд против Босса Подвала»."""
        return self.genitive or self.title


BOSSES: tuple[Boss, ...] = (
    Boss(
        # Код не трогаем: по нему лежат картинка и вся прошлая история рейдов
        code="cellar_boss",
        title="Босс Казино",
        emoji="🩸",
        class_code="tank",
        weapon="sledge",
        genitive="Босса Казино",
        tagline="Он тут всё построил и всех похоронил.",
        raid_title="Ограбление Босса Казино",
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
    "FATIGUE_WAVES",
    "ELIXIR_CHANCE",
    "MIN_PARTY",
    "MOSCOW",
    "RAID_PURSE",
    "RAID_SLOTS",
    "RAIDS_PER_DAY",
    "RAID_SOON",
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
    "slots_on",
    "windows_on",
    "elixir_for",
    "next_window",
    "schedule_text",
    "shares_of",
    "window_of",
]
