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
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "casa-app.js"
CREATE_JS = ASSETS / "shared" / "project-create.js"
WHO_JS = ASSETS / "casa-who.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


def _const_block(source: str, name: str) -> str:
    """Una costante di modulo su più righe, presa dal sorgente e non riscritta."""
    m = re.search(rf"(?ms)^(?:export )?const {re.escape(name)} = \{{.*?^\}};$", source)
    assert m, f"const {name} non trovata"
    return m.group(0).removeprefix("export ")


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0).replace("export function", "function")


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
const document = {
  querySelector: (sel) => (sel === '.image-lightbox' ? lightbox : null),
  getElementById: () => makeEl('button'),
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

/* `getSettings` e' il payload delle impostazioni, di cui la casa usa un campo
   solo: la versione. Qui e' sostituibile per poter misurare anche il caso in
   cui quel campo non c'e'. */
let settingsPayload = { version: { current: '0.11.0' } };
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
    this.dotEl = makeEl('span');
    this.kicker = makeEl('div');
    this.input = makeEl('textarea');
    this.emptyText = makeEl('div');
    this.send = makeEl('button');
    this.attach = makeEl('button');
    this.door = makeEl('button');
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
    /* «Tu e Jenny»: la scheda dell'officina e la riga della versione, che
       nasce nascosta e resta nascosta se la versione non si sa. */
    this.workshopName = makeEl('span');
    this.workshopHint = makeEl('span');
    this.versionEl = makeEl('div');
    this.versionEl.hidden = true;
    this._versionAsked = false;
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
      noteTurnRunning: (v) => this.fatti.push('turno:' + v),
      idle: () => this.fatti.push('quiete'),
      setOut: (v) => { this._jennyOut = v; this.fatti.push('fuori:' + v); },
    };
    this._jennyOut = true;
    this.who = {
      known: [{ name: 'piante', modified: 1 }],
      pagesOf: (name) => Promise.resolve(this.pageCounts?.[name] ?? null),
      invalidate: () => this.fatti.push('elenco da rileggere'),
      isOpen: false,
      close: () => { this.who.isOpen = false; this.fatti.push('tendina chiusa'); },
      render: () => {},
    };
  }
  _autosize() {}
  _renderPending() {}
  _setRunning(running) { this._running = running; this.fatti.push('ferma:' + running); }
  _showThreadError() { this._threadFailed = true; this.fatti.push('non si legge'); }
  __SWITCH__
  __APPLY_CONVERSATION__
  __RELEASE_TURN__
  __CLOSE_OVERLAYS__
  __BACK__
  __GO_HOME__
  __OPEN_CHAT__
  __APPLY_TRANSLATIONS__
  __CREATE_NOTEBOOK__
  __OPEN_PAGES__
  __GO_BACK_ONE_ROOM__
  __OPEN_TU__
  __LOAD_VERSION__
  __SET_VIEW__
  __APPLY_HEAD__
  __APPLY_BACK_LABEL__
  __UPDATE_PAGES_COUNT__
  __SET_HEAD_TITLE__
}

function casa() {
  lightbox = null;
  createOutcome = null;
  creations.length = 0;
  settingsPayload = { version: { current: '0.11.0' } };
  settingsCalls = 0;
  sessionManager.currentKey = sessionManager.personalKey;
  const app = new App();
  app._applyConversation();
  app.fatti.length = 0;
  return app;
}
"""


def _harness() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", _member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__DOT_COLOR__", _function(WHO_JS.read_text(encoding="utf-8"), "dotColor"))
        .replace("__SWITCH__", _member(src, "switchConversation"))
        .replace("__APPLY_CONVERSATION__", _member(src, "_applyConversation"))
        .replace("__RELEASE_TURN__", _member(src, "_releaseTurn"))
        .replace("__CLOSE_OVERLAYS__", _member(src, "_closeOverlays"))
        .replace("__BACK__", _member(src, "handleHardwareBack"))
        .replace("__GO_HOME__", _member(src, "goHome"))
        .replace("__OPEN_CHAT__", _member(src, "openChat"))
        .replace("__APPLY_TRANSLATIONS__", _member(src, "_applyTranslations"))
        .replace("__CREATE_NOTEBOOK__", _member(src, "createNotebook"))
        .replace("__PROJECT_WORDS__", _const_block(_read_create(), "PROJECT_WORDS"))
        .replace("__NOTEBOOK_WORDS__", _const_block(src, "NOTEBOOK_WORDS"))
        .replace("__OPEN_PAGES__", _member(src, "openPages"))
        .replace("__GO_BACK_ONE_ROOM__", _member(src, "goBackOneRoom"))
        .replace("__OPEN_TU__", _member(src, "openTu"))
        .replace("__LOAD_VERSION__", _member(src, "_loadVersion"))
        .replace("__APPLY_BACK_LABEL__", _member(src, "_applyBackLabel"))
        .replace("__SET_VIEW__", _member(src, "_setView"))
        .replace("__APPLY_HEAD__", _member(src, "_applyHead"))
        .replace("__UPDATE_PAGES_COUNT__", _member(src, "_updatePagesCount"))
        .replace("__SET_HEAD_TITLE__", _member(src, "_setHeadTitle"))
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
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


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


# ── L'intestazione ──────────────────────────────────────────────────────────


def test_the_header_says_which_notebook_you_are_in() -> None:
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      assert.equal(app.nameEl.textContent, 'piante');
      assert.equal(app.kicker.textContent, 'quaderno');
      assert.equal(app.input.placeholder, 'Scrivi a Jenny, nel quaderno');
      assert.ok(app.emptyText.textContent.includes('resta qui'), app.emptyText.textContent);
    """)


def test_the_house_takes_its_own_name_back() -> None:
    """Il nome personale è messo da parte all'avvio, non riletto dal titolo:
    leggerlo di lì, ora che il titolo porta il quaderno, lo farebbe restare
    «piante» per sempre."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      await app.switchConversation(null);
      assert.equal(app.nameEl.textContent, 'Jenny');
      assert.equal(app.kicker.textContent, 'conversazione personale');
      assert.equal(app.input.placeholder, 'Scrivi a Jenny');
      assert.ok(app.dotEl.hidden, 'la casa non è un quaderno fra i quaderni');
    """)


def test_the_dot_in_the_title_is_the_one_from_the_panel() -> None:
    """È l'unica cosa che lega la riga toccata alla stanza in cui sei finito:
    due colori diversi per lo stesso quaderno slegherebbero le due."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      assert.equal(app.dotEl.hidden, false);
      assert.equal(app.dotEl.style.background, dotColor('piante'));
    """)


# ── Il turno che resta indietro ─────────────────────────────────────────────


def test_leaving_closes_the_turn_that_was_running() -> None:
    """Il `turn_end` del turno in volo arriverà a una conversazione che non
    guardiamo più e verrà scartato: la faccia di Jenny resterebbe in pensiero
    per sempre, la riga di lavoro a girare, e il bottone a dire «ferma» senza
    niente da fermare."""
    _run_js("""
      const app = casa();
      app._releaseTurn();
      assert.deepEqual(app.fatti, ['riga ferma', 'turno:false', 'quiete', 'ferma:false']);
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


def test_one_press_closes_one_thing() -> None:
    """Con la tendina aperta sopra un quaderno, Indietro chiude la tendina e
    basta: chiudere anche il quaderno farebbe sparire due cose per un gesto."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      app.who.isOpen = true;
      app.fatti.length = 0;
      app.handleHardwareBack();
      await new Promise((r) => setTimeout(r, 0));
      assert.deepEqual(app.fatti, ['tendina chiusa']);
      assert.equal(sessionManager.currentKey, 'project:piante');
    """)


def test_home_means_the_personal_conversation() -> None:
    """«Sei a casa» torna a voler dire qualcosa il giorno in cui si può essere
    altrove."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('piante'));
      app.who.isOpen = true;
      app.fatti.length = 0;
      app.goHome();
      await new Promise((r) => setTimeout(r, 0));
      assert.ok(app.fatti.includes('tendina chiusa'));
      assert.equal(sessionManager.currentKey, 'websocket:default');
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
    """Gli strati vengono prima delle stanze: la tendina sta nel top layer, e
    chiuderla e' quel che l'occhio si aspetta da quel tasto."""
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app._setView('pages');
      app.who.isOpen = true;
      app.handleHardwareBack();
      assert.equal(app.who.isOpen, false, 'la tendina non si e\\u2019 chiusa');
      assert.equal(app.view, 'pages', 'la stanza se n\\u2019e\\u2019 andata insieme a lei');
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


# ── La quarta stanza: «Tu e Jenny» ──────────────────────────────────────────


def test_you_and_jenny_goes_back_to_the_chat() -> None:
    """La quarta stanza non sta nel ramo dei quaderni: da li' Indietro riporta
    alla conversazione, non alle pagine di qualcosa.

    E ci riporta **senza uscire dal quaderno**: «Tu e Jenny» si apre anche da
    dentro uno, e tornare indietro da una pagina di impostazioni non e' un modo
    di cambiare conversazione.
    """
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app.openTu();
      assert.equal(app.view, 'tu');
      assert.equal(app.shell.attrs['data-view'], 'tu', 'il CSS non sa in che stanza sei');
      app.handleHardwareBack();
      assert.equal(app.view, 'chat');
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
      assert.equal(dice('tu'), i18n.t('casa.back.chat'));
      assert.notEqual(i18n.t('casa.back.pages'), i18n.t('casa.back.chat'),
                      'le due frasi sono diventate la stessa, e il banco non misura piu\u2019 niente');
    """)


def test_talking_about_it_belongs_to_a_notebook() -> None:
    """«Parlane» riporta a parlare *di questo quaderno*: dentro «Tu e Jenny»
    non c'e' niente di cui parlare, e il bottone non ci va.

    Era `hidden = inChat`, che con tre stanze diceva la stessa cosa.
    """
    _run_js("""
      const app = casa();
      await app.switchConversation(projectKey('orto'));
      app._setView('pages');
      assert.equal(app.talkBtn.hidden, false, 'dalle pagine si torna a parlarne');
      app._setView('tu');
      assert.equal(app.talkBtn.hidden, true, '«Parlane» in mezzo alle impostazioni');
      assert.equal(app.pagesBtn.hidden, true, 'e nemmeno la pastiglia delle pagine');
      assert.equal(app.nameEl.textContent, i18n.t('casa.tu.title'), 'la testa non dice dove sei');
    """)


def test_the_version_is_asked_once_and_never_invented() -> None:
    """La riga nasce nascosta e resta nascosta finche' non c'e' un numero.

    `/api/settings` e' un payload grosso e di suo qui serve un campo: si chiede
    all'apertura della stanza, non al caricamento della casa, e una volta sola.
    Un numero che non si sa non si scrive — «versione {version}» con la graffa
    dentro sarebbe peggio di una riga che non c'e'.
    """
    _run_js("""
      const app = casa();
      app.openTu();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.versionEl.hidden, false);
      assert.ok(app.versionEl.textContent.includes('0.11.0'), app.versionEl.textContent);
      assert.ok(!app.versionEl.textContent.includes('{'), 'il segnaposto e\u2019 rimasto dentro');

      app._setView('chat');
      app.openTu();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(settingsCalls, 1, 'la versione viene richiesta a ogni apertura');
    """)


def test_a_version_that_is_not_known_leaves_no_line() -> None:
    """Impostazioni irraggiungibili, o un payload senza versione: la riga resta
    vuota e non occupa. Non e' un guasto di cui valga la pena parlare a chi sta
    guardando un tema."""
    _run_js("""
      const app = casa();
      settingsPayload = null;
      app.openTu();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(app.versionEl.hidden, true, 'una versione che non si sa e\u2019 finita a schermo');

      const altro = casa();
      settingsPayload = { version: {} };
      altro.openTu();
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(altro.versionEl.hidden, true);
      assert.equal(altro.versionEl.textContent, '');
    """)
