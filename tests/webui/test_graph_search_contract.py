"""La ricerca di un quaderno, guidata davvero: indice Python → ``wiki-search.js`` → risultati.

``test_wiki_search_client.py`` verifica il motore di ricerca in isolamento
(query → maschera). Qui i tre pezzi veri girano insieme: la risposta di
``/api/graph`` la costruisce :class:`WikiSearchService` come fa la rotta, la
smonta ``assets/shared/wiki-search.js`` e la legge la lente della bacheca
(``HomeBoard.searchResults`` in ``assets/home-board.js``), importata come
modulo. Si guarda cosa la ricerca *trova*, e cosa dice di dove sta.

È l'unico punto in cui si nota la classe di bug più cara di questa
funzionalità — la maschera che indicizza l'array sbagliato — con un indice che
nessuno ha scritto a mano. La maschera è indicizzata per posizione nell'array
``nodes`` del server, e qui i titoli sono scelti perché nessuno coincida col
percorso: leggerla con un'altra chiave accenderebbe la pagina vicina.

Ogni risultato dice dove sta: il cassetto in cui la pagina è, oppure «fuori
bacheca» — la lente è l'unica porta verso le pagine che non stanno in nessun
cassetto.
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

# Le dipendenze di ``home-board.js`` che non sono sotto prova: la rete, i
# comandi, le traduzioni (la chiave basta) e gli aiuti di ``utils``.
_STUBS = {
    "api-client.js": "export const api = {};\n",
    "rpc-client.js": "export const rpc = {};\n",
    "i18n.js": "export const i18n = { locale: 'it', t: (k) => k };\n",
    "utils.js": (
        "export const escapeHtml = (s) => s; export const showToast = () => {};\n"
        "export const showUndo = () => () => {};\n"
        "export const ensureVendor = async () => {};\n"
    ),
    "dialog.js": "export const confirmDialog = async () => true;\n",
}

_HARNESS = r"""
import fs from 'node:fs';
import assert from 'node:assert/strict';

globalThis.document = { getElementById: () => null };
const graph = JSON.parse(fs.readFileSync(new URL('./graph.json', import.meta.url), 'utf-8'));

const { HomeBoard } = await import('./assets/home-board.js');
const { WikiSearchIndex } = await import('./assets/shared/wiki-search.js');
const board = new HomeBoard({});
board._graph = graph;
// Una scheda sola, «Sonno», che tiene in cassetto la pagina su Doze.
board.data = { cards: [{ id: 'sonno', title: 'Sonno' }], pageOwners: { 'doze.md': 'sonno' } };
const index = WikiSearchIndex.from(graph.search);
const find = (q) => board.searchResults(q, index).map((r) => `${r.kind}:${r.path || r.card}`);

// 1. Una parola presente nel corpo di una sola pagina, che sta in un cassetto.
assert.deepEqual(find('profondo '), ['page:doze.md']);

// 2. Prefisso: ogni tappa della digitazione trova gia'. La scheda si trova per
//    nome; delle due pagine, una e' in cassetto e l'altra fuori bacheca.
for (const p of ['son', 'sonn', 'sonno']) {
  assert.deepEqual(find(p).sort(), ['card:sonno', 'offboard:wakelock.md', 'page:doze.md'], p);
}

// 3. La maschera si legge per posizione nell'array del server.
assert.deepEqual(find('batteria '), ['offboard:batteria.md']);
assert.deepEqual(find('pianificazione '), ['offboard:cron.md']);

// 4. Il risultato porta il nome del suo cassetto.
const inDrawer = board.searchResults('profondo ', index)[0];
assert.equal(inDrawer.cardTitle, 'Sonno');

// 5. La mappa non e' un risultato, e una ricerca vuota non trova niente.
assert.deepEqual(find('vedi '), ['offboard:batteria.md', 'page:doze.md']);
assert.deepEqual(find('inesistente '), []);
assert.deepEqual(find(''), []);

console.log('ok');
"""


def _harness_dir(tmp_path: Path) -> Path:
    """I due moduli veri, i due stub e la risposta del server, uno accanto all'altro."""
    assets = tmp_path / "assets"
    (assets / "shared").mkdir(parents=True)
    shutil.copy(ASSETS / "home-board.js", assets / "home-board.js")
    # Le strisce: un modulo vero, che non tocca la ricerca (06/10/2026).
    shutil.copy(ASSETS / "home-board-blocks.js", assets / "home-board-blocks.js")
    # Le note e le foto di una scheda, che la bacheca apre nel suo foglio (14/10/2026).
    for name in ("home-board-deck.js", "home-board-notes.js", "home-board-photos.js",
                 "home-photo-viewer.js"):
        shutil.copy(ASSETS / name, assets / name)
    # L'etichetta dei cron e il ritmo a parole, che la bacheca disegna (20/10/2026).
    for name in ("home-board-automation.js", "home-board-agenda.js"):
        shutil.copy(ASSETS / name, assets / name)
    for name in ("pinch-zoom.js", "horizontal-swipe.js", "cron-view.js"):
        shutil.copy(ASSETS / "shared" / name, assets / "shared" / name)
    shutil.copy(ASSETS / "shared" / "wiki-search.js", assets / "shared" / "wiki-search.js")
    for name, text in _STUBS.items():
        (assets / "shared" / name).write_text(text, encoding="utf-8")

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


def test_board_search_finds_what_the_server_meant(tmp_path: Path) -> None:
    out = run_module(_harness_dir(tmp_path) / "harness.mjs", timeout=120)
    assert out.strip().endswith("ok")
