"""Карта города: где боец стоит, что ему там доступно и сколько идти.

Раньше всё жило на вкладках: лавка, аптека, ринг и подвал открывались
одинаково из любого места. Теперь у каждого дела есть адрес — драться
идут в клуб, чинить вещи в мастерскую, за склянками в аптеку, — и боец
по городу ходит.

Правило проверяется на сервере, а не прятанием кнопки: спрятанная кнопка
обходится запросом мимо интерфейса, и тогда карта — украшение. Поэтому
каждая ручка спрашивает у локации, можно ли здесь то, что просят.

Дорога занимает время: соседнее здание в своём районе ближе, чем другой
конец города. Пока боец в пути, он не в старом месте и ещё не в новом —
и не может ни то, ни другое.

Ниже две половины. Сначала правила — что такое услуга, здание и район, и
сколько до чего идти. Потом сама карта: шесть районов, четырнадцать
зданий и зоны нажатия на нарисованных картинках. Вторая половина —
содержимое: числа зон меняются от того, как перерисовали дом, и правки
там движка не касаются. Их источник — docs/locations.md.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from bot.game import art


class Service(str, Enum):
    """Чем занимаются в локации. У каждой ручки сервера — своя услуга."""

    FIGHT = "fight"  # ринг: вызовы, бои, отряд, турниры
    RAID = "raid"  # рейд-босс
    REPAIR = "repair"  # починка вещей
    WEAPONS = "weapons"  # оружие и щиты: купить и сдать
    CLOTHES = "clothes"  # одежда и всё прочее носимое
    POTIONS = "potions"  # аптека: эликсиры
    HEAL = "heal"  # больница: здоровье за кредиты
    PREMIUM = "premium"  # элитный магазин, за звёзды
    FAN = "fan"  # фанатский магазин: экипировка своей команды
    MARKET = "market"  # комиссионка: торговля между бойцами

    @property
    def title(self) -> str:
        return SERVICE_TITLES[self]


SERVICE_TITLES: dict[Service, str] = {
    Service.FIGHT: "драться",
    Service.RAID: "идти в рейд",
    Service.REPAIR: "чинить вещи",
    Service.WEAPONS: "торговать оружием",
    Service.CLOTHES: "торговать одеждой",
    Service.POTIONS: "покупать эликсиры",
    Service.HEAL: "лечиться",
    Service.PREMIUM: "покупать за звёзды",
    Service.FAN: "покупать фанатскую экипировку",
    Service.MARKET: "торговать с бойцами",
}


@dataclass(frozen=True)
class Rect:
    """Прямоугольник на карте, долями от самой картинки.

    Доли, а не пиксели: карта показывается через `object-fit: contain` и
    на каждом экране своего размера. Считать зоны нужно от нарисованной
    картинки, а не от окна — иначе на телефоне с другим соотношением
    сторон поля по краям сдвинут все дома.

    Сами дома прямоугольниками не описываются: цель нажатия — дверь
    (`Location.entrance`). Прямоугольник нужен для нижнего прохода и для
    области касания вокруг двери, которая пальцу иначе мала.
    """

    x: float
    y: float
    w: float
    h: float

    def holds(self, x: float, y: float) -> bool:
        """Попало ли касание в эту рамку. Обе доли — от картинки."""
        return self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


# Точка силуэта: доли от ширины и высоты картинки
Point = tuple[float, float]


def inside(polygon: tuple[Point, ...], x: float, y: float) -> bool:
    """Попала ли точка внутрь многоугольника.

    Обычный луч вправо: считаем, сколько раз он пересёк стороны. Нечётное
    число — точка внутри. Дома нарисованы простыми выпуклыми силуэтами, но
    луч работает и на вогнутых, и лишних предположений не делает.
    """
    hit = False
    count = len(polygon)
    for index in range(count):
        (x1, y1), (x2, y2) = polygon[index], polygon[(index + 1) % count]
        # Сторона пересекает горизонталь точки, и пересечение правее её
        if (y1 > y) != (y2 > y):
            edge = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < edge:
                hit = not hit
    return hit


def bounds_of(polygon: tuple[Point, ...]) -> Rect:
    """Рамка вокруг силуэта — на случай, если многоугольник негде рисовать."""
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


@dataclass(frozen=True)
class Location:
    """Здание на карте: как называется, в каком районе и что тут делают."""

    code: str
    title: str
    district: str  # код района: одна карта — один район
    # Вход: дверь, ворота или открытый проход — ровно четыре точки по
    # часовой стрелке, последняя замыкается с первой. Целятся именно в
    # него: дом занимает полкарты, а войти в него можно в одном месте
    entrance: tuple[Point, Point, Point, Point]
    services: tuple[Service, ...] = ()
    # Что здесь будет, когда дойдут руки. Пустая строка — дом работает
    soon: str = ""
    # Родительный падеж для фраз «дойти до мастерской»
    genitive: str = ""
    # Имя файла с видом изнутри, если оно не совпадает с кодом дома.
    # Обычно не задаётся: картинка зовётся по дому — `pharmacy` →
    # `pharmacy_interior`. Три дома рисовали под другими именами, и
    # переименовывать файлы в бакете — не наше дело
    interior: str = ""
    # В какой папке бакета лежит его вид изнутри. Пусто — в той, куда
    # легли первые четырнадцать домов. Районы второй очереди выгрузили
    # в другую, и это единственное, чем они отличаются
    interior_folder: str = ""

    def allows(self, service: Service) -> bool:
        return service in self.services

    def holds(self, x: float, y: float) -> bool:
        """Попало ли касание точно во вход. Доли — от самой картинки."""
        return inside(self.entrance, x, y)

    @property
    def bounds(self) -> Rect:
        """Рамка вокруг двери: по ней ставится подпись."""
        return bounds_of(self.entrance)

    @property
    def touch(self) -> Rect:
        """Область касания: дверь с запасом под палец.

        Дверь на карте — сотня пикселей в исходнике и сантиметр на
        телефоне. Попасть в неё пальцем можно, но каждый раз прицеливаясь,
        поэтому невидимо расширяем — по ширине больше, чем по высоте:
        соседние дома стоят рядом по горизонтали, и разъехаться вверх
        безопаснее, чем вбок.
        """
        box = self.bounds
        return Rect(
            max(0.0, box.x - TOUCH_PAD_X),
            max(0.0, box.y - TOUCH_PAD_Y),
            min(1.0, box.w + TOUCH_PAD_X * 2),
            min(1.0, box.h + TOUCH_PAD_Y * 2),
        )

    @property
    def works(self) -> bool:
        """Дом уже что-то умеет. Иначе в него можно только зайти."""
        return bool(self.services)

    @property
    def whither(self) -> str:
        return self.genitive or self.title

    @property
    def image(self) -> str:
        """Картинка района, на которой стоит это здание.

        Спрашиваем у самого района, а не считаем адрес заново: формат у
        карт разный, и второе место, где он выводится, однажды разошлось
        бы с первым.
        """
        district = DISTRICT_BY_CODE.get(self.district)
        return district.image if district else ""

    @property
    def indoors(self) -> str:
        """Вид изнутри: его вешают сверху экрана, когда боец вошёл.

        Адрес считается от кода дома — как у вещей и склянок. Своё имя
        файла задаётся только там, где художник назвал его иначе.

        Зовётся не `inside`: так называется проверка попадания в силуэт,
        и два разных `inside` в одном файле читались бы как одно.
        """
        return art.interior(
            self.interior or f"{self.code}_interior",
            self.interior_folder or art.INTERIORS,
        )


@dataclass(frozen=True)
class District:
    """Район: одна нарисованная карта, дома на ней и соседи по сторонам."""

    code: str
    title: str
    # Куда ведут стрелки: сторона света → код соседнего района. Города
    # на карте не видно целиком, и это единственное, что связывает шесть
    # картинок в один город
    around: dict[str, str] = field(default_factory=dict)
    # Чем нарисована карта. Первую очередь отдали в jpeg, вторую — в
    # png, и это единственное, чем они отличаются
    picture_ext: str = "jpeg"

    @property
    def image(self) -> str:
        return art.location(self.code, self.picture_ext)

    @property
    def places(self) -> tuple[Location, ...]:
        return tuple(place for place in LOCATIONS if place.district == self.code)


# ---------- дорога ----------

# По городу ходят пешком, и дорога считается переходами. Переход один и
# тот же, откуда бы он ни был: шаг в соседний район или вход в дверь —
# десять секунд.
#
# Отсюда всё остальное само: соседний дом в своём районе — это одна
# дверь, десять секунд. Дом в соседнем районе — шаг и дверь, двадцать.
# Каждый лишний район по дороге добавляет свои десять.
#
# Из казино в бар: Старый город → Центр → Северный Вал (или Торговый
# квартал — дорога та же) → Стадион. Три шага да дверь бара — сорок
# секунд.
STEP = 10

# Дорога между районами, которые ничем не связаны. Такого на карте нет —
# связность стережёт тест, — но считать бесконечность в секундах нечем,
# а город и по диагонали проходится за шесть шагов
FAR_AWAY = 6


def _walk_from(start: str) -> dict[str, int]:
    """Обход в ширину: сколько шагов отсюда до каждого района."""
    steps = {start: 0}
    queue = deque([start])
    while queue:
        code = queue.popleft()
        district = DISTRICT_BY_CODE.get(code)
        if district is None:  # pragma: no cover - район без карты
            continue
        for neighbour in district.around.values():
            if neighbour not in steps:
                steps[neighbour] = steps[code] + 1
                queue.append(neighbour)
    return steps


# Расстояния между районами. Считаются один раз и лениво: справочник
# районов лежит ниже по файлу, а шестнадцать обходов в ширину — работа
# на глазок, но повторять её на каждый шаг игрока незачем
_DISTANCES: dict[str, dict[str, int]] | None = None


def district_hops(source: str, target: str) -> int:
    """Сколько шагов между районами. Ноль — это один и тот же район."""
    global _DISTANCES
    if _DISTANCES is None:
        _DISTANCES = {one.code: _walk_from(one.code) for one in DISTRICTS}
    return _DISTANCES.get(source, {}).get(target, FAR_AWAY)


def travel_seconds(source: str, target: str) -> int:
    """Сколько идти от одного места до другого. Ноль — уже на месте."""
    if source == target:
        return 0
    here, there = get_location(source), get_location(target)
    if here is None or there is None:  # pragma: no cover - дом не с карты
        return STEP
    return STEP * (district_hops(here.district, there.district) + 1)


# ---------- сама карта ----------
#
# Зоны взяты из docs/locations.md: там же пиксели исходных картинок
# 941×1672 и правило пересчёта. Здесь только доли — пиксели привязаны к
# размеру исходника, а он однажды поменяется.

FIGHT_CLUB = "fight_club"

# На сколько невидимо расширить дверь под палец, долями от карты.
# Подсветка при этом остаётся ровно на двери: расширение только для
# касания, иначе на рисунке загорится кусок стены рядом с ней
TOUCH_PAD_X = 0.025
TOUCH_PAD_Y = 0.015

# Куда можно шагнуть с каждой карты. Соседство взаимное: если из центра
# вверх Северный Вал, то из Вала вниз — центр. Иначе однажды из района
# можно будет выйти, но не вернуться, — это стережёт
# tests/test_travel.py::test_every_road_leads_back. С шестнадцатью
# районами в голове такое уже не держится.
UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"

# ---------- вторая очередь города ----------
#
# Десять новых районов пристроены к шести старым по свободным сторонам.
# Соседство взаимное, как и у первых: это стережёт тест.
#
# Коды — имена файлов карт в бакете, как их назвали при выгрузке.
# Стройной привычки в них нет: где-то на конце «_district», где-то нет,
# — и придумывать её задним числом значило бы разойтись с хранилищем.
# Поэтому имена списаны с бакета как есть, одним списком.
#
# Заодно: три кода совпадают с кодами домов, которые на этих картах
# стоят (особняк, автосалон, арена). Это не путаница — районы и дома
# живут в разных справочниках, и адрес карты считается только от кода
# района.
VCPD = "vcpd_hospital_district"
DRIVING = "driving_school_insurance_district"
CARS = "car_dealership"
GYM = "gym_office_district"
SCHOOLS = "police_school_medical_college"
BARRACKS = "military_base_training_ground"
CADETS = "cadet_corps_dormitory"
ARENA = "fight_tournament_stadium"
HOUSES = "residential_district"
MAFIA = "mafia_mansion"

# Вторую очередь нарисовали в png, первую — в jpeg
PNG = "png"

# Город — сетка четыре на четыре, и это не украшение, а правило: по
# сетке считается дорога. Каждый район связан со всеми своими соседями
# по стороне, связи взаимные, а диагоналей нет — ходят по улицам.
#
#            ⬅️ запад                          восток ➡️
#   север ⬆️  Деловой  Северный Вал  Стадион    Армейская часть
#             Старый   Центр         Торговый   Кадетский городок
#             Автошкола Участок      Деловой    Жилой квартал
#   юг    ⬇️  Автосалон Учебный      Арена      Особняк мафии
#
# Сетку стережёт tests/test_travel.py: он раскладывает районы по
# координатам от центра и проверяет, что каждая связь ведёт туда, куда
# показывает, и что обратная ей есть.
DISTRICTS: tuple[District, ...] = (
    # ---------- верхний ряд ----------
    District("bank_market_post", "Деловой квартал", {
        DOWN: "pawnshop_casino",
        RIGHT: "northern_wall_premium",
    }),
    District("northern_wall_premium", "Северный Вал", {
        DOWN: "main_hub",
        LEFT: "bank_market_post",
        RIGHT: "stadium_bar",
    }),
    District("stadium_bar", "Стадион", {
        DOWN: "clothes_pharmacy",
        LEFT: "northern_wall_premium",
        RIGHT: BARRACKS,
    }),
    District(BARRACKS, "Армейская часть", {
        LEFT: "stadium_bar",
        DOWN: CADETS,
    }, PNG),
    # ---------- ряд центра ----------
    District("pawnshop_casino", "Старый город", {
        UP: "bank_market_post",
        RIGHT: "main_hub",
        DOWN: DRIVING,
    }),
    District("main_hub", "Центр", {
        UP: "northern_wall_premium",
        LEFT: "pawnshop_casino",
        RIGHT: "clothes_pharmacy",
        DOWN: VCPD,
    }),
    District("clothes_pharmacy", "Торговый квартал", {
        UP: "stadium_bar",
        LEFT: "main_hub",
        RIGHT: CADETS,
        DOWN: GYM,
    }),
    District(CADETS, "Кадетский городок", {
        UP: BARRACKS,
        LEFT: "clothes_pharmacy",
        DOWN: HOUSES,
    }, PNG),
    # ---------- ряд участка ----------
    District(DRIVING, "Автошкола и страховая", {
        UP: "pawnshop_casino",
        RIGHT: VCPD,
        DOWN: CARS,
    }, PNG),
    District(VCPD, "Участок и больница", {
        UP: "main_hub",
        LEFT: DRIVING,
        RIGHT: GYM,
        DOWN: SCHOOLS,
    }, PNG),
    District(GYM, "Деловой угол", {
        UP: "clothes_pharmacy",
        LEFT: VCPD,
        RIGHT: HOUSES,
        DOWN: ARENA,
    }, PNG),
    District(HOUSES, "Жилой квартал", {
        UP: CADETS,
        LEFT: GYM,
        DOWN: MAFIA,
    }, PNG),
    # ---------- нижний ряд ----------
    District(CARS, "Автосалон", {
        UP: DRIVING,
        RIGHT: SCHOOLS,
    }, PNG),
    District(SCHOOLS, "Учебный квартал", {
        UP: VCPD,
        LEFT: CARS,
        RIGHT: ARENA,
    }, PNG),
    District(ARENA, "Турнирная арена", {
        UP: GYM,
        LEFT: SCHOOLS,
        RIGHT: MAFIA,
    }, PNG),
    District(MAFIA, "Особняк мафии", {
        UP: HOUSES,
        LEFT: ARENA,
    }, PNG),
)

# Какая сторона какой противоположна: по этому и проверяется взаимность
OPPOSITE: dict[str, str] = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}

LOCATIONS: tuple[Location, ...] = (
    # ---------- Центр: клуб, оружие, мастерская ----------
    Location(
        FIGHT_CLUB,
        "Бойцовский клуб VEGAS",
        district="main_hub",
        # двойные двери под вывеской VEGAS
        entrance=(
            (0.436769, 0.26256), (0.632306, 0.272727),
            (0.629118, 0.358852), (0.438895, 0.34988),
        ),
        services=(Service.FIGHT,),
        genitive="бойцовского клуба",
    ),
    Location(
        "weapon_shop",
        "Оружейный магазин",
        district="main_hub",
        # стеклянные двери под вывеской «ОРУЖИЕ»
        entrance=(
            (0.091392, 0.578349), (0.326249, 0.54366),
            (0.328374, 0.613038), (0.104145, 0.650718),
        ),
        services=(Service.WEAPONS,),
        genitive="оружейного магазина",
        interior="weapons_shop_interior",
    ),
    Location(
        "workshop",
        "Мастерская",
        district="main_hub",
        # открытый гараж под вывеской «МАСТЕРСКАЯ»
        entrance=(
            (0.697131, 0.538278), (0.890542, 0.589115),
            (0.876727, 0.689593), (0.688629, 0.62799),
        ),
        services=(Service.REPAIR,),
        genitive="мастерской",
    ),
    # ---------- Торговый квартал: одежда и аптека ----------
    Location(
        "clothes_shop",
        "Магазин одежды",
        district="clothes_pharmacy",
        # центральные двери под вывеской «ОДЕЖДА»
        entrance=(
            (0.436769, 0.212919), (0.636557, 0.228469),
            (0.633369, 0.314593), (0.445271, 0.300837),
        ),
        services=(Service.CLOTHES,),
        genitive="магазина одежды",
        interior="clothing_shop_interior",
    ),
    Location(
        "pharmacy",
        "Аптека",
        district="clothes_pharmacy",
        # центральные стеклянные двери под вывеской «АПТЕКА»
        entrance=(
            (0.387885, 0.615431), (0.620616, 0.648325),
            (0.619554, 0.724282), (0.393199, 0.689593),
        ),
        services=(Service.POTIONS,),
        genitive="аптеки",
    ),
    # ---------- Старый город: казино и комиссионка ----------
    Location(
        "casino",
        "Казино",
        district="pawnshop_casino",
        # двойные двери под вывеской «КАЗИНО»
        entrance=(
            (0.467588, 0.264354), (0.603613, 0.273923),
            (0.597237, 0.329545), (0.467588, 0.324163),
        ),
        services=(Service.RAID,),
        genitive="казино",
        interior="underground_casino_interior",
    ),
    Location(
        "pawnshop",
        "Комиссионный магазин",
        district="pawnshop_casino",
        # решётчатая центральная дверь под вывеской «КОМИССИОНКА»
        entrance=(
            (0.320935, 0.685407), (0.418704, 0.699163),
            (0.420829, 0.763158), (0.324123, 0.754187),
        ),
        services=(Service.MARKET,),
        genitive="комиссионки",
    ),
    # ---------- Северный Вал ----------
    Location(
        "northern_wall_shop",
        "«Северный Вал»",
        district="northern_wall_premium",
        # стеклянные двери между витринами
        entrance=(
            (0.455898, 0.261364), (0.557917, 0.266148),
            (0.557917, 0.338517), (0.449522, 0.333134),
        ),
        services=(Service.FAN,),
        genitive="фанатского магазина",
    ),
    Location(
        "premium_shop",
        "Элитный магазин",
        district="northern_wall_premium",
        # центральные двери под вывеской «ЭЛИТА»
        entrance=(
            (0.396387, 0.632775), (0.592986, 0.650718),
            (0.592986, 0.748804), (0.395324, 0.723684),
        ),
        services=(Service.PREMIUM,),
        genitive="элитного магазина",
    ),
    # ---------- Деловой квартал: банк, рынок, почта ----------
    Location(
        "bank",
        "Банк",
        district="bank_market_post",
        # В манифесте банк заходит на рынок тридцатью пикселями по нижней
        # кромке. Полоса на два пальца шириной, но нажатие в ней — монетка:
        # поэтому банк подрезан ровно до крыши рынка
        # массивные двери-хранилище под вывеской «БАНК»
        entrance=(
            (0.442083, 0.235646), (0.613177, 0.240431),
            (0.611052, 0.325359), (0.446334, 0.321172),
        ),
        soon="хранение денег",
        genitive="банка",
    ),
    Location(
        "market",
        "Рынок",
        district="bank_market_post",
        # открытый проход в торговые ряды под вывеской «РЫНОК»
        entrance=(
            (0.140276, 0.536483), (0.308183, 0.500598),
            (0.300744, 0.559809), (0.140276, 0.606459),
        ),
        soon="торговля между игроками",
        genitive="рынка",
    ),
    Location(
        "post_office",
        "Почта",
        district="bank_market_post",
        # двойные двери под вывеской «ПОЧТА»
        entrance=(
            (0.740701, 0.579545), (0.825717, 0.606459),
            (0.819341, 0.649522), (0.738576, 0.626794),
        ),
        soon="награды и подарки",
        genitive="почты",
    ),
    # ---------- Стадион и бар ----------
    Location(
        "stadium",
        "Стадион",
        district="stadium_bar",
        # главные ворота под вывеской «СТАДИОН»
        entrance=(
            (0.436769, 0.269139), (0.63762, 0.279306),
            (0.634431, 0.346292), (0.436769, 0.333134),
        ),
        soon="элитный рейд",
        genitive="стадиона",
    ),
    Location(
        "bar",
        "Бар",
        district="stadium_bar",
        # центральная дверь под вывеской «БАР»
        entrance=(
            (0.488842, 0.670455), (0.5983, 0.678828),
            (0.534538, 0.725478), (0.485654, 0.716507),
        ),
        soon="задания и угощения",
        genitive="бара",
    ),

    # ---------- вторая очередь: десять районов, девятнадцать домов ----------
    #
    # Услуг за ними пока нет ни одной: город вырос картинками, а правила
    # к ним будут писаться по одному дому. Зайти при этом можно в любой —
    # внутри вид изнутри и записка о том, чего ждать.
    #
    # Двери сняты с самих картинок, по четырём углам видимого проёма.
    # Сначала они стояли по шаблону — верхнему дому карты доставалась
    # дверь магазина одежды, нижнему дверь аптеки, — и подсветка садилась
    # на косяк как придётся. Бланк для новой разметки лежит в
    # docs/doors.md, пересчёт пикселей в доли делает scripts/doors.py.

    Location(
        # Код дома и код района остались от VCPD: так названы файлы в
        # хранилище, и переименовать их значило бы разойтись с ним. На
        # вывеске при этом то, что понятно без расшифровки
        "vcpd",
        "Полицейский участок",
        district=VCPD,
        entrance=(
            (0.412327, 0.241029), (0.5983, 0.244019),
            (0.5983, 0.308612), (0.41339, 0.305024),
        ),
        soon="дежурная часть и розыск",
        genitive="полицейского участка",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "hospital",
        "Больница",
        district=VCPD,
        entrance=(
            (0.339001, 0.669258), (0.579171, 0.708732),
            (0.580234, 0.757775), (0.340064, 0.720694),
        ),
        services=(Service.HEAL,),
        genitive="больницы",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "driving_school",
        "Автошкола",
        district=DRIVING,
        entrance=(
            (0.302869, 0.226077), (0.431456, 0.212919),
            (0.42508, 0.271531), (0.30712, 0.285287),
        ),
        soon="права и первая машина",
        genitive="автошколы",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "insurance_office",
        "Страховая компания",
        district=DRIVING,
        entrance=(
            (0.548353, 0.568182), (0.679065, 0.588517),
            (0.676939, 0.650718), (0.548353, 0.62799),
        ),
        soon="страховка вещей от износа",
        genitive="страховой",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "strength_gym",
        "Тренажёрный зал",
        district=GYM,
        entrance=(
            (0.560043, 0.272727), (0.701382, 0.26256),
            (0.699256, 0.324163), (0.561105, 0.333134),
        ),
        soon="тренировки на характеристики",
        genitive="зала",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "office_building",
        "Офисное здание",
        district=GYM,
        entrance=(
            (0.592986, 0.678828), (0.712009, 0.66866),
            (0.714134, 0.723684), (0.591923, 0.736842),
        ),
        soon="работа и жалованье",
        genitive="офиса",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "military_base",
        "Армейская часть",
        district=BARRACKS,
        entrance=(
            (0.418704, 0.217105), (0.561105, 0.221292),
            (0.556854, 0.276914), (0.419766, 0.268541),
        ),
        soon="служба и звания",
        genitive="части",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "indoor_training_ground",
        "Крытый полигон",
        district=BARRACKS,
        entrance=(
            (0.378321, 0.858852), (0.5356, 0.87201),
            (0.5356, 0.915072), (0.377258, 0.898923),
        ),
        soon="стрельба и спарринги",
        genitive="полигона",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "cadet_corps",
        "Кадетский корпус",
        district=CADETS,
        entrance=(
            (0.454835, 0.226675), (0.582359, 0.226077),
            (0.582359, 0.279306), (0.453773, 0.278708),
        ),
        soon="школа для новичков",
        genitive="корпуса",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "dormitory",
        "Общежитие",
        district=CADETS,
        entrance=(
            (0.432519, 0.67823), (0.561105, 0.680622),
            (0.55898, 0.721292), (0.42933, 0.721292),
        ),
        soon="отдых и восстановление",
        genitive="общежития",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        # Код остался от прежнего имени — так назван файл в хранилище,
        # и он же стоит в коде района
        "police_school",
        "Полицейская академия",
        district=SCHOOLS,
        entrance=(
            (0.431456, 0.248804), (0.536663, 0.243421),
            (0.536663, 0.302033), (0.432519, 0.307416),
        ),
        soon="путь в полицию",
        genitive="полицейской академии",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "medical_college",
        "Медицинский колледж",
        district=SCHOOLS,
        entrance=(
            (0.4644, 0.646531), (0.61424, 0.654306),
            (0.612115, 0.703947), (0.463337, 0.694378),
        ),
        soon="ремесло лекаря",
        genitive="колледжа",
        interior_folder=art.NEW_INTERIORS,
    ),

    # На карте жилого квартала нарисованы четыре дома, и дверь у каждого
    # своя. Домов четыре, а вид изнутри один на всех: внутри они
    # одинаковые, и заводить четыре одинаковые картинки незачем. Работать
    # они тоже будут одинаково — когда своё жильё вообще появится.
    Location(
        "residential_apartment",
        "Жилой дом №1",
        district=HOUSES,
        # слева сверху
        entrance=(
            (0.332625, 0.242225), (0.418704, 0.227871),
            (0.418704, 0.274522), (0.331562, 0.288278),
        ),
        soon="своё жильё",
        genitive="жилого дома",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "residential_apartment_2",
        "Жилой дом №2",
        district=HOUSES,
        # справа сверху
        entrance=(
            (0.723698, 0.276914), (0.807651, 0.293062),
            (0.802338, 0.339115), (0.724761, 0.322368),
        ),
        soon="своё жильё",
        genitive="жилого дома",
        interior="residential_apartment_interior",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "residential_apartment_3",
        "Жилой дом №3",
        district=HOUSES,
        # слева снизу
        entrance=(
            (0.11796, 0.551435), (0.215728, 0.532297),
            (0.215728, 0.589115), (0.11796, 0.608852),
        ),
        soon="своё жильё",
        genitive="жилого дома",
        interior="residential_apartment_interior",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "residential_apartment_4",
        "Жилой дом №4",
        district=HOUSES,
        # справа снизу
        entrance=(
            (0.785335, 0.57177), (0.873539, 0.601675),
            (0.872476, 0.656699), (0.785335, 0.626794),
        ),
        soon="своё жильё",
        genitive="жилого дома",
        interior="residential_apartment_interior",
        interior_folder=art.NEW_INTERIORS,
    ),

    Location(
        "mafia_mansion",
        "Особняк мафии",
        district=MAFIA,
        entrance=(
            (0.477152, 0.272129), (0.561105, 0.276316),
            (0.562168, 0.33134), (0.476089, 0.325359),
        ),
        soon="дела, о которых не пишут",
        genitive="особняка",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "fight_tournament_stadium",
        "Турнирная арена",
        district=ARENA,
        entrance=(
            (0.430393, 0.360646), (0.57492, 0.363636),
            (0.572795, 0.420455), (0.431456, 0.413876),
        ),
        soon="турниры на выбывание",
        genitive="арены",
        interior_folder=art.NEW_INTERIORS,
    ),
    Location(
        "car_dealership",
        "Автосалон",
        district=CARS,
        entrance=(
            (0.345377, 0.354067), (0.454835, 0.354067),
            (0.45271, 0.409091), (0.34644, 0.409091),
        ),
        soon="машины и гаражи",
        genitive="автосалона",
        interior_folder=art.NEW_INTERIORS,
    ),
)

BY_CODE: dict[str, Location] = {place.code: place for place in LOCATIONS}
DISTRICT_BY_CODE: dict[str, District] = {one.code: one for one in DISTRICTS}


def get_location(code: str | None) -> Location | None:
    return BY_CODE.get(code or "")


def get_district(code: str | None) -> District | None:
    return DISTRICT_BY_CODE.get(code or "")


# Магазины города: услуга — это прилавок, за которым стоят
SHOP_SERVICES: tuple[Service, ...] = (
    Service.WEAPONS,
    Service.CLOTHES,
    Service.POTIONS,
    Service.PREMIUM,
    Service.FAN,
)


def service_for(code: str) -> Service:
    """В каком магазине лежит эта вещь. Оружие и щиты — у оружейника.

    По коду, а не по слоту: склянки слота не имеют вовсе, а на прилавке
    стоят наравне с вещами.
    """
    from bot.game.equipment import FAN_SHELF, Slot, get_item
    from bot.game.potions import get_potion

    if get_potion(code) is not None:
        return Service.POTIONS
    item = get_item(code)
    if item is None:
        return Service.CLOTHES  # чего нет в каталоге, то не купят нигде
    if item.is_magic:
        return Service.PREMIUM
    # У тематического магазина свой прилавок: и купить, и сдать фанатскую
    # биту можно только там, хотя слот у неё оружейный
    if item.shelf == FAN_SHELF:
        return Service.FAN
    return (
        Service.WEAPONS
        if item.slot in (Slot.WEAPON, Slot.OFFHAND)
        else Service.CLOTHES
    )


def where_to(service: Service) -> Location | None:
    """Куда идти за этим делом. Первая же локация, где оно есть."""
    for place in LOCATIONS:
        if place.allows(service):
            return place
    return None


__all__ = [
    "BY_CODE",
    "DISTRICTS",
    "District",
    "DOWN",
    "LEFT",
    "OPPOSITE",
    "RIGHT",
    "UP",
    "FIGHT_CLUB",
    "LOCATIONS",
    "Location",
    "Rect",
    "STEP",
    "SHOP_SERVICES",
    "Service",
    "service_for",
    "district_hops",
    "get_district",
    "get_location",
    "travel_seconds",
    "where_to",
]
