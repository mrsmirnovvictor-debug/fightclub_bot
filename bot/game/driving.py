"""Автошкола и права: курс, экзамен по билету и водительское удостоверение.

Первый документ города, который не покупают, а сдают. Карту заводят за
деньги, полис выписывают за деньги, абонемент тоже — права стоят времени
и внимания: за курс платят один раз, а дальше учат правила сами и
приходят на экзамен в назначенный час.

**Курс — 300 кредитов и три полных дня.** Деньги берут за запись, а три
дня после неё — на самостоятельное изучение ПДД. Раньше срока на экзамен
не записывают: курс не «кнопка за деньги», он про подождать и выучить.

**Экзамен по средам и субботам, с 12 до 15.** Прийти надо в автошколу и
именно в эти часы: экзамен принимают не круглосуточно, как и в жизни.
Опоздал — ждёшь следующего дня приёма.

**Пятнадцать минут и шестнадцать вопросов.** Одна ошибка прощается, на
второй экзамен кончается сразу — пересдача. Вопросы идут по одному:
выбрал ответ, подтвердил, следующий. Время кончилось — попытка сгорела.

**Пересдача бесплатна.** Платят за курс, а не за попытку: деньги за
каждый заход превращали бы экзамен в торговлю, а не в проверку.

**Права выдают не здесь.** Сдал — приходи в полицейский участок, там
бланк и выписывают. Он бессрочный и лежит в документах; с ним открыто
то, что без него закрыто, — право водить и работа, для которой права
нужны.

Часов, которые закрывали бы попытку сами, здесь нет, как и во всём
остальном клубе: экзамен сводится в тот миг, когда на него смотрят.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from bot.content.pdd import TICKET
# Окна и расписания завели рейды: там они появились первыми и там же
# отлажены. Машинка общая — экзамену нужна та же: «по таким-то дням с
# такого-то часа», «открыто ли сейчас», «когда следующее окно»
from bot.game.raid import Weekly, Window, next_window, schedule_text, window_of

# ---------- курс ----------

COURSE_PRICE = 300
# Сколько суток после записи идёт самостоятельное изучение ПДД. Полных,
# а не календарных: записался в среду вечером — к субботнему вечеру
# отучился, а не к субботнему утру
STUDY_DAYS = 3
DAY_SECONDS = 24 * 60 * 60
STUDY_SECONDS = STUDY_DAYS * DAY_SECONDS

# ---------- экзамен ----------

# По средам и субботам с 12:00 до 15:00 мск
EXAM_SCHEDULE = Weekly(weekdays=(2, 5), hour=12, hours=3)
EXAM_MINUTES = 15
EXAM_SECONDS = EXAM_MINUTES * 60
# Сколько вопросов в билете и сколько ошибок прощается. Вторая ошибка
# кончает экзамен на месте: доучивай и приходи в следующий раз
TICKET_SIZE = len(TICKET)
MISTAKES_ALLOWED = 1

# ---------- права ----------

LICENCE_CODE = "licence"
LICENCE_TITLE = "Водительское удостоверение"
LICENCE_EMOJI = "🪪"
LICENCE_ISSUER = "ГИБДД Vegas City"
# Где его выдают и где учат — дома разные, и это нарочно: школа учит,
# участок выдаёт бланк
SCHOOL_HOUSE = "driving_school"
POLICE_HOUSE = "vcpd"

LICENCE_GIVES: tuple[str, ...] = (
    "Право управления транспортным средством",
    "Работа, для которой нужны права, — например, в таксопарке",
)
LICENCE_NOTE = (
    "Бланк бессрочный: переоформлять и продлевать его не нужно. "
    "Потерять его тоже нельзя — он лежит в документах."
)


def licence_number(user_id: int, issued: int) -> str:
    """Номер прав: «77 АВ 123456» — серия от бойца, номер от часа выдачи.

    Считается, а не хранится: те же два числа всегда дают тот же номер, а
    лишнее поле в базе — лишний повод ему разойтись с бланком.
    """
    letters = "АВЕКМНОРСТУХ"
    series = letters[user_id % len(letters)] + letters[(user_id // 12) % len(letters)]
    region = 77 + user_id % 9
    return f"{region} {series} {issued % 1_000_000:06d}"


@dataclass(frozen=True)
class School:
    """Что у бойца с автошколой: курс, попытка экзамена и права.

    Одна запись на бойца. Пустая — в школу он не ходил.
    """

    # Когда записался на курс. 0 — не записывался
    course_at: int = 0
    # До какого часа идёт попытка экзамена. 0 — попытки нет
    exam_until: int = 0
    # На каком вопросе билета стоит попытка и сколько в ней ошибок
    step: int = 0
    wrong: int = 0
    # Когда сдал экзамен. 0 — не сдавал
    passed_at: int = 0
    # Когда выданы права. 0 — не выданы
    licence_at: int = 0

    @property
    def enrolled(self) -> bool:
        return self.course_at > 0

    @property
    def passed(self) -> bool:
        return self.passed_at > 0

    @property
    def has_licence(self) -> bool:
        return self.licence_at > 0

    def study_left(self, moment: int) -> int:
        """Сколько секунд ещё учиться. Ноль — курс отучен."""
        if not self.enrolled:
            return STUDY_SECONDS
        return max(0, self.course_at + STUDY_SECONDS - moment)

    def studied(self, moment: int) -> bool:
        """Прошли ли три полных дня с записи."""
        return self.enrolled and self.study_left(moment) == 0

    def sitting(self, moment: int) -> bool:
        """Идёт ли попытка прямо сейчас. Истёкшая не идёт."""
        return self.exam_until > moment

    def time_left(self, moment: int) -> int:
        return max(0, self.exam_until - moment)

    def burnt(self, moment: int) -> bool:
        """Попытка была и сгорела по времени: её пора убрать."""
        return bool(self.exam_until) and self.exam_until <= moment

    def begin(self, moment: int) -> "School":
        """Начать попытку: пятнадцать минут, первый вопрос, ноль ошибок."""
        return replace(
            self, exam_until=moment + EXAM_SECONDS, step=0, wrong=0
        )

    def closed(self) -> "School":
        """Снять попытку с бойца: экзамен кончился — неважно, чем."""
        return replace(self, exam_until=0, step=0, wrong=0)


def exam_window(moment: int) -> Window | None:
    """Идёт ли приём экзамена. None — закрыто, ждите следующего дня."""
    return window_of(moment, EXAM_SCHEDULE)


def next_exam(moment: int) -> Window:
    """Ближайший экзамен после этого часа."""
    return next_window(moment, EXAM_SCHEDULE)


def exam_text(moment: int) -> str:
    """Расписание словами: «по средам и субботам с 12:00 до 15:00 мск»."""
    return schedule_text(moment, EXAM_SCHEDULE)


__all__ = [
    "COURSE_PRICE",
    "DAY_SECONDS",
    "EXAM_MINUTES",
    "EXAM_SCHEDULE",
    "EXAM_SECONDS",
    "LICENCE_CODE",
    "LICENCE_EMOJI",
    "LICENCE_GIVES",
    "LICENCE_ISSUER",
    "LICENCE_NOTE",
    "LICENCE_TITLE",
    "MISTAKES_ALLOWED",
    "POLICE_HOUSE",
    "SCHOOL_HOUSE",
    "STUDY_DAYS",
    "STUDY_SECONDS",
    "TICKET_SIZE",
    "School",
    "exam_text",
    "exam_window",
    "licence_number",
    "next_exam",
]
