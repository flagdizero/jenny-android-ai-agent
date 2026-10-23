"""Il piano a lista nella casa vera: tasto, scheda, fogli, lucchetto, bollino.

Il piano lo muove il codice del gateway; la casa ne mostra lo stato e manda i
tocchi dell'utente come RPC ``plan.*``. Qui si fissa il contratto fra le due
parti, col gateway finto: che il tasto Piano viaggi nel messaggio (e non in
uno stato sul server), che Approva mandi la versione vista e i passi tolti,
che un ``conflict`` rilegga invece di insistere, che in corsa la chat sia
spenta e il quaderno chiuso, e che il ritorno indietro mandi i gruppi tenuti.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, sent, rpcAnswers, rpcFailed, toasts, locales, routes, thread as shown, $ } from './boot.mjs';
const P = locales.en.home.plan;
const step = (id, title, over = {}) => ({ id, title, detail: `detail of ${title}`, kind: 'research',
  status: 'pending', tag: null, started_at: null, ended_at: null, note: null, ...over });
const plan = (over = {}) => ({
  id: 'p1', notebook: 'orto', session_key: 'project:orto', version: 1, status: 'proposed',
  title: 'Rebuild the wiki',
  steps: [step('s1', 'Search maple'), step('s2', 'Throw away old pages', { kind: 'delete' }),
    step('s3', 'Check links', { kind: 'check', tag: 'new' })],
  dropped: [], photos: { start: null, end: null, before_rollback: null, after_rollback: null },
  blocked: null, history: [], created_at: '2026-10-23T10:00:00Z', updated_at: '2026-10-23T10:00:00Z',
  done: 0, total: 3, current_step_id: null, can_rollback: false, can_undo_rollback: false, ...over,
});
let current = null;
rpcAnswers['plan.get'] = () => ({ ok: true, plan: current });
let open = [];
rpcAnswers['plan.list'] = () => ({ ok: true, plans: open });
const card = () => document.querySelector('#home-thread > .home-live > .home-plan');
const rpcs = (m) => sent.filter((x) => x.type === 'rpc' && x.method === m);
const messages = () => sent.filter((x) => x.type === 'message');
const text = (node) => node.textContent.replace(/\\s+/g, ' ').trim();
const action = (name) => card().querySelector(`[data-plan-action="${name}"]`);
const sheet = () => $('home-plan-sheet');
const row = (name) => sheet().querySelector(`[data-plan-action="${name}"]`);
const app = await boot();
const toNotebook = async () => {
  await app.switchConversation('project:orto');
  await tick(40);
};
"""


def test_the_plan_toggle_travels_in_the_message() -> None:
    run_home(_HEAD + """
await tick(30);
const toggle = document.querySelector('.home-plan-toggle');
assert.ok(toggle, 'il tasto c\\'e\\'');
assert.equal(toggle.hidden, true, 'nella chat di sempre il tasto non c\\'e\\'');
await toNotebook();
assert.equal(toggle.hidden, false, 'nella chat di un quaderno si');
// Spento: il messaggio parte normale.
$('home-input').value = 'hello';
$('home-send').click();
assert.equal(messages().at(-1).plan, undefined);
toggle.click();
assert.ok(document.querySelector('.home-field').classList.contains('is-plan'));
assert.equal($('home-input').placeholder, P.placeholder);
assert.match(text(document.querySelector('.home-plan-hint')), /touches nothing/);
$('home-input').value = 'rebuild the wiki';
$('home-send').click();
const msg = messages().at(-1);
assert.equal(msg.content, 'rebuild the wiki');
assert.equal(msg.plan, true);
assert.equal(msg.plan_revise, undefined, 'senza Rivedi non c\\'e\\' revisione');
// Il tasto vuol dire «il prossimo messaggio chiede un piano»: partito, si spegne.
assert.equal(toggle.getAttribute('aria-pressed'), 'false', 'dopo l\\'invio il tasto e\\' spento');
assert.equal(document.querySelector('.home-field').classList.contains('is-plan'), false);
assert.notEqual($('home-input').placeholder, P.placeholder);
assert.equal(document.querySelector('.home-plan-hint').hidden, true);
$('home-input').value = 'and how long will it take?';
$('home-send').click();
assert.equal(messages().at(-1).plan, undefined, 'la domanda dopo e\\' chiacchiera, non un piano');
// Chi manda senza il composer non porta il tasto: `/stop` non diventa un piano,
// e non spegne il tasto acceso per il messaggio vero.
toggle.click();
$('home-input').value = '/stop';
$('home-send').click();
assert.equal(messages().at(-1).content, '/stop');
assert.equal(messages().at(-1).plan, undefined, '/stop non e\\' un turno di pianificazione');
assert.equal(toggle.getAttribute('aria-pressed'), 'true', '/stop non consuma il tasto');
$('home-input').value = 'now plan it';
$('home-send').click();
assert.equal(messages().at(-1).plan, true);
assert.equal(toggle.getAttribute('aria-pressed'), 'false');
// Cambiando conversazione il tasto torna spento.
await app.switchConversation('unified:default');
await tick(30);
await toNotebook();
assert.equal(document.querySelector('.home-field').classList.contains('is-plan'), false);
""")


def test_a_proposed_plan_cuts_steps_and_approves_what_it_showed() -> None:
    run_home(_HEAD + """
current = plan({ version: 2 });
await toNotebook();
assert.ok(card(), 'la scheda compare nel posto vivo');
assert.match(text(card()), /Rebuild the wiki/);
assert.match(text(card()), new RegExp(P.tag.trash), 'un passo che cancella porta il cestino');
assert.match(text(card()), new RegExp(P.tag.new), 'il tag della versione nuova');
assert.match(text(card()), new RegExp(P.photoWord), 'la foto e\\' una riga, non un passo');
assert.equal(card().querySelectorAll('.home-plan-item').length, 3);
// Tolgo il passo 2: barrato, e l'Approva conta i passi rimasti.
card().querySelector('[data-step-id="s2"] .home-plan-box').click();
assert.ok(card().querySelector('[data-step-id="s2"]').classList.contains('is-off'));
assert.equal(text(action('approve')), P.approveSome.replace('{count}', '2'));
rpcAnswers['plan.approve'] = () => ({ ok: true });
action('approve').click();
await tick(30);
assert.deepEqual(rpcs('plan.approve')[0].params, { id: 'p1', version: 2, cut: ['s2'] });
""")


def test_a_conflict_says_so_and_rereads_the_new_version() -> None:
    run_home(_HEAD + """
current = plan();
await toNotebook();
card().querySelector('[data-step-id="s1"] .home-plan-box').click();
rpcAnswers['plan.approve'] = () => { current = plan({ version: 3 }); return rpcFailed('conflict', 'newer'); };
action('approve').click();
await tick(40);
assert.ok(toasts().includes(P.error.conflict), toasts());
assert.match(text(card().querySelector('.home-plan-ver')), /v3/);
assert.equal(card().querySelectorAll('.is-off').length, 0, 'la versione nuova ha la sua lista');
assert.equal(text(action('approve')), P.approve);
""")


def test_revise_marks_the_next_message_with_the_plan_id() -> None:
    run_home(_HEAD + """
current = plan();
await toNotebook();
action('revise').click();
assert.ok(card().classList.contains('is-review'));
assert.equal(action('approve'), null, 'in revisione la scheda aspetta il messaggio');
assert.match(text(document.querySelector('.home-plan-hint')), /revising plan v1/);
$('home-input').value = 'leave the substrate alone';
$('home-send').click();
await tick(10);
const msg = messages().at(-1);
assert.equal(msg.plan, true);
assert.equal(msg.plan_revise, 'p1');
assert.equal(card().classList.contains('is-review'), false, 'la revisione vale un messaggio');
$('home-input').value = 'and one more thing';
$('home-send').click();
assert.equal(messages().at(-1).plan_revise, undefined);
assert.equal(messages().at(-1).plan, undefined, 'dopo la revisione il messaggio dopo e\\' normale');
assert.equal(document.querySelector('.home-plan-toggle').getAttribute('aria-pressed'), 'false');
""")


def test_a_running_plan_ticks_and_turns_the_composer_off() -> None:
    run_home(_HEAD + """
current = plan({ status: 'running', version: 2, done: 1, total: 3, current_step_id: 's2',
  history: [{ at: '2026-10-23T10:58:00Z', event: 'approved', version: 2 }],
  steps: [step('s1', 'Search maple', { status: 'done', ended_at: '2026-10-23T11:09:00Z', note: '12 sources' }),
    step('s2', 'Throw away old pages', { kind: 'delete', status: 'now', started_at: new Date().toISOString() }),
    step('s3', 'Check links', { kind: 'check' })] });
open = [{ id: 'p1', notebook: 'orto', status: 'running', title: 'Rebuild the wiki', done: 1, total: 3 }];
await toNotebook();
assert.ok(card().classList.contains('is-running'));
assert.ok(card().querySelector('[data-step-id="s1"]').classList.contains('is-done'));
assert.match(text(card().querySelector('[data-step-id="s1"]')), /12 sources/);
assert.ok(card().querySelector('[data-step-id="s2"]').classList.contains('is-now'));
assert.match(text(card().querySelector('.home-plan-ver')), /2 of 3/);
assert.equal(card().querySelectorAll('.home-work-seg.is-past').length, 1);
// In corsa: solo Pausa e Ferma, e la chat e' spenta.
assert.deepEqual([...card().querySelectorAll('[data-plan-action]')].map((b) => b.dataset.planAction),
  ['pause', 'stop']);
assert.equal($('home-input').disabled, true);
assert.equal($('home-send').disabled, true);
assert.equal(document.querySelector('.home-plan-toggle').hidden, true);
assert.match(text(document.querySelector('.home-plan-hint')), /Pause to talk/);
// Il passo dopo: un frame, una rilettura.
current = { ...current, done: 2, current_step_id: 's3', steps: [current.steps[0],
  { ...current.steps[1], status: 'done', ended_at: '2026-10-23T11:20:00Z' },
  { ...current.steps[2], status: 'now', started_at: new Date().toISOString() }] };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.ok(card().querySelector('[data-step-id="s2"]').classList.contains('is-done'));
assert.ok(card().querySelector('[data-step-id="s3"]').classList.contains('is-now'));
rpcAnswers['plan.pause'] = () => ({ ok: true });
action('pause').click();
await tick(30);
assert.deepEqual(rpcs('plan.pause')[0].params, { id: 'p1' });
// In pausa la chat si riaccende.
current = { ...current, status: 'paused' };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal($('home-input').disabled, false);
assert.ok(action('resume'));
""")


def test_a_done_step_shows_what_it_did() -> None:
    run_home(_HEAD + """
current = plan({ status: 'running', done: 1, steps: [
  step('s1', 'Search maple', { status: 'done', ended_at: '2026-10-23T11:09:00Z' }),
  step('s2', 'Write', { kind: 'write', status: 'now' }), step('s3', 'Check', { kind: 'check' })] });
await toNotebook();
rpcAnswers['plan.step_files'] = () => ({ ok: true, added: ['raw/research/maple.md'], changed: [], removed: [] });
card().querySelector('[data-step-id="s1"]').click();
await tick(30);
assert.deepEqual(rpcs('plan.step_files')[0].params, { id: 'p1', step: 's1' });
assert.ok(sheet().open);
assert.match(text(sheet()), /Step 1 · Search maple/);
assert.match(text(sheet()), /raw\\/research\\/maple\\.md/);
// Un passo da fare non si tocca.
card().querySelector('[data-step-id="s3"]').click();
await tick(20);
assert.equal(rpcs('plan.step_files').length, 1);
""")


def test_a_blocked_plan_takes_an_answer() -> None:
    run_home(_HEAD + """
current = plan({ status: 'blocked', done: 1, current_step_id: 's2',
  blocked: { question: 'Sun or half shade?', step: 's2', at: '2026-10-23T11:25:00Z' },
  steps: [step('s1', 'Search', { status: 'done' }), step('s2', 'Compare', { status: 'blocked' }),
    step('s3', 'Write', { kind: 'write' })] });
await toNotebook();
assert.ok(card().classList.contains('is-blocked'));
assert.match(text(card()), /Sun or half shade\\?/);
assert.equal($('home-input').disabled, false, 'bloccata, la chat si riaccende');
const field = card().querySelector('.home-plan-answer');
assert.equal(card().querySelector('.home-plan-answer-send').disabled, true);
field.focus();
field.value = 'my note';
field.dispatchEvent(new Event('input'));
field.setSelectionRange(2, 2);
// Un frame qualunque ridisegna la scheda: la risposta a meta' resta, e resta
// lo stesso campo, col fuoco e il cursore dov'erano (una composizione IME in
// corso vive nel nodo: rifarlo la spezzava).
frame({ event: 'goal_status', status: 'running' });
frame({ event: 'goal_status', status: 'idle' });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(card().querySelector('.home-plan-answer'), field, 'lo stesso nodo');
assert.equal(document.activeElement, field, 'col fuoco');
assert.equal(field.selectionStart, 2, 'il cursore non salta in fondo');
assert.equal(card().querySelector('.home-plan-answer').value, 'my note');
const send = card().querySelector('.home-plan-answer-send');
assert.equal(send.disabled, false);
rpcAnswers['plan.answer'] = () => ({ ok: true });
send.click();
await tick(30);
assert.deepEqual(rpcs('plan.answer')[0].params, { id: 'p1', text: 'my note' });
// La risposta non passa dal registro della chat: la bolla la disegna la casa.
assert.equal(shown().at(-1), 'you: my note');
assert.deepEqual([...card().querySelectorAll('[data-plan-action]')].map((b) => b.dataset.planAction),
  ['stop']);
""")


def test_stop_offers_three_choices() -> None:
    run_home(_HEAD + """
current = plan({ status: 'running', done: 1, steps: [step('s1', 'A', { status: 'done' }),
  step('s2', 'B', { status: 'now' }), step('s3', 'C')] });
await toNotebook();
rpcAnswers['plan.stop'] = () => ({ ok: true });
action('stop').click();
assert.ok(sheet().open);
assert.match(text(sheet()), /1 of 3 steps done/);
row('continue').click();
assert.equal(sheet().open, false);
assert.equal(rpcs('plan.stop').length, 0, 'Continua non fa niente');
action('stop').click();
row('keep').click();
await tick(30);
assert.deepEqual(rpcs('plan.stop')[0].params, { id: 'p1', then: 'keep' });
action('stop').click();
row('back').click();
await tick(30);
assert.deepEqual(rpcs('plan.stop')[1].params, { id: 'p1', then: 'rollback' });
""")


def test_rollback_counts_first_and_keeps_the_groups_you_tick() -> None:
    run_home(_HEAD + """
current = plan({ status: 'done', done: 3, can_rollback: true,
  steps: [step('s1', 'A', { status: 'done' }), step('s2', 'B', { status: 'done' }),
    step('s3', 'C', { status: 'done' })] });
rpcAnswers['plan.changes'] = () => ({ ok: true, added: ['wiki/new.md', 'raw/research/x.md'],
  changed: ['wiki/index.md'], removed: ['wiki/old.md'], groups: {} });
rpcAnswers['plan.rollback_preview'] = () => ({ ok: true, expect_end: 'B1', report: {
  restored: ['wiki/old.md', 'wiki/index.md'], removed: ['wiki/new.md', 'raw/research/x.md'],
  left: ['wiki/acero.md'], kept: [], already: [], excluded: [],
  counts: { restored: 2, removed: 2, left: 1, kept: 0, already: 0, excluded: 0 } } });
rpcAnswers['plan.rollback'] = () => ({ ok: true, report: { counts: { restored: 2, removed: 1 } } });
await toNotebook();
await tick(30);
assert.ok(card().classList.contains('is-done'));
const cells = [...card().querySelectorAll('.home-plan-sum b')].map((b) => b.textContent);
assert.deepEqual(cells, ['1', '1', '1', '1'], 'i conti vengono dal confronto delle foto');
action('rollback').click();
await tick(30);
assert.ok(sheet().open);
assert.match(text(sheet()), /2 files come back/);
assert.match(text(sheet()), /2 new files go away/);
assert.match(text(sheet()), /acero/);
const boxes = [...sheet().querySelectorAll('.home-plan-keep input')].map((c) => c.dataset.group);
assert.deepEqual(boxes, ['pages', 'sources']);
const sources = sheet().querySelector('.home-plan-keep input[data-group="sources"]');
sources.checked = true;
sources.dispatchEvent(new Event('change'));
assert.match(text(sheet()), /1 new files go away/);
row('confirm').click();
await tick(40);
assert.deepEqual(rpcs('plan.rollback')[0].params, { id: 'p1', keep: ['sources'], expect_end: 'B1' });
""")


def test_rolled_back_offers_redo_and_undo() -> None:
    run_home(_HEAD + """
current = plan({ status: 'rolled_back', can_undo_rollback: true,
  steps: [step('s1', 'A', { status: 'done' }), step('s2', 'B', { status: 'done' })] });
await toNotebook();
assert.ok(card().querySelector('[data-step-id="s1"]').classList.contains('is-undone'));
rpcAnswers['plan.undo_rollback'] = () => ({ ok: true, report: {} });
action('undo').click();
await tick(30);
assert.deepEqual(rpcs('plan.undo_rollback')[0].params, { id: 'p1' });
action('redo').click();
const msg = messages().at(-1);
assert.equal(msg.plan, true);
assert.match(msg.content, /Rebuild the wiki/);
""")


def test_an_open_plan_locks_the_notebook_for_the_user() -> None:
    run_home(_HEAD + """
routes['/api/board'] = { exists: true, cards: [], notes: [], postits: [], connections: [],
  suggestions: [], since: [], offboard: 0, pages: 0, lastSeen: '', pageOwners: {}, full: false };
routes['/api/page'] = { wiki: 'orto', page: 'a.md', title: 'a', html: '<p>a</p>', raw: 'a' };
rpcAnswers['board.seen'] = () => ({ ok: true });
open = [{ id: 'p1', notebook: 'orto', status: 'proposed', title: 'x', done: 0, total: 3 }];
await toNotebook();
assert.equal(app.view, 'board');
assert.equal($('home-board-lock').hidden, true, 'una proposta non chiude niente');
open = [{ id: 'p1', notebook: 'orto', status: 'running', title: 'x', done: 1, total: 3 }];
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal($('home-board-lock').hidden, false);
assert.equal(text($('home-board-lock')), P.lock);
assert.equal($('home-board-add').hidden, true, 'il + sparisce');
app.board.openNewCard();
assert.ok(toasts().includes(P.lock));
await app.openPage('a.md', 'a');
await tick(30);
assert.equal(app.view, 'reader');
assert.equal($('home-reader-lock').hidden, false);
assert.equal($('home-edit').hidden, true, 'Modifica sparisce');
assert.equal(app.reader.startEdit(), false);
// Finito il piano, si riapre.
open = [];
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal($('home-reader-lock').hidden, true);
assert.equal($('home-edit').hidden, false);
""")


def test_the_notebook_cover_carries_the_plan_badge() -> None:
    run_home(_HEAD + """
open = [{ id: 'p1', notebook: 'orto', status: 'running', title: 'x', done: 2, total: 8 }];
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(30);
await app.who.show();
await tick(30);
const cover = document.querySelector('.home-who-cover[data-name="orto"]');
assert.ok(cover, 'la copertina c\\'e\\'');
const badge = cover.querySelector('.home-plan-badge');
assert.ok(badge);
assert.equal(text(badge), P.badge.replace('{done}', '2').replace('{total}', '8'));
assert.match(cover.getAttribute('aria-label'), /Plan 2\\/8/);
open = [];
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(document.querySelector('.home-who-cover[data-name="orto"] .home-plan-badge'), null);
""")


def test_the_strip_shows_when_the_card_scrolls_away() -> None:
    run_home(_HEAD + """
current = plan({ status: 'running', done: 1, current_step_id: 's2', steps: [
  step('s1', 'A', { status: 'done' }), step('s2', 'Search fig', { status: 'now' }), step('s3', 'C')] });
await toNotebook();
const thread = $('home-thread');
thread.getBoundingClientRect = () => ({ top: 0, bottom: 500, height: 500, left: 0, right: 400, width: 400 });
card().getBoundingClientRect = () => ({ top: 100, bottom: 400, height: 300, left: 0, right: 400, width: 400 });
thread.dispatchEvent(new Event('scroll'));
const strip = () => document.querySelector('#home-chat > .home-plan-strip');
assert.ok(!strip() || strip().hidden, 'la scheda si vede: niente striscia');
card().getBoundingClientRect = () => ({ top: -400, bottom: -20, height: 380, left: 0, right: 400, width: 400 });
thread.dispatchEvent(new Event('scroll'));
assert.equal(strip().hidden, false);
assert.match(text(strip()), /Step 2 · Search fig/);
rpcAnswers['plan.pause'] = () => ({ ok: true });
strip().querySelector('.home-plan-strip-btn').click();
await tick(30);
assert.equal(rpcs('plan.pause').length, 1);
""")


def test_the_full_plan_is_a_read_only_sheet() -> None:
    run_home(_HEAD + """
current = plan();
await toNotebook();
rpcAnswers['plan.full'] = () => ({ ok: true, title: 'Rebuild the wiki', steps: current.steps });
card().querySelector('.home-plan-open').click();
await tick(30);
assert.ok(sheet().open);
assert.match(text(sheet()), /detail of Search maple/);
assert.equal(sheet().querySelector('textarea, [contenteditable]'), null, 'si legge e basta');
""")


def test_approve_waits_for_the_turn_and_retries_once_after_busy() -> None:
    run_home(_HEAD + """
current = plan();
await toNotebook();
frame({ event: 'goal_status', status: 'running' });
await tick(10);
assert.equal(action('approve').disabled, true, 'mentre Jenny scrive il piano, Approva aspetta');
frame({ event: 'goal_status', status: 'idle' });
await tick(10);
assert.equal(action('approve').disabled, false);
let calls = 0;
rpcAnswers['plan.approve'] = () => (++calls === 1 ? rpcFailed('busy', 'turn') : { ok: true });
action('approve').click();
await tick(30);
assert.ok(toasts().includes(P.waitTurn), toasts());
assert.equal(calls, 1);
frame({ event: 'goal_status', status: 'running' });
frame({ event: 'goal_status', status: 'idle' });
await tick(40);
assert.equal(calls, 2, 'a fine turno si rifa una volta');
assert.deepEqual(rpcs('plan.approve')[1].params, rpcs('plan.approve')[0].params);
frame({ event: 'goal_status', status: 'running' });
frame({ event: 'goal_status', status: 'idle' });
await tick(40);
assert.equal(calls, 2, 'una volta sola');
""")


def test_stop_and_go_back_says_when_the_rollback_was_refused() -> None:
    run_home(_HEAD + """
current = plan({ status: 'running', done: 1, steps: [step('s1', 'A', { status: 'done' }),
  step('s2', 'B', { status: 'now' }), step('s3', 'C')] });
await toNotebook();
rpcAnswers['plan.stop'] = () => {
  current = { ...current, status: 'stopped' };
  return { ok: true, rollback_error: { code: 'conflict', message: 'changed' } };
};
action('stop').click();
row('back').click();
await tick(40);
assert.ok(card().classList.contains('is-stopped'));
assert.equal(text(card().querySelector('.home-plan-note.is-error')), P.stopRollbackConflict);
""")


def test_a_failed_read_keeps_the_running_plan_and_retries() -> None:
    run_home(_HEAD + """
app.plan.retryDelay = 0;
current = plan({ status: 'running', done: 1, current_step_id: 's2', steps: [
  step('s1', 'A', { status: 'done' }), step('s2', 'B', { status: 'now' }), step('s3', 'C')] });
open = [{ id: 'p1', notebook: 'orto', status: 'running', title: 'x', done: 1, total: 3 }];
await toNotebook();
assert.ok(card());
assert.equal($('home-input').disabled, true);
rpcAnswers['plan.get'] = () => rpcFailed('unavailable', 'x');
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
// Non sapere com'e' il piano non vuol dire che non c'e'.
assert.ok(card(), 'la scheda resta');
assert.equal($('home-input').disabled, true, 'la chat resta spenta');
assert.equal(document.querySelector('.home-plan-toggle').hidden, true, 'niente tasto sopra un piano aperto');
// Si rilegge al frame dopo.
current = { ...current, status: 'paused' };
rpcAnswers['plan.get'] = () => ({ ok: true, plan: current });
const before = rpcs('plan.get').length;
frame({ event: 'goal_status', status: 'idle' });
await tick(40);
assert.ok(rpcs('plan.get').length > before, 'la lettura fallita si rifa');
assert.ok(card().classList.contains('is-paused'));
assert.equal($('home-input').disabled, false);
// Entrando in un quaderno la cui lettura fallisce, l'elenco fa da riserva:
// la chat non si riaccende sotto un piano che corre.
await app.switchConversation('unified:default');
await tick(30);
current = { ...current, status: 'running' };
rpcAnswers['plan.get'] = () => rpcFailed('unavailable', 'x');
await toNotebook();
assert.equal($('home-input').disabled, true);
assert.equal(document.querySelector('.home-plan-toggle').hidden, true);
""")


def test_a_failed_list_retries_and_releases_the_lock() -> None:
    run_home(_HEAD + """
app.plan.retryDelay = 20;
routes['/api/board'] = { exists: true, cards: [], notes: [], postits: [], connections: [],
  suggestions: [], since: [], offboard: 0, pages: 0, lastSeen: '', pageOwners: {}, full: false };
rpcAnswers['board.seen'] = () => ({ ok: true });
open = [{ id: 'p1', notebook: 'orto', status: 'running', title: 'x', done: 1, total: 3 }];
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await toNotebook();
assert.equal($('home-board-lock').hidden, false);
// L'ultimo `plan_changed` (il piano e' finito), e la rilettura dell'elenco fallisce.
open = [];
// Due giri falliti: quello del frame e quello che segue la rilettura del piano.
let fails = 2;
rpcAnswers['plan.list'] = () => (fails-- > 0 ? rpcFailed('unavailable', 'x') : { ok: true, plans: open });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(10);
assert.equal($('home-board-lock').hidden, false, 'le letture sono fallite');
await tick(80);
assert.equal($('home-board-lock').hidden, true, 'il secondo giro riapre il quaderno');
""")


def test_a_running_redraw_keeps_the_focused_button() -> None:
    run_home(_HEAD + """
current = plan({ status: 'running', done: 1, current_step_id: 's2', steps: [
  step('s1', 'A', { status: 'done' }), step('s2', 'B', { status: 'now', started_at: new Date().toISOString() }),
  step('s3', 'C')] });
await toNotebook();
const pause = action('pause');
pause.focus();
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
frame({ event: 'goal_status', status: 'idle' });
await tick(40);
assert.equal(action('pause'), pause, 'lo stesso bottone');
assert.equal(document.activeElement, pause, 'il fuoco resta');
// Un passo dopo cambia la lista, ma non i bottoni.
current = { ...current, done: 2, current_step_id: 's3', steps: [current.steps[0],
  { ...current.steps[1], status: 'done' }, { ...current.steps[2], status: 'now' }] };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.ok(card().querySelector('[data-step-id="s2"]').classList.contains('is-done'));
assert.equal(document.activeElement, action('pause'));
""")


def test_the_hint_row_is_measured_with_the_composer() -> None:
    head = _HEAD.replace("const app = await boot();", """
const observed = [];
window.ResizeObserver = globalThis.ResizeObserver = class {
  constructor(cb) { this.cb = cb; }
  observe(node) { observed.push(node); }
  disconnect() {}
};
const app = await boot();
""")
    run_home(head + """
const hint = document.querySelector('.home-plan-hint');
assert.ok(hint);
assert.ok(observed.includes(hint), 'la riga del piano sposta il fondo del filo: va osservata');
""")


def test_the_sheets_close_from_the_x_and_the_backdrop() -> None:
    run_home(_HEAD + """
current = plan();
await toNotebook();
rpcAnswers['plan.full'] = () => ({ ok: true, title: 'Rebuild the wiki', steps: current.steps });
card().querySelector('.home-plan-open').click();
await tick(30);
assert.ok(sheet().open);
row('sheet-close').click();
assert.equal(sheet().open, false, 'la X chiude');
card().querySelector('.home-plan-open').click();
await tick(30);
assert.ok(sheet().open);
// Il tocco sintetico subito dopo l'apertura non chiude (come gli altri fogli).
sheet().dispatchEvent(new MouseEvent('click', { bubbles: true }));
assert.ok(sheet().open);
app.plan._sheetOpenedAt = 0;
sheet().querySelector('.home-plan-full').dispatchEvent(new MouseEvent('click', { bubbles: true }));
assert.ok(sheet().open, 'un tocco dentro il foglio non lo chiude');
sheet().dispatchEvent(new MouseEvent('click', { bubbles: true }));
assert.equal(sheet().open, false, 'il velo chiude');
""")


def test_a_rollback_stopped_halfway_says_so_and_offers_to_retry() -> None:
    run_home(_HEAD + """
current = plan({ status: 'done', done: 3, can_rollback: true,
  rollback_failed: { at: '2026-10-23T12:00:00Z', kind: 'rollback', error: 'disk', applied: 3, total: 8 },
  steps: [step('s1', 'A', { status: 'done' }), step('s2', 'B', { status: 'done' }),
    step('s3', 'C', { status: 'done' })] });
rpcAnswers['plan.changes'] = () => ({ ok: true, added: [], changed: [], removed: [], groups: {} });
await toNotebook();
const half = card().querySelector('.home-plan-half');
assert.ok(half, 'la scheda dice che il quaderno e\\' a meta\\'');
assert.equal(text(half), P.rollbackHalf.replace('{applied}', '3').replace('{total}', '8'));
assert.equal(text(action('rollback')), P.rollbackRetry);
assert.equal(card().querySelector('.home-plan-close'), null, 'non si toglie finche\\' e\\' a meta\\'');
// Riprovare passa dal conto, e un nuovo arresto a meta' ha le sue parole.
rpcAnswers['plan.rollback_preview'] = () => ({ ok: true, expect_end: 'C1', report: {
  restored: ['wiki/a.md'], removed: [], left: [], kept: [], already: [], excluded: [], counts: {} } });
rpcAnswers['plan.rollback'] = () => rpcFailed('rollback_failed', 'stopped after 4 of 8');
action('rollback').click();
await tick(30);
row('confirm').click();
await tick(40);
assert.ok(toasts().includes(P.error.rollbackFailed), toasts());
// L'annullamento a meta', idem; e un piano piu' nuovo che tiene il quaderno.
current = plan({ status: 'rolled_back', can_undo_rollback: true,
  rollback_failed: { at: '2026-10-23T12:00:00Z', kind: 'undo', error: 'disk' },
  steps: [step('s1', 'A', { status: 'done' })] });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(text(card().querySelector('.home-plan-half')), P.undoHalfNoCount);
assert.equal(text(action('undo')), P.undoRetry);
rpcAnswers['plan.undo_rollback'] = () => rpcFailed('plan_running', 'held');
action('undo').click();
await tick(40);
assert.ok(toasts().includes(P.error.planRunning), toasts());
""")


def test_the_strip_resume_waits_for_the_turn() -> None:
    run_home(_HEAD + """
current = plan({ status: 'paused', done: 1, steps: [step('s1', 'A', { status: 'done' }),
  step('s2', 'B'), step('s3', 'C')] });
await toNotebook();
const thread = $('home-thread');
thread.getBoundingClientRect = () => ({ top: 0, bottom: 500, height: 500, left: 0, right: 400, width: 400 });
card().getBoundingClientRect = () => ({ top: -400, bottom: -20, height: 380, left: 0, right: 400, width: 400 });
thread.dispatchEvent(new Event('scroll'));
const btn = () => document.querySelector('#home-chat > .home-plan-strip .home-plan-strip-btn');
frame({ event: 'goal_status', status: 'running' });
await tick(10);
assert.equal(btn().disabled, true, 'mentre Jenny parla, Riprendi aspetta');
frame({ event: 'goal_status', status: 'idle' });
await tick(10);
assert.equal(btn().disabled, false);
let calls = 0;
rpcAnswers['plan.resume'] = () => (++calls === 1 ? rpcFailed('busy', 'turn') : { ok: true });
btn().click();
await tick(30);
assert.ok(toasts().includes(P.waitTurn), toasts());
frame({ event: 'goal_status', status: 'running' });
frame({ event: 'goal_status', status: 'idle' });
await tick(40);
assert.equal(calls, 2, 'il busy si rifa a fine turno, come dalla scheda');
""")


def test_the_drawer_is_read_only_while_a_plan_holds_the_notebook() -> None:
    run_home(_HEAD + """
routes['/api/board'] = { exists: true, cards: [{ id: 'acero', title: 'Acero', glyph: '',
  pos: { x: 5, y: 6, by: 'user' }, pinned: false, pages: 1, signal: null }], notes: [], postits: [],
  connections: [], suggestions: [], since: [], offboard: 0, pages: 1, lastSeen: '', pageOwners: {}, full: false };
routes['/api/board/card'] = { id: 'acero', title: 'Acero', glyph: 'leaf',
  pages: [{ path: 'a.md', title: 'a' }], generated: '2026-10-04T04:12:00+02:00', brief: 'Fine.',
  sections: [{ title: 'Pot', text: 'Pot 20.', fresh: false,
    sources: [{ path: 'a.md', title: 'a', exists: true }] }],
  reminders: [{ id: 'r1', card: 'acero', text: 'Repot', when: { kind: 'date', date: '2027-03-01', precision: 'month' } }],
  userSections: [], connections: [{ to: 'fig', title: 'Fig', label: 'same pot', by: 'jenny' }],
  corrections: [], skeleton: ['Pot'] };
rpcAnswers['board.seen'] = () => ({ ok: true });
open = [{ id: 'p1', notebook: 'orto', status: 'running', title: 'x', done: 1, total: 3 }];
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await toNotebook();
await app.openCard('acero', 'Acero');
await tick(40);
assert.equal(app.view, 'drawer');
const drawer = $('home-board-drawer');
assert.match(text(drawer), new RegExp(P.lock), 'il cassetto dice che e\\' in sola lettura');
document.querySelector('.home-drawer-fold').click();
const fix = drawer.querySelector('.home-drawer-link.is-fix');
assert.ok(fix && fix.disabled, 'Correggi e\\' spento');
assert.ok(drawer.querySelector('.home-drawer-link.is-cut').disabled, 'il filo non si taglia');
assert.ok(drawer.querySelector('.home-drawer-cal-done').disabled, 'Fatto e\\' spento');
assert.ok([...drawer.querySelectorAll('.home-drawer-own .home-drawer-link')].every((b) => b.disabled));
assert.equal(drawer.querySelector('.home-drawer-chip').disabled, false, 'le pagine si aprono');
assert.equal(document.querySelector('.home-drawer-fab').hidden, true, 'il + sparisce');
// Finito il piano, il cassetto si riapre.
open = [];
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(drawer.querySelector('.home-drawer-link.is-fix').disabled, false);
assert.equal(document.querySelector('.home-drawer-fab').hidden, false);
""")


def test_a_rollback_conflict_counts_again() -> None:
    run_home(_HEAD + """
current = plan({ status: 'done', done: 2, can_rollback: true,
  steps: [step('s1', 'A', { status: 'done' }), step('s2', 'B', { status: 'done' })] });
rpcAnswers['plan.changes'] = () => ({ ok: true, added: [], changed: [], removed: [], groups: {} });
let previews = 0;
rpcAnswers['plan.rollback_preview'] = () => {
  previews += 1;
  return { ok: true, expect_end: `P${previews}`, report: {
    restored: previews === 1 ? ['wiki/a.md', 'wiki/b.md'] : ['wiki/a.md'], removed: [], left: [],
    kept: [], already: [], excluded: [], counts: {} } };
};
rpcAnswers['plan.rollback'] = (p) => (p.expect_end === 'P1' ? rpcFailed('conflict', 'changed') : { ok: true, report: { counts: { restored: 1 } } });
await toNotebook();
action('rollback').click();
await tick(30);
assert.match(text(sheet()), /2 files come back/);
row('confirm').click();
await tick(60);
// Il quaderno e' cambiato dopo il conto: si rifa' il conto e si rimostra.
assert.ok(toasts().includes(P.error.conflict), toasts());
assert.equal(previews, 2, 'un conto nuovo');
assert.ok(sheet().open, 'il foglio torna coi numeri nuovi');
assert.match(text(sheet()), /1 files come back/);
row('confirm').click();
await tick(40);
assert.equal(rpcs('plan.rollback').at(-1).params.expect_end, 'P2', 'si conferma il conto nuovo');
""")
