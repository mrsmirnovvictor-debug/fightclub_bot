"""README не расходится с кодом там, где в нём стоят числа.

Числом в документации пользуются как правдой: по нему игроку объясняют,
сколько здоровья принесёт вложенное очко. Разойдясь с кодом, такое
число хуже отсутствующего — оно врёт уверенно.

Здесь проверяется цена очка выносливости. Именно её отсутствие в
документации однажды и завело в тупик: запас складывался из трёх
слагаемых, ни одного из них не было видно, и разница между классами
читалась как ошибка расчёта.
"""

from __future__ import annotations

import pathlib
import re

from bot.game.classes import FIGHTER_CLASSES, Stats
from bot.game.stats import HP_BASE, HP_PER_ENDURANCE, derive

README = pathlib.Path(__file__).resolve().parent.parent / "README.md"

# Формула из README: `здоровье = 30 + 🫀 выносливость × 8 + запас ...`
RULE = re.compile(r"здоровье = (\d+) \+ 🫀 выносливость × (\d+)")


def test_the_readme_tells_the_truth_about_the_health_rule():
    """Формула запаса в README — та же, что в коде: и база, и цена очка."""
    told = RULE.search(README.read_text(encoding="utf-8"))
    assert told is not None, "формулы запаса здоровья в README больше нет"
    assert (int(told.group(1)), int(told.group(2))) == (
        HP_BASE, HP_PER_ENDURANCE
    ), "формула запаса здоровья в README разошлась с кодом"


def test_health_does_not_depend_on_the_class_any_more():
    """Запас у всех классов одинаковый при одной выносливости.

    README обещает, что класс на живучесть не влияет вовсе. Вернись
    классу свой запас или своя цена очка — обещание станет неправдой, и
    заметить это надо здесь, а не по жалобе игрока.
    """
    same = Stats(strength=5, agility=5, intuition=5, endurance=7)
    sizes = {
        derive(one, same).max_hp for one in FIGHTER_CLASSES.values()
    }
    assert sizes == {HP_BASE + 7 * HP_PER_ENDURANCE}, (
        "класс снова влияет на запас"
    )


def test_level_adds_nothing_to_health_by_itself():
    """Уровень сам по себе запаса не даёт — его приносит выносливость.

    Иначе за уровень платили бы дважды: очком выносливости и прибавкой
    сверх него.
    """
    one = FIGHTER_CLASSES["warrior"]
    same = Stats(strength=4, agility=3, intuition=3, endurance=4)
    assert derive(one, same).max_hp == HP_BASE + 4 * HP_PER_ENDURANCE
    # И единственное, что ещё входит в запас, — вещи и выпитое
    assert derive(one, same, 25).max_hp == HP_BASE + 4 * HP_PER_ENDURANCE + 25
