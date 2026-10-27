"""Машины города: салон, два служебных гаража и место за рулём.

Правил тут три, и все они про одно — машина меняет город в размерах.

**Машина — не вещь.** Её не носят в рюкзаке, не чинят и не теряют в
бою: она стоит в гараже, за руль её берут и ставят обратно. Поэтому у
неё нет ни слота, ни износа, а есть срок аренды и место в гараже.

**За руль садятся с правами.** Без водительского удостоверения машина
остаётся картинкой в салоне. Это первое, что права в городе дают,
кроме места водителя в таксопарке.

**Быстрее — дороже.** Дорога по городу считается шагами между
районами, и машина делит её на своё число: от двух раз у хэтчбека до
десяти у гиперкара. Город от этого не становится меньше — он
становится быстрее, и за это платят.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.art import BUCKET
from bot.game.clans import GARAGE_SLOTS

# Папка картинок: по файлу на машину, все в 16:9
VEHICLES = f"{BUCKET}/vehicles"

# Чья машина: салонная продаётся и сдаётся, служебную выдаёт гараж
SALON = "salon"
POLICE = "police"
TAXI = "taxi"

# Дома, в которых стоят машины
SALON_PLACE = "car_dealership"
POLICE_PLACE = "vcpd"
TAXI_PLACE = "taxi_depot"

DAY = 24 * 60 * 60

# Аренда месяца — двадцать суток по цене дня: иначе месяц незачем и
# предлагать. Покупка — сто суток, то есть пять месяцев: дольше пяти
# месяцев дешевле ездить на своей
MONTH_DAYS = 30
MONTH_PRICE_DAYS = 20
BUY_PRICE_DAYS = 100

# Должности, которым гараж выдаёт машину
CHIEF_JOB = "police_chief"
POLICE_JOBS: tuple[str, ...] = (CHIEF_JOB, "police_officer", "police_patrol")
TAXI_JOB = "taxi_driver"

# Код парковой машины: он же и единственный в своём гараже
TAXI_CAR = "taxi"
# Машины участка: патрульная любому в участке, начальничья — начальнику
PATROL_CAR = "vcpd_patrol"
CHIEF_CAR = "vcpd_chief"

# Сколько таксопарк берёт за смену за рулём. Смена — два часа, за
# неделю их пять, а жалованье водителя пятьсот: аренда забирает пятую
# часть смены, а не всю
TAXI_SHIFT_PRICE = 20


@dataclass(frozen=True)
class Car:
    """Машина: во сколько раз быстрее, почём и кому её дают.

    `times` — во сколько раз короче дорога. Это и есть вся машина:
    других свойств у неё нет, потому что в бою она не участвует, а в
    городе делает одно — везёт.

    `day` — цена суток аренды. Месяц и покупка считаются от неё: держать
    три цены руками значит однажды получить месяц дороже покупки.

    `jobs` — должности, которым машину выдаёт гараж. Пусто — машина
    салонная: её покупают или снимают, а не получают по службе.
    """

    code: str
    title: str
    file: str
    times: int
    day: int = 0
    kind: str = SALON
    note: str = ""
    # Сколько таких машин в гараже. У салона мест нет: сколько купят,
    # столько и соберут
    seats: int = 0
    jobs: tuple[str, ...] = ()

    @property
    def image(self) -> str:
        """Картинка 16:9: vehicles/01_street_compact.png."""
        return f"{VEHICLES}/{self.file}"

    @property
    def cutout(self) -> str:
        """Та же машина без фона: vehicles/01_street_compact_transparent.png.

        На витрине и в гараже машина стоит в своей студии — серый фон
        там часть картинки. А за спиной бойца студия лишняя: там уже
        есть фон рамки, и второй поверх него ложится заплаткой. Поэтому
        под аватар идёт вырезанная версия, а имя её считается от того же
        файла: держать второе имя руками значит однажды их рассинхронить.
        """
        stem = self.file.rsplit(".", 1)[0]
        return f"{VEHICLES}/{stem}_transparent.png"

    @property
    def month(self) -> int:
        return self.day * MONTH_PRICE_DAYS

    @property
    def price(self) -> int:
        return self.day * BUY_PRICE_DAYS

    @property
    def service(self) -> bool:
        """Служебная: такую не покупают, её выдаёт гараж."""
        return self.kind != SALON

    @property
    def times_text(self) -> str:
        return f"×{self.times}"


CARS: tuple[Car, ...] = (
    # ---------- салон: десять машин, от городской до гиперкара ----------
    Car(
        "street_compact", "Городской хэтчбек", "01_street_compact.png",
        times=2, day=30,
        note="Первая машина: не быстрая, зато своя.",
    ),
    Car(
        "city_sedan", "Кибер-«семёрка»", "02_city_sedan.png",
        times=3, day=50,
        note="Классика с подсветкой: такие город помнит.",
    ),
    Car(
        "neon_coupe", "Неоновое купе", "03_neon_coupe.png",
        times=4, day=80,
        note="Два места и ночь впереди.",
    ),
    Car(
        "street_rally", "Раллийный седан", "04_street_rally.png",
        times=5, day=120,
        note="Ему всё равно, асфальт под ним или нет.",
    ),
    Car(
        "shadow_sport", "Спортивный седан", "05_shadow_sport.png",
        times=6, day=160,
        note="Быстрый и незаметный — редкое сочетание.",
    ),
    Car(
        "iron_muscle", "Тяжёлый маслкар", "06_iron_muscle.png",
        times=6, day=200,
        note="Железа в нём больше, чем скорости, — и это слышно.",
    ),
    Car(
        "armored_suv", "Бронированный внедорожник", "07_armored_suv.png",
        times=7, day=250,
        note="Возит тех, кого возить опасно.",
    ),
    Car(
        "neon_gt", "Прокачанный GT", "08_neon_gt.png",
        times=8, day=300,
        note="Собран из того, чего в салоне не купишь.",
    ),
    Car(
        "phantom_supercar", "Фиолетовый суперкар", "09_phantom_supercar.png",
        times=9, day=400,
        note="По городу на нём ездят не затем, чтобы доехать.",
    ),
    Car(
        "apex_hypercar", "Элитный гиперкар", "10_apex_hypercar.png",
        times=10, day=600,
        note="Весь город за то время, что другие идут до соседней двери.",
    ),

    # ---------- гараж участка: две патрульные и машина начальника ----------
    Car(
        PATROL_CAR, "Патрульная VCPD", "11_vcpd_patrol.png",
        times=8, kind=POLICE, seats=2, jobs=POLICE_JOBS,
        note="Служебная: с сиреной город короче.",
    ),
    Car(
        CHIEF_CAR, "Машина начальника полиции", "12_vcpd_chief.png",
        times=9, kind=POLICE, seats=1, jobs=(CHIEF_JOB,),
        note="Одна на город, и ключи от неё одни.",
    ),

    # ---------- гараж таксопарка: три машины на смену ----------
    Car(
        TAXI_CAR, "Такси", "13_taxi.png",
        times=5, day=TAXI_SHIFT_PRICE, kind=TAXI, seats=3, jobs=(TAXI_JOB,),
        note="Казённая: берут на смену и сдают после неё.",
    ),
)

BY_CODE: dict[str, Car] = {one.code: one for one in CARS}

SHOWCASE: tuple[Car, ...] = tuple(one for one in CARS if one.kind == SALON)

# Что стоит в каком гараже
GARAGES: dict[str, tuple[Car, ...]] = {
    POLICE_PLACE: tuple(one for one in CARS if one.kind == POLICE),
    TAXI_PLACE: tuple(one for one in CARS if one.kind == TAXI),
}

# Сутки или месяц: других сроков аренды нет
SPANS: tuple[str, ...] = ("day", "month")

# ---------- сколько машин держат ----------
#
# Машина у бойца одна. Это не скупость: город короче в два раза с
# хэтчбеком и в десять с гиперкаром, и держать весь парк разом значило
# бы выбирать скорость под задачу, а не покупать машину. Подписка даёт
# три — это и есть её польза, а не обход правила.
#
# Служебная в счёт не идёт: патрульная и такси не его, их выдаёт гараж
# на смену и забирает обратно.

MAX_CARS = 1
PRO_MAX_CARS = 3


def cars_allowed(is_pro: bool) -> int:
    """Сколько машин боец может держать: одну, а с подпиской три."""
    return PRO_MAX_CARS if is_pro else MAX_CARS


# ---------- где машина стоит ----------
#
# Машина либо при хозяине, либо на парковке. Парковка — это место, а не
# владение: хозяин у неё тот же, и забрать её может только он. Поэтому
# «поставить» и «отдать» — разные дела: отданная машина уходит, а
# поставленная просто стоит в другом месте.

AT_HAND = ""
IN_CLAN = "clan"  # парковка особняка мафии: место на всю семью
IN_FLAT = "flat"  # место при своей квартире
IN_POLICE = "police"  # стоянка у участка: городская, под фонарём

PARKINGS: tuple[str, ...] = (IN_CLAN, IN_FLAT, IN_POLICE)

# Сколько машин влезает. Гараж особняка — имущество семьи, и число
# мест лежит у неё же; у квартиры место одно: оно при доме, а не
# стоянка; у участка стоянка городская, и мест на ней два десятка
CLAN_PARKING = GARAGE_SLOTS
FLAT_PARKING = 1
POLICE_PARKING = 20

PARKING_TITLES: dict[str, str] = {
    IN_CLAN: "парковка особняка",
    IN_FLAT: "место при квартире",
    IN_POLICE: "стоянка у участка",
}

PARKING_SLOTS: dict[str, int] = {
    IN_CLAN: CLAN_PARKING,
    IN_FLAT: FLAT_PARKING,
    IN_POLICE: POLICE_PARKING,
}

# Стоянка — и есть безопасное место. Поставленную машину не угоняют
# нигде: ни у особняка, ни при квартире, ни у участка. Проверить её
# полиция может всюду — на это стоянка не влияет
SAFE_PARKINGS: frozenset[str] = frozenset(PARKINGS)

# ---------- угон ----------
#
# Угоняют только надетую машину: поставил на стоянку — она в
# безопасности, оставил на себе — её уводят. Это и есть цена за то,
# чтобы дорога всегда была короче.

# Сколько держится право увести машину после выигранного нападения.
# Победа над живым человеком — это и есть выбор «угнать или нет», и
# выбирать его дают не вечно: четверть часа, и ключи остыли
STEAL_WINDOW = 15 * 60

# Сколько стоит перебить номер на автомойке. Дорого нарочно: это
# единственный способ снять с машины розыск, и он не должен быть
# дешевле самой дешёвой машины в городе
RESTAMP_PRICE = 1_500


def parking_title(parked: str) -> str:
    """Как парковка называется в строке. Пусто — машина при хозяине."""
    return PARKING_TITLES.get(parked, "")


def parking_slots(parked: str) -> int:
    """Сколько машин влезает на эту парковку."""
    return PARKING_SLOTS.get(parked, 0)


def get_car(code: str) -> Car | None:
    return BY_CODE.get(code or "")


def times_of(code: str) -> int:
    """Во сколько раз короче дорога за рулём. Пешком — в один."""
    car = get_car(code)
    return car.times if car else 1


def road_seconds(walk: int, times: int) -> int:
    """Дорога за рулём: та же дорога, делённая на машину.

    Короче секунды дорога не бывает: иначе гиперкар отменял бы
    расстояния вовсе, а город без расстояний — это список кнопок.
    """
    if walk <= 0:
        return max(0, walk)
    if times <= 1:
        return walk
    return max(1, walk // times)


def rent_seconds(span: str) -> int:
    """Сколько длится аренда: сутки или месяц."""
    return DAY if span == "day" else MONTH_DAYS * DAY


def rent_price(car: Car, span: str) -> int:
    return car.day if span == "day" else car.month


def span_title(span: str) -> str:
    return "сутки" if span == "day" else "месяц"


def may_take(car: Car, job_code: str) -> bool:
    """Выдаст ли гараж эту машину тому, кто на этой должности."""
    return bool(car.jobs) and job_code in car.jobs


def garage_of(place: str) -> tuple[Car, ...]:
    """Что стоит в гараже этого дома. Пусто — гаража тут нет."""
    return GARAGES.get(place, ())


__all__ = [
    "AT_HAND",
    "IN_POLICE",
    "POLICE_PARKING",
    "RESTAMP_PRICE",
    "SAFE_PARKINGS",
    "STEAL_WINDOW",
    "BUY_PRICE_DAYS",
    "CLAN_PARKING",
    "CARS",
    "CHIEF_CAR",
    "CHIEF_JOB",
    "Car",
    "FLAT_PARKING",
    "GARAGES",
    "IN_CLAN",
    "IN_FLAT",
    "MAX_CARS",
    "MONTH_DAYS",
    "MONTH_PRICE_DAYS",
    "POLICE",
    "POLICE_JOBS",
    "PATROL_CAR",
    "POLICE_PLACE",
    "PARKINGS",
    "PRO_MAX_CARS",
    "PARKING_SLOTS",
    "SALON",
    "SALON_PLACE",
    "SHOWCASE",
    "SPANS",
    "TAXI",
    "TAXI_CAR",
    "TAXI_JOB",
    "TAXI_PLACE",
    "TAXI_SHIFT_PRICE",
    "VEHICLES",
    "cars_allowed",
    "garage_of",
    "get_car",
    "may_take",
    "parking_slots",
    "parking_title",
    "rent_price",
    "rent_seconds",
    "road_seconds",
    "span_title",
    "times_of",
]
