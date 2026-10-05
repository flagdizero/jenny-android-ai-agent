"""Cancellare un quaderno mentre la sua bacheca e' aperta.

Dal 05/10/2026 un quaderno si apre sempre sulla sua bacheca, e la chat — a
casa, dietro la bacheca — passa alla sua conversazione senza farsi vedere
(``ChatMove.retune``). La sua scheda si apre dai Quaderni con una pressione
lunga. Cancellarlo deve togliere la bacheca di un quaderno che non c'e' piu',
riportare la chat alla personale e lasciarti sui Quaderni. ``deleteNotebook``,
la bacheca e il trasloco si incontrano solo nella casa intera.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom


def test_deleting_the_notebook_whose_board_is_open_goes_back_to_the_notebooks() -> None:
    run_home("""
import assert from 'node:assert/strict';
import { boot, tick, rpcAnswers, toasts, locales, $ } from './boot.mjs';
rpcAnswers['project.delete'] = () => ({ ok: true });
const app = await boot();
app.homePages.goToId('notebooks', { animated: false });
await app.switchConversation('project:orto');
await tick(30);
assert.equal(app.view, 'board', 'il quaderno non si e\\u2019 aperto sulla bacheca');
assert.equal(app.currentKey(), 'project:orto');

const done = app.deleteNotebook('orto');
await tick(20);
$('oc-confirm-ok').click();
assert.equal(await done, true);
await tick(30);

assert.equal(app.view, 'chat', 'la bacheca del quaderno cancellato e\\u2019 ancora aperta');
assert.equal(app._entry.id, 'notebooks', 'cancellare ha portato via dai Quaderni');
assert.equal(app.currentKey(), 'websocket:default');
assert.equal($('home-chat').closest('[data-page]')?.dataset.page, 'chat', 'la chat non e\\u2019 a casa');
const said = [locales.it, locales.en].map((l) => l.home.notebook.deleted.replace('{name}', 'orto'));
assert.ok(toasts().some((t) => said.includes(t)), JSON.stringify(toasts()));
""")
