"""Jenny al lavoro e' un divisore nel filo, non una bolla.

Fino al 13/10/2026 la riga di Jenny al lavoro aveva la pelle delle bolle di
lavoro — bordo, fondo, il posto dove arrivano le risposte — e si leggeva come
un messaggio («almost there, promise…»). Adesso e' una riga sottile con al
centro il fiore, la parola e i secondi del turno.

Si gira la casa vera (``support.home_dom``). La forma la fa il CSS, che jsdom
non applica: qui si controlla che la riga non porti piu' la classe delle bolle,
che abbia i suoi tratti, e che l'orologio conti il turno e non la riga.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, locales } from './boot.mjs';
const V = locales.en.home.verbs;
const line = () => document.querySelector('#home-thread > .home-live > .home-typing');
const clock = () => line().querySelector('.home-typing-clock').textContent;
const secs = () => Number(clock().replace(/[^0-9]/g, ''));
const running = (turn = 't1') => frame({ event: 'goal_status', chat_id: 'default', status: 'running', turn_id: turn });
/* Un turno lasciato aperto tiene vivi i timer della riga, e node non esce
   piu': ogni banco lo chiude. */
const done = async (turn = 't1') => {
  frame({ event: 'turn_end', chat_id: 'default', turn_id: turn });
  frame({ event: 'goal_status', chat_id: 'default', status: 'idle', turn_id: turn });
  await tick(20);
};
"""


def test_jenny_at_work_is_a_divider_not_a_bubble() -> None:
    run_home(_HEAD + """
const app = await boot();
running();
await tick(600);
const el = line();
assert.ok(el, 'la riga di Jenny al lavoro non c\\u2019e\\u2019');
assert.equal(el.hidden, false);
assert.equal(el.classList.contains('home-work'), false, 'ha ancora la pelle delle bolle');
assert.equal(el.querySelectorAll('.home-typing-rule').length, 2, 'mancano i tratti del divisore');
assert.ok(V.think.includes(el.querySelector('.home-activity-text').textContent));
assert.equal(clock(), locales.en.home.work.time.sec.replace('{sec}', '0'));
await done();
assert.equal(el.hidden, true, 'la riga resta a turno finito');
assert.equal(clock(), '');
""")


def test_the_clock_counts_the_turn_across_the_answer() -> None:
    """La risposta toglie la riga; se gli strumenti ripartono, i secondi
    riprendono da dove erano, perche' il turno e' lo stesso."""
    run_home(_HEAD + """
const app = await boot();
running();
await tick(600);
frame({ event: 'delta', chat_id: 'default', turn_id: 't1', text: 'eccomi' });
await tick(20);
assert.equal(line().hidden, true, 'la riga sta sopra la risposta che arriva');
await tick(1500);
frame({ event: 'message', chat_id: 'default', turn_id: 't1', kind: 'progress',
        tool_events: [{ phase: 'start', name: 'grep' }] });
await tick(600);
assert.equal(line().hidden, false, 'gli strumenti ripartiti non si dicono');
assert.ok(V.search.includes(line().querySelector('.home-activity-text').textContent));
assert.ok(secs() >= 2, `l\\u2019orologio e\\u2019 ripartito da zero: ${clock()}`);
await done();

// Un turno nuovo riparte da zero.
running('t2');
await tick(600);
assert.equal(secs(), 0, `il turno nuovo eredita i secondi del vecchio: ${clock()}`);
await done('t2');
""")
