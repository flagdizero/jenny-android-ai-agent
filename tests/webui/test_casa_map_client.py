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
            for n in ("MAX_LABELS", "LABEL_CHARS", "LABEL_HEIGHT", "LABEL_GAP")
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
    """Undici pagine, undici nomi: il tetto non tocca un quaderno piccolo.

    C'era una scorciatoia apposta (`LABEL_ALL_UNDER`), perche' col tetto a 10
    «dieci su undici» sarebbe stata una scelta incomprensibile. Dal 21/09/2026 il
    tetto e' 40 e la scorciatoia non cambia piu' nessuna risposta: se n'e'
    andata, e questa prova resta a tenere il risultato — che e' la cosa che
    conta, non il meccanismo che lo produce."""
    _run("""
      const nodi = Array.from({ length: 11 }, (_, i) => ({ id: 'n' + i, label: 'p' + i, degree: 0 }));
      assert.equal(labelledNodes(nodi).size, 11);
    """)


def test_a_big_notebook_names_only_where_it_knots() -> None:
    """Il tetto morde solo su un quaderno enorme, ed e' li' che deve mordere:
    oltre un certo numero anche i nomi che «ci stanno» sono un muro di testo, e
    lo spazio da solo non saprebbe dire di no."""
    _run("""
      const nodi = Array.from({ length: 60 }, (_, i) => ({
        id: 'n' + i, label: 'p' + String(i).padStart(2, '0'), degree: i,
      }));
      const con = labelledNodes(nodi);
      assert.equal(con.size, MAX_LABELS);
      assert.ok(con.has('n59'), 'il nodo piu\\u2019 collegato non ha un nome');
      assert.ok(!con.has('n0'), 'anche una foglia porta il nome');
    """)


def test_the_cap_is_a_safety_net_and_not_a_design_rule() -> None:
    """Il numero si legge da fuori, o il banco sopra non saprebbe distinguere un
    tetto da un conteggio qualsiasi — e' lo stesso difetto trovato sulla scheda
    dei file il 21/09/2026.

    Il tetto e' nato a 10 su una misura vera (31 pagine, titoli che sono frasi,
    566 px) e per due giorni e' stato la regola di disegno. Lo era a torto: su
    una mappa da 21 pagine lasciava undici pallini muti con lo spazio attorno
    visibile, perche' escludeva prima che qualcuno misurasse. A decidere e'
    `placeLabels`, che ordina gia' per collegamenti; il tetto serve solo a
    fermare il muro di testo su un quaderno enorme.

    Quindi: alto abbastanza da non decidere su un quaderno normale, basso
    abbastanza da restare una rete."""
    src = MAP_JS.read_text(encoding="utf-8")
    m = re.search(r"^const MAX_LABELS = (\d+);$", src, re.M)
    assert m, "il tetto non si trova"
    n = int(m.group(1))
    assert 25 <= n <= 60, (
        f"tetto a {n}: sotto la venticinquina torna a decidere lui al posto dello "
        f"spazio, sopra la sessantina non ferma piu' niente"
    )


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


# ── L'inquadratura non passa sopra al dito ──────────────────────────────────

_CAMERA = """
/* D3 ridotto a quel che queste due decisioni toccano: una selezione che
   registra le chiamate, e `zoomIdentity` che restituisce una trasformazione
   riconoscibile invece di una matrice vera — qui interessa **se** e **quante
   volte** viene applicata, non cosa valga. */
const d3 = {
  zoomIdentity: {
    translate(x, y) { return { ...this, tx: x, ty: y }; },
    scale(k) { return { ...this, k }; },
  },
  /* `d3.drag()` ridotto a un registratore: tiene la soglia del click e i
     gestori, cosi' il banco puo' recitare un gesto chiamandoli. */
  drag() {
    const gestori = {};
    const api = {
      soglia: null,
      gestori,
      clickDistance(n) { api.soglia = n; return api; },
      on(ev, fn) { gestori[ev] = fn; return api; },
    };
    return api;
  },
};

/* Il disco, ridotto a una variabile. `letto` e' cio' che il file contiene,
   `scritture` cio' che ci finisce; `rotto` fa fallire la lettura, `mkdir` conta
   le cartelle create. */
let letto = null, rotto = false;
const scritture = [], cartelle = [];
const api = {
  readWorkspaceFile() {
    if (rotto) return Promise.reject(new Error('404'));
    return Promise.resolve({ content: letto });
  },
  createWorkspaceFolder(p) { cartelle.push(p); return Promise.resolve(); },
};
const rpc = {
  writeWorkspaceFile(path, content) { scritture.push([path, content]); return Promise.resolve(); },
};

/* La fisica ridotta a un diario: interessa **quando** si riaccende e quando si
   spegne, non dove finiscono i nodi. */
function fisicaFinta() {
  const diario = [];
  const sim = {
    diario,
    alphaTarget(v) { diario.push(['alphaTarget', v]); return sim; },
    restart() { diario.push(['restart']); return sim; },
  };
  return sim;
}

function svgFinto() {
  const applicate = [];
  return { applicate, call(_che, t) { applicate.push(t); } };
}

const zoomFinto = { transform: 'TRANSFORM' };

class Mappa {
  constructor(quaderno = 'quaderno') {
    this._presaInMano = false;
    this._sim = fisicaFinta();
    this._quaderno = quaderno;
    this._spilli = null;
  }
__METODI__
}

/* Tre nodi in basso a destra: la nuvola che il difetto aveva reso famosa. */
const NODI = [{ x: 260, y: 300 }, { x: 300, y: 320 }, { x: 280, y: 280 }];
"""


def _run_gesti(script: str) -> None:
    src = MAP_JS.read_text(encoding="utf-8")
    harness = (
        "import assert from 'node:assert/strict';\n"
        + _const(src, "FIT_PADDING")
        + "\n"
        + _const(src, "FIT_MAX_SCALE")
        + "\n"
        + _const(src, "SOGLIA_TOCCO")
        + "\n"
        + _const(src, "FILE_SPILLI")
        + "\n"
        + _CAMERA.replace(
            "__METODI__",
            "\n".join(
                "  " + _member(src, n)
                for n in ("_onZoom", "_trascina", "_inquadra",
                          "_leggiSpilli", "_salvaSpilli")
            ),
        )
    )
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", harness + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_at_rest_the_cloud_gets_framed() -> None:
    """A fisica ferma la nuvola sta dove l'hanno lasciata le forze, che non e'
    il centro: sei nodi in basso a destra e mezza stanza vuota. Senza questa
    inquadratura la mappa si apre guardando il vuoto."""
    _run_gesti("""
const m = new Mappa();
const svg = svgFinto();
m._inquadra(svg, zoomFinto, NODI, 590, 400);
assert.equal(svg.applicate.length, 1, 'la nuvola non viene inquadrata');
assert.ok(svg.applicate[0].k > 0, "l'inquadratura non porta una scala");
""")


def test_the_frame_never_writes_over_your_finger() -> None:
    """**Il difetto del 21/09/2026**, chiesto cosi': «né drag né pan, niente».

    L'inquadratura girava senza condizioni dentro `sim.on('end')`, e la fisica
    si ferma cinque secondi circa dopo l'apertura della linguetta. Chi apriva la
    mappa e spostava subito la vedeva tornare indietro da sola: lo spostamento
    funzionava, e veniva riscritto un istante dopo.

    Vale anche in avanti: un trascinamento di nodi fa ripartire la fisica, quindi
    `end` scatta di nuovo — senza la guardia, ogni pallino trascinato costerebbe
    un salto della vista nel momento in cui si alza il dito.
    """
    _run_gesti("""
const m = new Mappa();
const radice = { attr() {} };

// Il dito: D3 porta `sourceEvent` solo per un gesto vero.
m._onZoom({ sourceEvent: { type: 'touchmove' }, transform: 'MIA' }, radice);
assert.equal(m._presaInMano, true, 'un gesto vero non viene riconosciuto');

const svg = svgFinto();
m._inquadra(svg, zoomFinto, NODI, 590, 400);
assert.deepEqual(svg.applicate, [],
  "l'inquadratura ha riscritto dove stava guardando l'utente");
""")


def test_framing_ourselves_does_not_count_as_your_finger() -> None:
    """L'inquadratura *e'* un evento di zoom: applicandola, D3 richiama lo
    stesso gestore. Se contasse come gesto, la mappa si inquadrerebbe una volta
    e poi si dichiarerebbe "toccata dall'utente" per sempre — e un quaderno
    ridisegnato si aprirebbe di nuovo guardando il vuoto. `sourceEvent` e' null
    proprio per questo."""
    _run_gesti("""
const m = new Mappa();
const radice = { attr() {} };
m._onZoom({ sourceEvent: null, transform: 'NOSTRA' }, radice);
assert.equal(m._presaInMano, false,
  'inquadrarsi da soli viene contato come un gesto dell\\'utente');

const svg = svgFinto();
m._inquadra(svg, zoomFinto, NODI, 590, 400);
assert.equal(svg.applicate.length, 1, 'la nuvola non viene piu' + ' inquadrata');
""")


def test_the_transform_reaches_the_drawing_either_way() -> None:
    """Gesto o no, la trasformazione va applicata al gruppo: la guardia decide
    *chi inquadra*, non se il disegno si muove."""
    _run_gesti("""
const m = new Mappa();
const viste = [];
const radice = { attr: (nome, v) => viste.push([nome, v]) };
m._onZoom({ sourceEvent: { type: 'touchmove' }, transform: 'MIA' }, radice);
m._onZoom({ sourceEvent: null, transform: 'NOSTRA' }, radice);
assert.deepEqual(viste, [['transform', 'MIA'], ['transform', 'NOSTRA']]);
""")


def test_a_redrawn_map_gets_its_frame_back_but_a_revisit_does_not() -> None:
    """Dove si azzera il flag e' la mezza decisione che resta, e si legge dal
    sorgente perche' vive in `_render`, che e' tutto D3.

    In `_render`: un disegno nuovo e' una mappa nuova e merita la sua
    inquadratura. **Non** in `draw`: per una risposta gia' disegnata `draw` esce
    subito (`this._drawn === data`), quindi azzerarlo li' butterebbe via dove
    l'utente stava guardando a ogni ritorno sulla linguetta.
    """
    src = MAP_JS.read_text(encoding="utf-8")
    render = _member(src, "_render")
    draw = _member(src, "draw")
    assert "this._presaInMano = false;" in render, (
        "un quaderno ridisegnato si apre guardando dove guardava il precedente"
    )
    assert "_presaInMano" not in draw, (
        "tornare sulla linguetta butta via l'inquadratura dell'utente"
    )


def test_the_names_are_replaced_at_every_rest() -> None:
    """I nomi dipendono da **dove stanno i nodi**, l'inquadratura da dove guarda
    l'utente: due domande diverse, e solo la seconda ha una guardia.

    Conta perche' i nomi si collocano una volta sola, a fisica ferma
    (`_placeLabels` fa una misura di testo per etichetta, e a ogni frame sul
    Titan non si regge). Ogni quiete successiva e' l'unica occasione di
    rimetterli a posto: un trascinamento di nodi fa ripartire la fisica, e se la
    ricollocazione finisse sotto la guardia dell'inquadratura i nomi resterebbero
    dove li aveva messi la quiete precedente — accavallati, addosso al pallino
    sbagliato.

    Si legge dal sorgente: il gestore di `end` e' una chiusura dentro `_render`,
    che e' tutto D3.
    """
    src = MAP_JS.read_text(encoding="utf-8")
    m = re.search(r"this\._sim\.on\('end', \(\) => \{(.*?)\n    \}\);", src, re.S)
    assert m, "il gestore della quiete non si trova piu'"
    corpo = m.group(1)
    assert "this._placeLabels(" in corpo, (
        "a fisica ferma i nomi non si ricollocano: restano dove stavano prima"
    )
    assert "_presaInMano" not in corpo, (
        "la ricollocazione dei nomi e' finita sotto la guardia dell'inquadratura: "
        "chi ha spostato la mappa col dito non li vedrebbe piu' aggiustare"
    )
    assert corpo.index("this._placeLabels(") < corpo.index("this._inquadra("), (
        "si inquadra prima di sapere dove stanno i nomi"
    )


# ── Un pallino preso resta dove lo metti ────────────────────────────────────


def test_a_dragged_dot_stays_where_you_put_it() -> None:
    """**La decisione dell'utente, il 21/09/2026.** Il grafo dell'officina
    rilasciava `fx`/`fy` all'`end`: il pallino tornava dove lo vuole la fisica, e
    su una nuvola annodata la matassa si richiudeva appena mollata. Qui resta.

    Lo spillo *e'* l'`end` che non rilascia. Se qualcuno lo rimettesse, il
    trascinamento tornerebbe a servire solo a sbirciare — e il pallino ha gia'
    un gesto che lo apre, quindi non sarebbe un motivo per metterci il dito.
    """
    _run_gesti("""
const m = new Mappa();
const d = { id: 'p1', x: 100, y: 100 };
const t = m._trascina([d]);

t.gestori.start({ active: 0 }, d);
assert.deepEqual([d.fx, d.fy], [100, 100], 'il pallino non si inchioda per il gesto');

t.gestori.drag({ x: 260, y: 40 }, d);
assert.deepEqual([d.fx, d.fy], [260, 40], 'il pallino non segue il dito');

t.gestori.end({ active: 0 }, d);
assert.deepEqual([d.fx, d.fy], [260, 40],
  'mollando, il pallino e\\' tornato in mano alla fisica');
""")


def test_dragging_takes_the_map_in_hand() -> None:
    """Un trascinamento riaccende la fisica, quindi la quiete arriva di nuovo, e
    con lei l'inquadratura automatica: **sposterebbe sotto gli occhi il pallino
    appena messo a posto.**

    E' il motivo per cui la guardia dell'inquadratura non parla di
    «spostamento» ma di mani sulla mappa: spostare sceglie da dove guardare,
    trascinare dove sta una pagina, e in entrambi i casi chi ha deciso cosa c'e'
    a schermo e' l'utente.
    """
    _run_gesti("""
const m = new Mappa();
m._trascina([]).gestori.start({ active: 0 }, { x: 10, y: 10 });
assert.equal(m._presaInMano, true, 'il trascinamento non prende la mappa in mano');

const svg = svgFinto();
m._inquadra(svg, zoomFinto, NODI, 590, 400);
assert.deepEqual(svg.applicate, [],
  'la quiete dopo il trascinamento ha reinquadrato la nuvola');
""")


def test_a_small_gesture_is_still_a_tap_on_the_page() -> None:
    """Il pallino porta due gesti e col pollice si pestano. Non e' simmetrico:
    un tocco che non apre e' un colpo a vuoto che si ripete, ma un
    trascinamento che apre **anche** la pagina ti porta nel lettore proprio
    mentre stavi sistemando la mappa.

    Se ne occupa `clickDistance` di D3: sotto la soglia il gesto resta un tocco,
    sopra il click viene soppresso. Senza, ogni trascinamento aprirebbe una
    pagina. Il numero e' l'unica cosa qui che solo un pollice puo' giudicare, e
    ha il suo commento in `SOGLIA_TOCCO`; il banco misura che ci sia e che sia
    quello, non che sia giusto.
    """
    _run_gesti("""
const soglia = new Mappa()._trascina([]).soglia;
assert.equal(soglia, SOGLIA_TOCCO, 'il gesto non distingue un tocco da un trascinamento');
assert.ok(soglia > 0 && soglia <= 16,
  'una soglia fuori scala: sotto lo zero ogni tocco e\\' un trascinamento, ' +
  'sopra la sedicina un trascinamento apre anche la pagina');
""")


def test_the_physics_wakes_for_the_drag_and_goes_back_to_sleep() -> None:
    """Senza riaccenderla, gli altri nodi resterebbero fermi mentre uno si
    muove: i fili si allungherebbero da soli e niente si riassesterebbe. E va
    rispenta, o la nuvola non arriva mai alla quiete — che e' l'unico momento in
    cui i nomi si ricollocano."""
    _run_gesti("""
const m = new Mappa();
const d = { id: 'p1', x: 1, y: 1 };
const t = m._trascina([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.end({ active: 0 }, d);
assert.deepEqual(m._sim.diario,
  [['alphaTarget', 0.3], ['restart'], ['alphaTarget', 0]],
  'la fisica non si riaccende per il gesto, o non si rispegne dopo');
""")


def test_a_second_finger_does_not_restart_the_physics_twice() -> None:
    """`event.active` conta i gesti in corso: e' zero solo per il primo. Un
    secondo dito che scende mentre il primo trascina non deve riaccendere una
    fisica gia' accesa, ne' — alzandosi — spegnerla mentre l'altro sta ancora
    tirando."""
    _run_gesti("""
const m = new Mappa();
const t = m._trascina([]);
t.gestori.start({ active: 1 }, { x: 1, y: 1 });
t.gestori.end({ active: 1 }, { x: 1, y: 1 });
assert.deepEqual(m._sim.diario, [],
  'il secondo dito rimette mano alla fisica del primo');
""")


# ── Gli spilli restano fra un'apertura e l'altra ────────────────────────────


def test_letting_go_writes_the_arrangement() -> None:
    """Chiesto dall'utente: «i nodi devono rimanere salvati». Si scrive alzando
    il dito, che è l'unico momento in cui uno spillo nasce o si sposta."""
    _run_gesti("""
const m = new Mappa('piante');
const d = { id: 'Monstera.md', x: 10, y: 10 };
const t = m._trascina([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.drag({ x: 240.4, y: 91.6 }, d);
t.gestori.end({ active: 0 }, d);
await new Promise((r) => setTimeout(r, 0));

assert.equal(scritture.length, 1, 'alzando il dito non si salva niente');
const [path, testo] = scritture[0];
assert.equal(path, FILE_SPILLI);
assert.deepEqual(JSON.parse(testo), { piante: { 'Monstera.md': [240, 92] } },
  'la posizione salvata non e\\' quella dove il dito ha lasciato il pallino');
""")


def test_only_the_pinned_dots_are_written() -> None:
    """Un pallino mai toccato non ha una posizione da ricordare: è la fisica a
    deciderla, e scriverla vorrebbe dire inchiodare tutta la mappa al primo
    trascinamento."""
    _run_gesti("""
const m = new Mappa('piante');
const spillato = { id: 'a', x: 5, y: 5 };
const libero = { id: 'b', x: 99, y: 99 };
const t = m._trascina([spillato, libero]);
t.gestori.start({ active: 0 }, spillato);
t.gestori.end({ active: 0 }, spillato);
await new Promise((r) => setTimeout(r, 0));
assert.deepEqual(Object.keys(JSON.parse(scritture[0][1]).piante), ['a'],
  'e\\' finito nel file anche un pallino che nessuno ha spostato');
""")


def test_another_notebook_keeps_its_own_arrangement() -> None:
    """Un file solo per tutti i quaderni, una chiave per ciascuno: salvare la
    propria non deve cancellare quella di un altro."""
    _run_gesti("""
letto = JSON.stringify({ viaggi: { 'Kyoto.md': [1, 2] } });
const m = new Mappa('piante');
await m._leggiSpilli();
const d = { id: 'a', x: 5, y: 5 };
const t = m._trascina([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.end({ active: 0 }, d);
await new Promise((r) => setTimeout(r, 0));
const scritto = JSON.parse(scritture[0][1]);
assert.deepEqual(scritto.viaggi, { 'Kyoto.md': [1, 2] },
  'salvando un quaderno si e\\' persa la disposizione di un altro');
assert.ok(scritto.piante, 'e la propria non c\\'e\\'');
""")


def test_a_page_that_left_the_notebook_leaves_the_file_too() -> None:
    """Si salva quel che è a schermo adesso, non quel che c'era più quel che si
    è aggiunto. Così una pagina cancellata sparisce al primo trascinamento
    successivo, senza che nessuno debba ricordarsene."""
    _run_gesti("""
letto = JSON.stringify({ piante: { 'Monstera.md': [1, 2], 'Sparita.md': [3, 4] } });
const m = new Mappa('piante');
await m._leggiSpilli();
// Nel disegno di oggi c'e' solo Monstera, ed e' spillata.
const viva = { id: 'Monstera.md', x: 7, y: 8, fx: 7, fy: 8 };
const t = m._trascina([viva]);
t.gestori.start({ active: 0 }, viva);
t.gestori.end({ active: 0 }, viva);
await new Promise((r) => setTimeout(r, 0));
assert.deepEqual(Object.keys(JSON.parse(scritture[0][1]).piante), ['Monstera.md'],
  'lo spillo di una pagina che non esiste piu\\u2019 resta nel file per sempre');
""")


def test_no_arrangement_yet_is_not_a_failure() -> None:
    """Il file non esiste la prima volta, ed è il caso normale: 404. Qualunque
    altro inciampo — file illeggibile, JSON di un'altra versione — finisce nello
    stesso posto **di proposito**: una disposizione perduta è un peccato, una
    mappa che non si disegna è un guasto, e fra i due non c'è partita."""
    _run_gesti("""
rotto = true;
assert.equal(await new Mappa('piante')._leggiSpilli(), null,
  'un file che non c\\'e\\' ancora viene preso per un guasto');

rotto = false; letto = '{ questo non e' + String.fromCharCode(39) + ' json';
assert.equal(await new Mappa('piante')._leggiSpilli(), null,
  'un file rotto fa saltare il disegno invece di essere ignorato');

letto = JSON.stringify({ altro: { a: [1, 2] } });
assert.equal(await new Mappa('piante')._leggiSpilli(), null,
  'un quaderno senza spilli non torna null');
""")


def test_a_workspace_without_the_folder_gets_it_made_once() -> None:
    """`.jenny/` può non esserci su un workspace appena nato, e questa può
    essere la prima a scriverci. Un solo secondo tentativo: la mappa a schermo è
    già come l'utente l'ha messa, e insistere non la cambierebbe."""
    _run_gesti("""
let falliti = 0;
rpc.writeWorkspaceFile = (path, content) => {
  falliti++;
  if (falliti === 1) return Promise.reject(new Error('ENOENT'));
  scritture.push([path, content]);
  return Promise.resolve();
};
const m = new Mappa('piante');
const d = { id: 'a', x: 1, y: 1 };
const t = m._trascina([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.end({ active: 0 }, d);
await new Promise((r) => setTimeout(r, 0));
assert.deepEqual(cartelle, ['.jenny'], 'la cartella non viene creata al primo inciampo');
assert.equal(scritture.length, 1, 'il secondo tentativo non ha scritto');
""")


def test_the_pins_are_applied_before_the_physics_starts() -> None:
    """Applicarli dopo vorrebbe dire far partire la simulazione da posizioni
    casuali e poi strattonare i nodi al loro posto sotto gli occhi. Si legge dal
    sorgente perché `draw` è la funzione che carica D3."""
    src = MAP_JS.read_text(encoding="utf-8")
    draw = _member(src, "draw")
    assert "_leggiSpilli()" in draw, "gli spilli non si leggono affatto"
    assert draw.index("_leggiSpilli()") < draw.index("this._render("), (
        "si disegna prima di sapere dove vanno i pallini spillati"
    )
    assert "n.x = n.fx" in draw and "n.y = n.fy" in draw, (
        "lo spillo fissa il nodo ma non lo mette li': la fisica partirebbe da "
        "una posizione casuale e lo strattonerebbe a posto sotto gli occhi"
    )
