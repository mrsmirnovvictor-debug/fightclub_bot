"""Образы бойца: как он выглядит на карточке.

Образ — это не аватарка из Telegram и не эмодзи, а выбранная внешность
персонажа. На каждый пол три образа открыты всем и ещё девять продаются
за кредиты; купленный остаётся у бойца навсегда — переключайся сколько
угодно.

Гардероб показывает образы своего пола: пол выбирают при создании
персонажа, и он же решает, из чего боец одевается.

Картинка образа лежит в бакете под его же кодом —
`avatars/rookie_transparent.png`, поэтому отдельно её прописывать не
нужно. Поле `image` остаётся на случай, когда для образа понадобится
файл с другим именем.

**Фон у образа прозрачный.** На карточке образ ложится третьим слоем:
под ним фон рамки, под фигурой — машина, если боец за рулём. Был бы фон
непрозрачным, машину он бы и закрыл, а вырезать её по контуру фигуры
страница не умеет. Отсюда и png: прозрачности в jpeg нет.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.game.art import look_avatar

# Сколько стоит платный образ. Двести — цена пары эликсиров: внешность
# это вкус, а не сила, и платить за неё как за комплект брони незачем.
# Раньше стоила тысячу, и за тысячу её почти никто не брал
LOOK_PRICE = 200

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
    # Образ, который раздавали прежде. За кредиты его не купить и с
    # подпиской он больше не приходит: у кого он есть — остался навсегда,
    # у кого нет — не появится. В гардеробе виден только своему хозяину:
    # дразнить кнопкой, которая ничего не делает, незачем.
    pro: bool = False

    @property
    def paid(self) -> bool:
        return self.price > 0

    @property
    def picture(self) -> str:
        """Адрес картинки. Не загрузилась — карточка покажет значок."""
        return self.image or look_avatar(self.code)


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
    # ---------- второй заход: по шестёрке на пол ----------
    #
    # Код образа — имя файла в бакете, и это не лень: файлы пришли
    # названными по порядку выгрузки, а не по смыслу, и выдумывать им
    # вторые имена значило бы держать в голове таблицу переводов.
    Look(
        "male_01_boxer", "Боксёр", "🥊", MALE, price=LOOK_PRICE,
        note="Бинты наматывает сам и молча",
    ),
    Look(
        "male_02_bouncer", "Вышибала", "🚪", MALE, price=LOOK_PRICE,
        note="На входе решает он, а не список",
    ),
    Look(
        "male_03_mechanic", "Механик", "🔩", MALE, price=LOOK_PRICE,
        note="Руки в масле, удар поставлен",
    ),
    Look(
        "male_04_streetfighter", "Уличный боец", "🧱", MALE, price=LOOK_PRICE,
        note="Учился там же, где и дрался",
    ),
    Look(
        "male_05_kickboxer", "Кикбоксёр", "🦵", MALE, price=LOOK_PRICE,
        note="Достанет ногой оттуда, откуда не ждут",
    ),
    Look(
        "male_06_coach", "Тренер", "📋", MALE, price=LOOK_PRICE,
        note="Знает, чем кончится бой, со второго раунда",
    ),
    Look(
        "female_01_boxer", "Боксёрша", "🥊", FEMALE, price=LOOK_PRICE,
        note="Работает в три удара и не любит первый",
    ),
    Look(
        "female_02_rider", "Мотогонщица", "🏍", FEMALE, price=LOOK_PRICE,
        note="Шлем снимает только в подвале",
    ),
    Look(
        "female_03_grappler", "Борец", "🤼", FEMALE, price=LOOK_PRICE,
        note="В партере разговор короткий",
    ),
    Look(
        "female_04_medic", "Подпольный медик", "🩹", FEMALE, price=LOOK_PRICE,
        note="Зашивает после боёв и дерётся между ними",
    ),
    Look(
        "female_05_punk", "Панк-боец", "🎸", FEMALE, price=LOOK_PRICE,
        note="Пришла из клуба, где музыка громче",
    ),
    Look(
        "female_06_tactician", "Тактик", "♟", FEMALE, price=LOOK_PRICE,
        note="Считает чужие ошибки заранее",
    ),
    # ---------- больше не выдаётся ----------
    Look(
        "assassin",
        "Ассасин",
        "🥷",
        MALE,
        pro=True,
        note="Достался с подпиской PRO и остался навсегда",
    ),
)

BY_CODE: dict[str, Look] = {look.code: look for look in LOOKS}
DEFAULT_LOOK = LOOKS[0].code


def get_look(code: str) -> Look | None:
    return BY_CODE.get(code)


def free_looks() -> tuple[Look, ...]:
    """Открытые всем. Старая выдача сюда не входит: она не бесплатная, она своя."""
    return tuple(look for look in LOOKS if not look.paid and not look.pro)


def pro_looks() -> tuple[Look, ...]:
    return tuple(look for look in LOOKS if look.pro)


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
    "pro_looks",
    "get_look",
    "looks_of_gender",
    "paid_looks",
]
