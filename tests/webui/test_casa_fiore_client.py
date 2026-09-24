"""Il fiore della riga di lavoro: si muove, ma sempre nel piano.

La richiesta era esplicita: nessun movimento che sembri profondita'. Due cose
lo fanno sembrare, e sono le due che qui si misurano (v. `casa-fiore.js`):
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
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


# Un DOM minimo: il fiore crea nodi SVG e scrive attributi, nient'altro.
_DOM = r"""
const creati = [];
globalThis.document = {
  createElementNS(ns, tag) {
    const el = {
      tag, attrs: {}, children: [],
      setAttribute(k, v) { this.attrs[k] = String(v); },
      appendChild(c) { this.children.push(c); return c; },
    };
    creati.push(el);
    return el;
  },
};
"""


def _run(corpo: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        shutil.copy(ASSETS / "casa-fiore.js", radice / "casa-fiore.js")
        entry = radice / "prova.mjs"
        entry.write_text(
            "import assert from 'node:assert/strict';\n"
            + _DOM
            + "const { POSE, Fiore } = await import('./casa-fiore.js');\n"
            + textwrap.dedent(corpo),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [str(_NODE), str(entry)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout


def test_nessuna_posa_sfasa_grandezza_o_spinta_fra_i_petali() -> None:
    _run(
        """
        // read e write muovono un petalo alla volta: v. il test che segue.
        const inFase = Object.keys(POSE).filter((k) => k !== 'read' && k !== 'write');
        assert.ok(inFase.length >= 7, inFase.join(','));
        for (const nome of inFase) {
          for (let t = 0; t < 12; t += 0.037) {
            const petali = [0, 1, 2, 3, 4].map((i) => POSE[nome].petal(t, i));
            for (const p of petali) {
              assert.ok(Math.abs(p.s - petali[0].s) < 1e-9, `${nome}: scala sfasata a t=${t}`);
              assert.ok(Math.abs(p.push - petali[0].push) < 1e-9, `${nome}: spinta sfasata a t=${t}`);
            }
          }
        }
        """
    )


def test_read_solleva_un_petalo_alla_volta() -> None:
    _run(
        """
        for (let t = 0; t < 12; t += 0.037) {
          const petali = [0, 1, 2, 3, 4].map((i) => POSE.read.petal(t, i));
          const mossi = petali.filter((p) => p.push !== 0 || p.wob !== 0);
          assert.ok(mossi.length <= 1, `a t=${t} si muovono ${mossi.length} petali`);
        }
        """
    )


def test_nessun_petalo_si_schiaccia_su_un_asse() -> None:
    _run(
        """
        // Nelle pose: nessuna larghezza separata dalla scala.
        for (const [nome, posa] of Object.entries(POSE)) {
          for (let t = 0; t < 6; t += 0.1) {
            for (let i = 0; i < 5; i++) {
              assert.equal(posa.petal(t, i).sx, undefined, `${nome} ha una larghezza`);
            }
          }
        }
        // Nel disegno: ogni scale() scritto sui nodi ha un argomento solo.
        const f = new Fiore(document.createElementNS('', 'svg'), { reducedMotion: true });
        for (const nome of Object.keys(POSE)) f.setMode(nome);
        const trasformazioni = creati.map((n) => n.attrs.transform).filter(Boolean);
        assert.ok(trasformazioni.length > 5);
        for (const tr of trasformazioni) {
          for (const m of tr.matchAll(/scale\\(([^)]*)\\)/g)) {
            assert.equal(m[1].trim().split(/[\\s,]+/).length, 1, tr);
          }
          assert.doesNotMatch(tr, /skew|matrix/, tr);
        }
        """
    )


def test_movimento_ridotto_non_avvia_animazioni() -> None:
    # Senza requestAnimationFrame nel finto, un ciclo avviato qui esploderebbe.
    _run(
        """
        const f = new Fiore(document.createElementNS('', 'svg'), { reducedMotion: true });
        f.setMode('search');
        f.start();
        assert.equal(f._raf, null);
        assert.equal(f.mode, 'search');
        """
    )


def test_ogni_famiglia_della_riga_ha_una_posa() -> None:
    """Una famiglia nuova in `FAMILY_BY_TOOL` senza posa cadrebbe su `busy` in
    silenzio: il fiore direbbe «mi do da fare» mentre la parola dice altro."""
    sorgente = (ASSETS / "casa-activity.js").read_text(encoding="utf-8")
    tabella = sorgente.split("const FAMILY_BY_TOOL = {", 1)[1].split("};", 1)[0]
    famiglie = set(re.findall(r":\s*'([a-z]+)'", tabella)) | {"think", "busy"}
    pose = set(
        re.findall(
            r"^  ([a-z]+): \{$",
            (ASSETS / "casa-fiore.js").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert famiglie <= pose, famiglie - pose
