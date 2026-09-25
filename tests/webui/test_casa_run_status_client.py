"""Il bottone Ferma e la riga di lavoro ascoltano solo la conversazione a schermo.

Il canale websocket manda alla casa i frame di **tutte** le conversazioni: un
turno che gira in un quaderno arriva anche mentre guardi la chat personale. La
chat li scarta da sempre (`HomeChat._belongsHere`); il guscio no, e un
`goal_status` di un altro quaderno accendeva Ferma su una chat ferma — Ferma
avrebbe mandato `/stop` alla conversazione sbagliata — o lo spegneva sul turno
vivo di questa.

I metodi si ritagliano da `home-app.js` e girano in node su un guscio finto.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "home-app.js"

pytestmark = requires_node

_HARNESS = """
import assert from 'node:assert/strict';

const sessionManager = { currentChatId: 'default' };
const i18n = { t: (k) => k };

class App {
  constructor() {
    this._running = false;
    this.send = {
      classList: { toggle() {} },
      innerHTML: '',
      setAttribute() {},
    };
    this.rows = [];
    this.activity = {
      turnId: null,
      start: (id) => this.rows.push('start:' + id),
      stop: () => this.rows.push('stop'),
      reasoning: () => this.rows.push('reasoning'),
      tools: () => this.rows.push('tools'),
      answering: () => this.rows.push('answering'),
    };
  }
  __READ_RUN__
  __READ_ACTIVITY__
  __FRAME_IS_HERE__
  __SET_RUNNING__
}
"""


def _run(script: str) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    harness = (
        _HARNESS.replace("__READ_RUN__", member(src, "_readRunStatus"))
        .replace("__READ_ACTIVITY__", member(src, "_readActivity"))
        .replace("__FRAME_IS_HERE__", member(src, "_frameIsHere"))
        .replace("__SET_RUNNING__", member(src, "_setRunning"))
    )
    run_js(harness + "\n" + script)


def test_a_turn_in_another_conversation_does_not_light_the_stop_button() -> None:
    _run("""
      const app = new App();
      app._readRunStatus({ event: 'goal_status', status: 'running', chat_id: 'project:orto' });
      assert.equal(app._running, false, 'Ferma acceso da un turno di un altro quaderno');
      app._readActivity({ event: 'goal_status', status: 'running', chat_id: 'project:orto', turn_id: 't9' });
      assert.deepEqual(app.rows, [], 'la riga di lavoro racconta un altro quaderno');
      assert.equal(app.activity.turnId, null, 'il turno di un altro quaderno e\\u2019 diventato il nostro');
    """)


def test_a_turn_ending_elsewhere_does_not_turn_off_ours() -> None:
    _run("""
      const app = new App();
      app._readRunStatus({ event: 'goal_status', status: 'running', chat_id: 'default' });
      assert.equal(app._running, true);
      app._readRunStatus({ event: 'goal_status', status: 'idle', chat_id: 'project:orto' });
      assert.equal(app._running, true, 'la fine di un altro turno ha spento Ferma sul nostro');
      app.rows.length = 0;
      app._readActivity({ event: 'turn_end', chat_id: 'project:orto' });
      assert.deepEqual(app.rows, [], 'la riga di lavoro si e\\u2019 fermata per un altro turno');
    """)


def test_frames_of_this_conversation_and_frames_without_a_chat_still_count() -> None:
    """Un frame senza `chat_id` e' di tutti, com'e' per la chat."""
    _run("""
      const app = new App();
      app._readRunStatus({ event: 'goal_status', status: 'running' });
      assert.equal(app._running, true);
      app._readRunStatus({ event: 'goal_status', status: 'idle', chat_id: 'default' });
      assert.equal(app._running, false);
      app._readActivity({ event: 'goal_status', status: 'running', chat_id: 'default', turn_id: 't1' });
      assert.deepEqual(app.rows, ['start:t1']);
    """)

