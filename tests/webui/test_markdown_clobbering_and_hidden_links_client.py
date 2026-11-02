"""Un contenuto non deve poter rubare un id del guscio, ne' nascondere un link.

Due difetti, entrambi in ``shared/markdown.js``:

- DOMPurify di serie conserva ``id`` e ``name``. Una risposta con
  ``<span id="oc-confirm-ok">`` metteva nel documento un secondo elemento con
  l'id del «Conferma» vero, e il dialogo restava muto (*clobbering*, non XSS).
- ``<area href>`` (dentro una ``<map>``) e ``<a xlink:href>`` dentro
  un ``<svg>`` sono link che non sono ``a[href]``: le chat li lasciavano
  passare, e il tocco navigava il frame principale verso un documento del
  guscio, senza ``#bs=`` — la SPA de-autenticata.

La prima meta' dei test gira col solo node e fissa la **configurazione**: e'
quella che la CI vede. La seconda usa DOMPurify e marked veri in jsdom, e si
salta senza jsdom (non e' una dipendenza del repo: ``NODE_PATH`` lo porta). Il
``closest('[*|href]')`` su un ``xlink:href`` jsdom lo sbaglia; lo si e' provato
in Chromium, a mano (v. il rapporto della correzione).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from support.js_harness import ASSETS, NODE, member, requires_node, run_js, run_module

MARKDOWN_JS = ASSETS / "shared" / "markdown.js"
CONTENT_LINK_JS = ASSETS / "shared" / "content-link.js"
PURIFY = ASSETS / "vendor" / "dompurify@3" / "purify.min.js"
MARKED = ASSETS / "vendor" / "marked@15.0.7" / "marked.min.js"

pytestmark = requires_node


def _has_jsdom() -> bool:
    if NODE is None:
        return False
    proc = subprocess.run(
        [NODE, "-e", "require('jsdom')"], capture_output=True, text=True, env=os.environ.copy()
    )
    return proc.returncode == 0


requires_jsdom = pytest.mark.skipif(not _has_jsdom(), reason="jsdom non disponibile (NODE_PATH)")


# ── La configurazione, col solo node ─────────────────────────────────────────


def test_the_config_prefixes_ids_and_forbids_image_maps() -> None:
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const {{ SANITIZE_CONFIG }} = await import('{MARKDOWN_JS.as_uri()}');\n"
        """
      assert.equal(SANITIZE_CONFIG.SANITIZE_NAMED_PROPS, true);
      for (const tag of ['area', 'map', 'form', 'input', 'button', 'textarea', 'select']) {
        assert.ok(SANITIZE_CONFIG.FORBID_TAGS.includes(tag), tag);
      }
    """
    )


def test_the_hook_drops_links_inside_svg_only() -> None:
    """Il modulo installa un hook sul DOMPurify della pagina: toglie ``href`` e
    ``xlink:href`` ai nodi SVG e non tocca quelli HTML."""
    run_js(
        "import assert from 'node:assert/strict';\n"
        """
      const hooks = [];
      globalThis.DOMPurify = {
        addHook(name, fn) { hooks.push([name, fn]); },
        sanitize: (h) => h,
      };
      """
        f"await import('{MARKDOWN_JS.as_uri()}');\n"
        """
      assert.equal(hooks.length, 1);
      const [name, fn] = hooks[0];
      assert.equal(name, 'uponSanitizeAttribute');
      const SVG = { namespaceURI: 'http://www.w3.org/2000/svg' };
      const HTML = { namespaceURI: 'http://www.w3.org/1999/xhtml' };
      for (const [node, attrName, kept] of [
        [SVG, 'href', false], [SVG, 'xlink:href', false], [SVG, 'width', true],
        [HTML, 'href', true],
      ]) {
        const data = { attrName, keepAttr: true };
        fn(node, data);
        assert.equal(data.keepAttr, kept, attrName);
      }
    """
    )


def test_an_anchor_is_found_under_its_sanitized_id() -> None:
    """``#sezione`` porta all'elemento che il sanificatore ha chiamato
    ``user-content-sezione``, e a nient'altro: non a un id del guscio."""
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const {{ findContentAnchor }} = await import('{CONTENT_LINK_JS.as_uri()}');\n"
        """
      globalThis.CSS = { escape: (s) => s };
      const asked = [];
      const root = { querySelector(sel) { asked.push(sel); return sel.includes('user-content-sezione') ? 'h2' : null; } };
      assert.equal(findContentAnchor(root, 'sezione'), 'h2');
      assert.equal(findContentAnchor(root, 'user-content-sezione'), 'h2');
      assert.equal(asked[0], '#user-content-sezione, [name="user-content-sezione"]');
      assert.equal(asked[1], asked[0], 'nessun doppio prefisso');
      assert.equal(findContentAnchor(root, ''), null);
      assert.equal(findContentAnchor(null, 'x'), null);
    """
    )


def test_the_workshop_chat_scrolls_to_the_sanitized_anchor() -> None:
    src = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const {{ findContentAnchor }} = await import('{CONTENT_LINK_JS.as_uri()}');\n"
        """
      globalThis.CSS = { escape: (s) => s };
      const toasts = [];
      const showToast = (m) => toasts.push(m);
      const i18n = { t: (k) => k };
      const scrolled = [];
      const heading = { scrollIntoView() { scrolled.push('h2'); } };
      const chat = {
        chatArea: { querySelector: (sel) => (sel.startsWith('#user-content-sezione') ? heading : null) },
      """
        + member(src, "_scrollToChatAnchor")
        + """
      };
      chat._scrollToChatAnchor('sezione');
      assert.deepEqual(scrolled, ['h2']);
      chat._scrollToChatAnchor('oc-confirm-ok');
      assert.deepEqual(toasts, ['common.linkNotOpenable']);
    """
    )


# ── DOMPurify e marked veri, in jsdom ────────────────────────────────────────


def _jsdom_run(body: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        for name in ("markdown.js", "utils.js", "content-link.js"):
            shutil.copy(ASSETS / "shared" / name, root / "shared" / name)
        entry = root / "prova.mjs"
        entry.write_text(
            "import assert from 'node:assert/strict';\n"
            "import fs from 'node:fs';\n"
            "import { createRequire } from 'node:module';\n"
            "const require = createRequire(import.meta.url);\n"
            "const { JSDOM } = require('jsdom');\n"
            "const dom = new JSDOM('<!doctype html><body>"
            '<dialog id=\"oc-confirm-dialog\"><button id=\"oc-confirm-ok\">OK</button></dialog>'
            "<div id=\"chat-area\"></div></body>', { runScripts: 'outside-only' });\n"
            f"dom.window.eval(fs.readFileSync({json.dumps(str(PURIFY))}, 'utf8'));\n"
            f"dom.window.eval(fs.readFileSync({json.dumps(str(MARKED))}, 'utf8'));\n"
            "for (const k of ['window', 'document', 'DOMPurify', 'marked', 'CSS']) {\n"
            "  globalThis[k] = dom.window[k];\n"
            "}\n"
            "globalThis.CSS = dom.window.CSS || { escape: (s) => s };\n"
            "const { renderMarkdown } = await import('./shared/markdown.js');\n"
            "const { contentLinkOf, findContentAnchor } = await import('./shared/content-link.js');\n"
            "const area = document.getElementById('chat-area');\n" + body,
            encoding="utf-8",
        )
        return run_module(entry)


@requires_jsdom
def test_an_answer_cannot_clobber_the_confirm_button() -> None:
    _jsdom_run(
        """
      area.innerHTML = renderMarkdown('Fatto! <span id="oc-confirm-ok">x</span><a name="oc-confirm-ok">y</a>');
      assert.equal(document.querySelectorAll('[id="oc-confirm-ok"]').length, 1);
      assert.equal(document.getElementById('oc-confirm-ok').tagName, 'BUTTON');
      assert.ok(area.querySelector('#user-content-oc-confirm-ok'), area.innerHTML);
    """
    )


@requires_jsdom
def test_image_maps_and_svg_links_are_not_links_any_more() -> None:
    _jsdom_run(
        """
      area.innerHTML = renderMarkdown(
        '<img src="data:," usemap="#m"><map name="m"><area shape="rect" coords="0,0,9,9" href="/api/settings"></map>'
        + '<svg width="9" height="9"><a xlink:href="/html-mobile/workshop.html"><text>a</text></a>'
        + '<a href="/html-mobile/index.html"><text>b</text></a></svg>');
      assert.equal(area.querySelector('area'), null, area.innerHTML);
      assert.equal(area.querySelector('map'), null, area.innerHTML);
      for (const a of area.querySelectorAll('svg a')) {
        assert.equal(a.getAttribute('href'), null, area.innerHTML);
        assert.equal(a.getAttribute('xlink:href'), null, area.innerHTML);
      }
      assert.ok(area.querySelector('svg text'), 'il disegno resta');
    """
    )


@requires_jsdom
def test_a_heading_anchor_still_leads_somewhere() -> None:
    """Il ``toc`` del server numera i titoli delle pagine: il prefisso non deve
    rompere un ``[vai](#sezione)``."""
    _jsdom_run(
        """
      area.innerHTML = DOMPurify.sanitize('<h2 id="sezione">T</h2><a href="#sezione">vai</a>',
        (await import('./shared/markdown.js')).SANITIZE_CONFIG);
      assert.equal(findContentAnchor(area, 'sezione')?.tagName, 'H2');
    """
    )


@requires_jsdom
def test_the_chat_gate_sees_links_that_are_not_anchors() -> None:
    """Il secondo cancello, per quel che il sanificatore lasciasse passare."""
    _jsdom_run(
        """
      const host = document.createElement('div');
      host.innerHTML = '<map name="m"><area href="/x"></map>'
        + '<svg><a href="/y"><text id="t">b</text></a></svg><p><a href="/z"><b>c</b></a></p>';
      assert.equal(contentLinkOf(host.querySelector('area'))?.tagName, 'AREA');
      assert.equal(contentLinkOf(host.querySelector('#t'))?.tagName.toLowerCase(), 'a');
      assert.equal(contentLinkOf(host.querySelector('b'))?.getAttribute('href'), '/z');
      assert.equal(contentLinkOf(host.querySelector('p')), null);
    """
    )


def test_the_hook_prefixes_internal_references_of_a_drawing() -> None:
    """I riferimenti interni di un SVG seguono il prefisso degli ``id``; un
    link resta tolto, un'immagine ``data:`` o del gateway resta."""
    run_js(
        "import assert from 'node:assert/strict';\n"
        """
      const hooks = [];
      globalThis.DOMPurify = { addHook(name, fn) { hooks.push(fn); }, sanitize: (h) => h };
      """
        f"await import('{MARKDOWN_JS.as_uri()}');\n"
        """
      const [fn] = hooks;
      const svg = (localName) => ({ namespaceURI: 'http://www.w3.org/2000/svg', localName });
      const run = (node, attrName, attrValue) => {
        const data = { attrName, attrValue, keepAttr: true };
        fn(node, data);
        return data.keepAttr ? data.attrValue : null;
      };
      assert.equal(run(svg('rect'), 'fill', 'url(#g)'), 'url(#user-content-g)');
      assert.equal(run(svg('rect'), 'stroke', "url('#g')"), "url('#user-content-g')");
      assert.equal(run(svg('line'), 'marker-end', 'url( #a )'), 'url(#user-content-a)');
      assert.equal(run(svg('rect'), 'style', 'fill:url(#g);stroke:red'),
        'fill:url(#user-content-g);stroke:red');
      assert.equal(run(svg('rect'), 'mask', 'url(https://x/#m)'), 'url(https://x/#m)');
      assert.equal(run(svg('rect'), 'fill', 'url(#user-content-g)'), 'url(#user-content-g)',
        'nessun doppio prefisso');
      assert.equal(run(svg('linearGradient'), 'href', '#base'), '#user-content-base');
      assert.equal(run(svg('textPath'), 'xlink:href', '#p'), '#user-content-p');
      assert.equal(run(svg('a'), 'href', '#g'), null, 'un link in un disegno resta tolto');
      assert.equal(run(svg('a'), 'xlink:href', '/html-mobile/index.html'), null);
      assert.equal(run(svg('image'), 'href', 'data:image/png;base64,AA'), 'data:image/png;base64,AA');
      assert.equal(run(svg('image'), 'href', '/api/media/x.png'), '/api/media/x.png');
      for (const bad of ['https://evil/x.png', '//evil/x.png', 'javascript:alert(1)', 'data:text/html,x']) {
        assert.equal(run(svg('image'), 'href', bad), null, bad);
      }
      assert.equal(run(svg('rect'), 'href', '/x'), null, 'un href che non e\\u2019 un riferimento');
      const html = { namespaceURI: 'http://www.w3.org/1999/xhtml', localName: 'div' };
      assert.equal(run(html, 'style', 'fill:url(#g)'), 'fill:url(#g)', 'fuori da un SVG non si tocca');
    """
    )


@requires_jsdom
def test_a_gradient_and_an_arrow_still_point_at_their_definitions() -> None:
    _jsdom_run(
        """
      area.innerHTML = renderMarkdown('<svg width="100" height="40"><defs>'
        + '<linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient>'
        + '<linearGradient id="h" href="#g"/>'
        + '<marker id="arrow"><path d="M0 0"/></marker></defs>'
        + '<rect fill="url(#g)" width="100" height="40"/><rect fill="url(#h)"/>'
        + '<line marker-end="url(#arrow)" x1="0" x2="10"/>'
        + '<image href="data:image/png;base64,AA"/><image href="https://x/y.png"/></svg>');
      for (const rect of area.querySelectorAll('svg rect')) {
        const id = rect.getAttribute('fill').match(/^url\\(#(.+)\\)$/)[1];
        assert.ok(area.querySelector(`[id="${id}"]`), `fill senza destinazione: ${area.innerHTML}`);
      }
      assert.equal(area.querySelector('#user-content-h').getAttribute('href'), '#user-content-g');
      const end = area.querySelector('svg line').getAttribute('marker-end').match(/^url\\(#(.+)\\)$/)[1];
      assert.equal(area.querySelector(`[id="${end}"]`)?.tagName.toLowerCase(), 'marker');
      const images = [...area.querySelectorAll('svg image')].map((i) => i.getAttribute('href'));
      assert.deepEqual(images, ['data:image/png;base64,AA', null]);
    """
    )
