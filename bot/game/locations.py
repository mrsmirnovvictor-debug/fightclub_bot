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
    """Зона нажатия на карте, долями от самой картинки.

    Доли, а не пиксели: карта показывается через `object-fit: contain` и
    на каждом экране своего размера. Считать зоны нужно от нарисованной
    картинки, а не от окна — иначе на телефоне с другим соотношением
    сторон поля по краям сдвинут все дома.
    """

    x: float
    y: float
    w: float
    h: float

    def holds(self, x: float, y: float) -> bool:
        """Попало ли касание в эту зону. Обе доли — от картинки."""
        return self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True)
class Location:
    """Здание на карте: как называется, в каком районе и что тут делают."""

    code: str
    title: str
    district: str  # код района: одна карта — один район
    zone: Rect
    services: tuple[Service, ...] = ()
    # Что здесь будет, когда дойдут руки. Пустая строка — дом работает
    soon: str = ""
    # Родительный падеж для фраз «дойти до мастерской»
    genitive: str = ""

    def allows(self, service: Service) -> bool:
        return service in self.services

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
        zone=Rect(0.244, 0.060, 0.542, 0.329),
        services=(Service.FIGHT,),
        genitive="бойцовского клуба",
    ),
    Location(
        "weapon_shop",
        "Оружейный магазин",
        district="main_hub",
        zone=Rect(0.000, 0.419, 0.457, 0.329),
        services=(Service.WEAPONS,),
        genitive="оружейного магазина",
    ),
    Location(
        "workshop",
        "Мастерская",
        district="main_hub",
        zone=Rect(0.542, 0.419, 0.458, 0.335),
        services=(Service.REPAIR,),
        genitive="мастерской",
    ),
    # ---------- Торговый квартал: одежда и аптека ----------
    Location(
        "clothes_shop",
        "Магазин одежды",
        district="clothes_pharmacy",
        zone=Rect(0.138, 0.048, 0.723, 0.311),
        services=(Service.CLOTHES,),
        genitive="магазина одежды",
    ),
    Location(
        "pharmacy",
        "Аптека",
        district="clothes_pharmacy",
        zone=Rect(0.255, 0.508, 0.616, 0.311),
        services=(Service.POTIONS,),
        genitive="аптеки",
    ),
    # ---------- Старый город: казино и комиссионка ----------
    Location(
        "casino",
        "Подпольное казино",
        district="pawnshop_casino",
        zone=Rect(0.181, 0.042, 0.669, 0.335),
        services=(Service.RAID,),
        genitive="казино",
    ),
    Location(
        "pawnshop",
        "Комиссионный магазин",
        district="pawnshop_casino",
        zone=Rect(0.117, 0.556, 0.691, 0.281),
        services=(Service.MARKET,),
        genitive="комиссионки",
    ),
    # ---------- Северный Вал ----------
    Location(
        "northern_wall_shop",
        "«Северный Вал»",
        district="northern_wall_premium",
        zone=Rect(0.138, 0.036, 0.723, 0.341),
        soon="фанатская экипировка",
        genitive="фанатского магазина",
    ),
    Location(
        "premium_shop",
        "Элитный магазин",
        district="northern_wall_premium",
        zone=Rect(0.138, 0.478, 0.744, 0.317),
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
        zone=Rect(0.213, 0.030, 0.595, 0.341),
        soon="хранение денег",
        genitive="банка",
    ),
    Location(
        "market",
        "Рынок",
        district="bank_market_post",
        zone=Rect(0.000, 0.371, 0.510, 0.335),
        soon="торговля между игроками",
        genitive="рынка",
    ),
    Location(
        "post_office",
        "Почта",
        district="bank_market_post",
        zone=Rect(0.531, 0.389, 0.469, 0.335),
        soon="награды и подарки",
        genitive="почты",
    ),
    # ---------- Стадион и бар ----------
    Location(
        "stadium",
        "Стадион",
        district="stadium_bar",
        zone=Rect(0.128, 0.000, 0.744, 0.431),
        soon="элитный рейд",
        genitive="стадиона",
    ),
    Location(
        "bar",
        "Бар",
        district="stadium_bar",
        zone=Rect(0.213, 0.490, 0.659, 0.311),
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
