"""Un percorso di file nella chat dell'officina apre l'anteprima di *quel* file.

``_makeFilePathsClickable`` trasforma i percorsi del testo in link. Il gestore del
clic leggeva ``match[1]`` al momento del tocco, ma ``match`` era la variabile del
``while ((match = re.exec(text)) !== null)``: finito il ciclo vale ``null``, quindi
**ogni** link lanciava ``TypeError: Cannot read properties of null (reading '1')``
e l'utente vedeva «An error occurred. Check console.». Misurato sul Titan 2 il
26/09/2026 su ``entities/Pothos.md`` nella chat di piante.

Due percorsi nello stesso nodo di testo: il clic sul secondo deve aprire il
secondo, cosi' il test prende anche un link che aprisse il file sbagliato.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, member, requires_node, run_js

_SOURCE = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")


@requires_node
def test_each_path_link_opens_its_own_file() -> None:
    method = member(_SOURCE, "_makeFilePathsClickable")
    script = f"""
import assert from 'node:assert/strict';

globalThis.NodeFilter = {{ SHOW_TEXT: 4 }};
const msgEl = {{ id: 'msg' }};
const made = [];

function el(tag) {{
  const node = {{
    tag, listeners: {{}}, children: [],
    addEventListener(type, fn) {{ this.listeners[type] = fn; }},
    appendChild(child) {{ this.children.push(child); return child; }},
    closest(sel) {{ return sel === '.chat-msg' ? msgEl : null; }},
  }};
  if (tag === 'a') made.push(node);
  return node;
}}

const container = {{
  replaced: null,
  replaceChild(frag, old) {{ this.replaced = frag; }},
}};
const text = {{ textContent: 'leggi entities/Pothos.md e poi wiki/serra.md grazie',
               parentNode: container }};

globalThis.document = {{
  createTreeWalker() {{
    let done = false;
    return {{
      currentNode: null,
      nextNode() {{ if (done) return false; done = true; this.currentNode = text; return true; }},
    }};
  }},
  createDocumentFragment: () => el('#fragment'),
  createTextNode: (t) => ({{ textContent: t }}),
  createElement: (tag) => el(tag),
}};

class Fake {{
  constructor() {{ this.opened = []; }}
  _renderFilePreview(path, msg) {{ this.opened.push([path, msg.id]); }}
{method}
}}

const chat = new Fake();
chat._makeFilePathsClickable(container);

assert.deepEqual(made.map((a) => a.textContent), ['entities/Pothos.md', 'wiki/serra.md']);
const ev = {{ preventDefault() {{}} }};
made[1].listeners.click(ev);
made[0].listeners.click(ev);
console.log(JSON.stringify(chat.opened));
"""
    out = run_js(script)
    assert json.loads(out.strip().splitlines()[-1]) == [
        ["wiki/serra.md", "msg"],
        ["entities/Pothos.md", "msg"],
    ]
