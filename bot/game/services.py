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

**Пять служб со светлой стороной и две без.** Полиция, армия, ФБР,
медицина и суд — светлая сторона: туда мафиози не берут. Клятву врача и
мантию судьи город ставит рядом со значком и погоном: лечить и судить
того, с кем ты в одной семье, нельзя.

Администрация и мэрия работают со всеми: секретарь выдаёт бумагу кому
угодно, и мэра выбирает весь город, а не половина его.

**Право силы — не то же самое, что светлая сторона.** На точки города
выходят только полиция, ФБР и армия (`force`): у них на это есть и
оружие, и приказ. Врач и судья в семью не вступают, но и за квартал не
дерутся — городская война мимо них.

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
    # Право силы: выходит ли служба на точки города и дерётся ли за них
    # наравне с семьями. Светлая сторона и право силы — разные вещи:
    # врач и судья в клане не состоят, но за квартал не воюют, и
    # городская война их не касается
    force: bool = False
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
        force=True,
        note="Порядок на улицах и протокол на всё остальное.",
    ),
    Service(
        "fbi",
        "ФБР",
        "🕵️",
        house="fbi_office",
        lawful=True,
        force=True,
        note="Смотрит на город сверху и не отчитывается перед мэром.",
    ),
    Service(
        "army",
        "Армия",
        "🎖",
        house="military_base",
        lawful=True,
        force=True,
        note="Сила, которую город держит на случай, когда поздно говорить.",
    ),
    Service(
        "medical",
        "Медицинская служба",
        "⚕️",
        house="hospital",
        lawful=True,
        note="Лечит всех подряд, но сама ни в одной семье не состоит.",
    ),
    Service(
        "court",
        "Суд Вегаса",
        "⚖️",
        house="courthouse",
        lawful=True,
        note="Последнее слово в городе, где все говорят разом.",
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

# Светлая сторона: эти не берут тех, кто состоит в клане
LAWFUL: frozenset[str] = frozenset(
    one.code for one in SERVICES if one.lawful
)

# Право силы: эти выходят на точки города
FORCE: frozenset[str] = frozenset(one.code for one in SERVICES if one.force)


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
    "FORCE",
    "LAWFUL",
    "SERVICES",
    "Service",
    "get_service",
    "service_at",
    "service_of_job",
]
