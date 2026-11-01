"""Разложить комнаты станции по сетке: ходы только вверх, вниз, вбок.

Игрок ходит джойстиком на четыре стороны, значит, и на схеме ход
обязан идти ровно по оси. Диагональная линия между комнатами — это
ход, которого игрок сделать не может: в первом лице такой двери некуда
встать.

    python3 scripts/grid_station.py --show    # что получится
    python3 scripts/grid_station.py --apply   # записать в манифест

Расстановка ищется перебором с возвратом, по этажу за раз. Сначала
пробуем уложить этаж так, чтобы каждая дверь вела **в соседнюю клетку**
— тогда у комнаты не бывает двух дверей в одну сторону, и первое лицо
не врёт нигде. Если этаж так не ложится, разрешаем ходу идти вдоль оси
через пустые клетки: он всё ещё не диагональ, но две двери могут
оказаться на одной стене.

Этаж с «треугольником» — три комнаты, соединённые попарно, — не
ложится в сетку никогда: две стороны такого треугольника выйдут вдоль
одной оси, а третья обязана быть диагональю. Скрипт про такие этажи
говорит прямо, чтобы это решал человек, а не случайность перебора.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "zombiemission" / "mission_manifest.json"
)
STEP = 300
REACH = 6  # насколько далеко по оси ищем место


def floors(body: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for node in body["nodes"]:
        out.setdefault(node["layer"], []).append(node)
    return out


def links(body: dict, codes: set[str]) -> dict[str, set[str]]:
    """Соседи внутри этажа. Лестницы на другие этажи сюда не идут."""
    near: dict[str, set[str]] = {code: set() for code in codes}
    for edge in body["edges"]:
        a, b = edge["a"], edge["b"]
        if a in codes and b in codes:
            near[a].add(b)
            near[b].add(a)
    return near


def order_of(codes: list[str], near: dict[str, set[str]]) -> list[str]:
    """Обход в ширину: каждая следующая комната цепляется за уже
    поставленную, и перебору есть от чего отталкиваться."""
    seen: list[str] = []
    left = set(codes)
    while left:
        start = min(left, key=lambda one: (-len(near[one]), one))
        queue = deque([start])
        left.discard(start)
        seen.append(start)
        while queue:
            cur = queue.popleft()
            for nxt in sorted(near[cur]):
                if nxt in left:
                    left.discard(nxt)
                    seen.append(nxt)
                    queue.append(nxt)
    return seen


def clear_way(a: tuple[int, int], b: tuple[int, int], taken: dict) -> bool:
    """Не проходит ли коридор сквозь чужую комнату."""
    (ax, ay), (bx, by) = a, b
    if ax == bx:
        lo, hi = sorted((ay, by))
        return all((ax, y) not in taken for y in range(lo + 1, hi))
    lo, hi = sorted((ax, bx))
    return all((x, ay) not in taken for x in range(lo + 1, hi))


def embed(
    codes: list[str], near: dict[str, set[str]], reach: int
) -> dict[str, tuple[int, int]] | None:
    """Разложить этаж: все ходы по оси, длиной не больше `reach` клеток."""
    spots = order_of(codes, near)
    taken: dict[tuple[int, int], str] = {}
    where: dict[str, tuple[int, int]] = {}

    def fits(code: str, cell: tuple[int, int]) -> bool:
        if cell in taken:
            return False
        for mate in near[code]:
            if mate not in where:
                continue
            mx, my = where[mate]
            x, y = cell
            if mx != x and my != y:
                return False  # диагональ
            if abs(mx - x) + abs(my - y) > reach:
                return False
            if not clear_way(cell, where[mate], taken):
                return False
        # Поставленные соседи не должны оказаться отрезанными: ход к
        # ним пойдёт по оси и не сквозь чужую комнату — проверено выше
        return True

    def options(code: str) -> list[tuple[int, int]]:
        placed = [where[one] for one in near[code] if one in where]
        if not placed:
            return [(0, 0)] if not taken else [
                (x, y)
                for x in range(-reach, reach + 1)
                for y in range(-reach, reach + 1)
                if (x, y) not in taken
            ]
        # Кандидаты — клетки по оси от первого поставленного соседа,
        # ближние вперёд: короткий ход лучше длинного
        first = placed[0]
        cells = []
        for step in range(1, reach + 1):
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                cells.append((first[0] + dx * step, first[1] + dy * step))
        return cells

    # Раскладок бывает много, и первая попавшаяся — обычно змейка во
    # всю длину: такой этаж на телефоне либо занимает экран, либо
    # ужимается до нечитаемых плиток. Поэтому собираем несколько и
    # берём самую складную — ту, что ближе к квадрату и плотнее.
    found: list[dict[str, tuple[int, int]]] = []
    budget = [40_000]

    def walk(at: int) -> None:
        if budget[0] <= 0 or len(found) >= 400:
            return
        if at == len(spots):
            found.append(dict(where))
            return
        code = spots[at]
        for cell in options(code):
            budget[0] -= 1
            if budget[0] <= 0 or len(found) >= 400:
                return
            if not fits(code, cell):
                continue
            where[code] = cell
            taken[cell] = code
            walk(at + 1)
            del taken[cell]
            del where[code]

    def shape(one: dict[str, tuple[int, int]]) -> tuple[float, int]:
        xs = [cell[0] for cell in one.values()]
        ys = [cell[1] for cell in one.values()]
        wide = max(xs) - min(xs) + 1
        high = max(ys) - min(ys) + 1
        return max(wide, high) / min(wide, high), wide * high

    walk(0)
    return min(found, key=shape) if found else None


def spun(cell: tuple[int, int], quarter: int) -> tuple[int, int]:
    """Повернуть клетку на четверть оборота `quarter` раз."""
    x, y = cell
    for _ in range(quarter % 4):
        x, y = -y, x
    return x, y


def aligned(
    found: dict[str, tuple[int, int]],
    было: dict[str, tuple[int, int]],
    near: dict[str, set[str]],
) -> dict[str, tuple[int, int]]:
    """Развернуть найденную раскладку ближе всего к прежней.

    Перебор ставит этаж как придётся: ему всё равно, куда смотрит
    главный коридор. А коридору не всё равно — под него уже нарисованы
    панорамы. Поэтому из четырёх разворотов берём тот, при котором
    больше дверей сохранили прежнюю сторону света.

    Отражения тут нет намеренно: зеркало меняет право и лево местами,
    и все кадры пришлось бы перерисовывать, а не переименовывать.
    """
    def side(a: tuple[int, int], b: tuple[int, int]) -> str:
        dx, dy = b[0] - a[0], b[1] - a[1]
        if abs(dx) >= abs(dy):
            return "east" if dx > 0 else "west"
        return "south" if dy > 0 else "north"

    pairs = [
        (one, two)
        for one in found for two in near[one]
        if one < two and one in было and two in было
    ]
    best, mark = found, -1
    for quarter in range(4):
        turn = {code: spun(cell, quarter) for code, cell in found.items()}
        same = sum(
            side(turn[one], turn[two]) == side(было[one], было[two])
            for one, two in pairs
        )
        if same > mark:
            best, mark = turn, same
    return best


def lay(body: dict, verbose: bool = False) -> tuple[list[dict], list[str]]:
    """Разложить все этажи. Второй ответ — что не легло идеально."""
    notes: list[str] = []
    moved: dict[str, tuple[int, int]] = {}
    for layer, nodes in floors(body).items():
        codes = [one["id"] for one in nodes]
        near = links(body, set(codes))
        tight = embed(codes, near, 1)
        if tight is None:
            tight = embed(codes, near, REACH)
            notes.append(
                f"{layer}: в соседние клетки не лёг — часть ходов идёт "
                f"вдоль оси через пустые клетки"
            )
        if tight is None:
            notes.append(f"{layer}: НЕ ЛЁГ ВОВСЕ — остался как был")
            continue
        tight = aligned(
            tight, {one["id"]: (one["x"], one["y"]) for one in nodes}, near
        )
        for code, (x, y) in tight.items():
            moved[code] = (x, y)
        if verbose:
            print(f"--- {layer}")
            for code in codes:
                print(f"    {code:5} -> {tight[code]}")
    out = []
    for node in body["nodes"]:
        cell = moved.get(node["id"])
        if cell is None:
            out.append(dict(node))
            continue
        out.append(dict(node, x=cell[0] * STEP, y=cell[1] * STEP))
    # Сдвигаем каждый этаж в положительные числа, как было
    by_layer: dict[str, list[dict]] = {}
    for node in out:
        by_layer.setdefault(node["layer"], []).append(node)
    for nodes in by_layer.values():
        shift_x = 160 - min(one["x"] for one in nodes)
        shift_y = 145 - min(one["y"] for one in nodes)
        for one in nodes:
            one["x"] += shift_x
            one["y"] += shift_y
    return out, notes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="записать манифест")
    ap.add_argument("--show", action="store_true", help="показать расстановку")
    args = ap.parse_args()

    body = json.loads(MANIFEST.read_text(encoding="utf-8"))
    nodes, notes = lay(body, verbose=args.show)
    for note in notes:
        print("!", note)
    if not args.apply:
        print("ничего не записано: нужен --apply")
        return
    body["nodes"] = nodes
    MANIFEST.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"разложено комнат: {len(nodes)} — {MANIFEST}")
    print("пересобери карту художника: python3 scripts/station_map.py")


if __name__ == "__main__":
    main()
