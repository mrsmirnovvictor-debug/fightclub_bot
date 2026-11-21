"""Графика сцены боя: манифесты против того, что знает игра.

Манифесты рисует и кладёт человек, а читает их renderer, у которого нет
ни глаз, ни догадки. Разойдётся манифест с игрой — сцена не упадёт, а
покажет не то: чужое тело, удар не в ту зону, пустую клетку атласа. Это
хуже ошибки, потому что молча.

Поэтому здесь сверяется ровно то, на что renderer опирается: есть ли
комплект на каждый класс, те ли зоны, то ли число кадров, та ли папка.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.game.art import ALL_ART_LINES, ART_LINES, FEMALE_LINE, MASTER_LINE, art_line, combat_base
from bot.game.classes import ALL_ZONES, FIGHTER_CLASSES
from bot.webapp.server import STATIC_DIR

FIGHT_DIR = STATIC_DIR / "fight"
# Сколько кадров в каждом наборе. Пять зон по шесть кадров у удара,
# блока и реакции; три ряда по шесть у движения и уворота
SETS = {"attack": 30, "block": 30, "hit": 30, "move": 18, "dodge": 18}
FRAMES = 126


def manifest(line: str) -> dict:
    return json.loads((FIGHT_DIR / line / "manifest.json").read_text(encoding="utf-8"))


def test_the_art_line_is_known_for_every_class_in_the_game():
    """У каждого класса игры есть своя линия образов, и лишних нет.

    Заведётся пятый класс — сцена должна сломаться здесь, на проверке, а
    не в бою попыткой нарисовать несуществующую папку.
    """
    assert set(ART_LINES) == set(FIGHTER_CLASSES), "классы и линии разошлись"
    assert ART_LINES["rogue"] == "trickster", "папка зовётся названием класса"


def test_the_female_body_is_one_line_for_every_class():
    """Женский боец выходит одним комплектом, какого бы класса он ни был.

    Тело рисовали под пол, а не под класс: четырёх женских комплектов
    нет и не будет, и спрашивать класс при выборе папки незачем.
    """
    for code in FIGHTER_CLASSES:
        assert art_line(code, female=True) == FEMALE_LINE
        assert art_line(code) == ART_LINES[code], "мужской боец остался классовым"


def test_every_line_the_scene_can_show_is_on_the_list():
    """Список линий — полный: классовые, женская и линия Тренера.

    По нему идут все проверки ниже. Линия, не попавшая в список,
    осталась бы без единой проверки — и разъехалась бы с игрой молча.
    """
    assert set(ALL_ART_LINES) == set(ART_LINES.values()) | {
        FEMALE_LINE, MASTER_LINE
    }
    for line in ALL_ART_LINES:
        assert (FIGHT_DIR / line / "manifest.json").exists(), line


@pytest.mark.parametrize("line", ALL_ART_LINES)
def test_every_class_brings_a_full_set_of_frames(line: str):
    """На линию — манифест, в нём пять наборов и ровно сто двадцать шесть кадров."""
    data = manifest(line)
    assert data["classCode"] == line, f"{line}: манифест не от своей линии"

    counted = {name: len(one["frames"]) for name, one in data["sets"].items()}
    assert counted == SETS, line
    assert sum(counted.values()) == FRAMES, line


@pytest.mark.parametrize("line", ALL_ART_LINES)
def test_the_zones_are_the_ones_the_engine_names(line: str):
    """Зоны манифеста — те же, что в правилах, и в том же порядке.

    Ряды атласа читаются по порядку зон: нулевой — голова, последний —
    ноги. Переставь их в манифесте, и удар в голову нарисуется удором
    по ногам, не сообщив об этом никому.
    """
    data = manifest(line)
    game = [zone.value for zone in ALL_ZONES]
    assert data["zones"] == game, line
    for name in ("attack", "block", "hit"):
        assert data["rowMap"][name] == game, f"{line}.{name}"


@pytest.mark.parametrize("line", ALL_ART_LINES)
def test_each_atlas_lies_where_its_line_lies(line: str):
    """Адрес атласа складывается из базы и пути манифеста, и путь — свой.

    Пути опубликованы относительными нарочно: бакет знает только
    `bot.game.art`. А вот папка в пути обязана быть своей — иначе танк
    выйдет в бой телом трикстера, и тест тут единственное, что это
    заметит.
    """
    data = manifest(line)
    for name, one in data["sets"].items():
        assert one["url"] == f"runtime/{line}/{name}.webp", f"{line}.{name}"
        assert not one["url"].startswith("http"), "адрес бакета в манифесте не нужен"
        assert combat_base().endswith("/"), "база склеивается с путём напрямую"


@pytest.mark.parametrize("line", ALL_ART_LINES)
def test_every_frame_fits_inside_its_atlas(line: str):
    """Прямоугольник кадра не вылезает за край атласа.

    Вылезший кадр рисуется прозрачной пустотой: боец просто исчезает на
    своём ударе, и в консоли об этом ни слова.
    """
    data = manifest(line)
    for name, one in data["sets"].items():
        width, height = one["width"], one["height"]
        for frame in one["frames"]:
            rect = frame["rect"]
            assert rect["x"] >= 0 and rect["y"] >= 0, f"{line}.{frame['id']}"
            assert rect["x"] + rect["w"] <= width, f"{line}.{frame['id']}"
            assert rect["y"] + rect["h"] <= height, f"{line}.{frame['id']}"
            assert rect["w"] > 0 and rect["h"] > 0, f"{line}.{frame['id']}"
            # Логический размер — не размер обрезанной картинки: по
            # нему кадр и рисуется, и подменять его нельзя
            assert frame["drawSize"]["w"] > 0, f"{line}.{frame['id']}"
            assert frame["drawSize"]["h"] > 0, f"{line}.{frame['id']}"


def test_the_scene_agrees_with_the_background_it_stands_on():
    """Логическая сцена, пол и якоря взяты из параметров фона, а не из кода."""
    data = json.loads((FIGHT_DIR / "background.json").read_text(encoding="utf-8"))
    assert data["logicalSize"] == {"width": 740, "height": 370}
    assert data["floorY"] == 332
    left = data["fighterAnchors"]["left"]
    right = data["fighterAnchors"]["right"]
    assert left["y"] == right["y"] == data["floorY"], "оба стоят на одном полу"
    assert left["x"] < right["x"], "левый действительно левее"
    # Якоря симметричны центру: иначе одна сторона прижата к краю
    middle = data["logicalSize"]["width"] / 2
    assert middle - left["x"] == pytest.approx(right["x"] - middle)
    assert data["asset"] == "runtime/background.webp", "адрес относительный"


def test_the_page_tells_the_scene_where_its_pictures_are():
    """Страница несёт адрес графики, но не знает бакета сама."""
    from bot.webapp.server import asset_stamp, stamped_page

    page = stamped_page(asset_stamp())
    assert f'data-combat="{combat_base()}"' in page
    assert "{{combat}}" not in page, "слот подставлен"
    assert "r2.dev" not in (STATIC_DIR / "card.html").read_text(encoding="utf-8")


# ---------- линия образов в выдаче ----------


def test_the_payload_names_the_art_folder_for_every_class():
    """Сцена не должна угадывать папку образов по коду класса.

    Имя папки не всегда равно коду: `rogue` рисуется как `trickster`.
    Соответствие живёт в `bot.game.art`, и выдача присылает готовое
    имя — иначе в браузере завелась бы вторая копия карты, они бы
    разошлись, и сцена молча начала грузить образы не того бойца.
    """
    for code in FIGHTER_CLASSES:
        line = ART_LINES[code]
        assert (FIGHT_DIR / line / "manifest.json").exists(), (
            f"у класса {code} нет папки образов {line}"
        )

    assert ART_LINES["rogue"] == "trickster", (
        "трикстер рисуется из своей папки, а не из папки с кодом класса"
    )


def test_the_payload_carries_the_art_line_next_to_the_class_code():
    """В выдаче бойца есть и код класса, и имя папки образов."""
    from bot.game.battle import Fighter
    from bot.webapp.fight import fighter_payload

    one = FIGHTER_CLASSES["rogue"]
    fighter = Fighter(
        user_id=1, name="Боец", fclass=one, stats=one.base_stats
    )
    out = fighter_payload(fighter, ready=True, you=True)
    assert out["class_code"] == "rogue"
    assert out["art_line"] == ART_LINES["rogue"] == "trickster"
    # Название класса локализовано, по нему выбирать нельзя
    assert out["fclass"] != out["art_line"]


def test_the_scene_is_wired_only_to_modes_the_game_really_has():
    """Режимы, которые идут сценой, должны существовать в движке."""
    import re

    from bot.game.modes import FightMode
    from bot.webapp.server import STATIC_DIR

    card = (STATIC_DIR / "card.js").read_text(encoding="utf-8")
    found = re.search(r"const SCENE_MODES = \[([^\]]*)\]", card)
    assert found, "список режимов сцены не найден в card.js"
    codes = re.findall(r'"([^"]+)"', found.group(1))
    assert codes, "сцена не включена ни для одного режима"
    real = {mode.value for mode in FightMode}
    assert set(codes) <= real, f"выдуманные режимы: {set(codes) - real}"


# ---------- стойка не ездит ----------

# Насколько якорю стойки позволено гулять между кадрами, в логических
# единицах сцены. Стойка крутится в петле всё время, пока боец ждёт
# хода, и сдвиг якоря переставляет фигуру по экрану: не дыхание, а
# прыжок. Число взято по тому, что уже нарисовано, с запасом — самый
# неровный из четырёх классов укладывается в десять:
#
#   warrior 10.5 · assassin 2.4 · tank 6.2 · trickster 0.0
#
# Трикстер до исправления давал 90 и выглядел как бег туда-обратно.
# Проверки на это не было, поэтому он и доехал до прода
STANCE_DRIFT = 15.0


def stance_offsets(line: str) -> list[float]:
    """Где стоит якорь относительно середины кадра, по кадрам стойки."""
    book = json.loads((FIGHT_DIR / line / "manifest.json").read_text("utf-8"))
    row = book["rowMap"]["move"].index("stance_reference")
    return [
        frame["pivot"]["x"] - frame["drawSize"]["w"] / 2
        for frame in book["sets"]["move"]["frames"]
        if frame["row"] == row
    ]


@pytest.mark.parametrize("line", sorted(set(ART_LINES.values())))
def test_the_stance_does_not_walk_the_fighter_across_the_scene(line):
    """Якорь стойки стоит на месте: петля дышит, а не ходит.

    Якорь говорит, какой точкой кадр садится на свою отметку на полу.
    Если он гуляет от кадра к кадру, фигура переставляется по экрану
    каждые несколько десятков миллисекунд — и спокойное ожидание хода
    превращается в беготню. Ровно это и случилось с трикстером.

    Сверяем кадры между собой, а не с серединой: у ассасина и танка
    якорь смещён от середины на сорок с лишним, и они стоят спокойно,
    потому что смещены одинаково. Беду делает разброс, а не смещение.
    """
    offsets = stance_offsets(line)
    assert len(offsets) == 6, f"{line}: кадров стойки не шесть, а {len(offsets)}"
    drift = max(offsets) - min(offsets)
    assert drift <= STANCE_DRIFT, (
        f"{line}: якорь стойки гуляет на {drift:.1f} — фигура будет "
        f"переставляться по экрану. Отклонения по кадрам: "
        f"{[round(one, 1) for one in offsets]}"
    )


def test_a_fighter_in_his_stance_keeps_to_his_half_of_the_scene():
    """Боец стоит на своей половине и не залезает в соперника.

    Якорь говорит, какой точкой кадр садится на свою отметку: левый
    боец рисуется влево от неё на `pivot.x`, правый — зеркально. Если
    силуэт широк, а якорь смещён к краю, фигура уезжает в середину — и
    двое в стойке оказываются нарисованы друг в друге. Так и было:
    танки перекрывались на 27 единиц, ассасины сходились до трёх.

    Середина — честная граница: пары бывают любые, и правило «каждый на
    своей половине» держится при любом сочетании классов, а проверка
    зазора между конкретными двумя — нет.
    """
    scene = json.loads((FIGHT_DIR / "background.json").read_text("utf-8"))
    anchors = scene["fighterAnchors"]
    middle = (anchors["left"]["x"] + anchors["right"]["x"]) / 2

    for line in sorted(set(ART_LINES.values())):
        book = json.loads((FIGHT_DIR / line / "manifest.json").read_text("utf-8"))
        row = book["rowMap"]["move"].index("stance_reference")
        for frame in book["sets"]["move"]["frames"]:
            if frame["row"] != row:
                continue
            width, pivot = frame["drawSize"]["w"], frame["pivot"]["x"]
            # Левый: от якоря влево на pivot.x, дальше вправо на ширину
            edge = anchors["left"]["x"] - pivot + width
            assert edge <= middle, (
                f"{line}, кадр стойки {frame['frame']}: силуэт доходит до "
                f"{edge:.0f} при середине сцены {middle:.0f} — боец залезает "
                f"на половину соперника"
            )
            # Правый зеркален, поэтому достаточно проверить одного


# ---------- падение кончается лёжа ----------


def ko_frames(line: str) -> list[dict]:
    """Кадры ряда падения по порядку."""
    book = json.loads((FIGHT_DIR / line / "manifest.json").read_text("utf-8"))
    row = book["rowMap"]["move"].index("ko")
    return sorted(
        (one for one in book["sets"]["move"]["frames"] if one["row"] == row),
        key=lambda one: one["frame"],
    )


@pytest.mark.parametrize("line", sorted(set(ART_LINES.values())))
def test_a_knocked_out_fighter_ends_up_lying(line):
    """Последний кадр падения — лежачий, и это не придирка к рисунку.

    Сцена держит бойца на последнем кадре всё время, пока бой не
    закрыли: это его поза до конца. Окажись она стоячей — и побеждённый
    так и простоит над собственным поражением.
    """
    last = ko_frames(line)[-1]["drawSize"]
    assert last["w"] > last["h"], (
        f"{line}: последний кадр падения {last['w']:.0f}x{last['h']:.0f} — "
        f"боец на нём стоит, а должен лежать"
    )


@pytest.mark.parametrize("line", sorted(set(ART_LINES.values())))
def test_a_fall_never_lifts_the_body_back_up(line):
    """Тело по ходу падения только оседает.

    Высота кадра — это высота силуэта: пока боец валится, она обязана
    убывать. Выросшая посередине высота значит, что тело подкинуло, и
    падение читается как прыжок.
    """
    heights = [one["drawSize"]["h"] for one in ko_frames(line)]
    grown = [
        (number, heights[number], heights[number + 1])
        for number in range(len(heights) - 1)
        if heights[number + 1] > heights[number]
    ]
    assert not grown, (
        f"{line}: тело поднимается обратно {grown}, все высоты {heights}"
    )
    assert heights[-1] < heights[0] / 2, (
        f"{line}: тело почти не осело — {heights[0]:.0f} → {heights[-1]:.0f}"
    )
