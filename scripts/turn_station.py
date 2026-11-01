"""Повернуть карту станции на четверть оборота.

Координаты комнат в манифесте — это не только миникарта. По ним
считается, на какой стене комнаты проём (`doorways`), и, значит, что
должно быть на кадре панорамы. Если коридор, который рисуют уходящим
на север, по координатам идёт на восток, спорить с художником бессмысленно
— проще повернуть карту.

    python3 scripts/turn_station.py --show          # что получится
    python3 scripts/turn_station.py --apply         # восток станет севером
    python3 scripts/turn_station.py --apply --back  # и обратно

Поворачиваются **все этажи разом**, и это не лень. Лестница между
этажами идёт в комнату с теми же координатами — на этом держится
раскладка лестничных дверей. Повернуть один этаж значило бы развалить
эту пару.

Поворот ничего не меняет в том, как комнаты связаны: соседи остаются
соседями, и дверей, которым не хватило своей стены, после поворота
ровно столько же. Он меняет только то, как стороны света называются.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "zombiemission" / "mission_manifest.json"
)


def turned(x: int, y: int, back: bool) -> tuple[int, int]:
    """Четверть оборота. Y растёт вниз, как на экране.

    Вперёд: то, что лежало на востоке, оказывается на севере.
    Назад: то, что лежало на севере, возвращается на восток.
    """
    return (-y, x) if back else (y, -x)


def step_of(values: list[int]) -> int:
    """Шаг сетки по оси: самое маленькое расстояние между соседями."""
    spread = sorted(set(values))
    gaps = [b - a for a, b in zip(spread, spread[1:])]
    return min(gaps) if gaps else 0


def even_grid(nodes: list[dict]) -> list[dict]:
    """Выровнять шаг сетки по обеим осям.

    Сетка станции была нарисована под горизонтальные этажи: 300 между
    столбцами и 120 между рядами. После поворота эти числа меняются
    местами, и этаж превращается в ленту 240 на 900 — на телефоне
    такая схема либо занимает пол-экрана, либо ужимается до нечитаемых
    плиток. Расстояния между комнатами тут условны, а вот форма этажа
    на экране — нет, поэтому узкую ось растягиваем до шага широкой.
    """
    out = [dict(one) for one in nodes]
    by_x = step_of([one["x"] for one in out])
    by_y = step_of([one["y"] for one in out])
    if not by_x or not by_y or by_x == by_y:
        return out
    wide = max(by_x, by_y)
    for one in out:
        if by_x < by_y:
            base = min(two["x"] for two in nodes)
            one["x"] = base + round((one["x"] - base) * wide / by_x)
        else:
            base = min(two["y"] for two in nodes)
            one["y"] = base + round((one["y"] - base) * wide / by_y)
    return out


def rotate(nodes: list[dict], back: bool) -> list[dict]:
    """Повернуть, выровнять сетку и подвинуть в прежние границы."""
    moved = even_grid([
        dict(one, **dict(zip(("x", "y"), turned(one["x"], one["y"], back))))
        for one in nodes
    ])
    shift_x = min(one["x"] for one in nodes) - min(one["x"] for one in moved)
    shift_y = min(one["y"] for one in nodes) - min(one["y"] for one in moved)
    for one in moved:
        one["x"] += shift_x
        one["y"] += shift_y
    return moved


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="записать манифест")
    ap.add_argument("--back", action="store_true", help="повернуть обратно")
    ap.add_argument("--show", action="store_true", help="показать комнаты")
    args = ap.parse_args()

    body = json.loads(MANIFEST.read_text(encoding="utf-8"))
    moved = rotate(body["nodes"], args.back)
    if args.show:
        for was, now in zip(body["nodes"], moved):
            print(
                f"{was['id']:5} {was['name'][:24]:24} "
                f"({was['x']:5},{was['y']:5}) -> ({now['x']:5},{now['y']:5})"
            )
    if not args.apply:
        print("ничего не записано: нужен --apply")
        return
    body["nodes"] = moved
    MANIFEST.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"повёрнуто комнат: {len(moved)} — {MANIFEST}")
    print("пересобери карту художника: python3 scripts/station_map.py")


if __name__ == "__main__":
    main()
