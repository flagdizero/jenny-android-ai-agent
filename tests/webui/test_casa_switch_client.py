"""Cambiare conversazione in casa: cosa succede davvero quando tocchi un quaderno.

Lo scambio in sé è una riga (`sessionManager.switchTo`). Qui si misura tutto il
resto — quel che smette di essere vero quando la casa non ha più una
conversazione sola: l'intestazione, il vuoto, l'invito del campo, la bozza a
metà, e il turno che stava girando nella conversazione che stai lasciando.

I metodi si ritagliano dal sorgente e si eseguono in node, come gli altri banchi
della casa. `conversation-list.js` e `dotColor` si importano **veri**: la forma
della chiave e il colore del pallino sono proprio le due cose che qui non devono
essere ricostruite a mano, o il banco misurerebbe la propria copia.

Il `sessionManager` invece è finto, ma con la sua regola che conta: `switchTo`
torna `false` quando la conversazione è già quella. Il legame fra `chat:switch`
e `_releaseTurn` non si può esercitare di qui — lo lega `init()` — e ha il suo
controllo in `test_casa_who_contract.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "casa-app.js"
CREATE_JS = ASSETS / "shared" / "project-create.js"
WHO_JS = ASSETS / "casa-who.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


def _const_block(source: str, name: str) -> str:
    """Una costante di modulo su più righe, presa dal sorgente e non riscritta."""
    m = re.search(rf"(?ms)^(?:export )?const {re.escape(name)} = \{{.*?^\}};$", source)
    assert m, f"const {name} non trovata"
    return m.group(0).removeprefix("export ")


_HARNESS = """
import assert from 'node:assert/strict';

const { projectKey, projectNameOf } = await import('__LIST_URL__');

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = {
  locale: 'it',
  translations: TRANSLATIONS,
  __T__
};

function makeEl(tag) {
  return {
    tag,
    className: '',
    textContent: '',
    value: '',
    placeholder: '',
    hidden: false,
    style: {},
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    blur() {},
  };
}

/* La lente ingrandita, quando c'è. */
let lightbox = null;
/* Un elemento per id, **lo stesso** a ogni domanda: il titolo della tendina
   (`#casa-who`) si spegne con `disabled`, e un finto che ne dava uno nuovo
   ogni volta non poteva dire se l'avevano spento. */
const perId = new Map();
const document = {
  querySelector: (sel) => (sel === '.image-lightbox' ? lightbox : null),
  getElementById: (id) => {
    if (!perId.has(id)) perId.set(id, makeEl('button'));
    return perId.get(id);
  },
  /* `_setView` dichiara il pavimento di Jenny quando non c'è un composer: la
     radice serve solo a ricevere quella proprietà, e il banco la legge. */
  documentElement: {
    style: {
      props: {},
      setProperty(k, v) { this.props[k] = v; },
      removeProperty(k) { delete this.props[k]; },
    },
  },
};

/* Il guscio nativo, finto: conta le volte che la casa gli dice «la chat e' a
   schermo» — e' quel che cancella gli avvisi letti. */
globalThis.window = { JennyNative: { aperte: 0, chatOpened() { this.aperte += 1; } } };

/* `/api/settings`: un payload solo, che il guscio chiede una volta e divide
   fra le due stanze. Qui interessa **quante volte** viene chiesto, e cosa
   succede quando non arriva. */
let settingsPayload = { version: { current: '0.11.0' }, floating: { available: true } };
let settingsCalls = 0;
const api = {
  clientLog() {},
  getSettings() {
    settingsCalls += 1;
    if (!settingsPayload) return Promise.reject(new Error('impostazioni non lette'));
    return Promise.resolve(settingsPayload);
  },
};

/* Finto, ma con la regola che conta: chi è già lì non cambia conversazione. */
const sessionManager = {
  personalKey: 'websocket:default',
  currentKey: 'websocket:default',
  switchTo(key) {
    if (key === this.currentKey) return false;
    this.currentKey = key;
    return true;
  },
};

__DOT_COLOR__
__FLOOR__
__BACK_TO__

/* I due vocabolari, presi dai sorgenti: quello dell'officina e quello di casa,
   che dal primo eredita tutto quel che non dice «progetto». */
__PROJECT_WORDS__
__NOTEBOOK_WORDS__

/* Il giro di creazione è esercitato dal suo banco; qui si misura l'aggancio —
   con quali parole viene chiamato, e cosa succede dopo. */
let createOutcome = null;
const creations = [];
function createProjectFlow(spec) {
  creations.push(spec);
  return Promise.resolve(createOutcome);
}

class App {
  constructor() {
    this.nameEl = makeEl('span');
    this.input = makeEl('textarea');
    this.emptyText = makeEl('div');
    this.send = makeEl('button');
    this.attach = makeEl('button');
    this.wire = makeEl('div');
    /* L'intestazione che cambia stanza, e le stanze stesse. */
    this.shell = makeEl('main');
    this.shell.setAttribute = (k, v) => { this.shell.attrs[k] = v; };
    this.pagesBtn = makeEl('button');
    this.pagesCount = makeEl('span');
    this.backBtn = makeEl('button');
    this.backLabel = makeEl('span');
    this.talkBtn = makeEl('button');
    this.talkLabel = makeEl('span');
    /* Le due stanze nuove sono moduli loro, coi loro banchi: qui interessa
       che il guscio le apra, e cosa ci mette dentro di quel che sa. */
    this.versioni = [];
    this.flottanti = [];
    this.tu = {
      applyTranslations: () => {},
      open: () => this.fatti.push('tu aperta'),
      sayUpdates: (v) => this.versioni.push(v),
      sayBackup: (v) => { this.valoreBackup = v; },
      sayJenny: (v) => { this.valoreJenny = v; },
      sayModel: (v) => { this.valoreModello = v; },
    };
    /* Il nome di Jenny viaggia con lo stesso payload della finestra
       flottante: una lettura sola per due campi della stessa stanza. */
    this.nomi = [];
    this.jennyRoom = {
      applyTranslations: () => {},
      open: () => this.fatti.push('jenny aperta'),
      setFloating: (v) => this.flottanti.push(v),
      setName: (v) => this.nomi.push(v),
      value: () => 'piccola',
    };
    this.impostazioniDate = [];
    this.versioniDate = [];
    this.backupDati = [];
    this.backupRoom = {
      applyTranslations: () => {},
      open: () => this.fatti.push('backup aperta'),
      setBackup: (v) => this.backupDati.push(v),
      value: () => 'mai fatto',
    };
    this.updatesRoom = {
      applyTranslations: () => {},
      open: () => this.fatti.push('aggiornamenti aperta'),
      close: () => this.fatti.push('aggiornamenti chiusa'),
      setVersion: (v) => this.versioniDate.push(v),
      value: () => '0.11.0',
    };
    this.modelRoom = {
      applyTranslations: () => {},
      open: () => this.fatti.push('modello aperta'),
      setSettings: (v) => this.impostazioniDate.push(v),
      value: () => 'OpenCode',
    };
    this.view = 'chat';
    this._jennyWasOut = true;
    this.map = null;
    this._measureFloor = () => this.fatti.push('pavimento rimisurato');
    this.pages = {
      applyTranslations: () => {},
      load: (name) => { this.fatti.push('pagine:' + name); return Promise.resolve(); },
    };
    this.files = { count: 0 };
    this._personalName = 'Jenny';
    this._drafts = new Map();
    this._threadFailed = false;
    this._running = false;
    /* Cosa è successo, in ordine. */
    this.fatti = [];
    this.reloadFails = false;
    this.chat = {
      reload: async () => {
        if (this.reloadFails) throw new Error('thread giù');
        this.fatti.push('riletto:' + sessionManager.currentKey);
      },
      scrollToBottom: () => this.fatti.push('in fondo'),
      keepBottom: () => {},
    };
    this.activity = { stop: () => this.fatti.push('riga ferma') };
    this.jenny = {
      el: { classList: { contains: () => this._jennyOut } },
      setOut: (v) => { this._jennyOut = v; this.fatti.push('fuori:' + v); },
    };
    this._jennyOut = true;
    this.who = {
      known: [{ name: 'piante', modified: 1 }],
      pagesOf: (name) => Promise.resolve(this.pageCounts?.[name] ?? null),
      invalidate: () => this.fatti.push('elenco da rileggere'),
      render: () => {},
    };
    /* La fila in alto: qui interessa solo la modalita' ordina, che Indietro
       chiude senza salvare. Il suo disegno ha il banco suo. */
    this.fila = {
      ordinando: false,
      disegna: () => {},
      chiudiOrdina: () => { this.fila.ordinando = false; this.fatti.push('ordina chiusa'); },
    };
    /* La pista, finta ma con la regola che conta: **la chat puo' stare
       ovunque**, e chi cambia pagina lo fa per nome. Arrivare su Impostazioni
       la accende — e' quel che fa il gancio vero — e la promessa si tiene da
       parte, perche' la lettura del server finisce dopo. Le conversazioni
       passano dritte al corpo del cambio: la regola di dove aprirle ha il suo
       banco (`test_casa_pista_client.py`). */
    const app = this;
    this.pagine = {
      ordine: ['app', 'chat', 'quaderni', 'impostazioni'],
      indice: 1,
      conversazioneCasa: null,
      get indiceChat() { return this.ordine.indexOf('chat'); },
      get corrente() { return this.ordine[this.indice]; },
      indiceDi(id) { return this.ordine.indexOf(id); },
      vaiA(i) {
        this.indice = i;
        app.fatti.push('pagina:' + this.ordine[i]);
        if (this.ordine[i] === 'impostazioni') app.accensione = app._apriImpostazioni();
      },
      vaiAId(id) { this.vaiA(this.indiceDi(id)); },
      apriConversazione: (k) => this.mostraConversazione(k),
    };
  }
  _autosize() {}
  _renderPending() {}
  _setRunning(running) { this._running = running; this.fatti.push('ferma:' + running); }
  _showThreadError() { this._threadFailed = true; this.fatti.push('non si legge'); }
  __SWITCH__
  __MOSTRA__
  __APPLY_CONVERSATION__
  __RELEASE_TURN__
  __CLOSE_OVERLAYS__
  __CLOSE_ALL_OVERLAYS__
  __HAS_OVERLAY_ABOVE__
  __BACK__
  __GO_HOME__
  __OPEN_CHAT__
  __APPLY_TRANSLATIONS__
  __CREATE_NOTEBOOK__
  __OPEN_PAGES__
  __GO_BACK_ONE_ROOM__
  __APRI_IMPOSTAZIONI__
  __NOME_CHAT__
  __HA_COMPOSER__
  __POSA_JENNY__
  __CHIEDI_NOMI_APP__
  __OPEN_JENNY__
  __OPEN_UPDATES__
  __ASK_SETTINGS__
  __SET_VIEW__
  __APPLY_HEAD__
  __APPLY_BACK_LABEL__
  __UPDATE_PAGES_COUNT__
  __SET_HEAD_TITLE__
  __ON_PAGINA__
  __IS_CHAT_ON_SCREEN__
  __SEGNALA_CHAT__
}

function casa() {
  lightbox = null;
  createOutcome = null;
  creations.length = 0;
  settingsPayload = { version: { current: '0.11.0' }, floating: { available: true } };
  settingsCalls = 0;
  sessionManager.currentKey = sessionManager.personalKey;
  const app = new App();
  app._applyConversation();
  app.fatti.length = 0;
  window.JennyNative.aperte = 0;
  return app;
}
"""


def _harness() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__DOT_COLOR__", function(WHO_JS.read_text(encoding="utf-8"), "dotColor"))
        .replace("__SWITCH__", member(src, "switchConversation"))
        # Il corpo del cambio vive in `mostraConversazione` dal 23/09/2026:
        # `switchConversation` decide solo **dove** (v. le pagine conversazione),
        # e qui non c'e' una pista — quindi passa dritto al corpo, che e' la
        # cosa che questo banco misura.
        .replace("__MOSTRA__", member(src, "mostraConversazione"))
        .replace("__APPLY_CONVERSATION__", member(src, "_applyConversation"))
        .replace("__RELEASE_TURN__", member(src, "_releaseTurn"))
        .replace("__CLOSE_OVERLAYS__", member(src, "_closeOverlays"))
        .replace("__CLOSE_ALL_OVERLAYS__", member(src, "_closeAllOverlays"))
        .replace("__HAS_OVERLAY_ABOVE__", member(src, "hasOverlayAbove"))
        .replace("__BACK__", member(src, "handleHardwareBack"))
        .replace("__GO_HOME__", member(src, "goHome"))
        .replace("__OPEN_CHAT__", member(src, "openChat"))
        .replace("__APPLY_TRANSLATIONS__", member(src, "_applyTranslations"))
        .replace("__CREATE_NOTEBOOK__", member(src, "createNotebook"))
        .replace("__PROJECT_WORDS__", _const_block(_read_create(), "PROJECT_WORDS"))
        .replace("__NOTEBOOK_WORDS__", _const_block(src, "NOTEBOOK_WORDS"))
        .replace("__OPEN_PAGES__", member(src, "openPages"))
        .replace("__GO_BACK_ONE_ROOM__", member(src, "goBackOneRoom"))
        .replace("__APRI_IMPOSTAZIONI__", member(src, "_apriImpostazioni"))
        .replace("__NOME_CHAT__", member(src, "_nomeChat"))
        .replace("__HA_COMPOSER__", member(src, "_haComposer"))
        .replace("__POSA_JENNY__", member(src, "_posaJenny"))
        .replace("__CHIEDI_NOMI_APP__", member(src, "_chiediNomiApp"))
        .replace("__OPEN_JENNY__", member(src, "openJenny"))
        .replace("__OPEN_UPDATES__", member(src, "openUpdates"))
        .replace("__ASK_SETTINGS__", member(src, "_askSettings"))
        .replace("__APPLY_BACK_LABEL__", member(src, "_applyBackLabel"))
        .replace("__SET_VIEW__", member(src, "_setView"))
        .replace("__APPLY_HEAD__", member(src, "_applyHead"))
        .replace("__UPDATE_PAGES_COUNT__", member(src, "_updatePagesCount"))
        .replace("__SET_HEAD_TITLE__", member(src, "_setHeadTitle"))
        .replace("__ON_PAGINA__", member(src, "onPaginaCambiata"))
        .replace("__IS_CHAT_ON_SCREEN__", member(src, "isChatOnScreen"))
        .replace("__SEGNALA_CHAT__", member(src, "_segnalaChatAschermo"))
        .replace("__FLOOR__", _const_block_scalar(src, "FLOOR_NO_COMPOSER"))
        .replace("__BACK_TO__", _const_block(src, "BACK_TO"))
    )


def _const_block_scalar(source: str, name: str) -> str:
    m = re.search(rf"(?m)^const {re.escape(name)} = .+?;$", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


def _read_create() -> str:
    return CREATE_JS.read_text(encoding="utf-8")


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Lo scambio ──────────────────────────────────────────────────────────────


def test_opening_a_notebook_rereads_the_thread_of_that_notebook() -> None:
    """E lo rilegge **dopo** aver cambiato chiave: nell'ordine opposto
    rileggerebbe la conversazione che stai lasciando."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      assert.equal(sessionManager.currentKey, 'project:piante');
      assert.deepEqual(app.fatti, ['riletto:project:piante']);
    """)


def test_going_where_you_already_are_is_not_a_switch() -> None:
    """Toccare la riga su cui sei non deve ributtare giù la conversazione: un
    filo che si svuota e si ridisegna per niente sembra averti perso qualcosa."""
    _run_js("""
      const app = casa();
      await app.switchConversation(null);
      await app.switchConversation('websocket:default');
      assert.deepEqual(app.fatti, []);
    """)


def test_the_draft_stays_with_the_conversation_it_was_written_in() -> None:
    """Mezza frase scritta in casa non deve partire dentro il quaderno che apri
    subito dopo. È la stessa famiglia di guasto di `switchGeneration` — quel che
    dici finisce nel diario di un altro progetto — solo un attimo prima."""
    _run_js("""
      const app = casa();
      app.input.value = 'ricordami di annaffiare';
      await app.switchConversation(projectKey('piante'));
      assert.equal(app.input.value, '', 'la bozza ha seguito nel quaderno');

      app.input.value = 'le punte sono secche';
      await app.switchConversation(null);
      assert.equal(app.input.value, 'ricordami di annaffiare');

      await app.switchConversation(projectKey('piante'));
      assert.equal(app.input.value, 'le punte sono secche');
    """)


# ── Il nome nella fila ──────────────────────────────────────────────────────
#
# Dal 23/09/2026 il titolo «Jenny ⌄» non c'e' piu': la pagina chat si chiama,
# nella fila in alto, come la conversazione che mostra (v.
# `.agent/pagine-in-alto-plan.md`).


def test_the_chat_page_is_named_after_the_notebook_it_is_in() -> None:
    """Col pallino dei Quaderni: e' l'unica cosa che lega la riga toccata alla
    stanza in cui sei finito, e due colori diversi per lo stesso quaderno
    slegherebbero le due."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      assert.deepEqual(app._nomeChat(), { nome: 'piante', colore: dotColor('piante') });
      assert.equal(app.input.placeholder, 'Scrivi a Jenny, nel quaderno');
      assert.ok(app.emptyText.textContent.includes('resta qui'), app.emptyText.textContent);
    """)


def test_the_house_takes_its_own_name_back() -> None:
    """Il nome personale è messo da parte all'avvio, non riletto dalla testa:
    la testa delle stanze porta il nome del quaderno, e leggerlo di lì lo
    farebbe restare «piante» per sempre."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      await app.switchConversation(null);
      assert.deepEqual(app._nomeChat(), { nome: 'Jenny', colore: null },
                       'la casa non è un quaderno fra i quaderni');
      assert.equal(app.input.placeholder, 'Scrivi a Jenny');
    """)


def test_the_chat_page_keeps_its_own_name_while_it_is_lent_to_a_notebook_page() -> None:
    """Su una pagina quaderno appesa la chat e' in prestito: il nome che la
    fila scrive sulla pagina chat e' quello a cui tornerai, non quello a
    schermo."""
    _run_js("""
      const app = casa();
      app.pagine.conversazioneCasa = 'websocket:default';
      sessionManager.currentKey = projectKey('piante');
      assert.equal(app._nomeChat().nome, 'Jenny');
    """)


# ── Il turno che resta indietro ─────────────────────────────────────────────


def test_leaving_closes_the_turn_that_was_running() -> None:
    """Il `turn_end` del turno in volo arriverà a una conversazione che non
    guardiamo più e verrà scartato: la riga di lavoro resterebbe a girare, e il
    bottone a dire «ferma» senza niente da fermare.

    Jenny non e' in questo elenco perche' il turno lo lascia da se': ascolta lo
    stesso `chat:switch` (`_releaseTrackedTurn` in `shared/jenny-mascot.js`,
    provato in `test_chat_scope_client.py`)."""
    _run_js("""
      const app = casa();
      app._releaseTurn();
      assert.deepEqual(app.fatti, ['riga ferma', 'ferma:false']);
    """)


# ── Le vie di ritorno ───────────────────────────────────────────────────────


def test_back_from_a_notebook_is_the_front_door() -> None:
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      app.fatti.length = 0;
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


def test_back_at_the_root_still_does_nothing() -> None:
    """Questa app è il launcher del telefono: Indietro non deve mai chiudere il
    task, e nella conversazione personale non c'è niente sotto."""
    _run_js("""
      const app = casa();
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(app.fatti, []);
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


# Un foglio aperto con una pressione lunga: un `<dialog>` nel top layer.
_FOGLIO = """
      const foglio = document.getElementById('casa-quaderno-sheet');
      foglio.open = true;
      foglio.close = () => { foglio.open = false; app.fatti.push('foglio chiuso'); };
"""


def test_one_press_closes_one_thing() -> None:
    """Con la scheda di un quaderno aperta sopra la chat, Indietro chiude la
    scheda e basta: uscire anche dal quaderno farebbe sparire due cose per un
    gesto."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
    """ + _FOGLIO + """
      app.fatti.length = 0;
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(app.fatti, ['foglio chiuso']);
      assert.equal(sessionManager.currentKey, 'project:piante');
    """)


def test_home_means_the_personal_conversation() -> None:
    """«Sei a casa» torna a voler dire qualcosa il giorno in cui si può essere
    altrove."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
    """ + _FOGLIO + """
      app.fatti.length = 0;
      app.goHome();
      await new Promise((r) => setTimeout(r, 0));
      assert.ok(app.fatti.includes('foglio chiuso'));
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


def test_back_from_any_page_lands_on_the_chat_wherever_it_sits() -> None:
    """La chat si sposta come le altre: Indietro la cerca per nome, non va alla
    prima casella. E sulla chat Indietro e' la porta di casa, come sempre."""
    _run_js("""
      const app = casa();
      app.pagine.ordine = ['quaderni', 'app', 'impostazioni', 'chat'];
      app.pagine.indice = 0;
      app.handleHardwareBack();
      assert.equal(app.pagine.corrente, 'chat');
      app.fatti.length = 0;
      app.handleHardwareBack();
      assert.deepEqual(app.fatti, [], 'dalla chat personale Indietro ha fatto qualcosa');
    """)


def test_a_tapped_alert_opens_the_conversation_the_alert_is_in() -> None:
    """La copia websocket di un avviso proattivo va sempre alla personale: da
    dentro un quaderno, «porta in chat» senza tornare a casa aprirebbe la
    stanza in cui quell'avviso non c'è."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      app.openChat();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


# ── Quando la lettura non riesce ────────────────────────────────────────────


def test_a_switch_that_cannot_read_the_thread_says_so() -> None:
    """Una conversazione irraggiungibile e una conversazione vuota sono due cose
    diverse, e confonderle fa credere di aver perso tutto."""
    _run_js("""
      const app = casa();
      app.reloadFails = true;
      await app.switchConversation(projectKey('piante'));
      assert.ok(app.fatti.includes('non si legge'));
      assert.equal(app._threadFailed, true);
    """)


def test_a_reading_that_works_takes_the_error_back() -> None:
    """Se restasse, il vuoto di questa conversazione direbbe «non riesco a
    leggerla» di una storia appena letta."""
    _run_js("""
      const app = casa();
      app.reloadFails = true;
      await app.switchConversation(projectKey('piante'));
      app.reloadFails = false;
      await app.switchConversation(null);
      assert.equal(app._threadFailed, false);
      assert.ok(app.emptyText.textContent.includes('Comincia tu'), app.emptyText.textContent);
    """)


# ── Un quaderno nuovo ───────────────────────────────────────────────────────


def test_the_house_asks_in_its_own_words() -> None:
    """Il giro di creazione è uno solo e non sa come si chiami quel che crea:
    prende le chiavi da chi lo chiama. Qui si controlla che la casa gliene passi
    di sue **solo** dove l'officina direbbe «progetto» — e che la regola dei
    nomi resti una, citata da un punto solo."""
    _run_js("""
      const proprie = Object.keys(NOTEBOOK_WORDS)
        .filter((k) => NOTEBOOK_WORDS[k] !== PROJECT_WORDS[k]);
      assert.ok(proprie.length >= 6, 'la casa ha smesso di parlare come casa');
      for (const k of proprie) {
        assert.ok(NOTEBOOK_WORDS[k].startsWith('casa.'), k + ' non è una parola di casa');
      }
      assert.equal(NOTEBOOK_WORDS.invalidName, PROJECT_WORDS.invalidName,
                   'la regola dei nomi è stata copiata una seconda volta');
      for (const k of Object.keys(PROJECT_WORDS)) {
        assert.ok(NOTEBOOK_WORDS[k], 'manca un posto del vocabolario: ' + k);
      }
    """)


def test_a_notebook_created_is_a_notebook_you_are_in() -> None:
    """Aver dato un nome e scritto la riga di scope senza finire dentro
    lascerebbe a metà il gesto cominciato."""
    _run_js("""
      const app = casa();
      createOutcome = 'orto';
      await app.createNotebook();
      assert.equal(creations[0].words, NOTEBOOK_WORDS);
      assert.deepEqual(creations[0].known, [{ name: 'piante', modified: 1 }],
                       'i nomi già noti non vengono dal pannello');
      assert.ok(app.fatti.includes('elenco da rileggere'), 'la cache è rimasta vecchia');
      assert.equal(sessionManager.currentKey, 'project:orto');
    """)


def test_a_creation_that_did_not_happen_opens_nothing() -> None:
    """Annullata, rifiutata o chiusa: il giro torna `null` in tutte le uscite
    che non hanno scritto su disco, e nessuna di quelle deve portare dentro un
    quaderno che non c'è."""
    _run_js("""
      const app = casa();
      createOutcome = null;
      await app.createNotebook();
      assert.equal(sessionManager.currentKey, 'websocket:default');
      assert.deepEqual(app.fatti, []);
    """)


# ── Le stanze ───────────────────────────────────────────────────────────────


def test_back_peels_one_room_at_a_time() -> None:
    """Lettore, pagine, chat, e solo allora si esce dal quaderno.

    Quattro pressioni per quattro cose. Se `goBackOneRoom` sparisse, dalle
    pagine un tocco solo farebbe sparire la stanza **e** il quaderno che la
    conteneva — due cose per un gesto, che e' esattamente quel che la stessa
    regola vieta alla tendina.
    """
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app.view = 'reader';
      app.shell.attrs['data-view'] = 'reader';

      app.handleHardwareBack();
      assert.equal(app.view, 'pages', 'dal lettore non si torna alle pagine');
      app.handleHardwareBack();
      assert.equal(app.view, 'chat', 'dalle pagine non si torna alla chat');
      assert.equal(sessionManager.currentKey, 'project:orto', 'e non si esce dal quaderno');
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'websocket:default', 'e adesso si esce');
    """)


def test_a_panel_over_the_pages_still_closes_first() -> None:
    """Gli strati vengono prima delle stanze: il foglio sta nel top layer, e
    chiuderlo e' quel che l'occhio si aspetta da quel tasto."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app._setView('pages');
    """ + _FOGLIO + """
      app.handleHardwareBack();
      assert.equal(foglio.open, false, 'il foglio non si e\\u2019 chiuso');
      assert.equal(app.view, 'pages', 'la stanza se n\\u2019e\\u2019 andata insieme a lui');
    """)


def test_leaving_the_chat_puts_jenny_away_and_coming_back_restores_her() -> None:
    """La tavola la disegna al bordo nelle pagine e fuori nella chat.

    Ma «fuori» al ritorno solo se era fuori quando sei uscito: metterla via e'
    una decisione dell'utente, e una stanza non la disfa.
    """
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));

      app._setView('pages');
      assert.equal(app._jennyOut, false, 'nelle pagine non si e\\u2019 messa via');
      app._setView('chat');
      assert.equal(app._jennyOut, true, 'tornando non e\\u2019 uscita');

      // Ora messa via a mano, dentro la chat.
      app._jennyOut = false;
      app._setView('pages');
      app._setView('chat');
      assert.equal(app._jennyOut, false, 'la stanza ha disfatto una scelta dell\\u2019utente');
    """)


def test_a_room_without_a_composer_declares_its_own_floor() -> None:
    """Il pavimento di Jenny e' il composer, e nelle pagine il composer non
    c'e': il suo `offsetHeight` la' e' zero, quindi il token va dichiarato o
    lei appoggia i piedi sul bordo dello schermo. Al ritorno si rimisura."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app.fatti.length = 0;

      app._setView('pages');
      assert.equal(
        document.documentElement.style.props['--casa-composer-h'],
        FLOOR_NO_COMPOSER + 'px',
        'le pagine non dichiarano il loro pavimento',
      );

      app._setView('chat');
      assert.ok(
        app.fatti.includes('pavimento rimisurato'),
        'tornando nella chat il composer non viene rimisurato',
      );
    """)


def test_the_pages_pill_only_exists_inside_a_notebook() -> None:
    """Nella conversazione personale non c'e' nessun quaderno da aprire, e una
    porta che non porta da nessuna parte e' peggio di nessuna porta."""
    _run_js("""
      const app = casa();
      assert.equal(app.pagesBtn.hidden, true, 'la pastiglia c\\u2019e\\u2019 anche a casa');
      await app.switchConversation(projectKey('orto'));
      assert.equal(app.pagesBtn.hidden, false, 'dentro un quaderno la pastiglia manca');
      await app.switchConversation(null);
      assert.equal(app.pagesBtn.hidden, true, 'tornando a casa la pastiglia resta');
    """)


def test_the_count_belongs_to_the_notebook_that_asked_for_it() -> None:
    """Fra la domanda e la risposta si puo' essere passati in un altro
    quaderno: scrivere li' il conteggio di quello di prima sarebbe un numero
    sbagliato su una stanza giusta."""
    _run_js("""
      const app = casa();
      app.pageCounts = { orto: 34, erbe: 1 };
      await app.switchConversation(projectKey('orto'));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.pagesCount.textContent, '34 pagine');

      // Una pagina sola non e' «1 pagine».
      await app.switchConversation(projectKey('erbe'));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.pagesCount.textContent, '1 pagina');

      // Chiesto per «orto», risposto mentre siamo in «erbe»: non si scrive.
      app.pagesCount.textContent = '';
      app._updatePagesCount('orto');
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.pagesCount.textContent, '', 'il conteggio di un\\u2019altra stanza');
    """)


def test_a_notebook_whose_count_is_unknown_still_has_its_door() -> None:
    """`null` e' «non lo so», e non si scrive. Ma la porta resta: un quaderno
    le pagine ce le ha comunque, e aspettare la cifra per mostrarla vorrebbe
    dire nascondere la porta a chi ha la rete lenta."""
    _run_js("""
      const app = casa();
      app.pageCounts = {};
      await app.switchConversation(projectKey('orto'));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.pagesCount.textContent, '');
      assert.equal(app.pagesBtn.hidden, false);
    """)


def test_switching_conversation_from_the_pages_comes_back_to_the_chat() -> None:
    """Le pagine parlano di *un* quaderno. Un avviso toccato porta nella
    conversazione personale: restare sull'elenco vorrebbe dire leggere le
    pagine di una stanza in cui non sei piu'."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app._setView('pages');
      app.openChat();
      assert.equal(app.view, 'chat');
      assert.equal(app.shell.attrs['data-view'], 'chat');
    """)


# ── La pagina Impostazioni: «Tu e Jenny» ────────────────────────────────────


def test_settings_is_a_page_and_back_from_it_is_the_chat() -> None:
    """«Tu e Jenny» non e' piu' una stanza sopra le pagine: e' la pagina
    Impostazioni (23/09/2026). Aprirla ci porta la pista, e Indietro riporta
    alla chat — **senza uscire dal quaderno**: tornare da una pagina di
    impostazioni non e' un modo di cambiare conversazione."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app.pagine.vaiAId('impostazioni');
      assert.equal(app.view, 'chat', 'le impostazioni sono ancora una stanza');
      assert.equal(app.pagine.corrente, 'impostazioni');
      assert.ok(app.fatti.includes('tu aperta'), 'la pagina non e\u2019 stata caricata');
      app.handleHardwareBack();
      assert.equal(app.pagine.corrente, 'chat');
      assert.equal(sessionManager.currentKey, 'project:orto', 'e il quaderno e\u2019 rimasto');
    """)


def test_the_eyelet_names_where_you_land() -> None:
    """L'occhiello dice dove si atterra, e le stanze non atterrano tutte nello
    stesso posto.

    Era una frase sola — «torna alla chat» — scritta in `_applyTranslations` e
    buona per tutte: vera dalle pagine, falsa dal lettore, che torna alle
    pagine. Con quattro stanze la parola giusta la decide la stessa tabella che
    decide il salto.
    """
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      const dice = (stanza) => { app._setView(stanza); return app.backLabel.textContent; };
      assert.equal(dice('pages'), i18n.t('casa.back.chat'));
      assert.equal(dice('reader'), i18n.t('casa.back.pages'), 'dal lettore si torna alle pagine');
      assert.equal(dice('jenny'), i18n.t('casa.back.impostazioni'), 'da lei si torna alle impostazioni');
      assert.notEqual(i18n.t('casa.back.pages'), i18n.t('casa.back.chat'),
                      'le due frasi sono diventate la stessa, e il banco non misura piu\u2019 niente');
    """)


def test_talking_about_it_belongs_to_a_notebook() -> None:
    """«Parlane» riporta a parlare *di questo quaderno*: nelle stanze delle
    impostazioni non c'e' niente di cui parlare, e il bottone non ci va.

    Era `hidden = inChat`, che con tre stanze diceva la stessa cosa.
    """
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app._setView('pages');
      assert.equal(app.talkBtn.hidden, false, 'dalle pagine si torna a parlarne');
      app._setView('jenny');
      assert.equal(app.talkBtn.hidden, true, '«Parlane» in mezzo alle impostazioni');
      assert.equal(app.nameEl.textContent, i18n.t('casa.jenny.title'), 'la testa non dice dove sei');
    """)


def test_the_settings_payload_is_asked_once_for_both_rooms() -> None:
    """`/api/settings` porta provider, contatori e lavoratori periodici, e di
    quel peso le due stanze leggono un campo per uno: la versione e lo stato
    della finestra flottante. Chiederlo due volte sarebbe due volte quel
    peso."""
    _run_js("""
      const app = casa();
      app.pagine.vaiAId('impostazioni');
      await app.accensione;
      await app.openJenny();
      app._setView('chat');
      app.pagine.vaiA(app.pagine.indiceChat);
      app.pagine.vaiAId('impostazioni');
      await app.accensione;
      assert.equal(settingsCalls, 1, 'il payload viene chiesto piu\u2019 di una volta');
      assert.deepEqual(app.versioniDate, [{ current: '0.11.0' }, { current: '0.11.0' }]);
      assert.deepEqual(app.flottanti, [{ available: true }, { available: true }]);
    """)


def test_a_settings_call_that_failed_is_tried_again() -> None:
    """Il fallimento non si ricorda. Una rete andata male una volta lascerebbe
    la riga della finestra flottante nascosta fino al riavvio della casa — e
    quella non e' una versione che manca, e' un'impostazione sparita."""
    _run_js("""
      const app = casa();
      settingsPayload = null;
      app.pagine.vaiAId('impostazioni');
      await app.accensione;
      assert.deepEqual(app.flottanti, [null], 'senza risposta la finestra resta sconosciuta');

      settingsPayload = { version: { current: '0.12.0' }, floating: { available: true } };
      app.pagine.vaiA(app.pagine.indiceChat);
      app.pagine.vaiAId('impostazioni');
      await app.accensione;
      assert.equal(settingsCalls, 2, 'il guscio si e\u2019 ricordato del fallimento');
      /* Anche il giro andato male passa dalla stanza: le dice «non lo so», e
         quella non scrive niente. */
      assert.deepEqual(app.versioniDate, [null, { current: '0.12.0' }]);
    """)


def test_her_room_hangs_off_you_and_jenny() -> None:
    """Indietro sbuccia una stanza per volta anche di qua: da lei si torna alla
    pagina Impostazioni, non alla chat — e da li', alla chat."""
    _run_js("""
      const app = casa();
      app.openJenny();
      assert.equal(app.view, 'jenny');
      assert.ok(app.fatti.includes('jenny aperta'));
      assert.equal(app.nameEl.textContent, i18n.t('casa.jenny.title'), 'la testa non dice dove sei');
      app.handleHardwareBack();
      assert.equal(app.view, 'chat');
      assert.equal(app.pagine.corrente, 'impostazioni');
      app.handleHardwareBack();
      assert.equal(app.pagine.corrente, 'chat');
    """)


def test_leaving_the_updates_room_stops_its_polling() -> None:
    """Un'installazione avviata va avanti per conto suo, ma il suo polling non
    deve tenere sveglia una stanza che non e' piu' a schermo — e rientrando si
    riaggancia da se'. Il guscio lo dice a **ogni** cambio di stanza, non solo
    tornando indietro: dalla chat, da un quaderno, da dove capita."""
    _run_js("""
      const app = casa();
      app.openUpdates();
      assert.equal(app.view, 'updates');
      assert.ok(app.fatti.includes('aggiornamenti aperta'));
      assert.ok(!app.fatti.includes('aggiornamenti chiusa'), 'chiusa appena aperta');

      app.goBackOneRoom();
      assert.equal(app.view, 'chat', 'da li si torna alle impostazioni');
      assert.equal(app.pagine.corrente, 'impostazioni');
      assert.ok(app.fatti.includes('aggiornamenti chiusa'), 'il polling resta vivo');

      /* E anche uscendo da un'altra parte: il guscio non sa da dove vieni. */
      app.fatti.length = 0;
      app.openUpdates();
      app._setView('chat');
      assert.ok(app.fatti.includes('aggiornamenti chiusa'));
    """)


# ── Dove appoggia Jenny, pagina per pagina (23/09/2026) ─────────────────────


def test_a_page_without_a_composer_puts_jenny_on_the_floor() -> None:
    """Il cassetto e le impostazioni non hanno un composer: misurarlo lo
    stesso — sta nella pagina accanto, alto quanto era — la terrebbe sospesa
    a mezz'aria sopra le righe. Una pagina quaderno il composer ce l'ha."""
    _run_js("""
      const app = casa();
      app.fatti.length = 0;
      app._voce = { id: 'app', kind: 'cassetto', fissa: true };
      app._posaJenny();
      assert.equal(document.documentElement.style.props['--casa-composer-h'], FLOOR_NO_COMPOSER + 'px');
      assert.ok(!app.fatti.includes('pavimento rimisurato'));
      app._voce = { id: 'q1', kind: 'conversazione', ref: 'project:piante' };
      app._posaJenny();
      assert.ok(app.fatti.includes('pavimento rimisurato'), 'una pagina quaderno ha il suo composer');
    """)


def test_every_switch_from_outside_asks_the_pages_where() -> None:
    """Titolo, Home, Indietro, un avviso: passano **tutti** dalla regola delle pagine.

    Una pagina quaderno mostra solo il suo quaderno, e a deciderlo e'
    `CasaPagine.apriConversazione`. Un guscio che cambiasse la chat per conto
    suo lascerebbe la personale dentro la pagina di «piante». E la personale si
    chiede con la sua chiave, non con `null`: le pagine confrontano chiavi.

    Ognuna delle quattro strade si esercita per conto suo: fino al 25/09/2026
    questo banco lo prometteva e chiamava solo `switchConversation`, quindi un
    `goHome` che avesse chiamato `mostraConversazione` direttamente sarebbe
    passato verde.
    """
    _run_js("""
      const app = casa();
      const chiesti = [];
      app.pagine = { apriConversazione: (k) => { chiesti.push(k); return Promise.resolve('instradata'); } };
      const r = await app.switchConversation(projectKey('piante'));
      assert.deepEqual(chiesti, ['project:piante']);
      assert.deepEqual(app.fatti, [], 'il guscio ha riletto il filo senza chiedere dove');
      assert.equal(r, 'instradata', 'la promessa delle pagine non torna a chi chiama');
      await app.switchConversation(null);
      assert.equal(chiesti[1], sessionManager.personalKey);

      /* Le pagine finte non cambiano conversazione: si resta in «piante», e
         ognuna delle tre strade deve chiedere la personale alle pagine. */
      sessionManager.currentKey = projectKey('piante');
      for (const [strada, fai] of [
        ['Home', () => app.goHome()],
        ['un avviso', () => app.openChat()],
        ['Indietro', () => app.handleHardwareBack()],
      ]) {
        chiesti.length = 0;
        app.fatti.length = 0;
        fai();
        await new Promise((r) => setTimeout(r, 0));
        assert.deepEqual(chiesti, [sessionManager.personalKey], strada + ': non ha chiesto alle pagine');
        assert.ok(!app.fatti.some((f) => f.startsWith('riletto:')),
                  strada + ': il guscio ha cambiato la chat per conto suo');
      }
    """)


# Un'app aperta sopra tutto, con due schermate interne: Indietro torna
# indietro *dentro* di lei, e solo `closeApp` la chiude davvero.
_APP_PROFONDA = """
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      const mini = { aperta: true, depth: 2, indietro: 0 };
      app._azioniApp = {
        get _openApp() { return mini.aperta ? mini : null; },
        isAppOpen() { return mini.aperta; },
        handleBack() {
          if (!mini.aperta) return false;
          if (mini.depth > 1) { mini.depth -= 1; mini.indietro += 1; return true; }
          mini.aperta = false;
          return true;
        },
        closeApp() { mini.aperta = false; },
      };
      app.fatti.length = 0;
"""


def test_home_closes_an_app_with_inner_screens_for_good() -> None:
    """Home e' un indirizzo, non Indietro: un'app con una schermata interna
    aperta tornava indietro dentro di se' e restava sopra la chat."""
    _run_js(_APP_PROFONDA + """
      app.goHome();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(mini.aperta, false, 'l\\u2019app e\\u2019 rimasta aperta sotto la chat');
      assert.equal(mini.indietro, 0, 'Home ha fatto Indietro dentro l\\u2019app');
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


def test_a_tapped_alert_closes_an_app_with_inner_screens_for_good() -> None:
    _run_js(_APP_PROFONDA + """
      app.openChat();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(mini.aperta, false, 'l\\u2019avviso ha lasciato l\\u2019app aperta sopra la chat');
      assert.equal(mini.indietro, 0);
    """)


def test_back_still_steps_inside_the_app_first() -> None:
    """Indietro resta un passo: una schermata interna dell'app, prima."""
    _run_js(_APP_PROFONDA + """
      app.handleHardwareBack();
      assert.equal(mini.aperta, true);
      assert.equal(mini.indietro, 1);
      assert.equal(sessionManager.currentKey, 'project:piante');
    """)


def test_the_chat_under_an_open_app_is_not_on_screen() -> None:
    """Gli avvisi si cancellano quando la chat si vede: con un'app aperta
    sopra, la chat c'e' ma non la stai guardando."""
    _run_js("""
      const app = casa();
      app.onPaginaCambiata(1, { id: 'chat', kind: 'chat', fissa: true });
      assert.equal(app.isChatOnScreen(), true);
      app._azioniApp = { _openApp: {}, isAppOpen: () => true };
      assert.equal(app.isChatOnScreen(), false, 'un\\u2019app aperta sopra la chat non la copre');
    """)


def test_back_peels_the_layers_of_the_app_page_one_at_a_time() -> None:
    """**Un foglio, poi l'app, poi la ricerca, poi la chat.**

    Visto sul telefono il 23/09/2026: Indietro chiudeva il cassetto *sotto* e
    lasciava sopra la chat il foglio di un'app, tasto Delete compreso. I fogli
    sono `<dialog>` nel top layer: vengono per primi. Poi un'app aperta a tutto
    schermo — fino a quel giorno la casa non la chiudeva mai. Poi la ricerca
    scritta nella pagina App, che si svuota invece di portarti via. E solo
    allora la pagina, che torna alla chat.
    """
    _run_js("""
      const app = casa();
      app._voce = { id: 'app', kind: 'cassetto', fissa: true };
      app.pagine.indice = app.pagine.indiceDi('app');
      let appAperta = true;
      app._azioniApp = { handleBack: () => { if (!appAperta) return false; appAperta = false; return true; } };
      app.launcher = { search: { value: 'tel' }, dismiss() { this.search.value = ''; } };
      for (const id of ['jenny-app-sheet', 'android-app-sheet']) {
        const foglio = document.getElementById(id);
        foglio.open = true;
        foglio.close = function () { this.open = false; };
        assert.equal(app._closeOverlays(), true);
        assert.equal(foglio.open, false, id + ': Indietro ha lasciato il foglio aperto');
        assert.equal(appAperta, true, id + ': ha chiuso l\u2019app sotto invece del foglio');
      }
      assert.equal(app._closeOverlays(), true);
      assert.equal(appAperta, false, 'Indietro non chiude l\u2019app aperta dal cassetto');
      assert.equal(app.launcher.search.value, 'tel', 'ha svuotato la ricerca insieme all\u2019app');
      assert.equal(app._closeOverlays(), true);
      assert.equal(app.launcher.search.value, '', 'la ricerca non si svuota');
      assert.equal(app._closeOverlays(), false);
      app.handleHardwareBack();
      assert.equal(app.pagine.corrente, 'chat');
    """)


def test_back_leaves_the_moving_mode_without_saving() -> None:
    """Salvare e' «Fatto». Indietro e' «lascia com'era»."""
    _run_js("""
      const app = casa();
      app.fila.ordinando = true;
      assert.equal(app._closeOverlays(), true);
      assert.deepEqual(app.fatti, ['ordina chiusa']);
    """)


def test_the_row_asks_for_the_app_names_once_and_only_the_light_list() -> None:
    """Lo slug non e' il nome: «todo» invece di «Todo» (telefono, 23/09/2026).
    Si chiede l'elenco delle Jenny App e basta — non quello delle app Android,
    che porta le icone — una volta, e solo se c'e' un'app appesa."""
    _run_js("""
      const app = casa();
      const chieste = [];
      let disegni = 0;
      app.fila.disegna = () => { disegni += 1; };
      app.appsSource = () => ({
        jennyApps: [],
        loadJennyApps: () => { chieste.push('jenny'); return Promise.resolve(); },
        ensureLoaded: () => chieste.push('tutto'),
      });
      app.pagine.schermate = [{ id: 'q1', kind: 'conversazione', ref: 'project:piante' }];
      app._chiediNomiApp();
      assert.deepEqual(chieste, [], 'senza app appese ha letto un elenco');
      app.pagine.schermate.push({ id: 'p1', kind: 'app', ref: 'todo' });
      app._chiediNomiApp();
      app._chiediNomiApp();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(chieste, ['jenny']);
      assert.equal(disegni, 1, 'la fila non si e ridisegnata coi nomi');
    """)


# ── Gli avvisi letti si cancellano ───────────────────────────────────────────
#
# Il guscio nativo chiede `isChatOnScreen()` al rientro in primo piano, e la
# casa gli dice `chatOpened()` quando la chat arriva a schermo da dentro la
# WebView. Fino al 24/09/2026 la casa non aveva ne' l'una ne' l'altra cosa, e
# gli avvisi in coda non si cancellavano mai.

CHAT = "{ id: 'chat', kind: 'chat', fissa: true }"
CASSETTO = "{ id: 'app', kind: 'cassetto', fissa: true }"


def test_boot_says_the_chat_is_not_on_screen_yet() -> None:
    """Prima che la pista dica dove sei, la risposta e' no: nel dubbio un avviso
    resta, che e' la direzione d'errore giusta."""
    _run_js("""
      const app = casa();
      assert.equal(app.isChatOnScreen(), false);
      assert.equal(window.JennyNative.aperte, 0);
    """)


def test_arriving_on_the_chat_page_clears_the_alerts() -> None:
    _run_js(f"""
      const app = casa();
      app.onPaginaCambiata(1, {CHAT});
      assert.equal(app.isChatOnScreen(), true);
      assert.equal(window.JennyNative.aperte, 1);
    """)


def test_another_page_is_not_the_chat() -> None:
    _run_js(f"""
      const app = casa();
      app.onPaginaCambiata(0, {CASSETTO});
      assert.equal(app.isChatOnScreen(), false);
      assert.equal(window.JennyNative.aperte, 0);
    """)


def test_the_chat_page_on_a_notebook_is_not_where_alerts_are() -> None:
    """Gli avvisi proattivi arrivano nella conversazione personale: la pagina
    chat su un quaderno non li mostra. Tornando alla personale, si'."""
    _run_js(f"""
      const app = casa();
      app.onPaginaCambiata(1, {CHAT});
      window.JennyNative.aperte = 0;
      await app.mostraConversazione(projectKey('piante'));
      assert.equal(app.isChatOnScreen(), false);
      assert.equal(window.JennyNative.aperte, 0);
      await app.mostraConversazione(null);
      assert.equal(app.isChatOnScreen(), true);
      assert.equal(window.JennyNative.aperte, 1);
    """)


def test_a_room_over_the_chat_hides_it_and_coming_back_clears() -> None:
    _run_js(f"""
      const app = casa();
      app.onPaginaCambiata(1, {CHAT});
      app._setView('jenny');
      window.JennyNative.aperte = 0;
      assert.equal(app.isChatOnScreen(), false);
      app._setView('chat');
      assert.equal(app.isChatOnScreen(), true);
      assert.equal(window.JennyNative.aperte, 1);
    """)
