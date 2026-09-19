"""Dalla wiki alla chat del suo progetto: il tasto, e le due condizioni che lo spengono.

Il collegamento fra un progetto e la sua wiki esisteva **in una direzione sola**:
lo scope chip pubblica `AppState.pinnedWiki` e le viste wiki e grafo si
agganciano a quella wiki (`test_project_views_contract`), ma dalla wiki non si
tornava alla conversazione se non passando dalla chat e riaprendo la tendina. La
destinazione è già a schermo — un progetto *è* una wiki, e il nome della cartella
è il nome della sessione — quindi il tasto non deve chiedere niente a nessuno.

Le due condizioni che lo spengono sono il punto di questi test:

**La Home non ha un progetto da aprire.** Il grafo di tutte le wiki e l'indice
delle wiki *sono* l'elenco dei progetti: nessuno di quelli è più aperto degli
altri, e un tasto acceso lì dovrebbe sceglierne uno per conto dell'utente.

**Una cartella che il server non aprirebbe non si offre.** ``wiki_routes.py::
_collect_projects`` divide le wiki in ``projects`` e ``unopenable`` proprio
perché un nome che il server elenca dev'essere un nome che il server accetta —
altrimenti si tappa «Ricerca ETF» e si finisce in un'**altra** conversazione. Il
tasto fa la stessa domanda con la stessa regola (`isOpenableProjectName`, la
sola espressione della regola nel client), e la rifà al momento della pressione:
fra l'accensione e il tocco la vista può essere cambiata sotto, e cambiare
conversazione è il solo guasto irrecuperabile del disegno delle sessioni-progetto.

E un ordine: **prima la vista, poi lo scope.** `select` pubblica l'aggancio, e i
due ascoltatori si riagganciano solo se sono la vista a schermo — dalla sezione
wiki significherebbe un grafo ricaricato per essere buttato un istante dopo, o
la pagina che si sta leggendo sostituita dall'indice del progetto un attimo prima
di lasciarla.

I metodi si estraggono dal sorgente e si eseguono in node, come in
``test_scope_pin_and_create_client.py``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
HEADER_JS = ASSETS / "mobile-header.js"
GRAPH_JS = ASSETS / "mobile-graph.js"
WIKI_JS = ASSETS / "mobile-wiki.js"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"
# La regola del nome sta col resto di cosa può essere una conversazione.
LIST_JS = ASSETS / "shared" / "conversation-list.js"
I18N = ASSETS / "i18n"

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _member(path: Path, name: str) -> str:
    """Il corpo di un metodo di classe, indentato di due spazi."""
    m = re.search(
        rf"\n  ((?:async |get |static )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        _src(path),
        re.S,
    )
    assert m, f"{name} non trovato in {path.name}"
    return m.group(1) + "\n  }"


def _const(path: Path, name: str) -> str:
    """La riga di una costante di modulo, presa dal sorgente e non riscritta."""
    m = re.search(rf"(?m)^const {re.escape(name)} = .*;$", _src(path))
    assert m, f"const {name} non trovata in {path.name}"
    return m.group(0)


def _function(path: Path, name: str) -> str:
    """Una funzione di modulo, presa dal sorgente. `export` cade: qui non serve."""
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", _src(path))
    assert m, f"funzione {name} non trovata in {path.name}"
    return m.group(0).removeprefix("export ")


def _run_js(harness: str, script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", harness + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── La pressione ──────────────────────────────────────────────────────────

_PRESS_HARNESS = """
import assert from 'node:assert/strict';

__IS_OPENABLE__

// Il registro condiviso: le due mosse del tasto devono avvenire *in quest'ordine*.
let log = [];

const scopeChip = {
  select(scope) { log.push(['select', scope.kind, scope.name]); },
};

/** L'app attorno al tasto.
 *
 *  `chatReady` è la promessa che la chat espone: `sessionManager.init()`, cioè
 *  anche l'installazione di `scopeChip.onSwitch`. `blocked` è il primo avvio,
 *  che dirotta ogni navigazione sull'onboarding.
 */
function makeApp(mode, { graphWiki = null, wikiWiki = null, chatReady = null, blocked = false } = {}) {
  const app = {
    currentMode: mode,
    controllers: {
      graph: { currentWiki: graphWiki },
      wiki: { currentWiki: wikiWiki },
      chat: { ready: chatReady === null ? Promise.resolve() : chatReady },
    },
    switchMode(next) {
      log.push(['switchMode', next]);
      app.currentMode = blocked ? 'onboarding' : next;
    },
  };
  global.window = { mobileApp: app };
  log = [];
  return app;
}

/** Lascia girare i microtask in coda: `select` arriva dopo `chat.ready`. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

class Header {
__HANDLE_ACTION__
}
const header = new Header();
"""


def _press_harness() -> str:
    return (
        _PRESS_HARNESS.replace("__IS_OPENABLE__", _const(LIST_JS, "VALID_NAME") + "\n" + _function(LIST_JS, "isOpenableProjectName"))
        .replace("__HANDLE_ACTION__", _member(HEADER_JS, "handleAction"))
    )


def test_the_button_opens_the_wiki_on_screen_from_both_views() -> None:
    """La wiki la sa la vista a schermo, e le due la tengono nello stesso campo."""
    _run_js(
        _press_harness(),
        """
        // Dal grafo. Il controller della wiki porta un'altra wiki apposta: se il
        // tasto leggesse quello, aprirebbe la conversazione sbagliata.
        makeApp('graph', { graphWiki: 'patreon', wikiWiki: 'salute' });
        header.handleAction('open-project-chat');
        await settle();
        assert.deepEqual(log, [['switchMode', 'chat'], ['select', 'project', 'patreon']]);

        // Dalla pagina wiki, e stavolta è il grafo a portare il residuo.
        makeApp('wiki', { graphWiki: 'patreon', wikiWiki: 'salute' });
        header.handleAction('open-project-chat');
        await settle();
        assert.deepEqual(log, [['switchMode', 'chat'], ['select', 'project', 'salute']]);
        """,
    )


def test_the_view_moves_before_the_scope() -> None:
    """Invertire l'ordine ricarica (o riscrive) la vista che si sta lasciando.

    `select` pubblica l'aggancio, e i due ascoltatori di `pinnedWiki` si
    riagganciano **solo se sono la vista a schermo**: pubblicarlo da dentro la
    sezione wiki significa un `loadGraph` intero buttato via un istante dopo, o
    la pagina che si sta leggendo sostituita dall'indice del progetto un attimo
    prima di lasciarla — e con lei `lastWikiPage`, che è dove si torna.
    """
    _run_js(
        _press_harness(),
        """
        makeApp('graph', { graphWiki: 'patreon' });
        header.handleAction('open-project-chat');
        await settle();
        assert.equal(log[0][0], 'switchMode', 'lo scope è cambiato mentre la wiki era ancora a schermo');
        """,
    )


def test_the_scope_waits_for_the_chat_to_be_ready() -> None:
    """Alla prima apertura la chat *nasce* in questo `switchMode`.

    `scopeChip.onSwitch` lo installa il controller della chat dopo
    `sessionManager.init()`, che è asincrono: un `select()` sincrono cambierebbe
    l'etichetta del chip e nient'altro — non c'è ancora nessuno che ascolti — e
    il caricamento della conversazione personale, arrivando dopo, rimetterebbe
    il chip com'era con la propria `syncFromSession`. Il tasto portava in chat,
    ma nella chat sbagliata: visto sul telefono il 01/09, prima apertura dopo un
    riavvio dell'app.
    """
    _run_js(
        _press_harness(),
        """
        let ready;
        const pending = new Promise((resolve) => { ready = resolve; });
        makeApp('graph', { graphWiki: 'patreon', chatReady: pending });

        header.handleAction('open-project-chat');
        await settle();
        assert.deepEqual(log, [['switchMode', 'chat']],
                         'lo scope è cambiato prima che la chat potesse ascoltarlo');

        ready();
        await settle();
        assert.deepEqual(log, [['switchMode', 'chat'], ['select', 'project', 'patreon']]);
        """,
    )


def test_a_navigation_that_did_not_land_leaves_the_scope_alone() -> None:
    """Il primo avvio dirotta ogni navigazione sull'onboarding.

    Cambiare scope lì significherebbe una conversazione-progetto aperta sotto
    una schermata di setup, e il chip che nomina un progetto appena l'utente ne
    esce. Stessa verifica di `openChat`, che per questo ritorna un booleano.
    """
    _run_js(
        _press_harness(),
        """
        makeApp('graph', { graphWiki: 'patreon', blocked: true });
        header.handleAction('open-project-chat');
        await settle();
        assert.deepEqual(log, [['switchMode', 'chat']]);
        """,
    )


def test_nothing_happens_without_a_wiki_or_on_a_name_the_server_refuses() -> None:
    """La Home non ha un progetto da aprire, e «Ricerca ETF» ne aprirebbe un altro.

    Il controllo è **qui** e non solo dove il tasto si accende: fra l'accensione
    e la pressione la vista può essere cambiata sotto (un cambio progetto, un
    link a un'altra wiki), e questo è l'ultimo punto prima di cambiare
    conversazione.
    """
    _run_js(
        _press_harness(),
        """
        // Grafo home: i nodi sono le wiki, non c'è una wiki corrente.
        makeApp('graph', { graphWiki: null });
        header.handleAction('open-project-chat');
        await settle();
        assert.deepEqual(log, [], 'la Home ha scelto un progetto per conto dell\\'utente');

        // Indice delle wiki: stessa cosa dal lato pagina.
        makeApp('wiki', { wikiWiki: null });
        header.handleAction('open-project-chat');
        await settle();
        assert.deepEqual(log, []);

        // I nomi che `_collect_projects` mette in `unopenable`, e i due che la
        // regex non vede da sé.
        for (const name of ['Ricerca ETF', '.nascosta', 'a..b', 'x'.repeat(65)]) {
          makeApp('wiki', { wikiWiki: name });
          header.handleAction('open-project-chat');
          await settle();
          assert.deepEqual(log, [], 'aperto ' + JSON.stringify(name));
        }
        """,
    )


# ── L'accensione ──────────────────────────────────────────────────────────

_SYNC_HARNESS = """
import assert from 'node:assert/strict';

__IS_OPENABLE__

class Header {
  constructor(mode) {
    this.currentMode = mode;
    // Il bottone nasce spento: `renderActions` lo disegna con `display:none`.
    this.btn = { style: { display: 'none' } };
    this.actionsEl = {
      querySelector: (sel) => (sel.includes('open-project-chat') ? this.btn : null),
    };
  }
__SHOW_ACTION__
__HIDE_ACTION__
}

class Graph {
  constructor(wiki) { this.currentWiki = wiki; }
__GRAPH_SYNC__
}

class Wiki {
  constructor(wiki, isHome = false) { this.currentWiki = wiki; this.isHome = isHome; }
__WIKI_SYNC__
}

/** Monta un header nella modalità *mode* e ritorna il display del tasto dopo
 *  che la vista ha detto la sua. */
function afterSync(mode, view, arg) {
  const header = new Header(mode);
  global.window = { mobileApp: { header } };
  view._syncProjectAction(arg);
  return header.btn.style.display;
}
"""


def _sync_harness() -> str:
    return (
        _SYNC_HARNESS.replace("__IS_OPENABLE__", _const(LIST_JS, "VALID_NAME") + "\n" + _function(LIST_JS, "isOpenableProjectName"))
        .replace("__SHOW_ACTION__", _member(HEADER_JS, "showAction"))
        .replace("__HIDE_ACTION__", _member(HEADER_JS, "hideAction"))
        .replace("__GRAPH_SYNC__", _member(GRAPH_JS, "_syncProjectAction"))
        .replace("__WIKI_SYNC__", _member(WIKI_JS, "_syncProjectAction"))
    )


def test_the_button_lights_up_only_where_there_is_a_project_to_open() -> None:
    """Le stesse due condizioni, dal lato dell'accensione."""
    _run_js(
        _sync_harness(),
        """
        // Grafo di una wiki apribile.
        assert.equal(afterSync('graph', new Graph('patreon'), 'patreon'), '');
        // Grafo home: nessuna wiki.
        assert.equal(afterSync('graph', new Graph(null), null), 'none');
        // Cartella che il server non aprirebbe.
        assert.equal(afterSync('graph', new Graph('Ricerca ETF'), 'Ricerca ETF'), 'none');

        // Pagina di una wiki apribile.
        assert.equal(afterSync('wiki', new Wiki('salute')), '');
        // Indice delle wiki: `isHome` vince sul residuo di `currentWiki`, che è
        // quel che `activate()` incontra rientrando su una Home già disegnata.
        assert.equal(afterSync('wiki', new Wiki('salute', true)), 'none');
        assert.equal(afterSync('wiki', new Wiki(null)), 'none');
        assert.equal(afterSync('wiki', new Wiki('Ricerca ETF')), 'none');
        """,
    )


def test_a_late_load_does_not_light_up_another_view_header() -> None:
    """`actionsEl` è il mount della vista **a schermo**, non di chi lo chiama.

    Un caricamento lento della sezione che si sta lasciando riprende dopo il
    cambio: senza `ownerMode` accenderebbe (o spegnerebbe) un bottone
    nell'header di destinazione. È la stessa guardia di `setTitle`, che esiste
    per lo stesso motivo.
    """
    _run_js(
        _sync_harness(),
        """
        // Il grafo finisce di caricare quando a schermo c'è già il workspace.
        assert.equal(afterSync('workspace', new Graph('patreon'), 'patreon'), 'none',
                     'il grafo ha acceso un tasto nell\\'header di un\\'altra vista');
        // E il verso opposto: chi spegne non deve spegnere in casa d'altri.
        const header = new Header('workspace');
        header.btn.style.display = '';
        global.window = { mobileApp: { header } };
        new Wiki(null)._syncProjectAction();
        assert.equal(header.btn.style.display, '');
        """,
    )


# ── Il contorno: il tasto nasce spento, e parla due lingue ────────────────


def test_the_button_is_born_off_in_both_views() -> None:
    """Acceso di default comparirebbe sulla Home, dove non porta da nessuna parte.

    Il momento è quello: `setMode` ridisegna le azioni a ogni cambio vista, e
    fra quel disegno e il primo `_syncProjectAction` c'è un fotogramma.
    """
    src = _src(HEADER_JS)
    factory = re.search(r"(?ms)^function projectChatAction\(\) \{.*?^\}$", src)
    assert factory, "projectChatAction non trovata"
    assert "hidden: true" in factory.group(0), "il tasto nasce acceso"
    assert "action: 'open-project-chat'" in factory.group(0)

    for mode in ("wiki", "graph"):
        block = re.search(rf"(?ms)^      {mode}: \{{.*?^      \}},$", src)
        assert block, f"la configurazione di {mode} non è dove ci si aspetta"
        assert "projectChatAction()" in block.group(0), (
            f"la vista {mode} non offre la strada verso la chat del progetto"
        )


def test_the_button_label_exists_in_both_locales() -> None:
    """Una chiave a metà stampa `header.openProjectChat` sul bottone."""
    for locale in ("it", "en"):
        strings = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        label = strings.get("header", {}).get("openProjectChat")
        assert label, f"header.openProjectChat manca in {locale}.json"
