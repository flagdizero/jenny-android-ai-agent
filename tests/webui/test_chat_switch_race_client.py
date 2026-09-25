"""Un cambio di conversazione scavalcato deve perdere, sempre.

Aprire una conversazione dura: `_switchConversation` sposta `currentKey` subito,
poi *aspetta* — `api.bootstrap()`, la fetch del thread. Le attese non sono in
fila (il latch `_initialHistoryLoaded` lo azzera `invalidateHistory()`, quindi il
secondo tap non è bloccato, e la prima fetch non viene annullata da nessuno), e
la vecchia `loadThread` scriveva lo scope su un campo condiviso: vinceva **chi
rispondeva per ultimo**, non chi era stato toccato per ultimo.

Il caso concreto: tap su `patreon`, tap su `bordi` 200 ms dopo, e se `patreon`
risponde per seconda il chip dice `patreon` mentre i messaggi vanno in `bordi`.
Da lì l'utente enuncia un fatto credendo di essere in un progetto e il fatto
finisce nel diario dell'altro, dove il gardener lo promuove in pagina: durevole
e non ritirabile, cioè l'unico guasto irrecuperabile che quel modulo si impegna
a non fare.

Servono due metà e qui si misurano entrambe: la **generazione** letta prima
delle attese, che ferma il disegno scaduto, e lo **scope restituito** da
`loadThread`, che toglie al chip la dipendenza dal campo condiviso.

I metodi si estraggono dal sorgente e si eseguono in node su doppi minimi, come
in ``test_live_turn_boundary_client.py`` e ``test_chat_scope_client.py``: la
WebUI non ha un runner con DOM, ma queste funzioni ne toccano solo
`chatArea.innerHTML`, che qui è una proprietà con setter — così "il DOM buttato"
è osservabile. La rete è a mano: ogni fetch resta appesa finché il test non la
risolve, che è l'unico modo di far arrivare due risposte nell'ordine sbagliato.
In coda due asserzioni sul solo testo del sorgente, dichiarate deboli.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
SESSION_JS = ASSETS / "shared" / "session-manager.js"
PAGER_JS = ASSETS / "shared" / "history-pager.js"


pytestmark = requires_node


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


_HARNESS = """
import assert from 'node:assert/strict';

/* Cursore e chiavistello della paginazione stanno nel modulo condiviso: qui si
   importa quello vero, cosi' la guardia contro il cambio di conversazione si
   misura sull'intera catena (pager -> `_beginHistoryPage`
   -> `loadThread`) invece che su un ritaglio di testo. */
globalThis.document = {
  createElement() {
    return { className: '', textContent: '', type: '', disabled: false,
             addEventListener() {}, remove() {} };
  },
};
const { HistoryPager } = await import('__PAGER_URL__');

// ── Doppi: tutto ciò che i metodi veri chiamano fuori da sé ─────────────────
const UNIFIED_KEY = 'websocket:default';
const chatIdOf = (key) => (key && key.startsWith('websocket:') ? key.substring(10) : key);
const wsManager = { attachChat() {}, detachChat() {} };
const scopeChip = { scope: undefined, syncFromSession(s) { this.scope = s; } };
const writeSwitch = { key: undefined, syncFromSession(k) { this.key = k; } };
const console = { error() {} };

/* La rete a mano: `fetchWebuiThread` non risolve da sé. Due risposte nell'ordine
   sbagliato sono il difetto, e con una fetch vera non si ordinano. */
const inflight = [];
const api = {
  bootstrap: () => Promise.resolve(),
  getBootstrapInfo: () => null,
  fetchWebuiThread(key, opts) {
    let settle;
    const promise = new Promise((resolve, reject) => { settle = { resolve, reject }; });
    inflight.push({ key, opts, resolve: settle.resolve, reject: settle.reject });
    return promise;
  },
};

class SessionManager extends EventTarget {
  __CTOR__
  get personalKey() { return UNIFIED_KEY; }
  __SWITCH_TO__
  __GENERATION__
  __LOAD_THREAD__
}

const sessionManager = new SessionManager();

function makeChat() {
  const chat = {
    rendered: [],
    identityEl: null,
    _initialHistoryLoaded: false,
    _loadingInitialHistory: false,
    _resetStreamState() {},
    _clearGoalBanner() {},
    /* La riga «storia non caricata» non è oggetto di questi test: qui interessa
       *chi* disegna, non cosa. Che la riga compaia solo per la conversazione
       aperta — cioè che non litighi con la generazione — sta in
       `test_history_load_failure_client.py`, che ne esegue il corpo vero. */
    _showHistoryError() {},
    _clearHistoryError() {},
    _ensureIdentity() {},
    /* Il bottone «mostra la conversazione precedente» non è oggetto di questi
       test, e il metodo vero misura l'altezza di un contenitore che qui non
       esiste. Ha un test suo: `test_history_reach_client.py`. */
    _ensureHistoryReach() {},
    _initRuntimeModelFromBootstrap() {},
    scrollToBottom() {},
    _renderThreadMessages(msgs) { chat.rendered = msgs.map((m) => m.text); },
    _renderThreadMessagesToTop(msgs) {
      chat.rendered = msgs.map((m) => m.text).concat(chat.rendered);
    },
    __INVALIDATE__,
    __LOAD_INITIAL__,
    __BEGIN_PAGE__,
    __SWITCH_CONVERSATION__,
  };
  // Il DOM ridotto all'osso: svuotarlo si vede, ed è ciò che `invalidateHistory`
  // fa e che un caricamento scaduto non deve poter riempire.
  // Lo scroller è il documento (`_scroller` in mobile-chat.js), non l'area.
  chat._scroller = { scrollHeight: 0, scrollTop: 0 };
  chat.chatArea = {
    get innerHTML() { return chat.rendered.join('\\n'); },
    set innerHTML(v) { if (v === '') chat.rendered = []; },
    querySelector() { return null; },
  };
  chat._pager = new HistoryPager({
    scroller: () => chat._scroller,
    listenOn: { addEventListener() {} },
    container: () => chat.chatArea,
    pageSize: 120,
    begin: () => chat._beginHistoryPage(),
    prepend: (msgs) => chat._renderThreadMessagesToTop(msgs),
    mount() {},
    label: () => 'i18n:chat.loadPrevious',
  });
  /* Gli stessi nomi di prima: li legge il resync e li scrivono i test. */
  Object.defineProperties(chat, {
    historyCursor: {
      get: () => chat._pager.cursor,
      set: (v) => { chat._pager.cursor = v || null; },
    },
    hasMoreHistory: {
      get: () => chat._pager.hasMore,
      set: (v) => { chat._pager.hasMore = !!v; },
    },
    isLoadingHistory: { get: () => chat._pager.loading },
  });
  return chat;
}

// Un giro di event loop: svuota tutte le microtask pendenti.
const tick = () => new Promise((r) => setTimeout(r, 0));

function thread(name, text, page = {}) {
  return {
    messages: [{ role: 'assistant', text }],
    page,
    workspace_scope: name ? { project_path: '/w/projects/' + name } : null,
  };
}

// La fetch in volo per una chiave, togliendola dalla lista.
function pending(key) {
  const i = inflight.findIndex((f) => f.key === key);
  assert.notEqual(i, -1, 'nessuna fetch in volo per ' + key);
  return inflight.splice(i, 1)[0];
}
"""


def _harness() -> str:
    chat = _read(CHAT_JS)
    session = _read(SESSION_JS)
    return (
        _HARNESS.replace("__CTOR__", member(session, "constructor"))
        .replace("__SWITCH_TO__", member(session, "switchTo"))
        .replace("__GENERATION__", member(session, "switchGeneration"))
        .replace("__LOAD_THREAD__", member(session, "loadThread"))
        .replace("__INVALIDATE__", member(chat, "invalidateHistory"))
        .replace("__LOAD_INITIAL__", member(chat, "loadInitialHistory"))
        .replace("__BEGIN_PAGE__", member(chat, "_beginHistoryPage"))
        .replace("__PAGER_URL__", PAGER_JS.as_uri())
        .replace("__SWITCH_CONVERSATION__", member(chat, "_switchConversation"))
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    run_js(source)


# ── La corsa ────────────────────────────────────────────────────────────────


def test_the_overtaken_switch_loses_even_when_it_answers_last() -> None:
    """Il difetto, per intero: due tap ravvicinati e la risposta del primo che
    arriva per ultima. Chip, interruttore di sola lettura, bolle e campo
    condiviso devono nominare tutti e quattro la conversazione toccata per
    ultima — perché è quella a cui il prossimo messaggio andrà."""
    _run_js("""
      const chat = makeChat();
      // Si parte dalla personale già a schermo.
      const boot = chat.loadInitialHistory();
      await tick();
      pending('websocket:default').resolve(thread(null, 'ciao'));
      await boot;

      // Tap su patreon, tap su bordi: il secondo non aspetta il primo, e non
      // deve — un tap che resta senza risposta per una fetch è il difetto
      // opposto.
      const first = chat._switchConversation('project:patreon');
      await tick();
      const second = chat._switchConversation('project:bordi');
      await tick();

      assert.equal(inflight.length, 2, 'i due caricamenti si sovrappongono: è il caso');
      // Ogni caricamento chiede la *sua* chiave, non quella corrente al momento
      // dell'attesa.
      assert.deepEqual(inflight.map((f) => f.key), ['project:patreon', 'project:bordi']);

      // bordi risponde, poi patreon: lo scavalcato risponde per ultimo.
      pending('project:bordi').resolve(thread('bordi', 'da bordi', { before_cursor: 'b-1' }));
      await tick();
      pending('project:patreon').resolve(thread('patreon', 'da patreon', { before_cursor: 'p-1' }));
      await tick();
      await Promise.all([first, second]);

      assert.equal(sessionManager.currentKey, 'project:bordi');
      assert.deepEqual(chat.rendered, ['da bordi'],
                       'il thread scavalcato si è dipinto sopra quello aperto');
      assert.equal(scopeChip.scope?.project_path, '/w/projects/bordi',
                   'il chip nomina un progetto diverso da quello che riceve i messaggi');
      assert.equal(writeSwitch.key, 'project:bordi',
                   "l'interruttore di sola lettura è per conversazione: seguirebbe l'altra");
      assert.equal(sessionManager.currentScope?.project_path, '/w/projects/bordi',
                   'il campo condiviso è quel che legge il popover Info sessione');
      assert.equal(chat.historyCursor, 'b-1',
                   'il cursore di paginazione è dell\\'altra conversation');
    """)


def test_three_taps_and_scrambled_answers_still_leave_the_last_one() -> None:
    """Non è una corsa a due: la regola è «solo l'ultimo tap disegna», qualunque
    sia l'ordine delle risposte."""
    _run_js("""
      const chat = makeChat();
      const a = chat._switchConversation('project:uno');
      await tick();
      const b = chat._switchConversation('project:due');
      await tick();
      const c = chat._switchConversation('project:tre');
      await tick();

      pending('project:tre').resolve(thread('tre', 'da tre', { before_cursor: 't-1' }));
      await tick();
      pending('project:uno').resolve(thread('uno', 'da uno'));
      await tick();
      pending('project:due').resolve(thread('due', 'da due'));
      await tick();
      await Promise.all([a, b, c]);

      assert.equal(sessionManager.currentKey, 'project:tre');
      assert.deepEqual(chat.rendered, ['da tre']);
      assert.equal(scopeChip.scope?.project_path, '/w/projects/tre');
      assert.equal(sessionManager.currentScope?.project_path, '/w/projects/tre');
      assert.equal(chat.historyCursor, 't-1');
    """)


def test_the_chip_takes_the_scope_from_the_answer_not_from_the_shared_field() -> None:
    """La seconda metà, isolata.

    La generazione da sola non basterebbe: `currentScope` lo scrive chiunque
    carichi un thread, e basta un altro scrittore — oggi il popover, domani una
    vista qualsiasi — per rimettere sul chip il nome sbagliato. Qui il campo è
    avvelenato da un getter mentre la risposta è in volo: il chip deve mostrare
    ciò che *questa* richiesta ha ricevuto."""
    _run_js("""
      const chat = makeChat();
      const load = chat._switchConversation('project:bordi');
      await tick();
      Object.defineProperty(sessionManager, 'currentScope', {
        configurable: true,
        get() { return { project_path: '/w/projects/veleno' }; },
        set(v) {},
      });
      pending('project:bordi').resolve(thread('bordi', 'da bordi'));
      await load;
      assert.equal(scopeChip.scope?.project_path, '/w/projects/bordi',
                   'il chip legge ancora il campo condiviso invece del valore di ritorno');
    """)


def test_a_superseded_failure_does_not_steal_the_load_latch() -> None:
    """Anche il fallimento appartiene a una conversazione sola.

    Il `catch` riapre `_initialHistoryLoaded` perché un caricamento fallito va
    ritentato — ma se lo riapre quello scavalcato, il caricamento buono perde il
    latch che si era preso, e il prossimo `activate()` rifà tutto sopra un
    thread già a schermo."""
    _run_js("""
      const chat = makeChat();
      const first = chat._switchConversation('project:patreon');
      await tick();
      const second = chat._switchConversation('project:bordi');
      await tick();

      pending('project:bordi').resolve(thread('bordi', 'da bordi'));
      await tick();
      pending('project:patreon').reject(new Error('rete'));
      await tick();
      await Promise.all([first, second]);

      assert.equal(chat._initialHistoryLoaded, true,
                   'il fallimento scavalcato ha riaperto il latch del caricamento buono');
      assert.deepEqual(chat.rendered, ['da bordi']);
      // E il latch tiene: nessuna seconda fetch.
      await chat.loadInitialHistory();
      assert.equal(inflight.length, 0);
    """)


def test_a_page_of_old_history_is_not_pasted_onto_another_conversation() -> None:
    """Anche la paginazione indietro è un'attesa, e nessuno la annulla: un cambio
    di chat a metà scroll incollava in cima al thread nuovo i messaggi vecchi di
    quello lasciato."""
    _run_js("""
      const chat = makeChat();
      chat._initialHistoryLoaded = true;
      chat.historyCursor = 'c-1';
      chat.rendered = ['recente'];

      // Il percorso vero: lo scroll infinito chiama il pager (`bindInfiniteScroll`).
      const more = chat._pager.loadMore();
      await tick();
      assert.deepEqual(inflight.map((f) => f.key), ['websocket:default']);

      // L'utente cambia progetto mentre la pagina vecchia è in volo.
      const sw = chat._switchConversation('project:bordi');
      await tick();
      pending('websocket:default').resolve(thread(null, 'vecchio della personale'));
      await more;
      assert.equal(chat.rendered.includes('vecchio della personale'), false,
                   'una pagina di un\\'altra conversation è stata incollata in cima');
      assert.equal(chat.isLoadingHistory, false,
                   'il finally deve sbloccare la paginazione anche uscendo prima');

      pending('project:bordi').resolve(thread('bordi', 'da bordi'));
      await sw;
      assert.deepEqual(chat.rendered, ['da bordi']);
    """)


def test_a_switch_to_the_same_conversation_is_not_a_switch() -> None:
    """La generazione sale solo su un cambio vero.

    Se salisse su un no-op, un secondo tap sul progetto già aperto invaliderebbe
    il caricamento in corso — e `_switchConversation` esce subito su `false`,
    quindi nessuno ne farebbe partire un altro: la vista resterebbe vuota."""
    _run_js("""
      assert.equal(sessionManager.switchGeneration, 0);
      assert.equal(sessionManager.switchTo('project:bordi'), true);
      assert.equal(sessionManager.switchGeneration, 1);
      assert.equal(sessionManager.switchTo('project:bordi'), false,
                   'la stessa chiave non è un cambio');
      assert.equal(sessionManager.switchGeneration, 1,
                   'un no-op non deve invalidare il caricamento in corso');
      assert.equal(sessionManager.switchTo(null), true);
      assert.equal(sessionManager.currentKey, 'websocket:default');
      assert.equal(sessionManager.switchGeneration, 2);
    """)


def test_a_thread_answered_after_a_switch_never_reaches_the_shared_state() -> None:
    """`loadThread` da sola, senza la vista: la risposta scaduta si dichiara
    `stale` e non scrive né lo scope né il run in corso."""
    _run_js("""
      const load = sessionManager.loadThread('project:patreon', 160);
      await tick();
      sessionManager.switchTo('project:bordi');
      pending('project:patreon').resolve({
        messages: [], page: {},
        workspace_scope: { project_path: '/w/projects/patreon' },
        run_started_at: 111,
      });
      const out = await load;
      assert.equal(out.stale, true);
      assert.equal(out.scope?.project_path, '/w/projects/patreon',
                   'il valore di ritorno resta quello di questa richiesta');
      assert.equal(sessionManager.currentScope, null,
                   'una risposta scaduta ha scritto lo scope condiviso');
      assert.equal(sessionManager.runStartedAt, null);

      // La risposta ancora attuale, invece, lo scrive.
      const fresh = sessionManager.loadThread('project:bordi', 160);
      await tick();
      pending('project:bordi').resolve({
        messages: [], page: {},
        workspace_scope: { project_path: '/w/projects/bordi' },
        run_started_at: 222,
      });
      const ok = await fresh;
      assert.equal(ok.stale, false);
      assert.equal(sessionManager.currentScope?.project_path, '/w/projects/bordi');
      assert.equal(sessionManager.runStartedAt, 222);
    """)


# ── Guardie sul testo del sorgente ──────────────────────────────────────────
#
# Deboli per costruzione: leggono il sorgente, non lo eseguono, quindi provano
# che una riga c'è e non che faccia effetto. Il comportamento sta sopra; queste
# due coprono le cose che l'esecuzione non distingue.


def test_the_generation_is_read_once_before_the_awaits() -> None:
    """Guardia debole: la generazione va letta *prima* della prima attesa.

    Rileggerla dopo darebbe sempre uguale — la guardia diventerebbe un no-op
    silenzioso, e nessuno dei test qui sopra fallirebbe in modo diverso da come
    fallisce ora, quindi vale scriverlo.
    """
    body = re.search(
        r"\n  async loadInitialHistory\(\) \{(.*?)\n  \}", _read(CHAT_JS), re.S
    )
    assert body, "loadInitialHistory non trovato"
    src = body.group(1)
    first_read = src.find("sessionManager.switchGeneration")
    first_await = src.find("await ")
    assert first_read != -1, "loadInitialHistory non legge la generazione del cambio"
    assert first_read < first_await, (
        "la generazione letta dopo un'attesa combacia sempre: la guardia non guarda niente"
    )
    # E la chiave si prende una volta sola, prima delle attese: dopo, `currentKey`
    # è già quella della conversazione nuova.
    assert "sessionManager.currentKey" not in src[first_await:], (
        "rileggere currentKey dopo un'attesa vuol dire chiedere una conversazione "
        "e mostrarla come se fosse un'altra"
    )
    # Una guardia per ogni attesa, più quella del catch.
    assert src.count("await ") == 2, "attese cambiate: rivedere le guardie una per una"
    assert src.count("superseded()") >= 3, (
        "ogni attesa (e il catch) deve avere la sua guardia"
    )


def test_the_shared_scope_field_has_a_single_writer() -> None:
    """Guardia debole: `currentScope` è lo stato condiviso che questo difetto
    aveva sporcato. Chi lo scrive lo fa dentro `loadThread`, sotto la guardia
    della generazione; un nuovo scrittore altrove rimetterebbe in piedi la
    stessa corsa da un'altra porta."""
    for path in sorted(ASSETS.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        src = _read(path)
        if path == SESSION_JS:
            continue
        assert not re.search(r"currentScope\s*=[^=]", src), (
            f"{path.name} scrive sessionManager.currentScope: lo scope di una "
            "conversazione deve arrivare dal valore di ritorno di loadThread"
        )
    session = _read(SESSION_JS)
    body = re.search(r"\n  async loadThread\(.*?\n  \}", session, re.S)
    assert body, "loadThread non trovato"
    assert "if (known && !stale)" in body.group(0), (
        "loadThread deve scrivere lo stato condiviso solo se la risposta è ancora attuale"
    )
