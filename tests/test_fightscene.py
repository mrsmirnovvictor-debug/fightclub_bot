"""Адаптер сцены боя: серверный ход превращается в очередь показа.

Сами проверки написаны на том же языке, что и адаптер, и лежат в
`tests/js/fightscene.test.js`: это чистая логика без браузера, и гонять
её через Playwright значило бы поднимать Chromium ради арифметики.
Здесь — обёртка, которая запускает их настоящим node и приносит вывод в
общий прогон.

Node в системе нет — тест пропускается, как и браузерные: остальной
прогон от него не зависит.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parent / "js" / "fightscene.test.js"
NODE = shutil.which("node") or "/opt/node22/bin/node"


def test_the_fight_scene_adapter_keeps_its_contract():
    """Прогнать проверки адаптера и показать их вывод при провале."""
    if not Path(NODE).exists():  # pragma: no cover - зависит от машины
        pytest.skip("node в системе нет")

    done = subprocess.run(
        [NODE, str(SUITE)], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, (
        "проверки адаптера не прошли:\n" + done.stdout + done.stderr
    )
    # Пустой вывод означал бы, что файл проверок подменили заглушкой
    assert "все проверки прошли" in done.stdout
