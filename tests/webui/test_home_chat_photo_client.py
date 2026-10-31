"""La foto della pagina chat se ne va quando la chat torna sua.

Aperto un quaderno, la chat vera passa dietro la bacheca con la sua chiave e
la pagina chat mostra una foto della personale (``ChatMove.retune``). Tornando,
la foto la toglie solo il trasloco. Due strade lo scavalcavano e la lasciavano
li' per sempre, sopra la chat vera (s22, 28/10/2026):

- Indietro dalla bacheca, aperta stando sulla pagina chat («Ci spostiamo?»):
  cambiava solo la vista, e la chat restava del quaderno sotto la foto della
  personale, i due fili uno sull'altro;
- ``openConversation`` dalla pagina chat (``goHome``, l'avviso della
  personale) cambiava conversazione con ``showConversation``.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, threads } from './boot.mjs';
threads['websocket:default'] = { messages: [
  { role: 'user', text: 'ciao' }, { role: 'assistant', text: 'ciao a te', turnId: 'h' },
] };
threads['project:orto'] = { messages: [
  { role: 'user', text: 'il basilico' }, { role: 'assistant', text: 'al sole', turnId: 'o' },
] };
const app = await boot();
await tick(50);
const chatPage = () => app.homePages._panelFor('chat');
const photos = () => [...chatPage().children].filter((c) => c.classList.contains('home-snapshot'));
/* «Ci spostiamo?»: il quaderno si apre stando sulla pagina chat. */
await app.switchConversation('project:orto');
await tick(700);
assert.equal(app.view, 'board');
assert.equal(app.currentKey(), 'project:orto');
assert.equal(photos().length, 1, 'la pagina chat non mostra la foto della personale');
"""


def test_back_from_the_board_brings_the_personal_chat_back_without_the_photo() -> None:
    run_home(_HEAD + """
app.goBackOneRoom();
await tick(700);
assert.equal(app.view, 'chat');
assert.equal(app.currentKey(), 'websocket:default', 'la pagina chat mostra il quaderno');
assert.equal(photos().length, 0, 'la foto e rimasta sopra la chat');
""")


def test_home_drops_the_photo_too() -> None:
    run_home(_HEAD + """
app.goHome();
await tick(700);
assert.equal(app.currentKey(), 'websocket:default');
assert.equal(photos().length, 0, 'la foto e rimasta sopra la chat');
""")
