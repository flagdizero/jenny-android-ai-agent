"""Dopo una riconnessione la casa lascia il turno e rilegge il filo.

Al ``chat:open`` la casa faceva solo ``_setWire(true)``. L'officina invece
rilegge il thread (``_resyncThreadAfterReconnect``), e il gateway, all'``attach``,
rimanda ``goal_status: running`` solo se un turno e' vivo — mai ``idle``. Quindi
con il gateway ripartito a meta' turno la casa restava col bottone Ferma acceso,
la riga di lavoro e il fiore che giravano e Jenny a pensare, per sempre; e una
caduta breve lasciava a schermo una risposta tronca.

I metodi si ritagliano da ``home-app.js`` e girano in node su un guscio finto.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "home-app.js"

pytestmark = requires_node

_HARNESS = """
import assert from 'node:assert/strict';

class App {
  constructor() {
    this.log = [];
    this._running = true;
    this._threadFailed = false;
    this.activity = { stop: () => this.log.push('activity.stop') };
    this.jenny = { _releaseTrackedTurn: () => this.log.push('jenny.release') };
    this.gate = null;
    this.chat = {
      following: true,
      reload: async () => {
        this.log.push('reload');
        if (this.gate) await this.gate;
        if (this.failReload) throw new Error('giu');
      },
    };
  }
  _setWire(on) { this.log.push('wire:' + on); }
  _setRunning(running) { this._running = running; this.log.push('running:' + running); }
  _applyTranslations() { this.log.push('translations'); }
  __METHODS__
}
const tick = () => new Promise((r) => setTimeout(r, 0));
"""


def _run(script: str) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    methods = "\n  ".join(
        member(src, name) for name in ("_onWireOpen", "_resyncAfterReconnect", "_releaseTurn")
    )
    run_js(_HARNESS.replace("__METHODS__", methods) + "\n" + script)


def test_the_first_open_is_the_boot_and_touches_nothing() -> None:
    _run("""
      const app = new App();
      app._onWireOpen();
      await tick();
      assert.deepEqual(app.log, ['wire:true']);
      assert.equal(app._running, true);
    """)


def test_a_reconnect_releases_the_turn_and_reloads_the_thread() -> None:
    """Il caso segnalato: gateway ripartito a meta' turno."""
    _run("""
      const app = new App();
      app._onWireOpen();
      app.log.length = 0;
      app._onWireOpen();
      await tick();
      assert.equal(app._running, false, 'Ferma resta acceso su un turno morto');
      assert.ok(app.log.includes('activity.stop'), 'la riga di lavoro e il fiore girano ancora');
      assert.ok(app.log.includes('jenny.release'), 'Jenny resta a pensare');
      assert.equal(app.log.filter((x) => x === 'reload').length, 1, app.log.join(','));
      assert.ok(app.log.indexOf('activity.stop') < app.log.indexOf('reload'));
    """)


def test_someone_reading_further_up_keeps_the_page() -> None:
    """Come in officina: il filo si rilegge solo se si stava seguendo il fondo;
    il turno si lascia comunque."""
    _run("""
      const app = new App();
      app._onWireOpen();
      app.chat.following = false;
      app.log.length = 0;
      app._onWireOpen();
      await tick();
      assert.ok(!app.log.includes('reload'));
      assert.equal(app._running, false);
    """)


def test_a_thread_that_never_arrived_is_retried_even_on_the_first_open() -> None:
    _run("""
      const app = new App();
      app._threadFailed = true;
      app.chat.following = false;
      app._onWireOpen();
      await tick();
      assert.ok(app.log.includes('reload'));
      assert.equal(app._threadFailed, false);
      assert.ok(app.log.includes('translations'), "la riga d'errore resta scritta");
    """)


def test_a_failed_resync_keeps_the_error_and_does_not_throw() -> None:
    _run("""
      console.warn = () => {};
      const app = new App();
      app._threadFailed = true;
      app.failReload = true;
      app._onWireOpen();
      await tick();
      assert.equal(app._threadFailed, true);
      app.failReload = false;
      app._onWireOpen();
      await tick();
      assert.equal(app._threadFailed, false, 'la riconnessione dopo non riprova');
    """)


def test_two_reconnects_in_a_row_read_the_thread_once() -> None:
    _run("""
      const app = new App();
      app._onWireOpen();
      let open;
      app.gate = new Promise((r) => { open = r; });
      app._onWireOpen();
      app._onWireOpen();
      open();
      await tick(); await tick();
      assert.equal(app.log.filter((x) => x === 'reload').length, 1, app.log.join(','));
      app.gate = null;
      app._onWireOpen();
      await tick();
      assert.equal(app.log.filter((x) => x === 'reload').length, 2, 'finita la prima, la dopo riparte');
    """)
