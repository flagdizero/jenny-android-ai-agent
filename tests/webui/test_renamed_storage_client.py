"""Quel che il browser del telefono ha salvato coi nomi italiani si legge ancora.

Il 25/09/2026 i nomi del front-end sono passati all'inglese, e tre cose salvate in
``localStorage`` portavano un nome o un valore italiano: la visibilita' e la taglia
della mascotte (``jenny-mascotte-visible``/``-size``), i due temi ``fumetto`` e
``pietra`` in ``tc-theme``, e i tre cassetti dell'officina in ``mobile-last-mode``
(``cervello``, ``mani``, ``memoria``). Una preferenza scelta dall'utente non deve
tornare al default per un rinomino: qui si prova che passa al nome nuovo.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from support.js_harness import requires_node, run_module

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_FAKE_STORAGE = """
const store = new Map(Object.entries(globalThis.__SEED__ || {}));
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: (k) => { store.delete(k); },
};
globalThis.document = { documentElement: { style: { setProperty() {} }, setAttribute() {} } };
globalThis.window = { dispatchEvent() {} };
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; Object.assign(this, init); } };
"""


def _run_mascot(seed: dict[str, str]) -> dict[str, str | None]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copy(ASSETS / "shared" / "mascot.js", root / "mascot.js")
        entry = root / "prova.mjs"
        entry.write_text(
            f"globalThis.__SEED__ = {json.dumps(seed)};\n"
            + _FAKE_STORAGE
            + "const m = await import('./mascot.js');\n"
            + "const keys = ['jenny-mascot-visible', 'jenny-mascot-size',"
            + " 'jenny-mascotte-visible', 'jenny-mascotte-size'];\n"
            + "console.log(JSON.stringify({"
            + " stored: Object.fromEntries(keys.map((k) => [k, localStorage.getItem(k)])),"
            + " visible: m.mascotVisible(), size: m.mascotSize() }));\n",
            encoding="utf-8",
        )
        return json.loads(run_module(entry))


def test_the_old_mascot_keys_move_to_the_new_names() -> None:
    out = _run_mascot({"jenny-mascotte-visible": "0", "jenny-mascotte-size": "lg"})
    assert out["stored"] == {
        "jenny-mascot-visible": "0",
        "jenny-mascot-size": "lg",
        "jenny-mascotte-visible": None,
        "jenny-mascotte-size": None,
    }
    assert out["visible"] is False
    assert out["size"] == "lg"


def test_a_value_already_under_the_new_name_wins() -> None:
    """Il nome nuovo e' la scelta piu' recente: quello vecchio non la scavalca."""
    out = _run_mascot({"jenny-mascotte-size": "lg", "jenny-mascot-size": "md"})
    assert out["stored"]["jenny-mascot-size"] == "md"
    assert out["stored"]["jenny-mascotte-size"] is None


def test_the_first_paint_reads_the_old_mascot_keys_too() -> None:
    """``bootstrap.js`` gira prima di ``mascot.js``: al primo avvio dopo
    l'aggiornamento la chiave nuova non c'e' ancora, e senza il nome vecchio la
    mascotte nascosta lampeggerebbe visibile per un fotogramma."""
    source = (ASSETS / "bootstrap.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('jenny-mascotte-visible')" in source
    assert "localStorage.getItem('jenny-mascotte-size')" in source


def _migration(source: str) -> dict[str, str]:
    m = re.search(r"MIGRATION = (\{[^}]*\})", source)
    assert m, "MIGRATION non trovata"
    return json.loads(re.sub(r"(\w+):", r'"\1":', m.group(1)).replace("'", '"'))


def test_the_italian_theme_ids_are_read_as_the_new_ones() -> None:
    """Le due copie della tabella — quella del primo fotogramma e quella del
    modulo — dicono la stessa cosa, e portano ``fumetto``/``pietra`` ai nomi nuovi."""
    boot = _migration((ASSETS / "bootstrap.js").read_text(encoding="utf-8"))
    module = _migration((ASSETS / "shared" / "theme.js").read_text(encoding="utf-8"))
    assert boot == module
    assert boot["fumetto"] == "comic"
    assert boot["pietra"] == "stone"
    assert boot["light"] == "stone"


def test_the_old_drawer_names_open_the_same_drawer() -> None:
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    assert "{ cervello: 'brain', mani: 'hands', memoria: 'memory' }[initialMode]" in app
