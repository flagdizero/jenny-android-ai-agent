"""Una sola tendina aperta per volta, sopra il composer.

La riga sopra il composer ha due controlli che aprono un pannello — lo scope chip
e i comandi — e i due pannelli si sovrappongono. Aprendo il secondo con il primo
già aperto restavano aperti **tutti e due**, uno sopra l'altro (misurato sul
telefono il 28/08).

La strada ovvia non funziona, e vale la pena sapere perché prima di toccare
questi test. Ogni chip chiama `stopPropagation()` sul proprio click, e **deve**:
senza, il click che apre la tendina arriverebbe al listener su `document` — lo
stesso che la chiude quando si tocca altrove — e la richiuderebbe nello stesso
gesto. Ma quel `stopPropagation` è anche il motivo per cui l'altro chip non vede
mai il click, quindi non può accorgersi di doversi chiudere.

Serve perciò un canale condiviso, ed è `AppState.composeMenu`. Qui si verifica la
regola sul codice vero di `state.js`, non su una riscrittura: quel che i due chip
aggiungono è una riga a testa.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
STATE_JS = ASSETS / "shared" / "state.js"
SCOPE_JS = ASSETS / "shared" / "scope-chip.js"
COMMANDS_JS = ASSETS / "shared" / "commands-chip.js"


pytestmark = requires_node


# `state.js` legge il tema da `localStorage` al caricamento del modulo, e in node
# non esiste: lo si finge vuoto, che è anche il caso di un'installazione nuova.
_LOCAL_STORAGE_STUB = "const localStorage = { getItem: () => null };\n"


def _state_source() -> str:
    """`state.js` senza gli `export`, per poterlo eseguire come script."""
    src = re.sub(r"^export ", "", STATE_JS.read_text(encoding="utf-8"), flags=re.M)
    return _LOCAL_STORAGE_STUB + src


def _run_js(script: str) -> None:
    source = _state_source() + "\n" + script
    run_js(source)


_TWO_MENUS = """
/* Due tendine finte: di vero c'è solo il flag `open` e la loro iscrizione, che
   è la stessa riga che i due chip scrivono. */
function menu(id) {
  const m = { id, open: false };
  onOtherComposeMenu(id, () => { m.open = false; });
  m.show = () => { claimComposeMenu(id); m.open = true; };
  return m;
}
const scope = menu('scope');
const commands = menu('commands');
"""


def test_opening_one_closes_the_other() -> None:
    _run_js(_TWO_MENUS + """
      import assert from 'node:assert/strict';
      scope.show();
      assert.equal(scope.open, true);
      commands.show();
      assert.equal(commands.open, true);
      assert.equal(scope.open, false, 'le due tendine sono rimaste aperte insieme');
    """)


def test_it_works_in_the_other_direction_too() -> None:
    """Il difetto era simmetrico, e la correzione dev'esserlo."""
    _run_js(_TWO_MENUS + """
      import assert from 'node:assert/strict';
      commands.show();
      scope.show();
      assert.equal(scope.open, true);
      assert.equal(commands.open, false);
    """)


def test_reopening_the_same_one_does_not_close_it() -> None:
    """Il gancio scatta su *un'altra*, non su una qualsiasi."""
    _run_js(_TWO_MENUS + """
      import assert from 'node:assert/strict';
      scope.show();
      scope.show();
      assert.equal(scope.open, true);
    """)


def test_closing_does_not_publish() -> None:
    """Se anche la chiusura scrivesse il campo, le due si richiamerebbero.

    Il `close()` provocato dal gancio ne scriverebbe un altro, e chi arriva dopo
    chiuderebbe chi è appena stato aperto. Qui si controlla che una chiusura non
    tocchi lo stato: dopo, il campo nomina ancora chi è davvero aperto.
    """
    _run_js(_TWO_MENUS + """
      import assert from 'node:assert/strict';
      scope.show();
      commands.show();
      assert.equal(AppState.composeMenu, 'commands',
                   'la chiusura di `scope` ha sovrascritto chi è aperto');
    """)


# ── I due chip usano davvero questo canale ──────────────────────────────────


def test_both_chips_claim_and_listen() -> None:
    """Grep, non comportamento: la riga per chip c'è, e con l'id giusto.

    Il comportamento vero sta nei test sopra; qui si tiene fermo che i due
    consumatori esistano, perché una tendina che non si iscrive non fallisce da
    nessuna parte — resta semplicemente aperta insieme all'altra.
    """
    for path, ident in ((SCOPE_JS, "scope"), (COMMANDS_JS, "commands")):
        src = path.read_text(encoding="utf-8")
        assert f"claimComposeMenu('{ident}')" in src, f"{path.name} non dichiara l'apertura"
        assert f"armComposeMenu(this, '{ident}')" in src, f"{path.name} non ascolta le altre"


# ── L'aggancio comune ────────────────────────────────────────────────────────

_ARMED = """
import assert from 'node:assert/strict';
/* Un `document` e due elementi che tengono i loro listener, e un evento che
   ricorda se qualcuno ne ha fermato la propagazione. */
function target() {
  const t = { on: {} };
  t.addEventListener = (type, fn) => { (t.on[type] ||= []).push(fn); };
  t.fire = (type, extra = {}) => {
    const e = { stopped: false, stopPropagation() { this.stopped = true; }, ...extra };
    for (const fn of t.on[type] || []) fn(e);
    return e;
  };
  return t;
}
globalThis.document = target();
function chip(id) {
  const c = { el: target(), menu: target(), toggles: 0, closes: 0 };
  c.toggle = () => { c.toggles += 1; };
  c.close = () => { c.closes += 1; };
  armComposeMenu(c, id);
  return c;
}
"""


def test_the_chip_click_toggles_and_does_not_reach_document() -> None:
    _run_js(_ARMED + """
      const c = chip('scope');
      const e = c.el.fire('click');
      assert.equal(c.toggles, 1);
      assert.equal(e.stopped, true, 'senza, il click che apre la richiuderebbe');
      assert.equal(c.menu.fire('click').stopped, true, 'un tocco dentro la tendina non la chiude');
      assert.equal(c.closes, 0);
    """)


def test_outside_tap_and_escape_close_other_keys_do_not() -> None:
    _run_js(_ARMED + """
      const c = chip('commands');
      document.fire('click');
      assert.equal(c.closes, 1);
      document.fire('keydown', { key: 'Enter' });
      assert.equal(c.closes, 1);
      document.fire('keydown', { key: 'Escape' });
      assert.equal(c.closes, 2);
    """)


def test_opening_another_menu_closes_the_armed_one() -> None:
    _run_js(_ARMED + """
      const scope = chip('scope');
      const commands = chip('commands');
      claimComposeMenu('commands');
      assert.equal(scope.closes, 1);
      assert.equal(commands.closes, 0, 'la propria apertura non la chiude');
    """)
