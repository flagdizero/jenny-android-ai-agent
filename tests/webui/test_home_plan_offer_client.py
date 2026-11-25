"""Il piano a passi proposto una volta, dopo un turno lungo in un quaderno.

Un quaderno si impara usandolo: il piano a passi pochi lo scoprono da soli, e
il prompt vieta a Jenny di proporlo (``plan/driver.py``). Lo propone il codice,
sotto la risposta, una volta per quaderno; «Fammi un piano» accende il tasto
Piano del campo.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, frame, $ } from './boot.mjs';
const offers = () => [...$('home-thread').querySelectorAll('.home-plan-offer')];
const reply = async (turn, ms) => {
  frame({ event: 'stream_end', chat_id: 'project:orto', turn_id: turn, text: 'ho riordinato le pagine' });
  frame({ event: 'turn_end', chat_id: 'project:orto', turn_id: turn, latency_ms: ms });
  await tick(20);
};
"""


def test_once_after_a_long_turn_in_a_notebook() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.switchConversation('project:orto');
await tick(30);
await reply('t1', 20000);
assert.equal(offers().length, 0, 'proposto dopo un turno breve');
await reply('t2', 95000);
await until(() => offers().length, { msg: 'nessuna proposta dopo un turno lungo' });
offers()[0].querySelector('.home-plan-offer-go').click();
assert.equal(app.plan.toggle.getAttribute('aria-pressed'), 'true', 'il tasto Piano non si e\\u2019 acceso');
await reply('t3', 120000);
assert.equal(offers().length, 0, 'proposto due volte nello stesso quaderno');
""")


def test_never_in_the_personal_chat() -> None:
    run_home(_HEAD + """
await boot();
frame({ event: 'stream_end', chat_id: 'default', turn_id: 'p1', text: 'fatto' });
frame({ event: 'turn_end', chat_id: 'default', turn_id: 'p1', latency_ms: 200000 });
await tick(30);
assert.equal(offers().length, 0);
""")
