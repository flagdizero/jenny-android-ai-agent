"""«Promemoria» in casa: la riga di Impostazioni e la stanza.

Fino al 25/11/2026 in casa i promemoria si vedevano solo quando erano troppi
(la pastiglia dei risvegli), e il suo «Tutti i promemoria» portava in officina.
Ora una riga dice quanti sono accesi, e la stanza li mostra in due gruppi —
chiesti da te, messi da Jenny — con la pausa e il cestino. Quelli dei quaderni
e i lavori di sistema restano fuori.

Si guida la casa intera (``support.home_dom``): conta il legame fra la riga,
la stanza, le rotte ``/api/webui/cron`` e l'avviso con «Annulla».
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, routes, calls, hooks, ok, rpcAnswers, $ } from './boot.mjs';
const NOW = Date.now();
const job = (id, extra = {}) => ({
  id, name: id, kind: 'user', enabled: true, autonomous: false, place: null,
  schedule: { kind: 'every', every_ms: 3600000 }, next_run_at_ms: NOW + 3600000,
  actions: ['pause', 'remove'], paused_at_ms: null, runs: [], ...extra,
});
const state = { jobs: [
  job('acqua'),
  job('dentista', { schedule: { kind: 'at', at_ms: NOW + 86400000 }, one_shot: true }),
  job('riepilogo', { autonomous: true, schedule: { kind: 'every', every_ms: 86400000, until_ms: NOW + 7 * 86400000 } }),
  job('vasi', { enabled: false, paused_at_ms: NOW - 1000, actions: ['resume', 'remove'] }),
  job('innaffia', { place: { notebook: 'orto', card: null } }),
  job('dream', { kind: 'system', actions: [] }),
] };
routes['/api/webui/cron'] = state;
const acted = [];
hooks.fetch = (u) => {
  const m = u.pathname.match(/^\\/api\\/webui\\/cron\\/([^/]+)\\/(\\w+)$/);
  if (!m) return undefined;
  acted.push(`${m[2]}:${m[1]}`);
  const [, id, action] = m;
  if (action === 'remove') state.jobs = state.jobs.filter((j) => j.id !== id);
  if (action === 'pause') Object.assign(state.jobs.find((j) => j.id === id), { enabled: false, paused_at_ms: NOW, actions: ['resume', 'remove'] });
  if (action === 'resume') Object.assign(state.jobs.find((j) => j.id === id), { enabled: true, paused_at_ms: null, actions: ['pause', 'remove'] });
  return ok({ result: 'ok' });
};
rpcAnswers['cron.restore'] = ({ id }) => { acted.push(`restore:${id}`); state.jobs.push(job(id)); return { ok: true, result: 'restored' }; };
const rows = () => [...$('home-reminders-body').querySelectorAll('.home-reminders-row')];
const names = () => rows().map((r) => r.dataset.job);
"""


def test_the_settings_row_counts_the_ones_that_are_on() -> None:
    run_home(_HEAD + """
const app = await boot();
await until(() => $('home-reminders-value').textContent, { msg: 'la riga non dice niente' });
// acqua, dentista, riepilogo: accesi. vasi in pausa, quello del quaderno e Dream fuori.
assert.equal($('home-reminders-value').textContent, app.remindersRoom.value());
assert.match($('home-reminders-value').textContent, /3/);
""")


def test_the_room_splits_yours_from_hers_and_leaves_out_notebooks_and_system() -> None:
    run_home(_HEAD + """
const app = await boot();
app.openReminders();
await until(() => rows().length, { msg: 'la stanza e\\u2019 vuota' });
assert.equal(app.view, 'reminders');
assert.deepEqual(names().sort(), ['acqua', 'dentista', 'riepilogo', 'vasi'].sort());
const sections = [...$('home-reminders-body').querySelectorAll('.home-reminders-section')];
assert.equal(sections.length, 2);
assert.ok(sections[1].textContent.includes('riepilogo'), 'quello di Jenny non sta nel suo gruppo');
assert.ok(!sections[0].textContent.includes('riepilogo'));
const paused = rows().find((r) => r.dataset.job === 'vasi');
assert.ok(paused.classList.contains('is-off'));
assert.equal(paused.querySelector('.home-switch').getAttribute('aria-checked'), 'false');
assert.equal(app._backTarget(), 'settings');
""")


def test_the_switch_pauses_and_resumes() -> None:
    run_home(_HEAD + """
const app = await boot();
app.openReminders();
await until(() => rows().length);
rows().find((r) => r.dataset.job === 'acqua').querySelector('.home-switch').click();
await until(() => acted.includes('pause:acqua'), { msg: 'la pausa non e\\u2019 partita' });
await until(() => rows().find((r) => r.dataset.job === 'acqua')?.classList.contains('is-off'));
rows().find((r) => r.dataset.job === 'vasi').querySelector('.home-switch').click();
await until(() => acted.includes('resume:vasi'), { msg: 'la ripresa non e\\u2019 partita' });
""")


def test_the_trash_removes_at_once_and_undo_puts_it_back() -> None:
    run_home(_HEAD + """
const app = await boot();
app.openReminders();
await until(() => rows().length);
rows().find((r) => r.dataset.job === 'acqua').querySelector('.home-reminders-remove').click();
await until(() => !names().includes('acqua'), { msg: 'il cestino non ha tolto niente' });
assert.ok(acted.includes('remove:acqua'));
const undo = document.querySelector('.mobile-toast-action');
assert.ok(undo, 'nessun Annulla dopo il cestino');
undo.click();
await until(() => acted.includes('restore:acqua'), { msg: 'Annulla non ha chiesto di rimetterlo' });
await until(() => names().includes('acqua'), { msg: 'il promemoria non e\\u2019 tornato' });
""")


def test_the_wakes_sheet_opens_the_room_not_the_workshop() -> None:
    run_home(_HEAD + """
const app = await boot();
app.wakes.onAll();
await until(() => app.view === 'reminders', { msg: 'Tutti i promemoria non apre la stanza' });
""")


def test_an_empty_list_says_how_to_ask_for_one() -> None:
    run_home(_HEAD + """
state.jobs = [job('dream', { kind: 'system', actions: [] })];
const app = await boot();
app.openReminders();
await until(() => $('home-reminders-body').querySelector('.home-reminders-empty'));
assert.equal(rows().length, 0);
const foot = $('home-reminders-body').querySelector('.home-reminders-foot').textContent;
assert.ok(!foot.includes('chat'), 'la stanza vuota dice due volte di chiederlo in chat');
""")
