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

from pathlib import Path

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
const { contentLinkTarget, openOutsideWebView } = await import('__MODULE__');
globalThis.window = {
  location: __HERE__,
  opened: [],
  open(href) { this.opened.push(href); },
};
globalThis.CSS = { escape: (s) => s };
const toasts = [];
const showToast = (msg, kind) => toasts.push([msg, kind]);
const i18n = { t: (key) => key };

function anchor(href, inside = true) {
  return {
    tag: 'a',
    inside,
    getAttribute: (name) => (name === 'href' ? href : null),
    closest(sel) { return sel === 'a[href]' ? this : null; },
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
const chat = {
  el: {
    contains: (node) => node.inside !== false,
    querySelector: () => null,
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
