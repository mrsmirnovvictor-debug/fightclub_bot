"""Приёмы клуба: шестнадцать штук и четыре ступени обучения.

Здесь только содержимое — названия, числа и кому что достаётся. Правила,
по которым приём ложится на бой, живут в `bot.game.abilities`.

Приёмы сложены в четыре ступени по уровням 1, 3, 6 и 10. На первой боец
получает свой классовый приём молча, дальше на каждой ступени выбирает
один из трёх: свой и два чужих. Чужие — не случайные: у каждого класса
свой набор соседей, и именно он задаёт, каким боец может вырасти.

Лестница у каждой ветки своя и ровная:

* удар: Сильный (+15) → Мощный (+30) → Сокрушительный (+45) → Массовый (+60)
* уворот: Проворность → Хитрость (с ответом) → Коварство (ответ критом) →
  Бог обмана (и Проворность союзникам)
* крит: Критический удар → Пролом (с блоком) → Смертельный (урон вдвое) →
  Призыв к крови (и Критический удар союзникам)
* лечение: Восстановление (10%) → Воля к победе (20%) → Мастер жизни (30%
  себе и 20% союзникам); на шестой ступени у танка вместо лечения
  Парирование — удар, который не доходит вовсе

Цена приёма — у ступени, на которой он выучен, а не у самого приёма:
«Сильный удар» стоит три энергии первым выученным и шесть, если взят
кросс-классом на третьем уровне. Считает это `Loadout.cost_of`.
"""

from __future__ import annotations

from bot.game.abilities import Ability, Effect

# ---------- сами приёмы ----------

ABILITIES: tuple[Ability, ...] = (
    # --- первая ступень: то, с чем класс приходит в клуб ---
    Ability(
        code="strong_hit",
        title="Сильный удар",
        effect=Effect.DAMAGE,
        tier=1,
        icon="👊",
        damage=15,
        note="К следующему точному удару добавляется +15 урона.",
    ),
    Ability(
        code="nimble",
        title="Проворность",
        effect=Effect.DODGE,
        tier=1,
        icon="🌀",
        note="От следующего точного удара боец уворачивается наверняка.",
    ),
    Ability(
        code="crit_hit",
        title="Критический удар",
        effect=Effect.CRIT,
        tier=1,
        icon="💥",
        note="Следующий удар — критический, без проверки.",
    ),
    Ability(
        code="recovery",
        title="Восстановление",
        effect=Effect.HEAL,
        tier=1,
        icon="❤️",
        heal=0.10,
        note="Возвращает 10% здоровья и не тратит ход удара.",
    ),
    # --- вторая ступень ---
    Ability(
        code="power_hit",
        title="Мощный удар",
        effect=Effect.DAMAGE,
        tier=3,
        icon="👊",
        damage=30,
        note="К следующему точному удару добавляется +30 урона.",
    ),
    Ability(
        code="cunning",
        title="Хитрость",
        effect=Effect.COUNTER,
        tier=3,
        icon="🔄",
        counter=True,
        note="Боец уходит от следующего удара и отвечает контрударом.",
    ),
    Ability(
        code="breach",
        title="Пролом",
        effect=Effect.BREAK,
        tier=3,
        icon="🛡🩸",
        note="Следующий удар — критический и проламывает блок.",
    ),
    Ability(
        code="will_to_win",
        title="Воля к победе",
        effect=Effect.HEAL,
        tier=3,
        icon="❤️",
        heal=0.20,
        note="Возвращает 20% здоровья и не тратит ход удара.",
    ),
    # --- третья ступень ---
    Ability(
        code="crushing_hit",
        title="Сокрушительный удар",
        effect=Effect.DAMAGE,
        tier=6,
        icon="👊",
        damage=45,
        note="К следующему точному удару добавляется +45 урона.",
    ),
    Ability(
        code="guile",
        title="Коварство",
        effect=Effect.COUNTER,
        tier=6,
        icon="🔄",
        counter=True,
        crit_counter=True,
        note="Боец уходит от удара и отвечает критическим контрударом.",
    ),
    Ability(
        code="deadly_hit",
        title="Смертельный удар",
        effect=Effect.DOUBLE,
        tier=6,
        icon="💀",
        note="Следующий удар — критический, и урон умножается на два.",
    ),
    Ability(
        code="parry",
        title="Парирование",
        effect=Effect.PARRY,
        tier=6,
        icon="🚫",
        note="Следующий удар не наносит урона — хоть обычный, хоть крит.",
    ),
    # --- четвёртая ступень: приёмы, которые видит весь ринг ---
    Ability(
        code="mass_hit",
        title="Массовый удар",
        effect=Effect.DAMAGE,
        tier=10,
        icon="💢",
        damage=60,
        splash=15,
        note="+60 урона к следующему точному удару, и по 15 всем "
        "остальным противникам.",
    ),
    Ability(
        code="trickster_god",
        title="Бог обмана",
        effect=Effect.COUNTER,
        tier=10,
        icon="🃏",
        counter=True,
        aura="nimble",
        note="Уворот с контрударом, и «Проворность» ложится всем союзникам.",
    ),
    Ability(
        code="blood_call",
        title="Призыв к крови",
        effect=Effect.BREAK,
        tier=10,
        icon="🩸",
        aura="crit_hit",
        note="Критический удар с проломом блока, и «Критический удар» "
        "ложится всем союзникам.",
    ),
    Ability(
        code="life_master",
        title="Мастер жизни",
        effect=Effect.HEAL,
        tier=10,
        icon="💖",
        heal=0.30,
        aura="heal_allies",
        note="Возвращает 30% здоровья себе и 20% каждому союзнику. "
        "Ход удара не тратится.",
    ),
)

CATALOGUE: dict[str, Ability] = {one.code: one for one in ABILITIES}

# ---------- кому что достаётся ----------
#
# На первой ступени выбора нет: класс приходит со своим приёмом. Дальше на
# каждой ступени три варианта — свой первым, за ним два чужих.

STARTER: dict[str, str] = {
    "warrior": "strong_hit",
    "rogue": "nimble",
    "assassin": "crit_hit",
    "tank": "recovery",
}

# Класс → ступень → что предложить. Первый в списке — классовый
CHOICES: dict[str, dict[int, tuple[str, ...]]] = {
    "warrior": {
        3: ("power_hit", "nimble", "crit_hit"),
        6: ("crushing_hit", "cunning", "will_to_win"),
        10: ("mass_hit", "parry", "deadly_hit"),
    },
    "rogue": {
        3: ("cunning", "strong_hit", "recovery"),
        6: ("guile", "power_hit", "will_to_win"),
        10: ("trickster_god", "crushing_hit", "parry"),
    },
    "assassin": {
        3: ("breach", "strong_hit", "nimble"),
        6: ("deadly_hit", "will_to_win", "cunning"),
        10: ("blood_call", "parry", "guile"),
    },
    "tank": {
        # Увороту танка взяться неоткуда, и в этом весь смысл развилки:
        # приём даёт то, чего у класса нет от рождения
        3: ("will_to_win", "strong_hit", "nimble"),
        6: ("parry", "breach", "cunning"),
        10: ("life_master", "guile", "crushing_hit"),
    },
}


def get_ability(code: str) -> Ability | None:
    return CATALOGUE.get(code)


def starter_of(class_code: str) -> Ability:
    """Приём, с которым класс приходит в клуб."""
    return CATALOGUE[STARTER[class_code]]


def choices_at(class_code: str, level: int) -> tuple[Ability, ...]:
    """Из чего боец этого класса выбирает на этой ступени.

    Пусто — на этом уровне выбирать нечего: ступеней всего четыре.
    """
    codes = CHOICES.get(class_code, {}).get(level, ())
    return tuple(CATALOGUE[code] for code in codes)


def abilities_of_tier(tier: int) -> tuple[Ability, ...]:
    return tuple(one for one in ABILITIES if one.tier == tier)


__all__ = [
    "ABILITIES",
    "CATALOGUE",
    "CHOICES",
    "STARTER",
    "abilities_of_tier",
    "choices_at",
    "get_ability",
    "starter_of",
]
