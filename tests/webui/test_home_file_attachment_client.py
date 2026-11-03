"""Un allegato che non e' un'immagine ne' un video, nel filo della casa.

Un PDF, un vocale: il filo li mostra come una pastiglia col nome. Era un
``<a>`` verso l'URL firmato, e da ``a1b8b1e3`` ogni ``<a>`` del filo passa da
``shared/content-link.js`` — giusto per il markdown di Jenny, che non deve
poter navigare la casa — e un indirizzo della stessa origine li' e' «link non
apribile»: la pastiglia non apriva piu' niente. Adesso e' un ``<button>``,
come in officina: aprire un file e' un'azione, non una navigazione.

Gli allegati li disegna ``renderMediaList`` (``shared/media.js``) per tutti e
due i gusci, e si aprono con ``openMediaFile``: col ponte nativo, che lo passa
al visore di sistema; se il ponte c'e' e fallisce lo si dice; fuori dal guscio
nativo, una scheda nuova.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, threads, toasts, locales, $ } from './boot.mjs';
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'listen', media: [
    { url: '/api/media/abc/voice.ogg?sig=1', name: 'voice.ogg', path: 'media/voice.ogg' },
    { url: '/api/media/abc/x?sig=2', kind: 'file', path: 'media/x' },
  ] },
] };
const app = await boot();
const chip = document.querySelector('#home-thread button.home-file');
assert.ok(chip, 'la pastiglia non c\\u2019e\\u2019');
const tap = () => {
  const ev = new window.MouseEvent('click', { bubbles: true, cancelable: true });
  chip.dispatchEvent(ev);
  return ev;
};
const inert = [locales.it.common.linkNotOpenable, locales.en.common.linkNotOpenable];
"""


def test_a_voice_note_opens_with_the_native_viewer() -> None:
    run_home(_HEAD + """
const opened = [];
window.JennyNative = { openFile: async (path) => { opened.push(path); return true; } };
tap();
await tick(10);
assert.deepEqual(opened, ['media/voice.ogg']);
assert.ok(!toasts().some((t) => inert.includes(t)), JSON.stringify(toasts()));
""")


def test_a_native_viewer_that_fails_says_so() -> None:
    run_home(_HEAD + """
window.JennyNative = { openFile: async () => false };
tap();
await tick(10);
const couldNot = [locales.it.chat.couldNotOpen, locales.en.chat.couldNotOpen]
  .map((t) => t.replace('{path}', 'voice.ogg'));
assert.ok(toasts().some((t) => couldNot.includes(t)), JSON.stringify(toasts()));
""")


def test_outside_the_native_shell_it_opens_in_a_new_tab() -> None:
    run_home(_HEAD + """
const tabs = [];
window.open = (url, target) => { tabs.push([url, target]); return null; };
tap();
await tick(10);
assert.deepEqual(tabs, [['/api/media/abc/voice.ogg?sig=1', '_blank']]);
""")


def test_a_file_without_a_name_says_file_in_the_user_s_language() -> None:
    """L'officina scriveva ``'file'`` a mano, la casa l'URL firmato intero."""
    run_home(_HEAD + """
const chips = [...document.querySelectorAll('#home-thread button.home-file')];
assert.equal(chips.length, 2);
assert.equal(chips[1].textContent.trim(), locales.it.chat.file);
""")
