"""«Чёрный пуск»: правила рейда на электростанцию «Контур».

Кооперативный PvE на 3–5 человек. Отряд входит на станцию пешком, по
узлам: панорама комнаты, двери к соседям, встреча или задача внутри.
Цель — собрать три модуля запуска, охладить реактор, перезапустить
станцию и выйти через аварийные тоннели.

**Карта приходит из манифеста, а не из кода.** 64 комнаты и 76
переходов лежат в `zombiemission/mission_manifest.json` — это проектные
материалы, и переписывать их в питон значило бы завести вторую правду.
Модуль читает файл один раз, проверяет его и дальше отвечает на вопросы
о графе: кто чей сосед, что закрыто и чем.

**Проверка обязательна.** Манифест — дизайн, а не движок: в нём могут
оказаться висящие комнаты, недостижимые цели или тупик после пуска.
`check_map()` проходит по графу и говорит, что не сходится; её же зовут
тесты и `scripts/validate_blackout_map.py`.

**Числа — настройка, а не литералы по коду.** Здоровье, урон, размер
групп, масштаб на 3/4/5 человек, время этапов и награда собраны тут,
чтобы плейтест правился в одном месте.

Состояние рейда и команды — в `bot.blackout_service`, экран — в
`bot.webapp.blackout`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from bot.game import art

# ---------- где лежат материалы ----------
#
# Данные карты — в репозитории: по ним ходит сервер, и тянуть их по сети
# на каждый шаг неоткуда. Картинки — в том же бакете, что и весь арт
# клуба: папка `zombiemission` выгружена туда целиком.

PACKAGE = Path(__file__).resolve().parent.parent.parent / "zombiemission"
MANIFEST = PACKAGE / "mission_manifest.json"

ZOMBIE_ART = f"{art.BUCKET}/zombiemission"


def art_url(path: str) -> str:
    """Адрес картинки миссии. Пустой путь — пустая строка, не битый URL."""
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{ZOMBIE_ART}/{path.lstrip('/')}"


# ---------- кто такие зомби ----------

FIGHTER_ART = "avatars/zombie_fighter.png"
BOSS_ART = {
    "hunter": "avatars/zombie_hunter.png",
    "brute": "avatars/zombie_brute.png",
    "leader": "avatars/zombie_leader.png",
}

# Панорама обзора: карта комплекса на экране «схема станции»
OVERVIEW = "maps/station_overview.jpg"

# ---------- время ----------

# Одно ребро графа — ровно десять секунд серверного времени, и лестница
# между соседними этажами тоже ребро. Это правило продакшена; ускорять
# его можно только тестам, подменив часы
EDGE_SECONDS = 10

# Сколько живёт рейд с минуты входа и когда предупреждать
RUN_LIMIT_MINUTES = 180
WARN_MINUTES = (15, 5)
# Мягкий ориентир на бонус скорости: пятнадцать минут от пуска до выхода
SPEED_BONUS_MINUTES = 15

# Окно входа: вторник и пятница, девятнадцать ноль-ноль по Москве
CYCLE_DAYS = (1, 4)  # понедельник 0 — как в `date.weekday()`
CYCLE_HOUR = 19
CYCLE_MINUTE = 0
ENTRY_WINDOW_MINUTES = 180

# ---------- разовые окна ----------
#
# Расписание города — вторник и пятница, но иногда окно нужно открыть
# разово: показать режим, собрать плейтест, проверить правку на живом
# сервере. Такие окна лежат отдельным списком и не трогают расписание:
# кончилось — и город снова живёт по вторникам и пятницам.
#
# Пишутся парами «начало/конец» по московским часам, без часового
# пояса в строке — он тут всегда московский. Список можно задать
# переменной окружения `BLACKOUT_WINDOWS`, через запятую:
#
#     BLACKOUT_WINDOWS=2026-10-30T23:30/2026-10-31T00:30
#
# Пустая переменная оставляет то, что записано здесь; строка `-`
# убирает разовые окна совсем.

EXTRA_WINDOWS: tuple[tuple[str, str], ...] = (
    # Разовое окно под первый плейтест «Чёрного пуска»
    ("2026-10-30T23:30", "2026-10-31T00:30"),
)


def extra_windows() -> tuple[tuple[int, int], ...]:
    """Разовые окна входа парами «начало, конец» в секундах эпохи."""
    import os
    from datetime import datetime

    from bot.game.clock import MOSCOW

    written = os.getenv("BLACKOUT_WINDOWS", "").strip()
    if written == "-":
        return ()
    if written:
        pairs = [
            tuple(one.split("/", 1))
            for one in written.split(",")
            if "/" in one
        ]
    else:
        pairs = list(EXTRA_WINDOWS)
    out: list[tuple[int, int]] = []
    for start, end in pairs:
        try:
            opens = datetime.fromisoformat(start.strip()).replace(
                tzinfo=MOSCOW
            )
            closes = datetime.fromisoformat(end.strip()).replace(tzinfo=MOSCOW)
        except ValueError:  # pragma: no cover - кривую строку просто мимо
            continue
        if closes > opens:
            out.append((int(opens.timestamp()), int(closes.timestamp())))
    return tuple(sorted(out))

# ---------- отряд ----------

# Сценарий зовёт отряд из 3–5 человек, и это по-прежнему тот состав,
# под который посчитан баланс. Но нижняя граница опущена до одного:
# город маленький, и ждать троих ради того, чтобы посмотреть станцию,
# некому. Одиночке станция по силам не потому, что её ослабили
# руками, а потому, что масштаб и так считается от состава: враги
# втрое слабее по запасу, бьют тише и выходят меньшими группами
MIN_PARTY = 1
BEST_PARTY = 4
MAX_PARTY = 5

# Масштаб под состав: фиксируется при входе и не пересчитывается, когда
# кто-то отключился. Иначе отряд выигрывал бы боссов выходом из игры
HP_PER_HEAD = 0.22
DAMAGE_PER_HEAD = 0.08


def group_size(base: int, party: int) -> int:
    """Сколько зомби выходит на отряд такого размера.

    Масштаб по здоровью отряд не спасает: шестеро на троих давят числом,
    а не запасом. Поэтому вместе с крепостью двигается и счёт — на
    одного бойца меньше четырёх убавляется один зомби, на одного
    больше прибавляется. Меньше двух группа не бывает: это уже не
    заслон, а одиночка в коридоре.
    """
    return max(2, base + (party - BEST_PARTY))


def hp_scale(party: int) -> float:
    """Во сколько раз крепче враги при таком составе."""
    return round(1 + HP_PER_HEAD * (party - BEST_PARTY), 4)


def damage_scale(party: int) -> float:
    """Во сколько раз больнее бьют враги при таком составе."""
    return round(1 + DAMAGE_PER_HEAD * (party - BEST_PARTY), 4)


# ---------- флаги состояния ----------
#
# Всё, что рейд может «иметь»: допуск, вентили, модули, охлаждение,
# пуск, дренаж. Имена те же, что в манифесте: переименовывать их значит
# расходиться с материалами на ровном месте.

CLEARANCE = "dispatch_clearance"
VALVE_A = "valve_a"
VALVE_B = "valve_b"
COOLING = "cooling_ready"
SYNCHRONIZER = "synchronizer"
POWER_CORE = "power_core"
RESTART_KEY = "restart_key"
EVACUATION_PLAN = "evacuation_plan"
DRAINAGE = "drainage_open"
RESTARTED = "station_restarted"
EXTRACTED = "extracted"

HUNTER_DOWN = "hunter_defeated"
BRUTE_DOWN = "brute_defeated"
LEADER_DOWN = "leader_defeated"

# Подготовка к боссам: прожекторы Охотника и два реле Предводителя. Это
# не ворота — без них босс просто тяжелее, и подсказка говорит, чего не
# хватает. Обязательными их делать нельзя: отряд без них должен пройти
FLOODLIGHTS = "floodlights"
RELAY_A = "relay_a"
RELAY_B = "relay_b"

# Три квестовых модуля. Принадлежат инстансу, а не игроку: их не
# продать, не выбросить и не потерять при смерти
QUEST_ITEMS = (SYNCHRONIZER, POWER_CORE, RESTART_KEY)

ITEM_TITLES = {
    SYNCHRONIZER: "Фазовый синхронизатор",
    POWER_CORE: "Силовой сердечник",
    RESTART_KEY: "Ключ аварийного запуска",
}
# Форма гнезда, а не только цвет: задача не должна зависеть от того,
# различает ли человек бирюзовый и зелёный
ITEM_SHAPES = {
    SYNCHRONIZER: ("треугольник", "▲"),
    POWER_CORE: ("цилиндр", "⬤"),
    RESTART_KEY: ("шестигранник", "⬢"),
}
ITEM_ART = {
    SYNCHRONIZER: "items/synchronizer.jpg",
    POWER_CORE: "items/power_core.jpg",
    RESTART_KEY: "items/restart_key.jpg",
}

FLAG_TITLES = {
    CLEARANCE: "Допуск диспетчерской",
    VALVE_A: "Вентиль А",
    VALVE_B: "Вентиль Б",
    COOLING: "Охлаждение запущено",
    EVACUATION_PLAN: "Схема эвакуации",
    DRAINAGE: "Дренаж открыт",
    RESTARTED: "Станция перезапущена",
    EXTRACTED: "Эвакуация",
    HUNTER_DOWN: "Охотник повержен",
    BRUTE_DOWN: "Громила повержен",
    LEADER_DOWN: "Предводитель повержен",
    FLOODLIGHTS: "Прожекторы наведены",
    RELAY_A: "Реле А отключено",
    RELAY_B: "Реле Б отключено",
    "heal_yard": "Медпункт двора",
    "heal_f3": "Медпункт третьего этажа",
    **ITEM_TITLES,
}

# ---------- этажи ----------

LAYER_TITLES = {
    "O": "Территория",
    "F1": "Первый этаж · турбины",
    "F2": "Второй этаж · галереи",
    "B": "Подвал охлаждения",
    "F3": "Третий этаж · энергоблок",
    "F4": "Четвёртый этаж · рубка",
    "E": "Эвакуационный лабиринт",
}
LAYER_MAPS = {
    "O": "maps/route_O.svg",
    "F1": "maps/route_F1.svg",
    "F2": "maps/route_F2.svg",
    "B": "maps/route_B.svg",
    "F3": "maps/route_F3.svg",
    "F4": "maps/route_F4.svg",
    "E": "maps/route_E.svg",
}


# ---------- граф станции ----------


@dataclass(frozen=True)
class Room:
    """Комната: узел графа, панорама и то, что в ней есть."""

    code: str
    name: str
    layer: str
    x: int
    y: int
    panorama: str
    safe: bool = False
    objective: str = ""
    encounter: str = ""

    @property
    def layer_title(self) -> str:
        return LAYER_TITLES.get(self.layer, self.layer)

    @property
    def image(self) -> str:
        return art_url(self.panorama)

    @property
    def boss(self) -> str:
        """Код босса этой комнаты. Пусто — босса тут нет."""
        return self.encounter[5:] if self.encounter.startswith("boss_") else ""


@dataclass(frozen=True)
class Door:
    """Переход между комнатами: куда ведёт и чем закрыт.

    `requires` — флаги, без которых дверь не откроется. `closed_by` —
    флаги, которые её закрывают: после пуска карантин запирает главный
    вход, и это не поломка, а часть сценария.
    """

    a: str
    b: str
    seconds: int = EDGE_SECONDS
    requires: tuple[str, ...] = ()
    closed_by: tuple[str, ...] = ()

    def other(self, room: str) -> str:
        return self.b if room == self.a else self.a


@dataclass(frozen=True)
class Station:
    """Станция целиком: комнаты, двери и всё, что из них следует."""

    rooms: dict[str, Room]
    doors: tuple[Door, ...]
    exits: dict[str, tuple[Door, ...]] = field(default_factory=dict)
    start: str = "O01"
    restart_room: str = "F405"
    exit_room: str = "E12"

    def room(self, code: str) -> Room | None:
        return self.rooms.get(code)

    def doors_from(self, code: str) -> tuple[Door, ...]:
        return self.exits.get(code, ())

    def door_between(self, a: str, b: str) -> Door | None:
        for door in self.doors_from(a):
            if door.other(a) == b:
                return door
        return None

    def layers(self) -> tuple[str, ...]:
        out: list[str] = []
        for room in self.rooms.values():
            if room.layer not in out:
                out.append(room.layer)
        return tuple(out)

    def rooms_on(self, layer: str) -> tuple[Room, ...]:
        return tuple(
            one for one in self.rooms.values() if one.layer == layer
        )


def door_open(door: Door, flags: Iterable[str]) -> str:
    """Чего двери не хватает. Пусто — проходи.

    Отказ возвращается словами, а не False: закрытая дверь обязана
    сказать, чего недостаёт, иначе отряд ходит по станции наугад.
    """
    have = set(flags)
    for flag in door.closed_by:
        if flag in have:
            return "Карантин запер этот проход после пуска."
    missing = [one for one in door.requires if one not in have]
    if not missing:
        return ""
    names = ", ".join(FLAG_TITLES.get(one, one) for one in missing)
    return f"Нужно: {names}."


@lru_cache(maxsize=1)
def station() -> Station:
    """Станция из манифеста. Читается один раз на процесс."""
    return load_station(MANIFEST)


class MapMissing(RuntimeError):
    """Карты миссии нет на диске: без неё станции не существует."""


def load_station(path: Path) -> Station:
    """Собрать станцию из файла манифеста."""
    where = Path(path)
    if not where.is_file():
        # Это бывает ровно в одном случае: образ собрали без папки
        # `zombiemission`. Падать безымянной пятисоткой тут нельзя —
        # человек увидит «что-то пошло не так» и не узнает ничего
        raise MapMissing(
            f"Карта станции не найдена: {where}. Папка `zombiemission` "
            "не доехала до сервера — проверь Dockerfile."
        )
    body = json.loads(where.read_text(encoding="utf-8"))
    rooms = {
        str(one["id"]): Room(
            code=str(one["id"]),
            name=str(one["name"]),
            layer=str(one["layer"]),
            x=int(one.get("x") or 0),
            y=int(one.get("y") or 0),
            panorama=str(one.get("panorama") or ""),
            safe=bool(one.get("safe")),
            objective=str(one.get("objective") or ""),
            encounter=str(one.get("encounter") or ""),
        )
        for one in body["nodes"]
    }
    doors: list[Door] = []
    for one in body["edges"]:
        doors.append(
            Door(
                a=str(one["a"]),
                b=str(one["b"]),
                seconds=int(one.get("seconds") or EDGE_SECONDS),
                requires=tuple(str(x) for x in one.get("requires") or ()),
                closed_by=tuple(str(x) for x in one.get("disabled_when") or ()),
            )
        )
    exits: dict[str, list[Door]] = {code: [] for code in rooms}
    for door in doors:
        exits.setdefault(door.a, []).append(door)
        exits.setdefault(door.b, []).append(door)
    return Station(
        rooms=rooms,
        doors=tuple(doors),
        exits={code: tuple(rows) for code, rows in exits.items()},
        start=str(body.get("start_node") or "O01"),
        restart_room=str(body.get("restart_node") or "F405"),
        exit_room=str(body.get("exit_node") or "E12"),
    )


# ---------- встречи ----------
#
# Восемнадцать обычных групп, три босса и три волны обороны рубки.
# Состав каждой группы — настройка, а не бросок кубика: «сколько-то
# зомби» в рейде на два часа превращается в лотерею, а не в сложность.
# Очищенная комната не заселяется заново никогда.

# Сколько бойцов в обычной группе при отряде в четыре человека
SQUAD_SIZE = {
    # Территория: первые две встречи учат, а не наказывают
    "O02": 3,
    "O08": 4,
    # Первый этаж по пути наверх
    "F101": 4,
    "F103": 4,
    # Ворота Громилы: особый заслон
    "F106": 5,
    "F109": 4,
    # Второй этаж: четыре встречи до Охотника
    "F202": 4,
    "F205": 4,
    "F206": 4,
    "F207": 5,
    # Подвал
    "B02": 4,
    "B06": 4,
    "B07": 5,
    # Третий этаж
    "F302": 5,
    "F304": 5,
    "F306": 6,
    # Лабиринт
    "E06": 4,
    "E11": 6,
}

# Уровень обычного зомби. Станция одна на город, и подстраивать её под
# уровень отряда целиком нельзя: иначе прокачка перестаёт значить что-то.
# Накачан он при этом хуже своего уровня: заражённый рабочий силён
# заразой, а не залом, и восемнадцать таких встреч по три минуты — это
# бюджет сценария, а не стена
FOE_LEVEL = 10
FOE_BUILD = 3
BOSS_LEVEL = {"hunter": 11, "brute": 13, "leader": 12}
# Запас здоровья босса за каждого человека сверх первого
BOSS_HP_SHARE = {"hunter": 0.3, "brute": 0.22, "leader": 0.26}

# Волны обороны рубки: входной коридор, боковая дверь, смешанный натиск
DEFENSE_WAVES = (3, 4, 5)
DEFENSE_WAVE_TITLES = (
    "Входной коридор",
    "Боковая дверь",
    "Смешанный натиск",
)
# Целостность консоли. Враги бьют её только после показанного
# предупреждения; ноль — пуск сорван, но модули остаются в гнёздах
CONSOLE_HEALTH = 100
CONSOLE_HIT = 20

# Сколько подкреплений может позвать босс — и ни одним больше
BOSS_REINFORCEMENTS = {"hunter": 2, "brute": 0, "leader": 2}
REINFORCEMENT_SIZE = 2


@dataclass(frozen=True)
class BossRules:
    """Как дерётся босс: телеграф, фазы и чем его ломают.

    Механики выражены статусами и доступными действиями, а не ручным
    экшеном: бой остаётся тем же, что в клубе, — удар, блок, приём.
    """

    code: str
    title: str
    class_code: str
    weapon: str
    offhand: str = ""
    # Что он готовит, предупреждая за ход
    telegraph: str = ""
    telegraph_title: str = ""
    # Действие на арене, которое отвечает на телеграф или снимает защиту
    counter_action: str = ""
    counter_title: str = ""
    counter_hint: str = ""
    # Пороги фаз по доле здоровья: выше первого — первая фаза и так далее
    phases: tuple[float, ...] = ()
    phase_titles: tuple[str, ...] = ()
    minutes: int = 10

    @property
    def image(self) -> str:
        return art_url(BOSS_ART.get(self.code, FIGHTER_ART))

    def phase_of(self, share: float) -> int:
        """Какая это фаза при такой доле здоровья. Первая — нулевая."""
        for number, edge in enumerate(self.phases):
            if share > edge:
                return number
        return len(self.phases)


BOSSES: dict[str, BossRules] = {
    "hunter": BossRules(
        code="hunter",
        title="Охотник",
        class_code="assassin",
        weapon="assassin_stiletto",
        telegraph="mark",
        telegraph_title="Метка добычи",
        counter_action="floodlight",
        counter_title="Прожектор",
        counter_hint="Свет снимает уклонение на два хода.",
        phases=(0.6, 0.25),
        phase_titles=("Разведка", "Две волны помощников", "Короткие серии"),
        minutes=10,
    ),
    "brute": BossRules(
        code="brute",
        title="Громила",
        class_code="tank",
        weapon="boss_sledge",
        offhand="boss_shield",
        telegraph="hammer",
        telegraph_title="Размах молота",
        counter_action="valve",
        counter_title="Охлаждающий клапан",
        counter_hint="Клапан снимает броню на два хода.",
        phases=(0.5,),
        phase_titles=("Щит держит", "Щит сломан"),
        minutes=12,
    ),
    "leader": BossRules(
        code="leader",
        title="Предводитель",
        class_code="warrior",
        weapon="fire_axe",
        telegraph="order",
        telegraph_title="Приказ стае",
        counter_action="relay",
        counter_title="Реле арены",
        counter_hint="Отключённое реле снимает щит босса.",
        phases=(0.65, 0.3),
        phase_titles=("Командование", "Реле и помощники", "Последний натиск"),
        minutes=15,
    ),
}

# Действия на арене доступны любому классу: отряд без конкретной роли
# обязан остаться проходимым
ARENA_ACTIONS = {
    "floodlight": "Навести прожектор",
    "valve": "Открыть клапан",
    "relay": "Отключить реле",
    "console": "Прикрыть консоль",
}

# Сколько здоровья возвращает зачищенная комната. Восемнадцать встреч
# подряд без единой перевязки — не сложность, а арифметика: отряд
# доходил бы до третьего этажа на последних очках и ложился там всегда.
# Медпункты лечат целиком, эта доля — просто «отдышались»
CLEAR_HEAL = 0.25

# Во сколько раз сильнее давит комната, где врагов больше, чем людей.
# Выше этого не поднимается: шесть зомби на одного — уже поражение, и
# превращать его в мгновенное незачем
PRESS_CAP = 2.0

# Сколько ходов держится окно уязвимости, открытое действием на арене
WINDOW_TURNS = 2
# Сколько ходов босс устойчив после контроля: навсегда выключить нельзя
CONTROL_IMMUNE_TURNS = 2

# ---------- награда ----------
#
# Доли из сценария: шестьдесят процентов за пуск, сорок за личную
# эвакуацию, десять сверху — за тоннели быстрее пятнадцати минут.
# Сумма берётся из экономики клуба и лежит здесь одним числом

BASE_REWARD = 2_500
RESTART_SHARE = 0.60
EXTRACT_SHARE = 0.40
SPEED_SHARE = 0.10

STAGE_RESTART = "restart"
STAGE_EXTRACT = "extract"
STAGE_SPEED = "speed"

STAGE_TITLES = {
    STAGE_RESTART: "Перезапуск станции",
    STAGE_EXTRACT: "Эвакуация",
    STAGE_SPEED: "Бонус скорости",
}


def reward_for(stage: str, base: int = BASE_REWARD) -> int:
    """Сколько кредитов стоит эта часть награды."""
    share = {
        STAGE_RESTART: RESTART_SHARE,
        STAGE_EXTRACT: EXTRACT_SHARE,
        STAGE_SPEED: SPEED_SHARE,
    }.get(stage, 0.0)
    return int(base * share)


# ---------- проверка карты ----------
#
# Манифест — проектный документ, а не движок. Прежде чем по нему ходить,
# его надо прочитать недоверчиво: есть ли висящие комнаты, достижимы ли
# цели в том порядке, в каком их требует сценарий, и не запирает ли
# пуск отряд на четвёртом этаже. Эти же проверки зовут тесты.

# Что рейд обязан собрать и в каком порядке это становится доступно
REQUIRED_ORDER = (
    CLEARANCE,
    HUNTER_DOWN,
    SYNCHRONIZER,
    BRUTE_DOWN,
    POWER_CORE,
    VALVE_A,
    VALVE_B,
    COOLING,
    LEADER_DOWN,
    RESTART_KEY,
    RESTARTED,
    DRAINAGE,
    EXTRACTED,
)


def reachable(
    place: Station, start: str, flags: Iterable[str]
) -> set[str]:
    """Куда можно дойти с этим набором флагов. Только пешком, по дверям."""
    have = set(flags)
    seen = {start}
    queue = [start]
    while queue:
        here = queue.pop()
        for door in place.doors_from(here):
            there = door.other(here)
            if there in seen or door_open(door, have):
                continue
            seen.add(there)
            queue.append(there)
    return seen


def walkthrough(place: Station | None = None) -> list[tuple[str, str]]:
    """Пройти станцию правилами и отдать порядок, в каком берутся цели.

    Это и есть главная проверка манифеста: не «есть ли путь», а
    «собирается ли рейд» — модуль за модулем, в том порядке, в каком
    двери их требуют. Отдаёт пары «флаг, комната».

    Идём **ногами**, а не глазами: после каждой цели отряд стоит в той
    комнате, где её взял, и дальше считает соседей оттуда. Считать
    достижимость от проходной на каждом шаге нельзя — после пуска
    карантин запирает главный вход, и от проходной станция недостижима
    вовсе. Снаружи после пуска уже не входят, и это сценарий, а не
    поломка карты.
    """
    place = place or station()
    flags: set[str] = set()
    taken: list[tuple[str, str]] = []
    where = place.start
    # Цели, которые даёт комната: достаточно до неё дойти и нажать
    gives = {
        room.objective: code
        for code, room in place.rooms.items()
        if room.objective
    }
    # Чего требует каждая цель, кроме того, чтобы до неё дойти
    needs = {
        SYNCHRONIZER: (HUNTER_DOWN,),
        POWER_CORE: (BRUTE_DOWN,),
        RESTART_KEY: (LEADER_DOWN,),
        COOLING: (VALVE_A, VALVE_B, POWER_CORE),
        RESTARTED: (SYNCHRONIZER, POWER_CORE, RESTART_KEY, COOLING),
        EXTRACTED: (RESTARTED, DRAINAGE),
    }
    for _ in range(len(gives) + 1):
        here = reachable(place, where, flags)
        grew = False
        for flag, code in gives.items():
            if flag in flags or code not in here:
                continue
            if any(one not in flags for one in needs.get(flag, ())):
                continue
            flags.add(flag)
            taken.append((flag, code))
            where = code
            grew = True
        if not grew:
            break
    return taken


def check_map(place: Station | None = None) -> list[str]:
    """Что в карте не сходится. Пустой список — всё в порядке."""
    place = place or station()
    bad: list[str] = []

    # 1. Двери ведут в существующие комнаты, и сама с собой комната не
    # соединяется
    for door in place.doors:
        for code in (door.a, door.b):
            if code not in place.rooms:
                bad.append(f"дверь {door.a}↔{door.b}: нет комнаты {code}")
        if door.a == door.b:
            bad.append(f"дверь {door.a} ведёт сама в себя")
        if door.seconds != EDGE_SECONDS:
            bad.append(
                f"дверь {door.a}↔{door.b}: {door.seconds} с вместо "
                f"{EDGE_SECONDS}"
            )
        for flag in door.requires + door.closed_by:
            if flag not in FLAG_TITLES:
                bad.append(f"дверь {door.a}↔{door.b}: неизвестный флаг {flag}")

    # 2. Висящих комнат нет: в каждую есть хоть одна дверь
    for code in place.rooms:
        if not place.doors_from(code):
            bad.append(f"комната {code} не соединена ни с чем")

    # 3. Цели комнат — из известного списка
    for code, room in place.rooms.items():
        if room.objective and room.objective not in FLAG_TITLES:
            bad.append(f"комната {code}: неизвестная цель {room.objective}")
        if room.encounter.startswith("boss_") and room.boss not in BOSSES:
            bad.append(f"комната {code}: неизвестный босс {room.encounter}")
        if room.encounter == "zombie_squad" and code not in SQUAD_SIZE:
            bad.append(f"комната {code}: группа есть, а её состав не задан")
    for code in SQUAD_SIZE:
        room = place.room(code)
        if room is None or room.encounter != "zombie_squad":
            bad.append(f"состав для {code} задан, а встречи в манифесте нет")

    # 4. Рейд собирается: все обязательные цели берутся по порядку
    taken = dict(walkthrough(place))
    for flag in REQUIRED_ORDER:
        if flag not in taken:
            bad.append(f"цель {flag} недостижима правилами карты")

    # 5. Ни один босс не обходится: без него цель не берётся
    for flag, boss_flag in (
        (SYNCHRONIZER, HUNTER_DOWN),
        (POWER_CORE, BRUTE_DOWN),
        (RESTART_KEY, LEADER_DOWN),
    ):
        if taken.get(flag) and not taken.get(boss_flag):
            bad.append(f"{flag} берётся без победы {boss_flag}")

    # 6. После пуска выход открыт, а главный вход закрыт
    after = set(REQUIRED_ORDER) - {EXTRACTED}
    here = reachable(place, place.restart_room, after)
    if place.exit_room not in here:
        bad.append("после пуска из рубки не дойти до выхода")
    if "F101" in here and "O10" in here:
        bad.append("после пуска главный вход остался открыт")

    # 7. Лабиринт не запирает: из каждой комнаты эвакуации видно выход
    maze = [code for code, room in place.rooms.items() if room.layer == "E"]
    for code in maze:
        if place.exit_room not in reachable(place, code, after):
            bad.append(f"из {code} не выбраться к выходу")
    return bad


__all__ = [
    "BOSSES",
    "BOSS_HP_SHARE",
    "BOSS_LEVEL",
    "BOSS_REINFORCEMENTS",
    "BossRules",
    "CLEAR_HEAL",
    "CONSOLE_HEALTH",
    "CONSOLE_HIT",
    "DEFENSE_WAVES",
    "Door",
    "EDGE_SECONDS",
    "FLAG_TITLES",
    "FOE_BUILD",
    "FOE_LEVEL",
    "ITEM_ART",
    "ITEM_SHAPES",
    "ITEM_TITLES",
    "LAYER_MAPS",
    "LAYER_TITLES",
    "MapMissing",
    "MAX_PARTY",
    "MIN_PARTY",
    "PRESS_CAP",
    "QUEST_ITEMS",
    "REQUIRED_ORDER",
    "Room",
    "RUN_LIMIT_MINUTES",
    "SPEED_BONUS_MINUTES",
    "SQUAD_SIZE",
    "Station",
    "art_url",
    "check_map",
    "damage_scale",
    "door_open",
    "extra_windows",
    "group_size",
    "hp_scale",
    "load_station",
    "reachable",
    "reward_for",
    "station",
    "walkthrough",
]
