"""Il posto nella casa sopravvive a un riavvio della WebView.

Android puo' chiudere il processo o il renderer di Jenny mentre usi un'altra
app pesante, e la WebView riparte da capo: fino al 10/10/2026 la casa ripartiva
sempre dalla chat. Ora ricorda la pagina in alto e, dentro un quaderno, la
stanza — e la riprende all'avvio, se e' recente e se l'avvio non chiede la chat
(``?mode=chat``: un avviso toccato ad app chiusa, il riavvio dopo un
ripristino).
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick } from './boot.mjs';
const place = (p) => localStorage.setItem('home-place', JSON.stringify({ at: Date.now(), ...p }));
const saved = () => JSON.parse(localStorage.getItem('home-place') || 'null');
"""


def test_the_page_you_were_on_is_remembered_and_comes_back() -> None:
    run_home(_HEAD + """
place({ page: 'notebooks', view: 'chat' });
const app = await boot();
await tick(400);
assert.equal(app.homePages.order[app.homePages.index], 'notebooks', 'si e\\u2019 ripartiti dalla chat');
app.homePages.goToId('app', { animated: false });
await tick(50);
assert.equal(saved().page, 'app', 'il posto nuovo non e\\u2019 stato ricordato');
""")


def test_a_notebook_board_comes_back_with_its_conversation() -> None:
    run_home(_HEAD + """
place({ page: 'notebooks', view: 'board', notebook: 'orto' });
const app = await boot();
await tick(400);
assert.equal(app.view, 'board');
assert.equal(app.currentKey(), 'project:orto');
""")


def test_an_old_place_or_a_boot_that_asks_for_the_chat_starts_in_the_chat() -> None:
    run_home(_HEAD + """
localStorage.setItem('home-place', JSON.stringify({ at: Date.now() - 7 * 3600 * 1000, page: 'notebooks' }));
const app = await boot();
await tick(400);
assert.equal(app.homePages.order[app.homePages.index], 'chat', 'un posto di sette ore fa e\\u2019 stato ripreso');
""")
    run_home(_HEAD + """
place({ page: 'notebooks', view: 'board', notebook: 'orto' });
history.replaceState(null, '', '/html-mobile/index.html?mode=chat#bs=secret');
const app = await boot();
await tick(400);
assert.equal(app.view, 'chat');
assert.equal(app.homePages.order[app.homePages.index], 'chat', 'l\\u2019avviso chiedeva la chat');
""")
