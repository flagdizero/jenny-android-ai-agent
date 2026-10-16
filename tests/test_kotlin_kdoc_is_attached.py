"""Ogni KDoc del Kotlin documenta la dichiarazione che la segue.

Due KDoc di fila vogliono dire che la prima non documenta niente: la sua
dichiarazione è stata tolta o spostata, e il commento è rimasto a descrivere
qualcosa che non c'è più — o, peggio, sembra descrivere quella dopo. Il 25/09
se ne sono trovate in ``FloatingOverlayController`` (tre), ``JennyBrowserBridge``
e ``MainActivity``.

Qui il bersaglio sono proprio i commenti, quindi si legge il sorgente grezzo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ANDROID_SRC = Path(__file__).resolve().parents[1] / "android/app/src/main/java/com/flagdizero/jenny"


def test_no_kdoc_is_followed_by_another_kdoc() -> None:
    # Tutti i source set: ``main`` e quelli dei flavor (``src/play``, ``src/foss``).
    files = sorted(ANDROID_SRC.parents[4].glob("*/java/com/flagdizero/jenny/*.kt"))
    if not files:
        pytest.skip("sorgente Android non presente in questo checkout")
    orphans = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\*/\s*/\*\*", src):
            orphans.append(f"{path.name}:{src.count(chr(10), 0, m.start()) + 1}")
    assert not orphans, f"KDoc senza dichiarazione (seguita da un'altra KDoc): {orphans}"
