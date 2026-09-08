"""Una sola variante dell'arte: il bianco/nero è uscito, e non deve rientrare.

Fino all'08/09/2026 ogni posa esisteva in due copie e ``poseUrl`` rimappava il
suffisso ``-color`` su una preferenza dell'utente. La preferenza è stata
ritirata (v. ``.agent/mascot-faces-plan.md``, F9): qui si tiene fermo che
nessun path lo cerchi più, che il modulo delle preferenze non lo esporti più, e
che un telefono che *aveva* scelto il B/N non se lo porti dietro — la chiave
morta in ``localStorage`` si ripulisce al caricamento invece di restare a
sporcare lo storage per sempre.

Il modulo si importa **davvero** in node, con un ``localStorage`` finto: è
l'unico modo di provare un effetto collaterale che sta a livello di modulo e
non dentro una funzione.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jenny.utils.android_assets import _UI_MANIFEST

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MASCOT_JS = ASSETS / "shared" / "mascot.js"
DEAD_KEY = "jenny-mascotte-color"

_NODE = shutil.which("node")
node = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _run(source: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_no_asset_path_asks_for_a_color_twin() -> None:
    """Nessun `-color.webp` nei sorgenti della WebUI né nel manifest."""
    offenders = []
    for path in sorted(ASSETS.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        if "-color.webp" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(path.name)
    assert not offenders, f"path della variante colore ancora vivi in: {offenders}"
    assert not [e for e in _UI_MANIFEST if e.endswith("-color.webp")]


def test_every_pose_in_the_manifest_exists_exactly_once() -> None:
    """Le pose "cotte" sono 10, una per file: se ne ricompare una a coppie il conto lo dice.

    Dieci e non quindici: il pensa e i quattro frame del parlato sono passati
    all'arte a due livelli e non si esportano più. I sorgenti a due livelli
    (``jenny-body-*`` / ``jenny-face-*``) sono un'altra famiglia e li conta
    ``test_mascot_layer_sources.py``.
    """
    poses = sorted(
        e for e in _UI_MANIFEST
        if e.startswith("assets/jenny-") and e.endswith(".webp")
        and not e.startswith(("assets/jenny-body-", "assets/jenny-face-"))
    )
    assert len(poses) == 10, poses
    for entry in _UI_MANIFEST:
        if entry.startswith("assets/jenny-") and entry.endswith(".webp"):
            assert (ASSETS.parent / entry).is_file(), f"{entry} è nel manifest ma non su disco"


@node
def test_the_preferences_module_no_longer_exports_the_variant() -> None:
    _run(f"""
import assert from 'node:assert/strict';
globalThis.localStorage = {{
  _s: new Map(),
  getItem(k) {{ return this._s.has(k) ? this._s.get(k) : null; }},
  setItem(k, v) {{ this._s.set(k, String(v)); }},
  removeItem(k) {{ this._s.delete(k); }},
}};
const mod = await import({json.dumps(MASCOT_JS.as_uri())});
for (const gone of ['poseUrl', 'mascotColor', 'setMascotColor']) {{
  assert.ok(!(gone in mod), `{{gone}} è ancora esportato`.replace('{{gone}}', gone));
}}
// Quello che resta, resta.
for (const kept of ['mascotVisible', 'mascotSide', 'mascotSize', 'applyMascotSize']) {{
  assert.ok(kept in mod, kept + ' non è più esportato');
}}
""")


@node
def test_a_phone_that_had_chosen_black_and_white_gets_it_cleaned_up() -> None:
    """La chiave morta si cancella al caricamento, e non fa cambiare nient'altro."""
    _run(f"""
import assert from 'node:assert/strict';
const store = new Map([[{json.dumps(DEAD_KEY)}, '0'], ['jenny-mascotte-size', 'lg']]);
globalThis.localStorage = {{
  getItem(k) {{ return store.has(k) ? store.get(k) : null; }},
  setItem(k, v) {{ store.set(k, String(v)); }},
  removeItem(k) {{ store.delete(k); }},
}};
const mod = await import({json.dumps(MASCOT_JS.as_uri())});
assert.equal(store.has({json.dumps(DEAD_KEY)}), false, 'la chiave morta è ancora lì');
// Le altre preferenze non le tocca.
assert.equal(mod.mascotSize(), 'lg');
assert.equal(mod.mascotVisible(), true);
""")


@node
def test_the_import_survives_a_storage_that_throws() -> None:
    """Finestra privata, storage bloccato: la pulizia è dentro un try, l'import regge.

    Prova solo questo. Che *leggere* una preferenza sopravviva a un getItem che
    lancia non è vero nemmeno oggi (``mascotVisible`` e ``mascotSize`` non sono
    protetti) ed è un difetto di prima, non di questo giro.
    """
    _run(f"""
import assert from 'node:assert/strict';
globalThis.localStorage = {{
  getItem() {{ throw new Error('storage bloccato'); }},
  setItem() {{ throw new Error('storage bloccato'); }},
  removeItem() {{ throw new Error('storage bloccato'); }},
}};
const mod = await import({json.dumps(MASCOT_JS.as_uri())});
assert.ok(typeof mod.mascotSize === 'function');
""")
