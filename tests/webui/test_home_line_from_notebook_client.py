"""Aprire la linea dalla chat di un quaderno porta nella chat di sempre.

La linea e' della chat di sempre (`home-line.js`): dalla chat di un quaderno il
tasto ⇄ c'e', ma la testa e il filo si vedono solo nella personale. Fino al
23/11/2026 il foglio apriva la linea e lasciava a schermo il quaderno: la chat
di sempre era passata all'altra Jenny, e tu non vedevi niente.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, until, rpcAnswers, rpcFailed } from './boot.mjs';
const PEER = { id: 'mac', name: 'Jenny MacBook', kind: 'jenny', has_secret: true };
const LINE = { lid: 'l1', peer: 'mac', name: 'Jenny MacBook', place: '', state: 'open', items: [] };
rpcAnswers['peer.line.status'] = () => ({ enabled: true, peers: [PEER], line: null });
const app = await boot();
const inNotebook = async () => {
  await app._enterNotebookChat('orto');
  await until(() => document.querySelector('.home-shell.is-over, .is-over') || app.chatOver,
    { msg: 'la chat del quaderno non si e\\' aperta' });
  assert.ok(!app.line.isPersonal(), 'a schermo dovrebbe esserci il quaderno');
};
"""


def test_opening_the_line_from_a_notebook_goes_to_the_main_chat() -> None:
    run_home(_HEAD + """
await inNotebook();
rpcAnswers['peer.line.open'] = () => ({ enabled: true, peers: [PEER], line: LINE });
await app.line.open('mac', '');
await until(() => app.line.isPersonal(), { msg: 'la casa e\\' rimasta nel quaderno' });
assert.ok(app.line.isOpenHere(), 'nella chat di sempre la linea si vede');
assert.equal(app.line.head.hidden, false);
""")


def test_a_refused_line_leaves_you_in_the_notebook() -> None:
    run_home(_HEAD + """
await inNotebook();
rpcAnswers['peer.line.open'] = () => rpcFailed('unreachable', 'unreachable');
await app.line.open('mac', '');
assert.ok(!app.line.isPersonal(), 'un rifiuto non deve spostarti');
assert.equal(app.line.line, null);
""")
