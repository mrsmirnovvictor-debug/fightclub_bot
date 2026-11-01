"""Карта станции «Контур» для тех, кто рисует панорамы.

Отвечает на единственный вопрос художника: **что боец видит, стоя в
этой комнате и глядя в эту сторону** — проём или глухую стену. Ответ
берётся не из описания карты, а из той самой функции `doorways`, по
которой экран рисует проёмы и по которой ходит джойстик. Расходиться
им негде по построению.

    python3 scripts/station_map.py            # таблицы в docs/
    python3 scripts/station_map.py --html ПУТЬ  # ещё и схемы этажей

Заодно пересчитывает, сколько дверей встало не на ту стену, в которую
они ведут по координатам. Это не ошибка кода, а теснота: из комнаты
бывает два хода в одну сторону, а стен всего четыре.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.game.blackout import (  # noqa: E402
    FACINGS,
    FACING_TITLES,
    LAYER_TITLES,
    doorways,
    station,
)

CORNERS = {
    "east": 0.0,
    "south": math.pi / 2,
    "west": math.pi,
    "north": -math.pi / 2,
}


def true_side(here, there) -> str:
    """В какую сторону сосед лежит на самом деле. Лестница — вверх/вниз."""
    dx, dy = there.x - here.x, there.y - here.y
    if not dx and not dy:
        return "лестница"
    angle = math.atan2(dy, dx)
    return min(
        CORNERS,
        key=lambda side: abs(
            (angle - CORNERS[side] + math.pi) % (2 * math.pi) - math.pi
        ),
    )


def crowded() -> list[tuple[str, str, str, str, str]]:
    """Двери, вставшие не на ту стену, в которую ведут по координатам.

    Лестницы между этажами сюда не идут, и это не поблажка: у хода на
    другой этаж направления нет вовсе. Координаты соседнего этажа
    лежат в своей системе, сравнивать их с этой бессмысленно — такой
    ход занимает любую свободную стену и ведёт не вбок, а вверх.
    """
    place = station()
    out = []
    for code, room in place.rooms.items():
        for side, there in doorways(code).items():
            neighbour = place.room(there)
            if neighbour.layer != room.layer:
                continue
            real = true_side(room, neighbour)
            if real not in ("лестница", side):
                out.append((code, room.name, side, there, real))
    return out


def tables() -> str:
    """Таблицы «комната → что на каждой из четырёх сторон»."""
    place = station()
    lines = [
        "# Карта станции «Контур»",
        "",
        "Файл собирается `scripts/station_map.py` из самой карты —",
        "править его руками незачем, он перезапишется.",
        "",
        "**Как читать.** Строка — комната, столбец — сторона света.",
        "В клетке код комнаты за проёмом или «стена». Это ровно то,",
        "что боец видит, стоя в комнате и глядя в ту сторону: по этим",
        "же данным экран рисует проёмы, а джойстик делает шаг.",
        "",
        "Кадр панорамы называется стороной света:",
        "`panoramas_fp/<код>/north.jpg`. Значит, на кадре `north`",
        "комнаты должен быть проём там, где в столбце «Север» стоит",
        "код, и глухая стена там, где стоит «стена».",
        "",
    ]
    for layer in place.layers():
        rooms = [one for one in place.rooms.values() if one.layer == layer]
        lines.append(f"## {layer} · {LAYER_TITLES.get(layer, layer)}")
        lines.append("")
        head = " | ".join(FACING_TITLES[one].capitalize() for one in FACINGS)
        lines.append(f"| Комната | Название | {head} |")
        lines.append("|---|---|" + "---|" * len(FACINGS))
        for room in sorted(rooms, key=lambda one: one.code):
            sides = doorways(room.code)
            cells = []
            for facing in FACINGS:
                there = sides.get(facing)
                if not there:
                    cells.append("стена")
                    continue
                neighbour = place.room(there)
                cells.append(f"{there} {neighbour.name}")
            lines.append(
                f"| **{room.code}** | {room.name} | " + " | ".join(cells) + " |"
            )
        lines.append("")

    tight = crowded()
    lines += [
        "## Двери, стоящие не на своей стене",
        "",
        f"Таких {len(tight)} из {len(place.doors)}. Это не поломка, а",
        "теснота: координаты станции — схема, а не план этажа, и из",
        "одной комнаты бывает два хода в одну сторону. Стен четыре,",
        "поэтому второй ход занимает ближайшую свободную.",
        "",
        "Лестницы между этажами в этот список не идут: у хода наверх",
        "направления нет вовсе, и стену он занимает любую свободную.",
        "",
        "**Для панорам важно только первое:** рисовать надо по",
        "таблицам выше, а не по тому, где сосед лежит на схеме.",
        "",
        "| Комната | Проём на стене | Ведёт в | А лежит |",
        "|---|---|---|---|",
    ]
    for code, name, side, there, real in tight:
        lines.append(
            f"| {code} {name} | {FACING_TITLES[side]} | {there} | "
            f"{FACING_TITLES.get(real, real)} |"
        )
    lines.append("")
    return "\n".join(lines)


def plan(layer: str) -> str:
    """Схема этажа: комнаты на своих местах, двери линиями."""
    place = station()
    rooms = [one for one in place.rooms.values() if one.layer == layer]
    if not rooms:
        return ""
    pad = 120
    left = min(one.x for one in rooms) - pad
    top = min(one.y for one in rooms) - pad
    wide = max(one.x for one in rooms) - left + pad
    high = max(one.y for one in rooms) - top + pad
    parts = [
        f'<svg viewBox="{left} {top} {wide} {high}" class="plan">'
    ]
    seen: set[tuple[str, str]] = set()
    for room in rooms:
        for door in place.doors_from(room.code):
            there = door.other(room.code)
            neighbour = place.room(there)
            if neighbour is None or neighbour.layer != layer:
                continue
            key = tuple(sorted((room.code, there)))
            if key in seen:
                continue
            seen.add(key)
            parts.append(
                f'<line x1="{room.x}" y1="{room.y}" x2="{neighbour.x}" '
                f'y2="{neighbour.y}" class="hall"/>'
            )
    for room in rooms:
        stairs = [
            door.other(room.code)
            for door in place.doors_from(room.code)
            if (place.room(door.other(room.code)) or room).layer != layer
        ]
        parts.append(
            f'<rect x="{room.x - 60}" y="{room.y - 34}" width="120" '
            f'height="68" rx="10" class="cell"/>'
            f'<text x="{room.x}" y="{room.y - 8}" class="code">{room.code}</text>'
            f'<text x="{room.x}" y="{room.y + 12}" class="name">'
            f'{room.name[:17]}</text>'
        )
        if stairs:
            parts.append(
                f'<text x="{room.x}" y="{room.y + 28}" class="stairs">'
                f'↕ {", ".join(stairs)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def html() -> str:
    """Схемы всех этажей одной страницей."""
    place = station()
    body = []
    for layer in place.layers():
        drawing = plan(layer)
        if not drawing:
            continue
        body.append(
            f"<h2>{layer} · {LAYER_TITLES.get(layer, layer)}</h2>{drawing}"
        )
    return (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Карта станции</title><style>"
        "body{background:#11151a;color:#e8e6e3;font:15px/1.5 system-ui,"
        "sans-serif;margin:0;padding:16px}"
        "h1{font-size:20px}h2{font-size:16px;margin:24px 0 8px}"
        ".plan{width:100%;height:auto;background:#171c22;border-radius:12px}"
        ".hall{stroke:#55606c;stroke-width:6}"
        ".cell{fill:#222a33;stroke:#3d4752;stroke-width:2}"
        ".code{fill:#e8c04a;font:700 20px sans-serif;text-anchor:middle}"
        ".name{fill:#aeb6c0;font:15px sans-serif;text-anchor:middle}"
        ".stairs{fill:#6fa8dc;font:13px sans-serif;text-anchor:middle}"
        "</style></head><body><h1>Станция «Контур»: этажи и переходы</h1>"
        "<p>Прямоугольник — комната, линия — дверь, ↕ — лестница на "
        "другой этаж. Что видно с каждой стороны, сказано в "
        "<code>docs/blackout_map.md</code>.</p>"
        + "".join(body)
        + "</body></html>"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", help="куда положить схемы этажей")
    ap.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent
                    / "docs" / "blackout_map.md"),
        help="куда положить таблицы",
    )
    args = ap.parse_args()
    Path(args.out).write_text(tables(), encoding="utf-8")
    print(f"таблицы: {args.out}")
    if args.html:
        Path(args.html).write_text(html(), encoding="utf-8")
        print(f"схемы: {args.html}")
    print(f"дверей не на своей стене: {len(crowded())}")


if __name__ == "__main__":
    main()
