"""Il campo vuoto della casa e' di una riga, qualunque cosa misuri il browser.

Da vuoto lo ``scrollHeight`` di Chromium conta anche il segnaposto, che va a
capo come un testo: misurato con il campo stretto — la WebView che parte senza
ancora una larghezza — dava il tetto di 140px, e il campo restava alto e vuoto
finche' non si scriveva una lettera. jsdom non fa layout, quindi lo
``scrollHeight`` di Chromium si finge: quel che si prova e' che da vuoto
``_autosize`` non lo legge piu'.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick } from './boot.mjs';
const app = await boot();
/* Quel che Chromium risponde per un campo stretto col segnaposto a capo. */
Object.defineProperty(app.input, 'scrollHeight', { configurable: true, get: () => 140 });
const type = (text) => {
  app.input.value = text;
  app.input.dispatchEvent(new app.input.ownerDocument.defaultView.Event('input', { bubbles: true }));
};
"""


def test_an_empty_field_carries_no_height_of_its_own() -> None:
    run_home(_HEAD + """
app._autosize();
assert.equal(app.input.style.height, '', 'il campo vuoto ha preso l\\u2019altezza del segnaposto');
""")


def test_text_still_grows_the_field_and_emptying_it_lets_go() -> None:
    run_home(_HEAD + """
type('una\\ndue\\ntre');
assert.equal(app.input.style.height, '140px', 'il campo non cresce piu\\u2019 col testo');
type('');
assert.equal(app.input.style.height, '', 'cancellato il testo, il campo e\\u2019 rimasto alto');
""")


def test_sending_leaves_a_one_line_field() -> None:
    run_home(_HEAD + """
type('una\\ndue\\ntre');
app._send();
await tick(10);
assert.equal(app.input.value, '');
assert.equal(app.input.style.height, '', 'dopo l\\u2019invio il campo e\\u2019 rimasto alto');
""")
