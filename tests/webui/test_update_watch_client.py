"""Dopo un aggiornamento del gateway la pagina si ricarica da sola (``shared/update-watch.js``).

Il 21/10/2026 il browser del Mac, collegato da remoto al Titan, è rimasto col JS
di prima dell'aggiornamento: il socket si era ricollegato da solo, la pagina no,
e la bacheca non disegnava le etichette dei cron che il telefono già mostrava.

Si guida la casa vera (``support.home_dom``): il legame fra il socket, il
bootstrap e la ricarica è proprio quel che una classe finta riscriverebbe.
"""

from __future__ import annotations

from support.home_dom import UI, requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, routes, calls, FakeWS, $ } from './boot.mjs';
routes['/webui/bootstrap'] = { ok: true, ui_version: __LOADED__, boot: 'b1' };
const app = await boot();
let reloads = 0;
app.updateWatch._reload = () => { reloads += 1; };
const reconnect = async (served) => {
  routes['/webui/bootstrap'] = served;
  FakeWS.last.onclose();
  FakeWS.last.readyState = 3;
  const { wsManager } = await import(__WS__);
  wsManager.connectChat();
  await tick(60);
};
/* Quel che decide si decide dopo l'assestamento della riapertura. */
const settle = () => tick(1100);
"""


def _run(body: str, *, loaded: str = "'v1'") -> str:
    ws = (UI / "assets" / "shared" / "ws-manager.js").as_posix()
    head = _HEAD.replace("__WS__", repr(ws)).replace("__LOADED__", loaded)
    return run_home(head + body)


def test_the_same_files_after_a_restart_do_not_reload() -> None:
    _run("""
await reconnect({ ok: true, ui_version: 'v1', boot: 'b2' });
await settle();
assert.equal(reloads, 0);
// Rilegge il bootstrap a ogni riconnessione, e solo allora.
assert.ok(calls.filter((c) => c === '/webui/bootstrap').length >= 2);
""")


def test_new_files_reload_once() -> None:
    _run("""
await reconnect({ ok: true, ui_version: 'v2', boot: 'b2' });
assert.equal(reloads, 0, 'not before the reopening settles');
await settle();
assert.equal(reloads, 1);
await reconnect({ ok: true, ui_version: 'v2', boot: 'b2' });
await settle();
assert.equal(reloads, 1, 'a reload already on its way is not asked twice');
""")


def test_a_typed_draft_or_a_running_turn_makes_it_wait() -> None:
    _run("""
$('home-input').value = 'half a sentence';
await reconnect({ ok: true, ui_version: 'v2', boot: 'b2' });
// Il gateway ridice il turno ancora vivo appena la pagina si riaggancia.
frame({ event: 'goal_status', chat_id: 'websocket:default', status: 'running' });
await settle();
assert.equal(reloads, 0, 'never under your hands');
$('home-input').value = '';
await tick(2200);
assert.equal(reloads, 0, 'the turn is still running');
frame({ event: 'goal_status', chat_id: 'websocket:default', status: 'idle' });
await tick(2200);
assert.equal(reloads, 1);
""")


def test_a_page_born_before_the_fingerprint_learns_it_from_the_same_process() -> None:
    _run("""
// Lo stesso processo: l'impronta di adesso è quella dei suoi file.
await reconnect({ ok: true, ui_version: 'v1', boot: 'b1' });
await settle();
assert.equal(reloads, 0);
// Poi un aggiornamento vero.
await reconnect({ ok: true, ui_version: 'v2', boot: 'b2' });
await settle();
assert.equal(reloads, 1);
""", loaded="''")


def test_a_page_born_before_the_fingerprint_reloads_on_another_process() -> None:
    _run("""
await reconnect({ ok: true, ui_version: 'v9', boot: 'b2' });
await settle();
assert.equal(reloads, 1, 'which files it started with, nobody can tell');
""", loaded="''")


def test_a_gateway_from_before_says_nothing_and_nothing_happens() -> None:
    _run("""
await reconnect({ ok: true });
await settle();
assert.equal(reloads, 0);
""")


def test_the_workshop_watches_too() -> None:
    src = (UI / "assets" / "mobile-app.js").read_text(encoding="utf-8")
    assert "import { UpdateWatch } from './shared/update-watch.js';" in src
    assert "new UpdateWatch().start()" in src


def test_a_new_process_still_computing_is_asked_again() -> None:
    _run("""
await reconnect({ ok: true, ui_version: '', boot: 'b2' });
await settle();
assert.equal(reloads, 0, 'it asks again instead of guessing');
// Due secondi dopo l'impronta e' pronta: gli stessi file, nessuna ricarica.
routes['/webui/bootstrap'] = { ok: true, ui_version: 'v1', boot: 'b2' };
await tick(2200);
assert.equal(reloads, 0);
""")
