"""Образы бойца: как он выглядит на карточке.

Образ — это не аватарка из Telegram и не эмодзи, а выбранная внешность
персонажа. Шесть образов открыты всем, ещё шесть продаются за кредиты и
остаются у бойца навсегда: купил один раз — переключайся сколько угодно.

Картинка образа лежит в бакете под его же кодом — `avatars/rookie.jpeg`,
поэтому отдельно её прописывать не нужно. Поле `image` остаётся на случай,
когда для образа понадобится файл с другим именем.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.art import avatar

# Сколько стоит платный образ. Ровно комплект своего уровня: покупка
# внешности должна ощущаться как выбор, а не как мелочь на сдачу.
LOOK_PRICE = 1000

MALE, FEMALE = "male", "female"
GENDER_TITLES = {MALE: "Мужские", FEMALE: "Женские"}


@dataclass(frozen=True)
class Look:
    """Образ: как он называется, чем рисуется и сколько стоит."""

    code: str
    title: str
    emoji: str
    gender: str
    price: int = 0
    image: str = ""  # пусто — берём файл по коду образа
    note: str = ""

    @property
    def paid(self) -> bool:
        return self.price > 0

    @property
    def picture(self) -> str:
        """Адрес картинки. Не загрузилась — карточка покажет значок."""
        return self.image or avatar(self.code)


LOOKS: tuple[Look, ...] = (
    # ---------- открыты всем ----------
    Look("rookie", "Новичок", "🥊", MALE, note="Пришёл с улицы, дерётся как умеет"),
    Look("worker", "Работяга", "🔧", MALE, note="Со смены — сразу в подвал"),
    Look("racer", "Гонщик", "🏍", MALE, note="Шлем оставил на мотоцикле"),
    Look("rebel", "Бунтарка", "💥", FEMALE, note="Пришла ломать порядок"),
    Look("barmaid", "Барменша", "🍸", FEMALE, note="Знает всех в этом клубе"),
    Look("runner", "Бегунья", "👟", FEMALE, note="Догонит и не запыхается"),
    # ---------- за кредиты ----------
    Look(
        "veteran",
        "Ветеран клуба",
        "🎖",
        MALE,
        price=LOOK_PRICE,
        note="Дрался, когда клуба ещё не было",
    ),
    Look(
        "boss",
        "Босс подвала",
        "🕴",
        MALE,
        price=LOOK_PRICE,
        note="Ему не надо драться, чтобы его боялись",
    ),
    Look(
        "ghost",
        "Призрак",
        "🥷",
        MALE,
        price=LOOK_PRICE,
        note="Его запоминают только по счёту",
    ),
    Look(
        "queen",
        "Королева ринга",
        "👑",
        FEMALE,
        price=LOOK_PRICE,
        note="Выходит последней и уходит первой",
    ),
    Look(
        "venom",
        "Ядовитая",
        "☠️",
        FEMALE,
        price=LOOK_PRICE,
        note="Бьёт один раз, но по больному",
    ),
    Look(
        "biker",
        "Мотоциклистка",
        "⛓️",
        FEMALE,
        price=LOOK_PRICE,
        note="Цепь на поясе не для красоты",
    ),
)

BY_CODE: dict[str, Look] = {look.code: look for look in LOOKS}
DEFAULT_LOOK = LOOKS[0].code


def get_look(code: str) -> Look | None:
    return BY_CODE.get(code)


def free_looks() -> tuple[Look, ...]:
    return tuple(look for look in LOOKS if not look.paid)


def paid_looks() -> tuple[Look, ...]:
    return tuple(look for look in LOOKS if look.paid)


def looks_of_gender(gender: str) -> tuple[Look, ...]:
    return tuple(look for look in LOOKS if look.gender == gender)


__all__ = [
    "BY_CODE",
    "DEFAULT_LOOK",
    "FEMALE",
    "GENDER_TITLES",
    "LOOKS",
    "LOOK_PRICE",
    "MALE",
    "Look",
    "free_looks",
    "get_look",
    "looks_of_gender",
    "paid_looks",
]
