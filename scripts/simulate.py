"""Оффлайн-прогон боёв: посмотреть бой глазами и проверить баланс.

    python scripts/simulate.py             # один показательный бой
    python scripts/simulate.py --balance   # винрейты всех пар классов
    python scripts/simulate.py --gear 8    # то же, но в полной экипировке
    python scripts/simulate.py --triangle  # держится ли круг классов
    python scripts/simulate.py --progress  # сколько боёв уходит до потолка
"""

from __future__ import annotations

import argparse
import itertools
import random
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.game.classes import (  # noqa: E402
    ALL_STATS,
    FIGHTER_CLASSES,
    START_POINTS,
    FighterClass,
    Stat,
)
from bot.game.economy import MAX_LEVEL, MICRO_UPS_PER_LEVEL, win_exp  # noqa: E402
from bot.game.reference import (  # noqa: E402
    developed_stats,
    kit_price,
    reference_equipment,
)
from bot.models import Player  # noqa: E402
from bot.game.combat import Fighter, random_action, resolve_round  # noqa: E402
from bot.game.equipment import Equipment  # noqa: E402
from bot.game.narrator import duel_intro, finish_report, round_report  # noqa: E402

TAGS = re.compile(r"</?[a-z][^>]*>")


def plain(text: str) -> str:
    return TAGS.sub("", text)


def show_fight(first: FighterClass, second: FighterClass, seed: int) -> None:
    rng = random.Random(seed)
    a = Fighter(1, f"{first.title}-1", first, first.base_stats)
    b = Fighter(2, f"{second.title}-2", second, second.base_stats)
    fighters = {1: a, 2: b}
    print(plain(duel_intro(a, b)), "\n")
    round_number = 1
    while True:
        result = resolve_round(
            a, random_action(a, rng), b, random_action(b, rng), round_number, rng
        )
        print(plain(round_report(result, fighters, rng)), "\n")
        if result.finished:
            print(plain(finish_report(result, fighters, 50, rng)))
            return
        round_number += 1


def balance(runs: int, seed: int) -> None:
    rng = random.Random(seed)
    totals: dict[str, list[float]] = {code: [] for code in FIGHTER_CLASSES}
    lengths: list[float] = []
    for first, second in itertools.combinations(FIGHTER_CLASSES.values(), 2):
        wins = {1: 0, 2: 0, None: 0}
        rounds: list[int] = []
        for _ in range(runs):
            a = Fighter(1, "A", first, first.base_stats)
            b = Fighter(2, "B", second, second.base_stats)
            round_number = 1
            while True:
                result = resolve_round(
                    a, random_action(a, rng), b, random_action(b, rng), round_number, rng
                )
                if result.finished:
                    wins[result.winner_id] += 1
                    rounds.append(round_number)
                    break
                round_number += 1
        share_first = (wins[1] + wins[None] / 2) / runs
        totals[first.code].append(share_first)
        totals[second.code].append(1 - share_first)
        lengths.append(statistics.mean(rounds))
        print(
            f"{first.title:8} vs {second.title:8} "
            f"{share_first:5.0%} / {1 - share_first:.0%}   "
            f"~{statistics.mean(rounds):.1f} раундов"
        )
    print("\nсредний винрейт:")
    for code, shares in totals.items():
        print(f"  {FIGHTER_CLASSES[code].title:10} {statistics.mean(shares):.0%}")
    print(f"средняя длина боя: {statistics.mean(lengths):.1f} раундов")


def progress(runs: int, seed: int) -> None:
    """Сколько боёв уходит до потолка уровня при винрейте около 50%."""
    rng = random.Random(seed)

    fights_taken, wins_taken = [], []
    for _ in range(runs):
        code = rng.choice(list(FIGHTER_CLASSES))
        fclass = FIGHTER_CLASSES[code]
        hero = Player(
            user_id=1,
            nickname="Боец",
            class_code=code,
            **fclass.base_stats.as_dict(),
        )
        hero.free_points = 6
        fights = wins = 0
        while not hero.at_max_level and fights < 1000:
            _spend(hero, rng)
            rival = FIGHTER_CLASSES[rng.choice(list(FIGHTER_CLASSES))]
            # соперник того же уровня и с такой же прокачкой — иначе замер врёт
            rival_stats = _developed(rival, hero.level, rng)
            a = Fighter(1, "Боец", hero.fclass, hero.stats, hero.level)
            b = Fighter(2, "Соперник", rival, rival_stats, hero.level)
            round_number = 1
            while True:
                result = resolve_round(
                    a, random_action(a, rng), b, random_action(b, rng), round_number, rng
                )
                if result.finished:
                    break
                round_number += 1
            fights += 1
            if result.winner_id == 1:
                wins += 1
                hero.grant_exp(win_exp(a.damage_dealt, hero.level, hero.level))
        fights_taken.append(fights)
        wins_taken.append(wins)

    print(f"до {MAX_LEVEL} уровня:")
    print(f"  боёв   — в среднем {statistics.mean(fights_taken):.0f} "
          f"(от {min(fights_taken)} до {max(fights_taken)})")
    print(f"  побед  — в среднем {statistics.mean(wins_taken):.0f}")
    print(f"  винрейт при случайной игре: "
          f"{statistics.mean(wins_taken) / statistics.mean(fights_taken):.0%}")


# ---------- экипировка ----------

# Эталонные сборки и комплекты живут в bot/game/reference.py: на них
# сходится баланс, их же проверяют тесты.


def geared(level: int, runs: int, seed: int) -> None:
    """Винрейты в полной экипировке и цена самой экипировки."""
    rng = random.Random(seed)
    totals: dict[str, list[float]] = {code: [] for code in FIGHTER_CLASSES}
    lengths: list[float] = []

    def make(code: str, user_id: int, dressed: bool) -> Fighter:
        fclass = FIGHTER_CLASSES[code]
        equipment = reference_equipment(fclass, level) if dressed else Equipment()
        stats = developed_stats(fclass, level)
        return Fighter(
            user_id,
            fclass.title,
            fclass,
            stats.merge(equipment.bonus),
            level,
            equipment=equipment,
        )

    def duel(a: Fighter, b: Fighter) -> tuple[int | None, int]:
        number = 1
        while True:
            result = resolve_round(
                a, random_action(a, rng), b, random_action(b, rng), number, rng
            )
            if result.finished:
                return result.winner_id, number
            number += 1

    print(f"{level} уровень, полная экипировка:\n")
    for first, second in itertools.combinations(FIGHTER_CLASSES.values(), 2):
        wins = {1: 0, 2: 0, None: 0}
        rounds: list[int] = []
        for _ in range(runs):
            winner, number = duel(
                make(first.code, 1, True), make(second.code, 2, True)
            )
            wins[winner] += 1
            rounds.append(number)
        share = (wins[1] + wins[None] / 2) / runs
        totals[first.code].append(share)
        totals[second.code].append(1 - share)
        lengths.append(statistics.mean(rounds))
        print(
            f"{first.title:8} vs {second.title:8} "
            f"{share:5.0%} / {1 - share:.0%}   ~{statistics.mean(rounds):.1f} раундов"
        )

    print("\nсредний винрейт:")
    for code, shares in totals.items():
        print(f"  {FIGHTER_CLASSES[code].title:10} {statistics.mean(shares):.0%}")
    print(f"средняя длина боя: {statistics.mean(lengths):.1f} раундов")

    print("\nэкипированный против голого (тот же класс и уровень):")
    for code, fclass in FIGHTER_CLASSES.items():
        wins = {1: 0, 2: 0, None: 0}
        for _ in range(runs):
            winner, _ = duel(make(code, 1, True), make(code, 2, False))
            wins[winner] += 1
        share = (wins[1] + wins[None] / 2) / runs
        price = kit_price(fclass, level)
        print(f"  {fclass.title:10} {share:5.0%}   комплект стоит {price} 💰")


# Кто кого должен бить в круге камень-ножницы-бумага
CIRCLE = (
    ("rogue", "tank", "ловкость пробивает сопротивление"),
    ("tank", "assassin", "выносливость гасит крит антикритом"),
    ("assassin", "rogue", "интуиция ловит уворот точностью"),
)


def triangle(runs: int, seed: int) -> None:
    """Держится ли круг на каждом уровне: трикстер → танк → ассасин → трикстер."""
    rng = random.Random(seed)
    levels = (1, 4, 6, 8, 10)

    def fighter(code: str, level: int, user_id: int) -> Fighter:
        fclass = FIGHTER_CLASSES[code]
        equipment = reference_equipment(fclass, level) if level > 1 else Equipment()
        stats = developed_stats(fclass, level).merge(equipment.bonus)
        return Fighter(user_id, fclass.title, fclass, stats, level, equipment=equipment)

    print("круг: кто кого бьёт (в комплектах своего уровня)\n")
    print(f"{'пара':26}" + "".join(f"{'ур.' + str(level):>8}" for level in levels))
    holds = True
    for winner, loser, why in CIRCLE:
        shares = []
        for level in levels:
            wins = {1: 0, 2: 0, None: 0}
            for _ in range(runs):
                a, b = fighter(winner, level, 1), fighter(loser, level, 2)
                number = 1
                while True:
                    result = resolve_round(
                        a, random_action(a, rng), b, random_action(b, rng), number, rng
                    )
                    if result.finished:
                        break
                    number += 1
                wins[result.winner_id] += 1
            share = (wins[1] + wins[None] / 2) / runs
            shares.append(share)
            holds = holds and share > 0.5
        pair = f"{FIGHTER_CLASSES[winner].title} → {FIGHTER_CLASSES[loser].title}"
        print(f"{pair:26}" + "".join(f"{s:>8.0%}" for s in shares) + f"   {why}")
    print("\nкруг держится" if holds else "\nкруг где-то разорван")


def _developed(fclass: FighterClass, level: int, rng: random.Random):
    """Статы бойца этого уровня при случайной раскидке очков."""
    stats = fclass.base_stats.plus(Stat.ENDURANCE, level - 1)
    for _ in range(START_POINTS + MICRO_UPS_PER_LEVEL * (level - 1)):
        stats = stats.plus(rng.choice(ALL_STATS))
    return stats


def _spend(hero, rng: random.Random) -> None:
    """Раскидать свободные очки — как это делает игрок после апа."""
    stats = hero.stats
    while hero.free_points:
        stats = stats.plus(rng.choice(ALL_STATS))
        hero.free_points -= 1
    hero.apply_stats(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="Симулятор боёв бойцовского клуба")
    parser.add_argument("--balance", action="store_true", help="прогнать все пары классов")
    parser.add_argument(
        "--progress", action="store_true", help="сколько боёв уходит до потолка уровня"
    )
    parser.add_argument(
        "--gear", type=int, metavar="УРОВЕНЬ", help="баланс в полной экипировке"
    )
    parser.add_argument(
        "--triangle", action="store_true", help="держится ли круг классов"
    )
    parser.add_argument("--runs", type=int, default=500, help="боёв на пару (для --balance)")
    parser.add_argument("--seed", type=int, default=None, help="зерно генератора")
    parser.add_argument("--first", default="warrior", choices=sorted(FIGHTER_CLASSES))
    parser.add_argument("--second", default="assassin", choices=sorted(FIGHTER_CLASSES))
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(10**6)
    if args.triangle:
        triangle(max(50, args.runs // 2), seed)
    elif args.gear:
        geared(args.gear, args.runs, seed)
    elif args.progress:
        progress(max(1, args.runs // 25), seed)
    elif args.balance:
        balance(args.runs, seed)
    else:
        print(f"(зерно {seed})\n")
        show_fight(FIGHTER_CLASSES[args.first], FIGHTER_CLASSES[args.second], seed)


if __name__ == "__main__":
    main()
