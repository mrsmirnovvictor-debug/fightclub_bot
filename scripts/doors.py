"""Пиксельный манифест дверей: что сейчас размечено на картах города.

    python scripts/doors.py            # вторая очередь: десять карт
    python scripts/doors.py --all      # весь город, все шестнадцать
    python scripts/doors.py --check    # только проверка: что не так

Картинка у каждой карты своя, а зоны хранятся долями — иначе на телефоне
с другим соотношением сторон все двери разъехались бы. Но размечают их
по пикселям исходника, и держать этот пересчёт в голове незачем.

Обратный пересчёт — в `--code`: присылают пиксели, отсюда выходит
готовый четырёхугольник для `bot/game/locations.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.game.locations import (  # noqa: E402
    BOTTOM_DOOR,
    DISTRICTS,
    TOP_DOOR,
    District,
    Location,
    Point,
)

# Исходный размер каждой карты. Один на весь город: вторая очередь
# нарисована в том же размере и ракурсе, что и первая
WIDTH, HEIGHT = 941, 1672

# Сколько места дверь занимает на карте в долях. Меньше нижней границы —
# в такую дверь не попасть даже с запасом под палец; больше верхней —
# размечена не дверь, а фасад целиком
MIN_SIDE = 0.03
MAX_SIDE = 0.45


def pixels(point: Point) -> tuple[int, int]:
    """Доли → пиксели исходника."""
    return round(point[0] * WIDTH), round(point[1] * HEIGHT)


def shares(px: float, py: float) -> Point:
    """Пиксели исходника → доли. Обратная дорога, для правок."""
    return round(px / WIDTH, 6), round(py / HEIGHT, 6)


def templated(place: Location) -> str:
    """Дверь взята с чужой картинки — значит, выверена не по своей."""
    if place.entrance is TOP_DOOR:
        return "шаблон, верхний дом"
    if place.entrance is BOTTOM_DOOR:
        return "шаблон, нижний дом"
    return ""


def complaints(place: Location) -> list[str]:
    """Что с этой дверью не так. Пустой список — всё в порядке."""
    said = []
    for x, y in place.entrance:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            said.append(f"угол ({x:.4f}, {y:.4f}) вне картинки")
    box = place.bounds
    if box.w < MIN_SIDE or box.h < MIN_SIDE:
        said.append(f"дверь мельче {MIN_SIDE}: {box.w:.4f}×{box.h:.4f}")
    if box.w > MAX_SIDE or box.h > MAX_SIDE:
        said.append(f"дверь крупнее {MAX_SIDE}: {box.w:.4f}×{box.h:.4f}")
    return said


def tell(place: Location) -> None:
    corners = " ".join(f"({x},{y})" for x, y in map(pixels, place.entrance))
    box = place.bounds
    note = templated(place)
    print(f"  {place.code} — {place.title}{' · ' + note if note else ''}")
    print(f"    углы:  {corners}")
    print(
        f"    рамка: {round(box.x * WIDTH)},{round(box.y * HEIGHT)} "
        f"{round(box.w * WIDTH)}×{round(box.h * HEIGHT)} px"
    )
    for said in complaints(place):
        print(f"    ⚠ {said}")


def show(districts: tuple[District, ...]) -> None:
    for one in districts:
        print(f"\n{one.title} — {one.code}.{one.picture_ext}")
        print(f"  {one.image}")
        for place in one.places:
            tell(place)


def check(districts: tuple[District, ...]) -> int:
    """Только жалобы. Возвращает их число — годится для ворот."""
    found = 0
    for one in districts:
        for place in one.places:
            for said in complaints(place):
                print(f"{one.code}/{place.code}: {said}")
                found += 1
    templates = [
        place.code
        for one in districts
        for place in one.places
        if templated(place)
    ]
    if templates:
        print(f"\nПо шаблону, не выверено по своей картинке: {len(templates)}")
        print("  " + ", ".join(templates))
    print(f"\nОшибок: {found}")
    return found


def as_code(numbers: list[int]) -> None:
    """Восемь чисел с картинки → четырёхугольник для locations.py."""
    if len(numbers) != 8:
        raise SystemExit("Нужно восемь чисел: x y по четырём углам")
    corners = [shares(numbers[i], numbers[i + 1]) for i in range(0, 8, 2)]
    print("        entrance=(")
    for pair in (corners[:2], corners[2:]):
        print("            " + " ".join(f"({x}, {y})," for x, y in pair))
    print("        ),")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="весь город")
    parser.add_argument("--check", action="store_true", help="только жалобы")
    parser.add_argument(
        "--code",
        nargs=8,
        type=int,
        metavar="N",
        help="восемь пикселей углов → строки для locations.py",
    )
    args = parser.parse_args()

    if args.code:
        as_code(args.code)
        return 0

    # Вторая очередь узнаётся по формату карты: её отдали в png
    districts = tuple(
        one for one in DISTRICTS if args.all or one.picture_ext == "png"
    )
    if args.check:
        return 1 if check(districts) else 0
    show(districts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
