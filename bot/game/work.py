"""Работа: пять вакансий города, смена в два часа и жалованье по понедельникам.

Первый в клубе источник дохода, который не зависит ни от боя, ни от
уровня. Кредиты до этого приносил только рост бойца — девяносто за
уровень, — и заканчивался этот источник вместе с ростом. Работа платит за
время и за постоянство: пришёл на неделе — получил в понедельник.

**Вакансия одна на город, а не на бойца.** Бармен в клубе один: взяли —
вакансия пропала из агентства, уволили — вернулась. Это главное свойство
всей затеи, и из него же берётся её главный изъян: рабочих мест в городе
пять, а бойцов может быть сколько угодно.

**Работа одна на бойца.** Вторую не берут, пока не ушли с первой.

**Смена — два часа, и больше двух часов в сутки не работают.** Пока смена
идёт, боец заперт в своём доме: он на работе, а не гуляет по городу.
Сутки московские, как и всё в клубе.

**Неделя считается от понедельника, девяти утра.** В этот час и платят:
норма выполнена — всё жалованье, меньше нормы — доля от неё, округлённая
вверх. Округление вверх, а не вниз, потому что округление вниз означало
бы, что последние минуты смены бойцу не оплатили.

**Меньше половины нормы — увольняют, но платят.** Платят за то, что
отработано: клуб не обкрадывает уходящего. А увольняют потому, что место
одно на город, и держать его за тем, кто не ходит, значит держать его
пустым.

Часов, которые ходили бы по базе в понедельник в девять, здесь нет — как
нет их у страховой и у зала. Неделя сводится в тот миг, когда за работой
пришли: на экране работы, в агентстве и в карточке.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil

from bot.game.art import MAGIC
from bot.game.clock import MOSCOW

# ---------- смена ----------

SHIFT_HOURS = 2
HOUR = 60 * 60
SHIFT_SECONDS = SHIFT_HOURS * HOUR
# Сколько часов в сутки можно отработать. Ровно одна смена: две смены
# подряд означали бы, что недельная норма закрывается за пять дней сидения
# в одном доме, а работа — это про приходить, а не про не уходить
DAY_HOURS = 2

# ---------- неделя и день зарплаты ----------

PAYDAY_WEEKDAY = 0  # понедельник
PAYDAY_HOUR = 9  # девять утра по Москве
WEEK_SECONDS = 7 * 24 * HOUR

# Ниже этой доли нормы — увольнение
KEEP_SHARE = 0.5

# Сколько дней нельзя подавать заявку после отказа или увольнения
COOLDOWN_DAYS = 7
COOLDOWN_SECONDS = COOLDOWN_DAYS * 24 * HOUR

# ---------- тест ----------

# Доля верных ответов, с которой берут на работу
PASS_SHARE = 0.8

NO_EDUCATION = "не требуется"


@dataclass(frozen=True)
class Vacancy:
    """Место работы: где, почём и сколько часов в неделю."""

    code: str
    title: str
    emoji: str
    salary: int  # кредиты за неделю при выполненной норме
    hours: int  # недельная норма в часах
    place: str  # код локации, где работают
    education: str = NO_EDUCATION
    note: str = ""

    @property
    def shifts(self) -> int:
        """Сколько смен в неделю — это и есть норма, только в сменах."""
        return ceil(self.hours / SHIFT_HOURS)

    @property
    def per_hour(self) -> float:
        """Во сколько обходится час: по нему и сравнивают вакансии."""
        return round(self.salary / self.hours, 1)

    def keep_hours(self) -> int:
        """Сколько часов надо отработать, чтобы не уволили."""
        return ceil(self.hours * KEEP_SHARE)

    def payout(self, hours: float) -> int:
        """Жалованье за столько часов.

        Норма выполнена — всё целиком, и переработка сверх нормы ничего
        не прибавляет: недельная ставка на то и недельная. Меньше нормы —
        доля, округлённая вверх.
        """
        if hours <= 0:
            return 0
        if hours >= self.hours:
            return self.salary
        return min(self.salary, ceil(self.salary * hours / self.hours))

    def fires(self, hours: float) -> bool:
        """Увольняют ли за такую неделю."""
        return hours < self.hours * KEEP_SHARE


VACANCIES: tuple[Vacancy, ...] = (
    Vacancy(
        "bartender", "Бармен", "🍸", salary=250, hours=10, place="bar",
        note="Наливать, слушать и не запоминать.",
    ),
    Vacancy(
        "postman", "Сотрудник почты", "📮", salary=200, hours=8,
        place="post_office",
        note="Разбирать, что принесли, и выдавать, за чем пришли.",
    ),
    Vacancy(
        "trainer", "Персональный тренер", "🏋️", salary=300, hours=10,
        place="strength_gym",
        note="Считать подходы вслух и не давать бросить.",
    ),
    Vacancy(
        "fight_host", "Распорядитель боёв", "🥊", salary=300, hours=10,
        place="fight_club",
        note="Сводить пары, держать счёт и разнимать.",
    ),
    Vacancy(
        "croupier", "Крупье", "🎲", salary=300, hours=10, place="casino",
        note="Раздавать, считать и не смотреть в глаза.",
    ),
)

WORK_IMAGE = f"{MAGIC}/work_contract.jpeg"


def get_vacancy(code: str) -> Vacancy | None:
    return next((one for one in VACANCIES if one.code == code), None)


def vacancy_at(place: str) -> Vacancy | None:
    """Какая работа есть в этом доме. У дома она одна."""
    return next((one for one in VACANCIES if one.place == place), None)


WORK_PLACES: frozenset[str] = frozenset(one.place for one in VACANCIES)


# ---------- часы ----------


def hours_of(minutes: int) -> float:
    """Минуты в часы. Смена ровная, но доля нужна при подсчёте доли нормы."""
    return round(minutes / 60, 2)


def day_is_full(minutes: int) -> bool:
    """Отработаны ли сегодняшние два часа."""
    return minutes >= DAY_HOURS * 60


def shift_fits(minutes_today: int) -> bool:
    """Влезает ли ещё одна смена в сегодняшний день."""
    return minutes_today + SHIFT_HOURS * 60 <= DAY_HOURS * 60


# ---------- понедельник, девять утра ----------


def payday_before(moment: int) -> int:
    """Ближайший прошедший понедельник, девять утра по Москве.

    Это начало недели, за которую сейчас копятся часы. По нему и видно,
    чью неделю пора закрывать: если записанное начало старше этого — та
    неделя кончилась, и за неё пора платить.
    """
    here = datetime.fromtimestamp(moment, MOSCOW)
    monday = here - timedelta(days=(here.weekday() - PAYDAY_WEEKDAY) % 7)
    payday = monday.replace(
        hour=PAYDAY_HOUR, minute=0, second=0, microsecond=0
    )
    if payday > here:
        # Понедельник, но ещё до девяти: неделя идёт прошлая
        payday -= timedelta(days=7)
    return int(payday.timestamp())


def payday_after(moment: int) -> int:
    """Следующий понедельник, девять утра: когда придут деньги."""
    return payday_before(moment) + WEEK_SECONDS


def week_of(moment: int) -> int:
    """Начало недели, в которую попадает этот миг."""
    return payday_before(moment)


def week_is_over(week_start: int, now: int) -> bool:
    """Кончилась ли та неделя, что началась в этот час."""
    return week_start < payday_before(now)


def moscow_day(moment: int) -> date:
    """Какие сегодня сутки по часам клуба."""
    return datetime.fromtimestamp(moment, MOSCOW).date()


__all__ = [
    "COOLDOWN_DAYS",
    "COOLDOWN_SECONDS",
    "DAY_HOURS",
    "KEEP_SHARE",
    "NO_EDUCATION",
    "PASS_SHARE",
    "PAYDAY_HOUR",
    "SHIFT_HOURS",
    "SHIFT_SECONDS",
    "VACANCIES",
    "WEEK_SECONDS",
    "WORK_IMAGE",
    "WORK_PLACES",
    "Vacancy",
    "day_is_full",
    "get_vacancy",
    "hours_of",
    "moscow_day",
    "payday_after",
    "payday_before",
    "shift_fits",
    "vacancy_at",
    "week_is_over",
    "week_of",
]
