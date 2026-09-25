"""«Aggiornamenti» in casa: cosa dice il pallino, e cosa dice la riga.

La macchina a stati sta altrove ed e' misurata altrove
(`test_update_flow_client.py`): questa e' la **seconda vista** di quella, e
qui si misura solo cio' che e' di questa vista.

**Il pallino dice il meccanismo, non la versione.** Verde vuol dire «il
controllo funziona», non «sei aggiornata»: un manifest irraggiungibile da un
mese mostrerebbe altrimenti la stessa schermata di chi lo e' davvero, e su un
telefono che nessuno guarda quella differenza non la scopre piu' nessuno. Da
cui la regola che vale il banco: **`warn` vince su `new`** — se i controlli
non arrivano piu' al server, quel che sai di una versione nuova e' vecchio
quanto l'ultimo esito positivo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
ROOM_JS = ASSETS / "home-updates.js"
FLOW_JS = ASSETS / "shared" / "update-flow.js"
WHEN_JS = ASSETS / "shared" / "when.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


def _const(source: str, name: str) -> str:
    m = re.search(rf"(?m)^export const {re.escape(name)} = (.+);$", source)
    assert m, f"const {name} non trovata"
    return f"const {name} = {m.group(1)};"


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

function makeEl(tag) {
  const el = {
    tag, className: '', textContent: '', href: '', hidden: false, disabled: false,
    style: {}, attrs: {}, children: [], listeners: {},
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(t, fn) { (el.listeners[t] ||= []).push(fn); },
    appendChild(c) { el.children.push(c); return c; },
    replaceChildren(...n) { el.children = n; },
    classList: {
      add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
      remove(n) {
        el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
      },
      contains(n) { return String(el.className).split(' ').includes(n); },
      toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
    },
  };
  return el;
}

const nodi = {};
const document = {
  createElement: (t) => makeEl(t),
  getElementById: (id) => (nodi[id] ||= makeEl('div')),
};

const brindisi = [];
function showToast(m, t) { brindisi.push([m, t]); }
const api = { _fetch: () => Promise.reject(new Error('il banco non chiama la rete')) };

/* Il flusso vero, con la rete spenta: qui interessa la vista, non la
   macchina — quella ha il suo banco. */
__STALE_MS__
__POLL_MS__
__POLL_MAX__
__PHASE_KEY__
__WHEN_TEXT__
__CHECK_LINES__

class UpdateFlow {
  __FLOW_CTOR__
  __FLOW_BUSY__
  __FLOW_IDLE__
  __FLOW_STALE__
  __FLOW_CHANGED__
  __FLOW_STOP__
  __FLOW_RESUME__
  __FLOW_SCHEDULE__
}

__UPDATES_VALUE__
__UPDATES_MOOD__

class HomeUpdates {
  __CTOR__
  __SET_VERSION__
  __OPEN__
  __CLOSE__
  __VALUE__
  __APPLY_TRANSLATIONS__
  __PAINT__
  __PAINT_STATE__
  __PAINT_PROGRESS__
  __PAINT_LINES__
}

const giorno = 86400000;
function room(version) {
  for (const k of Object.keys(nodi)) delete nodi[k];
  brindisi.length = 0;
  const s = new HomeUpdates({});
  s.setVersion(version);
  return s;
}
function pallino() { return nodi['home-update-dot'].className; }
function righe() {
  return nodi['home-update-lines'].children.map(
    (r) => [r.textContent, String(r.className).includes('is-warn')]);
}
"""


def _harness() -> str:
    room = ROOM_JS.read_text(encoding="utf-8")
    flow = FLOW_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__STALE_MS__", _const(flow, "STALE_MS"))
        .replace("__POLL_MS__", _const(flow, "POLL_MS"))
        .replace("__POLL_MAX__", _const(flow, "POLL_MAX"))
        .replace("__PHASE_KEY__", function(flow, "phaseKey"))
        .replace("__WHEN_TEXT__", function(WHEN_JS.read_text(encoding="utf-8"), "whenText"))
        .replace("__CHECK_LINES__", function(flow, "checkLines"))
        .replace("__FLOW_CTOR__", member(flow, "constructor"))
        .replace("__FLOW_BUSY__", member(flow, "busy"))
        .replace("__FLOW_IDLE__", member(flow, "idleTimer"))
        .replace("__FLOW_STALE__", member(flow, "_stale"))
        .replace("__FLOW_CHANGED__", member(flow, "_changed"))
        .replace("__FLOW_STOP__", member(flow, "stop"))
        .replace("__FLOW_RESUME__", member(flow, "resume"))
        .replace("__FLOW_SCHEDULE__", member(flow, "_schedulePoll"))
        .replace("__UPDATES_VALUE__", function(room, "updatesValue"))
        .replace("__UPDATES_MOOD__", function(room, "updatesMood"))
        .replace("__CTOR__", member(room, "constructor"))
        .replace("__SET_VERSION__", member(room, "setVersion"))
        .replace("__OPEN__", member(room, "open"))
        .replace("__CLOSE__", member(room, "close"))
        .replace("__VALUE__", member(room, "value"))
        .replace("__APPLY_TRANSLATIONS__", member(room, "applyTranslations"))
        .replace("__PAINT__", member(room, "_paint"))
        .replace("__PAINT_STATE__", member(room, "_paintState"))
        .replace("__PAINT_PROGRESS__", member(room, "_paintProgress"))
        .replace("__PAINT_LINES__", member(room, "_paintLines"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


def test_a_broken_check_wins_over_a_new_version() -> None:
    """Se i controlli partono e non arrivano piu' al server, quel che sai di
    una versione nuova e' vecchio quanto l'ultimo esito positivo: annunciarla
    come una novita' fresca sarebbe la cosa sbagliata da dire."""
    _run_js("""
      const rotto = { current: '0.11.0', latest: '0.12.0', update_available: true,
                      last_check: Date.now(), last_success: Date.now() - 30 * giorno };
      assert.equal(updatesMood(rotto), 'warn');

      const nuova = { current: '0.11.0', latest: '0.12.0', update_available: true,
                      last_check: Date.now(), last_success: Date.now() };
      assert.equal(updatesMood(nuova), 'new');

      const aposto = { current: '0.11.0', update_available: false,
                       last_check: Date.now(), last_success: Date.now() };
      assert.equal(updatesMood(aposto), 'ok');
    """)


def test_the_dot_says_the_mechanism() -> None:
    """Verde vuol dire «il controllo funziona», non «sei aggiornata»: senza
    questa distinzione un manifest irraggiungibile da un mese mostra la stessa
    schermata di chi e' aggiornato davvero."""
    _run_js("""
      room({ current: '0.11.0', last_check: Date.now(), last_success: Date.now() });
      assert.ok(pallino().includes('is-ok'), pallino());

      room({ current: '0.11.0', last_check: Date.now(), last_success: Date.now() - 30 * giorno });
      assert.ok(pallino().includes('is-warn'), pallino());
      const dette = righe();
      assert.equal(dette.length, 2, 'la riga che spiega il guasto non c e');
      assert.equal(dette[1][1], true, 'il guasto non e segnalato come tale');
    """)


def test_the_row_carries_the_version_and_where_it_is_going() -> None:
    """Una versione che non si sa non si finge: stringa vuota, e la riga resta
    muta."""
    _run_js("""
      assert.equal(updatesValue(null), '');
      assert.equal(updatesValue({}), '');

      const ferma = updatesValue({ current: '0.11.0', update_available: false });
      assert.ok(ferma.includes('0.11.0'));
      assert.ok(!ferma.includes('{'), 'il segnaposto e rimasto dentro: ' + ferma);

      const verso = updatesValue({ current: '0.11.0', latest: '0.12.0', update_available: true });
      assert.ok(verso.includes('0.11.0') && verso.includes('0.12.0'), verso);

      /* Annunciata senza numero: non si scrive una freccia verso il nulla. */
      const senzaNumero = updatesValue({ current: '0.11.0', update_available: true });
      assert.ok(!senzaNumero.includes('\\u2192'), senzaNumero);
    """)


def test_the_install_button_exists_only_when_there_is_something_to_install() -> None:
    _run_js("""
      const s = room({ current: '0.11.0', update_available: false,
                         last_check: Date.now(), last_success: Date.now() });
      assert.equal(nodi['home-update-install'].hidden, true);
      assert.equal(nodi['home-update-notes'].hidden, true, 'un link alle note che non ci sono');

      s.setVersion({ current: '0.11.0', latest: '0.12.0', update_available: true,
                     summary: 'tre cose nuove', notes_url: 'https://esempio.invalid/note',
                     last_check: Date.now(), last_success: Date.now() });
      assert.equal(nodi['home-update-install'].hidden, false);
      assert.equal(nodi['home-update-summary'].textContent, 'tre cose nuove');
      assert.equal(nodi['home-update-notes'].href, 'https://esempio.invalid/note');
      assert.ok(nodi['home-update-headline'].textContent.includes('0.12.0'));
    """)


def test_a_critical_update_does_not_read_like_an_ordinary_one() -> None:
    """Un aggiornamento critico non e' «una versione nuova con piu' cose»: e'
    una correzione che conviene installare subito, e deve leggersi diversamente
    gia' da qui."""
    _run_js("""
      const normale = room({ current: '0.11.0', latest: '0.12.0', update_available: true,
                               last_check: Date.now(), last_success: Date.now() });
      const parole = nodi['home-update-headline'].textContent;
      assert.equal(nodi['home-update-install'].classList.contains('is-critical'), false);

      normale.setVersion({ current: '0.11.0', latest: '0.12.0', update_available: true,
                           critical: true, last_check: Date.now(), last_success: Date.now() });
      assert.notEqual(nodi['home-update-headline'].textContent, parole,
        'un aggiornamento critico si legge come uno qualunque');
      assert.equal(nodi['home-update-install'].classList.contains('is-critical'), true);
    """)


def test_the_progress_block_is_not_there_before_you_press() -> None:
    """Prima di premere il bottone non c'e' niente da raccontare, e un
    riquadro vuoto sembrerebbe qualcosa che e' andato storto."""
    _run_js("""
      const s = room({ current: '0.11.0', last_check: Date.now(), last_success: Date.now() });
      assert.equal(nodi['home-update-progress'].hidden, true);

      /* Durante: la nota dice cosa aspettarsi, la fase dice a che punto e', e
         la barra c'e' solo mentre qualcosa si muove davvero. */
      s.flow.state = { busy: true, noteKey: 'settings.update.starting',
                       phase: 'downloading', progress: 40, detail: '10 MB' };
      s._paint();
      assert.equal(nodi['home-update-progress'].hidden, false);
      assert.equal(nodi['home-update-note'].textContent, i18n.t('settings.update.starting'));
      assert.equal(nodi['home-update-phase'].textContent,
                   i18n.t('settings.update.phaseDownloading'));
      assert.equal(nodi['home-update-detail'].textContent, '10 MB');
      assert.equal(nodi['home-update-track'].hidden, false);
      assert.equal(nodi['home-update-bar'].style.width, '40%');
      assert.equal(nodi['home-update-install'].disabled, true, 'si puo premere due volte');

      /* In attesa della conferma di sistema la barra non c'e': non si sta
         muovendo niente, sta aspettando una persona. */
      s.flow.state = { busy: false, noteKey: 'settings.update.promptNote',
                       phase: 'prompt', progress: 0, detail: '' };
      s._paint();
      assert.equal(nodi['home-update-track'].hidden, true);
      assert.equal(nodi['home-update-install'].disabled, false,
        'la conferma persa non si puo piu riprovare');
    """)


def test_leaving_stops_the_polling_and_invalidates_the_generation() -> None:
    """La guardia di generazione ferma le continuazioni; il timer va spento
    comunque, o terrebbe sveglia una stanza che non c'e' piu'."""
    _run_js("""
      const s = room({ current: '0.11.0' });
      s.flow.state = { busy: true, noteKey: null, phase: 'downloading', progress: 5, detail: '' };
      s.flow._schedulePoll(0);
      assert.ok(s.flow._timer, 'il polling non e partito');

      const prima = s._gen;
      s.close();
      assert.equal(s.flow._timer, null, 'il timer e rimasto vivo');
      assert.notEqual(s._gen, prima, 'la generazione non e cambiata: le continuazioni scrivono ancora');
    """)


def test_not_knowing_the_version_is_said_and_not_masked() -> None:
    """Visto sul rig: prima che `/api/settings` risponda la scheda diceva «Sei
    alla , ed e' l'ultima» — una frase con un buco, e per di piu' una
    rassicurazione inventata. Non sapere e' uno stato normale del primo
    secondo, e si dice."""
    _run_js("""
      room(null);
      assert.equal(nodi['home-update-headline'].textContent, i18n.t('home.updates.unknown'));

      room({ current: '0.11.0' });
      assert.ok(nodi['home-update-headline'].textContent.includes('0.11.0'));
      assert.ok(!nodi['home-update-headline'].textContent.includes('{'),
        'il segnaposto e rimasto dentro');
    """)
