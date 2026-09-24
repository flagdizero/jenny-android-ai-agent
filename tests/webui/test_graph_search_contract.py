"""La ricerca nel grafo, guidata davvero: indice Python → d3 → classi sui nodi.

``test_wiki_search_client.py`` verifica il motore di ricerca in isolamento
(query → maschera). Qui si verifica ciò che il motore *provoca*: quali nodi
disegnati si accendono, quali si spengono, cosa fa il tasto Indietro. È
l'unico punto in cui si nota la classe di bug più cara di questa funzionalità —
la maschera che indicizza l'array sbagliato.

Il grafo scarta ``wiki/index.md`` dal disegno mentre l'indice lo conta fra i
nodi: se la posizione dei nodi venisse letta *dopo* quel filtro, ogni ricerca
accenderebbe il vicino invece del risultato. È lo scenario 3 qui sotto.

Serve jsdom (per il DOM) oltre a node. Non essendo il repo un progetto npm, il
test si salta da solo se manca; per abilitarlo basta renderlo risolvibile::

    npm install jsdom && NODE_PATH=$PWD/node_modules pytest tests/webui/test_graph_search_contract.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support.js_harness import NODE

from jenny.webui.wiki import build_graph, read_pages
from jenny.webui.wiki_search import SearchIndex, pack_index

UI_DIR = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"



def _jsdom_available() -> bool:
    if NODE is None:
        return False
    probe = subprocess.run(
        [NODE, "-e", "require.resolve('jsdom')"], capture_output=True, text=True
    )
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(
    not _jsdom_available(), reason="node+jsdom non disponibili (v. docstring)"
)

# Wiki di prova: quattro pagine disegnate più una index.md che il grafo scarta.
_PAGES = {
    "index.md": "# Home\n\nVedi [[Doze]] e [[Wakelock]].",
    "doze.md": "# Doze mode\n\nIl sonno profondo di Android. Vedi [[Wakelock]].",
    "wakelock.md": "# Wakelock\n\nTenere sveglia la CPU durante il sonno.",
    "cron.md": "# Cron\n\nPianificazione dei lavori ricorrenti.",
    "batteria.md": "# Batteria\n\nConsumo e ottimizzazioni. Vedi [[Doze]].",
}

# Dipendenze del controller sostituite da stub: la fetch (che qui serve il
# payload già pronto) e le traduzioni. Tutto il resto — d3, il DOM, il
# controller — è quello vero.
_API_STUB = "export const api = { getGraph: async () => globalThis.__GRAPH__ };\n"
_I18N_STUB = "export const i18n = { t: (k) => k };\n"

_HARNESS = r"""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

// Via `createRequire` e non con un `import`: la risoluzione ESM ignora
// NODE_PATH, che è l'unico modo di trovare jsdom in un repo che non è un
// progetto npm.
const { JSDOM } = createRequire(import.meta.url)('jsdom');

const UI = new URL('./ui/', import.meta.url);
const dom = new JSDOM(fs.readFileSync(new URL('index.html', UI), 'utf-8'), {
  pretendToBeVisual: true, runScripts: 'outside-only', url: 'http://localhost/',
});
const { window } = dom;

// d3 è UMD: si esegue nel contesto della finestra, come nell'app.
window.eval(fs.readFileSync(new URL('assets/vendor/d3@7/d3.min.js', UI), 'utf-8'));

// jsdom non fa layout: senza queste due misure la tela è 0x0 e il grafo
// collassa in un punto. getBBox serve al declutter delle etichette.
Object.defineProperty(window.SVGElement.prototype, 'clientWidth', { get: () => 400 });
Object.defineProperty(window.SVGElement.prototype, 'clientHeight', { get: () => 600 });
window.SVGElement.prototype.getBBox = function () {
  return { x: 0, y: 0, width: (this.textContent || '').length * 6, height: 11 };
};

const define = (k, v) =>
  Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true });
// `localStorage` sta nell'elenco perché `assets/shared/state.js` lo legge
// a load-time, non dietro una funzione: senza, l'import del controller muore
// prima di arrivare al primo assert. jsdom lo fornisce grazie all'opzione
// `url` sopra; i moduli lo leggono come globale nudo, non come `window.*`.
for (const k of ['document', 'window', 'requestAnimationFrame', 'cancelAnimationFrame',
                 'CSS', 'getComputedStyle', 'SVGElement', 'Element', 'Node', 'd3',
                 'localStorage', 'sessionStorage']) {
  define(k, window[k]);
}
// Non si può aliasare window.performance: l'implementazione di jsdom rimbalza
// sul globale e ricorre all'infinito.
define('performance', { now: () => Date.now() });

define('__GRAPH__', JSON.parse(fs.readFileSync(new URL('./graph.json', import.meta.url), 'utf-8')));
// L'header sta per quello vero, e `loadGraph` ne usa tre metodi: il titolo e i
// due che accendono e spengono il tasto verso la chat del progetto. Un finto a
// cui manca un metodo del vero non e' un finto piu' piccolo, e' un errore in
// mezzo alla prova: `showAction is not a function` faceva morire l'import prima
// del primo assert sulla maschera di ricerca.
window.mobileApp = {
  currentMode: 'graph',
  header: { setTitle() {}, showAction() {}, hideAction() {} },
  pushNav() {},
  takePendingGraph: () => null, switchMode() {}, controllers: {},
};
define('mobileApp', window.mobileApp);

const { GraphController } = await import(new URL('assets/mobile-graph.js', UI).href);
const gc = new GraphController();
await gc.loadGraph('main', false);

const input = window.document.getElementById('graph-search-input');
const bar = window.document.getElementById('graph-search');
const count = window.document.getElementById('graph-search-count');

const drawn = () => [...window.document.querySelectorAll('g.node')];
const idsOf = (els) => els.map(n => window.d3.select(n).datum().id).sort();
const lit = () => idsOf(drawn().filter(n => n.classList.contains('match')));
const dimmed = () => idsOf(drawn().filter(n => n.classList.contains('dim')));

// `_setQuery` è il percorso sincrono (pulsante Cancella, tasto Indietro): sotto
// jsdom evita di dover pompare a mano il requestAnimationFrame della digitazione.
const type = (text) => { input.value = text; gc._setQuery(text); };

assert.equal(bar.hidden, false, 'la barra deve comparire su un grafo di wiki');
assert.equal(drawn().length, 4, 'index.md è escluso dal disegno, non dall’indice');

// 1. Una parola presente nel corpo di una sola pagina.
type('profondo ');
assert.deepEqual(lit(), ['wiki/doze.md'], 'match sul corpo della pagina');
assert.deepEqual(dimmed(), ['wiki/batteria.md', 'wiki/cron.md', 'wiki/wakelock.md']);
assert.equal(count.hidden, false);
assert.equal(count.textContent, '1');

// 2. Prefisso: ogni tappa della digitazione accende già.
for (const p of ['son', 'sonn', 'sonno']) {
  type(p);
  assert.deepEqual(lit(), ['wiki/doze.md', 'wiki/wakelock.md'], `prefisso '${p}'`);
}

// 3. La maschera indicizza l'array del *server*, non quello disegnato.
type('batteria ');
assert.deepEqual(lit(), ['wiki/batteria.md'], 'maschera disallineata dal filtro su index.md');

// 4. Zero risultati: spegne tutto e lo dichiara. Diverso da "nessun vincolo".
type('inesistente ');
assert.deepEqual(lit(), []);
assert.equal(dimmed().length, 4, 'zero risultati deve spegnere il grafo');
assert.equal(count.textContent, '0');
assert.ok(count.classList.contains('empty'));

// 5. Etichette accese esattamente sui match, senza rieseguire il declutter.
type('cron ');
const bigLabels = [...window.document.querySelectorAll('text.node-label.big')]
  .map(t => window.d3.select(t).datum().id);
assert.deepEqual(bigLabels, ['wiki/cron.md'], 'etichette forzate sui soli risultati');

// 5b. L'anello del match sta *dentro* al disco, sempre. Se sbordasse finirebbe
//     sul fondo, che è del suo stesso colore, e sparirebbe.
const ringOf = (el) => +el.querySelector('.node-ring').getAttribute('r');
const mainOf = (el) => +el.querySelector('.node-main').getAttribute('r');
for (const n of drawn()) {
  assert.ok(ringOf(n) < mainOf(n), 'anello fuori dal disco a riposo');
  assert.ok(ringOf(n) > 0, 'anello collassato');
}

// 6. Indietro smonta la ricerca, e poi non ha più niente da smontare.
assert.equal(gc.handleBack(), true, 'Indietro deve consumare la ricerca');
assert.equal(input.value, '');
assert.deepEqual(dimmed(), [], 'query svuotata: grafo di nuovo tutto acceso');
assert.equal(count.hidden, true);
assert.equal(gc.handleBack(), false, 'nessun sotto-stato residuo');

// 7. Il focus su un risultato sta *dentro* la ricerca: vince sul dim, ma il
//    marcatore .match sopravvive e uscendo si torna alla vista di ricerca.
type('doze ');
assert.deepEqual(lit(), ['wiki/batteria.md', 'wiki/doze.md']);
const nodeById = (id) => drawn().find(n => window.d3.select(n).datum().id === id);
const radiusAtRest = mainOf(nodeById('wiki/doze.md'));
const ringAtRest = ringOf(nodeById('wiki/doze.md'));
window.d3.select(nodeById('wiki/doze.md')).dispatch('click');
assert.ok(dimmed().includes('wiki/cron.md'), 'nel focus il resto resta spento');
assert.ok(!dimmed().includes('wiki/wakelock.md'),
          'un vicino del focus si accende anche se non è un risultato');
assert.deepEqual(lit(), ['wiki/batteria.md', 'wiki/doze.md'], '.match sopravvive al focus');
// Il nodo a fuoco è ingrandito 1.4×: l'anello deve essere cresciuto con lui e
// restare dentro al disco. È lo stato in cui il marcatore serve davvero —
// vicini tutti accesi, e solo l'anello dice quali erano risultati.
const focused = nodeById('wiki/doze.md');
assert.ok(mainOf(focused) > radiusAtRest, 'il nodo a fuoco non è cresciuto');
assert.ok(ringOf(focused) < mainOf(focused), 'anello sbordato dal nodo ingrandito');
assert.ok(ringOf(focused) > ringAtRest,
          'anello rimasto al raggio a riposo mentre il nodo cresceva');
assert.equal(gc.handleBack(), true, 'Indietro esce prima dal focus');
assert.deepEqual(dimmed(), ['wiki/cron.md', 'wiki/wakelock.md'], 'tornati alla vista di ricerca');
assert.equal(input.value, 'doze ', 'la query è ancora nella barra');

// 8. Un nodo spento non si tocca: a 0.12 di opacità un tap è un incidente.
window.d3.select(nodeById('wiki/cron.md')).dispatch('click');
assert.deepEqual(dimmed(), ['wiki/cron.md', 'wiki/wakelock.md'], 'tap su nodo spento ignorato');

console.log('ok');
"""


@pytest.fixture
def harness_dir(tmp_path: Path) -> Path:
    """Copia della SPA con le due dipendenze del controller sostituite da stub."""
    ui = tmp_path / "ui"
    shutil.copytree(UI_DIR, ui)
    (ui / "assets" / "shared" / "api-client.js").write_text(_API_STUB, encoding="utf-8")
    (ui / "assets" / "shared" / "i18n.js").write_text(_I18N_STUB, encoding="utf-8")

    pages_dir = tmp_path / "wikis" / "main" / "wiki"
    pages_dir.mkdir(parents=True)
    for rel, content in _PAGES.items():
        (pages_dir / rel).write_text(content, encoding="utf-8")

    graph = build_graph(pages_dir.parent)
    index = SearchIndex.from_pages(read_pages(pages_dir), graph)
    (tmp_path / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": n.id, "label": n.label, "path": n.path,
                        "group": n.group, "degree": n.degree, "title": n.title,
                    }
                    for n in graph.nodes
                ],
                "edges": [{"source": e.source, "target": e.target} for e in graph.edges],
                "search": pack_index(index, "v1"),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "harness.mjs").write_text(_HARNESS, encoding="utf-8")
    return tmp_path


def test_search_lights_and_dims_the_right_nodes(harness_dir: Path) -> None:
    assert NODE is not None
    proc = subprocess.run(
        [NODE, str(harness_dir / "harness.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=harness_dir,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout.strip().endswith("ok")
