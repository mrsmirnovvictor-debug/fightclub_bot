"""Казино VEGAS: правила четырёх игр и ничего кроме правил.

Тут нет ни базы, ни времени, ни игроков — только то, по чему считается
исход: порядок секторов колеса, выплаты за кости, достоинство карт и
решения крупье. Служба `bot/casino_service.py` берёт отсюда ответы, а
хранит и платит сама.

Почему врозь. Выплата — единственное место, где игра встречается с
деньгами, и проверять её надо не прогоном партии, а таблицей: все 37
карманов рулетки, все 36 пар костей, два туза и мягкие 17. Когда
правила лежат отдельным модулем, такая проверка пишется в три строки
и не тянет за собой ни сервера, ни сети.

Счёт везде целыми фишками. Дробной фишки в казино нет: «×2,3» — это
46 за пакет в 20, а не умножение с округлением на каждом шаге.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from bot.game.clock import MOSCOW

# ---------- общий курс ----------
#
# Фишка и кредит равны. Курса как такового нет, и это выбор: казино,
# которое меняет деньги по своему курсу, игрок считает обманом ещё до
# первой ставки. Поэтому меняют один к одному в обе стороны, без
# комиссии, и единственное, что отличает фишку от кредита, — за неё
# нельзя купить ничего, кроме игры.
CHIP_PER_CREDIT = 1

GAMES: tuple[str, ...] = ("dice", "roulette", "highcard", "blackjack")


# Сколько идёт круг. Одно число на все столы, и это решение, а не
# лень: у игрока за всеми четырьмя столами одни часы, и шкала ожидания
# всюду одинаковой длины. Разные окна — 12 секунд у костей, 8 у карт —
# читались на экране как сбоящий таймер, потому что у ленивого стола
# срок считается от первого взгляда, а не от общих часов.
#
# Полминуты — по плейтесту. Две минуты между партиями казино не
# оживляют, а растягивают: за столом сидят ради следующего броска, а
# не ради ожидания.
ROUND_SECONDS = 30

# Часы казино по Москве: открывается в восемь утра, закрывается в два
# ночи. Шесть часов под замком — не придирка к игроку, а то, что
# отличает заведение от автомата: у дома есть свои часы, и ночью он
# пустеет, как всякий зал в городе.
OPEN_HOUR = 8
SHUT_HOUR = 2


@dataclass(frozen=True)
class Game:
    """Стол: как называется, сколько мест и чем на нём платят."""

    code: str
    title: str
    about: str
    emoji: str
    seats: int
    least: int  # наименьшая ставка
    most: int  # наибольшая ставка
    step: int  # кратность ставки
    cover: str  # обложка стола в бакете
    accept: int = ROUND_SECONDS  # сколько секунд принимают ставки


GAME_LIST: tuple[Game, ...] = (
    Game(
        "dice",
        "Кости «Семёрка»",
        "Один бросок, три исхода.",
        "🎲",
        seats=4,
        least=20,
        most=2000,
        step=20,
        cover="seven.webp",
    ),
    Game(
        "roulette",
        "Европейская рулетка",
        "Общее колесо, личные ставки.",
        "🔴",
        seats=4,
        least=5,
        most=1000,
        step=5,
        cover="roulette.webp",
    ),
    Game(
        "highcard",
        "Старшая карта",
        "Равные взносы, общий банк.",
        "🃏",
        seats=4,
        least=50,
        most=50,
        step=50,
        cover="high-card.webp",
    ),
    Game(
        "blackjack",
        "Мини-блэкджек до 21",
        "Своя рука против крупье.",
        "♠️",
        seats=4,
        least=20,
        most=1000,
        step=2,
        cover="blackjack.webp",
    ),
)

GAME_BY_CODE = {one.code: one for one in GAME_LIST}


def open_now(moment: float) -> bool:
    """Открыто ли казино в этот час."""
    hour = datetime.fromtimestamp(moment, MOSCOW).hour
    return hour >= OPEN_HOUR or hour < SHUT_HOUR


def opens_at(moment: float) -> int:
    """Когда откроется. Если открыто — когда закроется.

    Одно число на оба случая: экрану нужно сказать либо «откроется в
    08:00», либо «работаем до 02:00», и считается это одинаково — по
    ближайшей границе московских суток.
    """
    here = datetime.fromtimestamp(moment, MOSCOW)
    hour = OPEN_HOUR if not open_now(moment) else SHUT_HOUR
    edge = here.replace(hour=hour, minute=0, second=0, microsecond=0)
    if edge <= here:
        edge += timedelta(days=1)
    return int(edge.timestamp())


def cover_url(code: str) -> str:
    """Адрес обложки стола."""
    from bot.game import art

    found = GAME_BY_CODE.get(code)
    return f"{art.CASINO_COVERS}/{found.cover}" if found else ""


def game(code: str) -> Game:
    """Стол по коду. Нет такого — ошибка на входе, а не пустая игра."""
    found = GAME_BY_CODE.get(code)
    if found is None:
        raise KeyError(code)
    return found


# ---------- рулетка ----------
#
# Порядок секторов настоящего европейского колеса. Он тут не для
# красоты: угол остановки диска считается по месту кармана в этом
# кольце, а не по «число × шаг». Напротив числа 13 на колесе стоит 36,
# а не 14, и если считать шагом, шарик сядет не туда, куда показывает
# подсветка.
WHEEL: tuple[int, ...] = (
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23,
    10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
)

REDS: frozenset[int] = frozenset(
    {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
)

# Поля стола: код поля → во сколько раз возвращается ставка. Начисление
# включает саму ставку, поэтому «×2» на красном — это вернуть ставку и
# столько же сверху, а чистый выигрыш вдвое меньше начисления.
ROULETTE_ODDS = {
    "number": 36,
    "color": 2,
    "parity": 2,
    "half": 2,
    "dozen": 3,
}

ROULETTE_SPOTS: dict[str, tuple[str, ...]] = {
    "color": ("red", "black"),
    "parity": ("even", "odd"),
    "half": ("low", "high"),
    "dozen": ("1", "2", "3"),
}

SPOT_TITLES = {
    "red": "Красное",
    "black": "Чёрное",
    "even": "Чёт",
    "odd": "Нечет",
    "low": "1–18",
    "high": "19–36",
    "1": "1–12",
    "2": "13–24",
    "3": "25–36",
}


def colour_of(number: int) -> str:
    """Цвет кармана. У зеро цвета нет вовсе, и это важно для ставок."""
    if number == 0:
        return "zero"
    return "red" if number in REDS else "black"


def pocket_angle(number: int) -> float:
    """Куда повернуть диск, чтобы этот карман оказался наверху.

    Считается по месту числа в кольце `WHEEL`, а не по самому числу:
    карманы на колесе идут не по порядку.
    """
    index = WHEEL.index(number)
    return 360.0 * index / len(WHEEL)


def roulette_wins(spot: str, pick: str, number: int) -> bool:
    """Выигрывает ли это поле при таком кармане.

    При зеро выигрывает только ставка на 0 — у него нет ни цвета, ни
    чётности, ни половины, ни дюжины. Это не поблажка казино, а то, на
    чём держится весь его доход: без зеро возврат всех полей был бы
    ровно сто процентов.
    """
    if spot == "number":
        return str(number) == pick
    if number == 0:
        return False
    if spot == "color":
        return colour_of(number) == pick
    if spot == "parity":
        return ("even" if number % 2 == 0 else "odd") == pick
    if spot == "half":
        return ("low" if number <= 18 else "high") == pick
    if spot == "dozen":
        return str((number - 1) // 12 + 1) == pick
    return False


def roulette_pay(bets: Iterable[dict], number: int) -> int:
    """Начисление по всем ставкам игрока разом.

    Складываются все выигравшие поля: десять на красное и пять на
    семёрку при красной семёрке дают 20 и 180, то есть 200 начисления
    при 15 ставки.
    """
    total = 0
    for bet in bets:
        spot = str(bet.get("spot") or "")
        pick = str(bet.get("pick") or "")
        stake = int(bet.get("amount") or 0)
        if stake > 0 and roulette_wins(spot, pick, number):
            total += stake * ROULETTE_ODDS[spot]
    return total


def spot_title(spot: str, pick: str) -> str:
    """Как поле называется словами — для журнала и расчёта."""
    if spot == "number":
        return f"Число {pick}"
    return SPOT_TITLES.get(pick, pick)


# ---------- кости «Семёрка» ----------
#
# Три исхода и честные шансы: 15/36 на меньше семи, 6/36 ровно на семь,
# 15/36 на больше. Выплаты подобраны так, чтобы возврат у всех трёх
# вариантов был одинаковым — 95,83%, — и у игрока не было варианта
# «правильнее» прочих.
#
#     15/36 × 46/20 = 0,9583
#      6/36 × 115/20 = 0,9583
DICE_PACKET = 20
DICE_PAY = {"under": 46, "seven": 115, "over": 46}
DICE_TITLES = {"under": "Меньше 7", "seven": "Ровно 7", "over": "Больше 7"}


def dice_outcome(first: int, second: int) -> str:
    """Исход броска по сумме костей."""
    total = first + second
    if total < 7:
        return "under"
    if total == 7:
        return "seven"
    return "over"


def dice_pay(pick: str, stake: int, first: int, second: int) -> int:
    """Начисление за ставку. Целыми: пакетов по 20 умножить на выплату.

    Считать «ставка × 2,3» нельзя: на ставке в 30 это дало бы 69, а
    пакет там один — ставка обязана быть кратной двадцати, и служба
    этого требует до приёма.
    """
    if not stake or dice_outcome(first, second) != pick:
        return 0
    return stake // DICE_PACKET * DICE_PAY[pick]


# ---------- карты ----------
#
# Колода общая для старшей карты и блэкджека: 52 карты, масти равны.
# Карта — строка вида «7♦» или «A♠»: так она и в журнале читается, и
# на экран уходит без перевода.
RANKS: tuple[str, ...] = (
    "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A",
)
SUITS: tuple[str, ...] = ("♠", "♥", "♦", "♣")
RANK_ORDER = {rank: index for index, rank in enumerate(RANKS)}


def fresh_deck(rng: random.Random) -> list[str]:
    """Перемешанная колода из 52 карт."""
    deck = [rank + suit for rank in RANKS for suit in SUITS]
    rng.shuffle(deck)
    return deck


def rank_of(card: str) -> str:
    """Достоинство карты без масти. «10♦» — это десятка, а не единица."""
    return card[:-1]


def high_rank(card: str) -> int:
    """Старшинство для «старшей карты»: двойка младшая, туз старший."""
    return RANK_ORDER[rank_of(card)]


def card_points(card: str) -> int:
    """Сколько очков карта стоит в блэкджеке. Туз пока за единицу."""
    rank = rank_of(card)
    if rank == "A":
        return 1
    if rank in ("J", "Q", "K"):
        return 10
    return int(rank)


def hand_total(cards: Iterable[str]) -> tuple[int, bool]:
    """Лучшая сумма руки и мягкая ли она.

    Туз стоит 1 или 11 — берётся большее, которое ещё не перебор.
    Мягкая рука это та, где туз считается за 11: её нельзя перебрать
    одной картой, и крупье на мягких 17 всё равно останавливается.
    """
    cards = list(cards)
    total = sum(card_points(one) for one in cards)
    soft = any(rank_of(one) == "A" for one in cards) and total + 10 <= 21
    return (total + 10 if soft else total), soft


def is_blackjack(cards: Iterable[str]) -> bool:
    """Блэкджек — только стартовые две карты. Обычное 21 ему уступает."""
    cards = list(cards)
    return len(cards) == 2 and hand_total(cards)[0] == 21


def dealer_draws(cards: Iterable[str]) -> bool:
    """Берёт ли крупье ещё карту: на 16 и меньше берёт, на 17+ встаёт.

    Мягкие 17 включены в «встаёт» нарочно: правило «крупье добирает на
    мягкой семнадцати» делает стол заметно жаднее, а у нас мини-игра, в
    которой лишнее правило — это лишний вопрос игрока.
    """
    total, _ = hand_total(cards)
    return total <= 16


def peeks(upcard: str) -> bool:
    """Проверяет ли крупье свой блэкджек до ходов игроков."""
    return rank_of(upcard) == "A" or card_points(upcard) == 10


# Начисления блэкджека. Как и везде, вместе со ставкой: ×2 — это
# «ставка назад и столько же сверху», ×1 — только ставка назад.
BLACKJACK_PAY = 5  # ×2,5 считается половинками: ставка × 5 // 2
BLACKJACK_STEP = 2  # поэтому ставка обязана быть чётной


def blackjack_pay(stake: int, mine: list[str], dealer: list[str]) -> int:
    """Сколько начислить руке игрока против руки крупье.

    Порядок проверок — это и есть правила:

    * блэкджек крупье бьёт всё, кроме блэкджека игрока, которому
      возвращают ставку;
    * стартовый блэкджек игрока платит ×2,5 и хода не требует;
    * перебор игрока — ноль, что бы ни было у крупье;
    * перебор крупье — победа всем оставшимся рукам.
    """
    if not stake:
        return 0
    mine_total, _ = hand_total(mine)
    dealer_total, _ = hand_total(dealer)
    if is_blackjack(dealer):
        return stake if is_blackjack(mine) else 0
    if is_blackjack(mine):
        return stake * BLACKJACK_PAY // BLACKJACK_STEP
    if mine_total > 21:
        return 0
    if dealer_total > 21 or mine_total > dealer_total:
        return stake * 2
    if mine_total == dealer_total:
        return stake
    return 0


# ---------- старшая карта ----------
#
# Банк минус комиссия дома. Пять процентов вниз и один раз: при
# четырёх взносах по 50 это 10 с двухсот, победителю 190.
RAKE_PERCENT = 5


def rake_of(pot: int) -> int:
    """Комиссия дома. Округление вниз — в пользу игрока."""
    return pot * RAKE_PERCENT // 100


def prize_of(pot: int) -> int:
    """Что достаётся победителю: банк минус комиссия."""
    return pot - rake_of(pot)


def leaders(hands: dict[str, str]) -> list[str]:
    """Чьи карты старшие. Несколько — значит, пересдача между ними."""
    if not hands:
        return []
    top = max(high_rank(card) for card in hands.values())
    return sorted(
        (seat for seat, card in hands.items() if high_rank(card) == top),
        key=int,
    )


def split_pot(prize: int, seats: list[str]) -> dict[str, int]:
    """Разделить банк поровну, лишние фишки — по одной с младшего места.

    Делёж нужен там, где пересдавать уже нечем: колода кончилась, а
    старшие равны. Дробной фишки нет, поэтому остаток раздаётся по
    одной по возрастанию номера места — правило простое и его видно.
    """
    if not seats:
        return {}
    order = sorted(seats, key=int)
    share, extra = divmod(prize, len(order))
    return {
        seat: share + (1 if index < extra else 0)
        for index, seat in enumerate(order)
    }
