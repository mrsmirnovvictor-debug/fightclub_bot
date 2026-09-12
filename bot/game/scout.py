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
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from bot.game.classes import ALL_ZONES

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
            if move.number == 1:
                habits.first_turns += 1
                habits.first_attacks.update(move.attacks)
                habits.first_blocks.update(move.block)
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
class Advice:
    """Две строки: одна над ударами, другая над блоками."""

    attack: str = ""
    block: str = ""
    title: str = ""

    @property
    def empty(self) -> bool:
        return not (self.attack or self.block)

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "attack": self.attack, "block": self.block}


def opening(habits: Habits) -> Advice:
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
    )


def _fights_word(count: int) -> str:
    tail = count % 10
    if count % 100 in range(11, 15) or tail in (0, 5, 6, 7, 8, 9):
        return "боёв"
    return "бой" if tail == 1 else "боя"


def trend(habits: Habits, last: Move) -> Advice:
    """Подсказка по следу: что соперник делал после такого же хода.

    Похожих случаев мало — не выдумываем закономерность, а говорим, что
    боец делает вообще. Честнее и полезнее, чем проценты из двух ходов.
    """
    return Advice(attack=_next_block(habits, last), block=_next_attack(habits, last))


def _next_attack(habits: Habits, last: Move) -> str:
    """Куда соперник ударит: смотрим, чем кончился его прошлый удар."""
    if not last.attacks:
        return _in_general(habits.attacks, habits.turns, "бьёт")
    was = (
        f"В прошлом ходу соперник бил в {listed(last.attacks)} и "
        f"{'попал' if last.landed else 'не дошёл'}."
    )
    cases = habits.after_attack.get(last.key_attack)
    total = sum(cases.values()) if cases else 0
    if not cases or total < MIN_CASES:
        return was + " " + _in_general(habits.attacks, habits.turns, "бьёт")
    zone, count = cases.most_common(1)[0]
    return (
        f"{was} После таких он обычно бьёт в {zone_title(zone)} "
        f"({count / total:.0%}, случаев: {total})."
    )


def _next_block(habits: Habits, last: Move) -> str:
    """Что соперник закроет: смотрим, как он стоял в прошлом ходу."""
    if not last.block:
        return _in_general(habits.blocks, habits.turns, "закрывает")
    was = f"В прошлом ходу соперник закрывал {listed(last.block)}"
    was += f" и пропустил в {listed(last.took)}." if last.took else " и выстоял."
    cases = habits.after_block.get(last.key_block)
    total = sum(cases.values()) if cases else 0
    if not cases or total < MIN_CASES:
        return was + " " + _in_general(habits.blocks, habits.turns, "закрывает")
    block, count = cases.most_common(1)[0]
    return (
        f"{was} После таких он обычно закрывает {listed(block)} "
        f"({count / total:.0%}, случаев: {total})."
    )


def _in_general(counts: Counter, turns: int, verb: str) -> str:
    rows = shares(counts, turns)
    if not rows or not rows[0][1]:
        return "Похожих ходов в его боях не было."
    return f"Вообще он чаще {verb} {named(rows, 2)}."


def advise(habits: Habits, turns: list[dict[str, Any]], rival_id: int) -> Advice:
    """Что сказать бойцу перед этим ходом.

    `turns` — уже закончившиеся ходы текущего боя, и только они. Пока ход
    не посчитан, его в этом списке нет: аналитик не знает, что соперник
    нажал прямо сейчас, и знать не должен.
    """
    if not habits.known:
        return Advice(title="Соперник новичок: разбирать пока нечего.")
    moves = moves_of(turns, rival_id)
    if not moves:
        return opening(habits)
    return trend(habits, moves[-1])


__all__ = [
    "Advice",
    "Habits",
    "MIN_CASES",
    "Move",
    "SCOUT_FIGHTS",
    "advise",
    "moves_of",
    "opening",
    "read_habits",
    "trend",
]
