"""I bottoni sotto una risposta finita in un errore del modello.

Prima un errore del provider arrivava in chat come testo grezzo («Error calling
LLM: …») e l'unico modo di riprovare era riscrivere la domanda. Ora il gateway
marca la risposta con ``error: {kind}`` — dal vivo e nella cronologia — e la
casa mette sotto la bolla i bottoni che servono per quel tipo d'errore:
«Riprova», che rimanda il messaggio a cui rispondeva (allegati compresi), e
«Apri Chi risponde», dove si ripara una chiave rifiutata o un credito finito.

Si guida la casa intera (``support.home_dom``): conta il legame fra la bolla
dell'utente, quella dell'errore e il socket.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, frame, threads, sent, $ } from './boot.mjs';
const again = () => [...$('home-thread').querySelectorAll('.home-retry-again')];
const toModel = () => [...$('home-thread').querySelectorAll('.home-retry-model')];
const buttons = () => [...$('home-thread').querySelectorAll('.home-retry')];
const sentTexts = () => sent.filter((m) => m.type === 'message').map((m) => m.content);
const sentMessages = () => sent.filter((m) => m.type === 'message');
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
await until(() => again().length, { msg: 'nessun Riprova sotto l\\u2019errore' });
assert.equal(again().length, 1);
assert.equal(toModel().length, 1, 'connessione caduta: anche Chi risponde');
const before = sentTexts().length;
again()[0].click();
await until(() => sentTexts().length > before, { msg: 'Riprova non ha rimandato niente' });
await until(() => buttons().length === 0, { msg: 'i bottoni usati sono rimasti' });
assert.equal(sentTexts().length, before + 1);
assert.equal(sentTexts().at(-1), 'che tempo fa?');
""")


def test_retry_resends_the_photo_with_the_question() -> None:
    run_home(_HEAD + """
const app = await boot();
const photo = { data_url: 'data:image/png;base64,AAAA', name: 'pianta.png' };
app._sendText('cos\\u2019e\\u2019 questa pianta?', [photo], [{ url: photo.data_url, name: photo.name, kind: 'image' }]);
await until(() => sentTexts().length === 1);
frame({ event: 'message', chat_id: 'default', text: 'Il modello non risponde.',
        error: { kind: 'timeout' } });
frame({ event: 'turn_end', chat_id: 'default' });
await until(() => again().length, { msg: 'nessun Riprova per una domanda con foto' });
assert.match(again()[0].textContent, /photo/, 'l\\u2019etichetta dice che torna la foto');
again()[0].click();
await until(() => sentMessages().length === 2, { msg: 'Riprova non ha rimandato niente' });
const resent = sentMessages().at(-1);
assert.equal(resent.content, 'cos\\u2019e\\u2019 questa pianta?');
assert.equal(resent.media?.length, 1, 'la foto e\\u2019 tornata con la domanda');
assert.equal(resent.media[0].data_url, photo.data_url);
""")


def test_a_photo_without_text_can_be_retried() -> None:
    run_home(_HEAD + """
const app = await boot();
const photo = { data_url: 'data:image/png;base64,BBBB', name: 'scontrino.png' };
app._sendText('', [photo], [{ url: photo.data_url, name: photo.name, kind: 'image' }]);
await until(() => sentMessages().length === 1);
frame({ event: 'message', chat_id: 'default', text: 'Server occupato.', error: { kind: 'server' } });
frame({ event: 'turn_end', chat_id: 'default' });
await until(() => again().length, { msg: 'una foto da sola non si poteva riprovare' });
again()[0].click();
await until(() => sentMessages().length === 2);
assert.equal(sentMessages().at(-1).media?.length, 1);
""")


def test_a_rejected_key_offers_only_who_answers() -> None:
    run_home(_HEAD + """
const app = await boot();
app.input.value = 'ciao';
app._send();
await until(() => sentTexts().includes('ciao'));
frame({ event: 'message', chat_id: 'default', text: 'La chiave e\\u2019 stata rifiutata.',
        error: { kind: 'auth' } });
frame({ event: 'turn_end', chat_id: 'default' });
await until(() => toModel().length, { msg: 'nessun Apri Chi risponde' });
assert.equal(again().length, 0, 'riprovare con la stessa chiave non serve');
toModel()[0].click();
await until(() => app.view === 'model', { msg: 'Chi risponde non si e\\u2019 aperta' });
assert.equal(app._backTarget(), 'chat', 'Indietro torna alla chat');
app.goBackOneRoom();
await until(() => app.view === 'chat');
app.openModel();
assert.equal(app._backTarget(), 'settings', 'dalla riga di Impostazioni si torna li\\u2019');
""")


def test_credit_offers_only_who_answers_and_other_kinds_only_retry() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'prima' },
  { role: 'assistant', text: 'credito finito', turnId: 'a', error: { kind: 'credit' } },
] };
await boot();
await tick(20);
assert.equal(toModel().length, 1);
assert.equal(again().length, 0);
""")
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'prima' },
  { role: 'assistant', text: 'troppe richieste', turnId: 'a', error: { kind: 'rate_limit' } },
] };
await boot();
await tick(20);
assert.equal(again().length, 1);
assert.equal(toModel().length, 0);
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
const r = buttons();
assert.equal(r.length, 1, 'i bottoni stanno solo sotto l\\u2019ultimo errore');
assert.ok(r[0].closest('.home-msg').textContent.includes('errore nuovo'));
""")


def test_no_retry_for_a_normal_answer_or_reloaded_attachments() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'ciao' },
  { role: 'assistant', text: 'ciao a te', turnId: 'a' },
  { role: 'user', text: 'guarda', media: ['/media/x.jpg'] },
  { role: 'assistant', text: 'errore', turnId: 'b', error: { kind: 'connection' } },
] };
await boot();
await tick(20);
assert.equal(again().length, 0, 'riletto dalla storia, il testo da solo non e\\u2019 la stessa domanda');
assert.equal(toModel().length, 1, 'Chi risponde resta: non dipende dalla domanda');
""")
