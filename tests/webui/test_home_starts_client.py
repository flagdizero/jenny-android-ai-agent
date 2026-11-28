"""Da dove si comincia: i suggerimenti sotto il saluto e la tessera delle app.

Dopo il primo avvio la chat ha gia' il saluto di Jenny, e una chat vuota con
«Comincia tu» quasi non si vede piu' (giro UX dell'11/10/2026, §7). I
suggerimenti stanno li' sotto finche' non scrivi tu; un tocco riempie il campo
e non manda niente. La pagina App dice da dove nascono le app di Jenny.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, threads, sent, routes, $ } from './boot.mjs';
const starts = () => [...$('home-thread').querySelectorAll('.home-start')];
const messages = () => sent.filter((m) => m.type === 'message');
"""


def test_under_the_welcome_until_you_write() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'assistant', text: 'Ciao, sono Jenny.', turnId: 'welcome:1' },
] };
const app = await boot();
await until(() => starts().length, { msg: 'nessun suggerimento sotto il saluto' });
assert.equal(starts().length, 4);
starts()[0].click();
assert.ok(app.input.value.length > 10, 'il tocco non ha riempito il campo');
assert.equal(messages().length, 0, 'il tocco ha mandato il messaggio');
app._send();
await until(() => !starts().length, { msg: 'i suggerimenti restano dopo il primo messaggio' });
""")


def test_not_when_you_already_wrote_and_not_in_a_notebook() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [
  { role: 'assistant', text: 'Ciao.', turnId: 'welcome:1' },
  { role: 'user', text: 'ciao' },
  { role: 'assistant', text: 'eccomi', turnId: 'a' },
] };
await boot();
await tick(60);
assert.equal(starts().length, 0);
""")


def test_a_draft_in_the_field_is_kept_aside() -> None:
    run_home(_HEAD + """
threads['websocket:default'] = { messages: [{ role: 'assistant', text: 'Ciao.', turnId: 'w' }] };
const app = await boot();
await until(() => starts().length);
app.input.value = 'la mia bozza';
starts()[1].click();
assert.notEqual(app.input.value, 'la mia bozza');
app._send();
await until(() => messages().length === 1);
await until(() => app.input.value === 'la mia bozza', { msg: 'la bozza non e\\u2019 tornata' });
""")


def test_the_apps_page_says_where_apps_come_from() -> None:
    run_home(_HEAD + """
routes['/api/webui/apps'] = { apps: [] };
const app = await boot();
app.homePages.goToId('app', { animated: false });
const tile = $('home-ask-app');
await until(() => app.appsSource().jennyListLoaded(), { msg: 'elenco delle app mai letto' });
await tick(10);
assert.equal(tile.hidden, false, 'senza app la tessera non si vede');
assert.ok(tile, 'nessuna tessera');
assert.ok($('home-ask-app-title').textContent.length > 0, 'la tessera e\\u2019 senza testo');
let asked = null;
app.openChatOver = (opts) => { asked = opts; return true; };
tile.click();
assert.ok(asked?.prefill, 'la tessera non apre la chat con un esempio');
""")


def test_the_tile_goes_once_you_have_an_app() -> None:
    """Insegna da dove nascono le app: con una Jenny App la strada la conosci."""
    run_home(_HEAD + """
routes['/api/webui/apps'] = { apps: [{ slug: 'spese', name: 'Spese' }] };
const app = await boot();
app.homePages.goToId('app', { animated: false });
await until(() => app.appsSource().jennyListLoaded(), { msg: 'elenco delle app mai letto' });
await tick(10);
assert.equal($('home-ask-app').hidden, true, 'la tessera resta con un\u2019app gia\u2019 fatta');
""")
