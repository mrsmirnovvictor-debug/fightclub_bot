#!/usr/bin/env python3
"""Проверка карты «Чёрного пуска»: сходится ли манифест.

Манифест — проектный документ, и прежде чем по нему ходить, его читают
недоверчиво. Скрипт показывает то же, что проверяют тесты, но словами и
с разбором: сколько комнат, где цели, в каком порядке они берутся и что
не сходится.

    python3 scripts/validate_blackout_map.py
    python3 scripts/validate_blackout_map.py zombiemission/mission_manifest.json

Возвращает 1, если в карте что-то не сходится: так его можно поставить
в проверку перед сборкой.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.game.blackout import (  # noqa: E402
    FLAG_TITLES,
    SQUAD_SIZE,
    check_map,
    load_station,
    station,
    walkthrough,
)


def main(argv: list[str]) -> int:
    place = load_station(Path(argv[1])) if len(argv) > 1 else station()

    print(f"Комнат: {len(place.rooms)}, дверей: {len(place.doors)}")
    for layer in place.layers():
        rooms = place.rooms_on(layer)
        fights = [one for one in rooms if one.encounter]
        goals = [one for one in rooms if one.objective]
        print(
            f"  {layer:3} {len(rooms):2} комнат, {len(fights)} встреч, "
            f"{len(goals)} целей"
        )
    print(f"Обычных групп: {len(SQUAD_SIZE)}")

    print("\nПорядок, в каком рейд собирается ногами:")
    for number, (flag, room) in enumerate(walkthrough(place), 1):
        print(f"  {number:2}. {FLAG_TITLES.get(flag, flag):28} ← {room}")

    bad = check_map(place)
    if not bad:
        print("\nКарта сходится: висящих комнат нет, цели достижимы, "
              "после пуска выход открыт.")
        return 0
    print(f"\nНе сходится ({len(bad)}):")
    for one in bad:
        print(f"  — {one}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
