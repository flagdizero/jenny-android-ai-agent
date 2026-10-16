"""La coda di una risposta scende nella fascia solo finche' e' l'ultima.

Il 16/10/2026, nella casa aperta dal Mac, la coda di una risposta («16:08 ·
18.1s») e' rimasta nella posizione assoluta della fascia dopo che una seconda
risposta era arrivata sotto: finiva sopra le prime righe di quella. La regola
era un ``:has(+ .home-live[hidden] + .home-band)``, che WebKit non ricalcola
quando un fratello entra in mezzo; la WebView del telefono (Chromium) si'. Ora
chi sta sopra la fascia lo segna ``home-band.js`` con la classe
``home-at-band``, a ogni cambio del filo.

jsdom non disegna, quindi qui si prova la classe, non la posizione: e' la
classe che la regola CSS guarda (v. ``test_home_band_client.py``).
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame } from './boot.mjs';
const marked = () => [...document.querySelectorAll('#home-thread .home-at-band')];
const replies = () => [...document.querySelectorAll('#home-thread > .home-msg-jenny')];
const reply = async (turn, text) => {
  frame({ event: 'stream_end', chat_id: 'default', turn_id: turn, text });
  frame({ event: 'turn_end', chat_id: 'default', turn_id: turn, latency_ms: 18100 });
  await tick(20);
};
"""


def test_a_second_reply_takes_the_mark_from_the_first() -> None:
    run_home(_HEAD + """
await boot();
await tick(20);
await reply('t1', 'dopo l\\u2019ordine di ieri sei sopra.');
assert.equal(replies().length, 1);
assert.ok(replies()[0].querySelector('.home-tail'), 'la risposta non ha la coda');
assert.deepEqual(marked(), [replies()[0]]);

// Il risultato della ricerca arriva dopo, come un'altra risposta.
await reply('t2', 'ok tornata, il quadro EM e\\u2019 forte.');
assert.equal(replies().length, 2);
assert.deepEqual(marked(), [replies()[1]], 'la coda della prima resta nella fascia');
""")


def test_a_message_of_yours_takes_the_mark_and_the_live_place_clears_it() -> None:
    run_home(_HEAD + """
await boot();
await tick(20);
await reply('t1', 'Ciao.');
// Un messaggio scritto da un altro dispositivo arriva come eco.
frame({ event: 'user', chat_id: 'default', text: 'come va?', origin: 'remote' });
await tick(10);
const mine = document.querySelector('#home-thread > .home-msg-user');
assert.ok(mine, 'l\u2019eco non e\u2019 nel filo');
assert.deepEqual(marked(), [mine]);

const live = document.querySelector('#home-thread > .home-live');
live.hidden = false;
await tick(5);
assert.deepEqual(marked(), [], 'col posto vivo acceso sopra la fascia c\\u2019e\\u2019 lui');
live.hidden = true;
await tick(5);
assert.equal(marked().length, 1, 'spento il posto vivo, il segno non e\\u2019 tornato');
""")
