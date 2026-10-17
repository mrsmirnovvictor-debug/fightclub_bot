"""Нанятые: наёмники семьи и патрульные города.

Точку мало взять — её надо держать, а держать её некем: живые бойцы
ходят по городу, а не стоят у дверей казино. Отсюда нанятые — люди без
карточки, которых ставят на точку за деньги и которые на ней дерутся,
пока их не вынесут.

**Нанятый принадлежит бойцу, а не семье.** Платит за него один человек
из своего кармана, и в его же счёт он идёт: больше трёх на руках не
держат, у старших — четыре и пять. Так семья растёт не кошельком одного
дона, а тем, сколько в ней людей.

**На точку от человека встаёт один.** Любой может прийти и усилить
охрану, но своего второго туда уже не поставит: десять мест на точке —
это десять разных людей, а не один богач.

**Нанятый погибает насовсем.** Отбили точку — его больше нет, и место в
счёте нанимателя освобождается. Поэтому охрана точки — не покупка, а
расход: её приходится ставить заново после каждого проигранного боя.

**Городская охрана — не нанятые.** Она ничья, стоит на любой точке,
которой не владеет семья, и в счёт никому не идёт. Уровень у неё
четвёртый и класс танковый: такую охрану проходят, но не походя.

Цены и ступени — здесь, действия — в `bot.hire_service`, бой — в
`bot.turf_war`, экраны — в `bot.webapp.turf`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Две стороны найма. Семья нанимает наёмников, город — патрульных;
# правила у них одни, и разводить два модуля было бы переписыванием
MERC = "merc"
PATROL = "patrol"

SIDE_TITLES: dict[str, str] = {MERC: "наёмники", PATROL: "патрульные"}


@dataclass(frozen=True)
class Grade:
    """Ступень найма: кого берут, какого уровня, почём и с каким лицом."""

    code: str
    title: str
    level: int
    fclass: str
    price: int
    # Имя файла лица в `avatars/npc`. Пишется руками, а не считается из
    # кода: в бакете наёмник зовётся мафией, а трикстер — трикстером, и
    # переименовывать чужую папку ради красивой формулы не наше дело
    art: str = ""

    @property
    def side(self) -> str:
        return MERC if self.code.startswith(MERC) else PATROL

    @property
    def picture(self) -> str:
        from bot.game.art import npc_avatar

        return npc_avatar(self.art) if self.art else ""


# Три ступени у каждой стороны. Классы расставлены так, чтобы средняя
# ступень не была просто «той же, но дороже»: у семьи это трикстер,
# ассасин и танк, у города — танк, трикстер и танк
MERC_GRADES: tuple[Grade, ...] = (
    Grade("merc_5", "Наёмник", 5, "rogue", 300, "mafia_5_trickster"),
    Grade("merc_7", "Головорез", 7, "assassin", 600, "mafia_7_assassin"),
    Grade("merc_9", "Громила", 9, "tank", 1_200, "mafia_9_tank"),
)

PATROL_GRADES: tuple[Grade, ...] = (
    Grade("patrol_5", "Патрульный", 5, "tank", 300, "police_5_tank"),
    Grade("patrol_7", "Сержант", 7, "rogue", 600, "police_7_trickster"),
    Grade("patrol_9", "Боец спецназа", 9, "tank", 1_200, "police_9_tank"),
)

GRADES: dict[str, Grade] = {
    one.code: one for one in MERC_GRADES + PATROL_GRADES
}

# Сколько нанятых держит на руках рядовой, старший и самый старший.
# У семьи это мафиози, приближённый и дон, у города — патрульный, офицер
# и начальник полиции
PLAIN_CAP = 3
SENIOR_CAP = 4
HEAD_CAP = 5

# Сколько нанятых стоит на одной точке и сколько их от одного человека
MAX_POSTED = 10
PER_PERSON = 1

# Городская охрана: трое четвёртого уровня, класс танковый. Ничья и
# бесплатная, в счёт никому не идёт
GUARD_LEVEL = 4
GUARD_CLASS = "tank"
GUARD_SIZE = 3
GUARD_ART = "guard_4_tank"


def guard_picture() -> str:
    """Лицо городской охраны. Одно на всех троих: это не люди, а пост."""
    from bot.game.art import npc_avatar

    return npc_avatar(GUARD_ART)


def grades_for(side: str) -> tuple[Grade, ...]:
    return MERC_GRADES if side == MERC else PATROL_GRADES


def get_grade(code: str) -> Grade | None:
    return GRADES.get(code)


def cap_for(rank: str) -> int:
    """Сколько нанятых держит боец этого чина.

    Чины двух лестниц считаются одной меркой нарочно: приближённый в
    семье и офицер в городе — одна и та же ступень, и давать им разное
    число людей значило бы спорить о том, кто из них главнее.
    """
    if rank in ("don", "police_chief"):
        return HEAD_CAP
    if rank in ("capo", "police_officer"):
        return SENIOR_CAP
    return PLAIN_CAP


def hired_left(cap: int, hired: int) -> int:
    return max(0, cap - hired)


__all__ = [
    "GRADES",
    "GUARD_ART",
    "GUARD_CLASS",
    "GUARD_LEVEL",
    "GUARD_SIZE",
    "HEAD_CAP",
    "MAX_POSTED",
    "MERC",
    "MERC_GRADES",
    "PATROL",
    "PATROL_GRADES",
    "PER_PERSON",
    "PLAIN_CAP",
    "SENIOR_CAP",
    "SIDE_TITLES",
    "Grade",
    "cap_for",
    "get_grade",
    "guard_picture",
    "grades_for",
    "hired_left",
]
