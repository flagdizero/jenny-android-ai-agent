"""``MainHop.call`` scrive nel log e torna il fallback anche sul main thread.

La KDoc lo prometteva «sempre», ma il ramo che esegue il blocco sul posto (già
sul main) non aveva il ``try``: un'eccezione lì risaliva al chiamante. Oggi
nessun chiamante arriva dal main thread, ma la regola scritta deve valere
sulla carta e nel codice. Visto dalla revisione finale della pulizia (25/09).

Il Kotlin non gira in CI: la regola si fissa sul sorgente.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MAIN_HOP = (
    Path(__file__).resolve().parents[2]
    / "android/app/src/main/java/com/flagdizero/jenny/MainHop.kt"
)


def test_the_in_place_branch_catches_and_falls_back() -> None:
    if not MAIN_HOP.is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    src = MAIN_HOP.read_text(encoding="utf-8")
    m = re.search(
        r"if \(Looper\.myLooper\(\) == Looper\.getMainLooper\(\)\) \{(.*?)\n        \}\n",
        src,
        re.S,
    )
    assert m, "il ramo sul posto non è più un blocco: torna block() senza try?"
    branch = m.group(1)
    assert "try" in branch and "catch (e: Exception)" in branch
    assert "fallback" in branch
