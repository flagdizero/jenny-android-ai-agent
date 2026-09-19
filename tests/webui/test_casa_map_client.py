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


def _private(source: str, name: str) -> str:
    """Una funzione di modulo non esportata: `placeLabels` si appoggia a due."""
    m = re.search(rf"(?ms)^function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0)


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?m)^const {re.escape(name)} = .+?;$", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}", source, re.S
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


_APPLICA = """
/* Una selezione di D3 ridotta a cio' che `_placeLabels` usa: `each` con
   `this` sul nodo del testo, e `attr` con una funzione per dato. */
function selezione(dati, larghezze) {
  const scritti = {};
  const sel = {
    scritti,
    each(fn) {
      for (const d of dati) fn.call({ getComputedTextLength: () => larghezze[d.id] }, d);
    },
    attr(nome, f) {
      scritti[nome] = dati.map((d) => [d.id, typeof f === 'function' ? f(d) : f]);
      return sel;
    },
  };
  return sel;
}

class Mappa {
  __PLACE__
}
"""


def _run(script: str) -> None:
    src = MAP_JS.read_text(encoding="utf-8")
    harness = "import assert from 'node:assert/strict';\n" + "\n".join(
        [
            _const(src, n)
            for n in ("MAX_LABELS", "LABEL_CHARS", "LABEL_ALL_UNDER",
                      "LABEL_HEIGHT", "LABEL_GAP")
        ]
        + [_private(src, "overlap")]
        + [
            _function(src, n)
            for n in ("radiusOf", "toSimulation", "shortLabel", "labelledNodes",
                      "labelOffsets", "labelBox", "placeLabels")
        ]
    )
    harness += _APPLICA.replace("__PLACE__", _member(src, "_placeLabels"))
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


# ── Dove va ogni nome ───────────────────────────────────────────────────────


def test_two_names_on_top_of_each_other_move_apart() -> None:
    """Il difetto visto sul Titan: due etichette di pagine vicine finivano una
    sopra l'altra. Il secondo nome prende il posto di riserva."""
    _run("""
      const a = { id: 'a', x: 100, y: 100, w: 80, r: 8, priority: 5 };
      const b = { id: 'b', x: 104, y: 104, w: 80, r: 8, priority: 3 };
      const dove = placeLabels([a, b]);
      assert.equal(dove.size, 2, 'un nome e\\u2019 sparito quando bastava spostarlo');
      assert.notEqual(dove.get('a'), dove.get('b'), 'sono ancora nello stesso posto');
      assert.ok(dove.get('a') > 0, 'il piu\\u2019 collegato ha perso il posto buono');
      assert.ok(dove.get('b') < 0, 'il secondo non e\\u2019 andato sopra');
    """)


def test_the_most_connected_page_keeps_the_good_spot() -> None:
    """Chi arriva prima sceglie, e arriva prima la pagina piu' collegata: e' la
    stessa gerarchia con cui si decide chi un nome ce l'ha."""
    _run("""
      const debole = { id: 'debole', x: 100, y: 100, w: 90, r: 8, priority: 1 };
      const hub = { id: 'hub', x: 100, y: 100, w: 90, r: 8, priority: 9 };
      const dove = placeLabels([debole, hub]);
      assert.equal(dove.get('hub'), labelOffsets(8)[0], 'il nodo hub non sta sotto');
    """)


def test_a_name_with_nowhere_to_go_disappears() -> None:
    """Due parole sovrapposte non sono due informazioni, sono zero. Il pallino
    resta, si tocca lo stesso, e il nome sta nell'elenco accanto."""
    _run("""
      const items = [0, 1, 2].map((i) => ({
        id: 'n' + i, x: 100, y: 100, w: 120, r: 8, priority: 3 - i,
      }));
      const dove = placeLabels(items);
      assert.equal(dove.size, 2, 'tre nomi nello stesso punto e ne restano ' + dove.size);
      assert.ok(dove.has('n0') && dove.has('n1'));
      assert.ok(!dove.has('n2'), 'il terzo si e\\u2019 accavallato');
    """)


def test_far_apart_names_all_keep_the_preferred_spot() -> None:
    """Il caso normale: niente si tocca, e niente si muove."""
    _run("""
      const items = [0, 1, 2, 3].map((i) => ({
        id: 'n' + i, x: 60 + i * 200, y: 60 + i * 120, w: 70, r: 6, priority: i,
      }));
      const dove = placeLabels(items);
      assert.equal(dove.size, 4);
      for (const off of dove.values()) assert.equal(off, labelOffsets(6)[0]);
    """)


def test_the_same_map_places_the_same_names_every_time() -> None:
    """A parita' di collegamenti decide il nome: senza, l'ordine di arrivo
    cambierebbe chi vince il posto, e la mappa si ridisegnerebbe diversa."""
    _run("""
      const items = [0, 1, 2].map((i) => ({
        id: 'pagina' + i, x: 100, y: 100, w: 120, r: 8, priority: 4,
      }));
      const a = [...placeLabels(items).keys()].sort();
      const b = [...placeLabels([...items].reverse()).keys()].sort();
      assert.deepEqual(a, b, 'chi tiene il nome dipende dall\\u2019ordine di arrivo');
    """)


def test_names_that_merely_brush_are_treated_as_touching() -> None:
    """Un pixel d'aria fra due parole si legge male quanto zero.

    I due riquadri qui distano **1**, sotto `LABEL_GAP`: senza quell'aria nel
    conto si direbbe che non si toccano, e sullo schermo si leggerebbero
    attaccati. E' la mutazione che passava verde perche' il banco provava solo
    nomi lontanissimi o esattamente sovrapposti.
    """
    _run("""
      const a = { id: 'a', x: 100, y: 100, w: 40, r: 6, priority: 9 };
      // A destra di `a`, un pixel di distanza.
      const destra = { id: 'b', x: 141, y: 100, w: 40, r: 6, priority: 1 };
      // E a sinistra, uguale: le due condizioni del confronto sono due righe
      // diverse, e una sola delle due morde a seconda di chi sta dove.
      const sinistra = { id: 'b', x: 59, y: 100, w: 40, r: 6, priority: 1 };
      for (const vicino of [destra, sinistra]) {
        const dove = placeLabels([a, vicino]);
        assert.equal(dove.get('a'), labelOffsets(6)[0]);
        assert.equal(dove.get('b'), labelOffsets(6)[1],
                     'un pixel d\\u2019aria e\\u2019 bastato a farli passare per lontani');
      }
    """)


def test_the_box_starts_above_the_baseline() -> None:
    """`y` di un testo SVG e' la **linea di base**, non il bordo alto.

    Oggi i riquadri si confrontano solo fra loro, quindi una traslazione comune
    non cambierebbe nessuna decisione: e' proprio per questo che la geometria
    va misurata qui invece che dedotta da un collocamento. Un riquadro che dice
    il falso su dove sta il testo e' una trappola pronta per il primo che gli
    confronti accanto qualcos'altro.
    """
    _run("""
      const item = { id: 'a', x: 100, y: 200, w: 60, r: 6 };
      const box = labelBox(item, 20);
      assert.equal(box.w, 60);
      assert.equal(box.h, LABEL_HEIGHT);
      assert.ok(box.y < 200 + 20, 'il riquadro parte sotto la linea di base');
      assert.ok(box.y + box.h > 200 + 20, 'la linea di base e\u2019 fuori dal riquadro');
      assert.equal(box.x, 100 - 30, 'il riquadro non e\u2019 centrato sull\u2019ancora');
    """)


def test_a_name_is_centred_on_its_dot() -> None:
    """Il testo e' `text-anchor: middle`, quindi il riquadro sta **attorno**
    alla x del pallino, non a destra di essa.

    Con larghezze diverse la differenza si vede: ancorati a sinistra questi due
    si sovrapporrebbero, centrati no. E due nomi che si scansano quando non
    serve sono due nomi spostati per niente.
    """
    _run("""
      const largo = { id: 'largo', x: 100, y: 100, w: 100, r: 6, priority: 9 };
      const stretto = { id: 'stretto', x: 175, y: 100, w: 20, r: 6, priority: 1 };
      const dove = placeLabels([largo, stretto]);
      assert.equal(dove.get('largo'), labelOffsets(6)[0]);
      assert.equal(dove.get('stretto'), labelOffsets(6)[0],
                   'si e\\u2019 spostato senza che ce ne fosse bisogno');
    """)


# ── E cosa ne fa il disegno ─────────────────────────────────────────────────


def test_the_drawing_hides_the_names_that_did_not_fit() -> None:
    """La meta' visibile della decisione. Senza, `placeLabels` puo' scegliere
    benissimo e a schermo restano tutti accavallati lo stesso.

    Tre pagine in fila con nomi lunghi: la prima sta sotto, la seconda trova il
    posto sopra, per la terza non ce n'e' e il nome sparisce — il pallino no, e
    si tocca lo stesso.
    """
    _run("""
      const nodi = [0, 1, 2].map((i) => ({
        id: 'n' + i, x: 100 + i * 40, y: 100, degree: 2,
      }));
      const sel = selezione(nodi, { n0: 120, n1: 120, n2: 120 });
      Mappa.prototype._placeLabels.call({}, sel);
      const display = Object.fromEntries(sel.scritti.display);
      const dy = Object.fromEntries(sel.scritti.dy);
      assert.equal(display.n0, null, 'il primo nome e\\u2019 stato nascosto');
      assert.equal(display.n1, null, 'il secondo non ha trovato il posto di sopra');
      assert.ok(dy.n0 > 0 && dy.n1 < 0, 'i due non si sono divisi: ' + JSON.stringify(dy));
      assert.equal(display.n2, 'none', 'il terzo si e\\u2019 accavallato invece di sparire');
    """)


def test_the_drawing_writes_the_offset_that_was_chosen() -> None:
    _run("""
      const nodi = [
        { id: 'a', x: 100, y: 100, degree: 5 },
        { id: 'b', x: 170, y: 100, degree: 1 },
      ];
      const sel = selezione(nodi, { a: 80, b: 80 });
      Mappa.prototype._placeLabels.call({}, sel);
      const dy = Object.fromEntries(sel.scritti.dy);
      assert.ok(dy.a > 0 && dy.b < 0, 'gli scarti scelti non arrivano al disegno: ' +
                JSON.stringify(dy));
    """)


def test_a_web_view_without_text_measurement_does_not_crash() -> None:
    """`getComputedTextLength` e' SVG e c'e' ovunque, ma un ripiego a zero
    costa una riga e trasforma «la mappa e' vuota» in «i nomi sono tutti
    sotto»."""
    _run("""
      const nodi = [{ id: 'a', x: 50, y: 50, degree: 2 }];
      const sel = {
        scritti: {},
        each(fn) { fn.call({}, nodi[0]); },
        attr(n, f) { sel.scritti[n] = f(nodi[0]); return sel; },
      };
      Mappa.prototype._placeLabels.call({}, sel);
      assert.equal(sel.scritti.display, null, 'senza misura il nome sparisce');
    """)


def test_a_name_may_graze_a_dot_and_that_is_on_purpose() -> None:
    """I pallini non sono ostacoli, ed e' una scelta misurata.

    Un nome sta dieci pixel sotto il bordo del suo cerchio — dentro l'aria che
    separa due riquadri — quindi litigava perfino col **proprio** pallino, e
    sparivano tutte. Escluso il proprio, restava che un nome largo fino a 90 px
    su nodi distanti 40 tocca sempre il cerchio del vicino: tre pagine in fila
    ne conservavano una su tre. Un nome che sfiora un pallino si legge; un nome
    che non c'e' no.
    """
    _run("""
      // Tre pagine vicine, nomi larghi: con i pallini fra gli ostacoli ne
      // sopravviveva uno solo. Qui i primi due si dividono i posti e il terzo
      // sparisce perche' i *nomi* non ci stanno, non per via dei cerchi.
      const nodi = [0, 1, 2].map((i) => ({
        id: 'n' + i, x: 100 + i * 40, y: 100, degree: 2,
      }));
      const sel = selezione(nodi, { n0: 120, n1: 120, n2: 120 });
      Mappa.prototype._placeLabels.call({}, sel);
      const display = Object.fromEntries(sel.scritti.display);
      const dy = Object.fromEntries(sel.scritti.dy);
      assert.equal(display.n0, null);
      assert.equal(display.n1, null, 'il secondo nome non ha trovato il posto di sopra');
      assert.ok(dy.n0 > 0 && dy.n1 < 0, 'i due non si sono divisi: ' + JSON.stringify(dy));
      assert.equal(display.n2, 'none');
    """)
