"""Quel che il browser del telefono ha salvato coi nomi italiani si legge ancora.

Il 25/09/2026 i nomi del front-end sono passati all'inglese, e tre cose salvate in
``localStorage`` portavano un nome o un valore italiano: la visibilita' e la taglia
della mascotte (``jenny-mascotte-visible``/``-size``; la taglia ha poi cambiato
scala e chiave il 25/11/2026), i due temi ``fumetto`` e
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

from support.js_harness import ASSETS, requires_node, run_module

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
            + " 'jenny-mascotte-visible', 'jenny-mascotte-size', 'jenny-mascot-scale',"
            + " 'jenny-mascot-hidden'];\n"
            + "console.log(JSON.stringify({"
            + " stored: Object.fromEntries(keys.map((k) => [k, localStorage.getItem(k)])),"
            + " size: m.mascotSize(), hidden: m.mascotHidden() }));\n",
            encoding="utf-8",
        )
        return json.loads(run_module(entry))


def test_the_old_mascot_keys_move_to_the_new_scale() -> None:
    """Il 25/11/2026 la scala e' cambiata: la grande e' la media di prima. Chi
    l'aveva media la ritrova uguale (``lg`` nuova), chi l'aveva grande scende
    alla grande nuova. I nomi vecchi, anche quello italiano, se ne vanno."""
    for before, after in (("sm", "sm"), ("md", "lg"), ("lg", "lg")):
        out = _run_mascot({"jenny-mascot-size": before})
        assert out["size"] == after, (before, out)
        assert out["stored"]["jenny-mascot-scale"] == after
        assert out["stored"]["jenny-mascot-size"] is None
    out = _run_mascot({"jenny-mascotte-size": "md"})
    assert out["size"] == "lg"
    assert out["stored"]["jenny-mascotte-size"] is None


def test_a_value_already_on_the_new_scale_wins() -> None:
    """La chiave nuova e' la scelta piu' recente: quella vecchia non la scavalca."""
    out = _run_mascot({"jenny-mascot-size": "lg", "jenny-mascot-scale": "md"})
    assert out["size"] == "md"
    assert out["stored"]["jenny-mascot-size"] is None


def test_an_old_hidden_flag_does_not_hide_her() -> None:
    """«Mostra la mascotte» e' tornata il 25/11/2026 con una chiave nuova: un «0»
    rimasto da prima del 05/10/2026 non la nasconde di sorpresa, e se ne va."""
    out = _run_mascot({"jenny-mascotte-visible": "0", "jenny-mascot-visible": "0"})
    assert out["hidden"] is False
    assert out["stored"]["jenny-mascot-visible"] is None
    assert out["stored"]["jenny-mascotte-visible"] is None
    assert _run_mascot({"jenny-mascot-hidden": "1"})["hidden"] is True


def test_the_first_paint_reads_the_old_mascot_keys_too() -> None:
    """``bootstrap.js`` gira prima di ``mascot.js``: al primo avvio dopo
    l'aggiornamento la chiave nuova non c'e' ancora, e senza i nomi vecchi la
    mascotte comparirebbe piccola e poi si ridimensionerebbe. La corrispondenza
    e' la stessa del modulo, e le chiavi vecchie della visibilita' non si
    leggono: solo quella nuova."""
    source = (ASSETS / "bootstrap.js").read_text(encoding="utf-8")
    module = (ASSETS / "shared" / "mascot.js").read_text(encoding="utf-8")
    # `read` e' la lettura protetta del file.
    for key in ("jenny-mascot-scale", "jenny-mascot-size", "jenny-mascotte-size",
                "jenny-mascot-hidden"):
        assert f"read('{key}')" in source, key
    assert "visible" not in source, "il primo fotogramma legge la visibilita' di prima"
    boot = re.search(r"var rescaledSizes = (\{[^}]*\})", source)
    mod = re.search(r"export const RESCALED_SIZES = (\{[^}]*\})", module)
    assert boot and mod
    assert boot.group(1) == mod.group(1)


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
