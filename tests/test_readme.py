"""README не расходится с кодом там, где в нём стоят числа.

Числом в документации пользуются как правдой: по нему игроку объясняют,
почему у соседа с меньшей выносливостью здоровья больше. Разойдясь с
кодом, такое число хуже отсутствующего — оно врёт уверенно.

Здесь проверяются ровно те таблицы README, которые можно сверить с
источником. Таблица запаса здоровья первая: именно её отсутствие и
завело в тупик — цены очка выносливости не было видно нигде, и
разница между классами читалась как ошибка расчёта.
"""

from __future__ import annotations

import pathlib
import re

from bot.game.classes import FIGHTER_CLASSES

README = pathlib.Path(__file__).resolve().parent.parent / "README.md"

# Строка таблицы: | ⚔️ Воин | 45 | +7 |
ROW = re.compile(r"^\|\s*\S+\s+(\S+)\s*\|\s*(\d+)\s*\|\s*\+(\d+)\s*\|$", re.M)


def _table() -> dict[str, tuple[int, int]]:
    """Запас класса и прибавка за очко выносливости — как в README."""
    text = README.read_text(encoding="utf-8")
    start = text.index("#### Сколько у класса здоровья")
    end = text.index("Полный запас", start)
    return {
        title: (int(base), int(gain))
        for title, base, gain in ROW.findall(text[start:end])
    }


def test_the_readme_tells_the_truth_about_health():
    """Запас класса и цена очка выносливости в README — те же, что в коде."""
    told = _table()
    real = {
        one.title: (one.hp_base, one.hp_per_endurance)
        for one in FIGHTER_CLASSES.values()
    }
    assert told == real, "таблица запаса здоровья в README разошлась с кодом"


def test_the_price_of_endurance_is_not_the_same_for_everyone():
    """Общей цены очка выносливости нет — на этом и строится README.

    Если однажды все классы сравняются, абзац про «боец с меньшей
    выносливостью носит больше здоровья» станет неправдой, и его надо
    будет переписать, а не оставлять как есть.
    """
    gains = {one.hp_per_endurance for one in FIGHTER_CLASSES.values()}
    bases = {one.hp_base for one in FIGHTER_CLASSES.values()}
    assert len(gains) > 1, "цена очка выносливости стала общей"
    assert len(bases) > 1, "запас класса стал общим"
