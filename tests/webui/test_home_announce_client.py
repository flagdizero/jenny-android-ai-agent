"""TalkBack sente le risposte nuove di Jenny, e non la storia.

Prima `#home-thread` non era una regione viva: TalkBack non diceva niente quando
Jenny rispondeva (giro UX dell'11/10/2026, §9). Rendere vivo il filo intero non
va: una rilettura della storia verrebbe letta tutta, e le bolle degli aiutanti
si annunciano gia' da sole. Per questo c'e' `#home-announce`, nascosto, dentro
`#home-chat`: riceve il testo di una risposta a turno chiuso, dal vivo.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, frame, threads, sent, $ } from './boot.mjs';
const live = () => $('home-announce').textContent;
"""


def test_a_finished_live_reply_is_announced_once() -> None:
    run_home(_HEAD + """
const app = await boot();
assert.equal($('home-thread').getAttribute('role'), 'log');
assert.equal($('home-thread').getAttribute('aria-live'), 'off');
assert.ok($('home-chat').contains($('home-announce')), 'l annunciatore non sta nella chat che si sposta');
frame({ event: 'delta', chat_id: 'default', text: 'Domani **piove**' });
frame({ event: 'stream_end', chat_id: 'default', text: 'Domani **piove**' });
await tick(10);
assert.equal(live(), '', 'annunciato a meta\\u2019 turno');
frame({ event: 'turn_end', chat_id: 'default' });
await until(() => live(), { msg: 'la risposta non e\\u2019 stata annunciata' });
/* Il testo disegnato, non il sorgente: qui il banco non carica la libreria
   del markdown, quindi i due coincidono; conta che venga dal blocco. */
const shown = [...document.querySelectorAll('#home-thread .home-msg-jenny .home-block')].at(-1).textContent.trim();
assert.equal(live(), shown);
""")


def test_the_history_is_not_read_out() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'ciao' },
  { role: 'assistant', text: 'ciao a te', turnId: 'a' },
] };
await boot();
await tick(120);
assert.equal(live(), '', 'la storia riletta e\\u2019 stata annunciata');
""")


def test_the_offline_banner_is_a_status() -> None:
    run_home(_HEAD + """
await boot();
assert.equal($('home-wire').getAttribute('role'), 'status');
""")
