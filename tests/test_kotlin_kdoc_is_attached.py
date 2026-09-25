"""Ogni KDoc del Kotlin documenta la dichiarazione che la segue.

Due KDoc di fila vogliono dire che la prima non documenta niente: la sua
dichiarazione è stata tolta o spostata, e il commento è rimasto a descrivere
qualcosa che non c'è più — o, peggio, sembra descrivere quella dopo. La
revisione profonda del 25/09 ne ha trovate in ``FloatingOverlayController``
(tre), ``JennyBrowserBridge`` e ``MainActivity``.

Qui il bersaglio sono proprio i commenti, quindi si legge il sorgente grezzo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ANDROID_SRC = Path(__file__).resolve().parents[1] / "android/app/src/main/java/com/flagdizero/jenny"


def test_no_kdoc_is_followed_by_another_kdoc() -> None:
    files = sorted(ANDROID_SRC.glob("*.kt"))
    if not files:
        pytest.skip("sorgente Android non presente in questo checkout")
    orphans = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\*/\s*/\*\*", src):
            orphans.append(f"{path.name}:{src.count(chr(10), 0, m.start()) + 1}")
    assert not orphans, f"KDoc senza dichiarazione (seguita da un'altra KDoc): {orphans}"
