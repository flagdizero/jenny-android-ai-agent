"""Un link a un file in chat apre l'editor, e Indietro torna in chat.

Il difetto (H5 della revisione profonda): ``_openFileInWorkspace`` faceva
``switchMode('workspace')`` *prima* di aprire il file. Con l'editor ancora
chiuso ``WorkspaceController.activate()`` rimandava in Memoria, e lo faceva in
modo sincrono **dentro** lo stesso ``switchMode``: il secondo ``switchMode``
annidato riscriveva la entry della chat come Memoria, poi il primo finiva e
scriveva ``AppState = workspace`` sopra la schermata di Memoria. Indietro
dall'editor portava in Memoria, e la chat era sparita dalla history.

Il banco esegue in node i metodi veri: ``switchMode``, ``ensureController``,
``navigateBack``, ``pushNav``, ``replaceNav`` del guscio; ``activate``,
``openFile``, ``_enterEditorView`` del gestore file; ``_openFileInWorkspace``
della chat. La history e' una pila finta con ``back()`` asincrono, come quella
vera.
"""

from __future__ import annotations

from support.js_harness import ASSETS, member, requires_node, run_js

pytestmark = requires_node

APP_SRC = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
WS_SRC = (ASSETS / "mobile-workspace.js").read_text(encoding="utf-8")
CHAT_SRC = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8", errors="replace")
SETTINGS_SRC = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")


def _script(body: str) -> str:
    view_of = next(
        line for line in SETTINGS_SRC.splitlines() if line.startswith("export const VIEW_OF")
    ).replace("export ", "")
    return f"""
import assert from 'node:assert/strict';

{view_of}
const views = {{}};
const view = (id) => (views[id] ||= {{ id, style: {{}} }});
const classes = new Set();
globalThis.document = {{
  getElementById: (id) => view(id),
  querySelectorAll: () => [],
  documentElement: {{
    classList: {{
      forEach: (fn) => [...classes].forEach(fn),
      remove: (c) => classes.delete(c),
      add: (c) => classes.add(c),
    }},
  }},
}};
const viewElement = (mode) => document.getElementById(`view-${{VIEW_OF[mode] || mode}}`);
globalThis.localStorage = {{ getItem: () => '1' }};
const AppState = {{ values: {{}}, set(k, v) {{ this.values[k] = v; }} }};
const showToast = () => {{}};
const i18n = {{ t: (k) => k }};

/* La history: una pila con un cursore; back() arriva dopo, come popstate. */
const stack = [null];
let cursor = 0;
globalThis.history = {{
  get state() {{ return stack[cursor] ?? null; }},
  pushState(st) {{ stack.splice(cursor + 1); stack.push(st); cursor += 1; }},
  replaceState(st) {{ stack[cursor] = st; }},
}};
globalThis.window = {{
  location: 'http://x/workshop.html',
  history: {{ back() {{ setTimeout(() => {{ cursor -= 1; shell._onPop(stack[cursor]); }}, 0); }} }},
}};
globalThis.URL = class {{ constructor() {{ this.searchParams = {{ set() {{}}, delete() {{}} }}; }} }};

class Shell {{
{member(APP_SRC, "ensureController")}
{member(APP_SRC, "switchMode")}
{member(APP_SRC, "navigateBack")}
{member(APP_SRC, "_navStateFor")}
{member(APP_SRC, "_navUrl")}
{member(APP_SRC, "pushNav")}
{member(APP_SRC, "replaceNav")}
  _onPop(state) {{ this._navPos = state.pos; this.switchMode(state.mode, false); }}
}}

class Controller {{
  activate() {{}}
}}

const api = {{
  readWorkspaceFile: async () => ({{ content: 'ciao' }}),
}};
const getFileExtension = (p) => p.split('.').pop();
const fileHelpText = () => '';
const IMAGE_EXTS = new Set(['png']);
const KNOWN_BINARY_EXTS = new Set(['zip']);

class WorkspaceController {{
  constructor() {{
    this.viewMode = 'explorer';
    this.currentDir = '';
    this.currentPath = '';
    this.viewerEl = {{ classList: {{ add() {{}}, remove() {{}} }} }};
    this.previews = [];
  }}
{member(WS_SRC, "activate")}
{member(WS_SRC, "openFile")}
{member(WS_SRC, "_enterEditorView")}
  showEditorView() {{}}
  _syncHeaderBack() {{}}
  renderBreadcrumb() {{}}
  renderCodeViewer(name) {{ this.shown = name; }}
  renderError() {{}}
  previewImage(path) {{ this.previews.push(path); }}
}}

class Chat {{
{member(CHAT_SRC, "_openFileInWorkspace")}
  activate() {{}}
}}

const shell = new Shell();
window.mobileApp = shell;
shell.controllers = {{}};
shell.controllerFactories = {{
  chat: () => new Chat(),
  workspace: () => new WorkspaceController(),
  memory: () => new Controller(),
}};
shell.currentMode = null;
shell._navPos = 0;
shell._firstRun = false;
shell.header = {{ setMode() {{}} }};
shell.drawer = {{ closeAll() {{}} }};
shell.launcher = {{ close() {{}} }};

/* Il boot: la chat e' la radice. */
shell.replaceNav(shell._navStateFor('chat'));
shell.switchMode('chat', false);
const settle = () => new Promise((r) => setTimeout(r, 5));

{body}
console.log('ok');
"""


def test_opening_a_file_from_chat_keeps_chat_under_the_editor() -> None:
    out = run_js(
        _script(
            """
await shell.controllers.chat._openFileInWorkspace('notes/a.md');
await settle();
const ws = shell.controllers.workspace;
assert.equal(ws.viewMode, 'editor');
assert.equal(ws.shown, 'a.md');
assert.equal(shell.currentMode, 'workspace');
assert.equal(AppState.values.currentMode, 'workspace');
assert.equal(views['view-workspace'].style.display, 'flex');
assert.notEqual(views['view-settings']?.style.display, 'flex');
assert.deepEqual(stack.map((s) => s.mode), ['chat', 'workspace'],
  "la entry della chat deve restare sotto l'editor");
assert.equal(cursor, 1);
"""
        )
    )
    assert out.strip() == "ok"


def test_an_image_link_does_not_leave_the_chat() -> None:
    out = run_js(
        _script(
            """
await shell.controllers.chat._openFileInWorkspace('pics/a.png');
await settle();
assert.deepEqual(shell.controllers.workspace.previews, ['pics/a.png']);
assert.equal(shell.currentMode, 'chat');
assert.equal(AppState.values.currentMode, 'chat');
assert.deepEqual(stack.map((s) => s.mode), ['chat']);
"""
        )
    )
    assert out.strip() == "ok"


def test_the_empty_file_view_sends_you_back_where_you_came_from() -> None:
    """Arrivare nella vista del file senza un file aperto: si torna indietro,
    e ``AppState`` dice dove si e' davvero."""
    out = run_js(
        _script(
            """
shell.switchMode('workspace');
await settle();
assert.equal(shell.currentMode, 'chat');
assert.equal(AppState.values.currentMode, 'chat');
assert.equal(views['view-chat'].style.display, 'flex');
assert.deepEqual(stack.slice(0, cursor + 1).map((s) => s.mode), ['chat']);
"""
        )
    )
    assert out.strip() == "ok"


def test_a_stale_root_workspace_entry_lands_on_memory_consistently() -> None:
    """Una entry ``mode: workspace`` alla radice (la history di un WebView mai
    chiuso): sotto non c'e' niente, si atterra su Memoria — e ``AppState`` lo
    dice, invece di restare su ``workspace``."""
    out = run_js(
        _script(
            """
shell._onPop({ mode: 'workspace', pos: 0 });
await settle();
assert.equal(shell.currentMode, 'memory');
assert.equal(AppState.values.currentMode, 'memory');
assert.equal(views['view-settings'].style.display, 'flex');
assert.deepEqual(stack.map((s) => s.mode), ['memory']);
"""
        )
    )
    assert out.strip() == "ok"
