"""La mascotte non resta a pensare per sempre.

Misurato il 28/09/2026 sull'emulatore, con un client WebSocket che manda come la
WebUI (``webui: true``). Le sequenze di frame qui sotto sono quelle vere:

* ``/stop`` a turno in corso: ``running`` → ``turn_end`` (con l'id di ``/stop``,
  non del turno fermato) → ``idle`` → **poi** «Stopped 1 task(s).»;
* ``/status``, e ``/stop`` a riposo: un solo ``message``, e niente dopo;
* ``/help``: ``message`` → ``turn_end`` → ``idle``, che si chiudeva gia'.

Il messaggio la faceva parlare, e dopo un secondo di silenzio il parlato la
rimetteva a pensare **senza chiedersi se il turno era aperto**: nessun frame
dopo l'avrebbe chiusa. E il ``turn_end`` di ``/stop``, che porta un id diverso,
le lasciava agganciato l'id del turno fermato, cosi' da li' in poi ignorava ogni
``turn_end`` — gli avvisi proattivi, che non mandano ``idle``, compresi.

In node, col modulo vero (``shared/jenny-mascot.js``) e i vicini finti. Il
silenzio del parlato si simula chiamando ``_talkTick`` con l'ultimo testo gia'
lontano: il timer vero non serve.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_NEIGHBORS = {
    "ws-manager.js": "export const wsManager = new EventTarget();\n",
    "session-manager.js": (
        "export const sessionManager = Object.assign(new EventTarget(), "
        "{ currentChatId: 'default' });\n"
    ),
    "mascot.js": """
export function mascotVisible() { return true; }
export function applyMascotSize() {}
""",
    "mascot-drag.js": """
export function bindMascotDrag() {}
export function buildFlyLayer() {}
""",
}

_PRELUDE = """
import assert from 'node:assert/strict';
globalThis.window = { matchMedia: () => ({ matches: false }) };
globalThis.performance = globalThis.performance || { now: () => Date.now() };
const { JennyMascot } = await import('./shared/jenny-mascot.js');
const { MotionChannels } = await import('./shared/mascot-motion.js');

function classes(...initial) {
  const s = new Set(initial);
  return {
    add: (c) => s.add(c), remove: (...cs) => cs.forEach((c) => s.delete(c)),
    contains: (c) => s.has(c), toggle: (c, on) => (on ? s.add(c) : s.delete(c)),
  };
}

/* Jenny nella chat vera, fuori all'angolo: lo stato di `JennyMascot` senza il
   suo DOM. Il parlato non avvia timer: il silenzio lo chiama il banco. */
function jenny() {
  const j = Object.create(JennyMascot.prototype);
  Object.assign(j, {
    mode: 'chat', _agentState: 'idle', _turnActive: false, _pendingTurn: false,
    _streamTurnId: null, _runSeen: false, _lastClosedTurnId: null,
    _mood: null, _moodUntil: 0, _moodTimer: null,
    _talk: { timer: null, animIdx: 0, open: false, lastTextAt: 0, switchAt: 0 },
    _greetTimer: null,
    el: {
      classList: classes('out'), offsetWidth: 0, style: { setProperty() {} },
      setAttribute() {}, removeAttribute() {},
    },
  });
  j._motions = new MotionChannels(j.el);
  j._syncArt = () => {};
  j._noteTalkActivity = () => { j._talk.lastTextAt = -1e9; };
  j._stopTalk = () => {};
  return j;
}
const sent = (j) => j._handleChatSent({ chat_id: 'default' });
const frame = (j, msg) => j._handleWsMessage(msg);
/* Un secondo di silenzio nel parlato. */
const silence = (j) => { if (j._agentState === 'talking') j._talkTick(); };
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "jenny-mascot.js", root / "shared" / "jenny-mascot.js")
        shutil.copy(ASSETS / "shared" / "bot-name.js", root / "shared" / "bot-name.js")
        shutil.copy(ASSETS / "shared" / "mascot-motion.js", root / "shared" / "mascot-motion.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
        run_module(entry)


def test_stop_during_a_turn_ends_at_rest() -> None:
    """La sequenza misurata di `/stop` a turno in corso, con la risposta per ultima."""
    _run(
        """
        const j = jenny();
        sent(j);
        frame(j, { event: 'goal_status', status: 'running' });
        frame(j, { event: 'delta', text: 'Questa e', turn_id: 'a1' });
        assert.equal(j._agentState, 'talking');
        sent(j);  // /stop
        frame(j, { event: 'goal_status', status: 'running' });
        frame(j, { event: 'turn_end', turn_id: 'b1' });
        frame(j, { event: 'goal_status', status: 'idle' });
        frame(j, { event: 'message', text: 'Stopped 1 task(s).', turn_id: 'b1' });
        assert.equal(j._agentState, 'talking');
        silence(j);
        assert.equal(j._agentState, 'idle', 'dopo la risposta di /stop torna a riposo');
        assert.equal(j._turnActive, false);
        assert.equal(j._streamTurnId, null, 'l\\u2019id del turno fermato non resta appeso');
        """
    )


def test_a_proactive_notice_after_a_stop_still_closes() -> None:
    """La conseguenza dell'id appeso: dopo un `/stop`, un avviso proattivo
    (``message`` + ``turn_end`` col suo id, niente ``idle``) la lasciava a pensare."""
    _run(
        """
        const j = jenny();
        sent(j);
        frame(j, { event: 'goal_status', status: 'running' });
        frame(j, { event: 'delta', text: 'Sto', turn_id: 'a1' });
        sent(j);
        frame(j, { event: 'turn_end', turn_id: 'b1' });
        frame(j, { event: 'goal_status', status: 'idle' });
        frame(j, { event: 'message', text: 'Stopped 1 task(s).', turn_id: 'b1' });
        silence(j);
        frame(j, { event: 'message', text: 'Promemoria: annaffia', turn_id: 'proactive:1' });
        silence(j);
        frame(j, { event: 'turn_end', turn_id: 'proactive:1' });
        assert.equal(j._agentState, 'idle');
        assert.equal(j._streamTurnId, null);
        """
    )


def test_a_command_at_rest_answers_and_rests() -> None:
    """`/status` e `/stop` a riposo: un solo ``message``, e niente dopo."""
    _run(
        """
        for (const reply of ['jenny v0.11.0', 'No active task to stop.']) {
          const j = jenny();
          sent(j);
          assert.equal(j._agentState, 'thinking');
          frame(j, { event: 'message', text: reply, turn_id: 's1' });
          silence(j);
          assert.equal(j._agentState, 'idle', reply + ': resta a pensare');
          assert.equal(j._turnActive, false);
          assert.equal(j._streamTurnId, null, 'la risposta di un comando non lascia un id');
        }
        """
    )


def test_a_command_the_gateway_now_closes_rests_too() -> None:
    """Dal 28/09/2026 il gateway chiude anche questi: risposta, ``turn_end``, ``idle``."""
    _run(
        """
        const j = jenny();
        sent(j);
        frame(j, { event: 'message', text: 'jenny v0.11.0', turn_id: 's1' });
        frame(j, { event: 'turn_end', turn_id: 's1' });
        frame(j, { event: 'goal_status', status: 'idle' });
        silence(j);
        assert.equal(j._agentState, 'idle');
        """
    )


def test_a_real_turn_still_thinks_between_its_words() -> None:
    """Il rovescio: dentro un turno vero il silenzio e' ancora il pensa."""
    _run(
        """
        const j = jenny();
        sent(j);
        frame(j, { event: 'goal_status', status: 'running' });
        frame(j, { event: 'delta', text: 'Ora controllo.', turn_id: 't1' });
        silence(j);
        assert.equal(j._agentState, 'thinking', 'fra due segmenti pensa');
        frame(j, { event: 'stream_end', turn_id: 't1' });
        assert.equal(j._agentState, 'thinking');
        frame(j, { event: 'message', text: 'Fatto.', turn_id: 't1' });
        assert.equal(j._turnActive, true, 'col running visto, un messaggio non chiude il turno');
        silence(j);
        assert.equal(j._agentState, 'thinking');
        frame(j, { event: 'turn_end', turn_id: 't1' });
        assert.equal(j._agentState, 'idle');
        """
    )


def test_a_message_sent_into_a_running_turn_keeps_waiting() -> None:
    """Un invio a turno aperto entra in quel turno: niente di nuovo da aspettare."""
    _run(
        """
        const j = jenny();
        sent(j);
        frame(j, { event: 'goal_status', status: 'running' });
        frame(j, { event: 'delta', text: 'Sto', turn_id: 't1' });
        sent(j);
        assert.equal(j._streamTurnId, 't1', 'il turno seguito resta quello');
        frame(j, { event: 'message', text: 'E poi...', turn_id: 't1' });
        assert.equal(j._turnActive, true);
        """
    )


def test_a_dropped_wire_puts_her_at_rest() -> None:
    _run(
        """
        const j = jenny();
        sent(j);
        frame(j, { event: 'goal_status', status: 'running' });
        assert.equal(j._agentState, 'thinking');
        j._onWireClose();
        assert.equal(j._agentState, 'idle');
        assert.equal(j._turnActive, false);
        frame(j, { event: 'goal_status', status: 'running' });
        assert.equal(j._agentState, 'thinking', 'un turno ancora vivo la rimette a pensare');
        """
    )


def test_late_work_frames_do_not_wake_the_thinking() -> None:
    """Ragionamento, file e suggerimenti arrivati dopo la chiusura."""
    _run(
        """
        const j = jenny();
        sent(j);
        frame(j, { event: 'goal_status', status: 'running' });
        frame(j, { event: 'turn_end', turn_id: 't1' });
        for (const late of [
          { event: 'reasoning_delta', text: 'uhm' },
          { event: 'file_edit' },
          { event: 'message', kind: 'tool_hint', text: 'leggo' },
          { event: 'message' },
        ]) {
          frame(j, late);
          assert.equal(j._agentState, 'idle', late.event + ' la rimette a pensare');
        }
        """
    )


def test_a_send_at_rest_forgets_a_stale_tracked_turn() -> None:
    _run(
        """
        const j = jenny();
        j._streamTurnId = 'vecchio';
        sent(j);
        assert.equal(j._streamTurnId, null);
        frame(j, { event: 'goal_status', status: 'running' });
        frame(j, { event: 'delta', text: 'Ciao', turn_id: 'nuovo' });
        frame(j, { event: 'turn_end', turn_id: 'nuovo' });
        assert.equal(j._agentState, 'idle');
        """
    )
