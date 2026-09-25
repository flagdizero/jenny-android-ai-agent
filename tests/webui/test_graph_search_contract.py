"""La ricerca di un quaderno, guidata davvero: indice Python → ``wiki-search.js`` → righe.

``test_wiki_search_client.py`` verifica il motore di ricerca in isolamento
(query → maschera) e ``test_casa_pages_client.py`` l'elenco della casa con un
indice finto. Qui i tre pezzi veri girano insieme: la risposta di
``/api/graph`` la costruisce :class:`WikiSearchService` come fa la rotta, la
smonta ``assets/shared/wiki-search.js`` e la legge ``assets/casa-pages.js``,
importato come modulo. Si guarda ciò che la ricerca *provoca*: quali righe
restano, quali si nascondono, cosa dice la nota.

È l'unico punto in cui si nota la classe di bug più cara di questa
funzionalità — la maschera che indicizza l'array sbagliato — con un indice che
nessuno ha scritto a mano. Le pagine di prova sono scelte perché l'ordine a
schermo (per titolo) sia **diverso** da quello del server (per percorso): se
la riga si cercasse per posizione a video, ogni ricerca accenderebbe il vicino.

Prima questo file guidava ``mobile-graph.js`` in jsdom; quel controller è
uscito con il grafo dell'officina, e l'unico consumatore di ``/api/graph`` è
ora la casa. Il DOM qui è finto e minimo come negli altri banchi della casa:
basta node, niente jsdom.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from support.js_harness import requires_node, run_module

from jenny.webui.wiki_search import WikiSearchService

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

# Server: per percorso (batteria, cron, doze, index, wakelock → 0..4).
# Schermo: per titolo (CPU sveglia, Doze mode, Home, Lavori ricorrenti,
# Vita della batteria → 4, 2, 3, 1, 0). Nessuna riga sta al suo posto tranne
# ``doze`` e ``index``, ed è voluto: v. il cappello.
_PAGES = {
    "index.md": "# Home\n\nVedi [[Doze]] e [[Wakelock]].",
    "doze.md": "# Doze mode\n\nIl sonno profondo di Android. Vedi [[Wakelock]].",
    "wakelock.md": "# CPU sveglia\n\nTenere accesa la CPU durante il sonno.",
    "cron.md": "# Lavori ricorrenti\n\nPianificazione dei lavori.",
    "batteria.md": "# Vita della batteria\n\nConsumo e ottimizzazioni. Vedi [[Doze]].",
}

# Le due dipendenze di ``casa-pages.js`` che non sono sotto prova: la fetch
# (che qui serve la risposta già costruita) e le traduzioni (la chiave basta).
_API_STUB = "export const api = { getGraph: async () => globalThis.__GRAPH__ };\n"
_I18N_STUB = "export const i18n = { t: (k) => k };\n"

_HARNESS = r"""
import fs from 'node:fs';
import assert from 'node:assert/strict';

/* Un DOM finto con la sola meccanica che `CasaPages` usa. */
function makeEl(tag) {
  const el = {
    tag, className: '', textContent: '', value: '', placeholder: '', type: '',
    hidden: false, dataset: {}, attrs: {}, children: [],
    isFragment: tag === '#fragment',
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener() {},
    closest() { return null; },
    appendChild(child) {
      if (child.isFragment) { el.children.push(...child.children); child.children = []; }
      else el.children.push(child);
      return child;
    },
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { if (!v) el.children.length = 0; },
  });
  return el;
}
const nodes = {};
globalThis.document = {
  createElement: (tag) => makeEl(tag),
  createDocumentFragment: () => makeEl('#fragment'),
  getElementById: (id) => (nodes[id] ||= makeEl('div')),
};
globalThis.__GRAPH__ = JSON.parse(
  fs.readFileSync(new URL('./graph.json', import.meta.url), 'utf-8'));

const { CasaPages } = await import('./assets/casa-pages.js');
const pages = new CasaPages();
await pages.load('main');

const rows = () => pages.listEl.children;
const shown = () => rows().filter((r) => !r.hidden).map((r) => r.dataset.label);
const type = (text) => { pages.queryEl.value = text; pages._applySearch(); };

assert.deepEqual(rows().map((r) => r.dataset.label),
  ['CPU sveglia', 'Doze mode', 'Home', 'Lavori ricorrenti', 'Vita della batteria'],
  'ordine a schermo per titolo');
assert.deepEqual(rows().map((r) => r.dataset.node), ['4', '2', '3', '1', '0'],
  'ogni riga porta la posizione che il server le ha dato');

// 1. Una parola presente nel corpo di una sola pagina.
type('profondo ');
assert.deepEqual(shown(), ['Doze mode'], 'match sul corpo della pagina');
assert.equal(pages.noteEl.hidden, true);

// 2. Prefisso: ogni tappa della digitazione accende già.
for (const p of ['son', 'sonn', 'sonno']) {
  type(p);
  assert.deepEqual(shown(), ['CPU sveglia', 'Doze mode'], `prefisso '${p}'`);
}

// 3. La maschera indicizza l'array del *server*: la pagina 0 è l'ultima riga.
type('batteria ');
assert.deepEqual(shown(), ['Vita della batteria'], 'maschera letta per posizione a video');
type('pianificazione ');
assert.deepEqual(shown(), ['Lavori ricorrenti'], 'maschera letta per posizione a video');

// 4. Zero risultati: nasconde tutto e lo dichiara. Diverso da "nessun vincolo".
type('inesistente ');
assert.deepEqual(shown(), []);
assert.equal(pages.noteEl.hidden, false);
assert.equal(pages.noteEl.textContent, 'casa.pages.noMatch');

// 5. Query vuota: nessun vincolo, tutto di nuovo in vista.
type('');
assert.equal(shown().length, 5, 'query svuotata: tutte le righe di nuovo in vista');
assert.equal(pages.noteEl.hidden, true);

console.log('ok');
"""


def _harness_dir(tmp_path: Path) -> Path:
    """I due moduli veri, i due stub e la risposta del server, uno accanto all'altro."""
    assets = tmp_path / "assets"
    (assets / "shared").mkdir(parents=True)
    shutil.copy(ASSETS / "casa-pages.js", assets / "casa-pages.js")
    shutil.copy(ASSETS / "shared" / "wiki-search.js", assets / "shared" / "wiki-search.js")
    (assets / "shared" / "api-client.js").write_text(_API_STUB, encoding="utf-8")
    (assets / "shared" / "i18n.js").write_text(_I18N_STUB, encoding="utf-8")

    pages_dir = tmp_path / "wikis" / "main" / "wiki"
    pages_dir.mkdir(parents=True)
    for rel, content in _PAGES.items():
        (pages_dir / rel).write_text(content, encoding="utf-8")

    # Lo stesso servizio e la stessa forma della risposta di ``_wiki_graph``.
    bundle = WikiSearchService().bundle(pages_dir)
    payload = {
        "nodes": [
            {
                "id": n.id, "label": n.label, "path": n.path,
                "group": n.group, "degree": n.degree, "title": n.title,
            }
            for n in bundle.graph.nodes
        ],
        "edges": [{"source": e.source, "target": e.target} for e in bundle.graph.edges],
        "search": bundle.search,
    }
    (tmp_path / "graph.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "harness.mjs").write_text(_HARNESS, encoding="utf-8")
    return tmp_path


def test_search_shows_the_rows_the_server_meant(tmp_path: Path) -> None:
    out = run_module(_harness_dir(tmp_path) / "harness.mjs", timeout=120)
    assert out.strip().endswith("ok")
