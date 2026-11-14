"""Боевая мощь NPC: во что рейду обходится один противник.

    python scripts/foe_power.py              # все противники рейдов
    python scripts/foe_power.py --level 12   # отряд другого уровня

Мерка одна и считается по правилам самого боя:

    мощь = живучесть × урон в ход

**Живучесть** — не запас здоровья, а сколько урона по нему придётся
выложить. Удар до тела доходит дважды урезанным: сперва выносливостью
(`resist`, режет всегда и ничем не ограничена), потом бронёй — а броня
по правилу `MAX_ARMOR_SHARE` не может съесть больше трёх пятых того,
что осталось. Трёх пятых она и съедает: у рейдовых NPC брони заведомо
больше, чем этот потолок, поэтому в формуле она стоит потолком, а не
числом с вещей. Лишняя броня сверх потолка в бою не работает — не
работает она и в мерке.

**Урон в ход** — то, чем он бьёт: урон от силы плюс урон оружия,
проведённый через множитель класса (одну и ту же биту воин
проворачивает лучше танка), и всё это на число рук с оружием.

**Огнестрел меряется дважды.** Пуля не разгоняется ни силой, ни
классом: на оружии написано 150–200, столько и прилетает. Но патроны
кончаются, а бой длится в разы дольше магазина, поэтому у караула базы
две мощи — залповая (пока магазин полон) и ровная (чем он бьёт потом).
Договор с лидером банды держится на ровной: именно ею солдат стоит в
бою девять ходов из десяти. Залповая показана рядом, и она намеренно
втрое выше — на этом и построена опасность первых восьми ходов.

Отношение двух мощей читается прямо: 1.12 значит «на двенадцать
процентов сильнее». Этим и держится договор охраны базы — солдат
обязан стоять на десять-пятнадцать процентов выше лидера банды
фанатов (`tests/test_military_raid.py`).

Чего мерка не знает: приёмов, склянок, повадки и числа противников.
Она про одного бойца против одного, а проходимость рейда целиком меряет
`scripts/raid_balance.py` — прогоном настоящего боя.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.game.classes import ALL_ZONES  # noqa: E402
from bot.game.combat import MAX_ARMOR_SHARE, Fighter  # noqa: E402
from bot.game.economy import GEAR_TOP_LEVEL  # noqa: E402
from bot.game.raid import (  # noqa: E402
    CELLAR_BOSS,
    GANG_CREW,
    GANG_LEADER,
    GANG_LEVEL,
    MILITARY_GUARDS,
    MILITARY_LEVEL,
    Boss,
    boss_fighter,
)


def damage_per_turn(fighter: Fighter) -> float:
    """Средний урон за ход: сила, оружие и множитель класса.

    У огнестрела своя арифметика: пулю не разгоняет ни сила бойца, ни
    класс, — поэтому в ход идёт ровно то, что написано на оружии.
    """
    low, high = fighter.equipment.weapon_damage
    if fighter.firearm() is not None:
        return (low + high) / 2 * fighter.attacks_per_round
    derived = fighter.derived
    own = (derived.damage_min + derived.damage_max) / 2
    weapon = (low + high) / 2 * fighter.fclass.damage_mult
    return (own + weapon) * fighter.attacks_per_round


def spent(fighter: Fighter) -> Fighter:
    """Тот же боец с пустым магазином — чем он бьёт после последнего патрона.

    Магазин опустошается настоящим путём, через `spend_shot`: он же
    перекладывает в руку оружие на потом, и мерка не повторяет это
    правило второй раз.
    """
    while fighter.firearm() is not None:
        fighter.spend_shot()
    return fighter


def toughness(fighter: Fighter) -> float:
    """Живучесть: сколько урона придётся выложить, чтобы его свалить."""
    absorbed = fighter.resist + MAX_ARMOR_SHARE * (1 - fighter.resist)
    return fighter.max_hp / (1 - absorbed)


def armor_mean(fighter: Fighter) -> float:
    """Средняя броня по пяти зонам — для таблицы, не для мерки."""
    return sum(sum(fighter.armor_range(zone)) / 2 for zone in ALL_ZONES) / len(
        ALL_ZONES
    )


def power(
    boss: Boss,
    level: int,
    party_level: int = GEAR_TOP_LEVEL,
    firing: bool = False,
) -> float:
    """Боевая мощь противника: живучесть на урон в ход.

    `level` — твёрдый уровень NPC; ноль у тех, кто встаёт по отряду, и
    тогда его считает `party_level`.

    `firing` — считать с полным магазином. По умолчанию нет: мерка
    сравнивает противников по тому, чем они бьют весь бой, а залп из
    восьми патронов короче любого боя рейда.
    """
    fighter = boss_fighter(boss, [party_level], hp_share=0.0, level=level)
    if not firing:
        fighter = spent(fighter)
    return toughness(fighter) * damage_per_turn(fighter)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", type=int, default=GEAR_TOP_LEVEL, help="отряд")
    args = parser.parse_args()

    bench: tuple[tuple[str, Boss, int], ...] = (
        ("Босс казино", CELLAR_BOSS, 0),
        ("Лидер банды", GANG_LEADER, GANG_LEVEL),
        ("Гопник", GANG_CREW[0], GANG_LEVEL),
        ("Охрана базы", MILITARY_GUARDS[0], MILITARY_LEVEL),
    )
    leader = power(GANG_LEADER, GANG_LEVEL, args.level)
    print(f"отряд {args.level}-го уровня\n")
    print(
        f"{'кто':>14} {'ур':>4} {'здоровье':>9} {'броня':>7} "
        f"{'живучесть':>10} {'урон':>7} {'мощь':>7} {'к лидеру':>9} "
        f"{'залпом':>8}"
    )
    for title, boss, level in bench:
        fighter = boss_fighter(boss, [args.level], hp_share=0.0, level=level)
        burst = toughness(fighter) * damage_per_turn(fighter)
        fighter = spent(fighter)
        value = toughness(fighter) * damage_per_turn(fighter)
        salvo = f"{burst / leader:>7.2f}x" if burst != value else " " * 8
        print(
            f"{title:>14} {fighter.level:>4} {fighter.max_hp:>9} "
            f"{armor_mean(fighter):>7.1f} {toughness(fighter):>10.0f} "
            f"{damage_per_turn(fighter):>7.1f} {value / 1000:>7.1f} "
            f"{value / leader:>8.2f}x {salvo}"
        )


if __name__ == "__main__":
    main()
