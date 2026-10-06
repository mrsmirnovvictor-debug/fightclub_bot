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
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Iterable

from bot.game import art
from bot.game.clock import MOSCOW, club_day as _day_of
from bot.game.classes import (
    FIGHTER_CLASSES,
    ALL_ZONES,
    BLOCK_WIDTH,
    Zone,
    block_combo,
)
from bot.game.combat import Action, Fighter
from bot.game.economy import MAX_LEVEL
from bot.game.equipment import (
    Equipment,
    FAN_SHELF,
    OwnedItem,
    Slot,
    get_item,
    shelf_of,
)
from bot.game.health import now_ts
from bot.game.potions import RAID_PASS, STADIUM_PASS, get_potion
from bot.game.reference import (
    best_kit,
    developed_stats,
    fan_only_kit,
)

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


@dataclass(frozen=True)
class Spoils:
    """Что падает за победу сверх кредитов. У каждого рейда своё.

    Три разных броска, и путать их нельзя.

    **Вещь — лучшему по урону.** Берётся с названной полки целиком, не
    глядя на класс: «рандомная вещь из магазина» и есть рандомная. Чужую
    линию можно продать или выменять, а подбирать приз под класс значило
    бы обещать полезное — чего не обещали.

    **Склянка лучшему по урону** — награда подвала, и она же разменная
    монета: падает чаще вещи, стоит меньше.

    **Склянка всем сразу или никому** — один бросок на весь отряд, а не
    по броску на человека. Так и задумано: либо после боя пьют все, либо
    не пьёт никто, и это заметное событие, а не лотерея, в которой одному
    повезло, а четверым нет.
    """

    # Полка, с которой падает вещь лучшему по урону, и с какой вероятностью
    item_shelf: str = ""
    item_chance: float = 0.0
    # Склянка лучшему по урону
    elixir_chance: float = 0.0
    elixirs: tuple[str, ...] = ()
    # Склянка всем сразу: один бросок на отряд
    potion_chance: float = 0.0
    potions: tuple[str, ...] = ()

    def item_for(self, rng: random.Random | None = None) -> str | None:
        """Вещь лучшему по урону. None — не выпала или полка не назначена."""
        if not self.item_shelf or not self.item_chance:
            return None
        rng = rng or random
        if rng.random() >= self.item_chance:
            return None
        goods = shelf_of(self.item_shelf)
        return rng.choice([item.code for item in goods]) if goods else None

    def elixir_for(self, rng: random.Random | None = None) -> str | None:
        """Склянка лучшему по урону. None — не выпала."""
        if not self.elixirs or not self.elixir_chance:
            return None
        rng = rng or random
        if rng.random() >= self.elixir_chance:
            return None
        return rng.choice(self.elixirs)

    def potion_for(self, rng: random.Random | None = None) -> str | None:
        """Склянка всему отряду — или никому. Бросок один на всех."""
        if not self.potions or not self.potion_chance:
            return None
        rng = rng or random
        if rng.random() >= self.potion_chance:
            return None
        return rng.choice(self.potions)


CELLAR_SPOILS = Spoils(elixir_chance=ELIXIR_CHANCE, elixirs=ELIXIR_PRIZES)


def prize_of(code: str) -> tuple[str, str] | None:
    """Приз по коду: значок и название. None — ни вещь, ни склянка.

    Призом бывает и то, и другое, а код у них общего вида. Разбирать его
    в двух местах — в рассказе судьи и в истории боёв — значит однажды
    разойтись: там, где не угадали, приз просто не покажется.
    """
    item = get_item(code)
    if item is not None:
        return item.emoji, item.title
    potion = get_potion(code)
    if potion is not None:
        return potion.emoji, potion.title
    return None

# ---------- когда подвал открыт ----------
#
# Босса пускают бить не когда угодно, а дважды в сутки по два часа.
# Слотов пять, из них на день выпадают два — и не те же, что вчера:
# расписание, которое можно выучить наизусть, перестаёт быть событием, а
# одно и то же время изо дня в день отсекает тех, кто в этот час работает.
# Время московское и без перевода часов, поэтому смещение постоянное.
# Часы общие с остальным клубом: `MOSCOW` и `_day_of` берутся из
# `bot.game.clock`.
# По какой ступени прилавка одет босс. Своя, а не отрядная: см. boss_kit
BOSS_GEAR_LEVEL = MAX_LEVEL

# Откуда берётся комплект противника: клубный прилавок или фанатский
# магазин «Северный Вал». Фанатская линия своя у каждого класса, и
# надевать её противнику — то же, что надеть на него форму
SHOWCASE_OUTFIT = "showcase"
FAN_OUTFIT = "fan"

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


def _start_of(day: date, hour: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=MOSCOW).timestamp())


def windows_on(day: date, schedule: "Schedule | None" = None) -> tuple[Window, ...]:
    """Окна этого дня, по порядку. Без расписания — расписание казино."""
    return (schedule or CELLAR_SCHEDULE).windows_on(day)


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


# ---------- расписания ----------
#
# Их два, и они разной природы. Казино открывается по жребию: два окна в
# сутки из пяти возможных, и не те же, что вчера. Стадион — наоборот, по
# твёрдым дням недели: гопники приходят на матч, а матчи по расписанию, и
# выучить его не грех, а смысл — на матч собираются заранее.
#
# Общее у них одно: «какие окна в этот день». Всё остальное — чей день
# открыт, когда следующее окно, как это сказать словами — считается от
# этого одного вопроса, и поэтому пишется один раз на оба расписания.


@dataclass(frozen=True)
class Lottery:
    """Жребий: по два окна в сутки, и не те же, что вчера."""

    slots: tuple[int, ...] = RAID_SLOTS
    hours: int = WINDOW_HOURS

    def windows_on(self, day: date) -> tuple[Window, ...]:
        return tuple(
            Window(_start_of(day, hour), _start_of(day, hour) + self.hours * 3600)
            for hour in slots_on(day)
        )

    def text(self, day: date) -> str:
        """«сегодня с 8:00 до 10:00 и с 16:00 до 18:00 мск».

        Расписание на день, а не общее правило: слоты каждый день свои, и
        список всех пяти сказал бы человеку не то, что будет сегодня.
        """
        return (
            "сегодня "
            + " и ".join(
                f"с {opens}:00 до {opens + self.hours}:00" for opens in slots_on(day)
            )
            + " мск"
        )


@dataclass(frozen=True)
class Weekly:
    """Твёрдое расписание: в такие-то дни недели с такого-то часа.

    Дни недели — по питоновскому счёту, где понедельник ноль. Окно одно
    на день: шесть часов подряд, а не два по три.
    """

    weekdays: tuple[int, ...]
    hour: int
    hours: int

    def windows_on(self, day: date) -> tuple[Window, ...]:
        if day.weekday() not in self.weekdays:
            return ()
        start = _start_of(day, self.hour)
        return (Window(start, start + self.hours * 3600),)

    def text(self, day: date) -> str:
        """«по средам и субботам с 18:00 до 00:00 мск» — одно и то же всегда.

        Конец окна приводим к суткам: шесть часов от шести вечера дают
        двадцать четыре, а такого часа на часах не бывает. Окно,
        кончающееся в полночь, и в карточке подписано «до 00:00» —
        считает его там `Window.title` по настоящему времени, и расходись
        эти две подписи, человек решил бы, что у рейда два расписания.
        """
        names = ", ".join(WEEKDAY_WHEN[number] for number in sorted(self.weekdays))
        last = names.rpartition(", ")
        if last[0]:
            names = f"{last[0]} и {last[2]}"
        closes = (self.hour + self.hours) % 24
        return f"по {names} с {self.hour}:00 до {closes:02d}:00 мск"


# «по средам и субботам» — падеж хранится готовым: склонять названия
# дней правилами не выйдет, а их всего семь
WEEKDAY_WHEN: tuple[str, ...] = (
    "понедельникам",
    "вторникам",
    "средам",
    "четвергам",
    "пятницам",
    "субботам",
    "воскресеньям",
)

Schedule = Lottery | Weekly

CELLAR_SCHEDULE = Lottery()


def _window_at(moment: int, hours: int = WINDOW_HOURS) -> Window:
    """Окно, которое началось в этот час. Часы берём московские."""
    here = datetime.fromtimestamp(moment, MOSCOW)
    start = here.replace(minute=0, second=0, microsecond=0)
    return Window(
        start=int(start.timestamp()),
        end=int(start.timestamp()) + hours * 3600,
    )


def window_of(
    moment: int | None = None, schedule: Schedule | None = None
) -> Window | None:
    """Открыто ли прямо сейчас. None — закрыто, ждите следующего окна.

    Смотрим и вчерашний день: окно с полуночи кончается в два часа ночи,
    и в час ночи открыло его вчерашнее расписание, а не сегодняшнее.
    """
    moment = now_ts() if moment is None else moment
    today = _day_of(moment)
    for day in (today - timedelta(days=1), today):
        for window in windows_on(day, schedule):
            if window.start <= moment < window.end:
                return window
    return None


def any_window(
    moment: int | None = None, schedule: Schedule | None = None
) -> Window:
    """Окно на каждый такой отрезок суток — для снятого расписания.

    Правило «одна победа на окно» должно работать и когда рейд открыт
    круглосуточно, иначе выключатель заодно снимает и его. Длина отрезка
    своя у каждого рейда: у шестичасового окна и клетка шестичасовая.
    """
    moment = now_ts() if moment is None else moment
    hours = (schedule or CELLAR_SCHEDULE).hours
    hour = datetime.fromtimestamp(moment, MOSCOW).hour
    return _window_at(moment - (hour % hours) * 3600, hours)


# На сколько дней вперёд ищем следующее окно. Недельному расписанию мало
# двух дней: от воскресенья до среды четыре шага, и ещё один на то, чтобы
# в субботу после матча назвать следующую среду
WINDOW_SEARCH_DAYS = 8


def next_window(
    moment: int | None = None, schedule: Schedule | None = None
) -> Window:
    """Ближайшее окно после этого момента — то, которого ждут."""
    moment = now_ts() if moment is None else moment
    day = _day_of(moment)
    for step in range(WINDOW_SEARCH_DAYS):
        for window in windows_on(day + timedelta(days=step), schedule):
            if window.start > moment:
                return window
    raise AssertionError("в расписании нет ни одного окна")  # pragma: no cover


def schedule_text(
    moment: int | None = None, schedule: Schedule | None = None
) -> str:
    """Расписание словами. У жребия — сегодняшнее, у матчей — всегдашнее."""
    moment = now_ts() if moment is None else moment
    return (schedule or CELLAR_SCHEDULE).text(_day_of(moment))


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
    RaidEnd.WIN: "Противник повержен",
    RaidEnd.DRAW: "Разменялись насмерть",
    RaidEnd.LOSS: "Отряд не выстоял",
}
RAID_END_EMOJI = {RaidEnd.WIN: "🏆", RaidEnd.DRAW: "🤝", RaidEnd.LOSS: "💀"}


# ---------- повадки босса ----------
#
# Босс не игрок: у него нет прошлых боёв, по которым аналитик читает
# живого соперника. Зато у него есть характер, и он записан здесь —
# весами по зонам. Кувалда ходит сверху, щит прикрывает голову, и по
# ногам такой боец бьёт редко.
#
# Один и тот же объект читают и кости, и аналитик подписчика. Разойтись
# им нельзя: подсказка, посчитанная не по тем числам, по которым босс
# бьёт, — это не аналитика, а враньё. Поэтому весов ровно одна копия, и
# лежит она у самого босса.
#
# Веса — не проценты: их приводят к сотне сами. Так правку можно вносить
# на глаз («по ногам пусть бьёт вдвое реже»), не пересчитывая остальные.

# К какой сумме приводим веса. Сто — чтобы доли читались процентами
TEMPER_SCALE = 100


@dataclass(frozen=True)
class Temper:
    """Характер босса: куда он бьёт и где держит блок.

    `attacks` — веса зон удара. `guards` — веса зоны, с которой босс
    начинает блок; сколько зон он этим закроет, решает его снаряжение,
    а не характер: со щитом блок шире, и это правило боя, а не повадка.
    """

    attacks: tuple[tuple[str, int], ...] = ()
    guards: tuple[tuple[str, int], ...] = ()

    @staticmethod
    def _spread(rows: tuple[tuple[str, int], ...]) -> dict[str, float]:
        """Веса зон, приведённые к сотне. Пусто — все зоны поровну."""
        weights = {zone.value: 0.0 for zone in ALL_ZONES}
        for code, weight in rows:
            if code in weights:
                weights[code] = float(max(0, weight))
        total = sum(weights.values())
        if total <= 0:
            even = TEMPER_SCALE / len(weights)
            return dict.fromkeys(weights, even)
        return {
            code: weight * TEMPER_SCALE / total for code, weight in weights.items()
        }

    @property
    def swings(self) -> dict[str, float]:
        """Куда он бьёт: доли по зонам в сумме на сотню."""
        return self._spread(self.attacks)

    @property
    def stances(self) -> dict[str, float]:
        """С какой зоны он начинает блок."""
        return self._spread(self.guards)

    def covers(self, width: int = BLOCK_WIDTH) -> dict[str, float]:
        """Как часто каждая зона оказывается закрытой.

        Блок держит несколько смежных зон разом, поэтому сумма здесь
        больше сотни — это не ошибка счёта, а несколько зон на один блок.
        По отдельной зоне число по-прежнему читается вероятностью.
        """
        covered = {zone.value: 0.0 for zone in ALL_ZONES}
        for code, weight in self.stances.items():
            for zone in block_combo(Zone(code), width):
                covered[zone.value] += weight
        return covered

    def turned(self, step: int) -> "Temper":
        """Тот же характер, повёрнутый по кольцу зон.

        Боец не стоит в одной стойке весь бой: между волнами босс
        перекладывает щит и меняет замах. Повадка при этом та же — та же
        форма перекоса, просто на других зонах.

        Поворот, а не новые числа: так за целый круг каждая зона бывает
        и любимой, и брошенной поровну. Отряд, который жмёт одну и ту же
        кнопку, в среднем получает ровно столько же, сколько получал от
        босса без характера, — а выигрывает тот, кто читает стойку
        каждую волну.
        """
        codes = [zone.value for zone in ALL_ZONES]
        count = len(codes)
        step %= count

        def rolled(rows: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
            weights = dict(rows)
            return tuple(
                (codes[(index + step) % count], weights.get(code, 0))
                for index, code in enumerate(codes)
            )

        return Temper(attacks=rolled(self.attacks), guards=rolled(self.guards))

    def _pick(self, weights: dict[str, float], rng) -> Zone:
        codes = list(weights)
        return Zone(rng.choices(codes, weights=[weights[c] for c in codes])[0])

    def swing(self, rng) -> Zone:
        """Куда он ударит в этот раз."""
        return self._pick(self.swings, rng)

    def stance(self, rng) -> Zone:
        """С какой зоны он закроется в этот раз."""
        return self._pick(self.stances, rng)


# Босс без характера: бьёт и закрывается как придётся. Таким он и был,
# пока повадок не завели, — и таким останется тот, кому их не прописали
EVEN_TEMPER = Temper()

# Сколько разных стоек у босса — по одной на зону кольца
STANCES = len(ALL_ZONES)


def boss_stance(rng: random.Random | None = None) -> int:
    """Какую стойку босс примет на эту волну."""
    return (rng or random).randrange(STANCES)


@dataclass(frozen=True)
class Boss:
    """NPC, против которого идёт рейд.

    `weapon` — код оружия, которым босс бьёт, `gear` — остальной его
    комплект. Чего в `gear` нет, добирается лучшим с прилавка: рейд-босс
    приходит одетым в любом случае.
    """

    code: str
    title: str
    emoji: str
    class_code: str
    weapon: str
    # Что у него во второй руке: щит, второй клинок или ничего. Пусто —
    # вторая рука пуста, и прилавок её не наполняет: он подбирал туда
    # самое дорогое, что в слот лезет, и трикстеру доставался щиток,
    # которого в его линии нет и который он в руки не возьмёт
    offhand: str = ""
    # Свои вещи босса по слотам. Пусто — оденем с прилавка
    gear: tuple[str, ...] = ()
    # «рейд против Босса Подвала»: падеж хранится рядом с именем, а не
    # угадывается по окончанию — прозвища не склоняются по правилам
    genitive: str = ""
    tagline: str = ""
    # Как называется сам рейд на этого босса. Пусто — «Рейд против кого-то»
    raid_title: str = ""
    # Повадки: по ним он бьёт, по ним же его читает аналитик подписчика.
    # Не задали — дерётся как придётся, и аналитик честно скажет, что
    # зоны у него все поровну
    temper: Temper = EVEN_TEMPER
    # Чем его повадка объясняется — одной строкой, для разбора аналитика
    manner: str = ""
    # С какого прилавка его одевают. Босс Казино донашивает клубное,
    # гопники со стадиона ходят в фанатском — для них это форма, а не
    # случайный набор, и класс в ней уже выверен кругом из `fan_kit`
    outfit: str = SHOWCASE_OUTFIT
    # По какому уровню ему распределены характеристики. Ноль — по своему,
    # как эталонному бойцу. Меньше своего — он одет как боец, а накачан
    # хуже: у гопника есть форма, но нет зала
    build_level: int = 0
    # Портрет из папки образов, а не из папки боссов: гопники рисовались
    # вместе с фанатской линией одежды, и лицо у них общее на класс —
    # четыре файла на тринадцать человек. Пусто — портрет ищется в
    # `bosses/` под кодом, как у босса казино
    portrait: str = ""

    @property
    def image(self) -> str:
        return art.avatar(self.portrait) if self.portrait else art.boss(self.code)

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
        weapon="boss_sledge",
        gear=(
            "boss_helmet",
            "boss_shield",
            "boss_tee",
            "boss_belt",
            "boss_gloves",
            "boss_jacket",
            "boss_pants",
            "boss_boots",
        ),
        genitive="Босса Казино",
        tagline="Он тут всё построил и всех похоронил.",
        raid_title="Ограбление Босса Казино",
        # Кувалда ходит сверху, щит стоит у лица: в своей стойке он
        # бьёт выше и закрывается выше. Перекос нарочно небольшой —
        # четверть разницы между самой частой зоной и самой редкой.
        # Сильнее делать нельзя: уже при половине отряд, читающий
        # стойку, перестаёт проигрывать вовсе, а подвал должен остаться
        # боем, а не чтением таблички.
        #
        # На отряд без подписки это не влияет никак: стойку босс
        # поворачивает каждую волну, и за круг каждая зона бывает и
        # любимой, и брошенной поровну. Тот, кто жмёт одну и ту же
        # кнопку, получает ровно те же проценты, что и от босса без
        # характера, — это посчитано, а не на глаз
        temper=Temper(
            attacks=(
                ("head", 23), ("chest", 23), ("belly", 20),
                ("belt", 18), ("legs", 16),
            ),
            guards=(
                ("head", 25), ("chest", 22), ("belly", 18),
                ("belt", 18), ("legs", 17),
            ),
        ),
        manner="Бьёт кувалдой сверху и держит щит у лица.",
    ),
)

# ---------- банда со стадиона ----------
#
# Второй рейд устроен иначе первого, и разница в одном: противник не
# один. Против отряда выходит банда футбольных фанатов, и валить её
# приходится по человеку.
#
# Поэтому у банды нет прибавки к здоровью за лишнего бойца отряда — у неё
# вместо прибавки лишние тела: начальный состав встречает хоть троих,
# хоть пятерых, а с шестого стороны равны, и каждый лишний боец приводит
# себе лишнего противника.
#
# Одеты все в фанатское из «Северного Вала» — своей линией на класс, и
# только в него: это форма, а не гардероб. Чего в линии нет, того у
# гопника нет вовсе — перчаток там нет, и дерётся он голыми руками. Что
# у него во второй руке, написано у него самого: щит воину и лидеру, по
# ножу в каждой руке ассасину, трикстеру с его зонтом — ничего.
#
# Сложность рейда держит не одежда, а выучка. Гопник одет как боец
# десятого уровня, а характеристики ему распределены по третьему: форма
# у него есть, зала за ним нет. Ступень эта мереная, а не на глаз —
# `scripts/gang_raid.py` гоняет тот же круг и тот же размен, которыми
# идёт настоящий рейд, и говорит, что выходит у отряда, который просто
# тыкает кнопки.
#
# Ступень ездила дважды и оба раза вслед за одеждой. Пока «Северный Вал»
# был слабее клубной витрины, рейд брался в 100% прогонов и выучка его не
# спасала вовсе. Фанатскую линию подняли над сетами девятого уровня — и
# та же седьмая ступень сделала рейд непроходимым: 0.5–11%. По третьей
# выходит 9–40%, и это нижняя граница: прогон жмёт наугад, без аналитика,
# приёмов и склянок, а живой отряд всем этим как раз и вытягивает.
#
# Числа видно и в игре: в карточке по кнопке «i» у гопника стоят его
# характеристики, и они заметно ниже, чем у бойца в таком же комплекте.
GANG_BUILD = 3

# Уровень гопников твёрдый: они не подстраиваются под отряд, как босс
# казино, и выше игроков не бывают. Сложность рейда — в их числе
GANG_LEVEL = MAX_LEVEL

# Сколько человек должна собрать стычка. Трое — не прихоть: банда выходит
# впятером, и вдвоём против пятерых нечего и начинать
GANG_PARTY = 3

# Сколько получает за победу каждый. Не делится на отряд, в отличие от
# казино: билет на матч каждый покупает свой, и доля за него не должна
# зависеть от того, сколько народу пришло. Втроём и вдесятером сделка у
# человека одна и та же — пятьдесят за вход, двести пятьдесят за победу.
#
# Большой отряд при этом легче не живёт, а наоборот: с шестерых стороны
# равны, и каждый лишний боец приводит себе лишнего противника. Делить
# кошель на отряд было бы поэтому вдвойне неверно — втроём за самый
# трудный бой доставалось бы по шестьдесят семь, меньше чем за два
# билета.
GANG_PURSE = 250

# Когда фанаты выходят со стадиона: среда и суббота, с шести вечера до
# полуночи. Расписание твёрдое и не прячется: на матч собираются заранее.
#
# Окно упирается в полночь и за неё не переходит: оно кончается ровно в
# 00:00, и час ночи — уже закрыто. Так же его читает и `window_of` —
# смотрит и вчерашний день, потому что окно, начавшееся вчера вечером,
# сегодняшним расписанием не открыто
GANG_SCHEDULE = Weekly(weekdays=(2, 5), hour=18, hours=6)

# Сколько у бойца есть на свой удар. Минута, а не полминуты: в банде
# цель каждую волну новая, и прочесть её надо успеть
GANG_TURN_SECONDS = 60

# Склянки, которые падают всему отряду разом: по характеристике на
# каждую и одна на запас здоровья
GANG_POTIONS: tuple[str, ...] = (
    "boost_strength",
    "boost_agility",
    "boost_endurance",
    "boost_hp",
)

# Что с банды падает. Вещь — лучшему по урону, и своя линия у неё не
# спрашивается: что упало с фанатского прилавка, то и упало. Склянка —
# всем сразу или никому, одним броском на отряд
GANG_SPOILS = Spoils(
    item_shelf=FAN_SHELF,
    item_chance=0.75,
    potion_chance=0.8,
    potions=GANG_POTIONS,
)

# Повадки по классу: внутри банды они общие, потому что общее у них всё
# — выучка, форма и привычка драться толпой. Различать их по человеку
# значило бы придумывать тринадцать характеров там, где есть четыре.
ROGUE_TEMPER = Temper(
    attacks=(("legs", 24), ("belt", 23), ("belly", 20), ("chest", 18), ("head", 15)),
    guards=(("legs", 25), ("belt", 23), ("belly", 19), ("chest", 17), ("head", 16)),
)
WARRIOR_TEMPER = Temper(
    attacks=(("chest", 25), ("belly", 23), ("head", 19), ("belt", 18), ("legs", 15)),
    guards=(("chest", 24), ("belly", 22), ("head", 20), ("belt", 18), ("legs", 16)),
)
ASSASSIN_TEMPER = Temper(
    attacks=(("belly", 25), ("belt", 24), ("chest", 19), ("legs", 17), ("head", 15)),
    guards=(("belly", 23), ("belt", 22), ("chest", 20), ("legs", 18), ("head", 17)),
)


def _hooligan(code: str, nick: str, kind: str) -> Boss:
    """Один гопник с кличкой. Всё остальное у него от класса.

    Кличка — это всё, чем они различаются, и этого достаточно: на табло
    из тринадцати строк «Ассасин №3» ничего не говорит, а «Мелкий»
    запоминается с первого боя.
    """
    look = {
        # Трикстер выходит с зонтом и пустой второй рукой: щит он в руки
        # не возьмёт, а второго зонта у него нет
        "rogue": ("🤸", "fan_rogue", "fan_rogue_umbrella", "", ROGUE_TEMPER,
                  "Метит по ногам и сам закрывается низко."),
        # Воину щит положен — тот же, что и лидеру: в фанатской линии он
        # один на двоих
        "warrior": ("⚔️", "fan_warrior", "fan_warrior_bat", FAN_SHIELD,
                    WARRIOR_TEMPER,
                    "Работает по корпусу, широко и без выдумки."),
        # У ассасина нож в каждой руке — и бьёт он поэтому дважды за ход
        "assassin": ("🗡️", "fan_assassin", "fan_assassin_knife",
                     "fan_assassin_knife", ASSASSIN_TEMPER,
                     "Два ножа ходят в живот и под ремень."),
    }[kind]
    emoji, portrait, weapon, second, temper, manner = look
    return Boss(
        code=code,
        title=nick,
        emoji=emoji,
        class_code=kind,
        weapon=weapon,
        offhand=second,
        outfit=FAN_OUTFIT,
        build_level=GANG_BUILD,
        portrait=portrait,
        temper=temper,
        manner=manner,
    )


# Щит в фанатской линии один, и носят его только двое: лидер и воины.
# Трикстеру с ассасином щита не положено — у одного зонт, у другого ножи
FAN_SHIELD = "fan_boss_shield"

GANG_LEADER = Boss(
    code="gang_major",
    title="Лидер банды — Майор",
    emoji="🪖",
    class_code="tank",
    weapon="fan_boss_bat",
    offhand=FAN_SHIELD,
    outfit=FAN_OUTFIT,
    portrait="fan_boss",
    tagline="Держит сектор и отвечает за всех, кто в нём орёт.",
    temper=Temper(
        attacks=(("head", 23), ("chest", 23), ("belly", 20), ("belt", 18), ("legs", 16)),
        guards=(("head", 25), ("chest", 22), ("belly", 18), ("belt", 18), ("legs", 17)),
    ),
    manner="Бьёт битой сверху и держит щит у лица.",
)

# Тринадцать кличек: лидер и по четыре на класс. Каждый выходит под своей
# — повторов в банде нет, и нумеровать однофамильцев не приходится
GANG_ROGUES: tuple[Boss, ...] = (
    _hooligan("gang_mazhorchik", "Мажорчик", "rogue"),
    _hooligan("gang_valera", "Валера", "rogue"),
    _hooligan("gang_seryy", "Серый", "rogue"),
    _hooligan("gang_toshchiy", "Тощий", "rogue"),
)
GANG_WARRIORS: tuple[Boss, ...] = (
    _hooligan("gang_yaryy", "Ярый", "warrior"),
    _hooligan("gang_sedoy", "Седой", "warrior"),
    _hooligan("gang_dubina", "Дубина", "warrior"),
    _hooligan("gang_arkadich", "Аркадич", "warrior"),
)
GANG_ASSASSINS: tuple[Boss, ...] = (
    _hooligan("gang_britva", "Бритва", "assassin"),
    _hooligan("gang_kastet", "Кастет", "assassin"),
    _hooligan("gang_melkiy", "Мелкий", "assassin"),
    _hooligan("gang_killer", "Киллер", "assassin"),
)

# Кого банда выставляет на троих — пятеро, считая лидера
GANG_CREW: tuple[Boss, ...] = (
    GANG_ROGUES[0],
    GANG_WARRIORS[0],
    GANG_ASSASSINS[0],
    GANG_ASSASSINS[1],
)

# Сколько человек встречает начальный состав банды. Трое, четверо и
# пятеро дерутся с теми же пятью: на троих это перевес банды, на пятерых
# — поровну. Шестой приводит шестого, и дальше состав всегда ровный, до
# десяти на десять.
#
# Раньше банда прибавляла по человеку с четвёртого, и вдесятером отряд
# встречали двенадцать. Перевес «всегда на двоих» не держался на числе:
# трое против пятерых — это полтора на одного, а десять против
# двенадцати — один и две десятых, то есть большой отряд выигрывал не от
# своей силы, а от арифметики
GANG_SQUAD = len(GANG_CREW) + 1

# Кого добавляют за каждого бойца сверх начального состава — по кругу:
# трикстер, воин, ассасин, и каждый следующий под новой кличкой. Пятерых
# хватает на полный отряд: вдесятером банда выходит десятью. Остальные
# три клички ждут своего часа — они выйдут, только если поднять потолок
GANG_RESERVE: tuple[Boss, ...] = (
    GANG_ROGUES[1], GANG_WARRIORS[1], GANG_ASSASSINS[2],
    GANG_ROGUES[2], GANG_WARRIORS[2], GANG_ASSASSINS[3],
    GANG_ROGUES[3], GANG_WARRIORS[3],
)

# Вся банда списком: по ней ищут гопника по коду и проверяют, что кличек
# хватает на любой отряд
GANG: tuple[Boss, ...] = (
    GANG_LEADER, *GANG_ROGUES, *GANG_WARRIORS, *GANG_ASSASSINS
)

CELLAR_BOSS = BOSSES[0]
BOSS_BY_CODE = {boss.code: boss for boss in BOSSES + GANG}
# У противника свой номер: он не игрок, и с чужим user_id путаться не
# должен. Банда занимает номера подряд от этого же: лидер — минус первый,
# и рейд на одного босса остаётся ровно тем, чем был
BOSS_ID = -1


def foe_id(index: int) -> int:
    """Номер противника по месту в банде. Первый — тот же BOSS_ID."""
    return BOSS_ID - index


@dataclass(frozen=True)
class RaidKind:
    """Рейд целиком: кого бьём, когда пускают и по какому пропуску.

    Рейдов в городе два, и различий между ними больше, чем похожего:
    расписание, пропуск, дом, размер отряда, кошель и сам противник. Всё
    это лежит здесь, одним предметом, а служба читает его, а не хранит
    своё — иначе третий рейд пришлось бы вписывать в каждую ручку
    отдельно.
    """

    code: str
    title: str
    emoji: str
    # Дом, в котором рейд идёт. Служба им не пользуется — его спрашивает
    # карта, чтобы знать, под какой вывеской вешать плашку
    house: str
    pass_code: str
    schedule: Schedule
    leader: Boss
    # Кто с лидером с самого начала
    crew: tuple[Boss, ...] = ()
    # Кого добавляют за каждого бойца сверх `free_slots` — по кругу
    reserve: tuple[Boss, ...] = ()
    # Сколько бойцов банда встречает своим начальным составом. Дальше
    # состав ровный: за каждого следующего выходит ещё один
    free_slots: int = 0
    min_party: int = MIN_PARTY
    max_party: int = MAX_PARTY
    purse: int = RAID_PURSE
    # Кошель делится на отряд или достаётся каждому целиком. Делить
    # можно там, где толпа облегчает бой; где банда растёт вместе с
    # отрядом, деление было бы наказанием за то, что пришли втроём
    split: bool = True
    # Твёрдый уровень противников. Ноль — считать от отряда, как в казино
    foe_level: int = 0
    hp_share: float = BOSS_HP_SHARE
    tagline: str = ""
    # Что падает за победу сверх кредитов
    spoils: Spoils = CELLAR_SPOILS
    # Сколько у бойца есть на свой удар. Ноль — брать из настроек
    turn_seconds: int = 0

    @property
    def one_on_one(self) -> bool:
        """Против отряда один противник — весь рейд про него."""
        return not self.crew and not self.reserve

    def roster(self, party: int) -> tuple[Boss, ...]:
        """Кто выйдет против отряда такого размера."""
        foes = [self.leader, *self.crew]
        extra = max(0, party - self.free_slots) if self.reserve else 0
        for step in range(extra):
            foes.append(self.reserve[step % len(self.reserve)])
        return tuple(foes)

    def shares(self, party: int) -> list[int]:
        """Кому сколько из кошелька за победу."""
        if self.split:
            return shares_of(self.purse, party)
        return [self.purse] * max(0, party)

    def window_now(self, moment: int | None = None) -> Window | None:
        return window_of(moment, self.schedule)

    def next_window(self, moment: int | None = None) -> Window:
        return next_window(moment, self.schedule)

    def any_window(self, moment: int | None = None) -> Window:
        return any_window(moment, self.schedule)

    def schedule_text(self, moment: int | None = None) -> str:
        return schedule_text(moment, self.schedule)


CELLAR_RAID = RaidKind(
    code="cellar",
    title=CELLAR_BOSS.raid_name,
    emoji=CELLAR_BOSS.emoji,
    house="casino",
    pass_code=RAID_PASS,
    schedule=CELLAR_SCHEDULE,
    leader=CELLAR_BOSS,
    tagline=CELLAR_BOSS.tagline,
    spoils=CELLAR_SPOILS,
)

HOOLIGAN_RAID = RaidKind(
    code="hooligans",
    title="Стычка с футбольными фанатами",
    emoji="🪖",
    house="stadium",
    pass_code=STADIUM_PASS,
    schedule=GANG_SCHEDULE,
    leader=GANG_LEADER,
    crew=GANG_CREW,
    reserve=GANG_RESERVE,
    # До пятерых банда выходит своим составом — впятером. Дальше поровну:
    # шестеро на шестерых, десять на десять
    free_slots=GANG_SQUAD,
    min_party=GANG_PARTY,
    purse=GANG_PURSE,
    # Каждому своё: билет на матч у каждого свой, и доля за победу тоже
    split=False,
    foe_level=GANG_LEVEL,
    # Прибавки к здоровью у банды нет: у неё вместо прибавки лишние тела
    hp_share=0.0,
    tagline="Фанатский сектор вываливается со стадиона и ищет, с кем поговорить.",
    spoils=GANG_SPOILS,
    turn_seconds=GANG_TURN_SECONDS,
)

RAID_KINDS: tuple[RaidKind, ...] = (CELLAR_RAID, HOOLIGAN_RAID)
KIND_BY_CODE = {kind.code: kind for kind in RAID_KINDS}


def get_kind(code: str) -> RaidKind:
    return KIND_BY_CODE.get(code, CELLAR_RAID)


def kind_at(house: str) -> RaidKind | None:
    """Какой рейд собирают в этом доме. None — здесь рейдов нет.

    Дом и решает, какой рейд: в казино спускаются к боссу, на стадионе
    встречают фанатский сектор, и выбирать из списка не нужно — боец уже
    пришёл туда, куда хотел.
    """
    for kind in RAID_KINDS:
        if kind.house == house:
            return kind
    return None


def kind_of_boss(code: str) -> RaidKind:
    """Какой рейд ведут на этого противника — для строки истории."""
    for kind in RAID_KINDS:
        if code == kind.leader.code or any(one.code == code for one in kind.crew):
            return kind
    return CELLAR_RAID


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


def boss_kit(boss: Boss, level: int = BOSS_GEAR_LEVEL) -> Equipment:
    """Комплект босса — весь его собственный, от шлема до берцев.

    Своё у босса не только оружие. Числа в комплекте те же, что у
    прилавочных вещей десятой ступени, которые он носил раньше: менялась
    не сила, а вид. Отдельные коды нужны ради картинок — общий код
    означал бы общую картинку, и арт босса перекрасил бы мотошлем
    половине клуба.

    Уровень снаряжения у босса свой и не зависит от отряда. Раньше он
    одевался по собственному уровню, а тот считается от отряда: трое
    третьего уровня встречали босса в вещах седьмого. Босс Казино — один
    на весь клуб, и одет он всегда одинаково, кто бы к нему ни пришёл.
    Расти от отряда продолжают запас здоровья и характеристики.

    Чего в его наборе не нашлось, добираем с прилавка: список вещей босса
    можно дополнять по одной, не боясь оставить слот пустым.

    Чужого класса при этом не надеваем. Прилавок добирает пустые слоты
    самым дорогим, что в них лезет, и своей вещи у класса там может не
    оказаться вовсе: ассасину так достаётся щит, который живой ассасин с
    земли не поднял бы. Снаряжение NPC видно в карточке по кнопке «i», и
    стоять в ней должно только то, что боец этого класса надеть может, —
    иначе числа врут, а объяснить это нечем.

    Своё, названное у босса, остаётся при нём в любом случае: там выбор
    сделан руками, и правило класса в нём учтено.
    """
    fclass = FIGHTER_CLASSES[boss.class_code]
    shelf = fan_only_kit if boss.outfit == FAN_OUTFIT else best_kit
    kit = {
        slot: item
        for slot, item in shelf(fclass, BOSS_GEAR_LEVEL).items()
        if not item.for_classes or boss.class_code in item.for_classes
    }
    # Вторая рука — только по имени. Прилавок её не наполняет: см. `offhand`
    kit.pop(Slot.OFFHAND, None)
    second = get_item(boss.offhand)
    if second is not None:
        kit[Slot.OFFHAND] = second
    # Выписанное в `gear` — последнее слово, в том числе и о второй руке
    for code in boss.gear:
        item = get_item(code)
        if item is not None:
            kit[item.slot] = item
    weapon = get_item(boss.weapon)
    if weapon is not None:
        kit[Slot.WEAPON] = weapon
    return Equipment(
        items={slot: OwnedItem(item=item, slot=slot) for slot, item in kit.items()}
    )


def boss_fighter(
    boss: Boss,
    levels: list[int],
    hp_share: float = BOSS_HP_SHARE,
    level: int = 0,
    name: str = "",
    number: int = BOSS_ID,
) -> Fighter:
    """Собрать противника под этот отряд: уровень по отряду, запас — по толпе.

    `level` — твёрдый уровень вместо отрядного: гопники стоят на своём
    десятом, кто бы к ним ни пришёл. `name` и `number` нужны банде, где
    противников несколько и каждому нужно своё имя и свой номер.
    """
    level = level or boss_level(levels)
    fclass = FIGHTER_CLASSES[boss.class_code]
    equipment = boss_kit(boss, level)
    stats = developed_stats(fclass, boss.build_level or level).merge(equipment.bonus)
    plain = Fighter(
        user_id=number,
        name=name or boss.title,
        fclass=fclass,
        stats=stats,
        level=level,
        equipment=equipment,
    )
    extra = round(plain.max_hp * hp_share * max(0, len(levels) - 1))
    if not extra:
        return plain
    return Fighter(
        user_id=number,
        name=name or boss.title,
        fclass=fclass,
        stats=stats,
        level=level,
        equipment=equipment,
        extra_hp=extra,
    )


def raid_foes(kind: RaidKind, levels: list[int]) -> dict[int, Fighter]:
    """Все противники этого рейда: номер → боец.

    Номера идут от BOSS_ID вниз, поэтому рейд на одного босса остаётся
    тем же, чем был: лидер под минус первым, и прошлые записи боёв
    по-прежнему про него.
    """
    return {
        foe_id(index): boss_fighter(
            boss,
            levels,
            kind.hp_share,
            level=kind.foe_level,
            number=foe_id(index),
        )
        for index, boss in enumerate(kind.roster(len(levels)))
    }


def boss_action(
    enemy: Fighter | None = None,
    rng: random.Random | None = None,
    temper: Temper | None = None,
) -> Action:
    """Чем босс отвечает на этот размен.

    Зону он не выбирает с умыслом — кидает кости, — но кости у него
    кривые: веса лежат в `temper`, и по ним же его читает аналитик
    подписчика. Без характера босс бьёт равномерно, как раньше.

    Рук у него столько же, сколько у любого бойца: со щитом одна, со вторым
    оружием две. Блок он держит той же ширины, что и его снаряжение.
    """
    rng = rng or random
    temper = temper or EVEN_TEMPER
    hands = enemy.attacks_per_round if enemy else 1
    width = enemy.block_width if enemy else BLOCK_WIDTH
    return Action(
        attacks=tuple(temper.swing(rng) for _ in range(hands)),
        block=block_combo(temper.stance(rng), width),
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


def judge_raid(
    enemies: Iterable[Fighter], fighters: dict[int, Fighter]
) -> RaidOutcome | None:
    """Кончился ли рейд, и если да — чем.

    None значит «дерёмся дальше». Противников может быть и один, и
    дюжина, и условие на всех одно: пока стоит хоть кто-то из них, рейд
    идёт. Упала вся банда — победа, а если отряд лёг с ней в один ход, то
    ничья: разменялись насмерть.
    """
    standing = [enemy for enemy in enemies if enemy.alive]
    alive = [user_id for user_id, fighter in fighters.items() if fighter.alive]
    if standing and not alive:
        return RaidOutcome(end=RaidEnd.LOSS, damage=damage_board(fighters))
    if not standing:
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
    """Приз лучшему по урону в подвале: иногда — один из эликсиров.

    Своя дверь к `CELLAR_SPOILS`, а не второе правило рядом с ним: что
    падает за победу, решает добыча рейда, и считаться это должно в одном
    месте.
    """
    return CELLAR_SPOILS.elixir_for(rng)


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
    "CELLAR_RAID",
    "CELLAR_SCHEDULE",
    "FAN_OUTFIT",
    "GANG",
    "GANG_ASSASSINS",
    "GANG_BUILD",
    "GANG_CREW",
    "GANG_POTIONS",
    "GANG_SPOILS",
    "GANG_TURN_SECONDS",
    "GANG_LEADER",
    "GANG_ROGUES",
    "GANG_SQUAD",
    "GANG_WARRIORS",
    "GANG_LEVEL",
    "GANG_PARTY",
    "GANG_PURSE",
    "GANG_RESERVE",
    "GANG_SCHEDULE",
    "HOOLIGAN_RAID",
    "KIND_BY_CODE",
    "Lottery",
    "CELLAR_SPOILS",
    "Spoils",
    "prize_of",
    "RAID_KINDS",
    "RaidKind",
    "SHOWCASE_OUTFIT",
    "Schedule",
    "WEEKDAY_WHEN",
    "Weekly",
    "foe_id",
    "get_kind",
    "kind_at",
    "kind_of_boss",
    "raid_foes",
    "BOSS_GEAR_LEVEL",
    "BOSS_HP_SHARE",
    "BOSS_ID",
    "EVEN_TEMPER",
    "STANCES",
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
    "Temper",
    "boss_action",
    "boss_stance",
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
