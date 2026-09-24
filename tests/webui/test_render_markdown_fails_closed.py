"""``renderMarkdown`` sanifica, e se non può farlo ripiega sul testo semplice.

Era scritta due volte (casa e officina), con la stessa regola di sicurezza:
senza ``marked`` o senza ``DOMPurify`` il testo esce con l'HTML neutralizzato,
mai iniettato così com'è; un errore di parse fa lo stesso. Dal 24/09/2026 la
funzione è una sola (``shared/markdown.js``); il banco gira su ogni sorgente
che la definisce o la usa.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")

ESCAPE = """
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
"""


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^(?:export )?function {name}\(.*?^\}}$", source)
    assert m, name
    return m.group(0).removeprefix("export ")


def _sources() -> dict[str, str]:
    """Da dove prendere la funzione: il modulo condiviso se c'è, le copie se no."""
    shared = ASSETS / "shared" / "markdown.js"
    if shared.exists():
        return {"shared": _function(shared.read_text(encoding="utf-8"), "renderMarkdown")}
    return {
        name: _function((ASSETS / name).read_text(encoding="utf-8"), "renderMarkdown")
        for name in ("casa-chat.js", "mobile-chat.js")
    }


def _run(fn_src: str, setup: str, text: str) -> str:
    script = (
        ESCAPE + "function initMarked() {}\n" + setup + "\n" + fn_src
        + f"\nprocess.stdout.write(JSON.stringify(renderMarkdown({json.dumps(text)})));"
    )
    proc = subprocess.run([str(_NODE), "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


MARKED = "globalThis.marked = { parse: (t) => '<p>' + t + '</p><script>x</script>' };"
PURIFY = "globalThis.DOMPurify = { sanitize: (h) => h.replace(/<script>.*?<\\/script>/g, '') };"
QUIET = "console.error = () => {};"


@pytest.mark.parametrize("where", list(_sources()))
def test_with_both_libraries_it_parses_and_sanitizes(where: str) -> None:
    assert _run(_sources()[where], MARKED + PURIFY, "ciao") == "<p>ciao</p>"


@pytest.mark.parametrize("where", list(_sources()))
def test_without_the_sanitizer_it_never_injects_html(where: str) -> None:
    assert _run(_sources()[where], MARKED, "<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"


@pytest.mark.parametrize("where", list(_sources()))
def test_without_marked_it_is_escaped_text(where: str) -> None:
    assert _run(_sources()[where], PURIFY, "<i>y</i>") == "&lt;i&gt;y&lt;/i&gt;"


@pytest.mark.parametrize("where", list(_sources()))
def test_a_parse_error_falls_back_to_escaped_text(where: str) -> None:
    broken = "globalThis.marked = { parse: () => { throw new Error('boom'); } };"
    assert _run(_sources()[where], broken + PURIFY + QUIET, "<u>z</u>") == "&lt;u&gt;z&lt;/u&gt;"


def test_both_chats_use_the_shared_function() -> None:
    """Una copia sola: nessuna delle due chat se ne riscrive una sua."""
    casa = (ASSETS / "casa-chat.js").read_text(encoding="utf-8")
    officina = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    assert "import { renderMarkdown } from './shared/markdown.js';" in casa
    assert "function renderMarkdown" not in casa
    assert "from './shared/markdown.js';" in officina
    body = _function(officina, "renderMarkdown")
    assert "initMarked();" in body and "renderSafeMarkdown(text)" in body
    assert "DOMPurify" not in body, "la regola di sicurezza sta in shared/markdown.js"
