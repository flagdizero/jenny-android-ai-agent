"""La bolla di lavoro, nella casa vera.

Quando qualcuno lavora e tu aspetti in questa chat, in fondo al filo c'e' una
bolla: il giardiniere, Dream, gli aiutanti, e per ultima Jenny che scrive. Il
05/10/2026 una passata del giardiniere ha lavorato sei minuti dietro un
«Gardening etf-finance...» immobile, e dalla casa sembrava ferma; prima ancora
il chip degli agenti diceva che qualcuno lavorava, ma piccolo e sotto la chat.

Si gira l'``index.html`` vero con ``home-app.js`` vero (``support.home_dom``):
la bolla vive fra quattro fonti — il frame ``work_status``, lo snapshot dei
subagent, le due letture HTTP all'apertura — e il filo che intanto cresce, e il
difetto possibile sta proprio fra loro.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, calls, hooks, ok, threads, thread, locales, $ } from './boot.mjs';
const W = locales.en.home.work;
/* Gli snapshot per sessione, come li servono `/api/subagents` e `/api/work`. */
const agents = {};
const works = {};
hooks.fetch = (u) => {
  const key = u.searchParams.get('session_key') || '';
  if (u.pathname === '/api/subagents') return ok(agents[key] || { running: [], recent: [] });
  if (u.pathname === '/api/work') return ok({ work: works[key] || [] });
  if (/^[/]api[/]subagents[/][^/]+[/]restart$/.test(u.pathname)) return ok({ task_id: 'new' });
  return undefined;
};
const worker = (over = {}) => ({ task_id: 't1', label: 'plant cards', state: 'running',
                                 elapsed_s: 30, idle_s: 1, last_tool: 'read_file', ...over });
const now = () => Date.now() / 1000;
const job = (over = {}) => ({ event: 'work_status', chat_id: 'project:orto', id: 'gardener-1',
  kind: 'gardener', state: 'running', manual: true, phases: ['pages', 'board'],
  phase: 'pages', writes: 0, retry: null, started_at: now() - 120, updated_at: now(), ...over });
const live = () => document.querySelector('#home-thread > .home-live');
const bubbles = () => [...live().querySelectorAll(':scope > .home-work:not(.home-typing)')];
const settled = () => [...document.querySelectorAll('#home-thread > .home-msg-work')];
const text = (node) => node.textContent.replace(/\\s+/g, ' ').trim();
"""


def test_a_gardener_pass_is_a_bubble_that_grows_in_place() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
assert.equal(live().hidden, true, 'senza nessuno al lavoro il posto vivo non occupa spazio');

frame(job());
assert.equal(live().hidden, false);
assert.equal(bubbles().length, 1);
const b = bubbles()[0];
assert.match(text(b), new RegExp(W.title.gardener));
assert.match(text(b), new RegExp(W.phase.pages));
assert.equal(b.querySelectorAll('.home-work-seg').length, 2, 'un tratto per fase');
assert.ok(b.querySelector('.home-work-seg.is-now'));

frame(job({ writes: 3 }));
assert.equal(bubbles().length, 1, 'un aggiornamento ha aperto una seconda bolla');
assert.match(text(bubbles()[0]), /3 written/);

frame(job({ phase: 'board', writes: 3 }));
const segs = [...bubbles()[0].querySelectorAll('.home-work-seg')];
assert.ok(segs[0].classList.contains('is-past') && segs[1].classList.contains('is-now'));
""")


def test_the_bubble_stays_under_the_last_thing_said() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame(job());
frame({ event: 'stream_end', chat_id: 'project:orto', turn_id: 'x', text: 'nel frattempo' });
await tick(10);
const kids = [...$('home-thread').children];
// Sotto il posto vivo c'e' solo la fascia di Jenny (`home-band.js`).
assert.ok(kids[kids.length - 1].classList.contains('home-band'), 'la fascia non e\\u2019 in fondo');
assert.equal(kids[kids.length - 2], live(), 'il posto vivo non e\\u2019 subito sopra la fascia');
assert.deepEqual(thread(), ['jenny: nel frattempo']);
""")


def test_a_finished_pass_becomes_the_report_and_offers_the_board() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
let opened = 0;
app.openBoard = () => { opened += 1; };
frame(job());
frame(job({ state: 'done', ended_at: now(), outcome: { status: 'written', writes: 7, proposals: 1 } }));
assert.equal(bubbles().length, 0, 'la bolla finita e\\u2019 rimasta nel posto vivo');
assert.equal(settled().length, 1, 'la bolla finita non e\\u2019 scesa nel filo');
const report = settled()[0];
assert.ok(report.classList.contains('is-done'));
assert.match(text(report), /7 pages updated/);
const button = report.querySelector('.home-work-action');
assert.equal(button.hidden, false);
assert.equal(button.textContent, W.openBoard);
button.click();
assert.equal(opened, 1);
""")


def test_the_text_of_a_work_already_in_a_bubble_is_not_drawn_twice() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame(job());
frame({ event: 'message', chat_id: 'project:orto', text: 'Gardening orto...', work_id: 'gardener-1' });
frame({ event: 'message', chat_id: 'project:orto', text: 'altro', work_id: 'gardener-zzz' });
await tick(10);
assert.deepEqual(thread(), ['jenny: altro'],
  'il testo di un lavoro in bolla e\\u2019 stato disegnato, o uno sconosciuto e\\u2019 sparito');
""")


def test_a_retry_says_so_in_one_line() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame(job());
assert.equal(bubbles()[0].querySelector('.home-work-slow').hidden, true);
frame(job({ retry: { attempt: 2, of: 3 } }));
const slow = bubbles()[0].querySelector('.home-work-slow');
assert.equal(slow.hidden, false);
assert.match(slow.textContent, /2 of 3/);
""")


def test_a_failed_pass_offers_to_try_again() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
const sentTexts = [];
app._sendText = (t) => { sentTexts.push(t); return true; };
frame(job());
frame(job({ state: 'failed', ended_at: now(), outcome: { status: 'failed', reason: 'model' } }));
const report = settled()[0];
assert.ok(report.classList.contains('is-failed'));
assert.match(text(report), new RegExp(W.why.model.slice(0, 20)));
report.querySelector('.home-work-action').click();
assert.deepEqual(sentTexts, ['/gardener']);
""")


def test_an_automatic_pass_says_a_message_will_pause_it() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame(job({ manual: false }));
const note = bubbles()[0].querySelector('.home-work-note');
assert.equal(note.hidden, false);
assert.equal(note.textContent, W.autoNote);
assert.match(text(bubbles()[0]), new RegExp(W.title.gardenerAuto));
""")


def test_work_and_helpers_are_read_back_when_the_conversation_opens() -> None:
    run_home(_HEAD + """
works['project:orto'] = [job()];
agents['project:orto'] = { running: [worker()], recent: [] };
const app = await boot();
assert.equal(bubbles().length, 0, 'il lavoro del quaderno e\\u2019 comparso nella chat personale');
await app.showConversation('project:orto');
await tick(20);
assert.ok(calls.includes('/api/work?session_key=project%3Aorto'), calls.join(' '));
assert.ok(calls.includes('/api/subagents?session_key=project%3Aorto'), calls.join(' '));
assert.equal(bubbles().length, 2);

await app.showConversation('websocket:default');
await tick(20);
assert.equal(bubbles().length, 0, 'le bolle del quaderno sono rimaste nella chat personale');
""")


def test_another_conversations_work_does_not_show_here() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame(job({ chat_id: 'default' }));
frame({ event: 'subagent_status', chat_id: 'default', running: [worker()], recent: [] });
assert.equal(bubbles().length, 0);
""")


def test_helpers_are_one_bubble_that_says_what_they_do() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [worker()], recent: [] });
assert.equal(bubbles().length, 1);
assert.match(text(bubbles()[0]), /plant cards/);
assert.match(text(bubbles()[0]), new RegExp(W.doing.read));

frame({ event: 'subagent_status', chat_id: 'project:orto',
        running: [worker(), worker({ task_id: 't2', label: 'other', last_tool: 'grep' })], recent: [] });
assert.equal(bubbles().length, 1, 'due aiutanti sono due bolle');
assert.equal(bubbles()[0].querySelectorAll('.home-work-row').length, 2);

app.work.doneLingerMs = 30;
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [],
        recent: [worker({ state: 'done' }), worker({ task_id: 't2', label: 'other', state: 'done' })] });
assert.equal(bubbles().length, 1, 'due mini-me tornate insieme sono una bolla sola');
assert.ok(bubbles()[0].classList.contains('is-done'), 'la bolla non dice che hanno finito');
assert.match(text(bubbles()[0]), new RegExp(W.helpersDoneMany.replace('{count}', '2')));
await tick(60);
assert.equal(bubbles().length, 0, 'la bolla «ha finito» e\\u2019 rimasta li\\u2019');
assert.equal(live().hidden, true);
""")


def test_a_mini_me_that_comes_back_says_so_by_name() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [worker()], recent: [] });
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [worker({ state: 'done' })] });
assert.equal(bubbles().length, 1);
assert.equal(text(bubbles()[0]), W.helperDone.replace('{label}', 'plant cards'));
assert.equal(settled().length, 0, '«ha finito» non resta nel filo: il resoconto lo fa Jenny');
""")


def test_a_mini_me_finished_before_the_home_opened_is_not_news() -> None:
    """Lo snapshot di una riapertura porta anche le mini-me finite da ore: solo
    quelle che questa casa ha visto lavorare, finendo, dicono «ho finito»."""
    run_home(_HEAD + """
agents['project:orto'] = { running: [], recent: [worker({ state: 'done' })] };
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [worker({ state: 'done' })] });
assert.equal(bubbles().length, 0);
""")


def test_a_stuck_helper_reads_as_slow() -> None:
    run_home(_HEAD + """
const app = await boot();
frame({ event: 'subagent_status', chat_id: 'default', running: [worker({ state: 'stalled' })], recent: [] });
assert.equal(bubbles()[0].querySelector('.home-work-slow').hidden, false);
""")


def test_a_helper_that_stopped_has_try_again() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [],
        recent: [worker({ state: 'failed', stop_reason: 'max_iterations', ended_at: now(), can_restart: true })] });
assert.equal(settled().length, 1);
const report = settled()[0];
assert.match(text(report), new RegExp(W.why.helperBudget.slice(0, 15)));
report.querySelector('.home-work-action').click();
await tick(10);
assert.ok(calls.some((c) => c.startsWith('/api/subagents/t1/restart')), calls.join(' '));
// Il 06/10/2026 sul Titan la bolla restava identica dopo il tap, «Riprova»
// compreso, e il rilancio partito sembrava un tap perso; fino al 07/10 restava
// come «Ripartito», accanto alla bolla del tentativo nuovo. Ora esce.
assert.equal(settled().length, 0);
assert.ok(!report.isConnected);

// Lo stesso fallimento in uno snapshot successivo non si ripete.
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [],
        recent: [worker({ state: 'failed', stop_reason: 'max_iterations', ended_at: now(), can_restart: true })] });
assert.equal(settled().length, 0);
""")


def test_a_restart_from_elsewhere_closes_the_try_again() -> None:
    # Sul Titan Jenny ha rilanciato la stessa linea mentre la bolla offriva
    # ancora «Riprova»: un tap li' avrebbe cancellato il suo tentativo.
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
const failed = worker({ lineage_id: 'L1', attempt: 1, state: 'failed', stop_reason: 'max_iterations',
                        ended_at: now(), can_restart: true });
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [failed] });
const report = settled()[0];
assert.equal(report.querySelector('.home-work-action').hidden, false);

frame({ event: 'subagent_status', chat_id: 'project:orto',
        running: [worker({ task_id: 't2', lineage_id: 'L1', attempt: 2 })], recent: [failed] });
assert.ok(!report.isConnected, 'la bolla del fallimento resta accanto al tentativo nuovo');
assert.equal(bubbles().length, 1, 'il tentativo in corso ha la sua bolla');
""")


def test_a_retry_that_stops_again_is_the_same_bubble() -> None:
    # Il 06/10/2026 sul Titan una diagnosi destinata a fallire e' stata
    # rilanciata due volte: tre bolle, due «Ripartito» e un «si e' fermato»,
    # e sembrava che l'aiutante uscisse due volte.
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
const attempt = (n, over = {}) => worker({ task_id: `t${n}`, lineage_id: 'L1', attempt: n, ...over });
const failed = (n) => attempt(n, { state: 'failed', stop_reason: 'error', ended_at: now(), can_restart: true });
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [failed(1)] });
const report = settled()[0];
report.querySelector('.home-work-action').click();
await tick(10);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [attempt(2)], recent: [failed(1)] });
assert.equal(settled().length, 0, 'dopo «Riprova» resta solo la bolla del tentativo in corso');
assert.equal(bubbles().length, 1);

frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [failed(2), failed(1)] });
assert.equal(settled().length, 1, 'il secondo fallimento della stessa linea ha aperto una bolla nuova');
assert.ok(report.classList.contains('is-failed'));
assert.match(text(report), new RegExp(W.helperStoppedAgain.replace('{count}', '2').slice(0, 20)));
const button = report.querySelector('.home-work-action');
assert.equal(button.hidden, false);
assert.equal(button.disabled, false);
frame({ event: 'stream_end', chat_id: 'project:orto', turn_id: 'x', text: 'nel frattempo' });
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [attempt(3)],
        recent: [failed(2), failed(1)] });
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [],
        recent: [failed(3), failed(2), failed(1)] });
assert.equal(settled().length, 1);
const said = document.querySelector('#home-thread > .home-msg-jenny');
assert.ok(said.compareDocumentPosition(report) & Node.DOCUMENT_POSITION_FOLLOWING,
          'la notizia nuova sta in fondo al filo, dove guardi');
button.click();
await tick(10);
assert.ok(calls.some((c) => c.startsWith('/api/subagents/t3/restart')), calls.join(' '));
""")


def test_a_failure_already_restarted_is_not_shown_on_opening() -> None:
    run_home(_HEAD + """
agents['project:orto'] = { running: [], recent: [
  worker({ task_id: 't2', lineage_id: 'L1', attempt: 2, state: 'completed', ended_at: now() }),
  worker({ lineage_id: 'L1', attempt: 1, state: 'failed', stop_reason: 'max_iterations',
           ended_at: now() - 60, can_restart: true }),
] };
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
assert.equal(settled().length, 0);
""")


def test_a_refused_restart_says_why_and_can_be_tried_again() -> None:
    run_home(_HEAD + """
const fetchBefore = hooks.fetch;
hooks.fetch = (u) => /restart$/.test(u.pathname)
  ? new Response('no free slots', { status: 429 })
  : fetchBefore(u);
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [],
        recent: [worker({ state: 'failed', stop_reason: 'max_iterations', ended_at: now(), can_restart: true })] });
const report = settled()[0];
const button = report.querySelector('.home-work-action');
button.click();
await tick(10);
assert.equal(button.hidden, false);
assert.equal(button.disabled, false);
assert.match(text(report.querySelector('.home-work-note')), /no free slots/);
""")


def test_an_old_failure_is_not_dug_up_on_opening() -> None:
    run_home(_HEAD + """
agents['project:orto'] = { running: [], recent: [worker({ state: 'failed', ended_at: now() - 3600 })] };
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
assert.equal(settled().length, 0);
""")


def test_a_late_read_does_not_undo_a_newer_frame() -> None:
    run_home(_HEAD + """
let release;
const app = await boot();
hooks.fetch = (u) => {
  if (u.pathname !== '/api/subagents') return undefined;
  return new Promise((r) => { release = () => r(ok({ running: [worker()], recent: [] })); });
};
const reading = app.work.loadSubagents();
frame({ event: 'subagent_status', chat_id: 'default', running: [], recent: [] });
release();
await reading;
assert.equal(bubbles().length, 0, 'una lettura vecchia ha riacceso la bolla');
""")


def test_jenny_writing_is_the_last_bubble_and_gives_way_to_the_answer() -> None:
    run_home(_HEAD + """
const app = await boot();
frame({ event: 'subagent_status', chat_id: 'default', running: [worker()], recent: [] });
frame({ event: 'goal_status', chat_id: 'default', status: 'running', turn_id: 't9' });
await tick(600);
const typing = live().querySelector('.home-typing');
assert.equal(typing.hidden, false, 'la bolla di Jenny non e\\u2019 comparsa');
assert.equal(live().lastElementChild, typing, 'Jenny che scrive non e\\u2019 l\\u2019ultima');
frame({ event: 'delta', chat_id: 'default', turn_id: 't9', text: 'eccomi' });
await tick(20);
assert.equal(typing.hidden, true, 'la bolla e\\u2019 rimasta sopra la risposta che arriva');
""")


def test_history_keeps_the_report_of_a_work_and_drops_its_announcement() -> None:
    """Anche a lavoro ancora in corso: allora il resoconto non c'e' ancora, e
    l'annuncio non deve prendere il suo posto (la bolla torna dallo snapshot)."""
    run_home(_HEAD + """
threads['project:orto'] = { messages: [
  { role: 'user', content: '/gardener' },
  { role: 'assistant', content: 'Gardening orto...', workId: 'gardener-1', workStart: true },
  { role: 'assistant', content: 'The gardener read 2 lines.', workId: 'gardener-1' },
  { role: 'user', content: '/gardener' },
  { role: 'assistant', content: 'Gardening orto...', workId: 'gardener-2', workStart: true },
] };
const app = await boot();
await app.showConversation('project:orto');
await tick(30);
assert.deepEqual(thread(), ['you: /gardener', 'jenny: The gardener read 2 lines.', 'you: /gardener']);
""")
