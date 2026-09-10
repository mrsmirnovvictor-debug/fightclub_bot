"""Карта города: где боец стоит и что ему там доступно.

Раньше всё жило на вкладках: лавка, аптека, ринг и подвал открывались
одинаково из любого места. Теперь у каждого дела есть адрес — драться
идут в клуб, чинить вещи в мастерскую, за склянками в аптеку, — и боец
по городу ходит.

Правило проверяется на сервере, а не прятанием кнопок: спрятанная кнопка
обходится запросом мимо интерфейса, и тогда карта — украшение. Поэтому
каждая ручка спрашивает у локации, можно ли здесь то, что просят.

Дорога занимает время: соседнее здание в своём районе ближе, чем другой
конец города. Пока боец в пути, он не в старом месте и ещё не в новом —
и не может ни то, ни другое.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.game import art


class Service(str, Enum):
    """Чем занимаются в локации. У ручки сервера — своя услуга."""

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
class Location:
    """Здание на карте: как называется, в каком районе и что тут делают."""

    code: str
    title: str
    district: str  # код района: одна карта — один район
    services: tuple[Service, ...] = ()
    # Родительный падеж для фраз «дойти до мастерской»
    genitive: str = ""

    def allows(self, service: Service) -> bool:
        return service in self.services

    @property
    def whither(self) -> str:
        return self.genitive or self.title

    @property
    def image(self) -> str:
        """Картинка района, на которой стоит это здание."""
        return art.location(self.district)


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
# Районы — это шесть карт города, по карте на район. Здания внутри
# расставлены на них зонами нажатия; сами зоны живут в веб-части, здесь
# только то, что нужно правилам: где боец стоит и что ему тут можно.

FIGHT_CLUB = "fight_club"

LOCATIONS: tuple[Location, ...] = (
    Location(
        FIGHT_CLUB,
        "Бойцовский клуб Vegas",
        district="main_hub",
        services=(Service.FIGHT,),
        genitive="бойцовского клуба",
    ),
    Location(
        "weapons",
        "Оружие",
        district="main_hub",
        services=(Service.WEAPONS,),
        genitive="оружейной лавки",
    ),
    Location(
        "clothes",
        "Одежда",
        district="clothes_pharmacy",
        services=(Service.CLOTHES,),
        genitive="лавки одежды",
    ),
    Location(
        "pharmacy",
        "Аптека",
        district="clothes_pharmacy",
        services=(Service.POTIONS,),
        genitive="аптеки",
    ),
    Location(
        "workshop",
        "Мастерская",
        district="main_hub",
        services=(Service.REPAIR,),
        genitive="мастерской",
    ),
    Location(
        "pawnshop",
        "Комиссионка",
        district="pawnshop_casino",
        services=(Service.MARKET,),
        genitive="комиссионки",
    ),
    Location(
        "casino",
        "Подпольное казино",
        district="pawnshop_casino",
        services=(Service.RAID,),
        genitive="казино",
    ),
    Location(
        "premium",
        "Элитный магазин",
        district="northern_wall_premium",
        services=(Service.PREMIUM,),
        genitive="элитного магазина",
    ),
)

BY_CODE: dict[str, Location] = {place.code: place for place in LOCATIONS}


def get_location(code: str | None) -> Location | None:
    return BY_CODE.get(code or "")


def where_to(service: Service) -> Location | None:
    """Куда идти за этим делом. Первая же локация, где оно есть."""
    for place in LOCATIONS:
        if place.allows(service):
            return place
    return None


def districts() -> tuple[str, ...]:
    """Районы в том порядке, в каком они впервые встречаются на карте."""
    seen: list[str] = []
    for place in LOCATIONS:
        if place.district not in seen:
            seen.append(place.district)
    return tuple(seen)


__all__ = [
    "BY_CODE",
    "FIGHT_CLUB",
    "LOCATIONS",
    "Location",
    "STEP_BETWEEN",
    "STEP_INSIDE",
    "Service",
    "districts",
    "get_location",
    "travel_seconds",
    "where_to",
]
