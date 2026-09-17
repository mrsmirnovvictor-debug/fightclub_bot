"""Подбор классовых рецептов энергии: держится ли круг с приёмами.

Шкала у бойца одна, но кормится она тем, в чём силён класс, и цена
события — своя у каждого. Это и есть рычаг: класс, чьи приёмы сильнее,
должен копить медленнее, иначе круг переворачивается.

Скрипт делает две вещи. Без аргументов — гоняет круг на нынешних рецептах
(`ENERGY_SOURCES`) и показывает, сколько энергии набегает за бой. С
`--sweep` — перебирает варианты рецептов и показывает, какие держат круг.

    python scripts/tune_energy.py
    python scripts/tune_energy.py --sweep

Бойцы здесь жмут самый дорогой доступный приём каждый раунд — это худший
случай, живой игрок так не сыграет. Зато если круг держится тут, он
держится и в жизни.
"""

from __future__ import annotations

import argparse
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.content.abilities import CHOICES, STARTER
from bot.game.abilities import ENERGY_SOURCES, Source
from bot.game.classes import FIGHTER_CLASSES
from bot.game.combat import MAX_TURNS, Fighter, random_action, resolve_round
from bot.game.reference import best_kit, developed_stats, equipment_of

# Круг: кто кого должен бить. Те же три пары, что в scripts/simulate.py
CIRCLE = (("rogue", "tank"), ("tank", "assassin"), ("assassin", "rogue"))
LEVELS = (3, 6, 10)


def build(code: str, level: int, uid: int, learn: bool = True) -> Fighter:
    fclass = FIGHTER_CLASSES[code]
    fighter = Fighter(
        uid, fclass.title, fclass, developed_stats(fclass, level), level,
        equipment=equipment_of(best_kit(fclass, level)),
    )
    if learn:
        fighter.loadout.learn(STARTER[code], 1)
        for tier in (3, 6, 10):
            if level >= tier:
                fighter.loadout.learn(CHOICES[code][tier][0], tier)
    return fighter


def press(fighter: Fighter) -> None:
    """Нажать самый дорогой доступный приём — худший случай для баланса."""
    best: tuple[str, int] | None = None
    for code in fighter.loadout.slots:
        cost = fighter.loadout.cost_of(code)
        if fighter.can_use(code) and (best is None or cost > best[1]):
            best = (code, cost)
    if best is None:
        return
    fighter.use(best[0])
    ability = fighter.charges[-1].ability
    # Лечение срабатывает сразу и заготовкой не остаётся
    if ability.heal:
        fighter.heal_by(ability.heal)
        fighter.charges.pop()


def duel(first: str, second: str, level: int, runs: int, seed: int,
         learn: bool = True) -> float:
    """Доля побед первого над вторым. Ничьи в счёт не идут."""
    rng = random.Random(seed)
    wins = decided = 0
    for _ in range(runs):
        one, two = build(first, level, 1, learn), build(second, level, 2, learn)
        for turn in range(1, MAX_TURNS + 1):
            if learn:
                press(one)
                press(two)
            result = resolve_round(
                one, random_action(one, rng), two, random_action(two, rng), turn, rng
            )
            if result.finished:
                if result.winner_id:
                    decided += 1
                    wins += result.winner_id == 1
                break
    return wins / decided if decided else 0.0


def energy_per_fight(code: str, level: int, runs: int, seed: int) -> float:
    """Сколько энергии набегает за бой — без нажатий, чистый доход."""
    rng = random.Random(seed)
    total = fights = 0
    for rival in FIGHTER_CLASSES:
        if rival == code:
            continue
        for _ in range(runs):
            mine, other = build(code, level, 1, False), build(rival, level, 2, False)
            for turn in range(1, MAX_TURNS + 1):
                result = resolve_round(
                    mine, random_action(mine, rng), other,
                    random_action(other, rng), turn, rng
                )
                if result.finished:
                    break
            total += mine.energy
            fights += 1
    return total / fights


def circle(runs: int, seed: int) -> dict[tuple[str, str, int], float]:
    return {
        (first, second, level): duel(first, second, level, runs, seed)
        for first, second in CIRCLE
        for level in LEVELS
    }


def holds(shares: dict) -> bool:
    return all(share > 0.5 for share in shares.values())


def report(runs: int, seed: int) -> None:
    print("Рецепты:")
    for code, recipe in ENERGY_SOURCES.items():
        parts = ", ".join(f"{src.value} {points}" for src, points in recipe.items())
        print(f"  {FIGHTER_CLASSES[code].title:<10} {parts}")

    print("\nЭнергии за бой (10 уровень):")
    for code in FIGHTER_CLASSES:
        print(f"  {FIGHTER_CLASSES[code].title:<10} {energy_per_fight(code, 10, 80, seed):>5.1f}")

    print("\nКруг классов: без приёмов → с приёмами")
    header = "".join(f"{('ур.' + str(level)):>22}" for level in LEVELS)
    print(f"{'пара':<22}{header}")
    shares = {}
    for first, second in CIRCLE:
        cells = []
        for level in LEVELS:
            off = duel(first, second, level, runs, seed, learn=False)
            on = duel(first, second, level, runs, seed)
            shares[(first, second, level)] = on
            cells.append(f"{off:>5.0%} → {on:>5.0%}")
        name = f"{FIGHTER_CLASSES[first].title} → {FIGHTER_CLASSES[second].title}"
        print(f"{name:<22}" + "".join(f"{cell:>22}" for cell in cells))

    worst = min(shares.items(), key=lambda row: row[1])
    print("\nкруг держится" if holds(shares) else
          f"круг не держится: {FIGHTER_CLASSES[worst[0][0]].title} → "
          f"{FIGHTER_CLASSES[worst[0][1]].title} на {worst[0][2]} уровне — {worst[1]:.0%}")


def sweep(runs: int, seed: int) -> None:
    """Перебор рецептов. Меняем только цену главного события каждого класса."""
    grid = {
        "warrior": [(Source.HIT, points) for points in (2, 3, 4)],
        "rogue": [(Source.DODGE, points) for points in (2, 3, 4, 6)],
        "assassin": [(Source.CRIT, points) for points in (6, 10, 14)],
        "tank": [(Source.BLOCK, points) for points in (1, 2)],
    }
    keep = {code: dict(recipe) for code, recipe in ENERGY_SOURCES.items()}
    best: list[tuple[float, dict]] = []
    combos = list(itertools.product(*(grid[code] for code in grid)))
    print(f"вариантов: {len(combos)}\n")
    for combo in combos:
        for code, (source, points) in zip(grid, combo, strict=True):
            ENERGY_SOURCES[code] = dict(keep[code])
            ENERGY_SOURCES[code][source] = points
        shares = circle(runs, seed)
        worst = min(shares.values())
        mark = "держится" if holds(shares) else "        "
        line = " ".join(
            f"{FIGHTER_CLASSES[code].title[:4]} {src.value[:4]}{points}"
            for code, (src, points) in zip(grid, combo, strict=True)
        )
        print(f"{mark}  худшая клетка {worst:>4.0%}   {line}")
        best.append((worst, {code: dict(ENERGY_SOURCES[code]) for code in grid}))

    best.sort(key=lambda row: -row[0])
    print("\nЛучший вариант, худшая клетка "
          f"{best[0][0]:.0%}:\n  {best[0][1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", action="store_true", help="перебрать рецепты")
    parser.add_argument("--runs", type=int, default=300, help="боёв на клетку")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    if args.sweep:
        sweep(args.runs, args.seed)
    else:
        report(args.runs, args.seed)


if __name__ == "__main__":
    main()
