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
        """Картинка района, на которой стоит это здание."""
        return art.location(self.district)


@dataclass(frozen=True)
class District:
    """Район: одна нарисованная карта, дома на ней и соседи по сторонам."""

    code: str
    title: str
    # Куда ведут стрелки: сторона света → код соседнего района. Города
    # на карте не видно целиком, и это единственное, что связывает шесть
    # картинок в один город
    around: dict[str, str] = field(default_factory=dict)

    @property
    def image(self) -> str:
        return art.location(self.code)

    @property
    def places(self) -> tuple[Location, ...]:
        return tuple(place for place in LOCATIONS if place.district == self.code)


# ---------- дорога ----------

# Секунды пути. По городу ходят пешком: соседнее здание в своём районе
# ближе, чем другой конец города, и это единственное, что отличает
# переход внутри района от перехода между районами.
STEP_INSIDE = 10
STEP_BETWEEN = 20


def travel_seconds(source: str, target: str) -> int:
    """Сколько идти от одного места до другого. Ноль — уже на месте."""
    if source == target:
        return 0
    here, there = get_location(source), get_location(target)
    if here is None or there is None:
        return STEP_BETWEEN
    return STEP_INSIDE if here.district == there.district else STEP_BETWEEN


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
# вверх Северный Вал, то из Вала вниз — центр. Это стережёт тест, иначе
# однажды из района можно будет выйти, но не вернуться.
UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"

DISTRICTS: tuple[District, ...] = (
    District("main_hub", "Центр", {
        UP: "northern_wall_premium",
        RIGHT: "clothes_pharmacy",
        LEFT: "pawnshop_casino",
    }),
    District("clothes_pharmacy", "Торговый квартал", {
        LEFT: "main_hub",
        UP: "stadium_bar",
    }),
    District("pawnshop_casino", "Старый город", {
        RIGHT: "main_hub",
        UP: "bank_market_post",
    }),
    District("northern_wall_premium", "Северный Вал", {
        DOWN: "main_hub",
        RIGHT: "stadium_bar",
        LEFT: "bank_market_post",
    }),
    District("bank_market_post", "Деловой квартал", {
        DOWN: "pawnshop_casino",
        RIGHT: "northern_wall_premium",
    }),
    District("stadium_bar", "Стадион", {
        DOWN: "clothes_pharmacy",
        LEFT: "northern_wall_premium",
    }),
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
    "STEP_BETWEEN",
    "STEP_INSIDE",
    "SHOP_SERVICES",
    "Service",
    "service_for",
    "get_district",
    "get_location",
    "travel_seconds",
    "where_to",
]
