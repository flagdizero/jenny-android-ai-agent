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
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MAP_JS = ASSETS / "home-map.js"


pytestmark = requires_node


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
    return member(source, name, prefixes=("async ",))


_APPLICA = """
/* Una selezione di D3 ridotta a cio' che `_placeLabels` usa: `each` con
   `this` sul nodo del testo, e `attr` con una funzione per dato. */
function selezione(data, larghezze) {
  const scritti = {};
  const sel = {
    scritti,
    each(fn) {
      for (const d of data) fn.call({ getComputedTextLength: () => larghezze[d.id] }, d);
    },
    attr(name, f) {
      scritti[name] = data.map((d) => [d.id, typeof f === 'function' ? f(d) : f]);
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
            function(src, n)
            for n in ("radiusOf", "toSimulation", "shortLabel", "labelledNodes",
                      "labelOffsets", "labelBox", "placeLabels")
        ]
    )
    harness += _APPLICA.replace("__PLACE__", _member(src, "_placeLabels"))
    run_js(harness + "\n" + script)


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
      const long = 'Coltivazione-Monstera-Roma \\u2014 Sostegno, fertilizzazione, crescita';
      const short = shortLabel(long);
      assert.ok(short.length <= LABEL_CHARS, short);
      assert.ok(short.endsWith('\\u2026'), short);
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
      const where = placeLabels([a, b]);
      assert.equal(where.size, 2, 'un nome e\\u2019 sparito quando bastava spostarlo');
      assert.notEqual(where.get('a'), where.get('b'), 'sono ancora nello stesso posto');
      assert.ok(where.get('a') > 0, 'il piu\\u2019 collegato ha perso il posto buono');
      assert.ok(where.get('b') < 0, 'il secondo non e\\u2019 andato sopra');
    """)


def test_the_most_connected_page_keeps_the_good_spot() -> None:
    """Chi arriva prima sceglie, e arriva prima la pagina piu' collegata: e' la
    stessa gerarchia con cui si decide chi un nome ce l'ha."""
    _run("""
      const debole = { id: 'debole', x: 100, y: 100, w: 90, r: 8, priority: 1 };
      const hub = { id: 'hub', x: 100, y: 100, w: 90, r: 8, priority: 9 };
      const where = placeLabels([debole, hub]);
      assert.equal(where.get('hub'), labelOffsets(8)[0], 'il nodo hub non sta sotto');
    """)


def test_a_name_with_nowhere_to_go_disappears() -> None:
    """Due parole sovrapposte non sono due informazioni, sono zero. Il pallino
    resta, si tocca lo stesso, e il nome sta nell'elenco accanto."""
    _run("""
      const items = [0, 1, 2].map((i) => ({
        id: 'n' + i, x: 100, y: 100, w: 120, r: 8, priority: 3 - i,
      }));
      const where = placeLabels(items);
      assert.equal(where.size, 2, 'tre nomi nello stesso punto e ne restano ' + where.size);
      assert.ok(where.has('n0') && where.has('n1'));
      assert.ok(!where.has('n2'), 'il terzo si e\\u2019 accavallato');
    """)


def test_far_apart_names_all_keep_the_preferred_spot() -> None:
    """Il caso normale: niente si tocca, e niente si muove."""
    _run("""
      const items = [0, 1, 2, 3].map((i) => ({
        id: 'n' + i, x: 60 + i * 200, y: 60 + i * 120, w: 70, r: 6, priority: i,
      }));
      const where = placeLabels(items);
      assert.equal(where.size, 4);
      for (const off of where.values()) assert.equal(off, labelOffsets(6)[0]);
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
      const right = { id: 'b', x: 141, y: 100, w: 40, r: 6, priority: 1 };
      // E a sinistra, uguale: le due condizioni del confronto sono due righe
      // diverse, e una sola delle due morde a seconda di chi sta dove.
      const sinistra = { id: 'b', x: 59, y: 100, w: 40, r: 6, priority: 1 };
      for (const vicino of [right, sinistra]) {
        const where = placeLabels([a, vicino]);
        assert.equal(where.get('a'), labelOffsets(6)[0]);
        assert.equal(where.get('b'), labelOffsets(6)[1],
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
      const where = placeLabels([largo, stretto]);
      assert.equal(where.get('largo'), labelOffsets(6)[0]);
      assert.equal(where.get('stretto'), labelOffsets(6)[0],
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
  /* `forceX`/`forceY` ridotte a quel che il banco chiede loro: con che
     bersaglio e con che forza sono state costruite. */
  forceX(x) { return molla('x', x); },
  forceY(y) { return molla('y', y); },
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

function molla(axis, target) {
  const f = { axis, target, forza: null };
  f.strength = (v) => { f.forza = v; return f; };
  return f;
}

/* Il disco, ridotto a una variabile. `letto` e' cio' che il file contiene,
   `scritture` cio' che ci finisce; `rotto` fa fallire la lettura con quello
   stato HTTP (come fa `api.readWorkspaceFile`, che lo appende all'errore),
   `letture` le conta, `mkdir` conta le cartelle create. */
let letto = null, rotto = 0, letture = 0;
const scritture = [], cartelle = [];
const api = {
  readWorkspaceFile() {
    letture += 1;
    if (rotto) {
      const err = new Error(`Workspace read failed: ${rotto}`);
      err.status = rotto;
      return Promise.reject(err);
    }
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
    molle: {},
    force(name, f) { sim.molle[name] = f; diario.push(['force', name]); return sim; },
  };
  return sim;
}

function svgFinto() {
  const applicate = [];
  return { applicate, call(_che, t) { applicate.push(t); } };
}

const zoomFinto = { transform: 'TRANSFORM' };

class Mappa {
  constructor(notebook = 'quaderno') {
    this._grabbed = false;
    this._sim = fisicaFinta();
    this._notebook = notebook;
    this._pins = null;
    this._w = 600;
    this._h = 400;
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
        + _const(src, "TAP_THRESHOLD")
        + "\n"
        + _const(src, "PINS_FILE")
        + "\n"
        + _const(src, "ANCHOR_FORCE")
        + "\n"
        + _CAMERA.replace(
            "__METODI__",
            "\n".join(
                "  " + _member(src, n)
                for n in ("_onZoom", "_drag", "_frame", "_release",
                          "_loadPins", "_readPins", "_savePins")
            ),
        )
    )
    run_js(harness + "\n" + script)


def test_at_rest_the_cloud_gets_framed() -> None:
    """A fisica ferma la nuvola sta dove l'hanno lasciata le forze, che non e'
    il centro: sei nodi in basso a destra e mezza stanza vuota. Senza questa
    inquadratura la mappa si apre guardando il vuoto."""
    _run_gesti("""
const m = new Mappa();
const svg = svgFinto();
m._frame(svg, zoomFinto, NODI, 590, 400);
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
const root = { attr() {} };

// Il dito: D3 porta `sourceEvent` solo per un gesto vero.
m._onZoom({ sourceEvent: { type: 'touchmove' }, transform: 'MIA' }, root);
assert.equal(m._grabbed, true, 'un gesto vero non viene riconosciuto');

const svg = svgFinto();
m._frame(svg, zoomFinto, NODI, 590, 400);
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
const root = { attr() {} };
m._onZoom({ sourceEvent: null, transform: 'NOSTRA' }, root);
assert.equal(m._grabbed, false,
  'inquadrarsi da soli viene contato come un gesto dell\\'utente');

const svg = svgFinto();
m._frame(svg, zoomFinto, NODI, 590, 400);
assert.equal(svg.applicate.length, 1, 'la nuvola non viene piu' + ' inquadrata');
""")


def test_the_transform_reaches_the_drawing_either_way() -> None:
    """Gesto o no, la trasformazione va applicata al gruppo: la guardia decide
    *chi inquadra*, non se il disegno si muove."""
    _run_gesti("""
const m = new Mappa();
const viste = [];
const root = { attr: (name, v) => viste.push([name, v]) };
m._onZoom({ sourceEvent: { type: 'touchmove' }, transform: 'MIA' }, root);
m._onZoom({ sourceEvent: null, transform: 'NOSTRA' }, root);
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
    assert "this._grabbed = false;" in render, (
        "un quaderno ridisegnato si apre guardando dove guardava il precedente"
    )
    assert "_grabbed" not in draw, (
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
    body = m.group(1)
    assert "this._placeLabels(" in body, (
        "a fisica ferma i nomi non si ricollocano: restano dove stavano prima"
    )
    assert "_grabbed" not in body, (
        "la ricollocazione dei nomi e' finita sotto la guardia dell'inquadratura: "
        "chi ha spostato la mappa col dito non li vedrebbe piu' aggiustare"
    )
    assert body.index("this._placeLabels(") < body.index("this._frame("), (
        "si inquadra prima di sapere dove stanno i nomi"
    )


# ── Un pallino preso resta dove lo metti ────────────────────────────────────


def test_letting_go_anchors_the_dot_instead_of_nailing_it() -> None:
    """**Il secondo pensiero dell'utente, il 21/09/2026:** «non si puo' fare in
    modo che segua la fisica ma da quella posizione? perche' ora e' proprio
    piantatissimo».

    Con `fx`/`fy` tenuti dopo il gesto il nodo usciva del tutto dalle forze: la
    nuvola si deformava attorno a un peso morto. Mollarlo e basta pero' non e' la
    risposta — a tirarlo indietro sono i collegamenti, e un nodo con molti fili
    tornerebbe nella matassa. Quindi il punto dove l'hai lasciato diventa il
    **suo** centro: tenuto col dito mentre trascini, ancorato quando alzi.
    """
    _run_gesti("""
const m = new Mappa();
const d = { id: 'p1', x: 100, y: 100 };
const t = m._drag([d]);

t.gestori.start({ active: 0 }, d);
assert.deepEqual([d.fx, d.fy], [100, 100], 'il pallino non si tiene sotto il dito');
t.gestori.drag({ x: 260, y: 40 }, d);
assert.deepEqual([d.fx, d.fy], [260, 40], 'il pallino non segue il dito');

t.gestori.end({ active: 0 }, d);
assert.deepEqual([d.ax, d.ay], [260, 40], 'il punto lasciato non diventa la sua ancora');
assert.deepEqual([d.fx, d.fy], [null, null],
  'il pallino resta inchiodato: le forze non lo toccano piu\u2019');
""")


def test_the_springs_are_rebuilt_when_an_anchor_appears() -> None:
    """**Il difetto silenzioso di questo giro.** `forceX` legge bersaglio e
    forza una volta sola, quando entra nella simulazione: li precalcola in due
    array. Scrivere `d.ax` dopo non lo vedrebbe nessuno.

    E sarebbe silenzioso proprio perche' *sembra* funzionare: alzando il dito il
    pallino e' gia' dove l'hai messo, e solo qualche secondo dopo la fisica se
    lo riporterebbe via — cioe' esattamente il comportamento che questo giro
    doveva togliere. Rimettere le forze le fa reinizializzare, ed e' la strada
    di D3.
    """
    _run_gesti("""
const m = new Mappa();
const d = { id: 'p1', x: 1, y: 1 };
const t = m._drag([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.drag({ x: 300, y: 200 }, d);
t.gestori.end({ active: 0 }, d);

assert.deepEqual(Object.keys(m._sim.molle).sort(), ['x', 'y'],
  'le molle non vengono rimesse: la nuova ancora resta invisibile alla fisica');
assert.equal(m._sim.molle.x.target(d), 300, "la molla non punta all'ancora");
assert.equal(m._sim.molle.x.forza(d), ANCHOR_FORCE, "la molla dell'ancora e' quella debole");
""")


def test_a_dot_nobody_moved_is_still_pulled_to_the_middle() -> None:
    """La molla dell'ancora e' la stessa che gia' teneva insieme la nuvola,
    puntata altrove e piu' tesa. Un pallino mai toccato deve continuare a
    sentire quella di prima: senza, le pagine scollegate volerebbero via e
    mezza stanza resterebbe vuota — e' il difetto per cui quella molla esiste."""
    _run_gesti("""
const m = new Mappa();
const f = m._release('x', 600, 400);
assert.equal(f.target({ id: 'libero' }), 300, 'un pallino libero non punta al centro');
assert.equal(f.forza({ id: 'libero' }), 0.06, 'la molla debole ha cambiato valore');
assert.ok(ANCHOR_FORCE > 0.06,
  "l'ancora non tira piu' del centro: il trascinamento non lascerebbe traccia");
assert.ok(ANCHOR_FORCE < 1, "un'ancora cosi' tesa e' di nuovo un chiodo");
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
m._drag([]).gestori.start({ active: 0 }, { x: 10, y: 10 });
assert.equal(m._grabbed, true, 'il trascinamento non prende la mappa in mano');

const svg = svgFinto();
m._frame(svg, zoomFinto, NODI, 590, 400);
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
    ha il suo commento in `TAP_THRESHOLD`; il banco misura che ci sia e che sia
    quello, non che sia giusto.
    """
    _run_gesti("""
const soglia = new Mappa()._drag([]).soglia;
assert.equal(soglia, TAP_THRESHOLD, 'il gesto non distingue un tocco da un trascinamento');
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
const t = m._drag([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.end({ active: 0 }, d);
assert.deepEqual(m._sim.diario.filter((r) => r[0] === 'alphaTarget' || r[0] === 'restart'),
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
const t = m._drag([]);
const d = { id: 'p1', x: 1, y: 1 };
t.gestori.start({ active: 1 }, d);
t.gestori.end({ active: 1 }, d);
assert.deepEqual(m._sim.diario.filter((r) => r[0] !== 'force'), [],
  'il secondo dito rimette mano alla fisica del primo');
""")


# ── Gli spilli restano fra un'apertura e l'altra ────────────────────────────


def test_letting_go_writes_the_arrangement() -> None:
    """Chiesto dall'utente: «i nodi devono rimanere salvati». Si scrive alzando
    il dito, che è l'unico momento in cui uno spillo nasce o si sposta."""
    _run_gesti("""
const m = new Mappa('piante');
const d = { id: 'Monstera.md', x: 10, y: 10 };
const t = m._drag([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.drag({ x: 240.4, y: 91.6 }, d);
t.gestori.end({ active: 0 }, d);
await new Promise((r) => setTimeout(r, 0));

assert.equal(scritture.length, 1, 'alzando il dito non si salva niente');
const [path, text] = scritture[0];
assert.equal(path, PINS_FILE);
assert.deepEqual(JSON.parse(text), { piante: { 'Monstera.md': [240, 92] } },
  'la posizione salvata non e\\' quella where il dito ha lasciato il pallino');
""")


def test_only_the_pinned_dots_are_written() -> None:
    """Un pallino mai toccato non ha una posizione da ricordare: è la fisica a
    deciderla, e scriverla vorrebbe dire inchiodare tutta la mappa al primo
    trascinamento."""
    _run_gesti("""
const m = new Mappa('piante');
const spillato = { id: 'a', x: 5, y: 5 };
const libero = { id: 'b', x: 99, y: 99 };
const t = m._drag([spillato, libero]);
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
await m._readPins();
const d = { id: 'a', x: 5, y: 5 };
const t = m._drag([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.end({ active: 0 }, d);
await new Promise((r) => setTimeout(r, 0));
const written = JSON.parse(scritture[0][1]);
assert.deepEqual(written.viaggi, { 'Kyoto.md': [1, 2] },
  'salvando un quaderno si e\\' persa la disposizione di un altro');
assert.ok(written.piante, 'e la propria non c\\'e\\'');
""")


def test_a_page_that_left_the_notebook_leaves_the_file_too() -> None:
    """Si salva quel che è a schermo adesso, non quel che c'era più quel che si
    è aggiunto. Così una pagina cancellata sparisce al primo trascinamento
    successivo, senza che nessuno debba ricordarsene."""
    _run_gesti("""
letto = JSON.stringify({ piante: { 'Monstera.md': [1, 2], 'Sparita.md': [3, 4] } });
const m = new Mappa('piante');
await m._readPins();
// Nel disegno di oggi c'e' solo Monstera, ed e' spillata.
const viva = { id: 'Monstera.md', x: 7, y: 8, ax: 7, ay: 8 };
const t = m._drag([viva]);
t.gestori.start({ active: 0 }, viva);
t.gestori.end({ active: 0 }, viva);
await new Promise((r) => setTimeout(r, 0));
assert.deepEqual(Object.keys(JSON.parse(scritture[0][1]).piante), ['Monstera.md'],
  'lo spillo di una pagina che non esiste piu\\u2019 resta nel file per sempre');
""")


def test_no_arrangement_yet_is_not_a_failure() -> None:
    """Il file non esiste la prima volta, ed è il caso normale: 404. Qualunque
    altro inciampo — file illeggibile, JSON di un'altra versione — torna null
    anche lui **di proposito**: una disposizione che non si vede è un peccato,
    una mappa che non si disegna è un guasto, e fra i due non c'è partita."""
    _run_gesti("""
rotto = 404;
assert.equal(await new Mappa('piante')._readPins(), null,
  'un file che non c\\'e\\' ancora viene preso per un guasto');

rotto = 500;
assert.equal(await new Mappa('piante')._readPins(), null,
  'una lettura fallita fa saltare il disegno invece di essere ignorata');

rotto = 0; letto = '{ questo non e' + String.fromCharCode(39) + ' json';
assert.equal(await new Mappa('piante')._readPins(), null,
  'un file rotto fa saltare il disegno invece di essere ignorato');

letto = JSON.stringify({ altro: { a: [1, 2] } });
assert.equal(await new Mappa('piante')._readPins(), null,
  'un quaderno senza spilli non torna null');
""")


def test_a_read_that_failed_never_writes_over_the_other_notebooks() -> None:
    """Solo il 404 vale «nessuno spillo». Una lettura fallita per altro — la
    rete, un 500 — diventava `{}` in cache come il 404, e il primo
    trascinamento dopo riscriveva il file con quel vuoto: **le disposizioni di
    tutti gli altri quaderni cancellate** per un inciampo di un momento."""
    _run_gesti("""
for (const [come, prepara] of [
  ['una lettura fallita', () => { rotto = 500; }],
  ['un JSON rotto', () => { rotto = 0; letto = '{ rotto'; }],
  ['un JSON che non e\\u2019 un oggetto', () => { rotto = 0; letto = '[1, 2]'; }],
]) {
  prepara();
  scritture.length = 0;
  const m = new Mappa('piante');
  await m._readPins();
  const d = { id: 'a', x: 5, y: 5 };
  const t = m._drag([d]);
  t.gestori.start({ active: 0 }, d);
  t.gestori.end({ active: 0 }, d);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(scritture, [], come + ': il file e\\u2019 stato riscritto senza averlo letto');
}
""")


def test_a_read_that_failed_is_tried_again() -> None:
    """Non messa in cache, una lettura fallita si ritenta: al disegno dopo, o
    al trascinamento, che se ci riesce scrive sopra il file vero."""
    _run_gesti("""
rotto = 500;
const m = new Mappa('piante');
assert.equal(await m._readPins(), null);
rotto = 0;
letto = JSON.stringify({ viaggi: { 'Kyoto.md': [1, 2] } });
const d = { id: 'a', x: 5, y: 5 };
const t = m._drag([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.end({ active: 0 }, d);
await new Promise((r) => setTimeout(r, 0));
assert.equal(letture, 2, 'la lettura fallita e\\u2019 rimasta in cache');
assert.deepEqual(JSON.parse(scritture[0][1]).viaggi, { 'Kyoto.md': [1, 2] },
  'il salvataggio dopo il ritento ha perso gli altri quaderni');
""")


def test_a_file_that_is_not_there_yet_is_written_from_scratch() -> None:
    _run_gesti("""
rotto = 404;
const m = new Mappa('piante');
assert.equal(await m._readPins(), null);
const d = { id: 'a', x: 5, y: 5 };
const t = m._drag([d]);
t.gestori.start({ active: 0 }, d);
t.gestori.end({ active: 0 }, d);
await new Promise((r) => setTimeout(r, 0));
assert.deepEqual(JSON.parse(scritture[0][1]), { piante: { a: [5, 5] } });
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
const t = m._drag([d]);
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
    assert "_readPins()" in draw, "gli spilli non si leggono affatto"
    assert draw.index("_readPins()") < draw.index("this._render("), (
        "si disegna prima di sapere dove vanno i pallini spillati"
    )
    assert "n.ax = n.x" in draw and "n.ay = n.y" in draw, (
        "l'ancora dice dove richiamarlo ma non da dove partire: la fisica "
        "comincerebbe da un punto a caso e lo strattonerebbe li' sotto gli occhi"
    )
