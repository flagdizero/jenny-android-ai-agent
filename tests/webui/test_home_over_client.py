"""Jenny e' la porta della chat: la chat vera sale sopra la pagina.

Dal 05/10/2026 il tocco sulla mascotte, dove la chat non c'e', porta su la
chat vera — la stessa `#home-chat`, spostata e non copiata — a tutto schermo,
in un velo (`#home-over`) che lascia vedere la pagina sotto senza farla
toccare. Sopra la bacheca di un quaderno e' la chat del quaderno; altrove la
personale. Si chiude ritoccando Jenny o con Indietro. Prima c'era la
minichat, una domanda a un turno in un fumetto.

Qui gira la casa intera (`support/home_dom.py`): pista, stanze, trasloco,
velo e mascotte veri. Il tocco si chiama dalla mascotte (`_onTap`), cioe' dalla
stessa funzione che la fisica del dito chiama a fine gesto.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, $ } from './boot.mjs';
const app = await boot();
await tick(30);
const tap = () => app.jenny._onTap();
const over = () => $('home-over');
const chatIn = () => $('home-chat').parentElement;
"""


def test_a_tap_on_the_app_page_brings_the_chat_over_it() -> None:
    run_home(_HEAD + """
app.homePages.goToId('app', { animated: false });
await tick(10);
tap();
await tick(30);
assert.equal(app.chatOver, true);
assert.equal(over().hidden, false);
assert.equal(chatIn(), over(), 'la chat non e\\u2019 salita nel velo');
assert.equal(app.homePages.order[app.homePages.index], 'app', 'la pagina sotto e\\u2019 cambiata');
assert.equal(app.currentKey(), 'websocket:default');
assert.equal(document.querySelector('.home-showcase').inert, true, 'la pagina sotto si tocca ancora');
assert.ok(!app.jenny.el.inert, 'lei non e\\u2019 piu\\u2019 il bottone che chiude');
assert.equal(app.jenny.el.getAttribute('aria-expanded'), 'true');
assert.equal(document.querySelectorAll('#home-chat').length, 1, 'la chat e\\u2019 stata copiata');
""")


def test_back_closes_it_and_the_chat_goes_home() -> None:
    run_home(_HEAD + """
app.homePages.goToId('settings', { animated: false });
await tick(10);
tap();
await tick(30);
app.handleHardwareBack();
await tick(10);
assert.equal(app.chatOver, false);
assert.equal(over().hidden, true);
assert.equal(chatIn().dataset.page, 'chat', 'la chat non e\\u2019 tornata nella sua pagina');
assert.equal(document.querySelector('.home-showcase').inert, false, 'la pagina sotto e\\u2019 rimasta intoccabile');
assert.equal(app.homePages.order[app.homePages.index], 'settings', 'Indietro ha chiuso anche la pagina');
""")


def test_a_second_tap_on_her_closes_it() -> None:
    run_home(_HEAD + """
app.homePages.goToId('app', { animated: false });
await tick(10);
tap();
await tick(30);
tap();
await tick(10);
assert.equal(app.chatOver, false);
assert.equal(app.jenny.el.getAttribute('aria-expanded'), 'false');
""")


def test_over_a_board_it_is_the_notebook_chat_and_nothing_switches() -> None:
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
assert.equal(app.view, 'board');
assert.equal(app.currentKey(), 'project:orto', 'aprire la bacheca non ha preparato la chat');
tap();
await tick(30);
assert.equal(app.chatOver, true);
assert.equal(app.view, 'board', 'la bacheca e\\u2019 sparita da sotto la chat');
assert.equal(app.currentKey(), 'project:orto');
assert.equal($('home-input').placeholder.includes('quaderno') || $('home-input').placeholder.includes('notebook'), true,
             'il campo non dice che scrivi nel quaderno: ' + $('home-input').placeholder);
assert.equal($('home-board').inert, true, 'la bacheca sotto si tocca ancora');
app.handleHardwareBack();
await tick(10);
assert.equal(app.view, 'board', 'Indietro ha chiuso anche la bacheca');
""")


def test_after_a_board_the_app_page_brings_up_the_personal_chat() -> None:
    """La chat resta sulla chiave del quaderno quando lasci la bacheca: sopra le
    App deve tornare la personale, o si scriverebbe nel quaderno."""
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
app.handleHardwareBack();
await tick(10);
app.homePages.goToId('app', { animated: false });
await tick(10);
assert.equal(app.currentKey(), 'project:orto');
tap();
await tick(40);
assert.equal(app.currentKey(), 'websocket:default', 'sopra le App si scrive nel quaderno');
assert.equal(app.view, 'chat');
""")


def test_a_tap_on_the_veil_does_not_close_it_she_nudges() -> None:
    run_home(_HEAD + """
app.homePages.goToId('app', { animated: false });
await tick(10);
tap();
await tick(30);
over().dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
assert.equal(app.chatOver, true, 'un tocco sul velo ha chiuso la chat');
assert.ok(app.jenny.el.classList.contains('nudge'), 'lei non ha detto «chiudi da me»');
""")


def test_on_the_chat_page_a_tap_says_hello() -> None:
    run_home(_HEAD + """
tap();
await tick(10);
assert.equal(app.chatOver, false, 'la chat e\\u2019 salita sopra se stessa');
assert.ok(app.jenny.el.classList.contains('hello'));
""")


def test_an_image_opened_from_the_chat_closes_before_the_chat() -> None:
    run_home(_HEAD + """
app.homePages.goToId('app', { animated: false });
await tick(10);
tap();
await tick(30);
const lb = document.createElement('div');
lb.className = 'image-lightbox';
document.body.appendChild(lb);
app.handleHardwareBack();
assert.equal(document.querySelector('.image-lightbox'), null);
assert.equal(app.chatOver, true, 'Indietro ha chiuso la chat sotto l\\u2019immagine');
""")


def test_jenny_stands_on_the_composer_of_the_chat_over_the_page() -> None:
    run_home(_HEAD + """
app.homePages.goToId('settings', { animated: false });
await tick(10);
const floor = () => document.documentElement.style.getPropertyValue('--home-composer-h');
assert.equal(floor(), '66px');
tap();
await tick(30);
assert.notEqual(floor(), '66px', 'con la chat sopra lei sta ancora sul pavimento delle pagine');
app.handleHardwareBack();
await tick(10);
assert.equal(floor(), '66px', 'chiusa la chat, il pavimento non e\\u2019 tornato quello della pagina');
""")


def test_ui_view_describes_the_page_under_the_chat() -> None:
    run_home(_HEAD + """
app.homePages.goToId('settings', { animated: false });
await tick(10);
tap();
await tick(30);
const payload = await app.uiQuery._collect();
assert.equal(payload.view, 'home:settings');
assert.equal(payload.over, 'chat');
""")
