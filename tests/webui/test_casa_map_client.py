"""La mappa: chi porta il nome scritto, e quanto lungo.

Due funzioni pure, e valgono il banco perche' la risposta l'ha data il
telefono e non il gusto. Su un quaderno vero da 31 pagine, scrivendo tutti i
nomi e interi, le etichette si sovrappongono fino a diventare una macchia — e
i titoli di una wiki sono frasi, non parole.

Quali nomi restano e' l'unica domanda a cui una mappa risponde meglio di un
elenco: **dove si annoda il quaderno**. Quindi i nodi piu' collegati. E a
parita' di collegamenti decide il nome, perche' l'insieme deve essere lo
stesso a ogni apertura: una mappa che cambia le etichette fra due sguardi
sembra rotta anche quando disegna gli stessi nodi.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MAP_JS = ASSETS / "casa-map.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0).replace("export function", "function")


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?m)^const {re.escape(name)} = .+?;$", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


def _run(script: str) -> None:
    src = MAP_JS.read_text(encoding="utf-8")
    harness = "import assert from 'node:assert/strict';\n" + "\n".join(
        [_const(src, n) for n in ("MAX_LABELS", "LABEL_CHARS", "LABEL_ALL_UNDER")]
        + [_function(src, n) for n in ("radiusOf", "toSimulation", "shortLabel", "labelledNodes")]
    )
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", harness + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_a_small_notebook_shows_every_name() -> None:
    """Dieci su undici sarebbe una scelta che non si capisce, e undici nomi ci
    stanno."""
    _run("""
      const nodi = Array.from({ length: 11 }, (_, i) => ({ id: 'n' + i, label: 'p' + i, degree: 0 }));
      assert.equal(labelledNodes(nodi).size, 11);
    """)


def test_a_big_notebook_names_only_where_it_knots() -> None:
    _run("""
      const nodi = Array.from({ length: 31 }, (_, i) => ({
        id: 'n' + i, label: 'p' + String(i).padStart(2, '0'), degree: i,
      }));
      const con = labelledNodes(nodi);
      assert.equal(con.size, MAX_LABELS);
      assert.ok(con.has('n30'), 'il nodo piu\\u2019 collegato non ha un nome');
      assert.ok(!con.has('n0'), 'anche una foglia porta il nome');
    """)


def test_the_same_notebook_gets_the_same_names_every_time() -> None:
    """A pari collegamenti decide il nome. Senza, l'ordine di `sort` su nodi
    equivalenti dipende dall'implementazione e l'insieme puo' cambiare fra due
    aperture: una mappa che si ridisegna diversa sembra rotta."""
    _run("""
      const pari = Array.from({ length: 20 }, (_, i) => ({
        id: 'n' + i, label: 'pagina ' + String(i).padStart(2, '0'), degree: 3,
      }));
      const a = [...labelledNodes(pari)].sort();
      const b = [...labelledNodes([...pari].reverse())].sort();
      assert.deepEqual(a, b, 'le etichette cambiano con l\\u2019ordine di arrivo');
    """)


def test_a_title_that_is_a_sentence_gets_cut() -> None:
    _run("""
      const lungo = 'Coltivazione-Monstera-Roma \\u2014 Sostegno, fertilizzazione, crescita';
      const corto = shortLabel(lungo);
      assert.ok(corto.length <= LABEL_CHARS, corto);
      assert.ok(corto.endsWith('\\u2026'), corto);
      assert.equal(shortLabel('Acero'), 'Acero', 'un nome corto non si tocca');
      assert.equal(shortLabel(''), '');
    """)


def test_the_radius_says_how_connected_a_page_is() -> None:
    """E' l'unico numero che la casa mostra, e lo mostra senza scriverlo."""
    _run("""
      assert.ok(radiusOf(0) < radiusOf(3), 'un nodo collegato non e\\u2019 piu\\u2019 grosso');
      assert.equal(radiusOf(0), radiusOf(undefined));
      assert.equal(radiusOf(1000), radiusOf(50), 'il raggio non ha un tetto');
    """)


def test_an_edge_to_a_page_that_is_not_there_is_dropped() -> None:
    """`summaries/` non entra nel grafo, ma una pagina puo' linkarlo: un arco
    verso un nodo che non esiste farebbe cadere la simulazione di D3."""
    _run("""
      const { nodes, links } = toSimulation({
        nodes: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }],
        edges: [{ source: 'a', target: 'b' }, { source: 'a', target: 'fantasma' }],
      });
      assert.equal(nodes.length, 2);
      assert.equal(links.length, 1);
    """)


def test_every_node_keeps_the_index_the_server_gave_it() -> None:
    """La terza resa della stessa risposta: la maschera della ricerca si legge
    con quel numero e non con la posizione qui dentro."""
    _run("""
      const { nodes } = toSimulation({
        nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], edges: [],
      });
      assert.deepEqual(nodes.map((n) => n.index), [0, 1, 2]);
    """)
