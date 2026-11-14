"""Сцена боя: адаптер событий и загрузчик её графики.

Сами проверки написаны на том же языке, что и проверяемый код, и лежат
в `tests/js/`: это чистая логика без браузера, и гонять её через
Playwright значило бы поднимать Chromium ради арифметики. Здесь —
обёртки, которые запускают их настоящим node и приносят вывод в общий
прогон.

Node в системе нет — тесты пропускаются, как и браузерные: остальной
прогон от них не зависит.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent / "js"
NODE = shutil.which("node") or "/opt/node22/bin/node"


def run_suite(name: str, about: str) -> None:
    """Прогнать набор проверок на node и показать его вывод при провале."""
    if not Path(NODE).exists():  # pragma: no cover - зависит от машины
        pytest.skip("node в системе нет")

    done = subprocess.run(
        [NODE, str(JS / name)], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, (
        f"проверки {about} не прошли:\n" + done.stdout + done.stderr
    )
    # Пустой вывод означал бы, что файл проверок подменили заглушкой
    assert "все проверки прошли" in done.stdout


def test_the_fight_scene_adapter_keeps_its_contract():
    run_suite("fightscene.test.js", "адаптера")


def test_the_fight_scene_renderer_keeps_its_contract():
    """Раскадровка и отрисовка на настоящих манифестах и фоне.

    Ходы в этих проверках идут через настоящий `fightQueue`, а не
    выдуманные события: иначе проверялась бы договорённость адаптера с
    самим собой, а не то, что сцена покажет в игре.
    """
    run_suite("fightrender.test.js", "раскадровки")


def test_the_fight_scene_loader_keeps_its_contract():
    """Загрузчик читает настоящие манифесты из статики приложения.

    Поэтому набор заодно сторожит и их: подменят в манифесте путь,
    тайминг или карту рядов — проверки упадут здесь, а не в браузере у
    человека.
    """
    run_suite("fightassets.test.js", "загрузчика")
