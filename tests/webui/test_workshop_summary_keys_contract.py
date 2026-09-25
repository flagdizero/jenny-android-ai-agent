"""Le righe di riepilogo dell'officina: un nome solo dal bottone al cassetto.

Il rinomino in inglese del 25/09/2026 aveva tradotto i lettori e non i
produttori: il template scriveva ancora ``id="riepilogo-…"`` mentre il codice
cercava ``#summary-…`` (tre righe ferme su «Caricamento…»), e il bottone dei
tetti portava ``data-summary="tetti"`` mentre il cassetto e la tabella dei
pannelli si chiamano ``caps`` (un bottone che non apriva niente). I test
esistenti fingevano ``querySelector`` e non potevano vederlo: questo legge il
sorgente e lega i due capi.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
SETTINGS = (UI / "assets" / "mobile-settings.js").read_text(encoding="utf-8")
WORKSHOP = (UI / "workshop.html").read_text(encoding="utf-8")


def _open_panel_keys() -> set[str]:
    body = re.search(r"get _OPEN_PANEL\(\)\s*\{\s*return\s*\{(.*?)\};", SETTINGS, re.S)
    assert body, "_OPEN_PANEL non trovato"
    return set(re.findall(r"^\s*(\w+)\s*:", body.group(1), re.M))


def test_every_summary_button_opens_a_panel_and_a_drawer() -> None:
    keys = _open_panel_keys()
    buttons = set(re.findall(r'data-summary="([\w-]+)"', SETTINGS))
    assert buttons, "nessun bottone data-summary letterale"
    for key in buttons:
        assert key in keys, f"data-summary={key!r} non e' in _OPEN_PANEL {sorted(keys)}"
        assert f'id="drawer-{key}"' in WORKSHOP, f"manca drawer-{key} in workshop.html"


def test_the_summary_value_id_is_the_one_the_code_looks_up() -> None:
    written = re.findall(r'id="([\w-]+)-\$\{id\}"', SETTINGS)
    assert written, "il template della riga di riepilogo non scrive piu' un id"
    looked_up = set(re.findall(r"querySelector\('#(summary)-[\w-]+'\)", SETTINGS))
    assert looked_up == {"summary"}
    assert "summary" in written, f"il template scrive {written}, il codice cerca #summary-…"
