"""Карта станции «Контур» для тех, кто рисует панорамы.

Отвечает на единственный вопрос художника: **что боец видит, стоя в
этой комнате и глядя в эту сторону** — проём или стену. Ответ берётся
не из описания карты, а из той самой функции `doorways`, по которой
экран рисует проёмы и по которой ходит джойстик. Расходиться им негде
по построению.

    python3 scripts/station_map.py            # таблицы в docs/
    python3 scripts/station_map.py --html ПУТЬ  # ещё и схемы этажей

Правила чтения — из ревью карты, и они здесь не украшение:

* Север — вверх на плане, стороны считаются по координатам комнат
  **внутри одного слоя**.
* «—» значит «хода по карте нет», а не «на рисунке глухая стена».
  Нарисованный проём сам по себе хода не открывает: его открывает
  флаг. Восточный кадр E12 показывает выход наружу, а комнаты за ним
  нет вовсе.
* Ход на другой этаж направления не имеет: координаты соседнего плана
  лежат в своей системе. Такой ход помечен стрелкой и вынесен в
  отдельную таблицу с условиями.

Заодно пересчитывает, сколько дверей встало не на ту стену, в которую
они ведут по координатам, и где подтверждённый кадр спорит с планом.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.game.blackout import (  # noqa: E402
    DRAWN_EYES,
    EYE_STAGES,
    eye_sides,
    FACINGS,
    FACING_TITLES,
    LAYER_DEPTH,
    LAYER_MAPS,
    LAYER_SHORT,
    LAYER_TITLES,
    art_url,
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


def plan_side(room: str, drawn: str) -> str:
    """Какой стене по плану отвечает кадр, названный художником так.

    Художник рисовал свою станцию в своей системе координат, и
    расстановка комнаты переводит одно в другое: `eye_sides` говорит,
    какой файл лежит за какой стеной, а тут нужен обратный ход.
    """
    for side, name in eye_sides(room).items():
        if name == drawn:
            return side
    return drawn  # pragma: no cover - расстановка всегда полная


def quarrel() -> list[tuple[str, str, str, str]]:
    """Кадры, спорящие не с планом, а друг с другом.

    Если на кадре F303 проём в F304 нарисован на севере, то на кадре
    F304 проём в F303 обязан быть на юге. Где это не сходится, кадры
    сами себе противоречат, и никакая раскладка комнат их не
    примирит: такие кадры надо перерисовывать.
    """
    back = {"north": "south", "south": "north", "east": "west", "west": "east"}
    out = []
    for code, sides in DRAWN_EYES.items():
        for drawn, there in sides.items():
            mate = DRAWN_EYES.get(there)
            if not there or mate is None:
                continue
            side = plan_side(code, drawn)
            was = [
                plan_side(there, one) for one, who in mate.items()
                if who == code
            ]
            if was and back[side] not in was:
                out.append((code, side, there, was[0]))
    return out


def clashes() -> list[tuple[str, str, str, str]]:
    """Где подтверждённый кадр спорит с планом этажа.

    Ранние кадры рисовались до ортогональных планов, и часть из них
    показывает проём не на той стене, где он стоит по карте. Спор
    решает не этот скрипт: он только называет комнаты, чтобы кадр
    перерисовали или подвинули комнату на плане.

    Лестницы сюда не попадают: у хода на другой этаж своего
    направления нет, и `doorways` ставит его на ту стену, где его уже
    нарисовали.
    """
    out = []
    for code, sides in DRAWN_EYES.items():
        plan_sides = doorways(code)
        for drawn, there in sides.items():
            side = plan_side(code, drawn)
            mine = plan_sides.get(side, "")
            if mine != there:
                out.append((code, side, there or "хода нет", mine or "хода нет"))
    return out


def stairs_of(room, place) -> list[tuple[str, str, bool]]:
    """Лестницы комнаты: код за проёмом, этаж и вверх ли он."""
    out = []
    for door in place.doors_from(room.code):
        there = place.room(door.other(room.code))
        if there is None or there.layer == room.layer:
            continue
        out.append((
            there.code,
            LAYER_SHORT.get(there.layer, there.layer),
            LAYER_DEPTH.get(there.layer, 0) > LAYER_DEPTH.get(room.layer, 0),
        ))
    return out


def conditions(door) -> str:
    """Чем дверь заперта, словами манифеста."""
    said = ", ".join(door.requires) if door.requires else "без условий"
    if door.closed_by:
        said += ", закрыт при " + ", ".join(door.closed_by)
    return said


def tables() -> str:
    """Таблицы «комната → что на каждой из четырёх сторон»."""
    place = station()
    cross = [
        door for door in place.doors
        if place.room(door.a).layer != place.room(door.b).layer
    ]
    inside = len(place.doors) - len(cross)
    lines = [
        "# Карта станции «Контур»",
        "",
        "Файл собирается `scripts/station_map.py` из самой карты —",
        "править его руками незачем, он перезапишется. Правила чтения",
        "живут в скрипте, туда же их и переносить.",
        "",
        f"Комнат: {len(place.rooms)}. Двусторонних переходов: "
        f"{len(place.doors)} — {inside} внутри плана и {len(cross)} между",
        "планами. Шаг — 10 секунд, поворот — 90°.",
        "",
        "**Как читать.** Север — вверх на плане. Строка — комната,",
        "столбец — сторона света, в клетке код комнаты за проёмом.",
        "Это ровно то, что боец видит, стоя в комнате и глядя в ту",
        "сторону: по этим же данным экран рисует проёмы, а джойстик",
        "делает шаг.",
        "",
        "**«—» значит «хода по карте нет»**, а не «на рисунке глухая",
        "стена». Нарисованный проём сам по себе хода не открывает —",
        "его открывает флаг. С южной стены F304 видна галерея, а",
        "пройти туда нельзя; восточный кадр E12 показывает выход",
        "наружу, и комнаты за ним нет вовсе.",
        "",
        "**↑ и ↓ — лестница на другой этаж.** Направления у неё нет:",
        "координаты соседнего плана лежат в своей системе. Стену она",
        "занимает свободную, и рисовать проём надо именно на той",
        "стене, которая названа тут. Условия — в таблице «Между",
        "планами».",
        "",
        "Кадр панорамы называется стороной света:",
        "`panoramas_fp/<код>/north.jpg`. Ранние кадры встречаются и в",
        "`.png` — экран пробует оба.",
        "",
    ]
    for layer in place.layers():
        rooms = sorted(place.rooms_on(layer), key=lambda one: one.code)
        lines.append(f"## {layer} · {LAYER_TITLES.get(layer, layer)}")
        lines.append("")
        drawn = LAYER_MAPS.get(layer)
        if drawn:
            lines.append(f"[Рисованный план этажа]({art_url(drawn)})")
            lines.append("")
        head = " | ".join(FACING_TITLES[one].capitalize() for one in FACINGS)
        lines.append(f"| Комната | Название | {head} |")
        lines.append("|---|---|" + "---|" * len(FACINGS))
        for room in rooms:
            sides = doorways(room.code)
            cells = []
            for facing in FACINGS:
                there = sides.get(facing)
                if not there:
                    cells.append("—")
                    continue
                neighbour = place.room(there)
                if neighbour.layer != room.layer:
                    sign = "↑" if (
                        LAYER_DEPTH.get(neighbour.layer, 0)
                        > LAYER_DEPTH.get(room.layer, 0)
                    ) else "↓"
                    short = LAYER_SHORT.get(neighbour.layer, neighbour.layer)
                    cells.append(f"{sign} {there} · {short}")
                    continue
                cells.append(f"{there} {neighbour.name}")
            lines.append(
                f"| **{room.code}** | {room.name} | " + " | ".join(cells) + " |"
            )
        lines.append("")

    lines += [
        "## Между планами",
        "",
        "У этих ходов стороны света нет: они ведут вверх или вниз.",
        "",
        "| Переход | Куда | Условие |",
        "|---|---|---|",
    ]
    for door in cross:
        here, there = place.room(door.a), place.room(door.b)
        sign = "↑" if (
            LAYER_DEPTH.get(there.layer, 0) > LAYER_DEPTH.get(here.layer, 0)
        ) else "↓"
        lines.append(
            f"| {door.a} ↔ {door.b} | {sign} "
            f"{LAYER_SHORT.get(there.layer, there.layer)} | "
            f"{conditions(door)} |"
        )
    lines.append("")

    shut = [
        door for door in place.doors
        if door not in cross and (door.requires or door.closed_by)
    ]
    lines += [
        "## Запертые двери внутри плана",
        "",
        "| Переход | Условие |",
        "|---|---|",
    ]
    for door in shut:
        lines.append(f"| {door.a} ↔ {door.b} | {conditions(door)} |")
    lines.append("")

    lines += ["## Виды, которые меняются по ходу рейда", ""]
    if EYE_STAGES:
        lines += [
            "Пока флага нет, кадр берётся из подпапки; поднялся — обычный.",
            "",
            "| Комната | Сторона | До флага | Флаг |",
            "|---|---|---|---|",
        ]
        for (code, side), (folder, flag) in sorted(EYE_STAGES.items()):
            lines.append(
                f"| {code} | {FACING_TITLES[side]} | "
                f"`panoramas_fp/{code}/{folder}/{side}.jpg` | {flag} |"
            )
        lines.append("")

    tight = crowded()
    lines.append("## Двери, стоящие не на своей стене")
    lines.append("")
    if not tight:
        lines += [
            f"Таких нет: все {len(place.doors)} дверей стоят на тех стенах,",
            "за которыми и правда сосед. Значит, таблицы выше — это не",
            "«как считает движок», а просто план станции: что в клетке",
            "написано, то боец и увидит.",
            "",
            "Так получилось не само. Комнаты разложены по клеткам",
            "ортогональных планов, ходов по диагонали нет вовсе, и двух",
            "ходов в одну сторону из одной комнаты тоже. Проверка",
            "`test_every_door_stands_on_the_wall_it_leads_through` следит,",
            "чтобы правка карты не вернула ни того, ни другого.",
            "",
        ]
    else:
        lines += [
            f"Таких {len(tight)} из {len(place.doors)}. Это не поломка, а",
            "теснота: из одной комнаты бывает два хода в одну сторону, а",
            "стен четыре, поэтому второй занимает ближайшую свободную.",
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

    spat = clashes()
    lines.append("## Кадры, спорящие с планом")
    lines.append("")
    if not spat:
        lines += [
            "Таких нет: все подтверждённые кадры показывают проёмы там,",
            "где они стоят по карте.",
            "",
        ]
    else:
        lines += [
            f"Подтверждённых кадров {sum(len(one) for one in DRAWN_EYES.values())},",
            f"и {len(spat)} из них спорят с планом этажа. Эти кадры",
            "рисовались до ортогональных планов. Спор решает человек:",
            "или кадр перерисовывается, или комната переставляется на",
            "плане — и тогда план придётся переложить целиком.",
            "",
            "| Комната | Сторона | На кадре | По плану |",
            "|---|---|---|---|",
        ]
        for code, side, drawn_there, plan_there in spat:
            lines.append(
                f"| {code} | {FACING_TITLES[side]} | {drawn_there} | "
                f"{plan_there} |"
            )
        lines.append("")
        feud = quarrel()
        if feud:
            lines += [
                f"Из них {len(feud)} спорят даже не с планом, а между",
                "собой: сосед на кадре лежит в одной стороне, а на его",
                "собственном кадре — в другой. Такую пару не примирит",
                "никакая раскладка комнат, её можно только перерисовать.",
                "",
                "| Комната | Сторона | Сосед | А у соседа она на стене |",
                "|---|---|---|---|",
            ]
            for code, side, there, other in feud:
                lines.append(
                    f"| {code} | {FACING_TITLES[side]} | {there} | "
                    f"{FACING_TITLES[other]} |"
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
        # Лестницу подписываем так же, как на миникарте у игрока:
        # вверх или вниз и на какой этаж. Код комнаты за ней человеку
        # ничего не говорит — этажей семь
        steps: dict[str, int] = {}
        for there, short, up in stairs_of(room, place):
            steps[short] = 1 if up else -1
        stairs = [
            ("↑ " if up > 0 else "↓ ") + title
            # Сперва то, что выше: этажи читают сверху вниз
            for title, up in sorted(steps.items(), key=lambda one: -one[1])
        ]
        parts.append(
            f'<rect x="{room.x - 60}" y="{room.y - 34}" width="120" '
            f'height="68" rx="10" class="cell"/>'
            f'<text x="{room.x}" y="{room.y - 8}" class="code">{room.code}</text>'
            f'<text x="{room.x}" y="{room.y + 12}" class="name">'
            f'{room.name[:17]}</text>'
        )
        # Подпись лежит под комнатой, на плашке цвета фона: иначе её
        # перечеркнул бы коридор, идущий между центрами комнат
        for step, say in enumerate(stairs):
            top_at = room.y + 42 + step * 22
            wide_at = len(say) * 8 + 12
            parts.append(
                f'<rect x="{room.x - wide_at / 2}" y="{top_at}" '
                f'width="{wide_at}" height="20" rx="6" class="back"/>'
                f'<text x="{room.x}" y="{top_at + 15}" class="stairs">'
                f'{say}</text>'
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
        ".back{fill:#171c22}"
        "</style></head><body><h1>Станция «Контур»: этажи и переходы</h1>"
        "<p>Север — вверх. Прямоугольник — комната, линия — дверь, "
        "«↑ 2 этаж» — лестница и куда она ведёт. Что видно с каждой "
        "стороны, сказано в <code>docs/blackout_map.md</code>.</p>"
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
    print(f"кадров, спорящих с планом: {len(clashes())}")
    print(f"кадров, спорящих между собой: {len(quarrel())}")


if __name__ == "__main__":
    main()
