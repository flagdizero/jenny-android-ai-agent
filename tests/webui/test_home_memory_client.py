"""«Cosa ricorda di te» in casa: la riga, i temi e i gesti.

Si guida la casa intera (``support.home_dom``) con un finto gateway che tiene
le voci in memoria e risponde ai comandi ``memory.*``: conta il legame fra la
stanza, il foglio (correggi, togli), l'«Annulla» e «Tolte da te».
I dati sono inventati (il repo e' pubblico).
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, rpcAnswers, rpcFailed, sent, $ } from './boot.mjs';
const mem = {
  dreaming: false, sorting: false,
  user: [
    { id: 'a1', text: 'Name: Alex Doe', heading: '', locked: false, theme: 'identity' },
    { id: 'b2', text: 'Lives in Lodi', heading: '', locked: true, theme: 'identity' },
    { id: 'c3', text: 'Has a sister, Marta', heading: '', locked: false, theme: 'people' },
    { id: 'd4', text: 'Wants short answers', heading: '', locked: false, theme: 'answers' },
    { id: 'e5', text: 'Collects stamps', heading: '', locked: false, theme: '' },
  ],
  notes: [{ id: 'n1', text: 'The garden uses the Tuesday market list', heading: '', locked: false }],
  recent: [{ kind: 'removed', id: 'r1', text: 'Likes jazz', date: '2026-11-21', file: 'user' }],
};
const calls = [];
rpcAnswers['memory.list'] = () => ({ ok: true, dreaming: mem.dreaming, sorting: mem.sorting,
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
const sheet = () => $('home-memory-sheet');
const themes = () => [...room().querySelectorAll('.home-memory-theme')].map((n) => n.dataset.theme);
const inTheme = (key) => [...room().querySelectorAll(`.home-memory-theme[data-theme="${key}"] .home-memory-sentence`)].map((n) => n.textContent);
const row = (id) => room().querySelector(`.home-memory-section[data-file] .home-memory-row[data-id="${id}"]`);
const open = async (id) => { row(id).querySelector('.home-memory-text').click(); await until(() => sheet().open, { msg: 'il foglio non si apre' }); };
const ready = async () => { const app = await boot(); app.openMemory(); await until(() => room().querySelector('.home-memory-row'), { msg: 'la stanza e\\u2019 vuota' }); return app; };
"""


def test_the_row_and_the_portrait_by_theme() -> None:
    run_home(_HEAD + """
const app = await boot();
await app._openSettings();
await until(() => $('home-memory-value').textContent, { msg: 'la riga non dice niente' });
assert.match($('home-memory-value').textContent, /5/);
app.openMemory();
await until(() => themes().length, { msg: 'nessun tema' });
assert.equal(app.view, 'memory');
assert.equal(app._backTarget(), 'settings');
// I temi nell'ordine fisso, «Altro» in fondo con la frase che non ne ha uno.
assert.deepEqual(themes(), ['identity', 'people', 'answers', 'other']);
assert.deepEqual(inTheme('identity'), ['Name: Alex Doe', 'Lives in Lodi']);
assert.deepEqual(inTheme('other'), ['Collects stamps']);
// Solo testo: nessuna icona sulle righe, nessun segno sulla frase corretta da te.
assert.equal(room().querySelector('.home-memory-row .ti'), null, 'c\\u2019e\\u2019 un\\u2019icona su una riga');
assert.equal(room().querySelector('.home-memory-locked'), null);
""")


def test_without_themes_the_sentences_stay_in_a_row() -> None:
    # Il modello non le ha ancora ordinate: niente «Altro» che le contiene tutte.
    run_home(_HEAD + """
for (const e of mem.user) e.theme = '';
mem.sorting = true;
await ready();
assert.deepEqual(themes(), []);
assert.equal(room().querySelectorAll('.home-memory-section[data-file="user"] .home-memory-row').length, 5);
assert.ok(room().querySelector('.home-memory-sorting'), 'non dice che le sta ordinando');
// Quando l'ordine arriva, la stanza si rilegge da sola.
mem.sorting = false;
for (const e of mem.user) e.theme = 'tastes';
await until(() => themes().length, { timeout: 6000, msg: 'non si e\\u2019 riletta' });
assert.deepEqual(themes(), ['tastes']);
assert.equal(room().querySelector('.home-memory-sorting'), null);
""")


def test_the_two_closed_sections_open_in_place() -> None:
    run_home(_HEAD + """
await ready();
const fold = (kind) => room().querySelector(`.home-memory-section.is-${kind} .home-memory-fold`);
assert.equal(room().querySelector('.is-notes .home-memory-row'), null, 'gli appunti sono gia\\u2019 aperti');
assert.equal(fold('notes').getAttribute('aria-expanded'), 'false');
fold('notes').click();
await until(() => room().querySelector('.is-notes .home-memory-row'), { msg: 'gli appunti non si aprono' });
assert.equal(fold('notes').getAttribute('aria-expanded'), 'true');
fold('removed').click();
await until(() => room().querySelector('.is-removed .home-memory-putback'), { msg: '«Tolte da te» non si apre' });
room().querySelector('.is-removed .home-memory-putback').click();
await until(() => calls.some(([m]) => m === 'restore'));
assert.deepEqual(calls.find(([m]) => m === 'restore')[1], { id: 'r1' });
""")


def test_no_removed_section_when_nothing_was_removed() -> None:
    run_home(_HEAD + """
mem.recent = [];
await ready();
assert.equal(room().querySelector('.home-memory-section.is-removed'), null);
""")


def test_correcting_is_one_save() -> None:
    run_home(_HEAD + """
await ready();
await open('a1');
assert.equal(sheet().querySelectorAll('.home-memory-save').length, 1);
sheet().querySelector('.home-memory-field').value = 'Name: Alex D. Doe';
sheet().querySelector('.home-memory-save').click();
await until(() => calls.some(([m]) => m === 'correct'), { msg: 'la correzione non e\\u2019 partita' });
assert.deepEqual(calls.find(([m]) => m === 'correct')[1],
  { file: 'user', id: 'a1', text: 'Name: Alex D. Doe', reason: 'wrong' });
await until(() => !sheet().open, { msg: 'il foglio e\\u2019 rimasto aperto' });
await until(() => inTheme('identity').includes('Name: Alex D. Doe'));
""")


def test_removing_is_in_the_sheet_and_undo_puts_it_back_as_it_was() -> None:
    # «Annulla» su una voce senza lucchetto non glielo mette; su una che l'aveva, si'.
    run_home(_HEAD + """
await ready();
await open('c3');
sheet().querySelector('.home-memory-drop').click();
await until(() => !row('c3'), { msg: '«Togli» non ha tolto' });
assert.ok(!sheet().open, 'il foglio e\\u2019 rimasto aperto');
const undo = () => [...document.querySelectorAll('.mobile-toast-action')].pop();
assert.ok(undo(), 'nessun Annulla');
undo().click();
await until(() => calls.some(([m]) => m === 'restore'));
assert.deepEqual(calls.find(([m]) => m === 'restore')[1], { id: 'c3', lock: false });
calls.length = 0;
await open('b2');
sheet().querySelector('.home-memory-drop').click();
await until(() => !row('b2'));
undo().click();
await until(() => calls.some(([m]) => m === 'restore'));
assert.deepEqual(calls.find(([m]) => m === 'restore')[1], { id: 'b2' });
""")


def test_a_gesture_keeps_your_place_in_the_list() -> None:
    # Una finta impaginazione: ogni riga e ogni titolo alti 100, la stanza 600.
    # Sei a meta' pagina; togli una frase sopra di te: quella che vedevi in cima
    # resta dov'era, invece di saltare.
    run_home(_HEAD + """
const app = await ready();
const body = room();
let st = 0;
Object.defineProperty(body, 'scrollTop', { get: () => st, set: (v) => { st = v; }, configurable: true });
const real = Element.prototype.getBoundingClientRect;
Element.prototype.getBoundingClientRect = function () {
  if (this === body) return { top: 0, bottom: 600, height: 600 };
  const marks = [...body.querySelectorAll('[data-place]')];
  const i = marks.indexOf(this);
  if (i < 0) return real.call(this);
  const top = i * 100 - st;
  return { top, bottom: top + 100, height: 100 };
};
const marks = () => [...body.querySelectorAll('[data-place]')];
st = marks().findIndex((n) => n.dataset.place === 'c3') * 100 + 50;
const seen = () => marks().find((n) => n.getBoundingClientRect().bottom > 0).dataset.place;
assert.equal(seen(), 'c3');
await app.memoryRoom._remove('user', mem.user.find((x) => x.id === 'a1'));
assert.equal(seen(), 'c3', 'dopo aver tolto una frase la stanza mostra un\\u2019altra voce');
assert.equal(marks().find((n) => n.dataset.place === 'c3').getBoundingClientRect().top, -50);
""")


def test_a_conflict_says_it_changed_and_rereads() -> None:
    run_home(_HEAD + """
const app = await ready();
const reads = () => sent.filter((m) => m.method === 'memory.list').length;
const before = reads();
await app.memoryRoom._correct('user', { id: 'gone' }, 'x');
assert.ok(reads() > before, 'dopo un conflitto non rilegge');
assert.ok([...document.querySelectorAll('.mobile-toast')].some((t) => t.textContent.length), 'nessun avviso');
""")


def test_an_empty_memory_says_how_it_fills() -> None:
    run_home(_HEAD + """
mem.user = []; mem.notes = []; mem.recent = [];
const app = await boot();
app.openMemory();
await until(() => room().querySelector('.home-memory-empty'), { msg: 'la stanza vuota non dice niente' });
assert.equal(room().querySelector('.home-memory-foot'), null, 'la stanza vuota lo dice due volte');
""")
