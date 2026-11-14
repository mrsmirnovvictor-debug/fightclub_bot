"""Учёба: корпус, колледж, академия и университет — и диплом в конце.

Второй документ города, который не покупают, а получают. Права сдают по
билету за один заход; диплом так не берут: за него надо ходить. Деньги
платят один раз, при записи, а дальше неделю или две приходить в
назначенный дом, в назначенные часы, и отстаивать занятие.

**Занятие — два часа, одно в сутки и только по будням.** Как смена:
пришёл, встал, два часа никуда не уходишь. Выходные учебными днями не
считаются — город учится по будням, и неделя курса это пять занятий, а
две недели — десять.

**Записываются в одном доме, учатся иногда в другом.** У кадетов так и
есть: записывают в корпусе, а занятия идут на крытом полигоне. Поэтому
`house` и `place` — разные поля, и у пяти курсов из семи они просто
совпадают.

**Экзамен — десять вопросов, восемь верных.** Восемьдесят процентов от
десяти делятся нацело, и спорить об округлении не о чем. Не сдал —
пересдача через неделю: не назавтра, иначе экзамен сдавался бы перебором,
и не «никогда», иначе тысяча кредитов уходила бы в никуда.

**Курс не сгорает.** Срока у него нет: заплативший за учёбу не теряет её
оттого, что неделю не заходил в город. «Неделя» в описании курса — это
сколько он идёт, если ходить каждый будний день, а не часы до списания.

**Диплом — бланк на всю жизнь.** Он ложится в документы и остаётся там:
отобрать его нельзя, продлевать не нужно. Учиться можно сколько угодно
раз подряд — но по одному курсу за раз, как и работа одна.

Правила здесь, вопросы — в `bot.content.exams`, действия — в
`bot.study_service`, экран — в `bot.webapp.study`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.game.clock import MOSCOW

# ---------- занятие ----------

LESSON_HOURS = 2
HOUR = 60 * 60
LESSON_SECONDS = LESSON_HOURS * HOUR
DAY_SECONDS = 24 * HOUR

# Будни: понедельник — ноль, пятница — четыре. Суббота и воскресенье в
# учебную неделю не входят
WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)

# Сколько занятий в учебной неделе. Пять будней — пять занятий
LESSONS_PER_WEEK = 5

# ---------- экзамен ----------

EXAM_SIZE = 10
PASS_SHARE = 0.8
# Сколько верных нужно. Считаем здесь, а не на глаз: восемь это восемь,
# но правило должно держаться и на другом числе вопросов
PASS_MARK = int(EXAM_SIZE * PASS_SHARE)

# Через сколько пускают на пересдачу
RETRY_DAYS = 7
RETRY_SECONDS = RETRY_DAYS * DAY_SECONDS


@dataclass(frozen=True)
class Course:
    """Курс: где записывают, куда ходить, почём и что дают в конце."""

    code: str
    title: str
    emoji: str
    price: int
    weeks: int
    house: str  # где записывают
    place: str  # где идут занятия
    open_hour: int  # с какого часа по Москве пускают на занятие
    close_hour: int  # и до какого
    diploma: str  # код диплома
    diploma_title: str
    quiz: str  # тема экзамена в `bot.content.exams`
    subject: str  # о чём экзамен — строкой, для экрана
    note: str = ""

    @property
    def lessons(self) -> int:
        """Сколько занятий надо отстоять. Пять на неделю."""
        return self.weeks * LESSONS_PER_WEEK

    @property
    def hours(self) -> int:
        return self.lessons * LESSON_HOURS

    @property
    def place_title(self) -> str:
        """Как называется дом, где идут занятия."""
        from bot.game.locations import get_location

        place = get_location(self.place)
        return place.title if place else self.place

    @property
    def window(self) -> str:
        """«с 10:00 до 18:00 мск» — одна строка на все экраны."""
        return f"с {self.open_hour}:00 до {self.close_hour}:00 мск"

    @property
    def weeks_text(self) -> str:
        return "неделя" if self.weeks == 1 else f"{self.weeks} недели"

    def open_now(self, moment: int) -> bool:
        """Идут ли занятия прямо сейчас: будний день и час в окне."""
        here = datetime.fromtimestamp(moment, MOSCOW)
        if here.weekday() not in WEEKDAYS:
            return False
        return self.open_hour <= here.hour < self.close_hour

    def fits_today(self, moment: int) -> bool:
        """Влезает ли занятие целиком до конца окна.

        Два часа должны уместиться в приёмные часы: занятие, начатое за
        полчаса до закрытия, кончалось бы в запертом доме.
        """
        here = datetime.fromtimestamp(moment, MOSCOW)
        return here.hour + LESSON_HOURS <= self.close_hour


COURSES: tuple[Course, ...] = (
    Course(
        "cadet",
        "Кадетский корпус",
        "🎖",
        price=1_000,
        weeks=1,
        house="cadet_corps",
        # Записывают в корпусе, а строят на полигоне: в корпусе учат за
        # партой, а всё остальное — на плацу
        place="indoor_training_ground",
        open_hour=10,
        close_hour=18,
        diploma="cadet",
        diploma_title="Диплом кадетского училища",
        quiz="cadet",
        subject="военное дело",
        note="Строй, устав и всё, что положено знать до первой формы.",
    ),
    Course(
        "medic",
        "Медицинский колледж",
        "⚕️",
        price=1_000,
        weeks=1,
        house="medical_college",
        place="medical_college",
        open_hour=8,
        close_hour=16,
        diploma="medic",
        diploma_title="Диплом медицинского колледжа",
        quiz="medic",
        subject="медицину",
        note="Как остановить кровь до того, как приедут те, кто умеет.",
    ),
    Course(
        "police",
        "Полицейская академия",
        "👮",
        price=1_000,
        weeks=1,
        house="police_school",
        place="police_school",
        open_hour=10,
        close_hour=18,
        diploma="police",
        diploma_title="Диплом полицейской академии",
        quiz="police",
        subject="охрану порядка",
        note="Закон, протокол и разница между задержанием и дракой.",
    ),
    Course(
        "banking",
        "Банковское дело",
        "🏦",
        price=2_000,
        weeks=2,
        house="university",
        place="university",
        open_hour=8,
        close_hour=16,
        diploma="banking",
        diploma_title="Диплом об экономическом образовании",
        quiz="banking",
        subject="экономику",
        note="Проценты, вклады и почему деньги в кармане дешевеют.",
    ),
    Course(
        "it",
        "Информационные технологии",
        "💻",
        price=2_000,
        weeks=2,
        house="university",
        place="university",
        open_hour=8,
        close_hour=16,
        diploma="it",
        diploma_title="Диплом об IT-специальности",
        quiz="it",
        subject="информационные технологии",
        note="Сети, данные и чем пароль отличается от секрета.",
    ),
    Course(
        "management",
        "Менеджмент",
        "📈",
        price=1_800,
        weeks=2,
        house="university",
        place="university",
        open_hour=8,
        close_hour=16,
        diploma="management",
        diploma_title="Диплом менеджера",
        quiz="management",
        subject="менеджмент",
        note="Как спросить с человека так, чтобы он пришёл и завтра.",
    ),
    Course(
        "law",
        "Юриспруденция",
        "⚖️",
        price=1_800,
        weeks=2,
        house="university",
        place="university",
        open_hour=8,
        close_hour=16,
        diploma="law",
        diploma_title="Диплом юриста",
        quiz="law",
        subject="право",
        note="Иск, срок и чем приговор отличается от решения.",
    ),
    Course(
        "gov",
        "Государственное управление",
        "🏛",
        price=2_500,
        weeks=2,
        house="university",
        place="university",
        open_hour=8,
        close_hour=16,
        diploma="gov",
        diploma_title="Диплом государственного управленца",
        quiz="gov",
        subject="государственное управление",
        note="Бюджет, регламент и почему бумага решает больше людей.",
    ),
)

BY_CODE: dict[str, Course] = {one.code: one for one in COURSES}

# Дома, где записывают, и дома, где учатся. Второе шире первого ровно на
# полигон: кадеты записываются в корпусе, а ходят туда
STUDY_HOUSES: frozenset[str] = frozenset(one.house for one in COURSES)
LESSON_HOUSES: frozenset[str] = frozenset(one.place for one in COURSES)


def get_course(code: str) -> Course | None:
    return BY_CODE.get(code)


def courses_at(house: str) -> tuple[Course, ...]:
    """Какие курсы записывают в этом доме. У университета их четыре."""
    return tuple(one for one in COURSES if one.house == house)


def diploma_of(code: str) -> Course | None:
    """Курс, который выдаёт такой диплом."""
    return next((one for one in COURSES if one.diploma == code), None)


def lesson_day(moment: int) -> str:
    """Какие это учебные сутки — «2026-10-13» по Москве.

    Занятие одно в сутки, и считать их проще датой, чем часами: сутки
    меняются в полночь, и никаких понедельничных исключений, как у
    работы, у учёбы нет — жалованья она не платит.
    """
    return datetime.fromtimestamp(moment, MOSCOW).date().isoformat()


def is_weekday(moment: int) -> bool:
    return datetime.fromtimestamp(moment, MOSCOW).weekday() in WEEKDAYS


def retry_at(exam_at: int) -> int:
    """Когда пустят на пересдачу после попытки в этот час."""
    return exam_at + RETRY_SECONDS


__all__ = [
    "COURSES",
    "DAY_SECONDS",
    "EXAM_SIZE",
    "LESSONS_PER_WEEK",
    "LESSON_HOURS",
    "LESSON_HOUSES",
    "LESSON_SECONDS",
    "PASS_MARK",
    "PASS_SHARE",
    "RETRY_DAYS",
    "RETRY_SECONDS",
    "STUDY_HOUSES",
    "WEEKDAYS",
    "Course",
    "courses_at",
    "diploma_of",
    "get_course",
    "is_weekday",
    "lesson_day",
    "retry_at",
]
