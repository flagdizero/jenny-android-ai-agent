"""«Ho fatto» sotto una risposta, e «Torna a prima di questo turno».

Si guida la casa intera (``support.home_dom``) con un finto gateway che
risponde ai comandi ``turn.*``: conta che la riga compaia solo sotto le
risposte dei turni che hanno toccato qualcosa, dal vivo e dalla storia, e che
il ritorno passi l'anteprima e offra «Annulla».
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, frame, threads, rpcAnswers, rpcFailed, $ } from './boot.mjs';
const touched = { 'turn-a': { files: 1, jobs: 1, others: 0, working: false, state: 'open' } };
const asked = [];
const calls = [];
rpcAnswers['turn.summaries'] = (p) => {
  asked.push(...p.turns);
  return { ok: true, turns: Object.fromEntries(p.turns.filter((t) => touched[t]).map((t) => [t, touched[t]])) };
};
rpcAnswers['turn.work'] = (p) => ({ ok: true, turn: p.turn, working: false,
  state: touched[p.turn].state, expect_end: 'tok-1',
  items: [{ kind: 'file', label: 'notes/a.md', undoable: true },
          { kind: 'job', label: 'acqua', undoable: true },
          { kind: 'egress', what: 'web_search', label: 'meteo', undoable: false }] });
rpcAnswers['turn.undo'] = (p) => {
  calls.push(['undo', p]);
  if (p.expect_end !== 'tok-1') return rpcFailed('conflict', 'moved');
  touched[p.turn].state = 'undone';
  return { ok: true, restored: 1, removed: 0, left: 0, jobs: 1 };
};
rpcAnswers['turn.redo'] = (p) => { calls.push(['redo', p]); touched[p.turn].state = 'open'; return { ok: true }; };
const did = () => [...$('home-thread').querySelectorAll('.home-did')];
"""


def test_the_row_shows_only_under_a_turn_that_touched_things() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'scrivi la lista', turnId: 'turn-a' },
  { role: 'assistant', text: 'fatto', turnId: 'turn-a' },
  { role: 'user', text: 'ciao', turnId: 'turn-b' },
  { role: 'assistant', text: 'ciao a te', turnId: 'turn-b' },
] };
await boot();
await until(() => did().length, { msg: 'nessuna riga sotto il turno che ha scritto' });
assert.equal(did().length, 1);
assert.equal(did()[0].closest('.home-msg').dataset.turn, 'turn-a');
assert.match(did()[0].textContent, /1/);
assert.ok(asked.includes('turn-b'), 'il turno senza lavoro non e\\u2019 stato chiesto');
""")


def test_a_live_turn_asks_at_its_end() -> None:
    run_home(_HEAD + """
await boot();
frame({ event: 'stream_end', chat_id: 'default', turn_id: 'turn-a', text: 'ho scritto la lista' });
await tick(10);
assert.equal(did().length, 0, 'la riga prima della fine del turno');
frame({ event: 'turn_end', chat_id: 'default', turn_id: 'turn-a' });
await until(() => did().length, { msg: 'nessuna riga a fine turno' });
""")


def test_opening_the_row_lists_and_goes_back_then_undo() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'scrivi', turnId: 'turn-a' },
  { role: 'assistant', text: 'fatto', turnId: 'turn-a' },
] };
await boot();
await until(() => did().length);
did()[0].click();
const detail = () => $('home-thread').querySelector('.home-did-detail');
await until(() => detail()?.querySelector('.home-peer-done'), { msg: 'l\\u2019elenco non si apre' });
assert.equal(detail().querySelectorAll('.home-peer-done-item').length, 3);
assert.ok(detail().querySelector('.is-egress .home-peer-done-note'), 'la ricerca non dice che non si annulla');
detail().querySelector('.home-did-act').click();
await until(() => calls.some(([m]) => m === 'undo'), { msg: 'il ritorno non e\\u2019 partito' });
assert.deepEqual(calls.find(([m]) => m === 'undo')[1], { turn: 'turn-a', expect_end: 'tok-1' });
await until(() => !detail(), { msg: 'l\\u2019elenco resta aperto dopo il ritorno' });
const undo = document.querySelector('.mobile-toast-action');
assert.ok(undo, 'nessun Annulla dopo il ritorno');
undo.click();
await until(() => calls.some(([m]) => m === 'redo'), { msg: 'Annulla non rimette' });
""")


def test_the_rows_come_back_when_the_thread_is_drawn_again() -> None:
    """Il filo riletto ha nodi nuovi: la riga torna, senza richiedere i conti."""
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'scrivi la lista', turnId: 'turn-a' },
  { role: 'assistant', text: 'fatto', turnId: 'turn-a' },
] };
const app = await boot();
await until(() => did().length, { msg: 'nessuna riga al primo disegno' });
const first = did()[0];
await app.chat.reload();
await until(() => did().length === 1 && did()[0] !== first,
  { msg: 'dopo la rilettura la riga non torna' });
assert.equal(asked.filter((t) => t === 'turn-a').length, 1, 'i conti di un turno chiuso richiesti due volte');
""")


def test_a_turn_asked_while_it_ran_is_asked_again_at_its_end() -> None:
    """I conti presi a meta' turno sono parziali: alla fine si richiedono."""
    run_home(_HEAD + """
delete touched['turn-a'];
const app = await boot();
frame({ event: 'stream_end', chat_id: 'default', turn_id: 'turn-a', text: 'sto scrivendo la lista' });
await tick(10);
/* Una pagina di storia caricata in mezzo: la bolla del turno in corso non si chiede. */
app.chat._shownTurns();
await tick(10);
assert.ok(!asked.includes('turn-a'), 'il turno in corso chiesto prima della fine');
/* E se qualcuno l'ha chiesto lo stesso, a meta': niente riga, ancora. */
await app.did.ask(['turn-a']);
assert.equal(did().length, 0);
touched['turn-a'] = { files: 1, jobs: 0, others: 0, working: false, state: 'open' };
frame({ event: 'turn_end', chat_id: 'default', turn_id: 'turn-a' });
await until(() => did().length, { msg: 'a fine turno i conti non sono stati richiesti' });
""")


def test_a_helper_still_working_is_asked_again_when_a_later_turn_ends() -> None:
    """«Un aiutante lavora ancora» non resta scritto dopo che ha finito."""
    run_home(_HEAD + """
touched['turn-a'] = { files: 0, jobs: 0, others: 0, working: true, state: 'open' };
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'cerca i voli', turnId: 'turn-a' },
  { role: 'assistant', text: 'ci guardo', turnId: 'turn-a' },
] };
await boot();
await until(() => did().length, { msg: 'nessuna riga per il turno con l\\u2019aiutante' });
const before = did()[0].textContent;
touched['turn-a'] = { files: 2, jobs: 0, others: 0, working: false, state: 'open' };
frame({ event: 'stream_end', chat_id: 'default', turn_id: 'turn-b', text: 'l\\u2019aiutante ha finito' });
frame({ event: 'turn_end', chat_id: 'default', turn_id: 'turn-b' });
await until(() => did()[0]?.textContent !== before, { msg: 'la riga dice ancora che l\\u2019aiutante lavora' });
assert.match(did()[0].textContent, /2/);
""")


def test_a_turn_still_running_says_so_instead_of_offering_to_go_back() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'scrivi', turnId: 'turn-a' },
  { role: 'assistant', text: 'sto scrivendo', turnId: 'turn-a' },
] };
rpcAnswers['turn.work'] = (p) => ({ ok: true, turn: p.turn, working: false, running: true,
  state: 'open', expect_end: null, items: [{ kind: 'file', label: 'notes/a.md', undoable: true }] });
await boot();
await until(() => did().length);
did()[0].click();
const detail = () => $('home-thread').querySelector('.home-did-detail');
await until(() => detail()?.querySelector('.home-did-note'), { msg: 'nessuna nota sul turno in corso' });
assert.equal(detail().querySelector('.home-did-act'), null, 'offre il ritorno a turno in corso');
""")
