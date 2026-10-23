"""Il piano a lista nella casa vera: tasto, scheda, fogli, lucchetto, bollino.

Il piano lo muove il codice del gateway; la casa ne mostra lo stato e manda i
tocchi dell'utente come RPC ``plan.*``. Qui si fissa il contratto fra le due
parti, col gateway finto: che il tasto Piano viaggi nel messaggio e resti
acceso finche' il gateway pianifica, che Approva mandi la versione vista e i
passi tolti,
che un ``conflict`` rilegga invece di insistere, che in corsa la chat sia
spenta e il quaderno chiuso, e che il ritorno indietro mandi i gruppi tenuti.
"""

from __future__ import annotations

from pathlib import Path

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
/* La pianificazione del gateway (`plan.get` → `drafting`): c'e' anche senza un piano. */
let drafting = null;
const DRAFTING = { since: '2026-10-23T10:00:00Z', revise: null, reads: 0 };
rpcAnswers['plan.get'] = () => ({ ok: true, plan: current, drafting });
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
// Partito il messaggio il tasto resta acceso: la pianificazione e' una
// conversazione, e finisce con la proposta.
assert.equal(toggle.getAttribute('aria-pressed'), 'true', 'dopo l\\'invio il tasto resta acceso');
// Una rilettura prima che il gateway cominci («niente») non lo spegne.
frame({ event: 'plan_changed', notebook: 'orto', plan_id: '' });
await tick(40);
assert.equal(toggle.getAttribute('aria-pressed'), 'true');
// Il gateway pianifica: il campo dice a chi rispondi, e c'e' l'uscita.
drafting = { ...DRAFTING };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: '' });
await tick(40);
assert.equal($('home-input').placeholder, P.placeholderDrafting);
assert.match(text(document.querySelector('.home-plan-hint')), /touches nothing/, 'la riga resta');
assert.equal(text(document.querySelector('[data-plan-action="cancel-drafting"]')), P.cancelDrafting);
assert.equal(toggle.getAttribute('aria-label'), P.toggleDrafting);
// Jenny fa una domanda, tu rispondi: e' ancora pianificazione.
$('home-input').value = 'keep the camellia';
$('home-send').click();
assert.equal(messages().at(-1).plan, true, 'la risposta resta in sola lettura');
assert.equal(toggle.getAttribute('aria-pressed'), 'true');
// La proposta chiude la pianificazione: il tasto si spegne da solo.
drafting = null;
current = plan();
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(toggle.getAttribute('aria-pressed'), 'false', 'finita la pianificazione il tasto e\\' spento');
assert.equal(document.querySelector('.home-field').classList.contains('is-plan'), false);
assert.notEqual($('home-input').placeholder, P.placeholder);
assert.notEqual($('home-input').placeholder, P.placeholderDrafting);
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
// Spento subito dopo l'invio, prima che arrivi il `plan_changed`: il gateway
// forse pianifica gia', quindi glielo si dice (e la rilettura non lo riaccende).
rpcAnswers['plan.cancel_drafting'] = () => ({ ok: true, cancelled: false });
toggle.click();
await tick(40);
assert.equal(rpcs('plan.cancel_drafting').length, 1);
assert.equal(toggle.getAttribute('aria-pressed'), 'false');
// Acceso e rispento senza aver mandato niente: non c'e' niente da annullare.
toggle.click();
toggle.click();
assert.equal(rpcs('plan.cancel_drafting').length, 1);
// Cambiando conversazione il tasto torna spento.
toggle.click();
await app.switchConversation('unified:default');
await tick(30);
await toNotebook();
assert.equal(document.querySelector('.home-field').classList.contains('is-plan'), false);
""")


def test_drafting_is_cancelled_from_the_hint_or_the_toggle() -> None:
    run_home(_HEAD + """
// Rientrando in un quaderno dove Jenny pianifica (o dopo un riavvio), il
// tasto si riaccende da solo: lo stato e' del gateway.
drafting = { ...DRAFTING, reads: 3 };
await toNotebook();
const toggle = document.querySelector('.home-plan-toggle');
assert.equal(toggle.getAttribute('aria-pressed'), 'true');
assert.equal($('home-input').placeholder, P.placeholderDrafting);
rpcAnswers['plan.cancel_drafting'] = () => { drafting = null; return { ok: true, cancelled: true }; };
document.querySelector('[data-plan-action="cancel-drafting"]').click();
await tick(40);
assert.deepEqual(rpcs('plan.cancel_drafting')[0].params, { notebook: 'orto' });
assert.equal(toggle.getAttribute('aria-pressed'), 'false');
assert.equal(document.querySelector('.home-plan-hint').hidden, true);
// Spegnere il tasto mentre pianifica e' annullare.
drafting = { ...DRAFTING };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: '' });
await tick(40);
assert.equal(toggle.getAttribute('aria-pressed'), 'true');
toggle.click();
await tick(40);
assert.equal(rpcs('plan.cancel_drafting').length, 2);
assert.equal(toggle.getAttribute('aria-pressed'), 'false');
// Un annullamento rifiutato lo dice, e il tasto resta com'e' il gateway.
drafting = { ...DRAFTING };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: '' });
await tick(40);
rpcAnswers['plan.cancel_drafting'] = () => rpcFailed('unavailable', 'x');
toggle.click();
await tick(40);
assert.ok(toasts().includes(P.error.failed), toasts());
assert.equal(toggle.getAttribute('aria-pressed'), 'true');
""")


def test_a_proposal_ends_planning_even_if_its_start_was_never_seen() -> None:
    run_home(_HEAD + """
await toNotebook();
const toggle = document.querySelector('.home-plan-toggle');
toggle.click();
$('home-input').value = 'rebuild the wiki';
$('home-send').click();
// L'inizio della pianificazione si perde: arriva direttamente la proposta.
current = plan();
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(toggle.getAttribute('aria-pressed'), 'false', 'una proposta nuova chiude il tasto');
// Riacceso sopra quella proposta, la stessa proposta non lo spegne.
toggle.click();
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(toggle.getAttribute('aria-pressed'), 'true');
// Una versione nuova si'.
current = plan({ version: 2 });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(toggle.getAttribute('aria-pressed'), 'false');
""")


def test_revise_during_a_plain_drafting_still_reaches_the_gateway() -> None:
    run_home(_HEAD + """
current = plan();
drafting = { ...DRAFTING };
await toNotebook();
// Jenny pianifica senza revisione, e tu tocchi Rivedi sulla proposta di prima.
action('revise').click();
$('home-input').value = 'drop step 2';
$('home-send').click();
assert.equal(messages().at(-1).plan, true);
assert.equal(messages().at(-1).plan_revise, 'p1', 'la revisione va al gateway che non la sa');
drafting = { ...DRAFTING, revise: 'p1' };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
$('home-input').value = 'and keep the photos';
$('home-send').click();
assert.equal(messages().at(-1).plan, true);
assert.equal(messages().at(-1).plan_revise, undefined, 'quando la sa, non si ripete');
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
// Il gateway rivede: la scheda resta in revisione, e la revisione la ricorda
// lui, quindi il messaggio dopo non la ripete.
drafting = { ...DRAFTING, revise: 'p1' };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.ok(card().classList.contains('is-review'), 'in revisione finche\\' Jenny rivede');
$('home-input').value = 'and one more thing';
$('home-send').click();
assert.equal(messages().at(-1).plan, true);
assert.equal(messages().at(-1).plan_revise, undefined);
// La versione nuova chiude la pianificazione.
drafting = null;
current = plan({ version: 2 });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(card().classList.contains('is-review'), false);
assert.ok(action('approve'));
$('home-input').value = 'thanks';
$('home-send').click();
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


def test_a_proposal_shows_its_coverage_and_its_assumptions() -> None:
    run_home(_HEAD + """
current = plan({
  coverage: [
    { item: 'Acero', have: '3 notes', missing: '', strategy: 'notebook', why: '' },
    { item: 'Ginkgo', have: 'a page', missing: '', strategy: 'notebook', why: '' },
    { item: 'Fico', have: 'raw notes', missing: '', strategy: 'notebook', why: '' },
    { item: 'Pino', have: 'a page', missing: '', strategy: 'notebook', why: '' },
    { item: 'Glicine', have: '', missing: 'substrate mix', strategy: 'web', why: 'raw has nothing' },
    { item: 'Camelia', have: '', missing: 'everything', strategy: 'leave', why: 'not your plant' },
  ],
  assumptions: ['Old concept pages go', 'Numbers only with a source'],
});
await toNotebook();
// Sotto il titolo: cosa so gia', cosa vado a cercare, cosa resta scoperto.
assert.ok(card().children[1].classList.contains('home-plan-cover'), 'la copertura sta sotto il titolo');
const line = (s) => text(card().querySelector(`.home-plan-cover-line.is-${s}`));
assert.equal(line('notebook'), `${P.cover.have.replace('{count}', '4')} Acero, Ginkgo, Fico ${P.cover.more.replace('{count}', '1')}`);
assert.equal(line('web'), `${P.cover.search.replace('{count}', '1')} Glicine`);
assert.match(line('leave'), /Camelia/);
// Il resto voce per voce, a richiesta.
assert.equal(card().querySelector('.home-plan-cover-list'), null, 'le voci partono chiuse');
action('fold-cover-more').click();
assert.match(text(card().querySelector('.home-plan-cover-list')), /substrate mix/);
assert.match(text(card().querySelector('.home-plan-cover-list')), /not your plant/);
// «Decido io»: chiuso di default, e apribile.
const fold = action('fold-assume-more');
assert.equal(text(fold), P.assume.title.replace('{count}', '2'));
assert.equal(fold.getAttribute('aria-expanded'), 'false');
assert.equal(card().querySelector('.home-plan-assume-list'), null);
fold.click();
assert.deepEqual([...card().querySelectorAll('.home-plan-assume-list li')].map(text),
  ['Old concept pages go', 'Numbers only with a source']);
// Un ridisegno non richiude quel che hai aperto.
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.ok(card().querySelector('.home-plan-assume-list'));
// Un piano vecchio, senza copertura, non ha il blocco.
current = plan({ id: 'p0' });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p0' });
await tick(40);
assert.equal(card().querySelector('.home-plan-cover'), null);
assert.equal(card().querySelector('.home-plan-assume'), null);
""")


def test_the_full_plan_renders_markdown_safely_with_coverage_and_done_when() -> None:
    vendor = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "vendor"
    boot = f"""
import {{ createRequire }} from 'node:module';
const require = createRequire(import.meta.url);
globalThis.DOMPurify = require({str(vendor / "dompurify@3" / "purify.min.js")!r})(window);
window.DOMPurify = globalThis.DOMPurify;
globalThis.marked = require({str(vendor / "marked@15.0.7" / "marked.min.js")!r});
window.marked = globalThis.marked;
"""
    run_home(boot + _HEAD + """
current = plan();
await toNotebook();
rpcAnswers['plan.full'] = () => ({ ok: true, title: 'Rebuild the wiki',
  coverage: [{ item: 'Glicine', have: '', missing: 'the `substrate` mix', strategy: 'web', why: 'raw is empty' }],
  assumptions: ['Delete `concepts/` pages'],
  steps: [step('s1', 'Search maple', {
    detail: 'Read `raw/acero.md` first.\\n\\n<img src=x onerror="globalThis.pwned=1"> and **bold**',
    done_when: 'every page has **3** sourced facts, see [the rules](workshop.html) and [here](#nowhere)' })] });
card().querySelector('.home-plan-open').click();
await tick(40);
const s = sheet();
assert.ok(s.open);
// Il markdown e' reso: i backtick diventano codice, non restano crudi.
const detail = s.querySelector('.home-plan-full-detail');
assert.equal(detail.querySelector('code')?.textContent, 'raw/acero.md');
assert.ok(!text(detail).includes('`'), 'niente backtick crudi');
assert.equal(detail.querySelector('strong')?.textContent, 'bold');
// ...e sanificato: l'HTML del modello non passa.
assert.equal(s.querySelector('[onerror]'), null, 'nessun attributo evento');
assert.equal(globalThis.pwned, undefined);
// Il «fatto quando» di ogni passo, la copertura e le assunzioni.
const done = s.querySelector('.home-plan-full-done');
assert.match(text(done), new RegExp(P.fullSheet.doneWhen));
assert.equal(done.querySelector('strong')?.textContent, '3');
assert.match(text(s), new RegExp(P.fullSheet.coverage));
assert.equal(s.querySelector('.home-plan-full-cover code')?.textContent, 'substrate');
assert.match(text(s), new RegExp(P.fullSheet.assumptions));
assert.equal(s.querySelector('.home-plan-full-assume code')?.textContent, 'concepts/');
// Un link del modello non naviga la WebView: si instrada come in chat.
const link = s.querySelector('.home-plan-full-done a[href="workshop.html"]');
assert.ok(link, 'il link e\\' reso');
const tap = new MouseEvent('click', { bubbles: true, cancelable: true });
link.dispatchEvent(tap);
assert.equal(tap.defaultPrevented, true, 'niente navigazione del frame');
assert.ok(toasts().includes(locales.en.common.linkNotOpenable), toasts());
const hashBefore = location.hash;
const hashTap = new MouseEvent('click', { bubbles: true, cancelable: true });
s.querySelector('a[href="#nowhere"]').dispatchEvent(hashTap);
assert.equal(hashTap.defaultPrevented, true, 'un #ancora non riscrive l\\'hash del guscio');
assert.equal(location.hash, hashBefore);
""")


_DRAFTS = """
const S = locales.en.home.staged;
const draftItem = (over = {}) => ({ id: 'd1', target: 'wikis/orto/AGENTS.md',
  sources: ['web_fetch:a.example', 'file:wikis/orto/.jenny/tool-results/call_1.txt',
    'web_search:b', 'web_fetch:c.example', 'web_fetch:d.example'],
  created_ms: 1, new_file: false, target_sha256: 'abc', diff: '--- a\\n+++ b\\n+one folder per plant\\n',
  truncated: false, ...over });
let pending = [draftItem()];
rpcAnswers['staged.list'] = () => ({ ok: true, staged: pending });
const blockedOnDrafts = (drafts = [{ id: 'd1', target: 'wikis/orto/AGENTS.md' }]) => plan({
  status: 'blocked', done: 1, current_step_id: 's2',
  blocked: { step: 's2', question: 'FALLBACK the change waits', reason: 'drafts',
    at: '2026-10-23T11:25:00Z', kind: 'drafts', drafts },
  steps: [step('s1', 'Rules', { status: 'done' }), step('s2', 'Write', { kind: 'write', status: 'blocked' }),
    step('s3', 'Check', { kind: 'check' })] });
const stagedCard = () => document.querySelector('#home-thread > .home-live > .home-staged');
"""


def test_a_step_blocked_on_drafts_shows_them_in_the_plan_card() -> None:
    run_home(_HEAD + _DRAFTS + """
current = blockedOnDrafts();
await toNotebook();
await tick(40);
const box = card().querySelector('.home-plan-drafts');
assert.ok(box, 'le bozze stanno nella scheda del piano');
assert.equal(text(box.querySelector('.home-plan-question')), P.drafts.questionOne.replace('{file}', 'AGENTS.md'));
assert.ok(!text(card()).includes('FALLBACK'), 'la domanda inglese del gateway e\\'  solo un ripiego');
assert.equal(card().querySelector('.home-plan-answer'), null, 'non e\\' una domanda da scrivere');
assert.equal(text(card().querySelector('.home-plan-note')), P.drafts.note);
// Le fonti: le prime tre, senza i file interni, e il resto contato.
const why = text(box.querySelector('.home-plan-draft-why'));
assert.match(why, /a\\.example, web_search:b, web_fetch:c\\.example and 1 more/);
assert.ok(!why.includes('tool-results'));
// La scheda delle bozze non la ripete mentre questa e' a schermo.
assert.equal(stagedCard(), null);
// La diff, a richiesta.
action('draft-diff-d1').click();
assert.match(card().querySelector('.home-staged-diff').textContent, /one folder per plant/);
// Installa: lo sha mostrato, e niente RPC del piano.
rpcAnswers['staged.install'] = () => { pending = []; return { ok: true }; };
action('draft-install-d1').click();
await tick(40);
assert.deepEqual(rpcs('staged.install')[0].params, { id: 'd1', target_sha256: 'abc' });
assert.ok(toasts().includes(S.installed.replace('{path}', 'AGENTS.md')), toasts());
assert.equal(rpcs('plan.answer').length, 0);
// Il gateway chiude il passo e riparte da solo.
current = plan({ status: 'running', done: 2, current_step_id: 's3', steps: [step('s1', 'Rules', { status: 'done' }),
  step('s2', 'Write', { kind: 'write', status: 'done' }), step('s3', 'Check', { kind: 'check', status: 'now' })] });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.ok(card().classList.contains('is-running'));
assert.equal(card().querySelector('.home-plan-drafts'), null);
""")


def test_a_draft_conflict_says_so_and_the_other_chats_still_see_the_draft() -> None:
    run_home(_HEAD + _DRAFTS + """
current = blockedOnDrafts();
await toNotebook();
await tick(40);
const lists = rpcs('staged.list').length;
rpcAnswers['staged.install'] = () => { pending = [draftItem({ target_sha256: 'new', diff: '+changed again' })]; return rpcFailed('conflict', 'x'); };
action('draft-install-d1').click();
await tick(60);
assert.ok(toasts().includes(S.conflict), toasts());
assert.ok(rpcs('staged.list').length > lists, 'si rilegge la diff nuova');
assert.ok(card().querySelector('.home-plan-drafts'), 'la bozza resta da decidere');
assert.equal(action('draft-install-d1').dataset.sha, 'new', 'il bottone porta lo sha nuovo');
// Scarta.
rpcAnswers['staged.discard'] = () => { pending = []; return { ok: true }; };
action('draft-discard-d1').click();
await tick(40);
assert.deepEqual(rpcs('staged.discard')[0].params, { id: 'd1' });
// Una bozza che l'elenco non ha piu': gia' decisa.
assert.equal(text(card().querySelector('.home-plan-draft-why')), P.drafts.gone);
// Fuori dalla chat del quaderno la scheda del piano non c'e': la bozza torna
// nella scheda delle bozze, dove si puo' decidere lo stesso.
pending = [draftItem()];
await app.staged.refresh();
await app.switchConversation('unified:default');
await tick(40);
assert.ok(stagedCard(), 'nelle altre chat la bozza resta visibile');
assert.match(text(stagedCard()), /AGENTS\\.md/);
""")


def test_a_failed_drafts_read_offers_a_retry_instead_of_already_decided() -> None:
    run_home(_HEAD + _DRAFTS + """
rpcAnswers['staged.list'] = () => rpcFailed('unavailable', 'x');
current = blockedOnDrafts();
await toNotebook();
await tick(60);
const why = () => text(card().querySelector('.home-plan-draft-why'));
assert.equal(why(), P.drafts.failed, 'una lettura fallita non e\\' «gia\\' decisa»');
const asked = rpcs('staged.list').length;
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(60);
assert.equal(rpcs('staged.list').length, asked, 'niente riletture a vuoto a ogni ridisegno');
rpcAnswers['staged.list'] = () => ({ ok: true, staged: pending });
action('draft-retry-d1').click();
await tick(60);
assert.ok(action('draft-install-d1'), 'riletta, la bozza si decide');
""")


def test_a_running_plan_between_steps_says_what_it_is_doing() -> None:
    run_home(_HEAD + """
current = plan({ status: 'running', done: 1, current_step_id: null, steps: [
  step('s1', 'A', { status: 'done' }), step('s2', 'B'), step('s3', 'C')] });
await toNotebook();
assert.equal(text(card().querySelector('.home-plan-closing')), P.closingStep);
current = { ...current, done: 3, steps: current.steps.map((x) => ({ ...x, status: 'done' })) };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(text(card().querySelector('.home-plan-closing')), P.closingPlan);
current = { ...current, done: 0, steps: current.steps.map((x) => ({ ...x, status: 'pending' })) };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(text(card().querySelector('.home-plan-closing')), P.startingPlan);
// Con un passo in corso, niente riga.
current = { ...current, current_step_id: 's1', steps: [{ ...current.steps[0], status: 'now' }, ...current.steps.slice(1)] };
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(card().querySelector('.home-plan-closing'), null);
""")


def test_the_plan_sheets_close_when_the_chat_goes() -> None:
    run_home(_HEAD + """
current = plan();
rpcAnswers['plan.full'] = () => ({ ok: true, title: 'Rebuild the wiki', steps: current.steps });
await toNotebook();
card().querySelector('.home-plan-open').click();
await tick(30);
assert.ok(sheet().open);
await app.switchConversation('unified:default');
await tick(30);
assert.equal(sheet().open, false, 'cambiata conversazione, il foglio del piano di prima si chiude');
// La chat sopra la pagina che scende si porta via il foglio.
assert.ok(app.openChatOver({ key: 'project:orto' }));
await tick(60);
card().querySelector('.home-plan-open').click();
await tick(30);
assert.ok(sheet().open);
app.closeChatOver();
assert.equal(sheet().open, false, 'chiusa la chat, il foglio non resta sopra la bacheca');
""")


def test_the_changes_sheet_names_pages_by_their_path_in_the_group() -> None:
    run_home(_HEAD + """
current = plan({ status: 'done', done: 3, steps: [step('s1', 'A', { status: 'done' })] });
rpcAnswers['plan.changes'] = () => ({ ok: true, groups: {},
  added: ['wiki/entities/Acero/substrato.md', 'wiki/entities/Fico/substrato.md', 'raw/research/2026-10-23-acero.md'],
  changed: ['wiki/index.md', 'wiki/entities/Acero/index.md'], removed: [] });
await toNotebook();
await tick(30);
action('changes').click();
await tick(30);
const added = text(sheet().querySelector('[data-kind="added"]'));
assert.match(added, /entities\\/Acero\\/substrato, entities\\/Fico\\/substrato/);
const changed = text(sheet().querySelector('[data-kind="changed"]'));
assert.match(changed, /index, entities\\/Acero\\/index/, 'due index si distinguono');
assert.match(text(sheet()), /research\\/2026-10-23-acero/);
""")


def test_the_thread_keeps_its_bottom_when_the_keyboard_opens() -> None:
    head = _HEAD.replace("const app = await boot();", """
const observed = [];
const observers = [];
window.ResizeObserver = globalThis.ResizeObserver = class {
  constructor(cb) { this.cb = cb; observers.push(this); }
  observe(node) { observed.push(node); (this.nodes ||= []).push(node); }
  disconnect() {}
};
window.visualViewport = new window.EventTarget();
const app = await boot();
""")
    run_home(head + """
// La tastiera accorcia il filo senza toccare niente di quel che gli sta
// sotto: si osserva anche lui.
assert.ok(observed.includes($('home-thread')), 'il filo e\\' osservato');
let kept = 0;
const bottom = app.chat.scrollToBottom.bind(app.chat);
app.chat.scrollToBottom = () => { kept += 1; bottom(); };
window.visualViewport.dispatchEvent(new Event('resize'));
assert.equal(kept, 1, 'il visualViewport che cambia riaggancia il fondo');
const floor = observers.find((o) => o.nodes?.includes(document.querySelector('#home-chat .home-composer')));
floor.cb([{ target: $('home-thread') }]);
assert.equal(kept, 2, 'e cosi\\' il filo che si accorcia');
// Chi e' risalito a rileggere resta dov'e', e il «↓» non dice «nuovo».
app.chat._stick = false;
floor.cb([{ target: $('home-thread') }]);
assert.equal(kept, 2);
assert.equal(app.chat.fab.classList.contains('has-new'), false);
app.chat._stick = true;
// La riga del piano entra nel conto del «↓»: la sua altezza in un token.
await toNotebook();
assert.ok(app.openChatOver({ key: 'project:orto' }));
await tick(60);
const hint = document.querySelector('.home-plan-hint');
Object.defineProperty(hint, 'offsetHeight', { configurable: true, get: () => 34 });
document.querySelector('.home-plan-toggle').click();
floor.cb([{ target: hint }]);
assert.equal(document.documentElement.style.getPropertyValue('--home-plan-hint-h'), '34px');
document.querySelector('.home-plan-toggle').click();
floor.cb([{ target: hint }]);
assert.equal(document.documentElement.style.getPropertyValue('--home-plan-hint-h'), '0px');
""")


def test_the_composer_lets_go_of_the_keyboard_when_the_plan_turns_it_off() -> None:
    run_home(_HEAD + """
current = plan();
await toNotebook();
const input = $('home-input');
// Il tasto porta il fuoco al campo senza far scorrere la pagina.
const calls = [];
const focus = input.focus.bind(input);
input.focus = (opts) => { calls.push(opts); focus(opts); };
document.querySelector('.home-plan-toggle').click();
assert.deepEqual(calls.at(-1), { preventScroll: true });
document.querySelector('.home-plan-toggle').click();
input.focus();
assert.equal(document.activeElement, input);
// Approvato, il piano corre e il campo si spegne: prima lascia il fuoco.
current = plan({ status: 'running', done: 0, current_step_id: 's1', steps: [
  step('s1', 'A', { status: 'now' }), step('s2', 'B'), step('s3', 'C')] });
frame({ event: 'plan_changed', notebook: 'orto', plan_id: 'p1' });
await tick(40);
assert.equal(input.disabled, true);
assert.notEqual(document.activeElement, input, 'un campo spento non tiene la tastiera');
""")


def test_the_plan_toggle_is_not_copied_into_the_chat_photo() -> None:
    run_home(_HEAD + """
await toNotebook();
document.querySelector('.home-plan-toggle').click();
// La chat sale sopra la pagina: dove era resta la sua foto.
assert.ok(app.openChatOver({ key: 'project:orto' }));
await tick(60);
assert.ok(document.querySelector('.home-snapshot'), 'c\\'e\\' una foto della chat');
assert.equal(document.querySelectorAll('.home-plan-toggle').length, 1, 'un tasto solo nel documento');
assert.equal(document.querySelectorAll('.home-plan-hint').length, 1);
assert.equal(document.querySelector('.home-snapshot .is-plan'), null, 'la foto non dice «Piano acceso»');
""")
