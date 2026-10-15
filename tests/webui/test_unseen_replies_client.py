"""Il pallino su Jenny: una risposta arrivata mentre la sua chat era chiusa.

Dal 05/10/2026 la chat sta dietro Jenny, e una risposta che arriva mentre
guardi altro non si vedrebbe. ``shared/unseen-replies.js`` decide quando
accendere il pallino; qui gira il modulo vero con i vicini finti, e i frame
sono quelli che il gateway manda davvero (``delta``, ``message``,
``turn_end``).
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.home_dom import requires_jsdom, run_home
from support.js_harness import requires_node, run_module

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_NEIGHBORS = {
    "ws-manager.js": "export const wsManager = new EventTarget();\n",
    "session-manager.js": (
        "export const sessionManager = { currentChatId: 'default' };\n"
    ),
}

_PRELUDE = """
import assert from 'node:assert/strict';
const { wsManager } = await import('./shared/ws-manager.js');
const { sessionManager } = await import('./shared/session-manager.js');
const { UnseenReplies } = await import('./shared/unseen-replies.js');
const send = (detail) => wsManager.dispatchEvent(Object.assign(new Event('chat:message'), { detail }));
let shown = new Set();
let wanted = 'default';
const painted = [];
const u = new UnseenReplies({
  onScreen: (id) => shown.has(id),
  wanted: () => wanted,
  paint: (on) => painted.push(on),
});
const turn = (chat_id, text = 'ecco') => {
  send({ event: 'goal_status', status: 'running', chat_id });
  send({ event: 'delta', text, chat_id });
  send({ event: 'turn_end', chat_id });
};
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "unseen-replies.js", root / "shared" / "unseen-replies.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
        run_module(entry)


def test_a_reply_while_the_chat_is_closed_lights_the_dot() -> None:
    _run("""
      turn('default');
      assert.deepEqual(painted, [true]);
    """)


def test_a_reply_you_are_reading_lights_nothing() -> None:
    _run("""
      shown.add('default');
      turn('default');
      assert.deepEqual(painted, []);
    """)


def test_opening_the_chat_puts_it_out() -> None:
    _run("""
      turn('default');
      shown.add('default');
      u.refresh();
      assert.deepEqual(painted, [true, false]);
      shown.delete('default');
      u.refresh();
      assert.deepEqual(painted, [true, false], 'una risposta gia\\u2019 vista si e\\u2019 riaccesa');
    """)


def test_a_silent_turn_or_a_stop_lights_nothing() -> None:
    """Solo i nomi degli strumenti, o un `/stop`: niente da leggere."""
    _run("""
      send({ event: 'message', kind: 'tool_hint', text: 'read_file', chat_id: 'default' });
      send({ event: 'turn_end', chat_id: 'default' });
      assert.deepEqual(painted, []);
    """)


def test_the_dot_is_for_the_conversation_she_would_open_from_here() -> None:
    """Una risposta nel quaderno non accende niente sopra le App, dove Jenny
    aprirebbe la personale; tornando sulla bacheca si accende."""
    _run("""
      turn('project:piante');
      assert.deepEqual(painted, []);
      wanted = 'project:piante';
      u.refresh();
      assert.deepEqual(painted, [true]);
    """)


def test_a_frame_without_a_chat_id_belongs_to_the_open_conversation() -> None:
    _run("""
      sessionManager.currentChatId = 'project:piante';
      wanted = 'project:piante';
      send({ event: 'message', text: 'promemoria' });
      send({ event: 'turn_end' });
      assert.deepEqual(painted, [true]);
    """)


@requires_jsdom
def test_a_reply_on_the_apps_page_lights_her_and_opening_the_chat_clears_it() -> None:
    """Nella casa vera: la risposta arriva mentre sei sulle App, il pallino si
    accende e lo dice anche a chi non lo vede; tocchi Jenny, la chat sale, e
    il pallino si spegne."""
    run_home("""
import assert from 'node:assert/strict';
import { boot, tick, frame } from './boot.mjs';
const app = await boot();
await tick(30);
app.homePages.goToId('app', { animated: false });
await tick(10);
frame({ event: 'delta', text: 'ecco la risposta', chat_id: 'default' });
frame({ event: 'turn_end', chat_id: 'default' });
await tick(10);
assert.ok(app.jenny.el.classList.contains('has-dot'), 'il pallino non si e\\u2019 acceso');
assert.match(app.jenny.el.getAttribute('aria-label'), / · /);
app.jenny._onTap();
await tick(30);
assert.equal(app.chatOver, true);
assert.ok(!app.jenny.el.classList.contains('has-dot'), 'aperta la chat il pallino resta');
""")


def test_a_turn_asked_from_another_device_lights_nothing() -> None:
    """Scrivi dal Mac: la risposta la leggi li', e sul telefono il pallino
    direbbe «c'e' qualcosa per te» di una cosa gia' letta."""
    _run("""
      send({ event: 'user', chat_id: 'default', text: 'dal Mac', origin: 'remote' });
      turn('default');
      assert.deepEqual(painted, []);
      // Il turno dopo, chiesto dal telefono, torna come sempre.
      turn('default');
      assert.deepEqual(painted, [true]);
    """)


def test_a_message_from_this_phone_after_one_from_the_mac_counts_again() -> None:
    _run("""
      send({ event: 'user', chat_id: 'default', text: 'dal Mac', origin: 'remote' });
      send({ event: 'user', chat_id: 'default', text: 'da Telegram', origin: 'telegram' });
      turn('default');
      assert.deepEqual(painted, [true]);
    """)
