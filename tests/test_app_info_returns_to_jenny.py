"""«Info app» di un'app Android si apre in un task suo, e Indietro torna a Jenny.

Con il solo ``FLAG_ACTIVITY_NEW_TASK``, avviata dal contesto dell'applicazione,
la schermata di sistema entrava per affinita' nel task di Impostazioni gia'
aperto: sul Titan 2, il 26/09/2026, Indietro portava a «Rete e Internet» lasciata
li' da prima, non a Jenny. Il comportamento vero si vede solo sul telefono; qui
si tiene fermo il flag che lo decide.
"""

from __future__ import annotations

import re
from pathlib import Path

_BRIDGE = (
    Path(__file__).resolve().parents[1]
    / "android/app/src/main/java/com/flagdizero/jenny/InstalledAppsBridge.kt"
)


def test_app_info_gets_its_own_task() -> None:
    source = _BRIDGE.read_text(encoding="utf-8")
    body = re.search(r"fun openAppInfo\(.*?\n    \}\n", source, re.S)
    assert body, "openAppInfo non trovata"
    assert "FLAG_ACTIVITY_NEW_DOCUMENT" in body.group(0)
