"""Un caricamento di storia fallito deve dirlo, e deve poter riprovare.

Il caso è quello normale su un telefono, non un bordo: si apre l'app (o si tocca
un progetto) mentre il gateway sta ancora salendo, la fetch del thread parte e
fallisce. Prima di questa correzione seguivano due cose, entrambe mute:

1. **Niente a schermo.** `loadInitialHistory` scriveva in console e usciva. La
   chat restava vuota *mentre il chip nominava un progetto*, cioè identica a un
   progetto senza storia. Da lì si scrive credendo di ripartire da zero in un
   posto che invece ha un passato — che l'agente rileggerà, perché la sessione
   lato gateway c'è comunque.
2. **Nessun recupero.** Il latch `_initialHistoryLoaded` torna giù sul
   fallimento, e `_resyncThreadAfterReconnect` — l'unica cosa che riprova
   davvero, ed è agganciata a `chat:open`, cioè al momento in cui il gateway c'è
   — usciva subito proprio su quel `!_initialHistoryLoaded`. La riconnessione,
   il solo istante utile per ritentare, era il solo istante in cui non ritentava.
   Si riprendeva solo uscendo dalla vista e rientrando (`activate()`).

Al posto di quel latch la guardia chiede quel che voleva chiedere davvero — *c'è
una fetch in volo?* (`_loadingInitialHistory`) — e questo chiude un difetto che
il latch **non** copriva: con il latch alzato dal primo istante, un `chat:open`
arrivato a caricamento in volo passava e faceva partire una seconda fetch sulla
stessa chiave; nessuna delle due scaduta (la generazione non cambia), quindi
entrambe disegnavano e il thread compariva due volte.

I metodi si estraggono dal sorgente e si eseguono in node su doppi minimi, come
in ``test_chat_switch_race_client.py``: la rete è a mano, così un fallimento e
una risposta buona possono arrivare nell'ordine che serve. Il DOM è ridotto ai
due gesti che questi metodi fanno davvero — accodare un nodo e cercarlo per
classe — così "la riga d'errore c'è / non c'è / non è raddoppiata" è osservabile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
SESSION_JS = ASSETS / "shared" / "session-manager.js"
PAGER_JS = ASSETS / "shared" / "history-pager.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _member(source: str, name: str) -> str:
    """Il testo vero di un membro, dalla dichiarazione alla chiusura a due spazi."""
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


_HARNESS = """
import assert from 'node:assert/strict';

/* La paginazione vive nel modulo condiviso: il caricamento iniziale registra li'
   dove e' arrivato (`adopt`) e `invalidateHistory` lo riazzera, quindi il finto
   monta quello vero invece di tre campi sciolti. */
globalThis.document = globalThis.document || {
  createElement() {
    return { className: '', textContent: '', type: '', disabled: false,
             addEventListener() {}, remove() {} };
  },
};
const { HistoryPager } = await import('__PAGER_URL__');

// ── Doppi ───────────────────────────────────────────────────────────────────
const UNIFIED_KEY = 'websocket:default';
const chatIdOf = (key) => (key && key.startsWith('websocket:') ? key.substring(10) : key);
const wsManager = { attachChat() {}, detachChat() {} };
const scopeChip = { scope: undefined, syncFromSession(s) { this.scope = s; } };
const writeSwitch = { syncFromSession() {} };
const console = { error() {} };
// Nessuna stringa a mano: quel che finisce nel nodo deve venire da i18n, e il
// prefisso rende visibile *quale* chiave.
const i18n = { t: (key) => 'i18n:' + key };

/* Il DOM ridotto ai due gesti che i metodi veri fanno: accodare un nodo a
   `chatArea` e ripescarlo per classe. `nodes` è la lista dei figli. */
let nodes = [];
const document = {
  createElement() {
    const el = { className: '', textContent: '' };
    el.remove = () => {
      const i = nodes.indexOf(el);
      if (i !== -1) nodes.splice(i, 1);
    };
    return el;
  },
};
const withClass = (cls) =>
  nodes.filter((n) => String(n.className).split(/\\s+/).includes(cls));

/* La rete a mano: nessuna fetch risolve da sé. Un fallimento e una risposta
   buona in un ordine preciso sono il caso da misurare. */
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
  nodes = [];
  const chat = {
    rendered: [],
    identityEl: null,
    _initialHistoryLoaded: false,
    _loadingInitialHistory: false,
    _resyncingThread: false,
    _autoScroll: true,
    _resetStreamState() {},
    _clearGoalBanner() {},
    _ensureIdentity() {},
    /* Il bottone «mostra la conversazione precedente» non è oggetto di questi
       test, e il metodo vero misura l'altezza di un contenitore che qui non
       esiste. Ha un test suo: `test_history_reach_client.py`. */
    _ensureHistoryReach() {},
    _initRuntimeModelFromBootstrap() {},
    scrollToBottom() {},
    _renderThreadMessages(msgs) { chat.rendered = msgs.map((m) => m.text); },
    __INVALIDATE__,
    __LOAD_INITIAL__,
    __SHOW_ERROR__,
    __CLEAR_ERROR__,
    __RESYNC__,
    __SWITCH_CONVERSATION__,
  };
  chat.chatArea = {
    scrollHeight: 0,
    scrollTop: 0,
    appendChild(el) { nodes.push(el); return el; },
    querySelectorAll(selector) { return withClass(selector.replace('.', '')); },
    querySelector(selector) { return withClass(selector.replace('.', ''))[0] || null; },
    get innerHTML() { return chat.rendered.join('\\n'); },
    set innerHTML(v) { if (v === '') { chat.rendered = []; nodes = []; } },
  };
  chat._scroller = { scrollHeight: 2000, clientHeight: 400, scrollTop: 0 };
  chat._pager = new HistoryPager({
    scroller: () => chat._scroller,
    listenOn: { addEventListener() {} },
    container: () => chat.chatArea,
    pageSize: 120,
    begin: () => null,
    prepend() {},
    mount() {},
    label: () => 'i18n:chat.loadPrevious',
  });
  /* Gli stessi nomi di prima: `isLoadingHistory` lo legge il resync, e i test
     leggono il cursore per dire dove il caricamento e' arrivato. */
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

const tick = () => new Promise((r) => setTimeout(r, 0));

function thread(name, text, page = {}) {
  return {
    messages: [{ role: 'assistant', text }],
    page,
    workspace_scope: name ? { project_path: '/w/wikis/' + name } : null,
  };
}

function pending(key) {
  const i = inflight.findIndex((f) => f.key === key);
  assert.notEqual(i, -1, 'nessuna fetch in volo per ' + key);
  return inflight.splice(i, 1)[0];
}

// Le righe d'errore attualmente a schermo.
const errorRows = () => withClass('chat-history-error');
"""


def _harness() -> str:
    chat = _read(CHAT_JS)
    session = _read(SESSION_JS)
    return (
        _HARNESS.replace("__CTOR__", _member(session, "constructor"))
        .replace("__SWITCH_TO__", _member(session, "switchTo"))
        .replace("__GENERATION__", _member(session, "switchGeneration"))
        .replace("__LOAD_THREAD__", _member(session, "loadThread"))
        .replace("__INVALIDATE__", _member(chat, "invalidateHistory"))
        .replace("__LOAD_INITIAL__", _member(chat, "loadInitialHistory"))
        .replace("__SHOW_ERROR__", _member(chat, "_showHistoryError"))
        .replace("__CLEAR_ERROR__", _member(chat, "_clearHistoryError"))
        .replace("__RESYNC__", _member(chat, "_resyncThreadAfterReconnect"))
        .replace("__SWITCH_CONVERSATION__", _member(chat, "_switchConversation"))
        .replace("__PAGER_URL__", PAGER_JS.as_uri())
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    run_js(source)


# ── 1. Il fallimento si vede ────────────────────────────────────────────────


def test_a_failed_load_leaves_a_row_instead_of_an_empty_chat() -> None:
    """Il difetto principale: chip su un progetto, chat vuota, nessun errore."""
    _run_js("""
      const chat = makeChat();
      const load = chat._switchConversation('project:bordi');
      await tick();
      pending('project:bordi').reject(new Error('gateway ancora giù'));
      await load;

      assert.equal(errorRows().length, 1,
                   'un caricamento fallito non ha detto niente a schermo');
      assert.equal(errorRows()[0].textContent, 'i18n:chat.historyLoadFailed',
                   'la stringa deve venire da i18n, non dal codice');
      assert.equal(chat._initialHistoryLoaded, false,
                   'il latch deve restare aperto: il caricamento va ritentato');
      assert.equal(chat._loadingInitialHistory, false,
                   'il finally deve chiudere il volo anche sul fallimento');
    """)


def test_two_failures_do_not_stack_two_identical_rows() -> None:
    """Riconnessioni a raffica su un gateway che non sale: una riga, non dieci."""
    _run_js("""
      const chat = makeChat();
      const first = chat._switchConversation('project:bordi');
      await tick();
      pending('project:bordi').reject(new Error('giù'));
      await first;

      const again = chat._resyncThreadAfterReconnect();
      await tick();
      pending('project:bordi').reject(new Error('ancora giù'));
      await again;

      assert.equal(errorRows().length, 1, 'le righe d\\'errore si impilano');
    """)


# ── 2. Il recupero alla riconnessione ───────────────────────────────────────


def test_the_reconnect_resync_retries_a_load_that_never_landed() -> None:
    """La riconnessione è il momento in cui il gateway c'è: deve ritentare.

    Con la vecchia guardia `!_initialHistoryLoaded` questo resync uscìva subito
    e la chat restava vuota fino a un cambio di vista.
    """
    _run_js("""
      const chat = makeChat();
      const load = chat._switchConversation('project:bordi');
      await tick();
      pending('project:bordi').reject(new Error('giù'));
      await load;
      assert.equal(errorRows().length, 1);

      // chat:open → resync.
      const retry = chat._resyncThreadAfterReconnect();
      await tick();
      assert.equal(inflight.length, 1, 'la riconnessione non ha ritentato niente');
      pending('project:bordi').resolve(thread('bordi', 'da bordi', { before_cursor: 'b-1' }));
      await retry;

      assert.deepEqual(chat.rendered, ['da bordi']);
      assert.equal(errorRows().length, 0,
                   'la riga d\\'errore deve sparire quando la storia arriva');
      assert.equal(chat._initialHistoryLoaded, true);
      assert.equal(chat.historyCursor, 'b-1');
    """)


def test_a_successful_load_clears_a_row_left_by_a_previous_failure() -> None:
    """`_renderThreadMessages` accoda: senza la pulizia la riga resta in cima.

    Percorso `activate()`, che non passa da `invalidateHistory()` e quindi non
    svuota il contenitore.
    """
    _run_js("""
      const chat = makeChat();
      const load = chat.loadInitialHistory();
      await tick();
      pending('websocket:default').reject(new Error('giù'));
      await load;
      assert.equal(errorRows().length, 1);

      // activate(): il latch è aperto, quindi ricarica senza invalidare.
      const again = chat.loadInitialHistory();
      await tick();
      pending('websocket:default').resolve(thread(null, 'ciao'));
      await again;

      assert.deepEqual(chat.rendered, ['ciao']);
      assert.equal(errorRows().length, 0,
                   'la riga d\\'errore è rimasta sopra un thread che c\\'è');
    """)


# ── 3. La riga non litiga con la generazione ────────────────────────────────


def test_a_superseded_failure_paints_no_error_row() -> None:
    """Cambiare progetto in fretta non deve dipingere errori altrui.

    Il fallimento della conversazione lasciata arriva dopo che quella nuova si è
    già disegnata: la riga comparirebbe sopra un thread a cui non appartiene, e
    accuserebbe di un guasto una conversazione che sta benissimo. La guardia è la
    stessa che protegge il latch, e sta *prima* della riga.
    """
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

      assert.deepEqual(chat.rendered, ['da bordi']);
      assert.equal(errorRows().length, 0,
                   'un errore scaduto è comparso sopra la conversazione aperta');
      // E il latch resta di chi ha caricato bene (regressione di T5.2).
      assert.equal(chat._initialHistoryLoaded, true);
    """)


def test_the_open_conversation_still_gets_its_own_error() -> None:
    """Il rovescio: se è la conversazione *aperta* a fallire, la riga si vede —
    anche se un'altra, lasciata prima, era andata a buon fine."""
    _run_js("""
      const chat = makeChat();
      const first = chat._switchConversation('project:patreon');
      await tick();
      const second = chat._switchConversation('project:bordi');
      await tick();

      pending('project:patreon').resolve(thread('patreon', 'da patreon'));
      await tick();
      pending('project:bordi').reject(new Error('rete'));
      await tick();
      await Promise.all([first, second]);

      assert.deepEqual(chat.rendered, [],
                       'il thread scavalcato si è disegnato comunque');
      assert.equal(errorRows().length, 1,
                   'la conversazione aperta ha fallito e non lo ha detto');
      assert.equal(chat._initialHistoryLoaded, false);
    """)


# ── 4. Il doppio disegno che il latch non copriva ───────────────────────────


def test_the_resync_does_not_double_render_a_load_in_flight() -> None:
    """`chat:open` a caricamento in volo: una fetch, non due.

    Questo era già rotto prima — il latch è alzato dal primo istante, quindi la
    vecchia guardia lasciava passare il resync — e rendere il resync
    raggiungibile in più situazioni lo avrebbe reso più frequente. La guardia
    nuova (`_loadingInitialHistory`) chiude entrambi i casi.
    """
    _run_js("""
      const chat = makeChat();
      const load = chat.loadInitialHistory();
      await tick();
      assert.equal(inflight.length, 1);

      // Il socket si riapre mentre la storia è in volo.
      await chat._resyncThreadAfterReconnect();
      assert.equal(inflight.length, 1,
                   'il resync ha fatto partire una seconda fetch sulla stessa chiave');

      pending('websocket:default').resolve(thread(null, 'ciao'));
      await load;
      assert.deepEqual(chat.rendered, ['ciao'],
                       'il thread è stato disegnato due volte');
      assert.equal(chat._loadingInitialHistory, false);
    """)


def test_the_resync_still_refuses_to_run_twice_at_once() -> None:
    """La guardia vecchia sul rientro resta: due `chat:open` ravvicinati."""
    _run_js("""
      const chat = makeChat();
      const boot = chat.loadInitialHistory();
      await tick();
      pending('websocket:default').resolve(thread(null, 'ciao'));
      await boot;

      const a = chat._resyncThreadAfterReconnect();
      await tick();
      const b = chat._resyncThreadAfterReconnect();
      await tick();
      assert.equal(inflight.length, 1, 'due resync sovrapposti');
      pending('websocket:default').resolve(thread(null, 'ciao'));
      await Promise.all([a, b]);
      assert.deepEqual(chat.rendered, ['ciao']);
    """)


def test_the_resync_still_respects_the_reading_position() -> None:
    """`_autoScroll` resta il gate: chi sta leggendo la storia non la perde."""
    _run_js("""
      const chat = makeChat();
      const boot = chat.loadInitialHistory();
      await tick();
      pending('websocket:default').resolve(thread(null, 'ciao'));
      await boot;

      chat._autoScroll = false;
      await chat._resyncThreadAfterReconnect();
      assert.equal(inflight.length, 0, 'il resync ha buttato la pagina che si stava leggendo');
      assert.deepEqual(chat.rendered, ['ciao']);
    """)


# ── 5. La chiave della stringa esiste in tutt'e due le lingue ───────────────


def test_the_error_row_string_is_translated_in_both_locales() -> None:
    """Il test in node prova che il testo viene da ``i18n.t``; qui che la chiave
    che gli viene passata esista davvero — altrimenti a schermo finisce
    ``chat.historyLoadFailed``."""
    for locale in ("it", "en"):
        data = json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        assert "historyLoadFailed" in data["chat"], f"chiave mancante in {locale}.json"
        assert data["chat"]["historyLoadFailed"].strip()


# ── Guardie sul solo testo del sorgente ─────────────────────────────────────
#
# Deboli per costruzione: provano che una riga c'è, non che faccia effetto.


def test_the_row_is_shown_after_the_generation_guard_not_before() -> None:
    """Ordine dentro il `catch`: prima la guardia, poi la riga.

    Il test eseguito sopra lo misura, ma solo per la corsa che sa costruire.
    Questa è la regola, scritta dove si vede.
    """
    body = _member(_read(CHAT_JS), "loadInitialHistory")
    catch = re.search(r"\} catch \(err\) \{(.*?)\n    \} finally \{", body, re.S)
    assert catch is not None, "il catch di loadInitialHistory non è più riconoscibile"
    src = catch.group(1)
    assert "if (superseded()) return;" in src
    assert "this._showHistoryError();" in src
    assert src.index("if (superseded()) return;") < src.index("this._showHistoryError();")


def test_the_old_latch_gate_is_gone_from_the_resync() -> None:
    """La guardia rimossa non deve tornare: era il "non si riprende mai più"."""
    fn = re.search(
        r"async _resyncThreadAfterReconnect\(\)\s*\{(.*?)\n  \}", _read(CHAT_JS), re.S
    )
    assert fn is not None
    src = fn.group(1)
    assert "if (!this._initialHistoryLoaded) return;" not in src
    assert "if (this._loadingInitialHistory) return;" in src
