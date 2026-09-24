"""Una riga singola va a capo in tutte e due le chat.

L'officina configurava ``marked`` con ``breaks: true``; la casa non lo
configurava affatto, e una lista scritta a righe singole («latte\\npane») si
fondeva in un paragrafo solo — la stessa risposta si leggeva in due modi, e la
finestra flottante (Markwon, ``SoftBreakAddsNewLinePlugin``) stava con
l'officina. Dal 24/09/2026 le opzioni stanno in ``shared/markdown.js``.

Con la libreria vera, quella vendorizzata, non un finto.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from support.js_harness import NODE, requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
MARKED = ASSETS / "vendor" / "marked@15.0.7" / "marked.min.js"
pytestmark = requires_node


def _casa_render(text: str) -> str:
    """``renderMarkdown`` come la chiama la casa: import dal modulo, nessuna
    configurazione a mano prima."""
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
            "globalThis.DOMPurify = { sanitize: (h) => h };\n"
            "const { renderMarkdown } = await import('./shared/markdown.js');\n"
            f"process.stdout.write(JSON.stringify(renderMarkdown({json.dumps(text)})));\n",
            encoding="utf-8",
        )
        return json.loads(run_module(entry))


def test_a_single_newline_is_a_line_break_in_the_casa() -> None:
    html = _casa_render("latte\npane\nuova")
    assert html.count("<br>") == 2, html


def test_a_blank_line_is_still_a_new_paragraph() -> None:
    html = _casa_render("uno\n\ndue")
    assert html.count("<p>") == 2, html


def test_gfm_tables_still_render() -> None:
    html = _casa_render("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html, html


def test_the_workshop_renderer_survives_the_shared_configuration() -> None:
    """L'officina mette il suo renderer (highlight + «Copia») e poi il modulo
    condiviso chiama ``setOptions`` con le opzioni di base: il renderer deve
    restare. ``setOptions`` fonde, non sostituisce — qui lo si verifica."""
    script = (
        "const { createRequire } = require('node:module');\n"
        f"const marked = require({json.dumps(str(MARKED))});\n"
        "const r = new marked.Renderer();\n"
        "r.code = () => '<div class=\"chat-code-block\">X</div>';\n"
        "marked.setOptions({ renderer: r, gfm: true, breaks: true });\n"
        "marked.setOptions({ gfm: true, breaks: true });\n"
        "process.stdout.write(marked.parse('```js\\nx\\n```\\n\\na\\nb'));\n"
    )
    proc = subprocess.run([str(NODE), "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert 'class="chat-code-block"' in proc.stdout, proc.stdout
    assert "a<br>b" in proc.stdout, proc.stdout
