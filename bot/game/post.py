"""Почта: посылки между бойцами, наложенный платёж и срок хранения.

Почта — третий способ отдать вещь другому бойцу, и у него своё место в
городе. Рынок — это стол: оба стоят рядом и меняются из рук в руки.
Комиссионка — прилавок: вещь кладут, её покупает кто угодно. Почта —
адрес: вещь уходит **конкретному бойцу**, и ждёт его столько, сколько
отправитель назначил.

**Отправка стоит пять кредитов** и не возвращается ни при каких раскладах:
посылку несли, хранили и везли обратно — работа сделана.

**Наложенный платёж.** Отправитель может назначить цену: получатель
заплатит её на почте, и деньги уйдут отправителю. Нуль — посылка даром,
подарок. Цену платит тот, кто забирает, и платит из своего кошелька:
не хватило — посылка лежит дальше.

**Срок хранения назначает отправитель**: пять дней, тридцать дней или до
востребования. Срок вышел — посылка едет обратно, и ждёт отправителя уже
без всякого срока. Вещь не пропадает никогда: она либо у получателя,
либо на полке, либо возвращается тому, кто её послал.

Правила здесь, действия — в `bot.post_service`, хранение — в
`bot.database`, экран — в `bot.webapp.post`.
"""

from __future__ import annotations

from dataclasses import dataclass

DAY = 24 * 60 * 60

# Сколько стоит отправить посылку. Цена одна на любую вещь: почта берёт
# за дорогу, а не за содержимое
SEND_PRICE = 5

# Наложенный платёж: ноль — подарок, больше — цена, которую заплатит
# получатель. Потолок стоит затем, чтобы в поле нельзя было написать
# число, которого в клубе не бывает
MAX_COD = 1_000_000

# Сколько посылок может ждать одного бойца. Без потолка почта
# превращается в способ завалить чужой экран сотней наложенных платежей
MAX_WAITING = 10


@dataclass(frozen=True)
class Term:
    """Срок хранения: как называется и сколько держат."""

    code: str
    title: str
    days: int  # 0 — до востребования, без срока

    @property
    def seconds(self) -> int:
        return self.days * DAY

    def until(self, now: int) -> int:
        """До какого часа лежит посылка. 0 — до востребования."""
        return now + self.seconds if self.days else 0


TERMS: tuple[Term, ...] = (
    Term("short", "5 дней", 5),
    Term("long", "30 дней", 30),
    Term("demand", "До востребования", 0),
)

DEFAULT_TERM = TERMS[1].code


def get_term(code: str) -> Term | None:
    return next((one for one in TERMS if one.code == code), None)


def term_of(keep_until: int, sent_at: int = 0) -> Term:
    """Каким сроком послана посылка. Нужен для подписи в списке."""
    if not keep_until:
        return TERMS[-1]
    days = round((keep_until - sent_at) / DAY) if sent_at else 0
    return next((one for one in TERMS if one.days == days), TERMS[1])


@dataclass(frozen=True)
class Parcel:
    """Посылка на полке: от кого, кому, что внутри и до какого часа.

    `back` — посылка едет обратно: срок вышел или получатель отказался.
    Такую забирает отправитель, и уже даром: наложенный платёж с
    возврата не берут, за дорогу уже заплачено.
    """

    id: int
    from_id: int
    to_id: int
    code: str
    wear: int = 0
    max_wear: int = 0
    mod: str = ""
    mod_value: int = 0
    price: int = 0  # наложенный платёж, 0 — подарок
    sent_at: int = 0
    keep_until: int = 0  # 0 — до востребования
    back: bool = False
    from_name: str = ""
    to_name: str = ""

    @property
    def owner(self) -> int:
        """Кто может забрать посылку прямо сейчас."""
        return self.from_id if self.back else self.to_id

    @property
    def due(self) -> int:
        """Сколько платить при получении. С возврата не берут."""
        return 0 if self.back else self.price

    def expired(self, now: int) -> bool:
        """Пора ли отправлять посылку обратно."""
        return bool(self.keep_until) and now >= self.keep_until and not self.back

    def seconds_left(self, now: int) -> int:
        """Сколько посылке лежать. 0 — срока нет или он вышел."""
        if not self.keep_until:
            return 0
        return max(0, self.keep_until - now)

    def term(self) -> Term:
        return term_of(self.keep_until, self.sent_at)


def price_problem(price: int) -> str:
    """Что не так с наложенным платежом. Пусто — всё в порядке."""
    if price < 0:
        return "Наложенный платёж не бывает отрицательным."
    if price > MAX_COD:
        return f"Наложенный платёж — не больше {MAX_COD} 💰."
    return ""


__all__ = [
    "DAY",
    "DEFAULT_TERM",
    "MAX_COD",
    "MAX_WAITING",
    "SEND_PRICE",
    "TERMS",
    "Parcel",
    "Term",
    "get_term",
    "price_problem",
    "term_of",
]
