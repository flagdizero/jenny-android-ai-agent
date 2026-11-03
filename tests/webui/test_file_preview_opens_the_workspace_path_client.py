"""«Apri nell'editor» dall'anteprima apre il file dal workspace.

La correzione recente per cui
``_renderFilePreview`` (``mobile-chat.js``) apre ``data.workspace_path`` — il
percorso dal workspace che il server rimanda, dove quello scritto in chat e'
relativo al quaderno (e spesso alla sua ``wiki/``) — non aveva un test: una
mutazione su quella riga restava verde. Il metodo vero, su un DOM finto.
"""

from __future__ import annotations

import json

import pytest
from support.js_harness import ASSETS, function, member, requires_node, run_js

pytestmark = requires_node

CHAT_SRC = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
UTILS_SRC = (ASSETS / "shared" / "utils.js").read_text(encoding="utf-8")


def _opened(payload: dict, *, html: bool = False):
    out = run_js(
        f"""
{function(UTILS_SRC, "escapeHtml")}
{function(UTILS_SRC, "sizeText")}
const i18n = {{ locale: 'it', t: (k) => k }};
const sessionManager = {{ currentKey: 'project:orto' }};
const api = {{ fetchFilePreview: async () => ({json.dumps(payload)}) }};
function el() {{
  const node = {{
    className: '', listeners: {{}}, children: [],
    set innerHTML(v) {{ this._html = v; }},
    get innerHTML() {{ return this._html || ''; }},
    remove() {{}},
    addEventListener(t, fn) {{ this.listeners[t] = fn; }},
    querySelector(sel) {{
      if (sel === '.file-preview') return null;
      return (this.parts ||= {{}})[sel] ||= el();
    }},
    appendChild(c) {{ this.children.push(c); return c; }},
  }};
  return node;
}}
globalThis.document = {{ createElement: () => el() }};
const opened = [];
const chat = {{
  async _openFileInWorkspace(p) {{ opened.push(p); }},
  {member(CHAT_SRC, "_renderFilePreview")}
}};
const container = el();
await chat._renderFilePreview('entities/Pothos.md', container);
const preview = container.children[0];
if ({json.dumps(html)}) {{ process.stdout.write(JSON.stringify(preview.innerHTML)); process.exit(0); }}
await preview.parts['.file-preview-action'].listeners.click({{ preventDefault() {{}} }});
process.stdout.write(JSON.stringify(opened));
"""
    )
    return json.loads(out)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"content": "x", "workspace_path": "wikis/orto/wiki/entities/Pothos.md"},
         "wikis/orto/wiki/entities/Pothos.md"),
        ({"content": "x"}, "entities/Pothos.md"),
    ],
    ids=["from-the-server", "fallback"],
)
def test_the_editor_opens_what_the_server_resolved(payload: dict, expected: str) -> None:
    assert _opened(payload) == [expected]


def test_the_language_from_the_server_is_escaped() -> None:
    """``language`` arriva dal server e finiva nell'``innerHTML`` com'era."""
    html = _opened({"content": "x", "language": '<img src=x onerror="alert(1)">'}, html=True)
    assert "<img" not in html
    assert "&lt;img" in html
    close = html[html.index('class="file-preview-close"') :]
    assert 'aria-hidden="true"' in close[: close.index("</button>")], "l'icona del ✕ si legge"
