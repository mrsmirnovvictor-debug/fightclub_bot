"""Аналитик боя: что известно о сопернике по его прошлым боям.

Подписка PRO даёт бойцу не силу, а знание. Перед боем аналитик поднимает
последние бои соперника и считает его привычки: куда тот бьёт первым
ходом, что закрывает, и что обычно делает следующим ходом после такого же
хода, как только что случился.

Правил боя здесь нет — только счёт по чужому логу. Поэтому модуль лежит в
`game`: на него смотрит и веб, и служба боёв, и ни один не тянет другой.

Два правила, которые важнее точности.

**Только прошлое.** Аналитик читает законченные ходы и ничего больше. Ни
текущий выбор соперника, ни его нажатые кнопки сюда не попадают и попасть
не могут: подсказка, знающая чужой ход, — это не аналитика, а подглядывание.

**Честная неуверенность.** Если похожих ходов в прошлом было мало, лучше
сказать «вообще он бьёт так-то», чем выдать за закономерность два случая.
Поэтому у условных подсказок есть порог, ниже которого аналитик переходит
на общий счёт и говорит об этом словами.

Сверх разбора аналитик даёт **совет**: куда бить и что закрывать. Разбор —
это два предложения с процентами, и прочесть их между ходами успевает не
каждый; совет говорит одно действие и одно число. Считается он по тому же
распределению, что и предложение над ним, — иначе совет спорил бы с
разбором, а это хуже, чем совета не иметь вовсе.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from bot.game.classes import ALL_ZONES, BLOCK_WIDTH, block_combos

# Сколько последних боёв соперника поднимает аналитик
SCOUT_FIGHTS = 10
# Меньше этого числа похожих случаев — не закономерность, а совпадение
MIN_CASES = 3
# Сколько зон называть в строке: три — это и есть «реже всего» или «чаще всего»
NAMED_ZONES = 3

# Исход удара глазами аналитика: попал, не дошёл или вовсе не бил. Мелкие
# различия (крит, пробитие блока) для привычек не важны — важно, получилось
# у бойца или нет, и от этого он пляшет в следующем ходу
LANDED = {"hit", "crit", "break"}
STOPPED = {"block", "dodge", "counter"}

# Зоны в винительном падеже: «блокирует Голову», «бьёт в Ноги». Меняется
# от именительного только голова, но писать «блокирует Голова» нельзя —
# подсказку читают глазами, а не парсером
ZONE_TITLES: dict[str, str] = {
    zone.value: zone.title.capitalize() for zone in ALL_ZONES
}
ZONE_TITLES["head"] = "Голову"


def zone_title(code: str) -> str:
    return ZONE_TITLES.get(code, code)


def listed(codes: Iterable[str]) -> str:
    """«Ноги и Голову» — перечисление зон человеческим языком."""
    names = [zone_title(code) for code in codes]
    if not names:
        return "—"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " и " + names[-1]


def shares(counts: Counter, total: int) -> list[tuple[str, float]]:
    """Зоны с долями, чаще всего сверху. Пустой счёт — пустой список."""
    if total <= 0:
        return []
    return sorted(
        ((code, counts.get(code, 0) / total) for code in ZONE_TITLES),
        key=lambda pair: (-pair[1], pair[0]),
    )


def named(pairs: list[tuple[str, float]], count: int = NAMED_ZONES) -> str:
    """«Голову — 45%, Ноги — 10% и Пояс — 5%».

    Нули не называем: «Живот — 0%» — это не наблюдение, а пустая строка. А
    вот ноль в списке «реже всего» смысл имеет — туда он и попадает своим
    порядком, если зон с попаданиями меньше трёх.
    """
    rows = [pair for pair in pairs if pair[1]][:count] or pairs[:1]
    parts = [f"{zone_title(code)} — {share:.0%}" for code, share in rows]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " и " + parts[-1]


# ---------- что боец сделал за один ход ----------


@dataclass(frozen=True)
class Move:
    """Ход одного бойца: куда бил, что закрывал и чем это кончилось."""

    number: int
    attacks: tuple[str, ...] = ()
    block: tuple[str, ...] = ()
    landed: bool = False  # хоть один его удар дошёл
    took: tuple[str, ...] = ()  # куда пропустил сам

    @property
    def key_attack(self) -> tuple[str, bool] | None:
        """Чем описывается его удар для поиска похожих случаев."""
        return (self.attacks[0], self.landed) if self.attacks else None

    @property
    def key_block(self) -> tuple[tuple[str, ...], bool] | None:
        """Чем описывается его защита: что закрывал и пропустил ли."""
        return (self.block, bool(self.took)) if self.block else None


def moves_of(turns: list[dict[str, Any]], user_id: int) -> list[Move]:
    """Ходы этого бойца по логу боя — по одному на ход."""
    moves: list[Move] = []
    for turn in turns:
        strikes = turn.get("strikes") or []
        attacks = tuple(
            strike["zone"]
            for strike in strikes
            if strike.get("attacker_id") == user_id and strike.get("zone")
        )
        landed = any(
            strike.get("outcome") in LANDED
            for strike in strikes
            if strike.get("attacker_id") == user_id
        )
        mine = [strike for strike in strikes if strike.get("defender_id") == user_id]
        block = tuple(mine[0].get("block") or ()) if mine else ()
        took = tuple(
            strike["zone"]
            for strike in mine
            if strike.get("outcome") in LANDED and strike.get("zone")
        )
        if not attacks and not block and not mine:
            continue
        moves.append(
            Move(
                number=int(turn.get("number") or len(moves) + 1),
                attacks=attacks,
                block=block,
                landed=landed,
                took=took,
            )
        )
    return moves


# ---------- привычки ----------


@dataclass
class Habits:
    """Привычки бойца, собранные по его прошлым боям."""

    fights: int = 0
    turns: int = 0
    first_turns: int = 0
    # Ходы, в которых он ставил блок, и в которых бил. Долю «закроет эту
    # зону» считаем от блоков, а не от ходов: ход без блока к делу не
    # относится, а от него доля выходила бы заниженной
    block_turns: int = 0
    first_block_turns: int = 0
    attacks: Counter = field(default_factory=Counter)
    blocks: Counter = field(default_factory=Counter)
    first_attacks: Counter = field(default_factory=Counter)
    first_blocks: Counter = field(default_factory=Counter)
    # После такого же хода — что он делал следующим
    after_attack: dict[tuple[str, bool], Counter] = field(default_factory=dict)
    after_block: dict[tuple[tuple[str, ...], bool], Counter] = field(
        default_factory=dict
    )

    @property
    def known(self) -> bool:
        """Есть ли вообще о чём говорить."""
        return self.turns > 0


def read_habits(fights: list[list[dict[str, Any]]], user_id: int) -> Habits:
    """Собрать привычки бойца по логам его боёв."""
    habits = Habits()
    for turns in fights:
        moves = moves_of(turns, user_id)
        if not moves:
            continue
        habits.fights += 1
        for index, move in enumerate(moves):
            habits.turns += 1
            habits.attacks.update(move.attacks)
            habits.blocks.update(move.block)
            if move.block:
                habits.block_turns += 1
            if move.number == 1:
                habits.first_turns += 1
                habits.first_attacks.update(move.attacks)
                habits.first_blocks.update(move.block)
                if move.block:
                    habits.first_block_turns += 1
            if index + 1 >= len(moves):
                continue
            following = moves[index + 1]
            attack_key = move.key_attack
            if attack_key is not None and following.attacks:
                habits.after_attack.setdefault(attack_key, Counter())[
                    following.attacks[0]
                ] += 1
            block_key = move.key_block
            if block_key is not None and following.block:
                habits.after_block.setdefault(block_key, Counter())[
                    following.block
                ] += 1
    return habits


# ---------- подсказки ----------


@dataclass(frozen=True)
class Read:
    """Что аналитик насчитал об одной стороне хода: словами и числами.

    `shares` — доли по зонам, из которых собрана строка. Совет считается
    по ним же: одно распределение на предложение и на совет, чтобы они не
    расходились.
    """

    line: str = ""
    shares: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Tip:
    """Совет: одно действие и одно число, почему именно оно."""

    move: str = ""  # «Бей в Корпус»
    why: str = ""  # «он закроет его с вероятностью 25%»

    @property
    def empty(self) -> bool:
        return not self.move

    def as_dict(self) -> dict[str, str]:
        return {"move": self.move, "why": self.why}


@dataclass(frozen=True)
class Advice:
    """Две строки разбора и два совета: над ударами и над блоками."""

    attack: str = ""
    block: str = ""
    title: str = ""
    attack_tip: Tip = field(default_factory=Tip)
    block_tip: Tip = field(default_factory=Tip)

    @property
    def empty(self) -> bool:
        return not (self.attack or self.block)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "attack": self.attack,
            "block": self.block,
            "attack_tip": self.attack_tip.as_dict(),
            "block_tip": self.block_tip.as_dict(),
        }


# ---------- из долей в совет ----------


def weakest_zone(shares: dict[str, float]) -> tuple[str, float] | None:
    """Зона, которую соперник закрывает реже прочих, — туда и бить.

    Ничьи разводим порядком зон на кольце: совет должен быть один и тот
    же при одних и тех же числах, иначе он выглядит гаданием.
    """
    order = {zone.value: index for index, zone in enumerate(ALL_ZONES)}
    rows = [(code, share) for code, share in shares.items() if code in order]
    if not rows:
        return None
    return min(rows, key=lambda pair: (pair[1], order[pair[0]]))


def best_block(
    shares: dict[str, float], width: int = BLOCK_WIDTH
) -> tuple[tuple[str, ...], float]:
    """Блок, накрывающий наибольшую долю его ударов.

    Перебираем не пары зон, а настоящие блоки: закрыть можно только
    смежные зоны кольца, и совет обязан быть нажимаемой кнопкой.
    """
    best: tuple[str, ...] = ()
    score = -1.0
    for combo in block_combos(width):
        codes = tuple(zone.value for zone in combo)
        total = sum(shares.get(code, 0.0) for code in codes)
        if total > score:
            best, score = codes, total
    return best, max(0.0, score)


def strike_tip(shares: dict[str, float]) -> Tip:
    """Совет по удару: бить туда, где он реже всего держит защиту."""
    weakest = weakest_zone(shares)
    if weakest is None:
        return Tip()
    code, share = weakest
    return Tip(
        move=f"Бей в {zone_title(code)}",
        why=f"он закроет его с вероятностью {share:.0%}",
    )


def guard_tip(shares: dict[str, float], width: int = BLOCK_WIDTH) -> Tip:
    """Совет по блоку: закрывать то, куда он бьёт чаще всего."""
    if not shares:
        return Tip()
    combo, share = best_block(shares, width)
    if not combo:
        return Tip()
    return Tip(
        move="Закрывай " + "+".join(zone_title(code) for code in combo),
        why=f"вероятность отбить удар {share:.0%}",
    )


def opening(habits: Habits, width: int = BLOCK_WIDTH) -> Advice:
    """Разбор до первого удара: что соперник обычно делает на старте."""
    if not habits.first_turns:
        return Advice()
    # Блок закрывает две зоны из пяти, поэтому доли по зонам в сумме дают
    # двести процентов — это не ошибка счёта, а два числа на один ход
    # «Реже всего» считаем по тем зонам, которые он вообще закрывал: зона,
    # не закрытая ни разу, — это не редкость, а дыра, и о ней скажет само
    # отсутствие. Если закрытых зон меньше трёх, добираем нулями
    blocked = shares(habits.first_blocks, habits.first_turns)
    rare = list(reversed([pair for pair in blocked if pair[1]])) or blocked[-1:]
    often = shares(habits.first_attacks, habits.first_turns)
    return Advice(
        title=f"Разбор соперника: {habits.fights} "
        f"{_fights_word(habits.fights)}, {habits.turns} ходов.",
        attack="По статистике в первом ходу соперник реже всего блокирует "
        f"{named(rare)}.",
        block="По статистике соперник чаще всего наносит первый удар в "
        f"{named(often)}.",
        attack_tip=strike_tip(_from_zones(habits.first_blocks, habits.first_block_turns)),
        block_tip=guard_tip(_from_zones(habits.first_attacks), width),
    )


def _fights_word(count: int) -> str:
    tail = count % 10
    if count % 100 in range(11, 15) or tail in (0, 5, 6, 7, 8, 9):
        return "боёв"
    return "бой" if tail == 1 else "боя"


def trend(habits: Habits, last: Move, width: int = BLOCK_WIDTH) -> Advice:
    """Подсказка по следу: что соперник делал после такого же хода.

    Похожих случаев мало — не выдумываем закономерность, а говорим, что
    боец делает вообще. Честнее и полезнее, чем проценты из двух ходов.
    """
    guard = _next_block(habits, last)
    strike = _next_attack(habits, last)
    return Advice(
        attack=guard.line,
        block=strike.line,
        # Совет считаем по тому же распределению, из которого собрана
        # строка над ним: не хватило похожих случаев — и строка, и совет
        # разом переходят на общий счёт
        attack_tip=strike_tip(guard.shares),
        block_tip=guard_tip(strike.shares, width),
    )


def _from_zones(counts: Counter, total: int = 0) -> dict[str, float]:
    """Доли по зонам. Ноль в знаменателе — считаем от суммы счёта.

    Два разных вопроса и два разных знаменателя. «Куда он ударит» — доля
    от всех его ударов: сумма по зонам даёт сто процентов, и накрытые
    блоком зоны честно складываются в «столько ударов отобьёшь». «Закроет
    ли он эту зону» — доля от его блоков: каждый блок держит две зоны
    сразу, и сумма по зонам даёт двести процентов, но по отдельной зоне
    это по-прежнему вероятность, что она закрыта.
    """
    denominator = total or sum(counts.values())
    if denominator <= 0:
        return {}
    return {code: counts.get(code, 0) / denominator for code in ZONE_TITLES}


def _next_attack(habits: Habits, last: Move) -> Read:
    """Куда соперник ударит: смотрим, чем кончился его прошлый удар."""
    overall = _from_zones(habits.attacks)
    if not last.attacks:
        return Read(_in_general(habits.attacks, habits.turns, "бьёт"), overall)
    was = (
        f"В прошлом ходу соперник бил в {listed(last.attacks)} и "
        f"{'попал' if last.landed else 'не дошёл'}."
    )
    cases = habits.after_attack.get(last.key_attack)
    total = sum(cases.values()) if cases else 0
    if not cases or total < MIN_CASES:
        return Read(
            was + " " + _in_general(habits.attacks, habits.turns, "бьёт"), overall
        )
    zone, count = cases.most_common(1)[0]
    return Read(
        f"{was} После таких он обычно бьёт в {zone_title(zone)} "
        f"({count / total:.0%}, случаев: {total}).",
        _from_zones(cases, total),
    )


def _next_block(habits: Habits, last: Move) -> Read:
    """Что соперник закроет: смотрим, как он стоял в прошлом ходу."""
    overall = _from_zones(habits.blocks, habits.block_turns)
    if not last.block:
        return Read(_in_general(habits.blocks, habits.turns, "закрывает"), overall)
    was = f"В прошлом ходу соперник закрывал {listed(last.block)}"
    was += f" и пропустил в {listed(last.took)}." if last.took else " и выстоял."
    cases = habits.after_block.get(last.key_block)
    total = sum(cases.values()) if cases else 0
    if not cases or total < MIN_CASES:
        return Read(
            was + " " + _in_general(habits.blocks, habits.turns, "закрывает"), overall
        )
    block, count = cases.most_common(1)[0]
    # Ключи здесь — целые блоки, а не зоны: разбираем их на зоны, иначе
    # совет считался бы по парам и про отдельную зону не сказал бы ничего
    covered: Counter = Counter()
    for combo, seen in cases.items():
        covered.update({code: seen for code in combo})
    return Read(
        f"{was} После таких он обычно закрывает {listed(block)} "
        f"({count / total:.0%}, случаев: {total}).",
        _from_zones(covered, total),
    )


def _in_general(counts: Counter, turns: int, verb: str) -> str:
    rows = shares(counts, turns)
    if not rows or not rows[0][1]:
        return "Похожих ходов в его боях не было."
    return f"Вообще он чаще {verb} {named(rows, 2)}."


# ---------- босс ----------


def habits_of_temper(
    swings: dict[str, float], covers: dict[str, float], scale: int = 100
) -> Habits:
    """Привычки соперника, у которого их знают наперёд.

    У живого бойца привычки считают по его прошлым боям; у рейд-босса
    считать нечего — он не игрок, и боёв за ним не записано. Зато у него
    есть характер, заданный весами, и эти веса и есть его привычки, без
    погрешности выборки.

    `swings` — доли зон удара в сумме на сотню. `covers` — как часто
    каждая зона оказывается закрытой; сумма тут больше сотни, потому что
    блок держит несколько зон разом. `scale` — сколько «ходов» они
    описывают: знаменатель, от которого аналитик считает проценты.

    Первый ход у босса ничем не отличается от прочих: ритуалов у него
    нет, и разбор до первого удара идёт по тем же числам.
    """
    attacks = Counter({code: share for code, share in swings.items() if share})
    blocks = Counter({code: share for code, share in covers.items() if share})
    return Habits(
        fights=0,
        turns=scale,
        first_turns=scale,
        block_turns=scale,
        first_block_turns=scale,
        attacks=attacks,
        blocks=blocks,
        first_attacks=Counter(attacks),
        first_blocks=Counter(blocks),
    )


def advise(
    habits: Habits,
    turns: list[dict[str, Any]],
    rival_id: int,
    width: int = BLOCK_WIDTH,
) -> Advice:
    """Что сказать бойцу перед этим ходом.

    `turns` — уже закончившиеся ходы текущего боя, и только они. Пока ход
    не посчитан, его в этом списке нет: аналитик не знает, что соперник
    нажал прямо сейчас, и знать не должен.
    """
    if not habits.known:
        return Advice(title="Соперник новичок: разбирать пока нечего.")
    moves = moves_of(turns, rival_id)
    if not moves:
        return opening(habits, width)
    return trend(habits, moves[-1], width)


__all__ = [
    "Advice",
    "Habits",
    "MIN_CASES",
    "Move",
    "Read",
    "SCOUT_FIGHTS",
    "Tip",
    "advise",
    "best_block",
    "guard_tip",
    "habits_of_temper",
    "moves_of",
    "opening",
    "read_habits",
    "strike_tip",
    "trend",
    "weakest_zone",
]
