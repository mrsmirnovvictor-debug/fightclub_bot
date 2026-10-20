"""Vegas Банк глазами мини-аппа: три вкладки и кошелёк на каждом прилавке.

Правил тут нет — их держат `bot.game.bank` и `bot.bank_service`. Здесь
перевод в json и одна забота сверх перевода: **чем боец платит, считает
сервер, а не страница**.

Отсюда `purse_payload`. Он уезжает в каждый экран, где есть цены, и
отвечает на один вопрос: из какого кошелька здесь уйдут деньги. Ответ
зависит от трёх вещей разом — что боец выбрал, обслужена ли карта и ходит
ли она в этом месте, — и решать это на странице значило бы, что витрина
однажды покажет цену со скидкой там, где скидки нет.

Цены на витринах приходят **уже посчитанными под выбранный кошелёк**.
Скидку считают там, где показывают цену: иначе на прилавке стояло бы одно
число, а с кошелька уходило другое.
"""

from __future__ import annotations

from typing import Any

from bot.game.bank import (
    ACCOUNT_TITLE,
    MOVE_TITLES,
    BANK_TITLE,
    CARD,
    CARD_BENEFITS,
    CARD_EMOJI,
    CARD_IMAGE,
    CARD_NOTE,
    CARD_PRICE,
    CARD_TITLE,
    CARD_YEAR_PRICE,
    CASH,
    DISCOUNTS,
    PURSE_EMOJI,
    PURSE_TITLES,
    YEAR_DAYS,
    card_number,
    card_works,
    discount_of,
    year_text,
)
from bot.game.clock import club_moment
from bot.game.health import format_term, now_ts
from bot.game.locations import Service
from bot.models import CardMove, Player

# Вкладки банка. Порядок — от того, чего у бойца ещё нет, к тому, чем он
# пользуется каждый день: пустой банк должен звать открыть счёт, а не
# показывать пустой банкомат
TABS: tuple[tuple[str, str], ...] = (
    ("new", "Новый продукт"),
    ("accounts", "Счета"),
    ("deposits", "Вклады"),
    ("loans", "Кредиты"),
    ("atm", "Банкомат"),
    ("log", "Выписка"),
)

# За какой срок показывают выписку. Тридцать дней — то, что открывается
# само: месяц это и есть «последнее время». Остальные сроки выбирают
# руками, и последний из них — без границы вовсе
STATEMENT_DAYS = 30
PERIODS: tuple[tuple[str, str, int], ...] = (
    ("week", "7 дней", 7),
    ("month", "30 дней", 30),
    ("quarter", "3 месяца", 90),
    ("year", "Год", 365),
    ("all", "Всё время", 0),
)


def purse_payload(
    player: Player, now: int | None = None, service: Service | None = None
) -> dict[str, Any]:
    """Чем боец платит здесь — и что ему на это доступно.

    Уезжает в каждый экран с ценами. `purse` — тот кошелёк, из которого
    деньги уйдут на самом деле; `prefers` — то, что боец выбрал. Они
    расходятся там, где карта не ходит или не обслужена, и страница по
    этому расхождению и объясняет бойцу, почему здесь платят наличными.
    """
    moment = now_ts() if now is None else now
    purse = player.purse_for(moment, service)
    takes_card = card_works(service)
    percent = discount_of(service)
    return {
        "purse": purse,
        "prefers": player.pay_from,
        "cash": player.credits,
        "balance": player.account_balance,
        "has_account": player.has_account,
        "has_card": player.has_card,
        "card_works": player.card_works(moment),
        # Ходит ли карта в этом месте вовсе. На рынке — нет
        "takes_card": takes_card,
        "discount": percent,
        # Сколько скидки боец получает прямо сейчас: выбрал наличные —
        # ноль, и это видно без чтения правил
        "now_off": percent if purse == CARD else 0,
        "titles": dict(PURSE_TITLES),
        "emoji": dict(PURSE_EMOJI),
        "note": _purse_note(player, moment, service, takes_card, percent),
    }


def _purse_note(
    player: Player,
    moment: int,
    service: Service | None,
    takes_card: bool,
    percent: int,
) -> str:
    """Строка под переключателем: почему здесь платят именно так."""
    if not player.has_card:
        return "Карта Vegas Банка даёт скидки в городе. Оформляется в банке."
    if not takes_card:
        return "Здесь платят только наличными: карта тут не ходит."
    if not player.card_works(moment):
        return (
            f"Карта не обслуживается: на счету нет {CARD_YEAR_PRICE} 💰 за год. "
            "Пополни счёт в банке."
        )
    if player.purse_for(moment, service) != CARD:
        return "Выбраны наличные: скидка по карте не считается."
    if percent:
        return f"Карта: −{percent}% к ценам в этом месте."
    return "Здесь банк скидки не обещал — платится полная цена."


def _choice_note(player: Player, moment: int) -> str:
    """Что сказать под переключателем в самом банке."""
    if not player.has_card:
        return "Пока платить можно только наличными: карты Vegas Банка нет."
    if not player.card_works(moment):
        return (
            f"Карта не обслуживается: на счету нет {CARD_YEAR_PRICE} 💰 за год. "
            "Пополни счёт — она заработает сама."
        )
    if player.pay_from == CARD:
        return (
            "Платим картой: в магазинах и конторах города скидка считается "
            "сама. На рынке из рук в руки всё равно идут наличные."
        )
    return "Платим наличными: скидки по карте не будет нигде."


def account_payload(player: Player) -> dict[str, Any]:
    """Счёт: номер, сколько на нём и чем он хорош."""
    return {
        "open": player.has_account,
        "title": ACCOUNT_TITLE,
        "number": player.account_number,
        "balance": player.account_balance,
        "gives": (
            "Открытие и ведение — бесплатно",
            "Наличные кладут и снимают в банкомате без комиссии",
            "Перевод на чужой счёт по номеру — без комиссии",
        ),
        "note": "Счёт один на бойца. Открывается сразу и ничего не стоит.",
    }


def card_payload(player: Player, moment: int) -> dict[str, Any]:
    """Карта: когда выпущена, до какого часа оплачена и что даёт."""
    if not player.has_card:
        return {
            "open": False,
            "title": CARD_TITLE,
            "emoji": CARD_EMOJI,
            "image": CARD_IMAGE,
            "price": CARD_PRICE,
            "year_price": CARD_YEAR_PRICE,
            "gives": list(CARD_BENEFITS),
            "note": CARD_NOTE,
        }
    works = player.card_works(moment)
    return {
        "open": True,
        "title": CARD_TITLE,
        "emoji": CARD_EMOJI,
        "image": CARD_IMAGE,
        "number": card_number(player.account_number),
        "holder": player.nickname,
        "issued": club_moment(player.card_at),
        "paid_until": year_text(player.card_paid_until),
        "works": works,
        "price": CARD_PRICE,
        "year_price": CARD_YEAR_PRICE,
        "balance": player.account_balance,
        "gives": list(CARD_BENEFITS),
        "note": CARD_NOTE,
        "state": "Действует" if works else "Не обслуживается",
    }


# Как называется место в прайсе скидок. `Service.title` для этого не
# годится: он отвечает на вопрос «зачем туда идти» — «лечиться»,
# «страховаться», — а в прайсе нужен дом, а не дело. Фанатский магазин
# зовётся по району, и в списке скидок это ничего не говорит
PLACE_TITLES: dict[Service, str] = {
    Service.CLOTHES: "Магазин одежды",
    Service.FAN: "Фанатская экипировка",
    Service.JEWEL: "Ювелирный магазин",
    Service.WEAPONS: "Оружейный магазин",
    Service.POTIONS: "Аптека",
    Service.HEAL: "Больница",
    Service.INSURANCE: "Страховая компания",
    Service.TRAIN: "Тренажёрный зал",
}


def discounts_payload() -> list[dict[str, Any]]:
    """Прайс скидок: где и сколько снимает карта.

    Собирается из той же таблицы, по которой считается цена. Списком
    руками он однажды разошёлся бы с правилами — и обещал бы то, чего
    касса не даёт.
    """
    return [
        {
            "code": service.value,
            "title": PLACE_TITLES.get(service, service.title),
            "percent": percent,
        }
        for service, percent in DISCOUNTS.items()
    ]


def statement_payload(
    moves: list[CardMove],
    days: int = STATEMENT_DAYS,
    income: int = 0,
    spent: int = 0,
    since: int = 0,
    until: int = 0,
    now: int | None = None,
) -> dict[str, Any]:
    """Выписка по карте: строки за срок и два числа под ними.

    Срок приходит уже посчитанным: сколько дней показывать, решает
    страница, а какие строки в них попали — база. Здесь только перевод
    в то, что умеет нарисовать экран.
    """
    moment = now_ts() if now is None else now
    return {
        "days": days,
        "periods": [
            {"code": code, "title": title, "days": span, "on": span == days}
            for code, title, span in PERIODS
        ],
        # Свой срок: если он задан, ни один из готовых не подсвечен
        "since": club_moment(since) if since else "",
        "until": club_moment(until) if until else "",
        "custom": bool(since or until),
        "rows": [
            {
                "when": club_moment(one.at),
                "amount": one.amount,
                "plus": one.amount > 0,
                "kind": one.kind,
                "kind_title": MOVE_TITLES.get(one.kind, "Операция"),
                "note": one.note,
                # За что платили. У старых строк пусто — экран их рисует
                # одним заголовком, как рисовал до комментария
                "about": one.about,
                "balance": one.balance,
            }
            for one in moves
        ],
        "income": income,
        "spent": spent,
        "note": (
            "По карте за этот срок ничего не проходило."
            if not moves
            else "Наличные в выписку не попадают: банк видит только счёт."
        ),
        "now": club_moment(moment),
    }


def deposit_row(one, moment: int) -> dict[str, Any]:
    """Вклад на экране: сколько, до какого часа и что принесёт."""
    left = one.left(moment)
    return {
        "id": one.id,
        "amount": one.amount,
        "months": one.months,
        "income": one.income,
        "total": one.total,
        "opened": club_moment(one.opened_at) if one.opened_at else "",
        "until": club_moment(one.paid_until) if one.paid_until else "",
        "ripe": one.ripe(moment),
        "left": left,
        "left_text": format_term(left) if left else "",
    }


def loan_row(loan, moment: int) -> dict[str, Any]:
    """Кредит на экране: график, долг и пени."""
    from bot.game.credit import days_between, late_penalty

    if loan is None:
        return {}
    days = days_between(loan.penalty_at, moment) if loan.late else 0
    penalty = loan.penalty + late_penalty(loan.owed, days)
    left = max(0, loan.due_at - moment)
    return {
        "amount": loan.amount,
        "months": loan.months,
        "payment": loan.payment,
        "paid": loan.paid,
        "pays_left": loan.left,
        "body_left": loan.body_left,
        "taken": club_moment(loan.taken_at) if loan.taken_at else "",
        "due": club_moment(loan.due_at) if loan.due_at else "",
        "due_in": left,
        "due_text": format_term(left) if left else "",
        "late": loan.late,
        "owed": loan.owed,
        "penalty": penalty,
        "debt": loan.owed + penalty,
        "late_days": days,
    }


def credit_payload(
    player: Player,
    moment: int,
    deposits: list | None = None,
    loan=None,
) -> dict[str, Any]:
    """Вклады и кредиты: что открыто, что можно открыть и почём.

    Калькулятор считается здесь: у страницы нет ни ставки, ни формулы
    аннуитета, и повторять их на клиенте значило бы однажды разойтись с
    банком в цене. Страница рисует таблицу, которую ей дали.
    """
    from bot.credit_service import weekly_income, why_not
    from bot.game.credit import (
        DEPOSIT_MIN,
        DEPOSIT_MONTHS,
        DEPOSIT_RATE,
        LATE_RATE,
        LOAN_ACCOUNT_AGE,
        LOAN_MAX,
        LOAN_MIN,
        LOAN_RATE,
        LOAN_TERMS,
        biggest_loan,
        deposit_income,
        monthly_payment,
        overpay,
        total_cost,
    )

    rows = [deposit_row(one, moment) for one in (deposits or [])]
    weekly = weekly_income(player)
    denial = why_not(player, moment)
    # Потолок суммы на каждый срок считается перебором, и считать его
    # по четыре раза на строку было бы расточительством: один раз на срок
    tops = {months: biggest_loan(weekly, months) for months in LOAN_TERMS}
    top = tops[LOAN_TERMS[-1]]
    calc = []
    for months in LOAN_TERMS:
        fits = tops[months]
        # Чего не дадут, то считают по минимальной сумме: строка всё
        # равно показывает платёж, от которого банк и отказался
        amount = fits or LOAN_MIN
        calc.append(
            {
                "months": months,
                "min": LOAN_MIN,
                "min_payment": monthly_payment(LOAN_MIN, months),
                "fits": bool(fits),
                "top": fits,
                "payment": monthly_payment(amount, months),
                "total": total_cost(amount, months),
                "overpay": overpay(amount, months),
            }
        )
    return {
        "deposit": {
            "min": DEPOSIT_MIN,
            "rate": round(DEPOSIT_RATE * 100),
            "terms": [
                {
                    "months": months,
                    # Что принесёт самый малый вклад на этот срок: по
                    # нему и сравнивают сроки
                    "income": deposit_income(DEPOSIT_MIN, months),
                }
                for months in DEPOSIT_MONTHS
            ],
            "balance": player.account_balance,
            "can_open": bool(
                player.has_account and player.account_balance >= DEPOSIT_MIN
            ),
            "rows": rows,
            "total": sum(one["amount"] for one in rows),
        },
        "loan": {
            "min": LOAN_MIN,
            "max": LOAN_MAX,
            "rate": round(LOAN_RATE * 100),
            "late_rate": round(LATE_RATE * 100),
            "terms": list(LOAN_TERMS),
            "week_days": LOAN_ACCOUNT_AGE // (24 * 60 * 60),
            "weekly": weekly,
            # Сколько банк даст при этом доходе на самый долгий срок:
            # потолок считает он, а не страница
            "top": top,
            "why": denial,
            "can_take": bool(not denial and not loan and top),
            "mine": loan_row(loan, moment),
            # Калькулятор: по строке на срок. В каждой — платёж за
            # самый малый кредит, потолок суммы при этом доходе и то,
            # выносит ли доход такой платёж вообще. Считает банк:
            # повторять формулу аннуитета на странице значило бы
            # однажды разойтись с ним в цене
            "calc": calc,
        },
    }


def build_bank(
    player: Player,
    now: int | None = None,
    said: str = "",
    statement: dict[str, Any] | None = None,
    deposits: list | None = None,
    loan=None,
) -> dict[str, Any]:
    """Банк целиком: счёт, карта, банкомат, выписка и прайс скидок.

    `statement` собирают снаружи: за строками выписки надо в базу, а
    этот сбор в базу не ходит. Пусто — выписки нет, и вкладка покажет
    пустой список: это честнее, чем прятать её у того, у кого карта есть.
    """
    moment = now_ts() if now is None else now
    return {
        "title": BANK_TITLE,
        "tabs": [{"code": code, "title": title} for code, title in TABS],
        "credits": player.credits,
        "account": account_payload(player),
        # Не «card»: этим ключом во всех экранах едет карточка бойца, и
        # банковская карта его затирала. Одно слово, два разных предмета
        "bank_card": card_payload(player, moment),
        "discounts": discounts_payload(),
        "statement": statement if statement is not None else statement_payload([]),
        "purse": purse_payload(player, moment, Service.BANK),
        # Вклады и кредиты: то, за что банк и зовётся банком
        "credit": credit_payload(player, moment, deposits, loan),
        # Переключатель в банке — про настройку, а не про этот дом. «Здесь
        # банк скидки не обещал» на его собственном экране читалось бы
        # как отказ, хотя речь о том, чем платить в городе
        "choice_note": _choice_note(player, moment),
        "year_days": YEAR_DAYS,
        "purses": [
            {
                "code": code,
                "title": PURSE_TITLES[code],
                "emoji": PURSE_EMOJI[code],
                "money": player.purse_money(code),
                # Наличные есть всегда, карту сначала выпускают
                "ready": code == CASH or player.has_card,
                "chosen": player.pay_from == code,
            }
            for code in (CASH, CARD)
        ],
        "said": said,
    }


__all__ = [
    "PERIODS",
    "STATEMENT_DAYS",
    "TABS",
    "account_payload",
    "build_bank",
    "card_payload",
    "discounts_payload",
    "purse_payload",
    "statement_payload",
]
