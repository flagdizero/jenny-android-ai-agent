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
};

const api = { clientLog() {} };

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
    };
    this.activity = { stop: () => this.fatti.push('riga ferma') };
    this.jenny = {
      noteTurnRunning: (v) => this.fatti.push('turno:' + v),
      idle: () => this.fatti.push('quiete'),
    };
    this.who = {
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
}

function casa() {
  lightbox = null;
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
    )


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
