"""Il fiore della riga di lavoro: si muove, ma sempre nel piano.

La richiesta era esplicita: nessun movimento che sembri profondita'. Due cose
lo fanno sembrare, e sono le due che qui si misurano (v. `home-flower.js`):
un petalo schiacciato su un asse, che e' un ribaltamento, e grandezza o spinta
sfasate da un petalo all'altro, che fanno leggere il fiore come un disco
inclinato. La seconda e' la piu' facile da reintrodurre ritoccando una posa —
basta un `- i * ...` nel posto sbagliato — e a occhio non si vede nel codice.

**Perche' in node sul file vero.** Le pose sono funzioni: l'unico modo onesto
di sapere che cosa fanno e' chiamarle, a tanti istanti, e confrontare i petali.

Quel che non prova: che il movimento sia bello. Quello lo dice il telefono.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node


# Un DOM minimo: il fiore crea nodi SVG e scrive attributi, nient'altro.
_DOM = r"""
const created = [];
globalThis.document = {
  createElementNS(ns, tag) {
    const el = {
      tag, attrs: {}, children: [],
      setAttribute(k, v) { this.attrs[k] = String(v); },
      appendChild(c) { this.children.push(c); return c; },
    };
    created.push(el);
    return el;
  },
};
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copy(ASSETS / "home-flower.js", root / "home-flower.js")
        entry = root / "prova.mjs"
        entry.write_text(
            "import assert from 'node:assert/strict';\n"
            + _DOM
            + "const { POSE, Flower } = await import('./home-flower.js');\n"
            + textwrap.dedent(body),
            encoding="utf-8",
        )
        run_module(entry)


def test_no_pose_misaligns_size_or_push_between_petals() -> None:
    _run(
        """
        // read e write muovono un petalo alla volta: v. il test che segue.
        const inPhase = Object.keys(POSE).filter((k) => k !== 'read' && k !== 'write');
        assert.ok(inPhase.length >= 7, inPhase.join(','));
        for (const name of inPhase) {
          for (let t = 0; t < 12; t += 0.037) {
            const petals = [0, 1, 2, 3, 4].map((i) => POSE[name].petal(t, i));
            for (const p of petals) {
              assert.ok(Math.abs(p.s - petals[0].s) < 1e-9, `${name}: scala sfasata a t=${t}`);
              assert.ok(Math.abs(p.push - petals[0].push) < 1e-9, `${name}: spinta sfasata a t=${t}`);
            }
          }
        }
        """
    )


def test_read_lifts_one_petal_at_a_time() -> None:
    _run(
        """
        for (let t = 0; t < 12; t += 0.037) {
          const petals = [0, 1, 2, 3, 4].map((i) => POSE.read.petal(t, i));
          const mossi = petals.filter((p) => p.push !== 0 || p.wob !== 0);
          assert.ok(mossi.length <= 1, `a t=${t} si muovono ${mossi.length} petali`);
        }
        """
    )


def test_no_petal_gets_squashed_on_one_axis() -> None:
    _run(
        """
        // Nelle pose: nessuna larghezza separata dalla scala.
        for (const [name, pose] of Object.entries(POSE)) {
          for (let t = 0; t < 6; t += 0.1) {
            for (let i = 0; i < 5; i++) {
              assert.equal(pose.petal(t, i).sx, undefined, `${name} ha una larghezza`);
            }
          }
        }
        // Nel disegno: ogni scale() scritto sui nodi ha un argomento solo.
        const f = new Flower(document.createElementNS('', 'svg'), { reducedMotion: true });
        for (const name of Object.keys(POSE)) f.setMode(name);
        const transforms = created.map((n) => n.attrs.transform).filter(Boolean);
        assert.ok(transforms.length > 5);
        for (const tr of transforms) {
          for (const m of tr.matchAll(/scale\\(([^)]*)\\)/g)) {
            assert.equal(m[1].trim().split(/[\\s,]+/).length, 1, tr);
          }
          assert.doesNotMatch(tr, /skew|matrix/, tr);
        }
        """
    )


def test_reduced_motion_starts_no_animations() -> None:
    # Senza requestAnimationFrame nel finto, un ciclo avviato qui esploderebbe.
    _run(
        """
        const f = new Flower(document.createElementNS('', 'svg'), { reducedMotion: true });
        f.setMode('search');
        f.start();
        assert.equal(f._raf, null);
        assert.equal(f.mode, 'search');
        """
    )


def test_every_family_in_the_row_has_a_pose() -> None:
    """Una famiglia nuova in `FAMILY_BY_TOOL` senza posa cadrebbe su `busy` in
    silenzio: il fiore direbbe «mi do da fare» mentre la parola dice altro."""
    source = (ASSETS / "home-activity.js").read_text(encoding="utf-8")
    table = source.split("const FAMILY_BY_TOOL = {", 1)[1].split("};", 1)[0]
    families = set(re.findall(r":\s*'([a-z]+)'", table)) | {"think", "busy"}
    pose = set(
        re.findall(
            r"^  ([a-z]+): \{$",
            (ASSETS / "home-flower.js").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert families <= pose, families - pose
