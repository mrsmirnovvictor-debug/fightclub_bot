"""Оффлайн-прогон рейда: насколько он вообще проходим.

    python scripts/raid_balance.py                   # стычка с фанатами
    python scripts/raid_balance.py --raid cellar     # подвал казино
    python scripts/raid_balance.py --level 7         # отряд не десятого уровня
    python scripts/raid_balance.py --fan             # отряд в фанатском
    python scripts/raid_balance.py --runs 400        # больше боёв на клетку

Уровень отряда здесь не для полноты: в подвале он решает всё. Босс встаёт
на четыре уровня выше отряда, а одет он всегда по десятому — значит, чем
ниже отряд, тем больше у босса фора в снаряжении, и одной клеткой эту
разницу не увидеть.

Бойцы жмут кнопки наугад: ни аналитика, ни приёмов, ни склянок. Это
нижняя граница — то, что получается у отряда, который просто тыкает. У
живых людей сверху есть и подписка, и заготовки, и лечение, поэтому
настоящий винрейт выше показанного, и насколько — отсюда не видно.

Правила боя берутся не отсюда, а из `RaidService`: прогон дёргает тот же
`_exchange` и тот же круг, которыми рейд идёт на самом деле. Иначе мерили
бы копию, а чинили оригинал.
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
from bot.game.raid import (  # noqa: E402
    CELLAR_RAID,
    HOOLIGAN_RAID,
    RaidEnd,
    RaidKind,
    raid_foes,
)
from bot.game.reference import (  # noqa: E402
    developed_stats,
    equipment_of,
    fan_kit,
    jewel_kit,
)
from bot.raid_service import RaidService, RaidSession  # noqa: E402


def party_of(size: int, level: int, fan: bool) -> dict[int, Fighter]:
    """Отряд: по кругу классов, чтобы ни один не красил результат один.

    Эталон здесь с украшениями (`jewel_kit`): цепь и три кольца стоят у
    ювелира дешевле любой одной вещи из комплекта, и к рейду их наденет
    кто угодно. Мерить рейд отрядом без украшений значило бы мерить
    отряд, которого в игре не будет, — тем более что банда свои носит.
    """
    shelf = fan_kit if fan else jewel_kit
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


def one_raid(
    service: RaidService, kind: RaidKind, size: int, level: int, fan: bool
) -> tuple[str, int]:
    """Один бой до конца. Отдаёт исход и число волн."""
    fighters = party_of(size, level, fan)
    session = RaidSession(
        id=1,
        chat_id=None,
        thread_id=None,
        kind=kind,
        enemies=raid_foes(kind, [one.level for one in fighters.values()]),
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


def sweep(kind: RaidKind, runs: int, level: int, fan: bool, seed: int) -> None:
    service = RaidService(
        bot=None, db=None, config=Config(bot_token="x"), rng=random.Random(seed)
    )
    dressed = "фанатское" if fan else "эталон с украшениями"
    print(
        f"{kind.title.lower()}: отряд {level}-го уровня в {dressed}, "
        f"{runs} боёв на клетку, кнопки наугад\n"
    )
    print(f"{'отряд':>6} {'враги':>6} {'победа':>8} {'ничья':>7} "
          f"{'провал':>7} {'волн':>6}")
    for size in range(kind.min_party, kind.max_party + 1):
        tally = {end.value: 0 for end in RaidEnd}
        waves: list[int] = []
        for _ in range(runs):
            end, count = one_raid(service, kind, size, level, fan)
            tally[end] += 1
            waves.append(count)
        gang = len(kind.roster(size))
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
    parser.add_argument(
        "--raid", default="hooligans", choices=("hooligans", "cellar"),
        help="какой рейд мерить",
    )
    args = parser.parse_args()
    kind = CELLAR_RAID if args.raid == "cellar" else HOOLIGAN_RAID
    sweep(kind, args.runs, args.level, args.fan, args.seed)


if __name__ == "__main__":
    main()
