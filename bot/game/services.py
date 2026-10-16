"""Городские службы: кто носит значок и кто за какой дом отвечает.

Служба — это не отдельная сущность рядом с работой, а **взгляд на неё
сверху**. Боец устраивается патрульным через то же агентство, что и
барменом; разница в том, что патрульный после этого носит значок, а
бармен нет.

Отсюда и устройство: у службы нет своего состава и своих записей.
Состав считается по рабочим местам — кто занял место полиции, тот и
полицейский, ушёл с него — и значка нет. Хранить это отдельно значило бы
завести вторую правду о том же самом и ждать, пока они разойдутся.

**Служба и семья несовместимы.** Это правило живёт не здесь, а там, где
проверяется: агентство не берёт мафиози на место с пометкой `lawful`
(`bot.game.work`), особняк не пускает служивого в клан
(`bot.clan_service`). Здесь только список самих служб.

**Три службы с правом силы и три без.** Полиция, армия и ФБР — светлая
сторона: туда мафиози не берут. Медицина, администрация и мэрия работают
со всеми: врач лечит и мафиози, секретарь выдаёт бумагу кому угодно.

ФБР и мэрия пока без своих рабочих мест: значки нарисованы, дома за ними
ещё не открыты. Пустая служба — это не поломка, а очередь: она стоит в
списке и ждёт своих вакансий.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.game import art


@dataclass(frozen=True)
class Service:
    """Городская служба: значок, дом и те места, что в неё входят."""

    code: str
    title: str
    emoji: str
    house: str  # где она сидит
    # Светлая сторона: в такую службу мафиози не берут
    lawful: bool = False
    note: str = ""

    @property
    def badge(self) -> str:
        """Значок службы — тот, что встаёт у ника."""
        return art.service_badge(self.code)

    @property
    def jobs(self) -> tuple[str, ...]:
        """Места, которые в неё входят. Считается по дому, а не списком.

        Список пришлось бы править каждый раз, когда в участке заводят
        ещё одно кресло, — и однажды его забыли бы поправить.
        """
        from bot.game.work import VACANCIES

        return tuple(one.code for one in VACANCIES if one.place == self.house)


SERVICES: tuple[Service, ...] = (
    Service(
        "police",
        "Полиция Вегаса",
        "👮",
        house="vcpd",
        lawful=True,
        note="Порядок на улицах и протокол на всё остальное.",
    ),
    Service(
        "fbi",
        "ФБР",
        "🕵️",
        house="fbi_office",
        lawful=True,
        note="Смотрит на город сверху и не отчитывается перед мэром.",
    ),
    Service(
        "army",
        "Армия",
        "🎖",
        house="military_base",
        lawful=True,
        note="Сила, которую город держит на случай, когда поздно говорить.",
    ),
    Service(
        "medical",
        "Медицинская служба",
        "⚕️",
        house="hospital",
        note="Лечит всех подряд и не спрашивает, чей ты.",
    ),
    Service(
        "administration",
        "Администрация города",
        "🏛",
        house="city_administration",
        note="Бумага, печать и очередь к окну.",
    ),
    Service(
        "mayor",
        "Мэр города",
        "🎩",
        house="city_hall",
        note="Один на весь Вегас, и отвечает за него тоже один.",
    ),
)

BY_CODE: dict[str, Service] = {one.code: one for one in SERVICES}

# Светлая сторона: эти три не берут тех, кто состоит в клане
LAWFUL: frozenset[str] = frozenset(
    one.code for one in SERVICES if one.lawful
)


def get_service(code: str) -> Service | None:
    return BY_CODE.get(code)


def service_at(house: str) -> Service | None:
    """Какая служба сидит в этом доме. Ни одной — дом не её."""
    return next((one for one in SERVICES if one.house == house), None)


def service_of_job(code: str) -> Service | None:
    """В какой службе состоит тот, кто занял это место.

    Пусто — место не служебное: бармен, крупье, менеджер по продажам.
    """
    from bot.game.work import get_vacancy

    job = get_vacancy(code) if code else None
    return service_at(job.place) if job is not None else None


__all__ = [
    "BY_CODE",
    "LAWFUL",
    "SERVICES",
    "Service",
    "get_service",
    "service_at",
    "service_of_job",
]
