"""La minichat dell'officina legge i frame del turno come la mascotte madre.

``JennyCompanion._handleFrame`` (``mobile-jenny.js``) aveva una sua copia
dell'intera macchina a stati di ``JennyMascot._handleChatStream``
(``shared/jenny-mascot.js``), con in più il fumetto. Qui si fissa cosa fa la
minichat, così che la copia possa diventare un gancio senza cambiare niente:
cosa mostra il fumetto, in che stato resta Jenny, quando si invalida lo storico
della chat e quali frame si scartano.

In node, coi moduli veri (``mobile-jenny.js`` e ``shared/jenny-mascot.js``) e i
vicini finti. L'istanza nasce da ``Object.create`` senza costruttore: il
costruttore disegna il DOM, e qui interessa la lettura dei frame. Lo stato di
Jenny e l'umore si registrano invece di disegnarli.
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
    "state.js": "export const AppState = { currentMode: 'home', on() {} };\n",
    "ws-manager.js": "export const wsManager = new EventTarget();\n",
    "session-manager.js": "export const sessionManager = { currentKey: 'websocket:default' };\n",
    "i18n.js": "export const i18n = { t: (k) => k, locale: 'it', load: async () => {} };\n",
    "mascot.js": """
export const OUT_SHIFT_RATIO = 0.5;
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
const { JennyCompanion } = await import('./mobile-jenny.js');

function classes(...initial) {
  const s = new Set(initial);
  return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c) };
}

/* Una minichat aperta, con la domanda in volo: `awaiting` e il flag del turno
   alzati come li alza `_send`. */
function minichat({ open = true, expected = true, inTurn = true } = {}) {
  const j = Object.create(JennyCompanion.prototype);
  Object.assign(j, {
    mode: 'home', awaiting: expected, _replyShown: false, _replyTimer: null,
    _deltaBuffer: '', _turnActive: false, _pendingTurn: inTurn,
    _streamTurnId: null, _lastClosedTurnId: null,
    el: { classList: classes() },
    mc: { classList: open ? classes('open') : classes(), dataset: {} },
    bubble: { textContent: '' },
  });
  j.states = [];
  j.moods = [];
  j._setAgentState = (s) => { j.states.push(s); j._agentState = s; };
  j._applyMood = (m) => j.moods.push(m);
  j.invalidations = 0;
  window.mobileApp = { controllers: { chat: { invalidateHistory: () => { j.invalidations += 1; } } } };
  return j;
}
const last = (j) => j.states[j.states.length - 1];
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "mobile-jenny.js", root / "mobile-jenny.js")
        shutil.copy(ASSETS / "shared" / "jenny-mascot.js", root / "shared" / "jenny-mascot.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
        run_module(entry)


def test_deltas_accumulate_into_the_bubble() -> None:
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'delta', text: 'Ciao ', turn_id: 't1' });
        j._handleFrame({ event: 'delta', text: '**Luca**', turn_id: 't1' });
        assert.equal(j.bubble.textContent, 'Ciao Luca', 'testo piano, accumulato');
        assert.equal(j.mc.dataset.state, 'reply');
        assert.equal(last(j), 'talking');
        assert.equal(j._streamTurnId, 't1', 'il primo frame adotta il turno');
        """
    )


def test_stream_end_shows_its_text_and_then_settles() -> None:
    """Il fumetto prima, lo stato dopo: `_showReply` mette `talking`, e se
    venisse per ultimo Jenny resterebbe a parlare a turno fermo."""
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'stream_end', text: 'Fatto.' });
        assert.equal(j.bubble.textContent, 'Fatto.');
        assert.equal(last(j), 'idle');
        j._turnActive = true;
        j._handleFrame({ event: 'stream_end' });
        assert.equal(j.bubble.textContent, 'Fatto.', 'senza testo il fumetto resta');
        assert.equal(last(j), 'thinking', 'col goal in corso si torna a pensare');
        """
    )


def test_a_message_with_text_is_shown_and_a_hint_is_not() -> None:
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'message', kind: 'tool_hint', text: 'leggo il file' });
        assert.equal(j.bubble.textContent, '', 'un suggerimento non va nel fumetto');
        assert.equal(last(j), 'thinking');
        j._handleFrame({ event: 'message', kind: 'progress', text: 'ancora un attimo' });
        assert.equal(j.bubble.textContent, '');
        assert.equal(last(j), 'thinking');
        j._handleFrame({ event: 'message', tool_events: [{}] });
        assert.equal(last(j), 'thinking');
        j._handleFrame({ event: 'message', text: 'Eccola' });
        assert.equal(j.bubble.textContent, 'Eccola');
        assert.equal(last(j), 'talking');
        """
    )


def test_turn_end_without_a_reply_shows_the_flower_and_invalidates_history() -> None:
    _run(
        """
        const j = minichat();
        let fired = false;
        j._replyTimer = setTimeout(() => { fired = true; }, 5);
        j._handleFrame({ event: 'turn_end', turn_id: 't1' });
        assert.equal(j.bubble.textContent, '✿');
        assert.equal(last(j), 'idle', 'il fiore non lascia Jenny a parlare');
        assert.equal(j.awaiting, false);
        assert.equal(j._pendingTurn, false);
        assert.equal(j._replyTimer, null);
        assert.equal(j._lastClosedTurnId, 't1');
        assert.equal(j.invalidations, 1);
        await new Promise((r) => setTimeout(r, 20));
        assert.equal(fired, false, 'il timer della risposta lenta va spento');
        """
    )


def test_turn_end_after_a_reply_keeps_the_reply() -> None:
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'delta', text: 'Risposta', turn_id: 't1' });
        j._handleFrame({ event: 'turn_end', turn_id: 't1' });
        assert.equal(j.bubble.textContent, 'Risposta');
        assert.equal(last(j), 'idle');
        assert.equal(j._streamTurnId, null);
        """
    )


def test_error_shows_its_text_and_the_sad_face() -> None:
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'error', detail: 'Il provider **non** risponde' });
        assert.equal(j.bubble.textContent, 'Il provider non risponde');
        assert.equal(last(j), 'idle');
        assert.deepEqual(j.moods, ['sad']);
        assert.equal(j.awaiting, false);
        assert.equal(j._pendingTurn, false);

        const k = minichat();
        k._handleFrame({ event: 'error' });
        assert.equal(k.bubble.textContent, 'jenny.genericError');
        """
    )


def test_the_closing_frame_of_a_foreign_turn_is_ignored() -> None:
    """L'avviso proattivo atterrato durante l'attesa non chiude la domanda."""
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'delta', text: 'sto', turn_id: 'mio' });
        j._handleFrame({ event: 'turn_end', turn_id: 'avviso' });
        j._handleFrame({ event: 'error', turn_id: 'avviso', detail: 'no' });
        assert.equal(j.awaiting, true);
        assert.equal(j._pendingTurn, true);
        assert.equal(j.bubble.textContent, 'sto');
        assert.equal(j.invalidations, 0);
        assert.deepEqual(j.moods, []);
        """
    )


def test_with_nothing_on_screen_only_the_pending_closing_passes() -> None:
    """Minichat chiusa a metà turno: i delta si scartano, la chiusura no —
    è l'unico punto che invalida lo storico della chat."""
    _run(
        """
        const j = minichat({ open: false, expected: false });
        j._handleFrame({ event: 'delta', text: 'invisibile', turn_id: 't1' });
        assert.deepEqual(j.states, []);
        assert.equal(j._deltaBuffer, '');
        assert.equal(j._streamTurnId, null, 'un frame scartato non apre il tracciamento');
        j._handleFrame({ event: 'turn_end', turn_id: 't1' });
        assert.equal(j.invalidations, 1);
        assert.equal(j._pendingTurn, false);
        assert.equal(j.bubble.textContent, '', 'a minichat chiusa il fumetto non si scrive');

        const k = minichat({ open: false, expected: false, inTurn: false });
        k._handleFrame({ event: 'turn_end', turn_id: 't1' });
        assert.equal(k.invalidations, 0, 'senza turno in volo non è roba nostra');
        """
    )


def test_goal_status_and_reasoning_move_the_state() -> None:
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'goal_status', status: 'running' });
        assert.equal(j._turnActive, true);
        assert.equal(last(j), 'thinking');
        j._handleFrame({ event: 'reasoning_delta', text: 'uhm' });
        j._handleFrame({ event: 'file_edit' });
        assert.equal(last(j), 'thinking');
        j._handleFrame({ event: 'goal_status', status: 'idle' });
        assert.equal(j._turnActive, false);
        assert.equal(last(j), 'idle');
        """
    )


def test_in_the_chat_view_the_bubble_is_not_touched() -> None:
    """In chat la minichat non c'è: il frame va alla macchina della madre."""
    _run(
        """
        const j = minichat({ expected: false, inTurn: false });
        j.mode = 'chat';
        j._handleFrame({ event: 'delta', text: 'in chat', turn_id: 'c1' });
        assert.equal(j.bubble.textContent, '');
        assert.equal(j._deltaBuffer, '');
        assert.equal(last(j), 'talking');
        j._handleFrame({ event: 'turn_end', turn_id: 'c1' });
        assert.equal(j.bubble.textContent, '');
        assert.equal(j.invalidations, 0);
        assert.equal(last(j), 'idle');
        """
    )


def test_an_empty_message_means_thinking_as_in_the_mother() -> None:
    """Cambio voluto (3.5 della pulizia): un ``message`` senza testo né
    ``tool_events`` nella minichat non faceva niente, nella madre porta a
    ``thinking``. Con una macchina sola vale la regola della madre."""
    _run(
        """
        const j = minichat();
        j._handleFrame({ event: 'message' });
        assert.deepEqual(j.states, ['thinking']);
        assert.equal(j.bubble.textContent, '');
        """
    )
