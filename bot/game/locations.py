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

from dataclasses import dataclass
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
    """Район: одна нарисованная карта и дома на ней."""

    code: str
    title: str

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

# Нижний проход, общий для всех карт: им выходят в выбор района
EXIT_ZONE = Rect(0.35, 0.84, 0.30, 0.14)

# На сколько невидимо расширить дверь под палец, долями от карты.
# Подсветка при этом остаётся ровно на двери: расширение только для
# касания, иначе на рисунке загорится кусок стены рядом с ней
TOUCH_PAD_X = 0.025
TOUCH_PAD_Y = 0.015

DISTRICTS: tuple[District, ...] = (
    District("main_hub", "Центр"),
    District("clothes_pharmacy", "Торговый квартал"),
    District("pawnshop_casino", "Старый город"),
    District("northern_wall_premium", "Северный Вал"),
    District("bank_market_post", "Деловой квартал"),
    District("stadium_bar", "Стадион"),
)

LOCATIONS: tuple[Location, ...] = (
    # ---------- Центр: клуб, оружие, мастерская ----------
    Location(
        FIGHT_CLUB,
        "Бойцовский клуб VEGAS",
        district="main_hub",
        # двойные двери под вывеской VEGAS
        entrance=(
            (0.445, 0.258), (0.657, 0.258), (0.635, 0.356), (0.462, 0.356),
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
            (0.245, 0.571), (0.365, 0.568), (0.371, 0.651), (0.251, 0.653),
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
            (0.704, 0.548), (0.948, 0.555), (0.946, 0.658), (0.716, 0.65),
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
            (0.44, 0.239), (0.612, 0.239), (0.604, 0.319), (0.447, 0.319),
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
            (0.454, 0.644), (0.606, 0.644), (0.6, 0.731), (0.461, 0.731),
        ),
        services=(Service.POTIONS,),
        genitive="аптеки",
    ),
    # ---------- Старый город: казино и комиссионка ----------
    Location(
        "casino",
        "Подпольное казино",
        district="pawnshop_casino",
        # двойные двери под вывеской «КАЗИНО»
        entrance=(
            (0.435, 0.25), (0.612, 0.25), (0.605, 0.337), (0.445, 0.337),
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
            (0.365, 0.702), (0.5, 0.702), (0.5, 0.786), (0.37, 0.786),
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
            (0.442, 0.27), (0.574, 0.27), (0.57, 0.35), (0.447, 0.35),
        ),
        soon="фанатская экипировка",
        genitive="фанатского магазина",
    ),
    Location(
        "premium_shop",
        "Элитный магазин",
        district="northern_wall_premium",
        # центральные двери под вывеской «ЭЛИТА»
        entrance=(
            (0.426, 0.632), (0.591, 0.632), (0.584, 0.735), (0.435, 0.735),
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
            (0.408, 0.218), (0.607, 0.218), (0.596, 0.337), (0.419, 0.337),
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
            (0.115, 0.474), (0.344, 0.474), (0.371, 0.611), (0.083, 0.611),
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
            (0.704, 0.565), (0.85, 0.565), (0.85, 0.65), (0.71, 0.65),
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
            (0.383, 0.246), (0.661, 0.246), (0.65, 0.346), (0.395, 0.346),
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
            (0.447, 0.651), (0.588, 0.651), (0.586, 0.737), (0.451, 0.737),
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
)


def service_for(code: str) -> Service:
    """В каком магазине лежит эта вещь. Оружие и щиты — у оружейника.

    По коду, а не по слоту: склянки слота не имеют вовсе, а на прилавке
    стоят наравне с вещами.
    """
    from bot.game.equipment import Slot, get_item
    from bot.game.potions import get_potion

    if get_potion(code) is not None:
        return Service.POTIONS
    item = get_item(code)
    if item is None:
        return Service.CLOTHES  # чего нет в каталоге, то не купят нигде
    if item.is_magic:
        return Service.PREMIUM
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
    "EXIT_ZONE",
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
