"""L'interruttore di sola lettura funziona al primo tocco, prima della rete.

`WriteSwitch._key` restava `null` fino al primo `syncFromSession`, e quello lo
chiama solo `loadInitialHistory` **dopo** che la fetch del thread è andata bene.
Quindi se il primo caricamento falliva — il caso normale su un telefono: gateway
ancora in piedi a metà, socket caduto — il tasto era morto: `toggle()` calcolava
`next = true` ma non registrava niente (`if (this._key)`), `_publish()` rileggeva
`this.readonly` ancora falso e uscìva subito, e `render()` ridisegnava
l'etichetta di prima. L'utente tocca "sola lettura", non succede nulla, nessuno
gliel'ha detto, e il messaggio dopo parte **scrivibile**.

È il guasto che questo modulo esiste per non fare: un messaggio partito
credendolo in sola lettura non si ritira. E non c'è nessuna ragione di aspettare
la rete, perché lo stato dell'interruttore è **di qui** — il server non lo tiene
da nessuna parte, il flag viaggia nell'envelope.

La chiave di partenza è quella **vera** della conversazione personale, non un
segnaposto: con un segnaposto la preferenza espressa prima del caricamento
finirebbe sotto una chiave che nessuno riguarda più, e il primo
`syncFromSession` riuscito riporterebbe l'interruttore su "scrive" da solo.

I metodi si estraggono dal sorgente e si eseguono in node, come in
``test_chat_switch_race_client.py``: il DOM è ridotto a quel poco che `render()`
tocca, così l'etichetta scritta a schermo è osservabile.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
SWITCH_JS = ASSETS / "shared" / "write-switch.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _member(source: str, name: str) -> str:
    m = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert m, f"{name} non trovato"
    return m.group(1) + "\n  }"


_HARNESS = """
import assert from 'node:assert/strict';

const i18n = { t: (key) => 'i18n:' + key };

/* Lo stato condiviso: `AppState.readonlyTurn` è ciò che `ws-manager.sendToChat`
   legge per mettere `readonly: true` nell'envelope. Qui si tiene anche l'elenco
   delle pubblicazioni, perché «non è cambiato niente» e «è cambiato due volte»
   sono due difetti diversi. */
const AppState = {
  readonlyTurn: false,
  published: [],
  set(key, value) { AppState[key] = value; AppState.published.push([key, value]); },
};

// Il session manager, per la sola cosa che l'interruttore gli chiede.
const sessionManager = { personalKey: 'websocket:default' };

/* Il DOM ridotto a quel che `render()` tocca: il modo, l'aria, l'icona e
   l'etichetta. È l'etichetta che dice all'utente se il tocco ha avuto effetto. */
function makeEl() {
  const mark = { className: '' };
  const label = { textContent: '' };
  return {
    dataset: {},
    attrs: {},
    mark,
    label,
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener() {},
    querySelector(sel) { return sel === '.write-switch-mark' ? mark : label; },
  };
}
const document = { getElementById: () => makeEl() };

class WriteSwitch {
  __CTOR__
  __SYNC__
  __READONLY__
  __TOGGLE__
  __PUBLISH__
  __RENDER__
}

// Lo stato pulito fra un caso e l'altro.
function makeSwitch() {
  AppState.readonlyTurn = false;
  AppState.published.length = 0;
  return new WriteSwitch();
}
"""


def _harness() -> str:
    src = _read(SWITCH_JS)
    return (
        _HARNESS.replace("__CTOR__", _member(src, "constructor"))
        .replace("__SYNC__", _member(src, "syncFromSession"))
        .replace("__READONLY__", _member(src, "readonly"))
        .replace("__TOGGLE__", _member(src, "toggle"))
        .replace("__PUBLISH__", _member(src, "_publish"))
        .replace("__RENDER__", _member(src, "render"))
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── Il primo tocco ──────────────────────────────────────────────────────────


def test_the_very_first_tap_takes_effect() -> None:
    """Il difetto, per intero: nessun `syncFromSession`, e il tasto deve mordere."""
    _run_js("""
      const sw = makeSwitch();

      sw.toggle();

      assert.equal(sw.readonly, true, 'il primo tocco non ha registrato nulla');
      assert.equal(AppState.readonlyTurn, true,
                   'il turno parte scrivibile: è il messaggio che non si ritira');
      /* L'avviso a chi possiede il composer è **questa** pubblicazione: c'era
         anche un `onChange` che nessuno montava (il chip legge
         `AppState.on('readonlyTurn')`), e un gancio che nessuno monta è un
         secondo modo di far sapere la stessa cosa. Tolto. */
      assert.deepEqual(AppState.published, [['readonlyTurn', true]]);
      // E si vede: modo, aria e etichetta.
      assert.equal(sw.el.dataset.mode, 'readonly');
      assert.equal(sw.el.attrs['aria-pressed'], 'true');
      assert.equal(sw.el.label.textContent, 'i18n:write.readonly',
                   "l'etichetta è rimasta quella di prima: il tocco sembra perso");
      assert.equal(sw.el.mark.className.includes('ti-eye'), true);
    """)


def test_the_key_is_never_null() -> None:
    """La chiave di partenza è quella vera della personale, non `null`."""
    _run_js("""
      const sw = makeSwitch();
      assert.equal(sw._key, 'websocket:default');
    """)


def test_a_load_that_arrives_later_does_not_undo_the_choice() -> None:
    """Il motivo per cui la chiave è quella vera e non un segnaposto.

    Con un segnaposto la preferenza espressa prima del caricamento resterebbe
    sotto una chiave che nessuno riguarda, e il `syncFromSession` riuscito
    rimetterebbe l'interruttore su "scrive": un interruttore che torna indietro
    da solo è peggio di uno che non si muove, perché la prima volta ha
    funzionato.
    """
    _run_js("""
      const sw = makeSwitch();
      sw.toggle();
      // Il thread arriva (o riprova e riesce) e conferma la conversazione aperta.
      sw.syncFromSession('websocket:default');
      assert.equal(sw.readonly, true, "la scelta dell'utente è stata dimenticata");
      assert.equal(AppState.readonlyTurn, true);
      assert.deepEqual(AppState.published, [['readonlyTurn', true]],
                       'una conferma che non cambia nulla non deve ripubblicare');
    """)


def test_a_missing_key_means_the_personal_chat_not_no_chat() -> None:
    """`syncFromSession(null)` non deve riaprire il buco da un'altra porta."""
    _run_js("""
      const sw = makeSwitch();
      sw.toggle();
      sw.syncFromSession(null);
      assert.equal(sw._key, 'websocket:default');
      assert.equal(sw.readonly, true);
      sw.toggle();
      assert.equal(sw.readonly, false, 'il tasto è morto dopo una chiave assente');
    """)


def test_tapping_again_goes_back_to_write() -> None:
    """L'interruttore è un interruttore: e il ritorno si pubblica."""
    _run_js("""
      const sw = makeSwitch();
      sw.toggle();
      sw.toggle();
      assert.equal(sw.readonly, false);
      assert.equal(AppState.readonlyTurn, false);
      assert.deepEqual(AppState.published,
                       [['readonlyTurn', true], ['readonlyTurn', false]]);
      assert.equal(sw.el.dataset.mode, 'write');
      assert.equal(sw.el.label.textContent, 'i18n:write.write');
    """)


# ── E resta per conversazione ───────────────────────────────────────────────


def test_the_preference_stays_per_conversation() -> None:
    """Un progetto parte scrivibile mentre la personale resta come l'avevi lasciata.

    È la proprietà che la correzione non deve costare: la chiave iniziale non è
    un contenitore unico per tutti.
    """
    _run_js("""
      const sw = makeSwitch();
      sw.toggle();                       // personale in sola lettura, prima di ogni rete
      sw.syncFromSession('project:bordi');
      assert.equal(sw.readonly, false, 'un progetto ha ereditato la preferenza della personale');
      assert.equal(AppState.readonlyTurn, false);

      sw.toggle();                       // e ora anche bordi
      assert.equal(sw.readonly, true);

      sw.syncFromSession('project:patreon');
      assert.equal(sw.readonly, false);

      sw.syncFromSession('websocket:default');
      assert.equal(sw.readonly, true, 'la personale non è tornata come era stata lasciata');
    """)


def test_a_switch_without_the_element_does_nothing_at_all() -> None:
    """Chat e onboarding condividono l'index: senza il blocco il modulo tace."""
    _run_js("""
      const saved = document.getElementById;
      document.getElementById = () => null;
      const sw = makeSwitch();
      document.getElementById = saved;
      assert.equal(sw.enabled, false);
      sw.toggle();
      assert.equal(AppState.readonlyTurn, false);
      assert.deepEqual(AppState.published, []);
    """)


# ── Guardie sul testo del sorgente ─────────────────────────────────────────
#
# Deboli per costruzione: provano che una riga c'è, non che faccia effetto.


def test_the_dead_guard_is_gone() -> None:
    """Guardia debole: la riga che rendeva morto il tasto non torni."""
    body = _member(_read(SWITCH_JS), "toggle")
    code = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$|\s//.*$", "", code)
    assert "this._byKey.set(this._key" in code
    assert not re.search(r"if \(this\._key\)", code), (
        "la preferenza torna a dipendere da una chiave che il primo tocco non ha ancora"
    )


def test_there_is_no_hook_nobody_mounts() -> None:
    """Guardia debole: `onChange` non torna, ne' dichiarato ne' chiamato.

    Era dichiarato nel costruttore e chiamato in `toggle`, e **nessuno lo
    montava** (nell'intero albero `assets/` c'erano solo quelle due righe). Quel
    che avrebbe servito lo fa l'iscrizione a `AppState.on('readonlyTurn')`, che
    e' anche la ragione per cui l'ordine dei due `syncFromSession` non conta.
    """
    # Senza commenti: il commento che dice perche' non c'e' piu' nomina il gancio.
    code = re.sub(r"/\*\*?.*?\*/", "", _read(SWITCH_JS), flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$|\s//.*$", "", code)
    assert "onChange" not in code, (
        "un gancio che nessuno monta e' un secondo modo di annunciare la stessa cosa"
    )
    switch_calls = [
        p.name
        for p in sorted(ASSETS.rglob("*.js"))
        if "writeSwitch.onChange" in p.read_text(encoding="utf-8")
    ]
    assert switch_calls == [], f"qualcuno lo monta adesso: {switch_calls}"


def test_the_starting_key_comes_from_the_session_manager() -> None:
    """Guardia debole: una sola definizione della chiave della personale.

    Riscriverla a mano qui sarebbe una seconda verità su quale sia la
    conversazione di partenza, e le due divergono al primo cambio di formato.
    """
    src = _read(SWITCH_JS)
    assert "sessionManager.personalKey" in src
    assert "'websocket:default'" not in src, (
        "la chiave della personale la conosce il session manager, non l'interruttore"
    )
