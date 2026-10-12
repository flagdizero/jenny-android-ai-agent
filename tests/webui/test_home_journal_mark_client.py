"""Il segno dei cassetti: un'icona sotto la risposta quando Jenny ha annotato in un quaderno.

L'utente ha scelto un segno discreto e non una frase («l'ho messo in…») nella
risposta (12/10/2026). Il segno nasce dagli eventi di ``journal_append`` con un
``notebook``, riusciti: dal vivo, dai frame del turno, e dopo una ricarica,
dalle righe di traccia della storia. Toccarlo apre le righe annotate.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, threads } from './boot.mjs';
"""

_EVENT = """
const filed = (call, notebook, text, result) => ({
  version: 1, phase: 'end', call_id: call, name: 'journal_append',
  arguments: { text, notebook, attribution: 'said' }, result,
});
"""


def test_a_live_turn_that_filed_gets_the_mark_and_opens_it() -> None:
    run_home(_HEAD + _EVENT + """
const app = await boot();
await tick(20);
frame({ event: 'message', chat_id: 'default', turn_id: 't1', kind: 'progress', text: '',
        tool_events: [filed('c1', 'camper', 'Giovedi mattina: batteria dei servizi',
                            'Appended to journal/20261012.md: - 09:00 — [said] ...')] });
frame({ event: 'stream_end', chat_id: 'default', turn_id: 't1', text: 'Va bene, giovedi.' });
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1', latency_ms: 1200 });
await tick(20);
const thread = document.getElementById('home-thread');
const mark = thread.querySelector('.home-journal-mark');
assert.ok(mark, thread.innerHTML);
const detail = thread.querySelector('.home-journal-detail');
assert.equal(detail.hidden, true);
assert.match(detail.textContent, /camper/);
assert.match(detail.textContent, /batteria dei servizi/);
mark.click();
assert.equal(detail.hidden, false);
assert.equal(mark.getAttribute('aria-expanded'), 'true');
""")


def test_a_refused_filing_leaves_no_mark() -> None:
    run_home(_HEAD + _EVENT + """
const app = await boot();
await tick(20);
frame({ event: 'message', chat_id: 'default', turn_id: 't1', kind: 'progress', text: '',
        tool_events: [filed('c1', 'cane', 'Prende un cane',
                            "No notebook named 'cane' (existing: camper). Nothing was written.")] });
frame({ event: 'stream_end', chat_id: 'default', turn_id: 't1', text: 'Ok.' });
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(20);
assert.equal(document.querySelector('#home-thread .home-journal-mark'), null);
""")


def test_the_mark_comes_back_after_a_reload() -> None:
    run_home(_HEAD + _EVENT + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'il meccanico viene giovedi', turnId: 't1' },
  { role: 'tool', kind: 'trace', content: 'journal_append', turnId: 't1',
    toolEvents: [filed('c1', 'camper', 'Giovedi: batteria', 'Appended to journal/x.md: ...')] },
  { role: 'assistant', text: 'Va bene.', turnId: 't1' },
] };
const app = await boot();
await tick(40);
const thread = document.getElementById('home-thread');
assert.ok(thread.querySelector('.home-journal-mark'), thread.innerHTML);
assert.match(thread.querySelector('.home-journal-detail').textContent, /camper/);
""")


def test_a_move_offer_opens_the_notebook_chat() -> None:
    """«Ci spostiamo?»: il tasto apre la chat del quaderno; decide l'utente."""
    run_home(_HEAD + """
const app = await boot();
await tick(20);
const opened = [];
app.switchConversation = async (key) => { opened.push(key); };
frame({ event: 'message', chat_id: 'default', turn_id: 't1', kind: 'progress', text: '',
        tool_events: [{ version: 1, phase: 'end', call_id: 'c9', name: 'suggest_notebook',
                        arguments: { notebook: 'camper', context: 'manutenzione' },
                        result: "The user now sees a button under your reply that opens the notebook 'camper'" }] });
frame({ event: 'stream_end', chat_id: 'default', turn_id: 't1', text: 'Vuoi che ci spostiamo nel camper?' });
frame({ event: 'turn_end', chat_id: 'default', turn_id: 't1' });
await tick(20);
const offer = document.querySelector('#home-thread .home-move-offer');
assert.ok(offer, document.getElementById('home-thread').innerHTML);
assert.match(offer.textContent, /camper/);
offer.click();
await tick(5);
assert.deepEqual(opened, ['project:camper']);
""")
