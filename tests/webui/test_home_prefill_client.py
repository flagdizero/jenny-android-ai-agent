"""Una frase gia' pronta nel campo non butta via ne' il posto ne' la bozza.

«Modifica questa app», «parlane in chat» di un'app e l'app rotta portano una
richiesta scritta nella conversazione personale. Prima passavano da Home: l'app
si chiudeva, si usciva dal quaderno e la mezza frase che avevi nel campo veniva
sovrascritta. Ora la chat sale sopra la pagina (il velo) e la bozza si mette da
parte finche' la frase non parte — la stessa regola dell'officina
(``test_prefill_keeps_the_draft_client.py``).
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, sent } from './boot.mjs';
const sentTexts = () => sent.filter((m) => m.type === 'message').map((m) => m.content);
"""


def test_a_prepared_request_sets_the_draft_aside_until_it_is_sent() -> None:
    run_home(_HEAD + """
const app = await boot();
app.input.value = 'mezza frase';
app._sendInChat('Modifica l\\u2019app spesa');
await tick(10);
assert.equal(app.chatOver, true, 'la chat non e\\u2019 salita');
assert.equal(app.input.value, 'Modifica l\\u2019app spesa');
app._send();
await tick(10);
assert.equal(sentTexts().at(-1), 'Modifica l\\u2019app spesa');
assert.equal(app.input.value, 'mezza frase', 'la bozza non e\\u2019 tornata');
""")


def test_a_prepared_request_leaves_the_notebook_where_it_was() -> None:
    run_home(_HEAD + """
const app = await boot();
await app.openNotebook('orto');
await tick(20);
assert.equal(app.view, 'board');
app._sendInChat('parliamo dell\\u2019app');
await tick(20);
assert.equal(app.view, 'board', 'la frase pronta ha portato via la bacheca');
assert.equal(app.currentKey(), 'websocket:default');
app.closeChatOver();
await tick(20);
assert.equal(app.currentKey(), 'project:orto', 'la bacheca e\\u2019 rimasta senza la sua chat');
""")


def test_the_same_text_is_not_set_aside_twice() -> None:
    run_home(_HEAD + """
const app = await boot();
app._sendInChat('Modifica l\\u2019app spesa');
await tick(10);
app._sendInChat('Modifica l\\u2019app spesa');
await tick(10);
app._send();
await tick(10);
assert.equal(app.input.value, '', 'una frase pronta e\\u2019 diventata la bozza di se stessa');
""")
