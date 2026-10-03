"""Оффлайн-прогон стычки с фанатами: насколько она вообще проходима.

    python scripts/gang_raid.py              # отряды 3–10 в эталонном комплекте
    python scripts/gang_raid.py --fan        # отряд в фанатском, как и банда
    python scripts/gang_raid.py --runs 400   # больше боёв на клетку

Бойцы здесь жмут кнопки наугад: ни аналитика, ни приёмов, ни склянок. Это
нижняя граница — то, что получается у отряда, который просто тыкает. У
живых людей сверху есть и подписка, и заготовки, и лечение, поэтому
настоящий винрейт выше показанного, и насколько — отсюда не видно.

Правила боя берутся не отсюда, а из `RaidService`: прогон дёргает тот же
`_exchange` и тот же круг по банде, которыми рейд идёт на самом деле.
Иначе мерили бы копию, а чинили оригинал.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import Config  # noqa: E402
from bot.game.classes import FIGHTER_CLASSES  # noqa: E402
from bot.game.combat import Fighter, random_action  # noqa: E402
from bot.game.economy import MAX_LEVEL  # noqa: E402
from bot.game.raid import HOOLIGAN_RAID, RaidEnd, raid_foes  # noqa: E402
from bot.game.reference import (  # noqa: E402
    best_kit,
    developed_stats,
    equipment_of,
    fan_kit,
)
from bot.raid_service import RaidService, RaidSession  # noqa: E402


def party_of(size: int, level: int, fan: bool) -> dict[int, Fighter]:
    """Отряд: по кругу классов, чтобы ни один не красил результат один."""
    shelf = fan_kit if fan else best_kit
    codes = list(FIGHTER_CLASSES)
    squad: dict[int, Fighter] = {}
    for index in range(size):
        fclass = FIGHTER_CLASSES[codes[index % len(codes)]]
        equipment = equipment_of(shelf(fclass, level))
        squad[index + 1] = Fighter(
            user_id=index + 1,
            name=f"{fclass.title}-{index + 1}",
            fclass=fclass,
            stats=developed_stats(fclass, level).merge(equipment.bonus),
            level=level,
            equipment=equipment,
        )
    return squad


def one_raid(service: RaidService, size: int, level: int, fan: bool) -> tuple[str, int]:
    """Один бой до конца. Отдаёт исход и число волн."""
    fighters = party_of(size, level, fan)
    session = RaidSession(
        id=1,
        chat_id=None,
        thread_id=None,
        kind=HOOLIGAN_RAID,
        enemies=raid_foes(
            HOOLIGAN_RAID, [one.level for one in fighters.values()]
        ),
        fighters=fighters,
    )
    while True:
        session.wave += 1
        session.stances = {
            number: service.rng.randrange(5) for number in session.enemies
        }
        session.take_aim()
        session.acted = set()
        for user_id in list(session.alive_ids):
            fighter = session.fighters[user_id]
            service._exchange(session, user_id, random_action(fighter, service.rng))
            outcome = service._judge(session)
            if outcome is not None:
                return outcome.end.value, session.wave
        outcome = service._judge(session)
        if outcome is not None:
            return outcome.end.value, session.wave


def sweep(runs: int, level: int, fan: bool, seed: int) -> None:
    service = RaidService(
        bot=None, db=None, config=Config(bot_token="x"), rng=random.Random(seed)
    )
    dressed = "фанатское" if fan else "эталон"
    print(
        f"стычка с фанатами: отряд {level}-го уровня в {dressed}, "
        f"{runs} боёв на клетку, кнопки наугад\n"
    )
    print(f"{'отряд':>6} {'банда':>6} {'победа':>8} {'ничья':>7} "
          f"{'провал':>7} {'волн':>6}")
    for size in range(HOOLIGAN_RAID.min_party, HOOLIGAN_RAID.max_party + 1):
        tally = {end.value: 0 for end in RaidEnd}
        waves: list[int] = []
        for _ in range(runs):
            end, count = one_raid(service, size, level, fan)
            tally[end] += 1
            waves.append(count)
        gang = len(HOOLIGAN_RAID.roster(size))
        share = {key: value * 100 / runs for key, value in tally.items()}
        print(
            f"{size:>6} {gang:>6} {share['win']:>7.1f}% {share['draw']:>6.1f}% "
            f"{share['loss']:>6.1f}% {sum(waves) / len(waves):>6.1f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=200, help="боёв на клетку")
    parser.add_argument("--level", type=int, default=MAX_LEVEL)
    parser.add_argument("--fan", action="store_true", help="отряд в фанатском")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    sweep(args.runs, args.level, args.fan, args.seed)


if __name__ == "__main__":
    main()
