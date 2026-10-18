"""Война за территории: точки города, которые приносят доход.

Клан мафии живёт взносами и сейфом, и до сих пор ему некуда было расти:
семья собралась — и дальше что? Территории и есть это «дальше». Город
поделён на точки, точка приносит деньги каждые сутки, и точек на всех не
хватает.

**Точка — это существующий дом.** Казино, рынок, комиссионка, таксопарк:
места, через которые и так идут деньги. Рисовать для них отдельный слой
карты не нужно — боец и так знает, где казино.

**Владелец — семья или служба.** Клан мафии и городская служба стоят
здесь на равных: у точки один владелец, и это либо клан, либо полиция,
армия, ФБР. Поэтому владелец — пара «чей» и «кто»: `clan:3` или
`service:police`. Два разных вида владельцев в одной колонке были бы
путаницей, а две колонки на одно и то же — двумя правдами.

**Доход капает раз в сутки и только тому, кто держал их целиком.**
Часов, которые бы его начисляли, в клубе нет — как нет их у работы, у
полиса и у аренды. Доход сводится в тот миг, когда на точки смотрят:
сколько полных суток точка была твоей, столько раз она и заплатит.
Поэтому захват в последний час суток не крадёт чужой день.

**Ничьих точек не бывает.** Пустая точка — это точка под городской
охраной: её тоже надо брать боем. Иначе первый зашедший снимал бы весь
город без единого удара, и воевать было бы не за что.

**Отбить можно любую, но не сразу.** После боя точка отдыхает: сутки её
не трогают, кто бы ни победил. Без этого сильный клан ходил бы по кругу
и отбивал всё назад в ту же минуту, а слабый не успевал бы и получить
первый доход.

Правила здесь, бой — в `bot.turf_service`, хранение — в `bot.database`,
экран — в `bot.webapp.turf`.
"""

from __future__ import annotations

from dataclasses import dataclass

HOUR = 60 * 60
DAY_SECONDS = 24 * HOUR

# Сколько точка отдыхает после боя — чья бы она ни стала
# Доля города в доходе с точки. Полиция держит точки не ради денег, и
# платить ей как семье значило бы сделать службу выгоднее мафии
SERVICE_SHARE = 0.5

CALM_HOURS = 24
CALM_SECONDS = CALM_HOURS * HOUR

# Сколько бойцов выходит в гарнизон. Больше десяти не помещается ни в
# панель, ни в лимит сообщений — те же десять, что и в рейде
MAX_GARRISON = 5

# Сколько точек одна семья держит разом. Без потолка первый же сильный
# клан снял бы весь город и дальше воевать было бы не с кем
MAX_SPOTS_PER_OWNER = 4

# Чем владеют: клан мафии или городская служба
CLAN = "clan"
SERVICE = "service"
# А это никто: точка под городской охраной
NOBODY = "none"


@dataclass(frozen=True)
class Owner:
    """Чей это дом: пара «вид владельца» и «кто именно».

    Клан — числом, служба — кодом. Пустой `kind` значит городскую
    охрану: точка ничья, и брать её всё равно придётся боем.
    """

    kind: str = NOBODY
    key: str = ""

    @property
    def nobody(self) -> bool:
        return self.kind not in (CLAN, SERVICE) or not self.key

    @property
    def clan_id(self) -> int:
        """Номер клана. Ноль — владелец не клан."""
        if self.kind != CLAN:
            return 0
        try:
            return int(self.key)
        except ValueError:  # pragma: no cover - в базе чужое число
            return 0

    @property
    def service(self) -> str:
        """Код службы. Пусто — владелец не служба."""
        return self.key if self.kind == SERVICE else ""

    def same(self, other: "Owner") -> bool:
        return self.kind == other.kind and self.key == other.key


def owner_of_clan(clan_id: int) -> Owner:
    return Owner(CLAN, str(clan_id))


def owner_of_service(code: str) -> Owner:
    return Owner(SERVICE, code)


NO_ONE = Owner()

# ---------- что сторона делает на этой точке ----------
#
# Три дела и ни одного пересечения: на точку приходят либо ставить
# охрану, либо брать её боем. Какое из них — решает не кнопка, а пара
# «чей дом» и «кто пришёл»:
#
# **Город бьёт только семью.** Ничья точка и так под городской охраной,
# и «отбивать» её у самого себя городу незачем: туда он приводит
# патрульного. Драка города с мафией зовётся задержанием, а не
# захватом: город не присваивает дом, он выводит с него мафию.
#
# **Семья берёт всё, что не её.** Ничью, городскую и чужую семейную —
# и всегда боем: у любой из них есть кому стоять.
#
# **Своя точка — только охрана.** Воевать за неё не с кем.
#
# **Брошенный дом город забирает без боя.** Семья, не поставившая на
# точку человека, держит её одной вывеской: драться там не с кем, и
# звать отряд на пустой дом — спектакль. Полицейский снимает такое
# владение одним нажатием, и это **освобождение**, а не задержание:
# задерживать некого.
SEIZE = "seize"  # захват: семья берёт чужое
ARREST = "arrest"  # задержание: город выводит мафию боем
FREE = "free"  # освобождение: город снимает влияние с пустого дома
GUARD = "guard"  # охрана: поставить своего на точку

# Сколько семья держит точку без охраны, прежде чем дом вернётся
# городу. Сутки — те же, что у отдыха точки: день без человека это и
# есть «бросили». Дом не бывает ничей вечно — ничей он под городской
# охраной, а вывеска семьи на пустом доме не значит ничего
ABANDON_HOURS = 24
ABANDON_SECONDS = ABANDON_HOURS * HOUR


def action_for(owner: Owner, mine: Owner, guarded: bool = True) -> str:
    """Что эта сторона может сделать на этой точке. Пусто — ничего.

    `guarded` — стоит ли на точке хоть один выставленный. Городу это
    меняет дело: пустую семейную точку он освобождает без боя.
    """
    if mine.nobody:
        return ""
    if owner.same(mine):
        return GUARD
    if mine.kind == SERVICE:
        if owner.kind != CLAN:
            return GUARD
        return ARREST if guarded else FREE
    return SEIZE


def abandoned(empty_since: int, now: int) -> bool:
    """Брошена ли точка: сутки без единого выставленного."""
    return bool(empty_since) and now - empty_since >= ABANDON_SECONDS


@dataclass(frozen=True)
class Spot:
    """Точка на карте: дом, доход в сутки и чем она интересна."""

    code: str  # код дома города
    income: int  # кредитов в сутки
    note: str = ""

    @property
    def title(self) -> str:
        from bot.game.locations import get_location

        place = get_location(self.code)
        return place.title if place else self.code

    @property
    def genitive(self) -> str:
        from bot.game.locations import get_location

        place = get_location(self.code)
        return place.genitive if place and place.genitive else self.title


# Десять точек. Доход расставлен по тому, сколько денег через дом и так
# идёт: казино берёт больше всех, лавка на окраине — меньше всех.
#
# Чего здесь нет и не будет: особняка мафии (это дом самих семей),
# участка, части и академий (там служат, а не платят дань), больницы и
# университета. Отбирать у города больницу — это другая игра.
SPOTS: tuple[Spot, ...] = (
    Spot("casino", 300, "Деньги тут не считают вслух, и это удобно всем."),
    Spot("market", 220, "Место, где каждый день меняют всё на всё."),
    Spot("premium_shop", 200, "За звёзды платят те, у кого они есть."),
    Spot("northern_wall_shop", 180, "Фанатский угол: шумно, людно, прибыльно."),
    Spot("jewelry_store", 170, "Витрина, за которую стоит подраться."),
    Spot("car_dealership", 150, "Машины дорогие, а охрана ленивая."),
    Spot("pawnshop", 130, "Через комиссионку проходит всё, что плохо лежит."),
    Spot("taxi_depot", 120, "Кто держит дороги, тот знает город."),
    Spot("weapon_shop", 110, "Оружейник платит за то, чтобы его не трогали."),
    Spot("bar", 90, "Стойка, у которой слышно весь Вегас."),
)

BY_CODE: dict[str, Spot] = {one.code: one for one in SPOTS}

# Сколько всего точек в городе — число на виду, его называет экран
TOTAL_SPOTS = len(SPOTS)


def get_spot(code: str) -> Spot | None:
    return BY_CODE.get(code)


def days_held(since: int, now: int) -> int:
    """Сколько полных суток прошло с этого часа. Неполные не считаются."""
    if not since or now <= since:
        return 0
    return (now - since) // DAY_SECONDS


def payout(
    spot: Spot, since: int, now: int, share: float = 1.0
) -> tuple[int, int]:
    """Сколько точка должна и до какого часа она рассчитана.

    Возвращает пару: деньги и новый час отсчёта. Остаток суток не
    пропадает — он переезжает в следующий расчёт, поэтому долгое
    отсутствие владельца ничего ему не стоит.

    `share` — доля владельца. У города она половинная: он держит точку
    не ради денег, и платить ему как семье значило бы сделать службу
    выгоднее мафии.
    """
    days = days_held(since, now)
    if not days:
        return 0, since
    return int(spot.income * share * days), since + days * DAY_SECONDS


def final_payout(spot: Spot, since: int, now: int, share: float = 1.0) -> int:
    """Доход за неполные сутки — при смене владельца.

    Обычный расчёт считает только полные сутки: остаток переезжает в
    следующий. Но при перезахвате следующего у прежнего владельца уже
    не будет, и остаток просто пропадал бы. Держал четырнадцать часов —
    получи за четырнадцать часов.
    """
    seconds = max(0, now - since) if since else 0
    if not seconds:
        return 0
    return int(spot.income * share * seconds / DAY_SECONDS)


def share_of(kind: str) -> float:
    """Какая доля дохода достаётся владельцу этого рода."""
    return SERVICE_SHARE if kind == SERVICE else 1.0


def calm_left(fought_at: int, now: int) -> int:
    """Сколько секунд точка ещё отдыхает после боя. Ноль — можно брать."""
    if not fought_at:
        return 0
    return max(0, fought_at + CALM_SECONDS - now)


__all__ = [
    "CALM_HOURS",
    "SERVICE_SHARE",
    "final_payout",
    "share_of",
    "CALM_SECONDS",
    "CLAN",
    "DAY_SECONDS",
    "MAX_GARRISON",
    "MAX_SPOTS_PER_OWNER",
    "NOBODY",
    "NO_ONE",
    "ABANDON_HOURS",
    "ABANDON_SECONDS",
    "ARREST",
    "FREE",
    "GUARD",
    "SEIZE",
    "SERVICE",
    "abandoned",
    "action_for",
    "SPOTS",
    "TOTAL_SPOTS",
    "Owner",
    "Spot",
    "calm_left",
    "days_held",
    "get_spot",
    "owner_of_clan",
    "owner_of_service",
    "payout",
]
