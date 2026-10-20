"""Вклады и кредиты: два способа, которыми банк берёт своё.

Счёт и карта — это кошелёк (`bot/game/bank.py`). Здесь то, за что банк
и зовётся банком: он платит за чужие деньги и берёт за свои.

**Вклад: деньги лежат и растут.** От тысячи, под 13% годовых, на один,
два или три месяца. Процент простой, а не сложный: на трёх месяцах
разница между простым и сложным — меньше процента самой суммы, а
объяснять её пришлось бы абзацем.

**Срок вклада — обещание, а не замок.** Забрать деньги можно когда
угодно, но досрочно закрытый вклад процентов не приносит: банк платит за
то, что деньгами можно было распоряжаться, а не за то, что их положили.

**Кредит: деньги дают сразу, берут по частям.** Двадцать пять процентов
годовых, аннуитет — равный платёж каждый месяц, — от четырёх до
двенадцати месяцев, от десяти до ста тысяч. Выдача моментальная и
только на карту: наличными такие суммы в этом городе не носят.

**Платёж не больше недельного дохода.** Это главное условие, и считается
оно от жалованья места, а не от того, что у бойца в кармане: кредит
выдают под доход, и доход должен его выносить. Поэтому без официальной
работы кредита нет вовсе.

**Счёту не меньше недели.** Банк даёт в долг тому, кого знает хотя бы
неделю: открыть счёт и в ту же минуту взять сто тысяч — это не кредит, а
касса.

**Просрочка стоит два процента в день.** Пени считаются от непогашенного
платежа и копятся по дням: месяц молчания дороже самого платежа, и это
сказано до подписи, а не после.
"""

from __future__ import annotations

from dataclasses import dataclass

HOUR = 60 * 60
DAY = 24 * HOUR
# Месяц у банка такой же, как у аренды и подписки: тридцать суток.
# Календарных месяцев в клубе нет ни у чего
MONTH = 30 * DAY
YEAR = 12

# ---------- вклад ----------

DEPOSIT_MIN = 1_000
DEPOSIT_RATE = 0.13
DEPOSIT_MONTHS: tuple[int, ...] = (1, 2, 3)

# ---------- кредит ----------

LOAN_MIN = 10_000
LOAN_MAX = 100_000
LOAN_RATE = 0.25
LOAN_MONTHS_MIN = 4
LOAN_MONTHS_MAX = 12
LOAN_TERMS: tuple[int, ...] = tuple(
    range(LOAN_MONTHS_MIN, LOAN_MONTHS_MAX + 1)
)

# Сколько счёту должно быть, чтобы в банке начали считать кредит
LOAN_ACCOUNT_AGE = 7 * DAY

# Пени за просрочку — за каждый день
LATE_RATE = 0.02


@dataclass(frozen=True)
class Deposit:
    """Вклад: сколько положено, на сколько и когда кончается срок.

    Вкладов у бойца может быть несколько — это не кошелёк, а сделка, и
    двух одинаковых сделок банк не боится. Кредит, наоборот, один:
    платёж считается от дохода, и второй кредит ломал бы само условие.
    """

    id: int
    user_id: int
    amount: int
    months: int
    opened_at: int
    paid_until: int

    @property
    def income(self) -> int:
        """Сколько принесёт вклад, если дождаться срока."""
        return deposit_income(self.amount, self.months)

    @property
    def total(self) -> int:
        return self.amount + self.income

    def ripe(self, now: int) -> bool:
        return now >= self.paid_until

    def left(self, now: int) -> int:
        return max(0, self.paid_until - now)


@dataclass(frozen=True)
class Loan:
    """Кредит: аннуитет, срок в платежах и то, что просрочено.

    `left` — сколько платежей ещё не внесено, `due_at` — час следующего
    списания. `owed` — платежи, которых не хватило на карте: они не
    исчезают, а ждут денег и обрастают пенями. `penalty_at` — час, до
    которого пени уже начислены.
    """

    user_id: int
    amount: int
    months: int
    payment: int
    left: int
    taken_at: int
    due_at: int
    owed: int = 0
    penalty: int = 0
    penalty_at: int = 0

    @property
    def paid(self) -> int:
        """Сколько платежей внесено. Просроченные не в счёт — их не платили."""
        missed = self.owed // self.payment if self.payment else 0
        return max(0, self.months - self.left - missed)

    @property
    def body_left(self) -> int:
        """Сколько осталось выплатить по графику, без пеней."""
        return self.payment * self.left

    @property
    def late(self) -> bool:
        return self.owed > 0

    @property
    def debt(self) -> int:
        """Сколько банк хочет прямо сейчас: просрочка и пени на неё."""
        return self.owed + self.penalty


def deposit_income(amount: int, months: int) -> int:
    """Доход вклада за срок. Процент простой, остаток копейки — банку."""
    if amount <= 0 or months <= 0:
        return 0
    return int(amount * DEPOSIT_RATE * months / YEAR)


def monthly_payment(amount: int, months: int) -> int:
    """Аннуитетный платёж: равный каждый месяц, с процентами внутри.

    Формула обычная: S · i / (1 − (1 + i)^−n). Округляем вверх — иначе
    двенадцать округлённых вниз платежей не закрывают долг, и последний
    приходилось бы делать больше остальных. Равный платёж на то и
    равный, чтобы на него можно было смотреть один раз.
    """
    if amount <= 0 or months <= 0:
        return 0
    rate = LOAN_RATE / YEAR
    share = rate / (1 - (1 + rate) ** (-months))
    payment = amount * share
    whole = int(payment)
    return whole if whole == payment else whole + 1


def total_cost(amount: int, months: int) -> int:
    """Сколько боец отдаст банку за весь срок."""
    return monthly_payment(amount, months) * months


def overpay(amount: int, months: int) -> int:
    """Во сколько обойдётся сам кредит: всё минус выданное."""
    return max(0, total_cost(amount, months) - amount)


def late_penalty(owed: int, days: int) -> int:
    """Пени: два процента в день от непогашенного платежа."""
    if owed <= 0 or days <= 0:
        return 0
    return int(owed * LATE_RATE * days)


def days_between(since: int, now: int) -> int:
    """Сколько полных суток прошло. Неполные не считаются."""
    if not since or now <= since:
        return 0
    return (now - since) // DAY


def fits_income(payment: int, weekly: int) -> bool:
    """Выносит ли доход такой платёж: платёж не больше недельного дохода."""
    return bool(weekly) and payment <= weekly


def biggest_loan(weekly: int, months: int) -> int:
    """Какую сумму банк даст при таком доходе — ровно под платёж.

    Считается перебором сверху вниз по сотням: формула аннуитета
    обратима, но обратная даёт сумму с копейками, которую потом всё
    равно пришлось бы округлять вниз и проверять. Шаг в сотню — это
    то, чем в банке и считают.
    """
    if not weekly or months <= 0:
        return 0
    if not fits_income(monthly_payment(LOAN_MIN, months), weekly):
        return 0
    amount = LOAN_MIN
    while amount + 100 <= LOAN_MAX and fits_income(
        monthly_payment(amount + 100, months), weekly
    ):
        amount += 100
    return amount


def schedule(amount: int, months: int, taken_at: int) -> list[tuple[int, int]]:
    """График: по паре (час списания, платёж) на каждый месяц.

    Первое списание — ровно через месяц после выдачи, дальше месяц в
    месяц. Нужен он калькулятору и экрану: в базе лежит только час
    ближайшего платежа, остальное считается.
    """
    payment = monthly_payment(amount, months)
    return [(taken_at + MONTH * step, payment) for step in range(1, months + 1)]


__all__ = [
    "DAY",
    "DEPOSIT_MIN",
    "DEPOSIT_MONTHS",
    "DEPOSIT_RATE",
    "Deposit",
    "LATE_RATE",
    "LOAN_ACCOUNT_AGE",
    "LOAN_MAX",
    "LOAN_MIN",
    "LOAN_MONTHS_MAX",
    "LOAN_MONTHS_MIN",
    "LOAN_RATE",
    "LOAN_TERMS",
    "Loan",
    "MONTH",
    "biggest_loan",
    "days_between",
    "deposit_income",
    "fits_income",
    "late_penalty",
    "monthly_payment",
    "overpay",
    "schedule",
    "total_cost",
]
