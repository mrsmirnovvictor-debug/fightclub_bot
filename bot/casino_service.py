"""Казино: столы на четыре места, партии и деньги.

Тут живёт всё, чего нет в правилах (`bot/game/casino.py`): места,
время, фишки и журнал. Правила отвечают «кто выиграл», служба — «когда
закрылся приём, чьи это были фишки и кому их отдали».

Устройство — то же, что у «Чёрного пуска», и по той же причине. Стол
лежит одной строкой с json и версией: партия это одно состояние, и
две команды не должны раздать карту дважды. Всякая команда читает стол
целиком, досчитывает то, что должно было случиться само, меняет json и
пишет обратно под проверкой версии.

**Время считает сервер, и только он.** Клиент не закрывает приём и не
решает, чей ход: он показывает обратный отсчёт по серверным числам.
Фазы догоняются лениво, при взгляде на стол, — будильника у нас нет, и
партия доигрывается тем, кто первым заглянул.

**Исход фиксируется до анимации.** Карман рулетки и грани костей
известны в момент закрытия приёма; анимация только показывает уже
решённое. Выводятся они из зерна стола и номера партии, поэтому
повторный расчёт после перезапуска даёт тот же ответ, а не бросает
кости заново.

**Деньги двигаются только через базу.** Ставка уходит в резерв сразу,
выплата проходит по первичному ключу журнала — одна на место и партию.
Повторный запрос, перезапуск сервера и два расчёта подряд безопасны не
потому, что мы их ловим, а потому, что вторая строка не встанет.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any, Callable

from bot.database import Database
from bot.game.casino import (
    CHIP_PER_CREDIT,
    DICE_PAY,
    DICE_TITLES,
    GAME_LIST,
    ROULETTE_ODDS,
    ROULETTE_SPOTS,
    ROUND_SECONDS,
    blackjack_pay,
    colour_of,
    cover_url,
    dealer_draws,
    dice_outcome,
    dice_pay,
    fresh_deck,
    game,
    hand_total,
    high_rank,
    is_blackjack,
    leaders,
    open_now,
    opens_at,
    peeks,
    prize_of,
    rake_of,
    roulette_pay,
    roulette_wins,
    split_pot,
    spot_title,
)
from bot.game.health import now_ts
from bot.models import Player


class CasinoError(Exception):
    """Отказ, который игроку показывают словами."""


# Сколько длится показ. Это не украшение: пока идёт розыгрыш, ставки
# уже не принимают, а расчёт висит столько, чтобы его успели прочесть
PLAY_SECONDS = {
    "dice": 2,
    "roulette": 5,
    "highcard": 6,
    "blackjack": 3,
}
DONE_SECONDS = {
    "dice": 4,
    "roulette": 0,  # у рулетки своё время: она ждёт следующего круга
    "highcard": 6,
    "blackjack": 6,
}

# Все столы живут по общим часам: круг начинается каждые 120 секунд
# серверного времени, и у всех столов разом. Так ожидание одинаково на
# любом экране — в том числе у того, кто только что сел, — и шкала
# ожидания всюду одной длины.
#
# Считать срок от первого взгляда, как считалось раньше, нельзя: стол
# догоняет время лениво, и у двух игроков, заглянувших с разницей в
# секунду, выходили разные сроки. На экране это читалось как скачущий
# таймер, и не зря: он и правда скакал.
SPIN_EVERY = ROUND_SECONDS

# Ход в блэкджеке: десять секунд на решение, дальше «хватит»
TURN_SECONDS = 10

# Сколько событий анимации стол помнит. Больше не нужно: опоздавший
# клиент не доигрывает пропущенное, а переходит к текущей фазе
EVENTS_KEPT = 24


def shut_words(moment: int) -> str:
    """Чем казино отвечает ночью."""
    from datetime import datetime

    from bot.game.clock import MOSCOW

    when = datetime.fromtimestamp(opens_at(moment), MOSCOW)
    return f"Казино закрыто. Откроется в {when.strftime('%H:%M')} по Москве."


# ---------- кошелёк и обмен ----------


async def wallet(db: Database, user_id: int) -> dict[str, int]:
    """Фишки на руках и в резерве."""
    return await db.casino_wallet(user_id)


async def exchange(
    db: Database, player: Player, kind: str, amount: int, now: int | None = None
) -> str:
    """Обменять кредиты на фишки и обратно. Курс один к одному.

    Курса как такового нет, и это выбор: казино, которое меняет деньги
    по своему курсу, игрок считает обманом ещё до первой ставки. Дом
    зарабатывает на зеро и на комиссии с банка, а не на кассе.

    Менять можно только то, что лежит свободно: фишки в резерве — это
    уже сделанная ставка, и до расчёта партии они не чьи.
    """
    moment = now_ts() if now is None else now
    amount = int(amount)
    if amount <= 0:
        raise CasinoError("Сколько менять?")
    if kind == "buy":
        chips = amount * CHIP_PER_CREDIT
        if player.credits < amount:
            raise CasinoError(
                f"На руках {player.credits} 💰, а нужно {amount}."
            )
        if not await db.casino_exchange(player.user_id, chips, -amount, moment):
            raise CasinoError("Не хватило кредитов.")
        player.credits -= amount
        return f"Разменяно {amount} 💰 на {chips} фишек."
    if kind == "sell":
        credits = amount // CHIP_PER_CREDIT
        purse = await db.casino_wallet(player.user_id)
        if purse["chips"] < amount:
            raise CasinoError(
                f"Свободных фишек {purse['chips']}, а нужно {amount}."
            )
        if not await db.casino_exchange(player.user_id, -amount, credits, moment):
            raise CasinoError("Не хватило фишек.")
        player.credits += credits
        return f"Сдано {amount} фишек, получено {credits} 💰."
    raise CasinoError("Непонятный обмен.")


# ---------- стол ----------


@dataclass
class Table:
    """Стол: строка базы вместе с разобранным состоянием."""

    id: int
    game: str
    phase: str
    round: int
    version: int
    state: dict[str, Any]

    @property
    def rules(self):
        return game(self.game)


def blank_state(seed: str) -> dict[str, Any]:
    return {
        "seed": seed,
        "until": 0,
        "since": 0,
        "seats": {},
        "result": None,
        "owed": [],
        "events": [],
        "seq": 0,
    }


def _seat_row(player: Player, seat: int) -> dict[str, Any]:
    return {
        "seat": str(seat),
        "user": player.user_id,
        "name": player.nickname,
        "avatar": player.avatar,
        "bet": 0,
        "pick": "",
        "bets": [],
        "ready": False,
        "cards": [],
        "stood": False,
        "said": "",
        "paid": 0,
    }


def _rng_of(state: dict, round_no: int, salt: str = "") -> random.Random:
    """Случайность партии: выводится из зерна стола и её номера.

    Так повторный расчёт после перезапуска даёт тот же исход, а не
    бросает кости заново. Зерно живёт в состоянии и наружу не уходит.
    """
    return random.Random(f"{state.get('seed')}:{round_no}:{salt}")


async def read_table(db: Database, table_id: int) -> Table:
    row = await db.casino_read(table_id)
    if row is None or int(row["closed_at"]):
        raise CasinoError("Такого стола нет.")
    return Table(
        id=int(row["id"]),
        game=str(row["game"]),
        phase=str(row["phase"]),
        round=int(row["round"]),
        version=int(row["version"]),
        state=json.loads(str(row["state"]) or "{}") or blank_state(""),
    )


async def open_table(db: Database, code: str, now: int, seed: str = "") -> Table:
    """Открыть стол этой игры."""
    game(code)  # игры нет — ошибка тут, а не в раздаче
    table_id = await db.casino_open(code, now)
    state = blank_state(seed or f"{table_id}:{random.random()}")
    await db.casino_write(table_id, 0, "wait", 0, json.dumps(state), now)
    return await read_table(db, table_id)


# ---------- события анимации ----------


def _event(state: dict, kind: str, now: int, **data: Any) -> None:
    """Записать событие анимации: что показывать и с какой секунды.

    Клиент, пришедший позже, видит номер партии и серверное время
    начала — и решает сам, доигрывать ли ему это событие или сразу
    показать конечный кадр.
    """
    state["seq"] = int(state.get("seq") or 0) + 1
    events = state.setdefault("events", [])
    events.append({"seq": state["seq"], "at": now, "kind": kind, **data})
    del events[:-EVENTS_KEPT]


# ---------- ленивый ход времени ----------


def advance(table: Table, now: int) -> bool:
    """Догнать всё, что должно было случиться само. True — что-то было.

    Зовётся при каждом взгляде на стол. Будильника у нас нет: партия
    закрывается и доигрывается тем, кто первым заглянул, — и считается
    она по серверному времени, а не по тому, когда заглянули.
    """
    moved = False
    for _ in range(12):  # партия за партией: заглянувший через час
        step = _advance_once(table, now)
        moved = moved or step
        if not step:
            break
    return moved


def _advance_once(table: Table, now: int) -> bool:
    state = table.state
    if table.game == "roulette":
        return _roulette_step(table, now)
    if table.game == "dice":
        return _dice_step(table, now)
    if table.game == "highcard":
        return _highcard_step(table, now)
    if table.game == "blackjack":
        return _blackjack_step(table, now)
    return False


def _sitting(state: dict) -> list[dict]:
    return [one for one in (state.get("seats") or {}).values() if one["user"]]


def _betting(state: dict) -> list[dict]:
    return [one for one in _sitting(state) if int(one.get("bet") or 0) > 0]


def _owe(
    state: dict, seat: dict, staked: int, paid: int, kind: str, said: str,
    why: str = "",
) -> None:
    """Записать расчёт места: что снять с резерва и что начислить.

    Деньги тут не двигаются — двигает их `_settle_up`, и двигает через
    базу. Состояние только помнит, кому сколько причитается, чтобы
    расчёт пережил и обрыв, и повторное чтение.

    `said` — короткий итог для фишки места, `why` — разбор словами:
    что выпало, что было у игрока и почему вышло так. Короткого мало:
    «−20» не объясняет проигрыша, а объяснение — единственное, что
    отличает игру от отъёма денег.
    """
    seat["paid"] = paid
    seat["said"] = said
    seat["why"] = why
    state.setdefault("owed", []).append({
        "seat": seat["seat"],
        "user": seat["user"],
        "staked": staked,
        "paid": paid,
        "kind": kind,
        "done": False,
    })


def _new_round(table: Table, now: int) -> None:
    """Начать следующую партию: обнулить ставки и исход.

    Исход прошлой партии не выбрасывается, а переезжает в `last`: у
    рулетки приём открывается сразу после расчёта, и прошлый карман с
    личной выплатой должны быть видны поверх нового приёма — иначе
    игрок не узнает, чем кончилось то, на что он ставил.
    """
    state = table.state
    if state.get("result"):
        state["last"] = {
            "round": table.round,
            "result": state["result"],
            "seats": {
                seat["seat"]: {
                    "user": seat["user"],
                    "bet": int(seat.get("bet") or 0),
                    "paid": int(seat.get("paid") or 0),
                    "said": seat.get("said") or "",
                    "why": seat.get("why") or "",
                    # Чем именно ставили — для «Повторить ставку».
                    # Кнопка обязана показывать сумму, а сумма берётся
                    # отсюда, а не из памяти экрана
                    "pick": seat.get("pick") or "",
                    "bets": list(seat.get("bets") or []),
                }
                for seat in (state.get("seats") or {}).values()
                if seat.get("said") or int(seat.get("bet") or 0)
            },
        }
    table.round += 1
    state["result"] = None
    state["owed"] = []
    state["events"] = []
    state["turn"] = ""
    state["dealer"] = []
    state["deck"] = []
    # Ушедшего из-за стола убираем только здесь: пока партия не
    # кончилась, он в ней остаётся — ставка сделана, и банк ему
    # причитается, где бы он ни был. А вот в следующую его место
    # должно быть пустым, иначе стол будет вечно играть с призраком
    for number, seat in list((state.get("seats") or {}).items()):
        if seat.get("gone"):
            state["seats"].pop(number, None)
    for seat in (state.get("seats") or {}).values():
        seat.update(
            bet=0, pick="", bets=[], ready=False, cards=[], stood=False,
            said="", why="", paid=0, open=False,
        )
    state["since"] = now


# ---------- кости ----------


def _dice_step(table: Table, now: int) -> bool:
    state = table.state
    if table.phase == "wait":
        if not _sitting(state) or not open_now(now):
            return False
        _new_round(table, now)
        table.phase = "accept"
        state["until"] = _next_slot(now)
        return True
    if table.phase == "accept" and now >= int(state["until"]):
        # Фиксация: сервер закрывает приём по своему времени, и исход
        # решается здесь же — до всякой анимации
        rng = _rng_of(state, int(state["until"]))
        first, second = rng.randint(1, 6), rng.randint(1, 6)
        outcome = dice_outcome(first, second)
        state["result"] = {
            "dice": [first, second],
            "sum": first + second,
            "outcome": outcome,
            "title": DICE_TITLES[outcome],
        }
        for seat in _betting(state):
            stake = int(seat["bet"])
            pick = str(seat.get("pick") or "")
            paid = dice_pay(pick, stake, first, second)
            mine = DICE_TITLES.get(pick, "—")
            _owe(
                state, seat, stake, paid, "payout",
                f"{DICE_TITLES[outcome]} · "
                + (f"+{paid - stake}" if paid else f"−{stake}"),
                why=(
                    f"{first} + {second} = {first + second}, это "
                    f"«{DICE_TITLES[outcome]}». Ваш выбор — «{mine}»: "
                    + (
                        f"угадали, {stake} даёт {paid}."
                        if paid else "не угадали, ставка сгорела."
                    )
                ),
            )
        table.phase = "play"
        state["since"] = now
        state["until"] = now + PLAY_SECONDS["dice"]
        _event(state, "roll", now, dice=[first, second])
        return True
    if table.phase == "play" and now >= int(state["until"]):
        table.phase = "done"
        state["since"] = now
        state["until"] = now + DONE_SECONDS["dice"]
        return True
    if table.phase == "done" and now >= int(state["until"]):
        table.phase = "wait"
        return True
    return False


# ---------- рулетка ----------


# Сколько секунд приёма гарантировано. Партия иногда перехлёстывает
# границу круга — четыре руки блэкджека с ходами по десять секунд идут
# дольше полминуты, — и без этого следующему приёму доставалась бы
# секунда до границы. Окно от десяти секунд до полного круга
LEAST_ACCEPT = 10


def _next_slot(now: int) -> int:
    """Когда начнётся следующий круг. Общий для всех столов города."""
    slot = (now // ROUND_SECONDS + 1) * ROUND_SECONDS
    while slot - now < LEAST_ACCEPT:
        slot += ROUND_SECONDS
    return slot


def _roulette_step(table: Table, now: int) -> bool:
    state = table.state
    if table.phase in ("wait", "done", "play") and now >= int(
        state.get("until") or 0
    ):
        # Ночью колесо стоит: казино закрыто, и крутить его некому
        if not open_now(now):
            table.phase = "wait"
            return False
        # Между вращениями приём открыт всегда, даже если за столом
        # никого: колесо крутится само, кнопки запуска нет
        _new_round(table, now)
        table.phase = "accept"
        state["until"] = _next_slot(now)
        return True
    if table.phase == "accept" and now >= int(state["until"]):
        spin = int(state["until"])
        rng = _rng_of(state, spin)  # партия привязана ко времени круга
        number = rng.choice(range(37))
        state["result"] = {
            "number": number,
            "colour": colour_of(number),
            "title": f"{number} · " + {
                "red": "красное", "black": "чёрное", "zero": "зеро",
            }[colour_of(number)],
        }
        for seat in _sitting(state):
            stake = int(seat.get("bet") or 0)
            if stake <= 0:
                continue
            bets = seat.get("bets") or []
            paid = roulette_pay(bets, number)
            _owe(
                state, seat, stake, paid, "payout",
                f"+{paid - stake}" if paid else f"−{stake}",
                why=_roulette_why(bets, number, paid),
            )
        table.phase = "play"
        state["since"] = spin
        state["until"] = spin + PLAY_SECONDS["roulette"]
        _event(state, "spin", spin, number=number)
        return True
    return False


# ---------- старшая карта ----------


def _highcard_step(table: Table, now: int) -> bool:
    state = table.state
    ready = [one for one in _sitting(state) if one.get("ready")]
    if table.phase == "wait":
        if not _sitting(state) or not open_now(now):
            return False
        _new_round(table, now)
        table.phase = "accept"
        state["until"] = _next_slot(now)
        return True
    if table.phase == "accept" and now >= int(state["until"]):
        if len(ready) < 2:
            # Партия не собралась: вдвоём и дешевле, и честнее, чем
            # одному против пустого стола. Взносы возвращаются
            for seat in ready:
                _owe(
                    state, seat, int(seat["bet"]), int(seat["bet"]),
                    "refund", "Партия не собралась — взнос возвращён",
                    why="Играть в «Старшую карту» вдвоём и больше; "
                        "к закрытию приёма желающих не набралось, "
                        "и взнос вернулся целиком.",
                )
            table.phase = "done"
            state["since"] = now
            state["until"] = now + 2
            return True
        _highcard_deal(table, now, ready)
        return True
    if table.phase == "play" and now >= int(state["until"]):
        return _highcard_show(table, now)
    if table.phase == "done" and now >= int(state["until"]):
        table.phase = "wait"
        return True
    return False


def _highcard_deal(table: Table, now: int, ready: list[dict]) -> None:
    """Зафиксировать состав, перемешать колоду и сдать по одной закрытой."""
    state = table.state
    rng = _rng_of(state, table.round)
    deck = fresh_deck(rng)
    pot = sum(int(one["bet"]) for one in ready)
    state["result"] = {
        "pot": pot,
        "rake": rake_of(pot),
        "prize": prize_of(pot),
        "hands": {},
        "winners": [],
        "deal": 1,
    }
    for seat in ready:
        seat["cards"] = [deck.pop()]
        seat["open"] = False
        state["result"]["hands"][seat["seat"]] = ""
    state["deck"] = deck
    table.phase = "play"
    state["since"] = now
    state["until"] = now + PLAY_SECONDS["highcard"]
    _event(state, "deal", now, seats=[one["seat"] for one in ready])


def _highcard_show(table: Table, now: int) -> bool:
    """Вскрыть карты и решить: победа, пересдача или дележ."""
    state = table.state
    result = state["result"]
    playing = [
        one for one in _sitting(state)
        if one.get("cards") and one["seat"] in result["hands"]
    ]
    # Доигравшие места показывают карту, даже если «Открыть» не нажали:
    # через пять секунд оставшиеся карты открываются сами
    for seat in playing:
        seat["open"] = True
        result["hands"][seat["seat"]] = seat["cards"][-1]
    top = leaders({one["seat"]: one["cards"][-1] for one in playing})
    if len(top) == 1:
        winner = next(one for one in playing if one["seat"] == top[0])
        best = winner["cards"][-1]
        for seat in playing:
            stake = int(seat["bet"])
            paid = result["prize"] if seat is winner else 0
            mine = seat["cards"][-1]
            _owe(
                state, seat, stake, paid, "payout",
                f"Банк {result['prize']} — ваш" if paid
                else f"Банк получил {winner['name']}",
                why=(
                    f"Ваша {mine} — старшая за столом. Банк {result['pot']} "
                    f"минус комиссия {result['rake']}: {result['prize']}."
                    if paid else
                    f"Ваша {mine} против {best} у {winner['name']} — "
                    f"его карта старше, банк ушёл ему."
                ),
            )
        result["winners"] = [winner["seat"]]
        table.phase = "done"
        state["since"] = now
        state["until"] = now + DONE_SECONDS["highcard"]
        _event(state, "win", now, seat=winner["seat"], prize=result["prize"])
        return True
    # Равенство старших: новую карту берут только лидеры и без доплаты
    deck = state.get("deck") or []
    if len(deck) >= len(top):
        result["deal"] = int(result.get("deal") or 1) + 1
        for seat in playing:
            if seat["seat"] in top:
                seat["cards"].append(deck.pop())
                seat["open"] = False
                result["hands"][seat["seat"]] = ""
            else:
                # Остальные наблюдают: их карта остаётся на столе
                result["hands"][seat["seat"]] = seat["cards"][-1]
        state["deck"] = deck
        state["since"] = now
        state["until"] = now + PLAY_SECONDS["highcard"]
        _event(state, "redeal", now, seats=top)
        return True
    # Колоды не хватило на полную пересдачу — делим поровну
    share = split_pot(result["prize"], top)
    for seat in playing:
        stake = int(seat["bet"])
        paid = share.get(seat["seat"], 0)
        _owe(
            state, seat, stake, paid, "payout",
            f"Ничья — {paid} из банка" if paid else "Банк разделили старшие",
            why=(
                f"Старшие равны, а колоды на пересдачу не хватило: банк "
                f"{result['prize']} разделили поровну, вам {paid}."
                if paid else
                f"Ваша {seat['cards'][-1]} младше: банк разделили те, "
                f"у кого карты равны и старше."
            ),
        )
    result["winners"] = top
    result["split"] = share
    table.phase = "done"
    state["since"] = now
    state["until"] = now + DONE_SECONDS["highcard"]
    _event(state, "split", now, seats=top)
    return True


# ---------- блэкджек ----------


def _blackjack_step(table: Table, now: int) -> bool:
    state = table.state
    if table.phase == "wait":
        if not _sitting(state) or not open_now(now):
            return False
        _new_round(table, now)
        table.phase = "accept"
        state["until"] = _next_slot(now)
        return True
    if table.phase == "accept" and now >= int(state["until"]):
        betting = _betting(state)
        if not betting:
            table.phase = "wait"
            return True
        _blackjack_deal(table, now, betting)
        return True
    if table.phase == "play" and now >= int(state["until"]):
        # Раздача показана — пора ходить
        return _blackjack_next_turn(table, now)
    if table.phase == "turns":
        if now >= int(state["until"]):
            # Таймер истёк — «Хватит», и ход уходит дальше
            seat = (state.get("seats") or {}).get(str(state.get("turn") or ""))
            if seat is not None:
                seat["stood"] = True
                _event(state, "stand", now, seat=seat["seat"], byclock=True)
            return _blackjack_next_turn(table, now)
        return False
    if table.phase == "dealer" and now >= int(state["until"]):
        return _blackjack_dealer(table, now)
    if table.phase == "done" and now >= int(state["until"]):
        table.phase = "wait"
        return True
    return False


def _blackjack_deal(table: Table, now: int, betting: list[dict]) -> None:
    """Новая колода, по две карты каждому и две крупье — одна втёмную."""
    state = table.state
    rng = _rng_of(state, table.round)
    deck = fresh_deck(rng)
    for _ in range(2):
        for seat in betting:
            seat["cards"].append(deck.pop())
        state.setdefault("dealer", []).append(deck.pop())
    state["deck"] = deck
    state["result"] = {"dealer": [], "upcard": state["dealer"][0], "peek": False}
    table.phase = "play"
    state["since"] = now
    state["until"] = now + PLAY_SECONDS["blackjack"]
    _event(state, "deal", now, seats=[one["seat"] for one in betting])


def _blackjack_next_turn(table: Table, now: int) -> bool:
    """Отдать ход следующему месту или увести партию к крупье."""
    state = table.state
    dealer = state.get("dealer") or []
    # Крупье смотрит свой блэкджек до ходов — иначе игроки успели бы
    # добрать карт в уже проигранной партии
    if not state["result"].get("peek") and peeks(dealer[0]):
        state["result"]["peek"] = True
        if is_blackjack(dealer):
            return _blackjack_dealer(table, now)
    for seat in sorted(_betting(state), key=lambda one: int(one["seat"])):
        if seat.get("stood"):
            continue
        if is_blackjack(seat["cards"]):
            # Стартовый блэкджек хода не требует
            seat["stood"] = True
            continue
        if hand_total(seat["cards"])[0] >= 21:
            seat["stood"] = True
            continue
        state["turn"] = seat["seat"]
        table.phase = "turns"
        state["since"] = now
        state["until"] = now + TURN_SECONDS
        return True
    state["turn"] = ""
    table.phase = "dealer"
    state["since"] = now
    state["until"] = now
    return True


def _blackjack_dealer(table: Table, now: int) -> bool:
    """Крупье открывается, добирает по правилу и партия считается."""
    state = table.state
    dealer = list(state.get("dealer") or [])
    deck = state.get("deck") or []
    if not is_blackjack(dealer):
        while dealer_draws(dealer) and deck:
            dealer.append(deck.pop())
    state["dealer"] = dealer
    state["deck"] = deck
    total, _ = hand_total(dealer)
    state["result"] = {
        **(state.get("result") or {}),
        "dealer": dealer,
        "total": total,
        "blackjack": is_blackjack(dealer),
        "bust": total > 21,
    }
    for seat in _betting(state):
        stake = int(seat["bet"])
        paid = blackjack_pay(stake, seat["cards"], dealer)
        mine, _ = hand_total(seat["cards"])
        if mine > 21:
            said = "Перебор"
        elif paid > stake:
            said = "Блэкджек!" if is_blackjack(seat["cards"]) else "Победа"
        elif paid == stake:
            said = "Возврат"
        else:
            said = "Проигрыш"
        _owe(
            state, seat, stake, paid, "payout", said,
            why=_blackjack_why(seat, dealer, stake, paid),
        )
    table.phase = "done"
    state["since"] = now
    state["until"] = now + DONE_SECONDS["blackjack"]
    _event(state, "dealer", now, cards=dealer)
    return True


def _roulette_why(bets: list[dict], number: int, paid: int) -> str:
    """Разбор ставок рулетки: какие поля сыграли, а какие нет."""
    if not bets:
        return ""
    said = []
    for bet in bets:
        spot, pick = str(bet.get("spot")), str(bet.get("pick"))
        stake = int(bet.get("amount") or 0)
        won = roulette_wins(spot, pick, number)
        said.append(
            f"{spot_title(spot, pick)} {stake} — "
            + (f"+{stake * ROULETTE_ODDS[spot]}" if won else "мимо")
        )
    where = {"red": "красное", "black": "чёрное", "zero": "зеро"}
    head = f"Выпало {number} · {where[colour_of(number)]}. "
    if number == 0 and paid == 0:
        head += "При зеро играет только ставка на ноль. "
    return head + "; ".join(said) + "."


def _blackjack_why(
    seat: dict, dealer: list[str], stake: int, paid: int
) -> str:
    """Разбор руки: сколько у кого и почему вышло так."""
    mine, _ = hand_total(seat.get("cards") or [])
    his, _ = hand_total(dealer)
    cards = " ".join(seat.get("cards") or [])
    his_cards = " ".join(dealer)
    head = f"У вас {cards} — {mine}; у крупье {his_cards} — {his}. "
    if is_blackjack(dealer) and is_blackjack(seat.get("cards") or []):
        return head + f"Блэкджек на блэкджек — ставка {stake} возвращена."
    if is_blackjack(dealer):
        return head + "У крупье блэкджек, и он бьёт всё, кроме блэкджека."
    if is_blackjack(seat.get("cards") or []):
        return head + f"Блэкджек с двух карт платит ×2,5: {paid}."
    if mine > 21:
        return head + "Перебор: рука больше 21 проигрывает сразу."
    if his > 21:
        return head + f"Крупье перебрал — победа, начислено {paid}."
    if mine > his:
        return head + f"Ваша рука ближе к 21 — начислено {paid}."
    if mine == his:
        return head + f"Поровну — ставка {stake} возвращена."
    return head + "Рука крупье ближе к 21."


# ---------- запись и расчёт ----------


async def _settle_up(db: Database, table: Table, now: int) -> None:
    """Отдать то, что причитается. Каждому месту — ровно один раз.

    Строки `owed` помечаются выплаченными не для защиты, а чтобы не
    ходить в базу зря: от второй выплаты держит первичный ключ
    журнала, и расчёт после перезапуска сервера безопасен сам по себе.
    """
    for owed in table.state.get("owed") or []:
        if owed.get("done"):
            continue
        await db.casino_settle(
            table.id, table.round, int(owed["user"]),
            int(owed["staked"]), int(owed["paid"]), str(owed["kind"]), now,
        )
        owed["done"] = True


async def look(db: Database, table_id: int, now: int | None = None) -> Table:
    """Прочитать стол и досчитать всё, что должно было случиться само."""
    moment = now_ts() if now is None else now
    for _ in range(3):
        table = await read_table(db, table_id)
        before = json.dumps(table.state, sort_keys=True)
        phase, round_no = table.phase, table.round
        advance(table, moment)
        after = json.dumps(table.state, sort_keys=True)
        if before == after and phase == table.phase and round_no == table.round:
            return table
        if await db.casino_write(
            table.id, table.version, table.phase, table.round, after, moment
        ):
            table.version += 1
            await _settle_up(db, table, moment)
            # Пометки о выплатах — тоже состояние: иначе следующий
            # взгляд пошёл бы платить по второму разу
            await db.casino_write(
                table.id, table.version, table.phase, table.round,
                json.dumps(table.state), moment,
            )
            table.version += 1
            return table
    return await read_table(db, table_id)


async def command(
    db: Database,
    table_id: int,
    user_id: int,
    change: Callable[[Table, int], str],
    command_id: str = "",
    now: int | None = None,
) -> str:
    """Выполнить команду над столом: прочитать, изменить, записать.

    Повторная команда с тем же ключом отдаёт прежний ответ и ничего не
    делает. Это не удобство, а требование: сеть теряет ответы, игрок
    жмёт ещё раз, и второе нажатие не должно стать второй ставкой.
    """
    moment = now_ts() if now is None else now
    said = await db.casino_done(table_id, command_id)
    if said is not None:
        return said
    for _ in range(3):
        table = await read_table(db, table_id)
        advance(table, moment)
        answer = change(table, moment)
        # И ещё раз: команда могла сама сдвинуть партию с места. Второй
        # подтвердивший взнос запускает отсчёт в ту же секунду, а не
        # тогда, когда кто-нибудь следующим заглянет на стол
        advance(table, moment)
        if await db.casino_write(
            table.id, table.version, table.phase, table.round,
            json.dumps(table.state), moment,
        ):
            table.version += 1
            await db.casino_remember(table_id, command_id, answer, moment)
            await _settle_up(db, table, moment)
            await db.casino_write(
                table.id, table.version, table.phase, table.round,
                json.dumps(table.state), moment,
            )
            return answer
    raise CasinoError("Стол занят другой командой. Повтори.")


# ---------- посадка ----------


async def sit(
    db: Database, player: Player, table_id: int, seat: int,
    now: int | None = None,
) -> str:
    """Сесть за стол на это место.

    Гонку за последним местом решает база, а не проверка: первичный
    ключ по месту не даст двоим занять одно, уникальный по игроку — не
    даст одному занять два. Пятый не становится пятым участником: ему
    остаётся другой стол.
    """
    moment = now_ts() if now is None else now
    if not open_now(moment):
        raise CasinoError(shut_words(moment))
    table = await read_table(db, table_id)
    if not 1 <= seat <= table.rules.seats:
        raise CasinoError("Такого места за столом нет.")
    where = await db.casino_seat_of(player.user_id)
    if where is not None:
        if int(where["table_id"]) == table_id and int(where["seat"]) == seat:
            return "Ты уже за этим столом."
        raise CasinoError("Ты уже сидишь за столом. Встань из-за него.")
    if not await db.casino_sit(table_id, seat, player.user_id, moment):
        raise CasinoError("Место уже заняли.")

    def change(one: Table, when: int) -> str:
        taken = (one.state.get("seats") or {}).get(str(seat))
        if taken is not None and taken.get("user") not in (0, player.user_id):
            raise CasinoError("Место уже заняли.")
        one.state.setdefault("seats", {})[str(seat)] = _seat_row(player, seat)
        return f"Место {seat} за столом «{one.rules.title}»."

    try:
        return await command(db, table_id, player.user_id, change, "", moment)
    except CasinoError:
        await db.casino_stand(player.user_id)
        raise


async def stand(db: Database, user_id: int, now: int | None = None) -> str:
    """Встать из-за стола. Что станет со ставкой — зависит от фазы.

    До фиксации ставка возвращается: партия ещё не началась, и держать
    деньги за ушедшего не за что. После фиксации уйти из партии уже
    нельзя — и не нужно: отключившийся сохраняет право на банк, расчёт
    пройдёт по номеру игрока, где бы он ни был.
    """
    moment = now_ts() if now is None else now
    where = await db.casino_seat_of(user_id)
    if where is None:
        return "Ты и так не за столом."
    table_id = int(where["table_id"])
    table = await look(db, table_id, moment)
    seat = (table.state.get("seats") or {}).get(str(where["seat"])) or {}
    back = int(seat.get("bet") or 0) if table.phase == "accept" else 0
    await db.casino_stand(user_id)
    if back:
        await db.casino_stake(table_id, table.round, user_id, -back, moment)

    def change(one: Table, when: int) -> str:
        mine = (one.state.get("seats") or {}).get(str(where["seat"]))
        if mine is None or int(mine.get("user") or 0) != user_id:
            return "Ты встал из-за стола."
        if one.phase in ("wait", "accept"):
            one.state["seats"].pop(str(where["seat"]), None)
        elif int(mine.get("bet") or 0):
            # В партии он остаётся до расчёта: ставка уже в игре
            mine["gone"] = True
        else:
            one.state["seats"].pop(str(where["seat"]), None)
        return (
            f"Ты встал из-за стола. Ставка {back} возвращена." if back
            else "Ты встал из-за стола."
        )

    return await command(db, table_id, user_id, change, "", moment)


# ---------- ставки и ходы ----------


def _my_seat(table: Table, user_id: int) -> dict[str, Any]:
    for seat in (table.state.get("seats") or {}).values():
        if int(seat.get("user") or 0) == user_id:
            return seat
    raise CasinoError("Ты не за этим столом.")


async def _staked(
    db: Database, table_id: int, round_no: int, user_id: int,
    delta: int, now: int, run: Any,
) -> str:
    """Выполнить команду, за которую уже списаны фишки.

    Фишки уходят в резерв до записи состояния — иначе ставка попадала
    бы в партию раньше, чем выяснится, что их не хватило. Значит, у
    отказа есть цена: если запись не прошла, деньги надо вернуть
    ровно в ту партию, из которой их взяли, и сделать это обязан тот
    же вызов, а не следующий расчёт.
    """
    try:
        return await run
    except Exception:
        if delta:
            await db.casino_stake(table_id, round_no, user_id, -delta, now)
        raise


def _check_stake(table: Table, amount: int) -> int:
    rules = table.rules
    if amount <= 0:
        raise CasinoError("Ставка должна быть больше нуля.")
    if amount % rules.step:
        raise CasinoError(f"Ставка должна быть кратна {rules.step}.")
    if amount < rules.least:
        raise CasinoError(f"Наименьшая ставка — {rules.least}.")
    if amount > rules.most:
        raise CasinoError(f"Наибольшая ставка — {rules.most}.")
    return amount


async def bet(
    db: Database,
    player: Player,
    table_id: int,
    amount: int,
    pick: str = "",
    spot: str = "",
    command_id: str = "",
    now: int | None = None,
) -> str:
    """Поставить. Фишки уходят в резерв сразу, до розыгрыша.

    Опоздавшая команда отклоняется без списания: приём закрывает
    сервер по своему времени, и ставка, пришедшая после, не списывает
    ничего — просто не принимается.
    """
    moment = now_ts() if now is None else now
    table = await look(db, table_id, moment)
    if table.phase != "accept":
        raise CasinoError("Приём ставок закрыт.")
    seat = _my_seat(table, player.user_id)
    if table.game == "roulette":
        _check_spot(spot, pick)
        delta = _check_stake(table, amount)
    elif table.game == "highcard":
        raise CasinoError("Тут взнос один и тот же: подтверди участие.")
    else:
        if table.game == "dice" and pick not in DICE_PAY:
            raise CasinoError("Выбери исход броска.")
        _check_stake(table, amount)
        delta = amount - int(seat.get("bet") or 0)
    if delta and not await db.casino_stake(
        table_id, table.round, player.user_id, delta, moment
    ):
        raise CasinoError("Не хватает фишек.")

    def change(one: Table, when: int) -> str:  # noqa: D401
        mine = _my_seat(one, player.user_id)
        if one.phase != "accept":
            raise CasinoError("Приём ставок закрыт.")
        if one.game == "roulette":
            mine.setdefault("bets", []).append(
                {"spot": spot, "pick": pick, "amount": amount}
            )
            mine["bet"] = int(mine.get("bet") or 0) + amount
            return f"{spot_title(spot, pick)} — {amount}."
        mine["bet"] = amount
        mine["pick"] = pick
        if one.game == "dice":
            return f"{DICE_TITLES[pick]} — {amount}."
        return f"Ставка {amount}."

    return await _staked(
        db, table_id, table.round, player.user_id, delta, moment,
        command(db, table_id, player.user_id, change, command_id, moment),
    )


def _check_spot(spot: str, pick: str) -> None:
    if spot == "number":
        if not pick.isdigit() or not 0 <= int(pick) <= 36:
            raise CasinoError("Числа на колесе — от 0 до 36.")
        return
    if spot not in ROULETTE_ODDS or pick not in ROULETTE_SPOTS.get(spot, ()):
        raise CasinoError("Такого поля на столе нет.")


async def take_back(
    db: Database, player: Player, table_id: int, whole: bool = False,
    command_id: str = "", now: int | None = None,
) -> str:
    """Снять последнюю фишку или очистить стол. Только до закрытия."""
    moment = now_ts() if now is None else now
    table = await look(db, table_id, moment)
    if table.phase != "accept":
        raise CasinoError("Приём ставок закрыт.")
    seat = _my_seat(table, player.user_id)
    bets = list(seat.get("bets") or [])
    if table.game == "roulette":
        if not bets:
            raise CasinoError("Фишек на столе нет.")
        # Снимают либо последнюю фишку, либо всё поле разом
        back = (
            sum(int(one["amount"]) for one in bets) if whole
            else int(bets[-1]["amount"])
        )
    else:
        back = int(seat.get("bet") or 0)
    if back <= 0:
        raise CasinoError("Ставки нет.")
    await db.casino_stake(table_id, table.round, player.user_id, -back, moment)

    def change(one: Table, when: int) -> str:
        mine = _my_seat(one, player.user_id)
        if one.game == "roulette" and not whole:
            mine["bets"] = list(mine.get("bets") or [])[:-1]
            mine["bet"] = sum(int(x["amount"]) for x in mine["bets"])
            return f"Фишка снята: {back}."
        mine["bets"] = []
        mine["bet"] = 0
        mine["pick"] = ""
        return f"Ставка снята: {back}."

    return await command(db, table_id, player.user_id, change, command_id, moment)


async def repeat_bet(
    db: Database, player: Player, table_id: int,
    command_id: str = "", now: int | None = None,
) -> str:
    """Повторить ставку прошлой партии — отдельным явным нажатием.

    Не «ставить как в прошлый раз автоматически»: сумма видна на
    кнопке, и нажатие на неё — такая же ставка, как любая другая. Само
    собой в казино не ставится ничего.
    """
    moment = now_ts() if now is None else now
    table = await look(db, table_id, moment)
    if table.phase != "accept":
        raise CasinoError("Приём ставок закрыт.")
    seat = _my_seat(table, player.user_id)
    was = None
    for row in ((table.state.get("last") or {}).get("seats") or {}).values():
        if int(row.get("user") or 0) == player.user_id:
            was = row
            break
    if was is None or not int(was.get("bet") or 0):
        raise CasinoError("Повторять нечего: прошлой ставки не было.")
    if int(seat.get("bet") or 0):
        raise CasinoError("Ставка уже сделана.")
    total = int(was["bet"])
    if not await db.casino_stake(
        table_id, table.round, player.user_id, total, moment
    ):
        raise CasinoError("Не хватает фишек.")

    def change(one: Table, when: int) -> str:
        mine = _my_seat(one, player.user_id)
        if one.phase != "accept":
            raise CasinoError("Приём ставок закрыт.")
        mine["bet"] = total
        mine["pick"] = was.get("pick") or ""
        mine["bets"] = list(was.get("bets") or [])
        return f"Ставка повторена: {total}."

    return await _staked(
        db, table_id, table.round, player.user_id, total, moment,
        command(db, table_id, player.user_id, change, command_id, moment),
    )


async def join_pot(
    db: Database, player: Player, table_id: int,
    command_id: str = "", now: int | None = None,
) -> str:
    """Подтвердить участие в «Старшей карте»: взнос один и тот же."""
    moment = now_ts() if now is None else now
    table = await look(db, table_id, moment)
    if table.game != "highcard":
        raise CasinoError("Тут ставят, а не вступают.")
    if table.phase != "accept":
        raise CasinoError("Партия уже идёт.")
    seat = _my_seat(table, player.user_id)
    if seat.get("ready"):
        return "Ты уже в партии."
    ante = table.rules.least
    if not await db.casino_stake(
        table_id, table.round, player.user_id, ante, moment
    ):
        raise CasinoError("Не хватает фишек на взнос.")

    def change(one: Table, when: int) -> str:
        mine = _my_seat(one, player.user_id)
        if one.phase != "accept":
            raise CasinoError("Партия уже идёт.")
        mine["ready"] = True
        mine["bet"] = ante
        return f"Взнос {ante} принят."

    return await _staked(
        db, table_id, table.round, player.user_id, ante, moment,
        command(db, table_id, player.user_id, change, command_id, moment),
    )


async def hand_move(
    db: Database, player: Player, table_id: int, move: str,
    command_id: str = "", now: int | None = None,
) -> str:
    """Ход в блэкджеке: «Ещё карту» или «Хватит»."""
    moment = now_ts() if now is None else now

    def change(one: Table, when: int) -> str:
        if one.game != "blackjack":
            raise CasinoError("Тут не ходят картами.")
        mine = _my_seat(one, player.user_id)
        if one.phase != "turns" or str(one.state.get("turn")) != mine["seat"]:
            raise CasinoError("Сейчас не твой ход.")
        state = one.state
        if move == "hit":
            deck = state.get("deck") or []
            if not deck:  # pragma: no cover - колода из 52 на четверых
                raise CasinoError("Колода кончилась.")
            card = deck.pop()
            mine["cards"].append(card)
            state["deck"] = deck
            total, _ = hand_total(mine["cards"])
            _event(state, "hit", when, seat=mine["seat"], card=card)
            if total >= 21:
                mine["stood"] = True
            _blackjack_next_turn(one, when)
            return f"{card} — {total}" + (" · перебор" if total > 21 else "")
        if move == "stand":
            mine["stood"] = True
            _event(state, "stand", when, seat=mine["seat"])
            _blackjack_next_turn(one, when)
            return "Хватит."
        raise CasinoError("Непонятный ход.")

    return await command(db, table_id, player.user_id, change, command_id, moment)


async def open_card(
    db: Database, player: Player, table_id: int,
    command_id: str = "", now: int | None = None,
) -> str:
    """Открыть свою карту в «Старшей карте». Скорость ничего не решает."""
    moment = now_ts() if now is None else now

    def change(one: Table, when: int) -> str:
        if one.game != "highcard":
            raise CasinoError("Тут нечего открывать.")
        mine = _my_seat(one, player.user_id)
        if one.phase != "play" or not mine.get("cards"):
            raise CasinoError("Карты ещё не сданы.")
        mine["open"] = True
        card = mine["cards"][-1]
        (one.state["result"]["hands"])[mine["seat"]] = card
        _event(one.state, "open", when, seat=mine["seat"], card=card)
        return f"Твоя карта: {card}."

    return await command(db, table_id, player.user_id, change, command_id, moment)


# ---------- лобби и снимок стола ----------


async def _tidy_tables(db: Database, code: str, now: int) -> list[dict]:
    """Столы этой игры: лишние пустые закрываются, один остаётся всегда.

    Столы открываются по мере надобности — за каждым четыре места, и
    пятому нужен следующий. Если их не прибирать, к утру в лобби будет
    полсотни пустых, и каждый взгляд на лобби будет их досчитывать.

    Правило простое: самый первый стол игры живёт всегда, остальные
    пустые закрываются. Так их ровно столько, сколько нужно сидящим,
    плюс один про запас.
    """
    rows = await db.casino_tables(code)
    kept = []
    for number, row in enumerate(rows):
        seats = await db.casino_seats(int(row["id"]))
        if seats or number == 0:
            kept.append(row)
            continue
        await db.casino_close(int(row["id"]), now)
    return kept


async def _free_table(db: Database, code: str, now: int) -> int:
    """Стол этой игры со свободным местом. Нет такого — открыть новый."""
    rules = game(code)
    for row in await db.casino_tables(code):
        taken = await db.casino_seats(int(row["id"]))
        if len(taken) < rules.seats:
            return int(row["id"])
    table = await open_table(db, code, now)
    return table.id


async def lobby(
    db: Database, user_id: int, now: int | None = None
) -> dict[str, Any]:
    """Что показать на входе: столы, занятость, фаза и свой кошелёк.

    Стол со свободным местом есть всегда: кончились — открывается
    новый. Пятый участник не становится пятым за столом, ему
    предлагается другой — это и есть «другой стол» из ТЗ.
    """
    moment = now_ts() if now is None else now
    rows = []
    for rules in GAME_LIST:
        await _tidy_tables(db, rules.code, moment)
        await _free_table(db, rules.code, moment)
        tables = []
        for row in await db.casino_tables(rules.code):
            table = await look(db, int(row["id"]), moment)
            seats = await db.casino_seats(table.id)
            tables.append({
                "id": table.id,
                "taken": len(seats),
                "seats": rules.seats,
                "phase": table.phase,
                "phase_title": PHASE_TITLES.get(table.phase, table.phase),
                "until": int(table.state.get("until") or 0),
                "round": table.round,
                "mine": any(int(one["user_id"]) == user_id for one in seats),
            })
        rows.append({
            "code": rules.code,
            "title": rules.title,
            "about": rules.about,
            "emoji": rules.emoji,
            "cover": cover_url(rules.code),
            "least": rules.least,
            "most": rules.most,
            "step": rules.step,
            "tables": tables,
        })
    where = await db.casino_seat_of(user_id)
    return {
        "games": rows,
        "wallet": await db.casino_wallet(user_id),
        "seat": dict(where) if where else None,
        # Часы дома: ночью залы закрыты, и экран говорит об этом сам,
        # а не отказом на нажатие
        "open": open_now(moment),
        "edge": opens_at(moment),
    }


PHASE_TITLES = {
    "wait": "Ждём игроков",
    "accept": "Приём ставок",
    "play": "Розыгрыш",
    "turns": "Ходят игроки",
    "dealer": "Крупье",
    "done": "Расчёт",
}


def _seat_view(
    table: Table, seat: dict, user_id: int, shown: bool
) -> dict[str, Any]:
    """Место глазами этого игрока: своё целиком, чужое — без карт.

    Клиент получает только доступные ему карты. Не потому, что
    подглядывать некрасиво, а потому, что иначе их видно в ответе
    сервера — и чужая закрытая карта перестаёт быть закрытой.
    """
    mine = int(seat.get("user") or 0) == user_id
    cards = list(seat.get("cards") or [])
    if table.game == "highcard":
        # Своя карта видна владельцу всегда, чужая — после вскрытия
        open_now = bool(seat.get("open")) or shown
        shows = cards if (mine or open_now) else []
    else:
        shows = cards if (mine or shown) else []
    total, soft = hand_total(cards) if cards else (0, False)
    return {
        "seat": seat["seat"],
        "user": seat["user"],
        "name": seat["name"],
        "avatar": seat.get("avatar") or "🥊",
        "me": mine,
        "bet": int(seat.get("bet") or 0),
        "pick": seat.get("pick") or "",
        "bets": list(seat.get("bets") or []) if mine else [],
        "ready": bool(seat.get("ready")),
        "gone": bool(seat.get("gone")),
        "cards": shows,
        "count": len(cards),
        "open": bool(seat.get("open")),
        "total": total if (mine or shown) else 0,
        "soft": soft if (mine or shown) else False,
        "stood": bool(seat.get("stood")),
        "said": seat.get("said") or "",
        # Разбор словами показывают только своему месту: чужой расчёт
        # на экране — это чужие деньги, и место ему не здесь
        "why": (seat.get("why") or "") if mine else "",
        "paid": int(seat.get("paid") or 0),
        "turn": str(table.state.get("turn") or "") == seat["seat"],
    }


async def table_view(
    db: Database, table_id: int, user_id: int, now: int | None = None
) -> dict[str, Any]:
    """Снимок стола для экрана. Всё время — серверное.

    Клиент ничего не решает сам: он берёт фазу, её срок и номер партии
    и рисует по ним. Вернувшийся из фона запрашивает тот же снимок и
    переходит к текущей фазе, а не доигрывает пропущенное.
    """
    moment = now_ts() if now is None else now
    table = await look(db, table_id, moment)
    rules = table.rules
    shown = table.phase == "done"
    seats = []
    for number in range(1, rules.seats + 1):
        seat = (table.state.get("seats") or {}).get(str(number))
        if seat is None or not seat.get("user"):
            seats.append({"seat": str(number), "user": 0, "empty": True})
            continue
        seats.append(_seat_view(table, seat, user_id, shown))
    result = dict(table.state.get("result") or {}) or None
    if result and table.game == "blackjack" and not shown:
        # Вторая карта крупье до расчёта не уходит на клиент вовсе
        result = {"upcard": result.get("upcard", ""), "peek": result.get("peek")}
    return {
        "id": table.id,
        "game": table.game,
        "title": rules.title,
        "about": rules.about,
        "emoji": rules.emoji,
        "cover": cover_url(table.game),
        "seats": seats,
        "phase": table.phase,
        "phase_title": PHASE_TITLES.get(table.phase, table.phase),
        "round": table.round,
        "until": int(table.state.get("until") or 0),
        "since": int(table.state.get("since") or 0),
        "now": moment,
        "turn": str(table.state.get("turn") or ""),
        "result": result,
        # Прошлая партия: у рулетки приём открывается сразу после
        # расчёта, и прошлый карман с выплатой ещё нужен на экране
        "last": table.state.get("last"),
        "events": list(table.state.get("events") or []),
        "least": rules.least,
        "most": rules.most,
        "step": rules.step,
        "accept": rules.accept,
        "open": open_now(moment),
        "edge": opens_at(moment),
        "shut": "" if open_now(moment) else shut_words(moment),
        # Что повторять: сумма прошлой ставки этого игрока. Ноль —
        # кнопки «Повторить» на экране нет вовсе
        "again": _again_for(table, user_id),
        "wallet": await db.casino_wallet(user_id),
        "mine": any(one.get("me") for one in seats),
    }


def _again_for(table: Table, user_id: int) -> int:
    """Сколько было поставлено в прошлой партии этим игроком."""
    for row in ((table.state.get("last") or {}).get("seats") or {}).values():
        if int(row.get("user") or 0) == user_id:
            return int(row.get("bet") or 0)
    return 0


async def my_table(db: Database, user_id: int) -> int:
    """За каким столом сидит боец. Ноль — ни за каким."""
    where = await db.casino_seat_of(user_id)
    return int(where["table_id"]) if where else 0
