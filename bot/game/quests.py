"""Квесты бармена: двадцать заданий, с которых начинается город.

Квест — это не история, а повод дойти ногами. Город большой, домов в нём
три десятка, и новичок обходит их годами; бармен проводит по главным за
четыре главы — ринг и первые двери, работа с обменом и залом, экипировка
и подготовка, самостоятельная жизнь.

**Четыре главы по пять заданий, глава на уровень.** Задание открывается,
когда сдано предыдущее и уровень дорос до главы. Если глава кончилась
раньше повышения, бармен так и говорит: дел больше нет, расти. Квесты
при этом уровень не поднимают и повышение не блокируют — они про город,
а не про прогресс.

**Берут по одному и по порядку.** Пока одно на руках, второго не дают:
это не ветка, а лестница. Отказаться можно — бармен не обидится и
предложит то же в следующий раз.

**Проверяет город, а не слово игрока.** Бармен не спрашивает «сходил
ли» — он смотрит. Часть условий закрывается событием (вышел на ринг,
дошёл до двери, открыл карточку, отработал тренировку, выставил вещь), и
о событии службы говорят сами; часть — состоянием бойца (счёт открыт,
работа есть, вещь в рюкзаке, броня надета), и его видно без всякой
памяти. Поэтому обмануть квест нельзя: соврать можно только тому, кто
слушает.

**Условий у задания может быть несколько.** Сходить и ответить, открыть
каталог и ответить — это два условия, и сдаётся задание, когда готовы
оба. Вопросы бармен задаёт последними: он спрашивает про то, что боец
видел своими глазами, и до похода спрашивать нечего.

**Маркер на карте — не посещение.** Зачёт даёт успешный ответ сервера на
запрос самого экрана или каталога, а не нажатие на дом: иначе задание
закрывалось бы, не показав игроку ни строчки из того, ради чего его
давали.

Правила здесь, слова бармена — в `bot.content.bartender`, действия — в
`bot.quest_service`, хранение — в `bot.database`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.game.classes import Zone

# Кто выдаёт. Пока один на весь город, но код стоит в записи: вторым
# будет кто-нибудь ещё, и тогда список заданий у каждого станет своим
BARTENDER = "bartender"

# Сколько дней держится абонемент, который бармен даёт на тренировку
GYM_DAYS = 1

# Сколько зон тела должна прикрывать броня в задании про экипировку.
# Зон, а не вещей: три вещи на одну зону оставляют остальные открытыми
ARMOR_ZONES = 3

# Услуга HR-агентства: там оформляют приём на работу. Нужна обходу
# задания про смену — тому, кто уже работает в другом месте
HR_SERVICE = "hire"


class Goal(str, Enum):
    """Чем условие закрывается — то есть что именно город должен увидеть."""

    RING = "ring"  # завершить кулачный бой с живым бойцом в клубе
    VISIT = "visit"  # дойти до двери услуги и войти
    PLACE = "place"  # войти в конкретный дом, а не в любой с услугой
    SEEN = "seen"  # открыть карточку из каталога
    ITEM = "item"  # принести вещь
    ACCOUNT = "account"  # открыть счёт в банке
    JOB = "job"  # устроиться на работу
    SCHOOL = "school"  # записаться на курс в автошколе
    MARKET = "market"  # выставить вещь в комиссионку
    TRAIN = "train"  # отработать тренировку в зале
    TRADE = "trade"  # положить свою часть обмена на стол
    ARMOR = "armor"  # прикрыть бронёй три зоны тела


# Какие цели закрываются событием, а какие видно по самому бойцу. Первые
# помнит запись квеста, вторые не нужно помнить вовсе
BY_EVENT: frozenset[Goal] = frozenset(
    {
        Goal.RING,
        Goal.VISIT,
        Goal.PLACE,
        Goal.SEEN,
        Goal.MARKET,
        Goal.TRAIN,
        Goal.TRADE,
    }
)


@dataclass(frozen=True)
class Need:
    """Одно условие задания: что сделать и где.

    Условий у задания может быть несколько, и тогда нужны все. Порядок в
    записи — порядок напоминаний: бармен называет первое незакрытое.
    """

    goal: Goal
    target: str = ""

    @property
    def by_event(self) -> bool:
        return self.goal in BY_EVENT

    @property
    def mark(self) -> str:
        """Отметка, которой город закрывает это условие. Пусто — не событие."""
        if not self.by_event:
            return ""
        return f"{self.goal.value}:{self.target}" if self.target else self.goal.value


@dataclass(frozen=True)
class Chapter:
    """Глава: пять заданий одной темы, открытых одним уровнем."""

    code: str
    level: int
    title: str


CHAPTERS: tuple[Chapter, ...] = (
    Chapter("city", 1, "Знакомство с городом"),
    Chapter("work", 2, "Работа, обмен и тренировки"),
    Chapter("gear", 3, "Экипировка и подготовка"),
    Chapter("own", 4, "Самостоятельная жизнь"),
)

CHAPTER_OF: dict[str, Chapter] = {one.code: one for one in CHAPTERS}


@dataclass(frozen=True)
class Quest:
    """Задание бармена: что сделать и что за это будет."""

    code: str
    title: str
    task: str  # строка задания — она же стоит в списке квестов
    chapter: str
    needs: tuple[Need, ...] = ()
    # Код набора вопросов в `bot.content.bartender`. Пусто — бармен не
    # спрашивает. Вопросы идут после того, как закрыты все условия
    quiz: str = ""
    exp: int = 50
    credits: int = 0  # награда кредитами
    # Доплата за победу там, где победа не обязательна
    win_bonus: int = 0
    advance: int = 0  # сколько бармен даёт вперёд, при взятии
    gives: str = ""  # вещь, которую бармен даёт при взятии
    takes: bool = False  # забирает ли принесённую вещь себе
    gym_pass: bool = False  # даёт ли свой абонемент в зал
    # Заданию про работу есть обход для того, кто уже работает в другом
    # месте: увольняться ради бармена незачем, достаточно изучить
    # вакансию и сказать, где её оформляют. Признак здесь, а не в
    # условиях, потому что это именно обход, а не второе условие:
    # устроившемуся барменом вопросов не задают вовсе
    job_or_quiz: bool = False

    @property
    def min_level(self) -> int:
        """С какого уровня бармен даёт это задание."""
        one = CHAPTER_OF.get(self.chapter)
        return one.level if one else 1

    @property
    def chapter_title(self) -> str:
        one = CHAPTER_OF.get(self.chapter)
        return one.title if one else ""

    @property
    def marks(self) -> tuple[str, ...]:
        """Отметки всех событийных условий — по ним город и закрывает их."""
        return tuple(one.mark for one in self.needs if one.by_event)

    @property
    def reward_text(self) -> str:
        """Награда словами — она же выводится из этих же полей."""
        parts = [f"{self.exp} опыта"]
        if self.credits:
            parts.append(f"{self.credits} 💰")
        if self.win_bonus:
            parts.append(f"ещё {self.win_bonus} 💰 за победу")
        return ", ".join(parts)


# Задания идут в том порядке, в каком их выдают: пять на главу, главы по
# возрастанию уровня. Порядок — он же лестница
QUESTS: tuple[Quest, ...] = (
    # ---------- уровень 1: знакомство с городом ----------
    Quest(
        code="ring",
        title="Первая кровь",
        task="Провести кулачный бой с живым бойцом в Бойцовском клубе",
        chapter="city",
        needs=(Need(Goal.RING),),
        exp=50,
        # Победа не обязательна: задание про то, чтобы выйти на ринг.
        # Доплата идёт по первому зачтённому бою, а не по лучшему
        win_bonus=10,
    ),
    Quest(
        code="pro",
        title="За что платят звёздами",
        task="Посмотреть PRO и PREMIUM в элитном магазине и вернуться с ответами",
        chapter="city",
        needs=(Need(Goal.VISIT, "premium"),),
        quiz="pro",
        exp=25,
        credits=5,
    ),
    Quest(
        code="bank",
        title="Деньги любят счёт",
        task="Открыть счёт в Vegas Банке",
        chapter="city",
        needs=(Need(Goal.ACCOUNT),),
        exp=25,
    ),
    Quest(
        code="policy",
        title="На всякий случай",
        task="Зайти в страховую и прочитать условия полиса",
        chapter="city",
        needs=(Need(Goal.VISIT, "insurance"),),
        exp=25,
    ),
    Quest(
        code="looks_intro",
        title="Свой стиль",
        task="Открыть каталог образов в барбершопе или салоне красоты",
        chapter="city",
        # Услуга образов открыта в двух домах сразу, и это та самая
        # альтернатива из условия: заходить можно в любой
        needs=(Need(Goal.VISIT, "looks"),),
        exp=25,
    ),
    # ---------- уровень 2: работа, обмен и тренировки ----------
    Quest(
        code="work",
        title="Своя смена",
        task="Устроиться барменом через HR-агентство",
        chapter="work",
        needs=(Need(Goal.JOB, "bartender"),),
        quiz="work",
        exp=75,
        job_or_quiz=True,
    ),
    Quest(
        code="study_intro",
        title="Учёба открывает двери",
        task="Зайти в университет и посмотреть направления",
        chapter="work",
        # Дом, а не услуга: учат в пяти местах города, а спрашивают
        # именно про университет
        needs=(Need(Goal.PLACE, "university"),),
        quiz="study",
        exp=50,
        credits=15,
    ),
    Quest(
        code="trade_intro",
        title="Стол на двоих",
        task="Занять стол на рынке и положить свою часть обмена",
        chapter="work",
        needs=(Need(Goal.TRADE),),
        exp=50,
    ),
    Quest(
        code="gym_intro",
        title="Спортивный интерес",
        task="Посмотреть расписание зала и вернуться с двумя числами",
        chapter="work",
        needs=(Need(Goal.VISIT, "train"),),
        quiz="gym",
        exp=50,
    ),
    Quest(
        code="gym",
        title="Железо не ждёт",
        task="Отработать тренировку в зале целиком",
        chapter="work",
        needs=(Need(Goal.TRAIN),),
        exp=100,
        gym_pass=True,
    ),
    # ---------- уровень 3: экипировка и подготовка ----------
    Quest(
        code="knuckles",
        title="Кастет для бармена",
        task="Принести бармену кастет из оружейного магазина",
        chapter="gear",
        needs=(Need(Goal.ITEM, "knuckles"),),
        exp=100,
        credits=50,
        advance=80,
        takes=True,
    ),
    Quest(
        code="ring_gift",
        title="Кольцо для бармена",
        task="Принести бармену кольцо силы от ювелира",
        chapter="gear",
        needs=(Need(Goal.ITEM, "ring_strength"),),
        exp=50,
        credits=100,
        takes=True,
    ),
    Quest(
        code="armor_intro",
        title="Броня держит удар",
        task=f"Прикрыть бронёй не меньше {ARMOR_ZONES} разных зон тела",
        chapter="gear",
        needs=(Need(Goal.ARMOR),),
        exp=100,
    ),
    Quest(
        code="repair_intro",
        title="Снаряжение в порядке",
        task="Открыть мастерскую и посмотреть состояние вещей",
        chapter="gear",
        needs=(Need(Goal.VISIT, "repair"),),
        quiz="repair",
        exp=75,
    ),
    Quest(
        code="dojo_intro",
        title="Не одной силой",
        task="Открыть в школе карточку приёма с условиями изучения",
        chapter="gear",
        needs=(Need(Goal.SEEN, "dojo"),),
        exp=75,
    ),
    # ---------- уровень 4: самостоятельная жизнь ----------
    Quest(
        code="market",
        title="Лишнее — на прилавок",
        task="Выставить кастет бармена в комиссионный магазин",
        chapter="own",
        needs=(Need(Goal.MARKET, "knuckles"),),
        exp=50,
        gives="knuckles",
    ),
    Quest(
        code="licence",
        title="Права на будущее",
        task="Записаться на курс ПДД в автошколе",
        chapter="own",
        needs=(Need(Goal.SCHOOL),),
        exp=50,
        credits=300,
    ),
    Quest(
        code="police_intro",
        title="Закон и кулак",
        task="Зайти в полицию и вернуться с ответом про договорный бой",
        chapter="own",
        needs=(Need(Goal.VISIT, "police"),),
        quiz="police",
        exp=75,
    ),
    Quest(
        code="cars_intro",
        title="Сначала выбери колёса",
        task="Открыть в автосалоне карточку машины и вернуться с ответом",
        chapter="own",
        needs=(Need(Goal.SEEN, "cars"),),
        quiz="cars",
        exp=75,
    ),
    Quest(
        code="raid_intro",
        title="Перед общим делом",
        task="Открыть условия рейда и вернуться с ответом про общего врага",
        chapter="own",
        needs=(Need(Goal.VISIT, "raid"),),
        quiz="raid",
        exp=100,
    ),
)

BY_CODE: dict[str, Quest] = {quest.code: quest for quest in QUESTS}

# Порядок — он же лестница: задание открывается, когда сдано прошлое
ORDER: tuple[str, ...] = tuple(quest.code for quest in QUESTS)

# Какие задания были в первой версии. Нужно миграции: сданные старые
# остаются сданными, а активное старое доделывается по старым правилам
OLD_CODES: frozenset[str] = frozenset(
    {
        "ring",
        "pro",
        "knuckles",
        "bank",
        "policy",
        "work",
        "licence",
        "ring_gift",
        "market",
        "gym",
    }
)


# Награды первой версии: по ним рассчитываются перенесённые задания.
# Боец соглашался на эти условия, и менять их задним числом нельзя —
# ни вверх, ни вниз: обещание есть обещание
OLD_REWARD: dict[str, tuple[int, int]] = {
    "ring": (50, 0),
    "pro": (50, 0),
    "knuckles": (50, 100),
    "bank": (50, 0),
    "policy": (50, 0),
    "work": (50, 0),
    "licence": (50, 300),
    "ring_gift": (50, 100),
    "market": (50, 0),
    "gym": (100, 0),
}

# Отметка перенесённого задания. Её ставит разовая работа по живой базе
# (`bot.chores`) тому, у кого в момент выката задание было на руках.
# Отметкой, а не датой взятия: дата выката в коде однажды разошлась бы с
# датой на сервере, и перенос достался бы не тем
OLD_MARK = "old:1"


def carried(marks: set[str]) -> bool:
    """Взято ли задание ещё по первой версии правил."""
    return OLD_MARK in marks


def reward_of(quest: Quest, marks: set[str]) -> tuple[int, int]:
    """Сколько платить за это задание: опыт и кредиты.

    Перенесённому платим по старому: условия, на которые боец
    соглашался, были другими, и новая цена — что выше, что ниже — стала
    бы подменой уже заключённой сделки. Доплата за победу к
    перенесённым не применяется: её в первой версии не обещали.
    """
    if carried(marks):
        return OLD_REWARD.get(quest.code, (quest.exp, quest.credits))
    return quest.exp, quest.credits


def get_quest(code: str) -> Quest | None:
    return BY_CODE.get(code)


def chapter_quests(code: str) -> tuple[Quest, ...]:
    """Задания одной главы — по порядку."""
    return tuple(one for one in QUESTS if one.chapter == code)


def next_quest(done: set[str], taken: str = "", level: int = 1) -> Quest | None:
    """Какое задание бармен предложит следующим. None — давать нечего.

    Берут по одному: пока одно на руках, второго не дают. Отказ ничего
    не меняет — то же задание предложат снова.

    Уровень смотрим здесь, а не на странице: спрятанная кнопка
    обходится запросом мимо интерфейса, и проверка обязана стоять там,
    где её не обойти. Доросшего уровня нет — возвращаем пусто, и
    бармен говорит, до какого расти (`locked_by`).
    """
    if taken:
        return None
    for code in ORDER:
        if code in done:
            continue
        quest = BY_CODE[code]
        return quest if level >= quest.min_level else None
    return None


def locked_by(done: set[str], taken: str = "", level: int = 1) -> int:
    """До какого уровня расти, чтобы бармен дал следующее дело.

    Ноль — расти не надо: или дело есть, или заданий больше нет вовсе.
    По этому числу и строится реплика «достигни уровня N».
    """
    if taken:
        return 0
    for code in ORDER:
        if code in done:
            continue
        quest = BY_CODE[code]
        return 0 if level >= quest.min_level else quest.min_level
    return 0


# ---------- готово ли задание ----------


def armor_zones(player) -> int:
    """Сколько зон тела прикрыто бронёй.

    Считаем зоны, а не вещи: три вещи на одну зону оставляют остальные
    открытыми. Прикрытой считается зона, в которой броня может хоть
    что-то поглотить, — то есть верх её разброса больше нуля. Оружие и
    прибавки к характеристикам бронёй не считаются вовсе: у них нет ни
    зон, ни брони, и в этот счёт они не попадают сами собой.
    """
    equipment = player.equipment
    return sum(1 for zone in Zone if equipment.armor_range(zone)[1] > 0)


# Отметка о выданной барменом вещи: в ней номер самой вещи, а не её
# код. Код у кастетов общий, а выставить в комиссионку нужно именно тот,
# который бармен дал: иначе задание закрывал бы любой купленный кастет
def given_mark(item_id: int) -> str:
    return f"given:{item_id}"


def given_id(marks: set[str]) -> int:
    """Номер выданной вещи из отметок. Ноль — бармен ничего не давал."""
    for mark in marks:
        if mark.startswith("given:"):
            try:
                return int(mark.split(":", 1)[1])
            except ValueError:  # pragma: no cover - отметку правили руками
                return 0
    return 0


def market_mark(item_id: int) -> str:
    """Отметка комиссионки: такую вещь выставили на прилавок."""
    return f"{Goal.MARKET.value}:{item_id}"


def market_done(marks: set[str]) -> bool:
    """Выставлена ли в комиссионку именно выданная барменом вещь.

    Общей отметки «что-то выставил» не хватает: боец мог сдать свою
    вещь, а кастет бармена оставить себе. Поэтому сверяем номер.

    У перенесённого из первой версии задания номера нет — тогда её и не
    записывали. Для него оставляем старую проверку: подойдёт любая
    выставленная вещь. Угадывать, какой кастет был выдан полгода назад,
    мы не станем.
    """
    item_id = given_id(marks)
    if not item_id:
        return Goal.MARKET.value in marks
    return market_mark(item_id) in marks


def need_done(need: Need, marks: set[str], player) -> bool:
    """Закрыто ли одно условие."""
    if need.goal is Goal.MARKET:
        return market_done(marks)
    if need.goal is Goal.RING:
        # Исход боя записан в самой отметке, и годится любой: задание
        # про то, чтобы выйти на ринг, а не про то, чтобы выиграть
        return any(one in marks for one in RING_MARKS)
    if need.by_event:
        return need.mark in marks
    if need.goal is Goal.ITEM:
        return any(
            owned.code == need.target and not owned.is_equipped
            for owned in player.gear
        )
    if need.goal is Goal.ACCOUNT:
        return player.has_account
    if need.goal is Goal.JOB:
        return player.job_code == need.target
    if need.goal is Goal.SCHOOL:
        # Права на руках — значит, курс давно пройден: такому бойцу
        # повторно записываться и платить незачем
        return player.school.enrolled or player.school.has_licence
    if need.goal is Goal.ARMOR:
        return armor_zones(player) >= ARMOR_ZONES
    return False  # pragma: no cover - цель без проверки не заводится


def wants(quest: Quest, mark: str, marks: set[str] | None = None) -> bool:
    """Нужна ли этому заданию такая отметка.

    Город рассказывает обо всём, что видит, и большая часть этого ни
    одному заданию не нужна. Отметки, которую задание не ждёт, в журнал
    не ложится вовсе: иначе он зарастал бы чужими событиями, а по ним
    потом читают, что боец делал.

    Отметки бывают заранее известные («вошёл в страховую») и
    сложившиеся по ходу: у ринга в отметке исход боя, у комиссионки —
    номер вещи. Для таких сверяем начало, а разбирают их сами правила.

    `marks` — то, что в журнале уже есть. Нужно там, где вторая отметка
    того же рода не должна ложиться поверх первой.
    """
    held = marks or set()
    if mark in quest.marks:
        return True
    goals = {one.goal for one in quest.needs}
    if Goal.RING in goals and mark in RING_MARKS:
        # Исход пишется один раз: зачёт даёт первый завершённый бой, и
        # поздняя победа зачтённое поражение не заменяет — иначе бонус
        # за победу брался бы перебором, не беря нового задания
        return not any(one in held for one in RING_MARKS)
    if Goal.MARKET in goals and (
        mark.startswith(f"{Goal.MARKET.value}:")
        # Старая отметка без номера: так писала первая версия, и у
        # перенесённого задания в журнале лежит именно она
        or mark == Goal.MARKET.value
    ):
        return True
    # Обход задания про работу: HR — не условие в записи, а второй путь
    if quest.job_or_quiz and mark == Need(Goal.VISIT, HR_SERVICE).mark:
        return True
    return False


def other_job(quest: Quest, player) -> bool:
    """Работает ли боец не там, куда его отправлял бармен.

    Это и открывает обход: увольняться ради поручения незачем, а
    вакансию изучить и назвать место оформления — вполне.
    """
    if not quest.job_or_quiz:
        return False
    wanted = next(
        (one.target for one in quest.needs if one.goal is Goal.JOB), ""
    )
    return bool(player.job_code) and player.job_code != wanted


def needs_left(quest: Quest, marks: set[str], player) -> tuple[Need, ...]:
    """Какие условия ещё не закрыты — в порядке записи.

    У задания с обходом условия считаются по тому пути, который бойцу
    и доступен: работающему в другом месте не напоминают про то, что
    ему делать не нужно.
    """
    if other_job(quest, player):
        return (
            ()
            if Need(Goal.VISIT, HR_SERVICE).mark in marks
            else (Need(Goal.VISIT, HR_SERVICE),)
        )
    return tuple(one for one in quest.needs if not need_done(one, marks, player))


def quiz_open(quest: Quest, marks: set[str], player) -> bool:
    """Пора ли бармену спрашивать.

    Сперва дойти, потом ответить: вопросы про то, что видно на
    прилавке, и спрашивать их раньше похода незачем. У задания про
    работу вопросов нет вовсе, пока боец не работает в другом месте:
    устроившемуся барменом спрашивать нечего.
    """
    if not quest.quiz:
        return False
    if quest.job_or_quiz and not other_job(quest, player):
        return False
    return not needs_left(quest, marks, player)


def is_ready(quest: Quest, marks: set[str], player) -> bool:
    """Сделано ли задание. Смотрим город, а не слово бойца."""
    if needs_left(quest, marks, player):
        return False
    if quest.job_or_quiz and not other_job(quest, player):
        # Устроился барменом — этого и хотели, вопросов не будет
        return True
    if not quest.quiz:
        return True
    # У перенесённого задания отметка о разговоре лежит от первой
    # версии: набор тогда был один на весь город, и отметка звалась
    # иначе. Переспрашивать того, кто уже всё рассказал, незачем
    if carried(marks) and OLD_QUIZ_DONE in marks:
        return True
    return quiz_done_mark(quest.quiz) in marks


# ---------- отметки вопросов ----------


def quiz_marks(quiz: str, step: int) -> str:
    """Отметка о верном ответе на вопрос номер `step` этого набора.

    Набор входит в отметку: наборов теперь много, и отметка «ответил на
    второй» без набора означала бы «ответил на второй у кого угодно».
    """
    return f"quiz:{quiz}:{step}"


def quiz_done_mark(quiz: str) -> str:
    """Отметка о том, что весь набор отвечен."""
    return f"quiz:{quiz}:done"


# Отметка первой версии: один набор вопросов на весь город. Остаётся в
# коде ради перенесённых заданий — у них в журнале лежит именно она
OLD_QUIZ_DONE = "quiz:done"


def ring_mark(won: bool | None) -> str:
    """Отметка о бое на ринге с его исходом.

    Исход входит в отметку, потому что доплата идёт за победу, а
    засчитывается первый завершённый бой: поздняя победа не заменяет
    зачтённое поражение ради бонуса, а поражение не отменяет зачёт.
    """
    end = "draw" if won is None else ("win" if won else "loss")
    return f"{Goal.RING.value}:{end}"


RING_MARKS: tuple[str, ...] = (
    ring_mark(True),
    ring_mark(False),
    ring_mark(None),
)


def ring_won(marks: set[str]) -> bool:
    """Был ли зачтённый бой выигран. По нему и платят доплату."""
    return ring_mark(True) in marks


__all__ = [
    "ARMOR_ZONES",
    "BARTENDER",
    "BY_CODE",
    "BY_EVENT",
    "CHAPTERS",
    "CHAPTER_OF",
    "GYM_DAYS",
    "HR_SERVICE",
    "OLD_CODES",
    "OLD_MARK",
    "OLD_QUIZ_DONE",
    "OLD_REWARD",
    "ORDER",
    "QUESTS",
    "RING_MARKS",
    "Chapter",
    "Goal",
    "Need",
    "Quest",
    "armor_zones",
    "carried",
    "chapter_quests",
    "get_quest",
    "given_id",
    "given_mark",
    "is_ready",
    "locked_by",
    "market_done",
    "market_mark",
    "need_done",
    "needs_left",
    "next_quest",
    "other_job",
    "quiz_done_mark",
    "quiz_marks",
    "quiz_open",
    "reward_of",
    "ring_mark",
    "ring_won",
    "wants",
]
