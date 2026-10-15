"""La casa del telefono, mentre scrivi a Jenny dal Mac.

Nella casa vera (jsdom): una domanda scritta dal Mac arriva dal vivo col suo
segnetto («from macbook»), e tornando a guardare la casa rilegge il filo solo se
la revisione del server non e' piu' quella che ha visto — con una richiesta da
una riga, mai una rilettura intera a ogni sblocco (la casa e' il launcher, e
torna visibile di continuo). V. ``tests/channels/test_remote_sync.py`` per il
lato server.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, threads, calls, thread, locales } from './boot.mjs';
const KEY = 'websocket:default';
const O = locales.en.home.origin;
threads[KEY] = {
  messages: [
    { role: 'user', content: 'ciao', sentAt: 1 },
    { role: 'assistant', content: 'ehi', sentAt: 2 },
  ],
  rev: 'boot.2',
};
/* Le letture del filo: `probe` quelle da una riga (il controllo), `full` le altre. */
const limitOf = (c) => Number(new URLSearchParams(c.split('?')[1] || '').get('limit'));
const threadCalls = () => calls.filter((c) => c.includes('/webui-thread'));
const reads = (kind) => threadCalls().filter((c) => (limitOf(c) === 1) === (kind === 'probe')).length;
const showHome = async () => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
  await tick(40);
};
const badges = () => [...document.querySelectorAll('#home-thread .home-origin')].map((b) => b.textContent.trim());
"""


def test_a_question_from_the_mac_arrives_live_with_its_badge() -> None:
    run_home(_HEAD + """
await boot();
await tick(30);
frame({ event: 'user', chat_id: 'default', text: 'dal Mac', origin: 'remote', device: 'macbook', rev: 'boot.3' });
frame({ event: 'delta', chat_id: 'default', text: 'risposta', stream_id: 's1' });
frame({ event: 'stream_end', chat_id: 'default', stream_id: 's1' });
frame({ event: 'turn_end', chat_id: 'default', rev: 'boot.5' });
await tick(30);
assert.deepEqual(thread().slice(-2), ['you: dal Mac', 'jenny: risposta']);
assert.deepEqual(badges(), [O.remoteNamed.replace('{device}', 'macbook')]);
""")


def test_a_question_without_a_device_name_says_another_device() -> None:
    run_home(_HEAD + """
await boot();
await tick(30);
frame({ event: 'user', chat_id: 'default', text: 'dal Mac', origin: 'remote' });
await tick(20);
assert.deepEqual(badges(), [O.remote]);
""")


def test_the_badge_comes_back_after_a_reload() -> None:
    run_home(_HEAD + """
threads[KEY].messages.push({ role: 'user', content: 'scritto prima', origin: 'remote', device: 'macbook', sentAt: 3 });
await boot();
await tick(30);
assert.deepEqual(badges(), [O.remoteNamed.replace('{device}', 'macbook')]);
""")


def test_back_on_the_home_it_rereads_only_when_the_thread_moved() -> None:
    run_home(_HEAD + """
await boot();
await tick(30);
const full = reads('full');
assert.ok(full >= 1, 'la casa non ha letto il filo all\\'avvio');

// Niente di nuovo sul server: una domanda da una riga, e basta.
await showHome();
assert.equal(reads('probe'), 1, 'non ha controllato');
assert.equal(reads('full'), full, 'ha riletto un filo che non era cambiato');

// Il vivo ha portato tutto (turn_end con la revisione nuova): ancora niente.
frame({ event: 'user', chat_id: 'default', text: 'dal Mac', origin: 'remote', rev: 'boot.3' });
frame({ event: 'turn_end', chat_id: 'default', rev: 'boot.5' });
threads[KEY].rev = 'boot.5';
await tick(20);
await showHome();
assert.equal(reads('full'), full, 'ha riletto anche se il vivo era completo');

// Qualcosa e' entrato senza passare di qui: si rilegge, e si vede.
threads[KEY] = {
  messages: [...threads[KEY].messages, { role: 'user', content: 'perso per strada', origin: 'remote', sentAt: 9 },
    { role: 'assistant', content: 'eccomi', sentAt: 10 }],
  rev: 'boot.9',
};
await showHome();
assert.equal(reads('full'), full + 1, 'il filo era cambiato e non ha riletto');
assert.deepEqual(thread().slice(-2), ['you: perso per strada', 'jenny: eccomi']);
""")


def test_no_reread_while_jenny_is_answering() -> None:
    run_home(_HEAD + """
await boot();
await tick(30);
const full = reads('full');
assert.ok(full >= 1, 'la casa non ha letto il filo all\\'avvio');
frame({ event: 'goal_status', status: 'running', chat_id: 'default', started_at: Date.now() / 1000 });
frame({ event: 'delta', chat_id: 'default', text: 'sto scriv', stream_id: 's1' });
threads[KEY].rev = 'boot.7';
await tick(20);
await showHome();
assert.equal(reads('full'), full, 'una rilettura a meta\\' turno strappa la risposta');
""")
