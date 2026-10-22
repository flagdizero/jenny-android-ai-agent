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


def test_the_page_under_is_blurred_and_comes_back_sharp() -> None:
    run_home(_HEAD + """
app.homePages.goToId('app', { animated: false });
await tick(10);
const blurred = () => Array.from(document.querySelectorAll('.home-beneath'));
tap();
await tick(30);
assert.ok(blurred().includes(document.querySelector('.home-showcase')), 'la pagina sotto non e\\u2019 sfocata');
assert.ok(!blurred().includes(over()), 'il velo sfoca la chat');
assert.ok(!blurred().includes(app.jenny.el), 'Jenny e\\u2019 sfocata');
assert.ok(!blurred().some((el) => el.tagName === 'DIALOG'), 'un dialogo aperto da sotto sarebbe sfocato');
tap();
await tick(10);
assert.deepEqual(blurred(), [], 'la pagina e\\u2019 rimasta sfocata');
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
assert.equal(app.jenny.motion('squash'), 'nudge', 'lei non ha detto «chiudi da me»');
""")


def test_in_a_notebook_the_head_is_the_board_path_with_her_at_the_end() -> None:
    """La fila delle pagine resta sotto il velo, sfocata: senza una testa non
    si sapeva se si scriveva in un quaderno o nella chat di sempre. Dal
    22/10/2026 nella chat di un quaderno e' il percorso della bacheca piu' una
    voce: «‹ QUADERNI › ● ORTO › JENNY»."""
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
tap();
await tick(30);
const head = $('home-over-head');
assert.equal(over().firstElementChild, head, 'la testa non sta in cima al velo');
assert.ok(head.classList.contains('is-path'), 'la testa non ha la geometria del percorso');
assert.equal($('home-over-back').hidden, false, 'manca la freccia');
assert.equal($('home-over-notebooks').hidden, false, 'manca QUADERNI');
assert.equal($('home-over-notebooks-name').textContent, $('home-path-root-name').textContent,
             'QUADERNI non dice quel che dice la bacheca');
assert.equal($('home-over-notebook').hidden, false, 'manca il quaderno');
assert.equal($('home-over-notebook-name').textContent, 'orto');
assert.equal($('home-over-name').textContent, app._personalName, 'l\u2019ultima voce non e\u2019 lei');
assert.equal($('home-over-dot').hidden, false, 'manca il pallino del quaderno');
assert.ok($('home-over-dot').style.background.includes('--spine-'), 'il pallino non ha il colore del quaderno');
assert.equal(head.style.getPropertyValue('--path-line'), $('home-over-dot').style.background,
             'la riga sotto di lei non e\u2019 del colore del quaderno');
for (const sep of document.querySelectorAll('[data-over-sep]')) assert.equal(sep.hidden, false);
assert.ok($('home-over-back').getAttribute('aria-label'), 'la freccia non dice cosa fa');
assert.ok($('home-over-notebook').getAttribute('aria-label').includes('orto'));
""")


def test_the_head_of_the_everyday_chat_is_only_her_name() -> None:
    run_home(_HEAD + """
app.homePages.goToId('app', { animated: false });
await tick(10);
tap();
await tick(30);
assert.equal($('home-over-name').textContent, app._personalName);
assert.equal($('home-over-head').classList.contains('is-path'), false);
for (const id of ['home-over-back', 'home-over-notebooks', 'home-over-notebook', 'home-over-dot']) {
  assert.equal($(id).hidden, true, id + ' nella chat di sempre');
}
for (const sep of document.querySelectorAll('[data-over-sep]')) assert.equal(sep.hidden, true);
assert.equal($('home-over-head').style.getPropertyValue('--path-line'), '');
""")


def test_the_personal_chat_over_a_board_has_no_path() -> None:
    """Un avviso della chat di sempre toccato sulla bacheca la porta su sopra
    il quaderno: la testa segue la conversazione, non la pagina sotto, o si
    leggerebbe «QUADERNI › ORTO» scrivendo nella personale."""
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
assert.equal(app.view, 'board');
app.openChat(null);
await tick(40);
assert.equal(app.chatOver, true);
assert.equal(app.currentKey(), 'websocket:default');
assert.equal($('home-over-notebook').hidden, true, 'la personale dice il quaderno');
assert.equal($('home-over-head').classList.contains('is-path'), false);
""")


def test_the_head_follows_the_conversation_from_a_notebook_back_to_hers() -> None:
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
app.handleHardwareBack();
await tick(10);
app.homePages.goToId('app', { animated: false });
await tick(10);
tap();
await tick(40);
assert.equal(app.currentKey(), 'websocket:default');
assert.equal($('home-over-name').textContent, app._personalName);
assert.equal($('home-over-notebook').hidden, true, 'la testa dice ancora il quaderno');
assert.equal($('home-over-dot').hidden, true);
""")


def test_the_arrow_closes_it_where_you_were() -> None:
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
await app.openPage('semina.md', 'Semina');
await tick(30);
assert.equal(app.view, 'reader');
tap();
await tick(30);
$('home-over-back').click();
await tick(20);
assert.equal(app.chatOver, false);
assert.equal(app.view, 'reader', 'la freccia non ha lasciato la pagina sotto');
""")


def test_the_notebook_takes_you_to_its_board_even_from_a_page() -> None:
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
await app.openPage('semina.md', 'Semina');
await tick(30);
tap();
await tick(30);
$('home-over-notebook').click();
await tick(40);
assert.equal(app.chatOver, false);
assert.equal(app.view, 'board', 'il quaderno non porta alla bacheca');
assert.equal(app.currentKey(), 'project:orto', 'si e\u2019 usciti dal quaderno');
""")


def test_the_notebook_on_its_board_just_closes_the_chat() -> None:
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
tap();
await tick(30);
$('home-over-notebook').click();
await tick(20);
assert.equal(app.chatOver, false);
assert.equal(app.view, 'board');
""")


def test_notebooks_takes_you_to_the_list() -> None:
    run_home(_HEAD + """
await app.switchConversation('project:orto');
await tick(30);
tap();
await tick(30);
$('home-over-notebooks').click();
await tick(40);
assert.equal(app.chatOver, false);
assert.equal(app.view, 'chat', 'si e\u2019 rimasti nella stanza del quaderno');
assert.equal(app.homePages.order[app.homePages.index], 'notebooks', 'QUADERNI non porta all\u2019elenco');
""")


def test_a_tap_on_the_title_is_a_tap_on_the_veil() -> None:
    run_home(_HEAD + """
app.homePages.goToId('app', { animated: false });
await tick(10);
tap();
await tick(30);
$('home-over-name').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
assert.equal(app.chatOver, true, 'un tocco sul titolo ha chiuso la chat');
assert.equal(app.jenny.motion('squash'), 'nudge', 'il titolo si comporta da comando');
""")


def test_on_the_chat_page_a_tap_says_hello() -> None:
    run_home(_HEAD + """
tap();
await tick(10);
assert.equal(app.chatOver, false, 'la chat e\\u2019 salita sopra se stessa');
assert.equal(app.jenny.motion('squash'), 'greet');
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
