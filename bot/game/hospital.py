"""Больница: здоровье за кредиты.

Само по себе здоровье затягивается само — десять минут с нуля до
полного, и это бесплатно. Больница продаёт не здоровье, а время: тому,
кого только что избили, до ринга ещё восемь минут, и он либо ждёт, либо
платит.

Поэтому цен две, и дешёвая не хуже дорогой. Перевязка доливает сотню и
стоит вдвое меньше — её берут, когда до порога боя не хватает чуть-чуть.
Полное выздоровление стоит полста и не считает, сколько именно долило, —
его берут, когда избили всерьёз.

Прайс держится здесь, а не в ручке сервера: цена — правило игры, и
менять её приходится вместе с остальной экономикой.
"""

from __future__ import annotations

from dataclasses import dataclass

# Полное выздоровление: сколько бы ни не хватало
FULL_PRICE = 50
# Перевязка: ровно столько здоровья и столько кредитов
PATCH_PRICE = 25
PATCH_HEAL = 100


@dataclass(frozen=True)
class Cure:
    """Одна строка прайса больницы."""

    code: str
    title: str
    price: int
    # Сколько здоровья доливает. Ноль — сколько угодно, до потолка
    heal: int
    note: str

    def healed(self, current: int, max_hp: int) -> int:
        """Сколько здоровья реально дольют этому бойцу."""
        missing = max(0, max_hp - current)
        return missing if self.heal == 0 else min(self.heal, missing)


CURES: tuple[Cure, ...] = (
    Cure(
        "full",
        "Полное выздоровление",
        FULL_PRICE,
        0,
        "Капельница, швы и час под лампой. Встаёшь как новый.",
    ),
    Cure(
        "patch",
        "Перевязка",
        PATCH_PRICE,
        PATCH_HEAL,
        f"Быстро и без разговоров: {PATCH_HEAL} единиц здоровья.",
    ),
)

BY_CODE: dict[str, Cure] = {cure.code: cure for cure in CURES}


def get_cure(code: str | None) -> Cure | None:
    return BY_CODE.get(code or "")


__all__ = [
    "BY_CODE",
    "CURES",
    "Cure",
    "FULL_PRICE",
    "PATCH_HEAL",
    "PATCH_PRICE",
    "get_cure",
]
