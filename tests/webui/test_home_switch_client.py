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
controllo in `test_home_who_contract.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "home-app.js"
SPINES_JS = ASSETS / "shared" / "spines.js"
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

const { projectKey, projectNameOf, isOpenableProjectName } = await import('__LIST_URL__');

const TRANSLATIONS = __TRANSLATIONS__;
/* Il nome di lei (`shared/bot-name.js`): qui quello di partenza. */
const botName = { get: () => 'Jenny', set() {}, onChange() { return () => {}; } };
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
    style: {
      setProperty(k, v) { this[k] = v; },
      removeProperty(k) { delete this[k]; },
    },
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    blur() {},
    /* Un dialogo condiviso si congeda con `cancel`: nessuno lo rifiuta. */
    dispatchEvent() { return true; },
  };
}

/* La lente ingrandita, quando c'è. */
let lightbox = null;
/* Un elemento per id, **lo stesso** a ogni domanda: il titolo della tendina
   (`#home-who`) si spegne con `disabled`, e un finto che ne dava uno nuovo
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
globalThis.window = { JennyNative: {
  opened: 0, closed: 0, openedWith: [],
  chatOpened(key) { this.opened += 1; this.openedWith.push(key ?? null); },
  chatClosed() { this.closed += 1; },
} };

/* `/api/settings`: un payload solo, che il guscio chiede una volta e divide
   fra le due stanze. Qui interessa **quante volte** viene chiesto, e cosa
   succede quando non arriva. */
let settingsPayload = { version: { current: '0.11.0' }, floating: { available: true } };
let settingsCalls = 0;
/* Il posto salvato (`_restorePlace`): nessuno, a meno che il test lo metta. */
const PLACE_KEY = 'home-place';
const PLACE_MAX_AGE_MS = 60 * 60 * 1000;
let savedPlace = null;
const readStorage = () => savedPlace;

/* I quaderni che `/api/projects` elenca; `null` = la lettura non riesce. */
let notebooksOnDisk = null;
const api = {
  clientLog() {},
  listProjects() {
    if (!notebooksOnDisk) return Promise.reject(new Error('elenco non letto'));
    return Promise.resolve({ projects: notebooksOnDisk.map((name) => ({ name })) });
  },
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

/* Rinominare e cancellare un quaderno: le domande hanno i loro banchi, qui
   si misura il seguito. `writtenName` e' la risposta al prompt, `confirmDelete`
   quella della conferma. */
let writtenName = null;
let confirmDelete = true;
const promptDialog = () => Promise.resolve(writtenName);
const deleteProjectFlow = () => Promise.resolve(confirmDelete);
/* Il saluto di un quaderno nuovo: chi l'ha chiesto, e in che ordine rispetto
   alla bolla «sta scrivendo». */
const greetings = [];
const rpc = {
  renameProject: () => Promise.resolve(),
  greetProject: (name) => { greetings.push(name); return Promise.resolve({ greeted: true }); },
};
const showToast = () => {};
/* `goHome` chiude la selezione prima di cambiare vista (shared/selection.js). */
let selectionsCleared = 0;
const clearSelection = () => { selectionsCleared += 1; };
/* La conferma di buttare le modifiche del lettore: risponde quando il caso
   chiama `reply`, come una modale vera. */
let reply = null;
const confirmDialog = () => new Promise((r) => { reply = r; });
/* Il filo: ricorda cosa gli si manda. */
const wsManager = { posted: [], sendToChat(...a) { this.posted.push(a); return true; } };

__DOT_COLOR__
__FLOOR__
/* Lo scorrimento della pista (`SLIDE_MS` in home-pages.js): la porta di un
   quaderno appeso aspetta che finisca. */
const SLIDE_MS = 220;
__SHEETS__
__DEFAULT_BOT_NAME__
__BACK_TO__
__NOTEBOOK_ROOMS__

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
    this.backBtn = makeEl('button');
    /* Il percorso della riga: la radice, i pallini, la riga d'accento. */
    this.headEl = makeEl('header');
    this.pathEl = makeEl('nav');
    this.pathRoot = makeEl('button');
    this.pathRootName = makeEl('span');
    this.pathRootDot = makeEl('span');
    this.headDot = makeEl('span');
    /* Le due stanze nuove sono moduli loro, coi loro banchi: qui interessa
       che il guscio le apra, e cosa ci mette dentro di quel che sa. */
    this.versions = [];
    this.floating = [];
    this.you = {
      applyTranslations: () => {},
      open: () => this.actions.push('tu aperta'),
      sayUpdates: (v) => this.versions.push(v),
      sayBackup: (v) => { this.backupValue = v; },
      sayJenny: (v) => { this.jennyValue = v; },
      sayModel: (v) => { this.modelValue = v; },
    };
    /* Il nome di Jenny viaggia con lo stesso payload della finestra
       flottante: una lettura sola per due campi della stessa stanza. */
    this.names = [];
    this.jennyRoom = {
      applyTranslations: () => {},
      open: () => this.actions.push('jenny aperta'),
      setFloating: (v) => this.floating.push(v),
      setName: (v) => this.names.push(v),
      value: () => 'piccola',
    };
    this.givenSettings = [];
    this.setVersions = [];
    this.dataBackup = [];
    this.backupRoom = {
      applyTranslations: () => {},
      open: () => this.actions.push('backup aperta'),
      setBackup: (v) => this.dataBackup.push(v),
      value: () => 'mai fatto',
    };
    this.updatesRoom = {
      applyTranslations: () => {},
      open: () => this.actions.push('aggiornamenti aperta'),
      close: () => this.actions.push('aggiornamenti chiusa'),
      setVersion: (v) => this.setVersions.push(v),
      value: () => '0.11.0',
    };
    this.modelRoom = {
      applyTranslations: () => {},
      open: () => this.actions.push('modello aperta'),
      setSettings: (v) => this.givenSettings.push(v),
      value: () => 'OpenCode',
    };
    this.view = 'chat';
    this._jennyWasOut = true;
    this._jennyInChat = true;
    this._measureFloor = () => this.actions.push('pavimento rimisurato');
    /* La bacheca, il cassetto e il loro foglio: moduli loro, coi loro banchi.
       Qui interessa che il guscio li apra e li chiuda. */
    this.board = {
      applyTranslations: () => {},
      load: (name) => { this.actions.push('bacheca:' + name); return Promise.resolve(); },
    };
    this.drawer = {
      card: null,
      title: '',
      applyTranslations: () => {},
      load: (name, card) => {
        this.drawer.card = card;
        this.actions.push('cassetto:' + name + '/' + card);
        return Promise.resolve(card);
      },
    };
    this.boardSheet = { close: () => {} };
    this._readerCard = null;
    this.files = { count: 0 };
    this._personalName = 'Jenny';
    this._settingsGen = 0;
    this._drafts = new Map();
    this._draftsAside = new Map();
    this._threadFailed = false;
    this._threadReads = 0;
    this._running = false;
    /* Cosa è successo, in ordine. */
    this.actions = [];
    this.reloadFails = false;
    this.chat = {
      reload: async () => {
        if (this.reloadFails) throw new Error('thread giù');
        this.actions.push('riletto:' + sessionManager.currentKey);
      },
      scrollToBottom: () => this.actions.push('in fondo'),
      keepBottom: () => {},
    };
    this.activity = {
      start: () => this.actions.push('sta scrivendo'),
      stop: () => this.actions.push('riga ferma'),
    };
    this.jenny = {
      el: { classList: { contains: () => this._jennyOut } },
      setOut: (v) => { this._jennyOut = v; this.actions.push('fuori:' + v); },
      setChatOnScreen: (v) => { this.jennyChat = v; },
      setExpanded: (v) => { this.jennyExpanded = v; },
      nudge: () => this.actions.push('sobbalzo'),
    };
    /* Il velo (`home-over.js`, banco suo): qui si misura chi lo apre e lo
       chiude, e cosa succede intorno. */
    this.over = {
      isOpen: false,
      host: makeEl('div'),
      show: () => { this.over.isOpen = true; this.actions.push('velo su'); },
      hide: () => { this.over.isOpen = false; this.actions.push('velo giu'); },
    };
    /* Il trasloco (banco suo): sollevare e riabbassare si segnano, e un
       cambio di conversazione passa dal corpo vero, come in quello vero. */
    this.chatMove = {
      read: Promise.resolve(),
      lift: (host, key) => {
        this.actions.push('sale:' + key);
        if (sessionManager.currentKey !== key) return this.showConversation(key);
        return Promise.resolve();
      },
      lower: () => this.actions.push('scende'),
      retune: (home, key) => {
        if (sessionManager.currentKey === key) return Promise.resolve();
        this.actions.push('risintonizza:' + key);
        return this.showConversation(key);
      },
    };
    this._jennyOut = true;
    this.who = {
      known: [{ name: 'piante', modified: 1 }],
      invalidate: () => this.actions.push('elenco da rileggere'),
      render: () => {},
    };
    /* La fila in alto: qui interessa solo la modalita' ordina, che Indietro
       chiude senza salvare. Il suo disegno ha il banco suo. */
    this.strip = {
      sorting: false,
      draw: () => {},
      closeSort: () => { this.strip.sorting = false; this.actions.push('ordina chiusa'); },
    };
    /* La pista, finta ma con la regola che conta: **la chat puo' stare
       ovunque**, e chi cambia pagina lo fa per nome. Arrivare su Impostazioni
       la accende — e' quel che fa il gancio vero — e la promessa si tiene da
       parte, perche' la lettura del server finisce dopo. Le conversazioni
       passano dritte al corpo del cambio: la regola di dove aprirle ha il suo
       banco (`test_home_track_client.py`). */
    const app = this;
    this.homePages = {
      order: ['app', 'chat', 'notebooks', 'settings'],
      index: 1,
      homeConversation: null,
      get chatIndex() { return this.order.indexOf('chat'); },
      get current() { return this.order[this.index]; },
      indexOf(id) { return this.order.indexOf(id); },
      goTo(i) {
        this.index = i;
        app.actions.push('pagina:' + this.order[i]);
        if (this.order[i] === 'settings') app.powerOn = app._openSettings();
      },
      goToId(id) { this.goTo(this.indexOf(id)); },
      _panelFor: (id) => (id === 'chat' ? 'pannello-chat' : null),
      /* Un quaderno si apre sulla sua bacheca (05/10/2026); la regola ha il
         suo banco (`test_home_track_client.py`). */
      openConversation: (k) => (projectNameOf(k) ? this.openNotebook(projectNameOf(k)) : this.showConversation(k)),
    };
  }
  _autosize() {}
  _renderPending() {}
  /* Il posto per un riavvio (`localStorage`) ha il suo banco
     (`test_home_place_restore_client.py`). */
  _savePlace() {}
  /* Gli allegati per conversazione hanno il loro banco
     (`test_home_attachments_per_conversation_client.py`). */
  _filesFor() { return this.files; }
  _setRunning(running) { this._running = running; this.actions.push('ferma:' + running); }
  _showThreadError() { this._threadFailed = true; this.actions.push('non si legge'); }
  __SWITCH__
  __SHOW__
  __READ_THREAD__
  __RETRY_THREAD__
  __APPLY_CONVERSATION__
  __RELEASE_TURN__
  __CLOSE_OVERLAYS__
  __CLOSE_ALL_OVERLAYS__
  __HAS_OVERLAY_ABOVE__
  __BACK__
  __GO_HOME__
  __OPEN_CHAT__
  __RESTORE_PLACE__
  __OPEN_NOTEBOOK_CHAT__
  __ENTER_NOTEBOOK_CHAT__
  __NOTEBOOK_EXISTS__
  __CLOSE_SHEETS__
  __APPLY_TRANSLATIONS__
  __APPLY_CONVERSATION_TEXTS__
  __APPLY_OVER_TITLE__
  __CREATE_NOTEBOOK__
  __GREET_NOTEBOOK__
  __OPEN_BOARD__
  __OPEN_CARD__
  __BACK_TARGET__
  __ASK_IN_NOTEBOOK__
  __GO_BACK_ONE_ROOM__
  __OPEN_SETTINGS__
  __PAINT_SETTINGS__
  __CHAT_NAME__
  __HAS_COMPOSER__
  __PLACE_JENNY__
  __SYNC_JENNY_PLACE__
  __ASK_APP_NAMES__
  __OPEN_JENNY__
  __OPEN_UPDATES__
  __ASK_SETTINGS__
  __SET_VIEW__
  __APPLY_HEAD__
  __APPLY_BACK_LABEL__
  __APPLY_PATH__
  __PAINT_DOT__
  __GO_TO_PATH_ROOT__
  __SET_HEAD_TITLE__
  __ON_PAGE__
  __IS_CHAT_ON_SCREEN__
  __CHAT_ON_SCREEN_KEY__
  __REPORT_CHAT__
  __ON_GONE__
  __ACTIVE_COMPOSER__
  __SEND__
  __SEND_TEXT__
  __RENAME_NOTEBOOK__
  __RENAME_DRAFT__
  __DELETE_NOTEBOOK__
  __KEEP_NAME__
  __READ_NAME__
  __APPLY_BOT_NAME__
  __CONFIRM_LEAVE__
  __OPEN_PAGE__
  __READER_TITLE__
  __BIND_COMPOSER__
  __OPEN_NOTEBOOK__
  __CHAT_OVER__
  __SUMMON_KEY__
  __OPEN_CHAT_OVER__
  __CLOSE_CHAT_OVER__
  __SHEET_OPEN__
  __BACK_LABEL_KEY__
  __FOLLOW_PAGE__
  __BACK_ONE_PAGE__
}

function home() {
  lightbox = null;
  createOutcome = null;
  creations.length = 0;
  settingsPayload = { version: { current: '0.11.0' }, floating: { available: true } };
  settingsCalls = 0;
  sessionManager.currentKey = sessionManager.personalKey;
  const app = new App();
  app._applyConversation();
  app.actions.length = 0;
  window.JennyNative.opened = 0;
  window.JennyNative.closed = 0;
  return app;
}
"""


def _spines() -> str:
    """Il colore dei quaderni com'e' nel modulo foglia: `dotColor` e i due
    conti da cui dipende, senza `export`, da incollare nello script."""
    src = SPINES_JS.read_text(encoding="utf-8")
    spines = re.search(r"(?m)^export const SPINES = \d+;$", src).group(0).removeprefix("export ")
    return "\n".join([spines] + [function(src, n) for n in ("hashOf", "spineOf", "dotColor")])


def _harness() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__DOT_COLOR__", _spines())
        .replace("__SWITCH__", member(src, "switchConversation"))
        # Il corpo del cambio vive in `showConversation` dal 23/09/2026:
        # `switchConversation` decide solo **dove** (v. le pagine conversazione),
        # e qui non c'e' una pista — quindi passa dritto al corpo, che e' la
        # cosa che questo banco misura.
        .replace("__SHOW__", member(src, "showConversation"))
        .replace("__READ_THREAD__", member(src, "_readThread"))
        .replace("__RETRY_THREAD__", member(src, "_retryThread"))
        .replace("__APPLY_CONVERSATION__", member(src, "_applyConversation"))
        .replace("__RELEASE_TURN__", member(src, "_releaseTurn"))
        .replace("__CLOSE_OVERLAYS__", member(src, "_closeOverlays"))
        .replace("__CLOSE_ALL_OVERLAYS__", member(src, "_closeAllOverlays"))
        .replace("__HAS_OVERLAY_ABOVE__", member(src, "hasOverlayAbove"))
        .replace("__BACK__", member(src, "handleHardwareBack"))
        .replace("__GO_HOME__", member(src, "goHome"))
        .replace("__OPEN_CHAT__", member(src, "openChat"))
        .replace("__RESTORE_PLACE__", member(src, "_restorePlace", prefixes=("async ",)))
        .replace("__OPEN_NOTEBOOK_CHAT__", member(src, "_openNotebookChat"))
        .replace("__ENTER_NOTEBOOK_CHAT__", member(src, "_enterNotebookChat"))
        .replace("__NOTEBOOK_EXISTS__", member(src, "_notebookExists"))
        .replace("__CLOSE_SHEETS__", member(src, "_closeSheets"))
        .replace("__APPLY_TRANSLATIONS__", member(src, "_applyTranslations"))
        .replace("__APPLY_CONVERSATION_TEXTS__", member(src, "_applyConversationTexts"))
        .replace("__APPLY_OVER_TITLE__", member(src, "_applyOverTitle"))
        .replace("__CREATE_NOTEBOOK__", member(src, "createNotebook"))
        .replace("__GREET_NOTEBOOK__", member(src, "_greetNotebook"))
        .replace("__OPEN_BOARD__", member(src, "openBoard", prefixes=("async ",)))
        .replace("__OPEN_CARD__", member(src, "openCard", prefixes=("async ",)))
        .replace("__BACK_TARGET__", member(src, "_backTarget"))
        .replace("__ASK_IN_NOTEBOOK__", member(src, "_askInNotebook"))
        .replace("__GO_BACK_ONE_ROOM__", member(src, "goBackOneRoom"))
        .replace("__OPEN_SETTINGS__", member(src, "_openSettings"))
        .replace("__PAINT_SETTINGS__", member(src, "_paintSettings"))
        .replace("__CHAT_NAME__", member(src, "_chatName"))
        .replace("__HAS_COMPOSER__", member(src, "_hasComposer"))
        .replace("__PLACE_JENNY__", member(src, "_placeJenny"))
        .replace("__SYNC_JENNY_PLACE__", member(src, "_syncJennyPlace"))
        .replace("__ASK_APP_NAMES__", member(src, "_askAppNames"))
        .replace("__OPEN_JENNY__", member(src, "openJenny"))
        .replace("__OPEN_UPDATES__", member(src, "openUpdates"))
        .replace("__ASK_SETTINGS__", member(src, "_askSettings"))
        .replace("__APPLY_BACK_LABEL__", member(src, "_applyBackLabel"))
        .replace("__APPLY_PATH__", member(src, "_applyPath"))
        .replace("__PAINT_DOT__", member(src, "_paintDot"))
        .replace("__GO_TO_PATH_ROOT__", member(src, "goToPathRoot"))
        .replace("__SET_VIEW__", member(src, "_setView"))
        .replace("__APPLY_HEAD__", member(src, "_applyHead"))
        .replace("__SET_HEAD_TITLE__", member(src, "_setHeadTitle"))
        .replace("__ON_PAGE__", member(src, "onPageChanged"))
        .replace("__IS_CHAT_ON_SCREEN__", member(src, "isChatOnScreen"))
        .replace("__CHAT_ON_SCREEN_KEY__", member(src, "chatOnScreenKey"))
        .replace("__REPORT_CHAT__", member(src, "_reportChatOnScreen"))
        .replace("__ON_GONE__", member(src, "onGoneChanged"))
        .replace("__ACTIVE_COMPOSER__", member(src, "_composerActive"))
        .replace("__SEND__", member(src, "_send"))
        .replace("__SEND_TEXT__", member(src, "_sendText"))
        .replace("__RENAME_NOTEBOOK__", member(src, "renameNotebook"))
        .replace("__RENAME_DRAFT__", member(src, "_renameDraft"))
        .replace("__DELETE_NOTEBOOK__", member(src, "deleteNotebook"))
        .replace("__KEEP_NAME__", member(src, "_keepName"))
        .replace("__READ_NAME__", member(src, "_readName"))
        .replace("__APPLY_BOT_NAME__", member(src, "_applyBotName"))
        .replace("__CONFIRM_LEAVE__", member(src, "_confirmLeaveReader"))
        .replace("__OPEN_PAGE__", member(src, "openPage"))
        .replace("__READER_TITLE__", member(src, "_readerTitle"))
        .replace("__BIND_COMPOSER__", member(src, "_bindComposer"))
        .replace("__OPEN_NOTEBOOK__", member(src, "openNotebook"))
        .replace("__CHAT_OVER__", member(src, "chatOver"))
        .replace("__SUMMON_KEY__", member(src, "_summonKey"))
        .replace("__OPEN_CHAT_OVER__", member(src, "openChatOver"))
        .replace("__CLOSE_CHAT_OVER__", member(src, "closeChatOver"))
        .replace("__SHEET_OPEN__", member(src, "_sheetOpen"))
        .replace("__BACK_LABEL_KEY__", member(src, "_backLabelKey"))
        .replace("__FOLLOW_PAGE__", member(src, "_followPage", prefixes=("async ",)))
        .replace("__BACK_ONE_PAGE__", member(src, "_backOnePage", prefixes=("async ",)))
        .replace("__NOTEBOOK_ROOMS__", _const_block_scalar(src, "NOTEBOOK_ROOMS"))
        .replace("__DEFAULT_BOT_NAME__", _const_block_scalar(src, "DEFAULT_BOT_NAME"))
        .replace("__SHEETS__", _const_block_scalar(src, "LONG_PRESS_SHEETS") + "\n"
                 + _const_block_scalar(src, "REPORT_SHEET") + "\n"
                 + _const_block_scalar(src, "BOARD_SHEET") + "\n"
                 + _const_block_scalar(src, "SHARED_DIALOGS"))
        .replace("__FLOOR__", _const_block_scalar(src, "FLOOR_NO_COMPOSER"))
        .replace("__BACK_TO__", _const_block(src, "BACK_TO"))
    )


def _const_block_scalar(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^const {re.escape(name)} = (?:\[\n.*?^\]|[^\n]+?);$", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Lo scambio ──────────────────────────────────────────────────────────────


def test_opening_a_notebook_rereads_the_thread_of_that_notebook() -> None:
    """E lo rilegge **dopo** aver cambiato chiave: nell'ordine opposto
    rileggerebbe la conversazione che stai lasciando. Dal 05/10/2026 un
    quaderno si apre sulla sua bacheca, con la chat gia' sulla sua chiave."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('piante'));
      assert.equal(sessionManager.currentKey, 'project:piante');
      const at = (a) => app.actions.indexOf(a);
      assert.ok(at('risintonizza:project:piante') >= 0);
      assert.ok(at('riletto:project:piante') > at('risintonizza:project:piante'));
      assert.ok(at('bacheca:piante') > at('riletto:project:piante'), 'la bacheca non si e\u2019 aperta');
      assert.equal(app.view, 'board');
    """)


def test_going_where_you_already_are_is_not_a_switch() -> None:
    """Toccare la riga su cui sei non deve ributtare giù la conversazione: un
    filo che si svuota e si ridisegna per niente sembra averti perso qualcosa."""
    _run_js("""
      const app = home();
      await app.switchConversation(null);
      await app.switchConversation('websocket:default');
      assert.deepEqual(app.actions, []);
    """)


def test_the_draft_stays_with_the_conversation_it_was_written_in() -> None:
    """Mezza frase scritta in casa non deve partire dentro il quaderno che apri
    subito dopo. È la stessa famiglia di guasto di `switchGeneration` — quel che
    dici finisce nel diario di un altro progetto — solo un attimo prima."""
    _run_js("""
      const app = home();
      app.input.value = 'ricordami la giacca';
      await app.switchConversation(projectKey('piante'));
      assert.equal(app.input.value, '', 'la bozza ha seguito nel quaderno');

      app.input.value = 'le punte sono secche';
      await app.switchConversation(null);
      assert.equal(app.input.value, 'ricordami la giacca');

      await app.switchConversation(projectKey('piante'));
      assert.equal(app.input.value, 'le punte sono secche');
    """)


# ── Il nome nella fila ──────────────────────────────────────────────────────
#
# Dal 23/09/2026 il titolo «Jenny ⌄» non c'e' piu': la pagina chat ha il suo
# nome nella fila in alto. Fino al
# 26/09/2026 era il nome della conversazione che mostrava; ora e' sempre il
# nome di lei, perche' un quaderno si apre nei Quaderni.


def test_the_chat_page_keeps_her_name_inside_a_notebook() -> None:
    """Una pagina non cambia nome a seconda di cosa ci guardi dentro: la
    pagina chat si chiamava come il quaderno aperto, e «Jenny» spariva dal
    menu. Il resto della chat dice ancora in che quaderno sei."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('piante'));
      assert.deepEqual(app._chatName(), { name: 'Jenny', color: null });
      assert.equal(app.input.placeholder, 'Scrivi a Jenny, nel quaderno');
      assert.ok(app.emptyText.textContent.includes('resta qui'), app.emptyText.textContent);
    """)


def test_the_house_takes_its_own_name_back() -> None:
    """Il nome personale è messo da parte all'avvio, non riletto dalla testa:
    la testa delle stanze porta il nome del quaderno, e leggerlo di lì lo
    farebbe restare «piante» per sempre."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('piante'));
      await app.switchConversation(null);
      assert.deepEqual(app._chatName(), { name: 'Jenny', color: null },
                       'la casa non è un quaderno fra i quaderni');
      assert.equal(app.input.placeholder, 'Scrivi a Jenny');
    """)


def test_the_chat_page_keeps_its_own_name_while_it_is_lent_to_a_notebook_page() -> None:
    """Su una pagina quaderno appesa la chat e' in prestito: il nome che la
    fila scrive sulla pagina chat e' quello a cui tornerai, non quello a
    schermo."""
    _run_js("""
      const app = home();
      app.homePages.homeConversation = 'websocket:default';
      sessionManager.currentKey = projectKey('piante');
      assert.equal(app._chatName().name, 'Jenny');
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
      const app = home();
      app._releaseTurn();
      assert.deepEqual(app.actions, ['riga ferma', 'ferma:false']);
    """)


# ── Le vie di ritorno ───────────────────────────────────────────────────────


# Un quaderno aperto dai Quaderni: la sua bacheca, sopra la pagina Quaderni.
_FROM_NOTEBOOKS = """
      app.homePages.index = app.homePages.indexOf('notebooks');
      app._entry = { id: 'notebooks', kind: 'notebooks', fixed: true };
      await app.switchConversation(projectKey('orto'));
"""


def test_back_from_a_notebook_goes_back_to_the_list() -> None:
    """Dalla bacheca Indietro torna all'elenco dei Quaderni, da cui l'hai
    aperta; solo la pressione dopo lascia la pagina."""
    _run_js("""
      const app = home();
    """ + _FROM_NOTEBOOKS + """
      assert.equal(app.view, 'board');
      app.handleHardwareBack();
      assert.equal(app.view, 'chat');
      assert.equal(app.homePages.current, 'notebooks', 'dalla bacheca non si torna all\u2019elenco');
      app.actions.length = 0;
      app.handleHardwareBack();
      assert.deepEqual(app.actions, ['pagina:chat'], 'dall\u2019elenco non si torna alla chat');
    """)


def test_back_at_the_root_still_does_nothing() -> None:
    """Questa app è il launcher del telefono: Indietro non deve mai chiudere il
    task, e nella conversazione personale non c'è niente sotto."""
    _run_js("""
      const app = home();
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(app.actions, []);
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


# Un foglio aperto con una pressione lunga: un `<dialog>` nel top layer.
_SHEET = """
      const sheet = document.getElementById('home-notebook-sheet');
      sheet.open = true;
      sheet.close = () => { sheet.open = false; app.actions.push('foglio chiuso'); };
"""


def test_one_press_closes_one_thing() -> None:
    """Con la scheda di un quaderno aperta sopra la chat, Indietro chiude la
    scheda e basta: uscire anche dal quaderno farebbe sparire due cose per un
    gesto."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('piante'));
    """ + _SHEET + """
      app.actions.length = 0;
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(app.actions, ['foglio chiuso']);
      assert.equal(sessionManager.currentKey, 'project:piante');
    """)


def test_home_means_the_personal_conversation() -> None:
    """«Sei a casa» torna a voler dire qualcosa il giorno in cui si può essere
    altrove."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('piante'));
    """ + _SHEET + """
      app.actions.length = 0;
      app.goHome();
      await new Promise((r) => setTimeout(r, 0));
      assert.ok(app.actions.includes('foglio chiuso'));
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


def test_back_from_any_page_lands_on_the_chat_wherever_it_sits() -> None:
    """La chat si sposta come le altre: Indietro la cerca per nome, non va alla
    prima casella. E sulla chat Indietro e' la porta di casa, come sempre."""
    _run_js("""
      const app = home();
      app.homePages.order = ['notebooks', 'app', 'settings', 'chat'];
      app.homePages.index = 0;
      app.handleHardwareBack();
      assert.equal(app.homePages.current, 'chat');
      app.actions.length = 0;
      app.handleHardwareBack();
      assert.deepEqual(app.actions, [], 'dalla chat personale Indietro ha fatto qualcosa');
    """)


def test_a_tapped_alert_opens_the_conversation_the_alert_is_in() -> None:
    """La copia websocket di un avviso proattivo va sempre alla personale: da
    dentro un quaderno, «porta in chat» senza tornare a casa aprirebbe la
    stanza in cui quell'avviso non c'è."""
    _run_js("""
      const app = home();
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
      const app = home();
      app.reloadFails = true;
      await app.switchConversation(projectKey('piante'));
      assert.ok(app.actions.includes('non si legge'));
      assert.equal(app._threadFailed, true);
    """)


def test_a_reading_that_works_takes_the_error_back() -> None:
    """Se restasse, il vuoto di questa conversazione direbbe «non riesco a
    leggerla» di una storia appena letta."""
    _run_js("""
      const app = home();
      app.reloadFails = true;
      await app.switchConversation(projectKey('piante'));
      app.reloadFails = false;
      await app.switchConversation(null);
      assert.equal(app._threadFailed, false);
      assert.ok(app.emptyText.textContent.includes('Comincia tu'), app.emptyText.textContent);
    """)


# ── Un quaderno nuovo ───────────────────────────────────────────────────────


def test_the_house_asks_in_the_shared_words() -> None:
    """Officina e casa dicono «quaderno» con le stesse parole: il giro di
    creazione ha un vocabolario solo, e la casa non ne passa uno suo. Se
    tornasse un `words` qui, i due tornerebbero a divergere senza che nessun
    test se ne accorga."""
    _run_js("""
      const app = home();
      createOutcome = null;
      await app.createNotebook();
      assert.equal(creations[0].words, undefined,
                   'la casa ha un vocabolario suo: due posti da tenere allineati');
    """)


def test_a_notebook_created_is_a_notebook_you_are_in() -> None:
    """Aver dato un nome e scritto la riga di scope senza finire dentro
    lascerebbe a metà il gesto cominciato."""
    _run_js("""
      const app = home();
      createOutcome = 'orto';
      await app.createNotebook();
      assert.deepEqual(creations[0].known, [{ name: 'piante', modified: 1 }],
                       'i nomi già noti non vengono dal pannello');
      assert.ok(app.actions.includes('elenco da rileggere'), 'la cache è rimasta vecchia');
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.ok(app.actions.includes('bacheca:orto'), 'sotto la chat manca la bacheca del quaderno');
    """)


def test_a_new_notebook_opens_on_its_chat_where_jenny_speaks_first() -> None:
    """La bacheca di un quaderno appena nato e' vuota per forza: si va nella
    sua chat, e Jenny saluta. Nessun'altra domanda dopo nome e scope — le
    sezioni dei cassetti e «cosa ricordare» sono state tolte il 10/10/2026."""
    _run_js("""
      const app = home();
      createOutcome = 'orto';
      writtenName = 'una risposta che nessuno deve chiedere';
      await app.createNotebook();
      assert.ok(app.chatOver, 'il quaderno nuovo si ferma sulla bacheca vuota');
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.deepEqual(greetings, ['orto'], 'Jenny non saluta nel quaderno appena nato');
      const typing = app.actions.indexOf('sta scrivendo');
      assert.ok(typing !== -1, 'mentre aspetta il saluto la chat non dice niente');
      assert.ok(app.actions.indexOf('riga ferma', typing) > typing,
                'la bolla «sta scrivendo» resta accesa dopo il saluto');
    """)


def test_a_creation_that_did_not_happen_opens_nothing() -> None:
    """Annullata, rifiutata o chiusa: il giro torna `null` in tutte le uscite
    che non hanno scritto su disco, e nessuna di quelle deve portare dentro un
    quaderno che non c'è."""
    _run_js("""
      const app = home();
      createOutcome = null;
      await app.createNotebook();
      assert.equal(sessionManager.currentKey, 'websocket:default');
      assert.deepEqual(app.actions, []);
    """)


# ── Le stanze ───────────────────────────────────────────────────────────────


def test_back_peels_one_room_at_a_time() -> None:
    """Lettore, bacheca, elenco dei Quaderni, chat: una cosa per pressione.

    Se `goBackOneRoom` sparisse, dal lettore un tocco solo farebbe sparire la
    stanza **e** il quaderno che la conteneva — due cose per un gesto.
    """
    _run_js("""
      const app = home();
    """ + _FROM_NOTEBOOKS + """
      app.view = 'reader';
      app.shell.attrs['data-view'] = 'reader';

      app.handleHardwareBack();
      assert.equal(app.view, 'board', 'dal lettore non si torna alla bacheca');
      app.handleHardwareBack();
      assert.equal(app.view, 'chat', 'dalla bacheca non si torna ai Quaderni');
      assert.equal(app.homePages.current, 'notebooks');
      app.handleHardwareBack();
      assert.equal(app.homePages.current, 'chat');
    """)


def test_a_panel_over_the_pages_still_closes_first() -> None:
    """Gli strati vengono prima delle stanze: il foglio sta nel top layer, e
    chiuderlo e' quel che l'occhio si aspetta da quel tasto."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app._setView('board');
    """ + _SHEET + """
      app.handleHardwareBack();
      assert.equal(sheet.open, false, 'il foglio non si e\\u2019 chiuso');
      assert.equal(app.view, 'board', 'la stanza se n\\u2019e\\u2019 andata insieme a lui');
    """)


def test_leaving_the_chat_puts_jenny_away_and_coming_back_restores_her() -> None:
    """La tavola la disegna al bordo nelle pagine e fuori nella chat.

    Ma «fuori» al ritorno solo se era fuori quando sei uscito: metterla via e'
    una decisione dell'utente, e una stanza non la disfa.
    """
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));

      app._setView('board');
      assert.equal(app._jennyOut, false, 'nelle pagine non si e\\u2019 messa via');
      app._setView('chat');
      assert.equal(app._jennyOut, true, 'tornando non e\\u2019 uscita');

      // Ora messa via a mano, dentro la chat.
      app._jennyOut = false;
      app._setView('board');
      app._setView('chat');
      assert.equal(app._jennyOut, false, 'la stanza ha disfatto una scelta dell\\u2019utente');
    """)


def test_a_room_without_a_composer_declares_its_own_floor() -> None:
    """Il pavimento di Jenny e' il composer, e nelle pagine il composer non
    c'e': il suo `offsetHeight` la' e' zero, quindi il token va dichiarato o
    lei appoggia i piedi sul bordo dello schermo. Al ritorno si rimisura."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app.actions.length = 0;

      app._setView('board');
      assert.equal(
        document.documentElement.style.props['--home-composer-h'],
        FLOOR_NO_COMPOSER + 'px',
        'le pagine non dichiarano il loro pavimento',
      );

      app._setView('chat');
      assert.ok(
        app.actions.includes('pavimento rimisurato'),
        'tornando nella chat il composer non viene rimisurato',
      );
    """)












def test_the_shell_hears_when_the_chat_leaves_the_screen() -> None:
    """Un avviso con Jenny davanti si sopprime solo se la chat e' a schermo:
    il guscio nativo deve sentirsi dire anche quando la chat se ne va, una
    volta per uscita e non a ogni ricalcolo."""
    _run_js("""
      const app = home();
      app.onPageChanged(1, { id: 'chat', kind: 'chat', fixed: true });
      assert.equal(window.JennyNative.opened, 1);
      app._setView('board');
      assert.equal(window.JennyNative.closed, 1, 'la bacheca non ha detto che la chat se n\u2019e\u2019 andata');
      app.openChat();
      await new Promise((r) => setTimeout(r, 0));
      assert.ok(window.JennyNative.opened >= 1, 'il velo su non l\u2019ha detto');
      app.closeChatOver();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(window.JennyNative.closed, 2, 'il velo giu\u2019 non l\u2019ha detto');
      app._reportChatOnScreen();
      assert.equal(window.JennyNative.closed, 2, 'ripetuto a vuoto');
    """)


def test_a_tapped_alert_raises_the_personal_chat_over_the_board() -> None:
    """Un avviso toccato dentro un quaderno porta la conversazione personale —
    e' li' che l'avviso sta — **sopra** la bacheca, non al suo posto (dal
    10/10/2026): chiusa la chat, sei di nuovo dov'eri, e la chat dietro la
    bacheca e' tornata quella del quaderno. Prima l'avviso ti portava fuori dal
    quaderno, e il posto era perso."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app._setView('board');
      app.actions.length = 0;
      assert.equal(app.openChat(), true);
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'board', 'l\u2019avviso ha portato via la bacheca');
      assert.equal(app.chatOver, true, 'la chat non e\u2019 salita');
      assert.equal(sessionManager.currentKey, 'websocket:default');
      assert.ok(app.actions.includes('sale:websocket:default'));
      assert.equal(app.isChatOnScreen(), true, 'gli avvisi non si cancellerebbero');
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.chatOver, false);
      assert.equal(app.view, 'board');
      assert.equal(sessionManager.currentKey, 'project:orto', 'la bacheca e\u2019 rimasta senza la sua chat');
      assert.ok(app.actions.includes('risintonizza:project:orto'));
    """)


def test_a_tapped_alert_swaps_a_notebook_chat_already_open_over_the_board() -> None:
    """Con la chat del quaderno gia' aperta sopra la bacheca, l'avviso la
    sostituisce con la personale; richiusa, il quaderno ritrova la sua."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app._setView('board');
      app.openChatOver();
      assert.equal(sessionManager.currentKey, 'project:orto');
      app.openChat();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.chatOver, true);
      assert.equal(sessionManager.currentKey, 'websocket:default');
      app.closeChatOver();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'project:orto');
    """)


def test_a_tapped_alert_on_the_chat_page_just_shows_the_bottom() -> None:
    """Sulla pagina chat, con la personale a schermo, il velo non serve:
    l'avviso porta in fondo al filo e basta."""
    _run_js("""
      const app = home();
      app.onPageChanged(1, { id: 'chat', kind: 'chat', fixed: true });
      app.actions.length = 0;
      assert.equal(app.openChat(), true);
      assert.equal(app.chatOver, false, 'un velo sopra la chat stessa');
      assert.ok(app.actions.includes('in fondo'));
    """)


def test_a_tapped_alert_leaves_the_pages_where_they_were() -> None:
    """Dai Quaderni, dalle App, dalle Impostazioni: la chat sale sopra la
    pagina e la pista non si muove."""
    _run_js("""
      const app = home();
      app.homePages.goToId('notebooks');
      app.onPageChanged(2, { id: 'notebooks', kind: 'notebooks', fixed: true });
      app.actions.length = 0;
      assert.equal(app.openChat(), true);
      assert.equal(app.homePages.current, 'notebooks');
      assert.equal(app.chatOver, true);
      assert.ok(!app.actions.some((a) => a.startsWith('pagina:')), 'la pista si e\u2019 mossa');
    """)


def test_a_tapped_alert_does_not_climb_over_an_open_question() -> None:
    """Una domanda aperta aspetta una risposta: il velo non sale, e il guscio
    nativo, sentendosi dire di no, lascia l'avviso in tendina."""
    _run_js("""
      const app = home();
      app._setView('board');
      document.getElementById('oc-confirm-dialog').open = true;
      const realQuery = document.querySelector;
      document.querySelector = (sel) => (sel === 'dialog[open]' ? {} : realQuery(sel));
      assert.equal(app.openChat(), false);
      assert.equal(app.chatOver, false);
      assert.equal(document.getElementById('oc-confirm-dialog').open, true, 'la domanda e\u2019 stata chiusa');
    """)


# ── La pagina Impostazioni: «Tu e Jenny» ────────────────────────────────────


def test_settings_is_a_page_and_back_from_it_is_the_chat() -> None:
    """«Tu e Jenny» non e' piu' una stanza sopra le pagine: e' la pagina
    Impostazioni (23/09/2026). Aprirla ci porta la pista, e Indietro riporta
    alla chat — **senza uscire dal quaderno**: tornare da una pagina di
    impostazioni non e' un modo di cambiare conversazione."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app._setView('chat');  // dalla bacheca alla pista
      app.homePages.goToId('settings');
      assert.equal(app.view, 'chat', 'le impostazioni sono ancora una stanza');
      assert.equal(app.homePages.current, 'settings');
      assert.ok(app.actions.includes('tu aperta'), 'la pagina non e\u2019 stata caricata');
      app.handleHardwareBack();
      assert.equal(app.homePages.current, 'chat');
      assert.equal(sessionManager.currentKey, 'project:orto', 'e il quaderno e\u2019 rimasto');
    """)


def test_the_back_arrow_names_where_you_land() -> None:
    """L'etichetta della freccia dice dove si atterra, e le stanze non
    atterrano tutte nello stesso posto.

    Era una frase sola — «torna alla chat» — scritta in `_applyTranslations` e
    buona per tutte: vera dalle pagine, falsa dal lettore, che torna alle
    pagine. Con quattro stanze la parola giusta la decide la stessa tabella che
    decide il salto. Dal 26/09/2026 la freccia e' solo icona, e la frase e' la
    sua `aria-label`: chi non vede la freccia la sente.
    """
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      const dice = (room) => { app._setView(room); return app.backBtn.attrs['aria-label']; };
      assert.equal(dice('board'), i18n.t('home.back.chat'));
      assert.equal(dice('reader'), i18n.t('home.back.board'), 'dal lettore si torna alle pagine');
      assert.equal(dice('jenny'), i18n.t('home.back.settings'), 'da lei si torna alle impostazioni');
      assert.notEqual(i18n.t('home.back.board'), i18n.t('home.back.chat'),
                      'le due frasi sono diventate la stessa, e il banco non misura piu\u2019 niente');
    """)


def test_the_path_says_whose_place_each_room_is() -> None:
    """La radice del percorso dice **di che posto** e' la stanza: le pagine
    sono dei Quaderni, il lettore del quaderno, le altre stanze delle
    Impostazioni. E il quaderno porta il suo pallino e il suo colore sulla
    riga d'accento, lo stesso della sua riga nei Quaderni."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      const color = dotColor('orto');

      app._setView('board');
      assert.equal(app.pathRootName.textContent, i18n.t('home.strip.notebooks'));
      assert.equal(app.nameEl.textContent, 'orto');
      assert.equal(app.pathRootDot.hidden, true, 'la radice «Quaderni» non e\u2019 un quaderno');
      assert.equal(app.headDot.hidden, false, 'il quaderno, dove sei, non ha il pallino');
      assert.equal(app.headDot.style.background, color);
      assert.equal(app.headEl.style['--path-line'], color, 'la riga d\u2019accento non e\u2019 del quaderno');

      app._setView('reader');
      assert.equal(app.pathRootName.textContent, 'orto', 'il lettore non dice di che quaderno e\u2019');
      assert.equal(app.pathRootDot.hidden, false);
      assert.equal(app.pathRootDot.style.background, color);
      assert.equal(app.headDot.hidden, true, 'la pagina letta ha preso il pallino del quaderno');
      assert.equal(app.headEl.style['--path-line'], color);

      app._setView('chat');
      app._setView('updates');
      assert.equal(app.pathRootName.textContent, i18n.t('home.strip.settings'));
      assert.equal(app.pathRootDot.hidden, true);
      assert.equal(app.headDot.hidden, true);
      assert.equal(app.headEl.style['--path-line'], undefined,
                   'il colore del quaderno e\u2019 rimasto sulla stanza delle impostazioni');
    """)


def test_the_path_root_leads_to_its_place() -> None:
    """Un tocco sulla radice porta al posto di cui la stanza e'. Dal lettore e
    dalle stanze delle impostazioni e' dove porta anche la freccia; dalla
    bacheca la radice porta ai Quaderni."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app._setView('reader');
      app.goToPathRoot();
      assert.equal(app.view, 'board', 'dal lettore la radice non riporta alla bacheca');

      app.goToPathRoot();
      assert.equal(app.view, 'chat');
      assert.equal(app.homePages.index, app.homePages.indexOf('notebooks'),
                   'dalla bacheca la radice non porta alla pagina Quaderni');
      assert.equal(sessionManager.currentKey, 'project:orto', 'la radice ha cambiato conversazione');

      app._setView('updates');
      app.goToPathRoot();
      assert.equal(app.view, 'chat');
      assert.equal(app.homePages.index, app.homePages.indexOf('settings'));
    """)


def test_the_settings_payload_is_asked_once_per_opening_for_both_rooms() -> None:
    """`/api/settings` porta provider, contatori e lavoratori periodici, e di
    quel peso le due stanze leggono un campo per uno: la versione e lo stato
    della finestra flottante. Aprire una stanza dalle Impostazioni non lo
    richiede.

    **Una volta per apertura, non una volta per sempre** (26/09/2026): la casa
    e' il launcher e vive per giorni, e con la lettura dell'avvio un
    aggiornamento trovato dal controllo periodico non compariva mai. Riaprendo,
    prima si ridisegna la cache e poi la risposta nuova."""
    _run_js("""
      const app = home();
      app.homePages.goToId('settings');
      await app.powerOn;
      await app.openJenny();
      assert.equal(settingsCalls, 1, 'la stanza di lei ha richiesto il payload');
      app._setView('chat');
      app.homePages.goTo(app.homePages.chatIndex);
      app.homePages.goToId('settings');
      await app.powerOn;
      assert.equal(settingsCalls, 2, 'la seconda apertura non ha riletto il server');
      assert.deepEqual(app.setVersions,
        [{ current: '0.11.0' }, { current: '0.11.0' }, { current: '0.11.0' }]);
      assert.deepEqual(app.floating, [{ available: true }, { available: true }, { available: true }]);
    """)


def test_a_settings_call_that_failed_is_tried_again() -> None:
    """Il fallimento non si ricorda. Una rete andata male una volta lascerebbe
    la riga della finestra flottante nascosta fino al riavvio della casa — e
    quella non e' una versione che manca, e' un'impostazione sparita."""
    _run_js("""
      const app = home();
      settingsPayload = null;
      app.homePages.goToId('settings');
      await app.powerOn;
      assert.deepEqual(app.floating, [null], 'senza risposta la finestra resta sconosciuta');

      settingsPayload = { version: { current: '0.12.0' }, floating: { available: true } };
      app.homePages.goTo(app.homePages.chatIndex);
      app.homePages.goToId('settings');
      await app.powerOn;
      assert.equal(settingsCalls, 2, 'il guscio si e\u2019 ricordato del fallimento');
      /* Anche il giro andato male passa dalla stanza: le dice «non lo so», e
         quella non scrive niente. */
      assert.deepEqual(app.setVersions, [null, { current: '0.12.0' }]);
    """)


def test_her_room_hangs_off_you_and_jenny() -> None:
    """Indietro sbuccia una stanza per volta anche di qua: da lei si torna alla
    pagina Impostazioni, non alla chat — e da li', alla chat."""
    _run_js("""
      const app = home();
      app.openJenny();
      assert.equal(app.view, 'jenny');
      assert.ok(app.actions.includes('jenny aperta'));
      assert.equal(app.nameEl.textContent, i18n.t('home.jenny.title', { name: 'Jenny' }), 'la testa non dice dove sei');
      app.handleHardwareBack();
      assert.equal(app.view, 'chat');
      assert.equal(app.homePages.current, 'settings');
      app.handleHardwareBack();
      assert.equal(app.homePages.current, 'chat');
    """)


def test_leaving_the_updates_room_stops_its_polling() -> None:
    """Un'installazione avviata va avanti per conto suo, ma il suo polling non
    deve tenere sveglia una stanza che non e' piu' a schermo — e rientrando si
    riaggancia da se'. Il guscio lo dice a **ogni** cambio di stanza, non solo
    tornando indietro: dalla chat, da un quaderno, da dove capita."""
    _run_js("""
      const app = home();
      app.openUpdates();
      assert.equal(app.view, 'updates');
      assert.ok(app.actions.includes('aggiornamenti aperta'));
      assert.ok(!app.actions.includes('aggiornamenti chiusa'), 'chiusa appena aperta');

      app.goBackOneRoom();
      assert.equal(app.view, 'chat', 'da li si torna alle impostazioni');
      assert.equal(app.homePages.current, 'settings');
      assert.ok(app.actions.includes('aggiornamenti chiusa'), 'il polling resta vivo');

      /* E anche uscendo da un'altra parte: il guscio non sa da dove vieni. */
      app.actions.length = 0;
      app.openUpdates();
      app._setView('chat');
      assert.ok(app.actions.includes('aggiornamenti chiusa'));
    """)


# ── Dove appoggia Jenny, pagina per pagina (23/09/2026) ─────────────────────


def test_a_page_without_a_composer_puts_jenny_on_the_floor() -> None:
    """Il cassetto e le impostazioni non hanno un composer: misurarlo lo
    stesso — sta nella pagina accanto, alto quanto era — la terrebbe sospesa
    a mezz'aria sopra le righe. Dal 05/10/2026 nemmeno la pagina di un
    quaderno appeso: e' una porta alla sua bacheca."""
    _run_js("""
      const app = home();
      app.actions.length = 0;
      app._entry = { id: 'app', kind: 'drawer', fixed: true };
      app._placeJenny();
      assert.equal(document.documentElement.style.props['--home-composer-h'], FLOOR_NO_COMPOSER + 'px');
      assert.ok(!app.actions.includes('pavimento rimisurato'));
      app._entry = { id: 'q1', kind: 'conversation', ref: 'project:piante' };
      app._placeJenny();
      assert.ok(!app.actions.includes('pavimento rimisurato'), 'la porta di un quaderno ha un composer');
      app._entry = { id: 'chat', kind: 'chat' };
      app._placeJenny();
      assert.ok(app.actions.includes('pavimento rimisurato'), 'la pagina chat ha perso il suo composer');
    """)


def test_every_switch_from_outside_asks_the_pages_where() -> None:
    """Titolo, Home, un avviso: passano **tutti** dalla regola delle pagine.

    Una pagina quaderno mostra solo il suo quaderno, e a deciderlo e'
    `HomePages.openConversation`. Un guscio che cambiasse la chat per conto
    suo lascerebbe la personale dentro la pagina di «piante». E la personale si
    chiede con la sua chiave, non con `null`: le pagine confrontano chiavi.

    Indietro non c'e' piu' dal 26/09/2026: dalla pagina chat non esce da un
    quaderno, perche' la pagina chat un quaderno non lo mostra; dai Quaderni
    lo chiude (`closeNotebook`, v. il suo banco).

    Ognuna delle strade si esercita per conto suo: fino al 25/09/2026
    questo banco lo prometteva e chiamava solo `switchConversation`, quindi un
    `goHome` che avesse chiamato `showConversation` direttamente sarebbe
    passato verde.
    """
    _run_js("""
      const app = home();
      const requested = [];
      app.homePages = { openConversation: (k) => { requested.push(k); return Promise.resolve('instradata'); } };
      const r = await app.switchConversation(projectKey('piante'));
      assert.deepEqual(requested, ['project:piante']);
      assert.deepEqual(app.actions, [], 'il guscio ha riletto il filo senza chiedere dove');
      assert.equal(r, 'instradata', 'la promessa delle pagine non torna a chi chiama');
      await app.switchConversation(null);
      assert.equal(requested[1], sessionManager.personalKey);

      /* Le pagine finte non cambiano conversazione: si resta in «piante», e
         ognuna delle due strade deve chiedere la personale alle pagine. */
      sessionManager.currentKey = projectKey('piante');
      /* Sulla pagina chat: e' li' che l'avviso cambia conversazione invece di
         salire nel velo (v. `openChat`). */
      app._entry = { id: 'chat', kind: 'chat' };
      for (const [route, perform] of [
        ['Home', () => app.goHome()],
        ['un avviso', () => app.openChat()],
      ]) {
        requested.length = 0;
        app.actions.length = 0;
        perform();
        await new Promise((r) => setTimeout(r, 0));
        assert.deepEqual(requested, [sessionManager.personalKey], route + ': non ha chiesto alle pagine');
        assert.ok(!app.actions.some((f) => f.startsWith('riletto:')),
                  route + ': il guscio ha cambiato la chat per conto suo');
      }
    """)


# Un'app aperta sopra tutto, con due schermate interne: Indietro torna
# indietro *dentro* di lei, e solo `closeApp` la chiude davvero.
_DEEP_APP = """
      const app = home();
      await app.switchConversation(projectKey('piante'));
      const mini = { open: true, depth: 2, back: 0 };
      app._appActions = {
        isAppOpen() { return mini.open; },
        handleBack() {
          if (!mini.open) return false;
          if (mini.depth > 1) { mini.depth -= 1; mini.back += 1; return true; }
          mini.open = false;
          return true;
        },
        closeApp() { mini.open = false; },
      };
      app.actions.length = 0;
"""


def test_home_closes_an_app_with_inner_screens_for_good() -> None:
    """Home e' un indirizzo, non Indietro: un'app con una schermata interna
    aperta tornava indietro dentro di se' e restava sopra la chat."""
    _run_js(_DEEP_APP + """
      app.goHome();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(mini.open, false, 'l\\u2019app e\\u2019 rimasta aperta sotto la chat');
      assert.equal(mini.back, 0, 'Home ha fatto Indietro dentro l\\u2019app');
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


def test_a_tapped_alert_keeps_an_open_app_under_the_chat() -> None:
    """L'app aperta resta li', sotto il velo, alla sua schermata interna:
    chiusa la chat la si ritrova com'era. Fino al 10/10/2026 l'avviso la
    chiudeva, e con lei quel che ci stavi facendo."""
    _run_js(_DEEP_APP + """
      assert.equal(app.openChat(), true);
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.chatOver, true);
      assert.equal(mini.open, true, 'l\\u2019avviso ha chiuso l\\u2019app');
      assert.equal(mini.depth, 2);
      assert.equal(sessionManager.currentKey, 'websocket:default');
      app.handleHardwareBack();
      assert.equal(app.chatOver, false, 'Indietro chiude prima la chat');
      assert.equal(mini.open, true);
      assert.equal(mini.back, 0, 'Indietro ha toccato anche l\\u2019app');
    """)


def test_back_still_steps_inside_the_app_first() -> None:
    """Indietro resta un passo: una schermata interna dell'app, prima."""
    _run_js(_DEEP_APP + """
      app.handleHardwareBack();
      assert.equal(mini.open, true);
      assert.equal(mini.back, 1);
      assert.equal(sessionManager.currentKey, 'project:piante');
    """)


def test_the_chat_under_an_open_app_is_not_on_screen() -> None:
    """Gli avvisi si cancellano quando la chat si vede: con un'app aperta
    sopra, la chat c'e' ma non la stai guardando."""
    _run_js("""
      const app = home();
      app.onPageChanged(1, { id: 'chat', kind: 'chat', fixed: true });
      assert.equal(app.isChatOnScreen(), true);
      app._appActions = { isAppOpen: () => true };
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
      const app = home();
      app._entry = { id: 'app', kind: 'drawer', fixed: true };
      app.homePages.index = app.homePages.indexOf('app');
      let openApp = true;
      app._appActions = { handleBack: () => { if (!openApp) return false; openApp = false; return true; } };
      app.launcher = { search: { value: 'tel' }, dismiss() { this.search.value = ''; } };
      for (const id of ['jenny-app-sheet', 'android-app-sheet']) {
        const sheet = document.getElementById(id);
        sheet.open = true;
        sheet.close = function () { this.open = false; };
        assert.equal(app._closeOverlays(), true);
        assert.equal(sheet.open, false, id + ': Indietro ha lasciato il foglio aperto');
        assert.equal(openApp, true, id + ': ha chiuso l\u2019app sotto invece del foglio');
      }
      assert.equal(app._closeOverlays(), true);
      assert.equal(openApp, false, 'Indietro non chiude l\u2019app aperta dal cassetto');
      assert.equal(app.launcher.search.value, 'tel', 'ha svuotato la ricerca insieme all\u2019app');
      assert.equal(app._closeOverlays(), true);
      assert.equal(app.launcher.search.value, '', 'la ricerca non si svuota');
      assert.equal(app._closeOverlays(), false);
      app.handleHardwareBack();
      assert.equal(app.homePages.current, 'chat');
    """)


def test_back_and_home_leave_the_moving_mode_saving() -> None:
    """Ogni uscita dalla modalita' ordina salva, come nei launcher (scelta
    dell'utente, 10/10/2026): prima Indietro e Home buttavano il nuovo ordine
    senza dirlo."""
    _run_js("""
      const app = home();
      const saves = [];
      app.strip.closeSort = (opts) => { saves.push(opts); app.strip.sorting = false; };
      app.strip.sorting = true;
      assert.equal(app._closeOverlays(), true);
      app.strip.sorting = true;
      app.goHome();
      assert.deepEqual(saves, [{ save: true }, { save: true }], 'un\u2019uscita non ha salvato');
    """)


def test_the_row_asks_for_the_app_names_once_and_only_the_light_list() -> None:
    """Lo slug non e' il nome: «todo» invece di «Todo» (telefono, 23/09/2026).
    Si chiede l'elenco delle Jenny App e basta — non quello delle app Android,
    che porta le icone — una volta, e solo se c'e' un'app appesa."""
    _run_js("""
      const app = home();
      const requested = [];
      let draws = 0;
      app.strip.draw = () => { draws += 1; };
      app.appsSource = () => ({
        jennyApps: [],
        loadJennyApps: () => { requested.push('jenny'); return Promise.resolve(); },
        ensureLoaded: () => requested.push('tutto'),
      });
      app.homePages.pages = [{ id: 'q1', kind: 'conversation', ref: 'project:piante' }];
      app._askAppNames();
      assert.deepEqual(requested, [], 'senza app appese ha letto un elenco');
      app.homePages.pages.push({ id: 'p1', kind: 'app', ref: 'todo' });
      app._askAppNames();
      app._askAppNames();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(requested, ['jenny']);
      assert.equal(draws, 1, 'la fila non si e ridisegnata coi nomi');
    """)


# ── Gli avvisi letti si cancellano ───────────────────────────────────────────
#
# Il guscio nativo chiede `isChatOnScreen()` al rientro in primo piano, e la
# casa gli dice `chatOpened()` quando la chat arriva a schermo da dentro la
# WebView. Fino al 24/09/2026 la casa non aveva ne' l'una ne' l'altra cosa, e
# gli avvisi in coda non si cancellavano mai.

CHAT = "{ id: 'chat', kind: 'chat', fixed: true }"
DRAWER = "{ id: 'app', kind: 'drawer', fixed: true }"


def test_boot_says_the_chat_is_not_on_screen_yet() -> None:
    """Prima che la pista dica dove sei, la risposta e' no: nel dubbio un avviso
    resta, che e' la direzione d'errore giusta."""
    _run_js("""
      const app = home();
      assert.equal(app.isChatOnScreen(), false);
      assert.equal(window.JennyNative.opened, 0);
    """)


def test_arriving_on_the_chat_page_clears_the_alerts() -> None:
    _run_js(f"""
      const app = home();
      app.onPageChanged(1, {CHAT});
      assert.equal(app.isChatOnScreen(), true);
      assert.equal(window.JennyNative.opened, 1);
    """)


def test_another_page_is_not_the_chat() -> None:
    _run_js(f"""
      const app = home();
      app.onPageChanged(0, {DRAWER});
      assert.equal(app.isChatOnScreen(), false);
      assert.equal(window.JennyNative.opened, 0);
    """)


def test_the_chat_page_on_a_notebook_is_not_where_alerts_are() -> None:
    """Gli avvisi proattivi arrivano nella conversazione personale: la pagina
    chat su un quaderno non li mostra. Il guscio sente comunque quale chat e'
    a schermo — quella del quaderno, col suo nome, che cancella solo gli
    avvisi di quel quaderno (dal 10/10/2026). Tornando alla personale, la
    personale, senza nome."""
    _run_js(f"""
      const app = home();
      app.onPageChanged(1, {CHAT});
      window.JennyNative.opened = 0;
      window.JennyNative.openedWith.length = 0;
      await app.showConversation(projectKey('piante'));
      assert.equal(app.isChatOnScreen(), false);
      assert.equal(app.chatOnScreenKey(), 'project:piante');
      assert.deepEqual(window.JennyNative.openedWith, ['project:piante']);
      await app.showConversation(null);
      assert.equal(app.isChatOnScreen(), true);
      assert.equal(app.chatOnScreenKey(), 'personal');
      assert.deepEqual(window.JennyNative.openedWith, ['project:piante', null]);
    """)


# ── L'avviso di un quaderno porta nel quaderno (10/10/2026) ──────────────────
# Il tag della notifica nomina il quaderno, il guscio nativo lo passa a
# `openChat(key)`. Prima ogni avviso apriva la chat personale, dove la risposta
# del quaderno non c'e'.

def test_a_notebook_alert_from_the_chat_page_enters_the_notebook() -> None:
    _run_js(f"""
      const app = home();
      app.onPageChanged(1, {CHAT});
      notebooksOnDisk = ['orto'];
      assert.equal(app.openChat('project:orto'), true, 'risponde subito: evaluateJavascript non aspetta');
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'board', 'sotto non c\u2019e\u2019 la bacheca del quaderno');
      assert.equal(app.chatOver, true, 'la chat del quaderno non e\u2019 salita');
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.equal(app.chatOnScreenKey(), 'project:orto');
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.chatOver, false);
      assert.equal(app.view, 'board', 'Indietro non riporta alla bacheca');
    """)


def test_a_notebook_alert_inside_that_notebook_only_raises_its_chat() -> None:
    _run_js("""
      const app = home();
      notebooksOnDisk = ['orto'];
      await app.openNotebook('orto');
      app.actions.length = 0;
      assert.equal(app.openChat('project:orto'), true);
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'board');
      assert.equal(app.chatOver, true);
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.ok(!app.actions.includes('bacheca:orto'), 'ha riaperto il quaderno in cui eri gia\u2019');
    """)


def test_a_notebook_alert_from_another_notebook_moves_there() -> None:
    _run_js("""
      const app = home();
      notebooksOnDisk = ['orto', 'piante'];
      await app.openNotebook('piante');
      app.openChatOver();
      assert.equal(sessionManager.currentKey, 'project:piante');
      assert.equal(app.openChat('project:orto'), true);
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.equal(app.view, 'board');
      assert.equal(app.chatOver, true);
    """)


def test_a_notebook_alert_with_its_chat_already_up_changes_nothing() -> None:
    _run_js("""
      const app = home();
      notebooksOnDisk = ['orto'];
      await app.openNotebook('orto');
      app.openChatOver();
      app.actions.length = 0;
      assert.equal(app.openChat('project:orto'), true);
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.chatOver, true);
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.ok(!app.actions.includes('bacheca:orto'));
    """)


def test_the_alert_of_a_deleted_notebook_falls_back_to_the_personal_chat() -> None:
    _run_js(f"""
      const app = home();
      app.onPageChanged(1, {CHAT});
      notebooksOnDisk = ['piante'];
      assert.equal(app.openChat('project:orto'), true);
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'websocket:default');
      assert.equal(app.isChatOnScreen(), true);
      assert.notEqual(app.view, 'board');
    """)


def test_a_notebook_list_that_cannot_be_read_still_opens_the_notebook() -> None:
    """Nel dubbio si prova: mandare nella personale chi ha un quaderno vero e'
    l'errore da evitare."""
    _run_js("""
      const app = home();
      notebooksOnDisk = null;
      assert.equal(app.openChat('project:orto'), true);
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.equal(app.chatOver, true);
    """)


def test_a_key_that_is_not_a_notebook_opens_the_personal_chat() -> None:
    _run_js(f"""
      const app = home();
      app.onPageChanged(1, {CHAT});
      for (const key of ['project:../fuori', 'project:', 'websocket:default', 'cron:x']) {{
        assert.equal(app.openChat(key), true, key);
        assert.equal(sessionManager.currentKey, 'websocket:default', key);
      }}
    """)


def test_a_room_over_the_chat_hides_it_and_coming_back_clears() -> None:
    _run_js(f"""
      const app = home();
      app.onPageChanged(1, {CHAT});
      app._setView('jenny');
      window.JennyNative.opened = 0;
      assert.equal(app.isChatOnScreen(), false);
      app._setView('chat');
      assert.equal(app.isChatOnScreen(), true);
      assert.equal(window.JennyNative.opened, 1);
    """)


# ── La pagina di un quaderno cancellato (M7, 25/09/2026) ─────────────────────

_GONE = """
      const app = home();
      const panel = { dataset: {} };
      let gone = false;
      app.homePages.panelOf = () => panel;
      app.homePages.goneHere = () => gone;
      const focus = [];
      app.focus = { restore: () => { focus.push('rimesso'); return true; } };
      app.input.blur = () => focus.push('tolto');
      const NOTEBOOK = { id: 'q1', kind: 'conversation', ref: 'project:piante' };
"""


def test_a_link_between_pages_leaves_a_trail_for_back() -> None:
    """Un link dal lettore a un'altra pagina dello stesso cassetto: Indietro
    torna alla pagina di prima, dov'eri, e solo dopo al cassetto. Prima il
    link non lasciava traccia e Indietro saltava la pagina A."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('piante'));
      app.reader = {
        path: null, editing: false, bodyEl: { scrollTop: 0 },
        isDirty: () => false, cancelEdit() {}, applyTranslations() {},
        load: async (nb, path) => { app.reader.path = path; return path.toUpperCase(); },
      };
      await app.openPage('a.md', 'A', 'c1');
      app.reader.bodyEl.scrollTop = 120;
      await app._followPage('b.md');
      assert.equal(app.reader.path, 'b.md');
      assert.equal(app.backBtn.attrs['aria-label'], 'torna alla pagina di prima');
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'reader', 'Indietro e\u2019 uscito dal lettore');
      assert.equal(app.reader.path, 'a.md', 'Indietro ha saltato la pagina A');
      assert.equal(app.reader.bodyEl.scrollTop, 120, 'la pagina A e\u2019 ripartita dall\u2019alto');
      assert.equal(app.backBtn.attrs['aria-label'], 'torna al cassetto');
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'drawer');
    """)


def test_the_back_arrow_says_the_page_it_returns_to() -> None:
    """Dalla bacheca aperta dai Quaderni la freccia porta ai Quaderni, e ora
    lo dice: prima si annunciava «torna alla chat»."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('piante'));
      app.onPageChanged(2, { id: 'notebooks', kind: 'notebooks', fixed: true });
      await app.openBoard();
      assert.equal(app.backBtn.attrs['aria-label'], 'torna ai Quaderni');
    """)


def test_scrolling_onto_a_notebook_door_opens_nothing() -> None:
    """La porta di un quaderno appeso si apre **al tocco** (dal 10/10/2026).
    Prima si apriva a scorrimento finito, e chi scorreva verso le pagine dopo
    restava chiuso nella sua bacheca, dove la pista non scorre."""
    _run_js(_GONE + """
      app.homePages.index = 1;
      app.onPageChanged(1, NOTEBOOK, { animated: true });
      await new Promise((r) => setTimeout(r, 400));
      assert.ok(!app.actions.includes('bacheca:piante'), 'la porta si e\u2019 aperta da sola');
      assert.equal(app.view, 'chat');
    """)


def test_back_from_a_board_opened_at_its_door_returns_to_the_door() -> None:
    """Aperta dalla porta, la bacheca torna alla porta — la pagina in alto da
    cui sei entrato — e non salta alla chat."""
    _run_js(_GONE + """
      app.homePages.index = 1;
      app.onPageChanged(1, NOTEBOOK);
      await app.openNotebook('piante');
      assert.equal(app.view, 'board');
      app.actions.length = 0;
      app.handleHardwareBack();
      assert.equal(app.view, 'chat');
      assert.ok(!app.actions.some((a) => a.startsWith('pagina:')), 'la pista e\u2019 saltata altrove');
      assert.equal(app.homePages.index, 1);
    """)


def test_enter_on_a_gone_notebook_sends_nothing() -> None:
    """L'ultima guardia: un Invio che arriva comunque al campo non parte."""
    _run_js(_GONE + """
      app.files = { count: 0, getImages: () => [], getAttachmentEntries: () => [], clear() {} };
      app.chat.appendOwn = () => {};
      app.input.value = 'ciao';
      gone = true;
      assert.equal(app._send(), false);
      assert.deepEqual(wsManager.posted, [], 'e\\u2019 partito un messaggio a un quaderno cancellato');
      gone = false;
      assert.equal(app._send(), true);
      assert.equal(wsManager.posted.length, 1);
    """)


# ── Rinominare e cancellare dalla pagina Quaderni (M8, 25/09/2026) ───────────

_NOTEBOOKS = """
      const app = home();
      await app.switchConversation(projectKey('piante'));
      app.homePages.homeConversation = projectKey('piante');
      app.homePages.renameConversation = function (v, n) {
        if (this.homeConversation === v) this.homeConversation = n;
      };
      /* La regola delle pagine vera, da qui (la pagina Quaderni), porta alla
         pagina chat: il finto lo segna. */
      app.homePages.openConversation = (k) => {
        app.actions.push('dirottata:' + k);
        return app.showConversation(k);
      };
      app.who.refresh = async () => {};
      app.pagesPort = () => ({ reload: async () => {} });
      app.actions.length = 0;
"""


def test_renaming_the_notebook_you_are_in_keeps_you_on_the_notebooks_page() -> None:
    """Rinominare dalla pagina Quaderni portava alla pagina chat, e la bozza
    restava sotto la chiave vecchia — cioe' persa."""
    _run_js(_NOTEBOOKS + """
      app.input.value = 'mezza frase';
      writtenName = 'orto';
      assert.equal(await app.renameNotebook('piante'), true);
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.ok(!app.actions.some((f) => f.startsWith('dirottata')), 'rinominare ha portato alla chat');
      assert.equal(app.input.value, 'mezza frase', 'la bozza si e\\u2019 persa col nome vecchio');
      assert.ok(!app._drafts.has('project:piante'), 'resta una bozza sotto il nome vecchio');
      assert.equal(app.homePages.homeConversation, 'project:orto');
    """)


def test_the_draft_of_a_renamed_notebook_follows_the_new_name() -> None:
    _run_js(_NOTEBOOKS + """
      app.input.value = 'da finire';
      await app.switchConversation(null);
      writtenName = 'orto';
      await app.renameNotebook('piante');
      assert.equal(sessionManager.currentKey, 'websocket:default');
      await app.showConversation(projectKey('orto'));
      assert.equal(app.input.value, 'da finire', 'la bozza e\\u2019 rimasta al nome vecchio');
    """)


def test_deleting_the_notebook_you_are_in_keeps_you_on_the_notebooks_page() -> None:
    _run_js(_NOTEBOOKS + """
      app.input.value = 'mezza frase';
      assert.equal(await app.deleteNotebook('piante'), true);
      assert.equal(sessionManager.currentKey, 'websocket:default');
      assert.ok(!app.actions.some((f) => f.startsWith('dirottata')), 'cancellare ha portato alla chat');
      assert.equal(app.homePages.homeConversation, 'websocket:default');
      assert.ok(!app._drafts.has('project:piante'), 'la bozza di un quaderno cancellato resta in memoria');
    """)


def test_deleting_a_notebook_puts_the_chat_back_where_the_chat_page_is() -> None:
    """La chat parcheggiata nella pagina del quaderno cancellato torna alla
    conversazione della pagina chat, non d'ufficio alla personale."""
    _run_js(_NOTEBOOKS + """
      app.homePages.homeConversation = projectKey('erbe');
      await app.deleteNotebook('piante');
      assert.equal(sessionManager.currentKey, 'project:erbe');
      assert.equal(app.homePages.homeConversation, 'project:erbe');
    """)


def test_a_deletion_that_did_not_happen_moves_nothing() -> None:
    _run_js(_NOTEBOOKS + """
      confirmDelete = false;
      assert.equal(await app.deleteNotebook('piante'), false);
      assert.equal(sessionManager.currentKey, 'project:piante');
      confirmDelete = true;
    """)


# ── Il nome di lei (M10, 25/09/2026) ─────────────────────────────────────────


def test_the_personal_conversation_is_named_after_her() -> None:
    """La fila e i Quaderni dicevano «Jenny» comunque si chiamasse: il nome
    era il testo fisso dell'intestazione. Ora e' `bot_name`, e la fila che lo
    scrive si ridisegna quando arriva. Dal 05/10/2026 i Quaderni non lo
    scrivono piu' (la conversazione personale e' uscita dalla pagina): un nome
    nuovo non li ridisegna per niente."""
    _run_js("""
      const app = home();
      let draws = 0, rows = 0;
      app.strip.draw = () => { draws += 1; };
      app.who.render = () => { rows += 1; };
      settingsPayload = { agent: { bot_name: 'Ada' } };
      await app._readName();
      assert.deepEqual(app._chatName(), { name: 'Ada', color: null });
      assert.equal(draws, 1, 'la fila dice ancora il nome di prima');
      assert.equal(rows, 0, 'i Quaderni si ridisegnano per un nome che non portano');
    """)


def test_an_empty_or_unread_name_falls_back_like_the_server() -> None:
    _run_js("""
      const app = home();
      settingsPayload = null;
      await app._readName();
      assert.equal(app._chatName().name, DEFAULT_BOT_NAME);
      settingsPayload = { agent: { bot_name: '   ' } };
      await app._readName();
      assert.equal(app._chatName().name, DEFAULT_BOT_NAME);
    """)


def test_a_saved_name_reaches_the_row_and_the_cache() -> None:
    """Salvato nella stanza di lei: la fila lo scrive subito, e la pagina
    Impostazioni riaperta non rimette il nome letto la prima volta (la cache,
    come per la finestra flottante)."""
    _run_js("""
      const app = home();
      settingsPayload = { agent: { bot_name: 'Ada' }, floating: null };
      app.homePages.goToId('settings');
      await app.powerOn;
      assert.deepEqual(app.names, ['Ada']);
      app._keepName('Vera');
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app._chatName().name, 'Vera', 'la fila dice ancora il nome vecchio');
      app.homePages.goTo(app.homePages.chatIndex);
      app.homePages.goToId('settings');
      await app.powerOn;
      /* Riaprendo si rilegge anche il server (qui il finto e' lo stesso
         oggetto della cache, cioe' un server che il nome l'ha salvato): la
         cache ridisegna «Vera» subito, la risposta nuova lo conferma. */
      assert.equal(settingsCalls, 2);
      assert.deepEqual(app.names, ['Ada', 'Vera', 'Vera'], 'la cache ha rimesso il nome vecchio');
      assert.equal(app._chatName().name, 'Vera');
    """)


def test_settings_that_could_not_be_read_do_not_say_an_empty_name() -> None:
    """`null`, cioe' «non lo so»: con `''` la stanza offriva «Salva» contro
    un nome vuoto che nessuno aveva scelto."""
    _run_js("""
      const app = home();
      settingsPayload = null;
      app.homePages.goToId('settings');
      await app.powerOn;
      assert.deepEqual(app.names, [null]);
    """)


def test_the_name_is_asked_at_start_and_heard_from_her_room() -> None:
    """I due fili che `init` e il costruttore legano, e che il banco sopra non
    puo' esercitare: la lettura all'avvio, e l'avviso della stanza di lei."""
    src = APP_JS.read_text(encoding="utf-8")
    init = member(src, "init", prefixes=("async ",))
    assert "this._readName()" in init, "all'avvio il nome non si chiede"
    assert "onName: (name) => this._keepName(name)" in src, "la stanza di lei non avvisa il guscio"
    html = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    assert 'id="home-head-name"></span>' in html, "l'intestazione porta di nuovo un nome fisso"


# ── Home con l'editor del lettore modificato ─────────────────────────────────

_DIRTY_READER = """
      const app = home();
      await app.switchConversation(projectKey('piante'));
      let dirty = true;
      app.reader = {
        isDirty: () => dirty,
        blurEditor() {},
        cancelEdit() { dirty = false; app.actions.push('modifiche buttate'); },
        askCancel() { app.actions.push('annulla chiesto'); },
        applyTranslations() {},
        editing: true,
      };
      app._setView('board');
      app._setView('reader');
      app.actions.length = 0;
"""


def test_home_waits_for_the_reader_confirm_before_switching() -> None:
    """Home differiva il cambio di stanza (la conferma di `_setView`) ma
    cambiava conversazione subito, sotto la modale: con un no restavi nel
    lettore di un quaderno mentre la chat era gia' la personale."""
    _run_js(_DIRTY_READER + """
      app.goHome();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(sessionManager.currentKey, 'project:piante', 'la conversazione e\u2019 cambiata sotto la conferma');
      assert.equal(app.view, 'reader');
      reply(true);
      await new Promise((r) => setTimeout(r, 0));
      assert.ok(app.actions.includes('modifiche buttate'));
      assert.equal(app.view, 'chat');
      assert.equal(sessionManager.currentKey, 'websocket:default');
    """)


def test_back_in_the_editor_closes_only_the_editor() -> None:
    """Indietro con l'editor aperto chiude l'editor (chiedendo, se c'e' del
    lavoro) e resta sulla pagina. Prima usciva dal lettore: con un si' alla
    conferma buttava le modifiche *e* usciva, due cose per una pressione."""
    _run_js(_DIRTY_READER + """
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'reader', 'Indietro e\u2019 uscito dalla pagina');
      assert.ok(app.actions.includes('annulla chiesto'), 'non ha chiesto all\u2019editor');
      assert.equal(reply, null, 'ha chiesto di uscire dal lettore');
    """)


def test_home_asks_about_the_edit_once() -> None:
    """Home chiude tutto in un giro: la domanda sul lavoro non salvato la fa
    il cambio di stanza, una volta sola, non l'editor a ogni giro."""
    _run_js(_DIRTY_READER + """
      app.goHome();
      await new Promise((r) => setTimeout(r, 0));
      assert.ok(!app.actions.includes('annulla chiesto'), 'Home ha chiesto all\u2019editor');
    """)


def test_saying_no_to_the_reader_confirm_keeps_everything() -> None:
    _run_js(_DIRTY_READER + """
      app.goHome();
      reply(false);
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'reader');
      assert.equal(sessionManager.currentKey, 'project:piante');
      assert.ok(!app.actions.includes('modifiche buttate'));
    """)


def test_a_tapped_alert_keeps_the_reader_edit_under_the_chat() -> None:
    """L'avviso non esce dal lettore, quindi non chiede di buttare le
    modifiche: la chat sale sopra l'editor, e richiusa lo ritrovi a meta'."""
    _run_js(_DIRTY_READER + """
      reply = null;
      assert.equal(app.openChat(), true);
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(reply, null, 'ha chiesto di buttare le modifiche');
      assert.equal(app.view, 'reader');
      assert.equal(sessionManager.currentKey, 'websocket:default');
      app.closeChatOver();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.view, 'reader');
      assert.equal(sessionManager.currentKey, 'project:piante');
      assert.ok(!app.actions.includes('modifiche buttate'));
    """)


def test_a_page_title_that_arrives_after_back_does_not_take_the_head() -> None:
    """La pagina si legge dalla rete: tornati alle pagine prima che arrivi,
    il suo titolo finiva nella testa delle pagine al posto del quaderno."""
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      let arrives;
      app.reader = {
        load: () => new Promise((r) => { arrives = r; }),
        isDirty: () => false, cancelEdit() {}, applyTranslations() {}, editing: false,
      };
      app._setView('board');
      const open = app.openPage('semina.md', 'Semina');
      assert.equal(app.nameEl.textContent, 'Semina');
      app.handleHardwareBack();
      assert.equal(app.view, 'board');
      assert.equal(app.nameEl.textContent, 'orto');
      arrives('Semina di marzo');
      await open;
      assert.equal(app.nameEl.textContent, 'orto', 'il titolo della pagina lasciata ha preso la testa');
    """)


def test_a_page_title_that_arrives_in_the_reader_is_written() -> None:
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app.reader = {
        load: async () => 'Semina di marzo',
        isDirty: () => false, cancelEdit() {}, applyTranslations() {}, editing: false,
      };
      app._setView('board');
      await app.openPage('semina.md', 'Semina');
      assert.equal(app.nameEl.textContent, 'Semina di marzo');
    """)


def test_a_title_from_a_save_after_leaving_the_reader_is_dropped() -> None:
    """Lo stesso per il titolo che il lettore annuncia dopo un salvataggio o
    un conflitto (`onTitle`): e' lo stesso cancello."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "this.reader.onTitle = (title) => this._readerTitle(title);" in src
    _run_js("""
      const app = home();
      await app.switchConversation(projectKey('orto'));
      app._setView('board');
      app._readerTitle('Semina di marzo');
      assert.equal(app.nameEl.textContent, 'orto');
    """)


def test_jenny_stands_on_the_real_composer_not_on_a_photo() -> None:
    """Le foto del trasloco sono copie della chat, composer compreso: una
    ricerca per classe poteva trovare quella di una foto prima nel documento,
    e il pavimento di Jenny si misurava su una copia."""
    _run_js("""
      const app = home();
      const listens = { addEventListener() {} };
      Object.assign(app.attach, listens);
      Object.assign(app.send, listens);
      app.pending = Object.assign(makeEl('div'), listens);
      Object.assign(app.input, listens, { scrollHeight: 20 });
      app.composer = { offsetHeight: 90 };
      const composerSnapshot = { offsetHeight: 12 };
      const find = document.querySelector;
      document.querySelector = (sel) => (sel === '.home-composer' ? composerSnapshot : find(sel));
      app._entry = { id: 'chat', kind: 'chat', fixed: true };
      app._bindComposer();
      document.querySelector = find;
      assert.equal(document.documentElement.style.props['--home-composer-h'], '90px',
                   'il pavimento e\u2019 stato misurato sul composer di una foto');
    """)


def test_every_sheet_that_back_closes_counts_as_something_above() -> None:
    """Chi chiede «c'e' qualcosa sopra?» e chi chiude gli strati leggono lo
    stesso elenco di fogli: erano due elenchi scritti a mano, e un foglio
    nuovo aggiunto a uno solo avrebbe lasciato la tastiera al campo sotto."""
    _run_js("""
      const app = home();
      for (const id of [...SHARED_DIALOGS, ...LONG_PRESS_SHEETS, REPORT_SHEET]) {
        const sheet = document.getElementById(id);
        sheet.open = true;
        sheet.close = function () { this.open = false; };
        assert.equal(app.hasOverlayAbove(), true, id + ' sopra la chat non conta');
        assert.equal(app._closeOverlays(), true, id + ': Indietro non lo chiude');
        assert.equal(sheet.open, false);
        assert.equal(app.hasOverlayAbove(), false);
      }
    """)


# ── Jenny fra pagine e stanze ────────────────────────────


def test_a_page_without_composer_sends_her_to_the_edge() -> None:
    """Fino al 28/09/2026 nelle pagine App e Impostazioni lei restava fuori, e
    il tocco non apriva niente. Adesso la chat e' a schermo solo nella pagina
    chat: fuori sta al bordo, e un tocco porta su la chat."""
    _run_js("""
      const app = home();
      app._entry = { id: 'app', kind: 'app' };
      app._syncJennyPlace();
      assert.equal(app.jennyChat, false, 'fuori dalla chat lei crede di averla a schermo');
      assert.deepEqual(app.actions, ['fuori:false']);
      app._entry = { id: 'settings', kind: 'settings' };
      app.actions.length = 0;
      app._syncJennyPlace();
      assert.deepEqual(app.actions, [], 'fra due pagine senza chat resta dov\\'e\\'');
      app._entry = { id: 'chat', kind: 'chat' };
      app._syncJennyPlace();
      assert.equal(app.jennyChat, true);
      assert.deepEqual(app.actions, ['fuori:true'], 'tornando si rimette com\\'era');
    """)


def test_put_away_in_the_chat_she_stays_away_on_the_way_back() -> None:
    _run_js("""
      const app = home();
      app._entry = { id: 'chat', kind: 'chat' };
      app._jennyOut = false;
      app._entry = { id: 'app', kind: 'app' };
      app._syncJennyPlace();
      app._entry = { id: 'chat', kind: 'chat' };
      app.actions.length = 0;
      app._syncJennyPlace();
      assert.deepEqual(app.actions, ['fuori:false'], 'metterla via era una tua decisione');
    """)


def test_a_room_counts_as_away_even_on_the_chat_page() -> None:
    _run_js("""
      const app = home();
      app._entry = { id: 'chat', kind: 'chat' };
      app._setView('jenny');
      assert.equal(app.jennyChat, false);
      app._setView('chat');
      assert.equal(app.jennyChat, true);
    """)


def test_back_closes_the_chat_over_the_page_before_an_app_under_it() -> None:
    """La chat sopra la pagina sta sopra la mini-app (112 contro 110): Indietro
    chiude prima lei, e l'app alla pressione dopo."""
    _run_js("""
      const app = home();
      let closedApp = false;
      app._appActions = { handleBack: () => { closedApp = true; return true; }, isAppOpen: () => true };
      app.openChatOver();
      assert.equal(app.chatOver, true);
      assert.equal(app._closeOverlays(), true);
      assert.equal(app.chatOver, false);
      assert.equal(closedApp, false, 'Indietro ha chiuso l\\'app sotto la chat');
      assert.equal(app._closeOverlays(), true);
      assert.equal(closedApp, true, 'alla pressione dopo, l\\'app');
    """)


def test_entering_a_notebook_does_not_tell_the_shell_the_personal_chat_passed_by() -> None:
    """`openNotebook` passa dalla pagina chat, ancora sulla personale: detto al
    guscio, cancellava gli avvisi personali non letti. Misurato sull'emulatore
    il 10/10/2026 toccando l'avviso di un quaderno con un avviso personale in
    tendina."""
    _run_js(f"""
      const app = home();
      app.onPageChanged(1, {CHAT});
      app._setView('jenny');
      window.JennyNative.openedWith.length = 0;
      await app.openNotebook('orto');
      assert.ok(!window.JennyNative.openedWith.includes(null), 'la personale e\\u2019 stata detta a schermo');
      app.openChatOver();
      assert.deepEqual(window.JennyNative.openedWith, ['project:orto']);
    """)


def test_a_cold_start_from_a_notebook_alert_lands_in_that_notebook() -> None:
    """Il tocco sull'avviso di un quaderno con l'activity morta arriva come
    `?mode=chat&chat=project:<nome>` (`MainActivity.buildGatewayUrl`). Mentre il
    posto si riprende la casa passa dalla pagina chat personale, e il guscio non
    lo deve sentire: cancellerebbe gli avvisi personali non letti."""
    _run_js(f"""
      globalThis.location = {{ search: '?mode=chat&chat=project%3Aorto' }};
      const app = home();
      app.onPageChanged(1, {CHAT});
      notebooksOnDisk = ['orto'];
      window.JennyNative.openedWith.length = 0;
      app._placeHeld = true;
      await app._restorePlace();
      assert.equal(sessionManager.currentKey, 'project:orto');
      assert.equal(app.view, 'board');
      assert.equal(app.chatOver, true);
      assert.deepEqual(window.JennyNative.openedWith, [], 'il guscio ha sentito il passaggio');
      app._placeHeld = false;
      app._reportChatOnScreen();
      assert.deepEqual(window.JennyNative.openedWith, ['project:orto']);
    """)


def test_a_cold_start_for_the_personal_chat_does_not_restore_the_old_place() -> None:
    _run_js(f"""
      globalThis.location = {{ search: '?mode=chat' }};
      savedPlace = JSON.stringify({{ at: Date.now(), page: 'chat', view: 'board', notebook: 'orto' }});
      const app = home();
      app.onPageChanged(1, {CHAT});
      await app._restorePlace();
      assert.equal(sessionManager.currentKey, 'websocket:default');
      assert.equal(app.view, 'chat');
    """)
