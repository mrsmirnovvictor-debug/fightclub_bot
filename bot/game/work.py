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

**Смена — два часа, и больше двух часов в рабочий день не работают.**
Пока смена идёт, боец заперт в своём доме: он на работе, а не гуляет по
городу. Рабочий день — это московские сутки, кроме понедельника: в
девять утра там закрывается неделя, и день делится надвое. Ночная смена
до девяти идёт в зачёт прошлой неделе, дневная после девяти — новой, и
отработать можно обе.

**Неделя считается от понедельника, девяти утра.** В этот час и платят:
норма выполнена — всё жалованье, меньше нормы — доля от неё, округлённая
вверх. Округление вверх, а не вниз, потому что округление вниз означало
бы, что последние минуты смены бойцу не оплатили.

**Сверх нормы платят вдвойне.** Час переработки стоит две ставки:
ставка часа — это недельное жалованье, делённое на норму, и за каждый
лишний час сверх неё приходит двойная. Тренер с нормой в десять часов и
жалованьем в триста получает тридцать за час; отработал двенадцать —
триста оклада и сто двадцать сверхурочных.

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
# Сколько часов в рабочий день можно отработать. Ровно одна смена: две
# смены подряд означали бы, что недельная норма закрывается за пять дней
# сидения в одном доме, а работа — это про приходить, а не про не уходить
DAY_HOURS = 2

# ---------- неделя и день зарплаты ----------

PAYDAY_WEEKDAY = 0  # понедельник
PAYDAY_HOUR = 9  # девять утра по Москве
WEEK_SECONDS = 7 * 24 * HOUR

# Ниже этой доли нормы — увольнение
KEEP_SHARE = 0.5

# Во сколько раз дороже час сверх нормы. Двести процентов: час
# переработки стоит две обычных ставки, а не одну сверх оклада
OVERTIME_RATE = 2.0

# Сколько дней нельзя подавать заявку после отказа или увольнения
COOLDOWN_DAYS = 7
COOLDOWN_SECONDS = COOLDOWN_DAYS * 24 * HOUR

# ---------- опыт ----------

# Сколько секунд в «месяце опыта». Месяц берём ровный, в тридцать суток:
# опыт считается от часа приёма, а не по календарю, и февраль с августом
# в нём не должны стоить по-разному
MONTH_SECONDS = 30 * 24 * HOUR

# Недельная норма новых мест. Пять смен по два часа — та же рабочая
# неделя, что у тренера и крупье: места различаются дипломом и деньгами,
# а не тем, сколько в них сидеть
STAFF_HOURS = 10

# ---------- тест ----------

# Доля верных ответов, с которой берут на работу
PASS_SHARE = 0.8

NO_EDUCATION = "не требуется"

# ---------- светлая сторона ----------
#
# Служба и семья несовместимы. Полиция, армия и ФБР не берут того, кто
# состоит в клане, — и это не придирка кадровика, а то единственное, что
# делает выбор семьи выбором: иначе дон города ходил бы в патруль, а
# начальник полиции платил бы взносы в общак.
#
# Отметка стоит на самом месте, а не выводится из дома: в полицейском
# участке работает и уборщица, и если завтра такое место заведут, оно не
# должно закрываться перед мафией заодно с начальником.
LAWFUL = "lawful"

# Чем, кроме диплома, бывает закрыта дверь. Права — не диплом: их не
# учат неделями, их сдают по билету, и у таксопарка требование именно
# такое
LICENCE = "licence"


def paper_title(code: str) -> str:
    """Как называется документ, которого требует место.

    Диплом называет сам курс: если завтра переименуют «Диплом
    менеджера», доска агентства переименуется вместе с ним и спорить с
    самим дипломом не станет.
    """
    if not code:
        return NO_EDUCATION
    if code == LICENCE:
        from bot.game.driving import LICENCE_TITLE

        return LICENCE_TITLE
    from bot.game.study import diploma_of

    course = diploma_of(code)
    return course.diploma_title if course else code


def plural_months(count: int) -> str:
    """Месяцы после «от»: «от 1 месяца», «от 2 месяцев».

    Падеж родительный, а не именительный: после предлога «от» стоит
    «месяца» при единице и «месяцев» при всём остальном. Привычное
    «месяц, месяца, месяцев» здесь даёт «от 2 месяца» — так не говорят.
    """
    tail = count % 100
    if 11 <= tail <= 14:
        return "месяцев"
    return "месяца" if count % 10 == 1 else "месяцев"


def months_count(count: int) -> str:
    """Месяцы при счёте: «1 месяц», «2 месяца», «5 месяцев».

    Не то же, что `plural_months`: там форма после предлога «от», здесь
    — при числе. «У тебя 1 месяца» звучит так же неправильно, как «от 2
    месяца», и одной функцией оба случая не закрыть.
    """
    tail = count % 100
    if 11 <= tail <= 14:
        return "месяцев"
    last = count % 10
    if last == 1:
        return "месяц"
    if 2 <= last <= 4:
        return "месяца"
    return "месяцев"


def months_of(seconds: int) -> int:
    """Опыт в месяцах — вниз: полтора месяца это один, а не два."""
    return max(0, seconds // MONTH_SECONDS)


@dataclass(frozen=True)
class Vacancy:
    """Место работы: где, почём, сколько часов в неделю и кого берут.

    `seats` — сколько таких мест в городе. У бармена их двадцать, у
    патрульного четыре, у управляющего банком одно: город большой, и
    патрулировать его вчетвером естественнее, чем вдвоём драться за
    одну дубинку, а за стойку берут всех, кто сдал тест.

    `needs` — документ, без которого не возьмут: код диплома из
    `bot.game.study` или `licence` — водительское удостоверение. Пусто
    — берут с улицы, как брали бармена.

    `months` — сколько месяцев опыта требуют. Опыт считается от часа
    первого приёма и копится по всем местам: город смотрит, сколько
    боец вообще работал, а не кем именно.
    """

    code: str
    title: str
    emoji: str
    salary: int  # кредиты за неделю при выполненной норме
    hours: int  # недельная норма в часах
    place: str  # код локации, где работают
    # Ноль — мест сколько угодно: в патруль берут всех, кто кончил
    # академию, и считать их городу незачем
    seats: int = 1
    needs: str = ""
    months: int = 0
    # Светлая сторона: место не берут того, кто состоит в клане
    lawful: bool = False
    # Берут не в агентстве, а в самом доме. Агентство такие места не
    # показывает вовсе: в полицию приходят в полицию
    at_house: bool = False
    note: str = ""

    @property
    def limited(self) -> bool:
        """Считает ли город эти места. Нет — берут всех подходящих."""
        return self.seats > 0

    @property
    def cap(self) -> int:
        """Сколько мест для запроса в базу. Безлимит — число с запасом."""
        return self.seats if self.limited else 1_000_000

    @property
    def education(self) -> str:
        """Чего требуют от соискателя — строкой, для витрины."""
        return paper_title(self.needs)

    @property
    def months_text(self) -> str:
        """«без опыта», «от 1 месяца», «от 2 месяцев»."""
        if not self.months:
            return "без опыта"
        return f"от {self.months} {plural_months(self.months)}"

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

    @property
    def hour_rate(self) -> float:
        """Ставка часа без округления: по ней считают сверхурочные.

        Не `per_hour`: та округлена до десятых и годится для витрины, а
        не для денег — на ней расчёт разъехался бы с обещанным.
        """
        return self.salary / self.hours if self.hours else 0.0

    def overtime(self, hours: float) -> float:
        """Сколько часов отработано сверх нормы."""
        return max(0.0, hours - self.hours)

    def overtime_pay(self, hours: float) -> int:
        """Сверхурочные: каждый лишний час по двойной ставке.

        Округляем вверх, как и долю неполной недели: лишние минуты
        бойцу оплачивают, а не списывают.
        """
        extra = self.overtime(hours)
        if not extra:
            return 0
        return ceil(extra * self.hour_rate * OVERTIME_RATE)

    def payout(self, hours: float) -> int:
        """Жалованье за столько часов: оклад и сверхурочные.

        Норма выполнена — оклад целиком, и каждый час сверх неё
        прибавляет две ставки. Меньше нормы — доля от оклада,
        округлённая вверх.
        """
        if hours <= 0:
            return 0
        if hours >= self.hours:
            return self.salary + self.overtime_pay(hours)
        return min(self.salary, ceil(self.salary * hours / self.hours))

    def fires(self, hours: float) -> bool:
        """Увольняют ли за такую неделю."""
        return hours < self.hours * KEEP_SHARE


VACANCIES: tuple[Vacancy, ...] = (
    # Бар — первая работа в городе: сюда приходят, когда денег нет
    # вовсе. Одно место на всех означало, что устроиться успевает
    # ровно один человек за всю жизнь города, а остальным на доске
    # написано «занято». Поэтому за стойкой двадцать мест
    Vacancy(
        "bartender", "Бармен", "🍸", salary=250, hours=10, place="bar",
        seats=20,
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

    # ---------- места под диплом ----------
    #
    # Всё, что ниже, город отдаёт только учёным: без бумаги сюда не
    # берут, а на верхние ступени не берут и с бумагой, пока не
    # отработал своё. Поэтому и платят здесь втрое против улицы — за
    # диплом, которого у патрульного с улицы нет.

    # Банк: банковское дело
    Vacancy(
        "bank_head", "Управляющий филиалом Банка «Vegas»", "🏦",
        salary=1_500, hours=STAFF_HOURS, place="bank",
        seats=1, needs="banking", months=2,
        note="Подписывать то, что до тебя никто не решился подписать.",
    ),
    Vacancy(
        "bank_cashier", "Кассир в Банке", "💵",
        salary=500, hours=STAFF_HOURS, place="bank",
        seats=2, needs="banking",
        note="Считать чужое вслух и не ошибаться.",
    ),
    Vacancy(
        "bank_clerk", "Менеджер по работе с клиентами", "🗂",
        salary=700, hours=STAFF_HOURS, place="bank",
        seats=4, needs="banking", months=1,
        note="Объяснять ставку тому, кто пришёл спорить.",
    ),

    # Администрация: государственное управление
    Vacancy(
        "mayor_adviser", "Советник мэра", "🏛",
        salary=700, hours=STAFF_HOURS, place="city_administration",
        seats=2, needs="gov", months=1,
        note="Говорить мэру то, что он не хочет слышать, но вовремя.",
    ),
    Vacancy(
        "clerk", "Секретарь", "📋",
        salary=500, hours=STAFF_HOURS, place="city_administration",
        seats=4, needs="gov",
        note="Знать, где лежит бумага, раньше того, кто её спросит.",
    ),

    # Полиция: полицейская академия
    Vacancy(
        "police_chief", "Начальник полиции", "🎖",
        salary=1_500, hours=STAFF_HOURS, place="vcpd",
        seats=1, needs="police", months=2,
        lawful=True, at_house=True,
        note="Отвечать за город, когда отвечать больше некому.",
    ),
    Vacancy(
        "police_officer", "Офицер полиции", "👮",
        salary=700, hours=STAFF_HOURS, place="vcpd",
        seats=0, needs="police", months=1,
        lawful=True, at_house=True,
        note="Знать свой квартал по именам и по кличкам.",
    ),
    Vacancy(
        "police_patrol", "Патрульный", "🚓",
        salary=500, hours=STAFF_HOURS, place="vcpd",
        seats=0, needs="police",
        lawful=True, at_house=True,
        note="Ходить по улицам так, чтобы на них было тихо.",
    ),

    # Больница: медицинский колледж
    Vacancy(
        "head_doctor", "Главный врач", "🩺",
        salary=1_500, hours=STAFF_HOURS, place="hospital",
        seats=1, needs="medic", months=2,
        note="Решать первым и объясняться потом.",
    ),
    Vacancy(
        "district_doctor", "Участковый врач", "⚕️",
        salary=700, hours=STAFF_HOURS, place="hospital",
        seats=2, needs="medic", months=1,
        note="Смотреть тех, кто дошёл сам, и тех, кого принесли.",
    ),
    Vacancy(
        "orderly", "Санитар", "🧤",
        salary=500, hours=STAFF_HOURS, place="hospital",
        seats=4, needs="medic",
        note="Донести, удержать и не упасть самому.",
    ),

    # Армейская часть: кадетский корпус
    Vacancy(
        "army_head", "Начальник армейской части", "⭐",
        salary=1_500, hours=STAFF_HOURS, place="military_base",
        seats=1, needs="cadet", months=2,
        lawful=True,
        note="Отдавать приказ, за который отвечать тебе.",
    ),
    Vacancy(
        "senior_officer", "Старший офицер", "🎗",
        salary=700, hours=STAFF_HOURS, place="military_base",
        seats=2, needs="cadet", months=1,
        lawful=True,
        note="Держать роту между приказом и здравым смыслом.",
    ),
    Vacancy(
        "junior_officer", "Младший офицер", "🪖",
        salary=500, hours=STAFF_HOURS, place="military_base",
        seats=4, needs="cadet",
        lawful=True,
        note="Строить, вести и считать по головам на обратном пути.",
    ),

    # Менеджмент: пять одинаковых мест в пяти разных домах
    Vacancy(
        "sales_cars", "Менеджер по продажам", "🚘",
        salary=600, hours=STAFF_HOURS, place="car_dealership",
        seats=3, needs="management",
        note="Продавать машину тому, кто зашёл посмотреть.",
    ),
    Vacancy(
        "sales_insurance", "Менеджер по продажам", "📑",
        salary=600, hours=STAFF_HOURS, place="insurance_office",
        seats=3, needs="management",
        note="Продавать то, чего человек надеется не получить.",
    ),
    Vacancy(
        "sales_driving", "Менеджер по продажам", "🚦",
        salary=600, hours=STAFF_HOURS, place="driving_school",
        seats=3, needs="management",
        note="Записывать на курс тех, кто пришёл «просто спросить».",
    ),
    Vacancy(
        "sales_gym", "Менеджер по продажам", "🏋️",
        salary=600, hours=STAFF_HOURS, place="strength_gym",
        seats=3, needs="management",
        note="Продавать абонемент на год тому, кто пришёл на месяц.",
    ),
    Vacancy(
        "sales_northern", "Менеджер по продажам", "🛒",
        salary=600, hours=STAFF_HOURS, place="northern_wall_shop",
        seats=3, needs="management",
        note="Держать прилавок и знать, что на нём кончается.",
    ),

    # Таксопарк: права, а не диплом
    Vacancy(
        "taxi_driver", "Водитель такси", "🚕",
        salary=500, hours=STAFF_HOURS, place="taxi_depot",
        seats=4, needs=LICENCE,
        note="Везти туда, куда сказали, и молчать про остальное.",
    ),

    # Восточный столбец: пять мест на пять домов. Четыре из них — улица
    # в чистом виде: ни диплома, ни конторы за спиной. Барбер и мастер
    # салона всё же ремесло, и спрашивают с них стаж: диплома под
    # ножницы в городе нет, а брать на них прямо с улицы — всё равно что
    # сажать за стойку банка первого встречного
    Vacancy(
        "car_washer", "Мойщик", "🧽",
        salary=250, hours=STAFF_HOURS, place="car_wash",
        seats=2,
        note="Отмыть то, на чём ездят, и не спрашивать откуда брызги.",
    ),
    Vacancy(
        "waiter", "Официант", "🍽",
        salary=250, hours=STAFF_HOURS, place="restaurant",
        seats=3,
        note="Носить тарелки и помнить, кто что заказал.",
    ),
    Vacancy(
        "barber", "Барбер", "💈",
        salary=300, hours=STAFF_HOURS, place="barbershop",
        seats=2, months=1,
        note="Стричь молча и слушать, когда заговорят.",
    ),
    Vacancy(
        "beauty_master", "Мастер салона", "💅",
        salary=300, hours=STAFF_HOURS, place="beauty_salon",
        seats=3, months=1,
        note="Привести в порядок того, кто завтра снова полезет в драку.",
    ),
    # Дилер стоит особняком: это продажи, как в салоне и страховой, и
    # спрос с него тот же — диплом управления и те же шестьсот
    Vacancy(
        "car_dealer", "Дилер", "🔑",
        salary=600, hours=STAFF_HOURS, place="used_car_market",
        seats=2, needs="management",
        note="Продать подержанное так, чтобы вернулись за вторым.",
    ),

    # Бизнес-центр: разработка. Ступеней три, и разница между ними не в
    # дипломе — он один на всех, — а в стаже: младшего берут сразу после
    # университета, старшего не берут и с дипломом, пока не отработал
    # своё. Так же устроен банк и так же полиция
    Vacancy(
        "dev_senior", "Старший разработчик", "🧠",
        salary=1_500, hours=STAFF_HOURS, place="office_building",
        seats=2, needs="it", months=3,
        note="Отвечать за то, что написали другие.",
    ),
    Vacancy(
        "dev_middle", "Разработчик", "💻",
        salary=1_000, hours=STAFF_HOURS, place="office_building",
        seats=4, needs="it", months=1,
        note="Делать так, чтобы к этому больше не возвращались.",
    ),
    Vacancy(
        "dev_junior", "Младший разработчик", "⌨️",
        salary=600, hours=STAFF_HOURS, place="office_building",
        seats=6, needs="it",
        note="Писать код и спрашивать, пока спрашивать не стыдно.",
    ),
)

WORK_IMAGE = f"{MAGIC}/work_contract.jpeg"


def get_vacancy(code: str) -> Vacancy | None:
    return next((one for one in VACANCIES if one.code == code), None)


def hired_at_house(place: str) -> tuple[Vacancy, ...]:
    """Места, на которые берут в самом доме, а не в агентстве."""
    return tuple(
        one for one in VACANCIES if one.at_house and one.place == place
    )


def agency_board() -> tuple[Vacancy, ...]:
    """Что показывает агентство. В полицию приходят в полицию."""
    return tuple(one for one in VACANCIES if not one.at_house)


def vacancies_at(place: str) -> tuple[Vacancy, ...]:
    """Какая работа есть в этом доме. У банка её три, у бара одна."""
    return tuple(one for one in VACANCIES if one.place == place)


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


def day_is_over(moment: int, hours: int) -> str:
    """Почему сегодня больше не работают — и когда можно снова.

    В ночь на понедельник отказ звучал бы неправдой: «на сегодня хватит»
    — а в девять утра того же дня смена откроется снова, потому что
    начнётся новая неделя. Поэтому в эту ночь говорим про девять утра, а
    не про завтра.
    """
    said = f"На сегодня хватит: в рабочий день работают не больше {hours} часов."
    here = datetime.fromtimestamp(moment, MOSCOW)
    if here.weekday() == PAYDAY_WEEKDAY and here.hour < PAYDAY_HOUR:
        said += (
            f" В {PAYDAY_HOUR}:00 неделя закроется, придёт жалованье — "
            "и можно будет отработать дневную смену."
        )
    return said


def days_to_payday(hired: int, week_start: int) -> int:
    """Сколько целых рабочих дней осталось принятому до расчёта.

    День приёма не в счёт: он уже наполовину прошёл, и требовать смену
    от того, кто устроился вечером, значит считать время, которого у
    него не было. Понедельник расчёта тоже не в счёт: в девять утра
    неделя закрывается, и его ночь — это уже торг с будильником, а не
    рабочий день.

    Поэтому считаем дни между: устроился в пятницу — остаются суббота и
    воскресенье, в воскресенье — не остаётся ничего.
    """
    payday = moscow_day(week_start + WEEK_SECONDS)
    return max(0, (payday - moscow_day(hired)).days - 1)


def hours_before_payday(hired: int, week_start: int) -> float:
    """Сколько часов боец физически успевал отработать в эту неделю."""
    return days_to_payday(hired, week_start) * DAY_HOURS


def work_day(moment: int) -> str:
    """Какой сейчас рабочий день: сутки и неделя, которой они принадлежат.

    Обычно рабочий день — это московские сутки, и считать их хватило бы
    одной датой. Понедельник ломает это правило: в девять утра неделя
    закрывается и платят жалованье, а значит, у понедельника два рабочих
    дня — ночной, который идёт в зачёт прошлой неделе, и дневной, уже
    новой. Смену можно отработать в каждом: иначе ночная смена съедала
    бы первый день новой недели, за который ей не заплатят.

    Поэтому в ключе рядом с датой стоит начало недели: в полночь меняется
    дата, в девять утра понедельника — неделя, и оба раза это новый
    рабочий день. В остальные дни неделя не меняется, и всё работает как
    раньше — сутки по Москве.

    Старая запись (одна дата, без недели) с новой не совпадёт, и первая
    смена после обновления откроется заново. Это разовая мелочь: касается
    только тех, кто отработал сегодня, и ровно одного дня.
    """
    return f"{moscow_day(moment).isoformat()}/{week_of(moment)}"


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
    "agency_board",
    "days_to_payday",
    "hours_before_payday",
    "hired_at_house",
    "OVERTIME_RATE",
    "WEEK_SECONDS",
    "WORK_IMAGE",
    "LICENCE",
    "MONTH_SECONDS",
    "STAFF_HOURS",
    "WORK_PLACES",
    "Vacancy",
    "day_is_full",
    "day_is_over",
    "get_vacancy",
    "hours_of",
    "months_count",
    "months_of",
    "paper_title",
    "plural_months",
    "moscow_day",
    "payday_after",
    "payday_before",
    "shift_fits",
    "vacancies_at",
    "week_is_over",
    "week_of",
    "work_day",
]
