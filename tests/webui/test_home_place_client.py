"""Il punto di lettura della chat, e il «↓» per tornare in fondo.

Fino al 10/10/2026 la chat della casa tornava sempre in fondo: aprire e chiudere
Jenny (il velo), tornare da una stanza, rileggere il filo. Chi stava rileggendo
la storia la perdeva. E se sotto arrivava una risposta mentre eri risalito, non
c'era niente che lo dicesse.

jsdom non misura niente: le misure del filo si fissano a mano, ed e' il modo
onesto di provare la regola senza fingere un layout.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, frame, $ } from './boot.mjs';
const app = await boot();
const el = $('home-thread');
let top = 0;
Object.defineProperty(el, 'clientHeight', { get: () => 400, configurable: true });
Object.defineProperty(el, 'scrollHeight', { get: () => 2000, configurable: true });
Object.defineProperty(el, 'scrollTop', {
  get: () => top, set: (v) => { top = Math.max(0, Math.min(1600, v)); }, configurable: true,
});
const scrollTo = (v) => { el.scrollTop = v; el.dispatchEvent(new window.Event('scroll')); };
const fab = () => el.parentElement.querySelector('.home-scroll-fab');
"""


def test_the_veil_brings_the_chat_back_where_you_were_reading() -> None:
    run_home(_HEAD + """
scrollTo(1600);
scrollTo(500);
assert.equal(fab().classList.contains('visible'), true, 'risalito, il \\u2193 non c\\u2019e\\u2019');
app.homePages.goToId('app');
await tick(300);
app.openChatOver();
await tick(20);
assert.equal(el.scrollTop, 500, 'il velo ha riportato in fondo chi stava rileggendo');
app.closeChatOver();
await tick(20);
assert.equal(el.scrollTop, 500, 'richiuso, il punto e\\u2019 perso');
""")


def test_who_was_at_the_bottom_stays_at_the_bottom() -> None:
    run_home(_HEAD + """
scrollTo(1600);
app.homePages.goToId('app');
await tick(300);
app.openChatOver();
await tick(20);
assert.equal(el.scrollTop, 1600);
assert.equal(fab().classList.contains('visible'), false);
""")


def test_something_new_below_lights_the_arrow_instead_of_pulling_you_down() -> None:
    run_home(_HEAD + """
scrollTo(1600);
scrollTo(500);
frame({ event: 'message', chat_id: 'default', text: 'arrivata adesso' });
await tick(10);
assert.equal(el.scrollTop, 500, 'una risposta ti ha strappato via dalla lettura');
assert.equal(fab().classList.contains('has-new'), true, 'il \\u2193 non dice che e\\u2019 arrivato qualcosa');
fab().click();
assert.equal(el.scrollTop, 1600);
assert.equal(fab().classList.contains('visible'), false);
""")
