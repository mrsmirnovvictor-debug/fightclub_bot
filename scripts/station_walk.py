"""Карта станции для обходчика панорам — одним файлом для браузера.

Обходчик (`bot/webapp/static/station.html`) ходит по станции без зомби
и спрашивает у карты ровно одно: что по плану на каждой стене комнаты.
Ответ берётся из `doorways` — той же функции, по которой экран рисует
проёмы и ходит джойстик. Расходиться обходчику и игре негде.

    python3 scripts/station_walk.py          # пересобрать station.json
    python3 scripts/station_walk.py --show   # посмотреть, не записывая

Почему файлом, а не ручкой на сервере: обходчик открывается по прямой
ссылке, без Телеграма и без входа, а все ручки мини-аппа спрашивают
подпись. Статика же отдаётся как есть.

Файл сгенерирован и в руки не правится: за этим следит проверка
`test_the_walker_map_matches_the_station_itself`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.game.blackout import (  # noqa: E402
    EYE_FORMATS,
    EYE_STAGES,
    FACINGS,
    ZOMBIE_ART,
    doorways,
    station,
)

OUT = Path(__file__).resolve().parent.parent / "bot" / "webapp" / "static"
FILE = OUT / "station.json"


def walls(code: str, place) -> dict[str, dict]:
    """Что по плану на каждой стене: комната за ней и лестница ли это.

    Лестница помечается особо. У хода между этажами своего направления
    нет — координаты соседнего плана лежат в своей системе, — и стену
    он занимает любую свободную. Кадр такой стены поворотом не судят.
    """
    here = place.room(code)
    out: dict[str, dict] = {}
    for side, there in doorways(code).items():
        room = place.room(there)
        out[side] = {
            "to": there,
            "name": room.name if room else there,
            "stair": bool(room and room.layer != here.layer),
        }
    return out


def carte() -> dict:
    """Вся станция для браузера: комнаты, стены, этажи и адрес кадров."""
    place = station()
    rooms: dict[str, dict] = {}
    order: list[str] = []
    layers: list[dict] = []
    for layer in place.layers():
        here = place.rooms_on(layer)
        layers.append({
            "code": layer,
            "title": here[0].layer_title if here else layer,
            "rooms": [one.code for one in here],
        })
        order.extend(one.code for one in here)
    for one in (place.room(code) for code in order):
        row = {
            "name": one.name,
            "layer": one.layer,
            "x": one.x,
            "y": one.y,
            "safe": bool(one.safe),
            "walls": walls(one.code, place),
        }
        if one.objective:
            row["objective"] = one.objective
        if one.encounter:
            row["encounter"] = one.encounter
        # Стена, у которой вид меняется по ходу рейда: у неё два кадра,
        # и обходчику надо показать оба — иначе половина работы не
        # видна вовсе
        stages = {
            side: folder
            for (place_code, side), (folder, _flag) in EYE_STAGES.items()
            if place_code == one.code
        }
        if stages:
            row["stages"] = stages
        rooms[one.code] = row

    return {
        "art": f"{ZOMBIE_ART}/panoramas_fp",
        "formats": list(EYE_FORMATS),
        "facings": list(FACINGS),
        "layers": layers,
        "order": order,
        "rooms": rooms,
    }


def text() -> str:
    """Готовый файл строкой — с ней же сверяется проверка."""
    return json.dumps(carte(), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show", action="store_true", help="напечатать, а не записывать"
    )
    args = parser.parse_args()
    body = text()
    if args.show:
        print(body)
        return
    FILE.write_text(body, encoding="utf-8")
    place = station()
    print(f"{FILE}: {len(place.rooms)} комнат")


if __name__ == "__main__":
    main()
