"""Tornare alla chat da una stanza costa un disegno, non quattro.

``_setView('chat')`` chiamava ``_applyConversation`` — che rifa' le
traduzioni di tutta la casa, e con loro la fila, i Quaderni, le pagine e le
stanze delle Impostazioni — e poi ridisegnava di nuovo fila e Quaderni, e il
conteggio delle pagine una terza volta. Tornare da «Jenny» alla pagina
Impostazioni disegnava la fila quattro volte e i Quaderni due, per una
conversazione che non era cambiata.

La conversazione cambia in ``showConversation``, ed e' li' che si ridisegna
quel che la dice; un ritorno alla chat rimette solo l'intestazione.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick } from './boot.mjs';
const app = await boot();
const count = {};
const spy = (obj, name, label) => {
  const f = obj[name].bind(obj);
  obj[name] = (...a) => { count[label] = (count[label] || 0) + 1; return f(...a); };
};
spy(app.strip, 'draw', 'strip');
spy(app.who, 'render', 'who');
spy(app.board, 'applyTranslations', 'pages');
spy(app.modelRoom, '_paint', 'modelRoom');
spy(app.updatesRoom, '_paint', 'updatesRoom');
spy(app.backupRoom, '_paint', 'backupRoom');
const reset = () => { for (const k of Object.keys(count)) delete count[k]; };
"""


def test_back_from_a_settings_room_draws_the_strip_at_most_once() -> None:
    run_home(_HEAD + """
app.homePages.goToId('settings');
await tick(30);
app.openJenny();
await tick(10);
reset();
app.goBackOneRoom();
await tick(30);
assert.equal(app.view, 'chat');
assert.ok((count.strip || 0) <= 1, `la fila disegnata ${count.strip} volte`);
assert.deepEqual(
  { who: count.who, pages: count.pages, model: count.modelRoom, updates: count.updatesRoom,
    backup: count.backupRoom },
  { who: undefined, pages: undefined, model: undefined, updates: undefined, backup: undefined },
  JSON.stringify(count),
);
""")


def test_back_from_the_notebook_board_draws_the_strip_at_most_once() -> None:
    # Dai Quaderni, come chi tocca il quaderno: Indietro ci torna senza cambiare
    # conversazione. Aperta dalla pagina chat, la bacheca riporta la chat alla
    # personale, e quello e' un cambio vero (``test_home_chat_photo_client``).
    run_home(_HEAD + """
app.homePages.goToId('notebooks');
await tick(30);
await app.switchConversation('project:orto');
await tick(30);
await app.openBoard();
await tick(30);
reset();
app.goBackOneRoom();
await tick(30);
assert.equal(app.view, 'chat');
assert.ok((count.strip || 0) <= 1, `la fila disegnata ${count.strip} volte`);
assert.equal(count.who, undefined, JSON.stringify(count));
assert.equal(count.pages, undefined, JSON.stringify(count));
""")
