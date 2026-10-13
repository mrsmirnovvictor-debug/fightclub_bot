"""Аренда жилья: четыре дома в жилом квартале, пятьдесят семь квартир.

Квартира — это место, где у бойца есть сейф под кредиты и кладовая под
вещи. Больше она пока ничего не делает: ни здоровья, ни бонусов, ни
второй двери в город. Поэтому и правил у неё немного — срок, цена и
вместимость.

**Дома нарисованы, и планировка у них разная.** В домах №1–3 четыре
этажа по четыре квартиры: две однокомнатные, двушка и трёшка. В доме №4
три этажа по три квартиры: трёшка и две двушки. Номера идут, как в
жизни, — снизу вверх и по порядку: первый этаж дома №1 это квартиры
1–4, второй — 5–8.

**Месяц вперёд.** Тридцать дней от часа найма, час в час; цена — 300,
400 и 500 кредитов за комнатную, двухкомнатную и трёхкомнатную. Продлить
можно в любой момент, и новый месяц ложится поверх старого срока, а не
от сегодняшнего дня.

**Срок вышел — квартира заперта.** Ни положить, ни взять: не платил —
не входишь. Продлил — снова открыта, и всё лежит на месте.

**Неделя просрочки, и квартиру сдают другому.** Но имущество при этом не
пропадает: вещи возвращаются хозяину в рюкзак, кредиты из сейфа — на
руки. Терять чужое добро за неуплату клуб не берётся, а держать жильё за
тем, кто перестал играть, — тем более.

**Одна квартира на бойца.** Не из экономики, а из простоты: сейф и
кладовая должны быть одним местом, а не списком адресов.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------- сроки ----------

DAY = 24 * 60 * 60

# Месяц аренды — те же тридцать дней, что у полиса и у подписки
RENT_DAYS = 30
RENT_SECONDS = RENT_DAYS * DAY

# Просрочка: столько квартира ждёт хозяина после конца срока
GRACE_DAYS = 7
GRACE_SECONDS = GRACE_DAYS * DAY

# ---------- комнаты ----------

# Цена месяца по числу комнат
RENT_PRICE: dict[int, int] = {1: 300, 2: 400, 3: 500}

# Вместимость кладовой: шесть вещей на комнату
FLAT_SLOTS: dict[int, int] = {1: 6, 2: 12, 3: 18}

ROOM_TITLES: dict[int, str] = {
    1: "однокомнатная",
    2: "двухкомнатная",
    3: "трёхкомнатная",
}

ROOM_SHORT: dict[int, str] = {1: "1 комн.", 2: "2 комн.", 3: "3 комн."}

# ---------- дома ----------

# Планировка: дом — это список этажей, этаж — комнатность квартир на нём,
# в том порядке, в каком они идут по номерам. Отсюда считается и число
# квартир, и номер каждой: держать их списком в базе незачем — дом
# нарисован один раз и не меняется
FLOORS: dict[str, tuple[tuple[int, ...], ...]] = {
    "residential_apartment": ((1, 1, 2, 3),) * 4,
    "residential_apartment_2": ((1, 1, 2, 3),) * 4,
    "residential_apartment_3": ((1, 1, 2, 3),) * 4,
    "residential_apartment_4": ((3, 2, 2),) * 3,
}

HOUSES: tuple[str, ...] = tuple(FLOORS)


@dataclass(frozen=True)
class Flat:
    """Квартира в доме: адрес и комнатность. Из планировки, не из базы."""

    house: str
    number: int
    floor: int
    rooms: int

    @property
    def price(self) -> int:
        return RENT_PRICE[self.rooms]

    @property
    def slots(self) -> int:
        return FLAT_SLOTS[self.rooms]

    @property
    def rooms_title(self) -> str:
        return ROOM_TITLES[self.rooms]

    @property
    def title(self) -> str:
        return f"кв. {self.number}"


def flats_of(house: str) -> tuple[Flat, ...]:
    """Все квартиры дома по порядку номеров. Пусто — дом не жилой."""
    plan = FLOORS.get(house)
    if plan is None:
        return ()
    flats: list[Flat] = []
    number = 1
    for level, floor in enumerate(plan, start=1):
        for rooms in floor:
            flats.append(Flat(house=house, number=number, floor=level, rooms=rooms))
            number += 1
    return tuple(flats)


def flat_at(house: str, number: int) -> Flat | None:
    """Квартира по адресу. None — такой в доме нет."""
    return next(
        (flat for flat in flats_of(house) if flat.number == number), None
    )


def is_house(code: str) -> bool:
    """Жилой ли это дом: в нежилых квартир не сдают."""
    return code in FLOORS


@dataclass(frozen=True)
class Lease:
    """Наём: чья квартира, до какого часа оплачена и что в сейфе.

    Срок — единственное, что решает, открыта квартира или заперта.
    Просрочку считает `evicted`: после неё квартира освобождается, и
    имущество уезжает к хозяину.
    """

    id: int
    user_id: int
    house: str
    number: int
    rooms: int
    rented_at: int = 0
    paid_until: int = 0
    vault: int = 0

    @property
    def flat(self) -> Flat | None:
        return flat_at(self.house, self.number)

    @property
    def floor(self) -> int:
        flat = self.flat
        return flat.floor if flat else 0

    @property
    def price(self) -> int:
        return RENT_PRICE.get(self.rooms, 0)

    @property
    def slots(self) -> int:
        return FLAT_SLOTS.get(self.rooms, 0)

    @property
    def rooms_title(self) -> str:
        return ROOM_TITLES.get(self.rooms, "")

    @property
    def title(self) -> str:
        return f"кв. {self.number}"

    def paid(self, now: int) -> bool:
        """Оплачена ли квартира прямо сейчас."""
        return now < self.paid_until

    def locked(self, now: int) -> bool:
        """Заперта ли: срок вышел, а новый не оплачен."""
        return not self.paid(now)

    def seconds_left(self, now: int) -> int:
        """Сколько оплаченного времени осталось."""
        return max(0, self.paid_until - now)

    def grace_left(self, now: int) -> int:
        """Сколько осталось до выселения. Ноль — срок ещё идёт или уже всё."""
        if self.paid(now):
            return 0
        return max(0, self.paid_until + GRACE_SECONDS - now)

    def evicted(self, now: int) -> bool:
        """Пора ли освобождать квартиру: неделя просрочки прошла."""
        return now >= self.paid_until + GRACE_SECONDS


@dataclass(frozen=True)
class Kept:
    """Вещь в кладовой: она же, что в рюкзаке, только лежит у хозяина дома.

    Чьей она была, здесь не пишут: квартира одна и хозяин у неё один —
    в отличие от клановых полок, куда кладут всей семьёй.
    """

    id: int
    code: str
    wear: int = 0
    max_wear: int = 0
    mod: str = ""
    mod_value: int = 0
    put_at: int = 0


def extended(paid_until: int, now: int) -> int:
    """Срок после оплаты: месяц поверх прежнего, но не задним числом.

    Живая квартира продлевается от своего конца — заплативший вперёд
    получает два месяца, а не один. Просроченная начинается от этого
    часа: за дни, когда в квартиру не заходили, клуб денег не берёт и
    времени не возвращает.
    """
    return max(paid_until, now) + RENT_SECONDS


def house_rooms(house: str) -> dict[int, int]:
    """Сколько в доме квартир каждой комнатности: {1: 8, 2: 4, 3: 4}."""
    counted: dict[int, int] = {}
    for flat in flats_of(house):
        counted[flat.rooms] = counted.get(flat.rooms, 0) + 1
    return counted


__all__ = [
    "DAY",
    "FLAT_SLOTS",
    "FLOORS",
    "GRACE_DAYS",
    "GRACE_SECONDS",
    "HOUSES",
    "RENT_DAYS",
    "RENT_PRICE",
    "RENT_SECONDS",
    "ROOM_SHORT",
    "ROOM_TITLES",
    "Flat",
    "Kept",
    "Lease",
    "extended",
    "flat_at",
    "flats_of",
    "house_rooms",
    "is_house",
]
