"""Касса клуба: пакеты кредитов за Telegram Stars.

Цены привязаны к экономике игры, а не взяты с потолка. Уровень сам по себе
приносит 90 кредитов, комплект своего уровня стоит 600–1000 — значит пачка
в 100 кредитов должна ощущаться как «докинуть на одну вещь», а не как
«купить победу». Чем больше пачка, тем дешевле кредит: покупать оптом
выгоднее, но и потолок у вещей всё равно уровневый.

Деньги не поднимают ни уровень, ни характеристики: за кредиты берут только
то, что и так продаётся в лавке. Это осознанное ограничение — рейтинговый
клуб, где силу можно докупить, перестаёт быть клубом.
"""

from __future__ import annotations

from dataclasses import dataclass

# Валюта Telegram Stars в Bot API
STARS = "XTR"


@dataclass(frozen=True)
class CreditPack:
    """Пачка кредитов: сколько стоит в звёздах и сколько даёт."""

    code: str
    title: str
    emoji: str
    credits: int
    stars: int
    bonus: int = 0  # сколько кредитов идёт сверху пачки
    note: str = ""

    @property
    def total(self) -> int:
        """Сколько кредитов боец получит на руки."""
        return self.credits + self.bonus

    @property
    def stars_per_hundred(self) -> float:
        """Во сколько звёзд обходится сотня кредитов — для сравнения пачек."""
        return self.stars * 100 / self.total

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"

    def describe(self) -> str:
        """Строка для кнопки и для списка в чате."""
        amount = f"{self.total} кр."
        if self.bonus:
            amount += f" ({self.credits} + {self.bonus} сверху)"
        return f"{self.label} — {amount} за {self.stars} ⭐"


PACKS: tuple[CreditPack, ...] = (
    CreditPack(
        "handful",
        "Горсть мелочи",
        "🪙",
        credits=100,
        stars=50,
        note="Хватит докинуть на вещь, которой не хватало",
    ),
    CreditPack(
        "roll",
        "Пачка купюр",
        "💵",
        credits=500,
        stars=200,
        bonus=50,
        note="Половина комплекта своего уровня",
    ),
    CreditPack(
        "case",
        "Кейс",
        "💼",
        credits=1000,
        stars=350,
        bonus=200,
        note="Полный комплект на свой уровень",
    ),
    CreditPack(
        "safe",
        "Сейф",
        "🏦",
        credits=2500,
        stars=800,
        bonus=700,
        note="Одеться и не думать о кредитах до десятого",
    ),
)

BY_CODE: dict[str, CreditPack] = {pack.code: pack for pack in PACKS}


def get_pack(code: str) -> CreditPack | None:
    return BY_CODE.get(code)


# Что можно купить за звёзды: пачка кредитов, вещь из лавки мага или подписка
PACK_KIND = "pack"
RELIC_KIND = "relic"
PRO_KIND = "pro"


def parse_payload(payload: str) -> tuple[str, str]:
    """«pack:case:12345» → («pack», «case»). Чужое — («», «»)."""
    parts = (payload or "").split(":")
    if len(parts) < 2 or parts[0] not in (PACK_KIND, RELIC_KIND, PRO_KIND):
        return "", ""
    return parts[0], parts[1]


def pack_of_payload(payload: str) -> CreditPack | None:
    """Разобрать payload инвойса: «pack:case:12345» — пачка «кейс» для 12345."""
    kind, code = parse_payload(payload)
    return get_pack(code) if kind == PACK_KIND else None


def payload_for(pack: CreditPack, user_id: int) -> str:
    return f"{PACK_KIND}:{pack.code}:{user_id}"


def relic_payload(code: str, user_id: int) -> str:
    return f"{RELIC_KIND}:{code}:{user_id}"


def pro_payload(user_id: int) -> str:
    return f"{PRO_KIND}:month:{user_id}"


__all__ = [
    "BY_CODE",
    "PACKS",
    "PACK_KIND",
    "PRO_KIND",
    "RELIC_KIND",
    "STARS",
    "CreditPack",
    "get_pack",
    "pack_of_payload",
    "parse_payload",
    "payload_for",
    "pro_payload",
    "relic_payload",
]
