"""Промокоды: слово, которое превращается в подписку.

Акция по дате (`promo_is_on` в `bot.game.pro`) и промокод — разные вещи,
и живут они раздельно. Акция открыта всем, кто успел: её не надо знать,
её видно на прилавке. Код знать надо — его называют там, где игра
встречается с игроком снаружи: в объявлении о старте, в ролике, в
рассылке. Поэтому акция сама себя показывает, а код сам себя не
показывает никогда: на прилавке стоит пустое поле, и что в него вводить,
знает только тот, кому сказали.

**Код даётся один раз на бойца.** Не на одного человека на весь город и
не без счёта: первое превратило бы раздачу в гонку, второе — в кнопку
«ещё месяц», которую жмут до скончания времён. Защита — запись в журнале
покупок с уникальным ключом, а не проверка перед выдачей: два быстрых
нажатия подряд не проскочат (см. `redeem_code` в `bot.pro_service`).

**Срок у кода свой.** `until` пустой — код работает, пока его отсюда не
уберут. Это осознанно: у раздачи на старте нет заранее известного конца,
а дата, выставленная наугад, однажды отключит код посреди рекламы.

**Пишут код как придётся.** Строчными, с пробелами, через дефис, с
кириллическими буквами, похожими на латинские, — всё это один и тот же
код. Отказ «такого кода нет» в ответ на верный код, набранный в другой
раскладке, игрок прочтёт как обман, а не как опечатку.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from bot.game.pro import PRO, PRO_DAYS, PRO_TIER, ProOffer, Tier, get_tier

# Кириллические буквы, неотличимые от латинских на вид. Набранный в
# другой раскладке код выглядит для игрока точно так же, как верный
LOOKALIKE = str.maketrans(
    "АВЕКМНОРСТУХаеорсху",
    "ABEKMHOPCTYXaeopcxy",
)

# Что выбрасываем из набранного: пробелы и чёрточки ставят для красоты
JUNK = " \t -–—_"


@dataclass(frozen=True)
class PromoCode:
    """Код и то, во что он превращается."""

    code: str
    days: int = PRO_DAYS
    tier: str = PRO
    until: datetime | None = None

    @property
    def tier_obj(self) -> Tier:
        return get_tier(self.tier) or PRO_TIER

    def is_on(self, now: datetime | None = None) -> bool:
        if self.until is None:
            return True
        moment = now or datetime.now(timezone.utc)
        return moment < self.until

    def offer(self) -> ProOffer:
        """Условия выдачи: та же оферта, что у покупки, только даром."""
        return ProOffer(stars=0, days=self.days, promo=True, tier=self.tier_obj)

    @property
    def term_text(self) -> str:
        return "месяц" if self.days == 30 else f"{self.days} дней"


# Действующие коды. Отключить код — убрать его отсюда: он перестанет
# работать с ближайшим выкатом, а у тех, кто успел, подписка останется
CODES: tuple[PromoCode, ...] = (
    # Раздача на старте большого набора: месяц PRO каждому, кто назвал
    PromoCode(code="VEGAS1334", days=PRO_DAYS, tier=PRO),
)


def normalize(typed: str) -> str:
    """Привести набранное к виду кода: верхний регистр, без мусора."""
    folded = (typed or "").translate(LOOKALIKE)
    return "".join(one for one in folded if one not in JUNK).upper()


def find(typed: str, now: datetime | None = None) -> PromoCode | None:
    """Найти действующий код. Нет такого или истёк — пусто."""
    wanted = normalize(typed)
    if not wanted:
        return None
    for one in CODES:
        if normalize(one.code) == wanted and one.is_on(now):
            return one
    return None


def known(typed: str) -> bool:
    """Есть ли такой код вообще — хотя бы и отработавший свой срок.

    Нужно, чтобы отличить «такого кода нет» от «код больше не
    действует»: второе игрок может исправить, попросив новый.
    """
    wanted = normalize(typed)
    return bool(wanted) and any(normalize(one.code) == wanted for one in CODES)


__all__ = ["CODES", "PromoCode", "find", "known", "normalize"]
