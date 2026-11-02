"""Un link dentro un contenuto non deve poter ricaricare il guscio.

Il difetto da cui veniamo (26/09/2026): la chat della casa scriveva il markdown
di Jenny in ``innerHTML`` e il suo solo ascoltatore di click guardava
``.home-copy``. Un ``[x](workshop.html)`` o un ``[x](?mode=chat)`` erano quindi
navigazioni di main frame vere, e ``MainActivity.isShellDocument`` le lascia
dentro la WebView: la casa si ricaricava **senza** il fragment ``#bs=`` —
``/webui/bootstrap`` 401, API e websocket morti finche' l'app non veniva uccisa.
Il lettore delle pagine aveva la meta' simmetrica: ogni ``http(s)://`` passava per
esterno, anche quello all'origine del gateway, e ``window.open`` lo caricava in
casa.

La regola e' una e sta in ``shared/content-link.js``; qui si misura il modulo
vero, e poi i tre posti che lo usano girando in node il loro codice vero.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.home_dom import requires_jsdom, run_home
from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CONTENT_LINK_JS = ASSETS / "shared" / "content-link.js"
HOME_CHAT_JS = ASSETS / "home-chat.js"
WORKSHOP_CHAT_JS = ASSETS / "mobile-chat.js"

pytestmark = requires_node

# Un'origine qualunque del gateway: quel che conta e' che sia la stessa della pagina.
_HERE = "{ href: 'http://127.0.0.1:18790/html-mobile/index.html', origin: 'http://127.0.0.1:18790' }"

# Tutto cio' che riporta a un documento-guscio, o comunque al gateway.
_SHELL_LINKS = (
    "workshop.html",
    "index.html",
    "?mode=chat",
    "./",
    "/html-mobile/",
    "/html-mobile/workshop.html?x=1",
    "http://127.0.0.1:18790/html-mobile/workshop.html",
    "//127.0.0.1:18790/html-mobile/",
    "/api/settings",
    "note.md",
    "javascript:alert(1)",
)


def _module_harness() -> str:
    return (
        "import assert from 'node:assert/strict';\n"
        f"const {{ contentLinkTarget, openOutsideWebView }} = await import('{CONTENT_LINK_JS.as_uri()}');\n"
        f"const HERE = {_HERE};\n"
    )


def test_no_link_to_the_gateway_is_openable() -> None:
    run_js(
        _module_harness()
        + f"""
      for (const href of {list(_SHELL_LINKS)!r}) {{
        assert.equal(contentLinkTarget(href, HERE), null, href);
      }}
      assert.equal(contentLinkTarget('', HERE), null);
    """
    )


def test_another_origin_and_mail_go_outside() -> None:
    run_js(
        _module_harness()
        + """
      assert.deepEqual(contentLinkTarget('https://example.org/a', HERE),
                       { kind: 'external', href: 'https://example.org/a' });
      // Stesso host, altra porta: e' un'altra origine, non il gateway.
      assert.equal(contentLinkTarget('http://127.0.0.1:8080/', HERE).kind, 'external');
      assert.equal(contentLinkTarget('mailto:a@b.c', HERE).kind, 'external');
      assert.equal(contentLinkTarget('tel:+390000', HERE).kind, 'external');
      assert.deepEqual(contentLinkTarget('#sintomi', HERE), { kind: 'hash', id: 'sintomi' });
    """
    )


def test_without_a_known_origin_the_web_stays_inert() -> None:
    """Nel dubbio non si apre: senza sapere qual e' il gateway, un http potrebbe
    essere lui."""
    run_js(
        _module_harness()
        + """
      assert.equal(contentLinkTarget('https://example.org/a', undefined), null);
      assert.equal(contentLinkTarget('mailto:a@b.c', undefined).kind, 'external');
    """
    )


# ── I due gusci, col loro codice vero ────────────────────────────────────────

_CLICK_HARNESS = """
import assert from 'node:assert/strict';
const {
  contentLinkHref, contentLinkOf, contentLinkTarget, findContentAnchor, openOutsideWebView,
  openContentLink,
} = await import('__MODULE__');
globalThis.window = {
  location: __HERE__,
  opened: [],
  open(href) { this.opened.push(href); },
};
globalThis.CSS = { escape: (s) => s };
const toasts = [];
const showToast = (msg, kind) => toasts.push([msg, kind]);
const i18n = { t: (key) => key };
/* La scheda di un posto (`shared/places.js`) ha il suo banco: qui nessun tocco e' suo. */
const openPlaceFromClick = () => false;

/* Un link finto: risponde a `closest` come un elemento vero, cioe' a ogni
   selettore della lista che lo prende — `a[href]` di un tempo e quello di
   `contentLinkOf`, che vuole anche `<area href>` e i link SVG. */
function anchor(href, inside = true, tag = 'a') {
  return {
    tag,
    inside,
    getAttribute: (name) => (name === 'href' ? href : null),
    getAttributeNS: () => null,
    closest(sel) {
      const parts = sel.split(',').map((p) => p.trim());
      return parts.includes(`${tag}[href]`) || parts.includes('[href]') ? this : null;
    },
  };
}
function click(target) {
  const e = {
    target: { closest: (sel) => target.closest(sel) },
    defaultPrevented: false,
    prevented: 0,
    preventDefault() { this.prevented += 1; this.defaultPrevented = true; },
  };
  return e;
}
"""


def _click_harness() -> str:
    return _CLICK_HARNESS.replace("__MODULE__", CONTENT_LINK_JS.as_uri()).replace("__HERE__", _HERE)


def _home_chat() -> str:
    src = HOME_CHAT_JS.read_text(encoding="utf-8")
    return (
        _click_harness()
        + """
const copied = [];
const scrolled = [];
const heading = { scrollIntoView() { scrolled.push('h2'); } };
const chat = {
  el: {
    contains: (node) => node.inside !== false,
    querySelector: (sel) => (sel.startsWith('#user-content-sezione') ? heading : null),
  },
  _copy(node) { copied.push(node); },
  """
        + member(src, "_onClick")
        + ",\n  "
        + member(src, "_openLink")
        + "\n};\n"
    )


def test_the_home_chat_never_lets_a_gateway_link_navigate() -> None:
    """Il caso segnalato: ogni link che riporta al guscio e' annullato, nessuna
    apertura parte, e il tocco dice che il link non si apre."""
    run_js(
        _home_chat()
        + f"""
      for (const href of {list(_SHELL_LINKS)!r}) {{
        toasts.length = 0;
        const e = click(anchor(href));
        chat._onClick(e);
        assert.equal(e.prevented, 1, href + ': click non annullato');
        assert.deepEqual(window.opened, [], href + ': aperto fuori');
        assert.deepEqual(toasts, [['common.linkNotOpenable', 'info']], href);
      }}
    """
    )


def test_the_home_chat_opens_the_web_outside() -> None:
    run_js(
        _home_chat()
        + """
      const e = click(anchor('https://example.org/x'));
      chat._onClick(e);
      assert.equal(e.prevented, 1);
      assert.deepEqual(window.opened, ['https://example.org/x']);
      assert.deepEqual(toasts, []);
    """
    )


def test_the_home_chat_does_not_let_an_image_map_navigate() -> None:
    """Un ``<area href>`` non e' un ``a[href]``: il tocco passava e navigava il
    frame principale. Oggi il sanificatore toglie ``<area>``; questo e' il
    secondo cancello."""
    run_js(
        _home_chat()
        + """
      const e = click(anchor('workshop.html', true, 'area'));
      chat._onClick(e);
      assert.equal(e.prevented, 1, 'il tocco su un <area> non e\u2019 stato annullato');
      assert.deepEqual(toasts, [['common.linkNotOpenable', 'info']]);
    """
    )


def test_the_home_chat_scrolls_to_the_sanitized_anchor() -> None:
    """``#sezione`` porta al titolo che il sanificatore ha chiamato
    ``user-content-sezione``."""
    run_js(
        _home_chat()
        + """
      chat._onClick(click(anchor('#sezione')));
      assert.deepEqual(scrolled, ['h2']);
      assert.deepEqual(toasts, []);
    """
    )


def test_the_home_chat_copy_button_still_copies() -> None:
    run_js(
        _home_chat()
        + """
      const msg = { id: 'bolla' };
      const btn = { closest: (sel) => (sel === '.home-copy' ? btn : sel === '.home-msg' ? msg : null) };
      chat._onClick(click(btn));
      assert.deepEqual(copied, [msg]);
    """
    )


def test_the_home_chat_leaves_alone_a_link_that_already_has_an_owner() -> None:
    run_js(
        _home_chat()
        + """
      const e = click(anchor('workshop.html'));
      e.defaultPrevented = true;
      chat._onClick(e);
      assert.equal(e.prevented, 0);
      assert.deepEqual(toasts, []);
    """
    )


def test_the_workshop_chat_applies_the_same_rule() -> None:
    src = WORKSHOP_CHAT_JS.read_text(encoding="utf-8")
    run_js(
        _click_harness()
        + """
const scrolled = [];
const chat = {
  _scrollToChatAnchor(id) { scrolled.push(id); },
  """
        + member(src, "_handleContentLink")
        + f"""
}};
      for (const href of {list(_SHELL_LINKS)!r}) {{
        const e = click(anchor(href));
        chat._handleContentLink(e, anchor(href));
        assert.equal(e.prevented, 1, href);
      }}
      assert.deepEqual(window.opened, []);
      assert.equal(toasts.length, {len(_SHELL_LINKS)});
      chat._handleContentLink(click(anchor('#su')), anchor('#su'));
      assert.deepEqual(scrolled, ['su']);
      chat._handleContentLink(click(anchor('https://example.org/')), anchor('https://example.org/'));
      assert.deepEqual(window.opened, ['https://example.org/']);
    """
    )


# ── La chat della casa vera, in jsdom, con DOMPurify e marked veri ───────────

_VENDOR = ASSETS / "vendor"
_HOME_WITH_LIBS = f"""
import assert from 'node:assert/strict';
import {{ createRequire }} from 'node:module';
import {{ boot, tick, frame, toasts }} from './boot.mjs';
const require = createRequire(import.meta.url);
globalThis.DOMPurify = require({json.dumps(str(_VENDOR / "dompurify@3" / "purify.min.js"))})(window);
globalThis.marked = require({json.dumps(str(_VENDOR / "marked@15.0.7" / "marked.min.js"))});
window.DOMPurify = globalThis.DOMPurify;
window.marked = globalThis.marked;
const scrolled = [];
window.HTMLElement.prototype.scrollIntoView = function () {{ scrolled.push(this); }};
const app = await boot();
const tap = (node) => {{
  const e = new window.MouseEvent('click', {{ bubbles: true, cancelable: true }});
  node.dispatchEvent(e);
  return e;
}};
"""


@requires_jsdom
def test_an_anchor_in_a_home_answer_scrolls_to_its_heading() -> None:
    run_home(
        _HOME_WITH_LIBS
        + """
frame({ event: 'stream_end', chat_id: 'default', turn_id: 't1',
        text: '<h2 id="semina">Semina</h2>\\n\\n[vai](#semina)' });
await tick(10);
const thread = document.getElementById('home-thread');
const heading = thread.querySelector('h2');
assert.equal(heading?.id, 'user-content-semina', thread.innerHTML);
const e = tap(thread.querySelector('a[href="#semina"]'));
assert.equal(e.defaultPrevented, true);
assert.equal(scrolled.length, 1, 'l\u2019ancora non ha fatto scorrere: ' + JSON.stringify(toasts()));
assert.equal(scrolled[0], heading);
"""
    )


@requires_jsdom
def test_a_home_image_map_that_got_through_does_not_navigate() -> None:
    """Il sanificatore toglie ``<area>``; se una sua versione futura lo lasciasse
    passare, il tocco non deve comunque navigare il frame principale."""
    run_home(
        _HOME_WITH_LIBS
        + """
frame({ event: 'stream_end', chat_id: 'default', turn_id: 't2', text: 'Mappa' });
await tick(10);
const block = document.querySelector('#home-thread .home-block');
block.insertAdjacentHTML('beforeend', '<map name="m"><area shape="rect" coords="0,0,9,9" href="workshop.html"></map>');
const e = tap(block.querySelector('area'));
assert.equal(e.defaultPrevented, true, 'il tocco su un <area> naviga il frame principale');
"""
    )
