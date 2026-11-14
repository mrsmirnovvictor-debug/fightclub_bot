"""Знаки кланов: семнадцать картинок, и каждая достаётся одному клану.

Знак выбирают при создании клана, и выбранный уходит из списка: двух
семей под одним знаком в городе не бывает. Отсюда и потолок — пока
нарисовано семнадцать знаков, столько кланов в городе и может быть.
Нарисуют ещё — список вырастет, и больше ничего править не придётся.

У каждого знака есть имя, с которым он приехал. Это не имя клана, а
подсказка: имя клана пишет игрок сам, и первым, что ему предложат в
поле, будет имя знака.

Сами картинки лежат в бакете под кодом знака (`clans/black_ravens.png`),
правила — в `bot.game.clans`, а выбор при создании — в
`bot.clan_service`.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.game import art


@dataclass(frozen=True)
class Sign:
    """Знак клана: код, имя из раздачи и картинка."""

    code: str
    title: str

    @property
    def picture(self) -> str:
        return art.clan_sign(self.code)


SIGNS: tuple[Sign, ...] = (
    # Первый знак приехал под именем файла `mafia`: его рисовали раньше
    # прочих, вместе с самой мыслью про кланы, и переименовывать чужой
    # бакет не наше дело
    Sign("mafia", "Кровавые черепа"),
    Sign("black_ravens", "Чёрные вороны"),
    Sign("blood_roses", "Кровавые розы"),
    Sign("silver_wolves", "Серебряные волки"),
    Sign("golden_serpents", "Золотые змеи"),
    Sign("ace_of_spades", "Туз пик"),
    Sign("iron_fist", "Железный кулак"),
    Sign("night_foxes", "Ночные лисы"),
    Sign("crimson_dragons", "Багровые драконы"),
    Sign("black_panthers", "Чёрные пантеры"),
    Sign("bone_kings", "Костяные короли"),
    Sign("amber_scorpions", "Янтарные скорпионы"),
    Sign("steel_bulls", "Стальные быки"),
    Sign("ash_order", "Пепельный орден"),
    Sign("deep_sharks", "Глубинные акулы"),
    Sign("violet_jesters", "Фиолетовые шуты"),
    Sign("key_keepers", "Хранители ключей"),
)

BY_CODE: dict[str, Sign] = {sign.code: sign for sign in SIGNS}


def get_sign(code: str) -> Sign | None:
    """Знак по коду. None — такого знака не рисовали."""
    return BY_CODE.get(code)


__all__ = ["BY_CODE", "SIGNS", "Sign", "get_sign"]
