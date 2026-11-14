"""Подписки клуба: PRO и PREMIUM — что дают, сколько стоят и как долго.

Подписка — единственное в клубе, что продаётся на время. Всё остальное за
звёзды покупается навсегда, поэтому её правила собраны отдельно и целиком
здесь: сроки, цены, акция и состав каждой ступени.

**Ступеней две, и они не складываются.** PRO — опыт, аналитик, зал,
скидка и вторая машина. PREMIUM — то же, но опыт двойной, скидка вдвое
больше, машин три и полис держится сам. Боец может держать обе сразу
(продлевал PRO, потом взял PREMIUM), и тогда работает старшая: ступень —
это не сумма услуг, а уровень обслуживания.

**Подписка не даёт вещей — ни снаряжения, ни внешности.** Раньше вместе с
PRO приходили клинок ассасина и его образ; и то и другое оставалось у
бойца навсегда, то есть подписка продавала вечное за месячную цену.
Теперь обе ступени дают только то, что кончается вместе со сроком:
знание и услуги.

У тех, кому клинок и образ уже выдали, они остаются. Отбирать выданное
хуже, чем один раз выдать лишнее.

**Списки короткие намеренно.** Строки по одному предложению и без
оговорок: такой список читают целиком, а с оговорками не читает никто, и
подписка выглядит сложнее, чем она есть. Значок у имени в список не идёт
вовсе: его видно и так, а строкой он занимает место наравне с аналитиком.

**Подписка держит услуги, а не выдаёт их месяцами.** И полис, и абонемент
в зал живут ровно столько, сколько жива подписка: кончилась — кончились и
они. Месяц, выданный подпиской, складывался бы сам с собой сколько
угодно раз и переживал бы её.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from bot.game.art import SUBSCRIPTIONS

DAY = 24 * 60 * 60

# ---------- коды ступеней ----------

PRO = "pro"
PREMIUM = "premium"

# Значки у имени. В сообщениях Телеграма идут эмодзи: картинку в строку с
# именем не вставить, а значок висит везде, где боец назван по имени
PRO_BADGE = "💎"
PREMIUM_BADGE = "👑"

# Обычные условия
PRO_STARS = 250
PRO_DAYS = 30
PREMIUM_STARS = 500
PREMIUM_DAYS = 30

# Опыт за бой: PRO идёт в полтора, PREMIUM — вдвое
PRO_EXP_SHARE = 1.5
PREMIUM_EXP_SHARE = 2.0

# Скидка в городе, при оплате картой. Сама карта даёт пять процентов и
# без подписки; подписка поднимает их до своей ступени, а не прибавляет
# к ним свои: лестница, а не сумма
PRO_DISCOUNT = 10
PREMIUM_DISCOUNT = 20

# Машин на руках: одна у всех, две по PRO, три по PREMIUM
PRO_CARS = 2
PREMIUM_CARS = 3

# Клинок и образ, которые подписка выдавала прежде. Не выдаются больше
# никому; коды остались ровно для одного — вернуть их при возврате звёзд
# за ту старую покупку. Отбирать их у тех, кто просто владеет ими, мы не
# собираемся, а вот «забрал звёзды — вернул и то, что они принесли»
# должно продолжать работать и для покупок до этой правки
LEGACY_ITEM = "hidden_blade"
LEGACY_LOOK = "assassin"

# ---------- акция ----------
#
# До 1 сентября подписка достаётся даром, но на неделю вместо месяца. Час
# указан в UTC: полночь первого сентября по Москве — это 21:00 UTC 31 августа.
#
# Акция была и остаётся только у PRO: бесплатный вход в клуб один, и
# ведёт он на нижнюю ступень.
PROMO_UNTIL = datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc)
PROMO_STARS = 0
PROMO_DAYS = 7


@dataclass(frozen=True)
class Tier:
    """Ступень подписки: чем она отличается от бесплатного бойца."""

    code: str
    title: str
    emoji: str
    badge: str
    picture: str  # файл значка для мини-аппа
    stars: int
    days: int
    exp_share: float
    discount: int  # процентов в городе, при оплате картой
    cars: int
    policy: bool  # держит ли страховой полис
    rank: int  # чем больше, тем старше ступень
    benefits: tuple[str, ...] = field(default_factory=tuple)

    @property
    def image(self) -> str:
        return f"{SUBSCRIPTIONS}/{self.picture}"

    @property
    def short(self) -> str:
        """Одно слово: «PRO», «PREMIUM». Для кнопок и тесных строк.

        «Продлить Подписка PRO» на кнопке не читается, а падежей у
        названия нет: короткое имя избавляет и от того, и от другого.
        """
        return self.title.removeprefix("Подписка ")

    @property
    def seconds(self) -> int:
        return self.days * DAY


PRO_TIER = Tier(
    code=PRO,
    title="Подписка PRO",
    emoji=PRO_BADGE,
    badge=PRO_BADGE,
    picture="pro.webp",
    stars=PRO_STARS,
    days=PRO_DAYS,
    exp_share=PRO_EXP_SHARE,
    discount=PRO_DISCOUNT,
    cars=PRO_CARS,
    policy=False,
    rank=1,
    benefits=(
        "150% опыта за бой",
        "Аналитик-помощник во время боя",
        "Абонемент в тренажёрный зал на всё время подписки",
        f"Скидка {PRO_DISCOUNT}% в городе при оплате картой",
        f"{PRO_CARS} машины на руках вместо одной",
    ),
)

PREMIUM_TIER = Tier(
    code=PREMIUM,
    title="Подписка PREMIUM",
    emoji=PREMIUM_BADGE,
    badge=PREMIUM_BADGE,
    picture="premium.webp",
    stars=PREMIUM_STARS,
    days=PREMIUM_DAYS,
    exp_share=PREMIUM_EXP_SHARE,
    discount=PREMIUM_DISCOUNT,
    cars=PREMIUM_CARS,
    policy=True,
    rank=2,
    benefits=(
        "200% опыта за бой",
        "Аналитик-помощник во время боя",
        "Страховка жизни и здоровья подключается автоматически",
        "Абонемент в тренажёрный зал на всё время подписки",
        f"Скидка {PREMIUM_DISCOUNT}% в городе при оплате картой",
        f"{PREMIUM_CARS} машины на руках вместо одной",
    ),
)

TIERS: tuple[Tier, ...] = (PRO_TIER, PREMIUM_TIER)
TIER_BY_CODE: dict[str, Tier] = {one.code: one for one in TIERS}


def get_tier(code: str | None) -> Tier | None:
    return TIER_BY_CODE.get(code or "")


def tier_of(rank: int) -> Tier | None:
    """Ступень по её номеру. Ноль — подписки нет."""
    for one in TIERS:
        if one.rank == rank:
            return one
    return None


# Состав бесплатного бойца — для таблицы сравнения на экране подписки.
# Отдельной ступенью он не заведён: у него нет ни срока, ни цены
FREE_EXP_SHARE = 1.0
FREE_DISCOUNT = 0
FREE_CARS = 1


@dataclass(frozen=True)
class ProOffer:
    """Условия, на которых подписку берут прямо сейчас."""

    stars: int
    days: int
    promo: bool = False
    tier: Tier = PRO_TIER

    @property
    def free(self) -> bool:
        """Даром: счёт в звёздах выставлять не за что."""
        return self.stars <= 0

    @property
    def seconds(self) -> int:
        return self.days * DAY

    @property
    def price_text(self) -> str:
        return "бесплатно" if self.free else f"{self.stars} ⭐"

    @property
    def term_text(self) -> str:
        return f"{self.days} дней" if self.days != 30 else "месяц"


def promo_is_on(now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    return moment < PROMO_UNTIL


def paid_offer(tier: Tier | str = PRO_TIER) -> ProOffer:
    """Обычные условия: месяц за звёзды.

    Акция их не трогает. Она — единственный бесплатный вход в клуб, и берут
    её один раз; продлевают подписку всегда за звёзды.
    """
    one = get_tier(tier) if isinstance(tier, str) else tier
    one = one or PRO_TIER
    return ProOffer(stars=one.stars, days=one.days, tier=one)


def promo_offer() -> ProOffer:
    return ProOffer(
        stars=PROMO_STARS, days=PROMO_DAYS, promo=True, tier=PRO_TIER
    )


def current_offer(
    now: datetime | None = None, tier: Tier | str = PRO_TIER
) -> ProOffer:
    """Что предлагают прямо сейчас: во время акции — неделю даром.

    Акция только у PRO: на PREMIUM бесплатного входа нет и не было.
    """
    one = get_tier(tier) if isinstance(tier, str) else tier
    one = one or PRO_TIER
    if one.code == PRO and promo_is_on(now):
        return promo_offer()
    return paid_offer(one)


def rank_for(pro_until: int, premium_until: int, now: int) -> int:
    """Старшая живая ступень по двум срокам. 0 — подписки нет.

    Нужна там, где бойца целиком не загружают: в составе клана и в
    списке заявок лежат только сроки.
    """
    if premium_until > now:
        return PREMIUM_TIER.rank
    if pro_until > now:
        return PRO_TIER.rank
    return 0


def badge_for(pro_until: int, premium_until: int, now: int) -> str:
    """Значок по двум срокам. Пусто — подписки нет."""
    one = tier_of(rank_for(pro_until, premium_until, now))
    return one.badge if one else ""


def exp_share(rank: int) -> float:
    """Множитель опыта этой ступени. Ноль — подписки нет, значит один."""
    one = tier_of(rank)
    return one.exp_share if one else FREE_EXP_SHARE


def cars_of(rank: int) -> int:
    """Сколько машин держит боец на этой ступени."""
    one = tier_of(rank)
    return one.cars if one else FREE_CARS


def discount_of_tier(rank: int) -> int:
    """Скидка этой ступени в городе. Ноль — своей скидки у ступени нет."""
    one = tier_of(rank)
    return one.discount if one else FREE_DISCOUNT


def holds_policy(rank: int) -> bool:
    """Держит ли эта ступень страховой полис."""
    one = tier_of(rank)
    return bool(one and one.policy)


# Для совместимости с экранами, которые знают про одну подписку: там, где
# раньше стояло «подписка», подразумевалась PRO
TITLE = PRO_TIER.title
EMOJI = PRO_TIER.emoji
IMAGE = PRO_TIER.image
BENEFITS: tuple[str, ...] = PRO_TIER.benefits

# Оговорок под списком нет. Всё, что стоило сказать, сказано строкой
# цены: столько-то звёзд за столько-то дней.
NOTE = ""


def promo_note(offer: ProOffer, claimed: bool = False) -> str:
    """Строка про акцию — пусто, когда акции нет."""
    if not offer.promo:
        return ""
    if claimed:
        return (
            f"Бесплатную неделю ты уже забрал: она даётся один раз. "
            f"Дальше подписка продлевается за {PRO_STARS} ⭐ на {PRO_DAYS} дней."
        )
    return (
        f"🔥 До 1 сентября первая подписка достаётся даром — на {offer.days} "
        f"дней вместо месяца. Один раз на бойца; продление — "
        f"{PRO_STARS} ⭐ за {PRO_DAYS} дней."
    )


__all__ = [
    "BENEFITS",
    "DAY",
    "EMOJI",
    "FREE_CARS",
    "FREE_DISCOUNT",
    "FREE_EXP_SHARE",
    "IMAGE",
    "LEGACY_ITEM",
    "LEGACY_LOOK",
    "NOTE",
    "PREMIUM",
    "PREMIUM_BADGE",
    "PREMIUM_CARS",
    "PREMIUM_DAYS",
    "PREMIUM_DISCOUNT",
    "PREMIUM_EXP_SHARE",
    "PREMIUM_STARS",
    "PREMIUM_TIER",
    "PRO",
    "PRO_BADGE",
    "PRO_CARS",
    "PRO_DAYS",
    "PRO_DISCOUNT",
    "PRO_EXP_SHARE",
    "PRO_STARS",
    "PRO_TIER",
    "PROMO_DAYS",
    "PROMO_STARS",
    "PROMO_UNTIL",
    "TIERS",
    "TIER_BY_CODE",
    "TITLE",
    "ProOffer",
    "Tier",
    "badge_for",
    "cars_of",
    "current_offer",
    "discount_of_tier",
    "exp_share",
    "get_tier",
    "holds_policy",
    "paid_offer",
    "promo_is_on",
    "promo_note",
    "promo_offer",
    "rank_for",
    "tier_of",
]
