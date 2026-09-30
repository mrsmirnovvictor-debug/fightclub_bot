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
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.models import Player

# Вкладки банка. Порядок — от того, чего у бойца ещё нет, к тому, чем он
# пользуется каждый день: пустой банк должен звать открыть счёт, а не
# показывать пустой банкомат
TABS: tuple[tuple[str, str], ...] = (
    ("new", "Новый продукт"),
    ("accounts", "Счета"),
    ("atm", "Банкомат"),
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
        # Сколько скидки боец получает прямо сейчас: выбрал мешочек —
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
        return "Выбран мешочек: скидка по карте не считается."
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


def build_bank(
    player: Player, now: int | None = None, said: str = ""
) -> dict[str, Any]:
    """Банк целиком: счёт, карта, банкомат и прайс скидок."""
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
        "purse": purse_payload(player, moment, Service.BANK),
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
                # Мешочек есть всегда, карту сначала выпускают
                "ready": code == CASH or player.has_card,
                "chosen": player.pay_from == code,
            }
            for code in (CASH, CARD)
        ],
        "said": said,
    }


__all__ = [
    "TABS",
    "account_payload",
    "build_bank",
    "card_payload",
    "discounts_payload",
    "purse_payload",
]
