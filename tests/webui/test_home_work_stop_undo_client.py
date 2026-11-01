"""Fermare e annullare una mini-me dalla sua bolla, nella casa vera.

Sulla bolla di chi lavora c'e' «Fermala»: un foglio dal basso, nella forma del
Ferma del piano, con «Lasciala lavorare», «Ferma e tieni» e — solo se ha
cambiato dei file — «Ferma e annulla». Una mini-me ferma che ha cambiato dei
file offre «Annulla le sue modifiche (N file)», con conferma; fatto l'annulla,
la bolla dice quanti file sono tornati com'erano e quali no. L'annulla vero lo
fa il server (``agent/subagent_journal.py``): qui si guarda che la casa chiami
le route giuste e racconti il resoconto.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

from tests.webui.test_home_work_client import _HEAD as _BASE

pytestmark = requires_jsdom

_HEAD = _BASE + """
const fetchBase = hooks.fetch;
hooks.fetch = (u) => {
  if (/^[/]api[/]subagents[/][^/]+[/]cancel$/.test(u.pathname)) {
    return u.searchParams.get('undo') === '1'
      ? ok({ undone: true, report: { restored: ['wikis/orto/wiki/a.md', 'wikis/orto/wiki/b.md'],
                                     kept: [], by: 'user', at: 1 } })
      : ok({ cancelled: true });
  }
  if (/^[/]api[/]subagents[/][^/]+[/]undo$/.test(u.pathname)) {
    return ok({ undone: true, report: {
      restored: ['wikis/orto/wiki/a.md'],
      kept: [{ path: 'wikis/orto/wiki/b.md', reason: 'changed_after' }], by: 'user', at: 2 } });
  }
  return fetchBase(u);
};
const sheet = () => document.getElementById('home-work-sheet');
const rows = () => [...sheet().querySelectorAll('.home-plan-sheet-row')];
const row = (action) => rows().find((r) => r.dataset.workAction === action);
const stopped = (over = {}) => worker({ lineage_id: 'L1', attempt: 1, state: 'cancelled',
  stop_reason: 'cancelled_by_user', cancel_reason: 'user', stopped_by: 'user',
  resumable: false, can_restart: true, handled_at: null, ended_at: now(), changes: 2,
  undo: null, ...over });
"""


def test_a_working_helper_can_be_stopped_keeping_what_she_did() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', recent: [],
        running: [worker({ lineage_id: 'L1', changes: 2 })] });
const bubble = bubbles()[0];
const stop = bubble.querySelector('.home-work-action2');
assert.equal(stop.hidden, false);
assert.equal(stop.textContent, W.helperStop);
assert.equal(bubble.querySelector('.home-work-action').hidden, true,
             'il tasto pieno resta spento: fermare non e’ l’azione principale');
stop.click();
assert.equal(sheet().open, true);
assert.deepEqual(rows().map((r) => r.dataset.workAction), ['continue', 'keep', 'undo']);
assert.match(text(sheet()), new RegExp(W.undoLimit.slice(0, 20)));
row('continue').click();
assert.equal(sheet().open, false, '«Lasciala lavorare» chiude e basta');
assert.ok(!calls.some((c) => c.includes('/cancel')), calls.join(' '));
stop.click();
row('keep').click();
await tick(10);
const cancel = calls.find((c) => c.includes('/cancel'));
assert.ok(cancel && cancel.startsWith('/api/subagents/t1/cancel'), calls.join(' '));
assert.ok(!cancel.includes('undo=1'), cancel);
assert.ok(cancel.includes('session_key=project%3Aorto'), cancel);
assert.equal(sheet().open, false);
""")


def test_stop_and_undo_calls_cancel_with_undo() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', recent: [],
        running: [worker({ lineage_id: 'L1', changes: 2 })] });
bubbles()[0].querySelector('.home-work-action2').click();
assert.match(text(row('undo')), /2 files/);
row('undo').click();
await tick(10);
assert.ok(calls.some((c) => c.startsWith('/api/subagents/t1/cancel') && c.includes('undo=1')),
          calls.join(' '));
""")


def test_a_helper_that_wrote_nothing_has_no_undo_in_the_sheet() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', recent: [],
        running: [worker({ lineage_id: 'L1', changes: 0 })] });
bubbles()[0].querySelector('.home-work-action2').click();
assert.deepEqual(rows().map((r) => r.dataset.workAction), ['continue', 'keep']);
assert.match(text(row('keep')), new RegExp(W.stopSheet.keepSubNothing.slice(0, 15)));
""")


def test_with_several_helpers_each_row_has_its_own_stop() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', recent: [], running: [
  worker({ task_id: 't1', label: 'felci', changes: 1 }),
  worker({ task_id: 't2', label: 'aceri', changes: 3 }),
] });
const bubble = bubbles()[0];
assert.equal(bubble.querySelector('.home-work-action2').hidden, true,
             'con due mini-me un tasto solo non direbbe quale');
const stops = [...bubble.querySelectorAll('.home-work-row-stop')];
assert.equal(stops.length, 2);
assert.equal(stops[1].getAttribute('aria-label'), W.helperStopNamed.replace('{label}', 'aceri'));
stops[1].click();
assert.match(text(sheet()), /aceri/);
row('undo').click();
await tick(10);
assert.ok(calls.some((c) => c.startsWith('/api/subagents/t2/cancel') && c.includes('undo=1')),
          calls.join(' '));
""")


def test_a_helper_you_stopped_offers_to_undo_her_changes() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [stopped()] });
assert.equal(settled().length, 1);
const card = settled()[0];
assert.match(text(card), new RegExp(W.helperStoppedByYou.split(':')[0]));
assert.match(text(card), new RegExp(W.why.helperStoppedKept.split(':')[0]));
assert.equal(card.querySelector('.home-work-action').hidden, true,
             'fermata da te: nessun tasto che la rimette al lavoro');
const undo = card.querySelector('.home-work-action2');
assert.equal(undo.textContent, W.undoActionMany.replace('{count}', '2'));
undo.click();
assert.equal(sheet().open, true);
assert.deepEqual(rows().map((r) => r.dataset.workAction), ['keep', 'undo']);
row('undo').click();
await tick(10);
assert.ok(calls.some((c) => c.startsWith('/api/subagents/t1/undo')), calls.join(' '));
assert.equal(sheet().open, false);
assert.match(text(card), new RegExp(W.undone.you.split(':')[0]));
assert.match(text(card.querySelector('.home-work-note')), /b\\.md/);
assert.equal(card.querySelector('.home-work-action2').hidden, true, 'gia’ annullata');
""")


def test_one_file_put_back_and_one_left_read_in_the_singular() -> None:
    """«Undone: 1 files put back as they were» si e' visto sul telefono il
    31/10/2026: con un file solo la riga e la nota vanno al singolare, come
    gia' facevano il tasto («Undo her change (1 file)») e il foglio."""
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [stopped()] });
const card = settled()[0];
card.querySelector('.home-work-action2').click();
row('undo').click();
await tick(10);
assert.ok(text(card).includes(W.undone.youOne), text(card));
assert.equal(text(card.querySelector('.home-work-note')),
             W.undone.keptOthersOne.replace('{files}', 'b.md'));
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [stopped({
  changes: 0, undo: { restored: 1, kept: [{ path: 'x/c.md', reason: 'missing' }],
                      kept_count: 1, by: 'jenny', at: 6 } })] });
assert.ok(text(card).includes(W.undone.jennyOne), text(card));
assert.equal(text(card.querySelector('.home-work-note')),
             W.undone.keptOtherOne.replace('{files}', 'c.md'));
""")


def test_a_helper_out_of_breath_offers_continue_and_undo() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [stopped({
  state: 'budget', stop_reason: 'max_iterations', cancel_reason: null, stopped_by: null,
  resumable: true, changes: 1 })] });
const card = settled()[0];
assert.equal(card.querySelector('.home-work-action').textContent, W.helperContinue);
assert.equal(card.querySelector('.home-work-action2').textContent, W.undoActionOne);
""")


def test_an_undo_done_by_jenny_repaints_the_card() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [stopped({
  state: 'budget', stop_reason: 'max_iterations', cancel_reason: null, stopped_by: null,
  resumable: true })] });
const card = settled()[0];
assert.equal(card.querySelector('.home-work-action').textContent, W.helperContinue);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [stopped({
  state: 'budget', stop_reason: 'max_iterations', cancel_reason: null, stopped_by: null,
  resumable: false, changes: 0,
  undo: { restored: 2, kept: [], kept_count: 0, by: 'jenny', at: 5 } })] });
assert.match(text(card), new RegExp(W.undone.jenny.split(':')[0]));
assert.equal(card.querySelector('.home-work-action').hidden, true,
             'annullata: niente da far continuare');
assert.equal(card.querySelector('.home-work-action2').hidden, true);
""")


def test_a_refused_undo_says_why_and_keeps_the_sheet() -> None:
    run_home(_HEAD + """
const before = hooks.fetch;
hooks.fetch = (u) => /undo$/.test(u.pathname)
  ? new Response('This helper is still working', { status: 409 })
  : before(u);
const app = await boot();
await app.showConversation('project:orto');
await tick(20);
frame({ event: 'subagent_status', chat_id: 'project:orto', running: [], recent: [stopped()] });
settled()[0].querySelector('.home-work-action2').click();
row('undo').click();
await tick(10);
assert.equal(sheet().open, true);
assert.equal(row('undo').disabled, false);
assert.match(text(sheet().querySelector('.home-work-sheet-error')), /still working/);
""")
