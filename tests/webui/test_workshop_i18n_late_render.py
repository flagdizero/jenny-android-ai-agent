"""L'officina ridisegna i cassetti quando arrivano le traduzioni.

``mobile-app.js`` carica il file di lingua senza aspettarlo, e le etichette dei
cassetti le scrive JS. Sul telefono il file arriva da loopback e vince quasi
sempre la gara col primo disegno; aperta da un altro dispositivo via Tailscale,
con un giro di rete in piu', una volta su tre restava «settings.telegram.title»
(misurato il 14/10/2026). La richiamata della ``load`` deve quindi ridisegnare
anche le impostazioni, non solo le scritte statiche.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = (
    Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "mobile-app.js"
).read_text(encoding="utf-8")


def test_the_i18n_callback_rerenders_the_drawers() -> None:
    m = re.search(r"i18n\.load\(i18n\.locale\)\.then\(\(\) => \{(.*?)\n    \}\);", APP, re.S)
    assert m, "la richiamata di i18n.load non si trova piu'"
    assert "this._settings?.render()" in m.group(1), (
        "i cassetti disegnati prima delle traduzioni restano con le chiavi grezze"
    )
