"""«Cosa ricorda di te» in casa: la riga, le due sezioni e i gesti.

Si guida la casa intera (``support.home_dom``) con un finto gateway che tiene
le voci in memoria e risponde ai comandi ``memory.*``: conta il legame fra la
stanza, il foglio di correzione, il cestino con «Annulla» e «Rimetti».
I dati sono inventati (il repo e' pubblico).
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, rpcAnswers, rpcFailed, sent, $ } from './boot.mjs';
const mem = {
  dreaming: false,
  user: [
    { id: 'a1', text: 'Name: Alex Doe', heading: 'Basics', locked: false, lost: [] },
    { id: 'b2', text: 'Lives in Lodi', heading: 'Basics', locked: true,
      lost: [{ words: ['Oreste'], id: 'c3', text: 'Lives in Cremona with Oreste', date: '2026-11-20' }] },
  ],
  notes: [{ id: 'd4', text: 'The garden uses the Tuesday market list', heading: '', locked: false, lost: [] }],
  recent: [
    { kind: 'removed', id: 'e5', text: 'Likes jazz', date: '2026-11-21', file: 'user' },
    { kind: 'wrong', id: 'f6', text: 'Has a cat', date: '2026-11-22', file: 'user' },
  ],
};
const calls = [];
rpcAnswers['memory.list'] = () => ({ ok: true, dreaming: mem.dreaming,
  files: [{ file: 'user', entries: mem.user }, { file: 'memory', entries: mem.notes }],
  recent: mem.recent });
rpcAnswers['memory.correct'] = (p) => {
  calls.push(['correct', p]);
  if (p.id === 'gone') return rpcFailed('conflict', 'changed');
  const e = mem.user.find((x) => x.id === p.id);
  Object.assign(e, { id: 'n' + e.id, text: p.text, locked: true });
  return { ok: true, id: e.id };
};
rpcAnswers['memory.remove'] = (p) => {
  calls.push(['remove', p]);
  const e = mem.user.find((x) => x.id === p.id);
  mem.user = mem.user.filter((x) => x.id !== p.id);
  mem.recent.unshift({ kind: 'removed', id: e.id, text: e.text, date: '2026-11-25', file: 'user' });
  return { ok: true, id: p.id };
};
rpcAnswers['memory.restore'] = (p) => { calls.push(['restore', p]); return { ok: true, id: p.id, file: 'user' }; };
const room = () => $('home-memory-body');
const sentences = (sec) => [...room().querySelectorAll(`.home-memory-section[data-file="${sec}"] .home-memory-sentence`)].map((n) => n.textContent);
"""


def test_the_row_and_the_two_sections() -> None:
    run_home(_HEAD + """
const app = await boot();
await app._openSettings();
await until(() => $('home-memory-value').textContent, { msg: 'la riga non dice niente' });
assert.match($('home-memory-value').textContent, /2/);
app.openMemory();
await until(() => sentences('user').length, { msg: 'la stanza e\\u2019 vuota' });
assert.equal(app.view, 'memory');
assert.equal(app._backTarget(), 'settings');
assert.deepEqual(sentences('user'), ['Name: Alex Doe', 'Lives in Lodi']);
assert.deepEqual(sentences('memory'), ['The garden uses the Tuesday market list']);
const locked = room().querySelector('.home-memory-row[data-id="b2"] .home-memory-locked');
assert.ok(locked, 'la voce corretta non dice che l\u2019hai sistemata tu');
assert.ok(locked.querySelector('.ti-pencil-check') && !locked.querySelector('.ti-lock'),
  'una voce sistemata da te ha ancora il lucchetto');
assert.ok(room().querySelector('.home-memory-row[data-id="b2"] .home-memory-lost'), 'il dettaglio perso non si vede');
const recent = room().querySelector('.home-memory-section.is-recent');
assert.ok(recent.querySelector('.is-wrong'), 'la voce non vera non e\\u2019 barrata');
assert.equal(recent.querySelector('.is-wrong .home-memory-putback'), null, 'una voce non vera si rimette');
assert.ok(recent.querySelector('.is-removed .home-memory-putback'), 'una voce tolta non si rimette');
""")


def test_correcting_says_why_and_locks() -> None:
    run_home(_HEAD + """
const app = await boot();
app.openMemory();
await until(() => sentences('user').length);
room().querySelector('.home-memory-row[data-id="a1"] .home-memory-text').click();
const sheet = $('home-memory-sheet');
await until(() => sheet.open, { msg: 'il foglio non si apre' });
sheet.querySelector('.home-memory-field').value = 'Name: Alex D. Doe';
sheet.querySelector('.home-memory-wrong').click();
await until(() => calls.some(([m]) => m === 'correct'), { msg: 'la correzione non e\\u2019 partita' });
assert.deepEqual(calls.find(([m]) => m === 'correct')[1],
  { file: 'user', id: 'a1', text: 'Name: Alex D. Doe', reason: 'wrong' });
await until(() => !sheet.open, { msg: 'il foglio e\\u2019 rimasto aperto' });
await until(() => sentences('user').includes('Name: Alex D. Doe'));
""")


def test_the_trash_removes_and_undo_restores() -> None:
    run_home(_HEAD + """
const app = await boot();
app.openMemory();
await until(() => sentences('user').length);
room().querySelector('.home-memory-row[data-id="a1"] .home-memory-remove').click();
await until(() => !sentences('user').includes('Name: Alex Doe'), { msg: 'il cestino non ha tolto' });
const undo = document.querySelector('.mobile-toast-action');
assert.ok(undo, 'nessun Annulla');
undo.click();
await until(() => calls.some(([m, p]) => m === 'restore' && p.id === 'a1'), { msg: 'Annulla non rimette' });
""")


def test_put_back_a_lost_detail_goes_after_its_entry() -> None:
    run_home(_HEAD + """
const app = await boot();
app.openMemory();
await until(() => sentences('user').length);
const row = () => room().querySelector('.home-memory-row[data-id="b2"]');
// Chiusa: una riga sola, senza parole sciolte e senza «Rimetti».
assert.equal(row().querySelector('.home-memory-putback'), null, 'il dettaglio perso e\u2019 gia\u2019 aperto');
assert.ok(!row().textContent.includes('Oreste'), 'la riga chiusa mostra parole sciolte');
row().querySelector('.home-memory-lost-head').click();
await until(() => row().querySelector('.home-memory-old'), { msg: 'la versione di prima non si apre' });
// Aperta: la frase vecchia intera, col dettaglio perso evidenziato.
assert.equal(row().querySelector('.home-memory-old-text').textContent, 'Lives in Cremona with Oreste');
assert.deepEqual([...row().querySelectorAll('mark')].map((m) => m.textContent), ['Oreste']);
row().querySelector('.home-memory-putback').click();
await until(() => calls.some(([m]) => m === 'restore'));
assert.deepEqual(calls.find(([m]) => m === 'restore')[1], { id: 'c3', after: 'b2' });
// Il ridisegno dopo «Rimetti» la lascia aperta.
await until(() => row()?.querySelector('.home-memory-old'), { msg: 'il ridisegno l\u2019ha richiusa' });
""")


def test_undo_puts_it_back_as_it_was() -> None:
    # «Annulla» su una voce che non era sistemata da te non la rende tale; su
    # una che lo era, si'. Prima ogni voce rimessa prendeva il lucchetto.
    run_home(_HEAD + """
const app = await boot();
app.openMemory();
await until(() => sentences('user').length);
mem.user[1].lost = [];
room().querySelector('.home-memory-row[data-id="a1"] .home-memory-remove').click();
await until(() => document.querySelector('.mobile-toast-action'));
document.querySelector('.mobile-toast-action').click();
await until(() => calls.some(([m]) => m === 'restore'));
assert.deepEqual(calls.find(([m]) => m === 'restore')[1], { id: 'a1', lock: false });
calls.length = 0;
mem.user.unshift({ id: 'a1', text: 'Name: Alex Doe', heading: 'Basics', locked: false, lost: [] });
await app.memoryRoom.refresh();
await app.memoryRoom._remove('user', mem.user.find((x) => x.id === 'b2'));
await until(() => document.querySelector('.mobile-toast-action'));
[...document.querySelectorAll('.mobile-toast-action')].pop().click();
await until(() => calls.some(([m]) => m === 'restore'));
assert.deepEqual(calls.find(([m]) => m === 'restore')[1], { id: 'b2' });
""")


def test_a_gesture_keeps_your_place_in_the_list() -> None:
    # Una finta impaginazione: ogni riga alta 100, la stanza alta 600. Sei a
    # meta' della seconda voce; il cestino toglie la prima, sopra di te: la
    # seconda resta dove la vedevi, invece di saltare.
    run_home(_HEAD + """
const app = await boot();
app.openMemory();
await until(() => sentences('user').length);
const body = room();
let st = 0;
Object.defineProperty(body, 'scrollTop', { get: () => st, set: (v) => { st = v; }, configurable: true });
const real = Element.prototype.getBoundingClientRect;
Element.prototype.getBoundingClientRect = function () {
  if (this === body) return { top: 0, bottom: 600, height: 600 };
  const rows = [...body.querySelectorAll('[data-place]')];
  const i = rows.indexOf(this);
  if (i < 0) return real.call(this);
  const top = i * 100 - st;
  return { top, bottom: top + 100, height: 100 };
};
st = 150;
const seen = () => [...body.querySelectorAll('[data-place]')].find((n) => n.getBoundingClientRect().bottom > 0).dataset.place;
assert.equal(seen(), 'b2');
body.querySelector('.home-memory-row[data-id="a1"] .home-memory-remove').click();
await until(() => !sentences('user').includes('Name: Alex Doe'));
assert.equal(seen(), 'b2', 'dopo il cestino la stanza mostra un\u2019altra voce');
assert.equal(st, 50);
""")


def test_a_conflict_says_it_changed_and_rereads() -> None:
    run_home(_HEAD + """
const app = await boot();
app.openMemory();
await until(() => sentences('user').length);
const reads = () => sent.filter((m) => m.method === 'memory.list').length;
const before = reads();
await app.memoryRoom._correct('user', { id: 'gone' }, 'x', 'changed');
assert.ok(reads() > before, 'dopo un conflitto non rilegge');
assert.ok([...document.querySelectorAll('.mobile-toast')].some((t) => t.textContent.length), 'nessun avviso');
""")
