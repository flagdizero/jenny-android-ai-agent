"""«Riprova» sotto una risposta finita in un errore del modello.

Prima un errore del provider arrivava in chat come testo grezzo («Error calling
LLM: …») e l'unico modo di riprovare era riscrivere la domanda. Ora il gateway
marca la risposta con ``error: {kind}`` — dal vivo e nella cronologia — e la
casa mette sotto la bolla un bottone che rimanda il messaggio a cui rispondeva.

Si guida la casa intera (``support.home_dom``): conta il legame fra la bolla
dell'utente, quella dell'errore e il socket.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, frame, threads, sent, $ } from './boot.mjs';
const retries = () => [...$('home-thread').querySelectorAll('.home-retry')];
const sentTexts = () => sent.filter((m) => m.type === 'message').map((m) => m.content);
"""


def test_a_live_model_error_offers_retry_and_resends_the_question() -> None:
    run_home(_HEAD + """
const app = await boot();
app.input.value = 'che tempo fa?';
app._send();
await until(() => sentTexts().includes('che tempo fa?'));
frame({ event: 'message', chat_id: 'default', text: 'Non riesco a raggiungere il modello.',
        error: { kind: 'connection' } });
frame({ event: 'turn_end', chat_id: 'default' });
await until(() => retries().length, { msg: 'nessun Riprova sotto l\\u2019errore' });
assert.equal(retries().length, 1);
const before = sentTexts().length;
retries()[0].click();
await until(() => sentTexts().length > before, { msg: 'Riprova non ha rimandato niente' });
await until(() => retries().length === 0, { msg: 'il Riprova usato e\\u2019 rimasto' });
assert.equal(sentTexts().length, before + 1);
assert.equal(sentTexts().at(-1), 'che tempo fa?');
""")


def test_retry_survives_a_reload_and_only_on_the_last_answer() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'prima' },
  { role: 'assistant', text: 'errore vecchio', turnId: 'a', error: { kind: 'timeout' } },
  { role: 'user', text: 'seconda' },
  { role: 'assistant', text: 'errore nuovo', turnId: 'b', error: { kind: 'server' } },
] };
await boot();
await tick(20);
const r = retries();
assert.equal(r.length, 1, 'un Riprova per ogni errore, anche quelli superati');
assert.ok(r[0].closest('.home-msg').textContent.includes('errore nuovo'));
""")


def test_no_retry_for_a_normal_answer_or_a_question_with_attachments() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'ciao' },
  { role: 'assistant', text: 'ciao a te', turnId: 'a' },
  { role: 'user', text: 'guarda', media: ['/media/x.jpg'] },
  { role: 'assistant', text: 'errore', turnId: 'b', error: { kind: 'connection' } },
] };
await boot();
await tick(20);
assert.equal(retries().length, 0, 'il testo da solo non e\\u2019 la stessa domanda');
""")
