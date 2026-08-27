"""Сетка плей-офф: посев, пары и названия кругов.

Чистая арифметика турнира, без базы и без Telegram: кому с кем драться,
кто проходит без боя и как называется круг. Всё остальное — вокруг неё.
"""

from __future__ import annotations

from dataclasses import dataclass

# Сколько бойцов пускают в турнир
ALLOWED_SIZES = (8, 16, 32)
MIN_PLAYERS = 2
# Сколько раз переигрываем ничью, прежде чем судья решит по рейтингу
MAX_REPLAYS = 3

ROUND_TITLES = {
    1: "Финал",
    2: "Полуфиналы",
    4: "Четвертьфиналы",
}


def round_title(matches: int) -> str:
    """Как называется круг, в котором столько пар."""
    if matches in ROUND_TITLES:
        return ROUND_TITLES[matches]
    return f"1/{matches} финала"


def bracket_size(players: int) -> int:
    """Ближайшая сверху степень двойки: под неё и рисуется сетка."""
    size = 1
    while size < max(MIN_PLAYERS, players):
        size *= 2
    return size


def seed_positions(size: int) -> list[int]:
    """Порядок посевов по позициям сетки: первый с последним, второй с предпоследним.

    Классическая раскладка: сильные встречаются не раньше, чем должны.
    """
    order = [0]
    while len(order) < size:
        width = len(order) * 2
        order = [value for seed in order for value in (seed, width - 1 - seed)]
    return order


def first_round(seeded: list[int]) -> list[tuple[int | None, int | None]]:
    """Пары первого круга. Кому не хватило соперника — проходит без боя.

    `seeded` — бойцы в порядке посева: сильнейший первым.
    """
    size = bracket_size(len(seeded))
    slots: list[int | None] = [
        seeded[position] if position < len(seeded) else None
        for position in seed_positions(size)
    ]
    return [(slots[index], slots[index + 1]) for index in range(0, size, 2)]


def next_round(winners: list[int | None]) -> list[tuple[int | None, int | None]]:
    """Пары следующего круга из победителей предыдущего, по порядку сетки."""
    return [
        (winners[index], winners[index + 1] if index + 1 < len(winners) else None)
        for index in range(0, len(winners), 2)
    ]


@dataclass(frozen=True)
class Match:
    """Пара сетки: кто с кем и чем кончилось."""

    round: int
    slot: int
    first_id: int | None
    second_id: int | None
    winner_id: int | None = None
    replays: int = 0

    @property
    def is_bye(self) -> bool:
        """Соперника нет — боец проходит дальше без боя."""
        return (self.first_id is None) != (self.second_id is None)

    @property
    def is_empty(self) -> bool:
        return self.first_id is None and self.second_id is None

    @property
    def bye_winner(self) -> int | None:
        return self.first_id if self.second_id is None else self.second_id

    @property
    def is_done(self) -> bool:
        return self.winner_id is not None or self.is_empty

    @property
    def can_replay(self) -> bool:
        return self.replays < MAX_REPLAYS
