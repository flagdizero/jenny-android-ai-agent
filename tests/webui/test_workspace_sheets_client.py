"""I due menu del Workspace sullo stesso foglio: cosa elencano, cosa fanno.

Il menu contestuale di un file o cartella (``showContextSheet``) e il menu
«Nuovo» (``_showNewMenu``) usano lo stesso ``<dialog>`` (``ws-context-sheet``),
e lo montavano ognuno con la sua copia: pulsanti, Annulla, backdrop con la
finestra di grazia da 400 ms, sgancio alla chiusura. Qui si esegue il
montaggio su un DOM finto e si fissa che le due strade facciano la stessa
cosa, ognuna con le sue voci.

I metodi si estraggono dal sorgente e girano in node su una classe finta, come
in ``test_chat_scope_client.py``: il modulo intero si porta dietro mezza WebUI.
Si estrae anche ``_apriFoglio`` se c'è, così il banco vale prima e dopo che il
montaggio diventi uno.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
WORKSPACE_JS = ASSETS / "mobile-workspace.js"

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _members(source: str) -> str:
    out = []
    for name in ("showContextSheet", "_showNewMenu", "_apriFoglio"):
        m = re.search(rf"\n  ({re.escape(name)}\([^)]*\)\s*\{{.*?\n  \}})", source, re.S)
        if m:
            out.append(m.group(1))
        else:
            assert name == "_apriFoglio", f"{name} non trovato"
    return "\n".join(out)


def _harness() -> str:
    source = WORKSPACE_JS.read_text(encoding="utf-8")
    return f"""
import assert from 'node:assert/strict';
const i18n = {{ t: (k) => k }};
function fileHelpText(path) {{ return path === 'SOUL.md' ? 'aiuto:' + path : ''; }}

/* Un <dialog> e i suoi figli, ridotti a quel che il montaggio tocca. */
function el(id) {{
  const e = {{ id, textContent: '', innerHTML: '', on: {{}}, onclick: null, opened: 0, closed: 0 }};
  e.addEventListener = (t, fn, opts) => {{ (e.on[t] ||= []).push({{ fn, once: !!opts?.once }}); }};
  e.fire = (t, ev = {{}}) => {{
    const hs = e.on[t] || [];
    e.on[t] = hs.filter((h) => !h.once);
    hs.forEach((h) => h.fn(ev));
  }};
  return e;
}}
const sheet = el('ws-context-sheet');
sheet.showModal = () => {{ sheet.opened += 1; }};
sheet.close = () => {{ sheet.closed += 1; sheet.fire('close'); }};
const nodes = {{
  'ws-context-sheet': sheet,
  'ws-context-title': el('title'),
  'ws-context-desc': el('desc'),
  'ws-context-actions': el('actions'),
  'ws-context-cancel': el('cancel'),
}};
/* I pulsanti nascono da innerHTML: qui si leggono i `data-action` dal testo. */
let buttons = [];
nodes['ws-context-actions'].querySelectorAll = () => {{
  buttons = [...nodes['ws-context-actions'].innerHTML.matchAll(/data-action="([^"]+)"/g)]
    .map((m) => {{ const b = el('btn'); b.dataset = {{ action: m[1] }}; return b; }});
  return buttons;
}};
globalThis.document = {{ getElementById: (id) => nodes[id] ?? null }};
const actions = () => buttons.map((b) => b.dataset.action);

class Workspace {{
  constructor() {{ this.picked = []; this.currentDir = 'progetti'; }}
  handleSheetAction(action, info) {{ this.picked.push(['sheet', action, info.path]); }}
  _handleNewAction(action) {{ this.picked.push(['new', action]); }}
  {_members(source)}
}}
const ws = new Workspace();
let now = 1000;
Date.now = () => now;
"""


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_the_context_sheet_lists_the_file_actions_and_runs_the_pick() -> None:
    _run_js("""
      ws.showContextSheet({ name: 'SOUL.md', path: 'SOUL.md', kind: 'file' });
      assert.equal(nodes['ws-context-title'].textContent, 'SOUL.md');
      assert.equal(nodes['ws-context-desc'].textContent, 'aiuto:SOUL.md');
      assert.deepEqual(actions(), ['openExternal', 'share', 'saveDownloads', 'rename', 'clone', 'delete']);
      assert.ok(nodes['ws-context-actions'].innerHTML.includes('oc-sheet-action danger'));
      assert.equal(sheet.opened, 1);
      let stopped = false;
      buttons[3].fire('click', { stopPropagation() { stopped = true; } });
      assert.equal(stopped, true);
      assert.equal(sheet.closed, 1);
      assert.deepEqual(ws.picked, [['sheet', 'rename', 'SOUL.md']]);
    """)


def test_a_folder_and_the_help_mode_list_their_own_actions() -> None:
    _run_js("""
      ws.showContextSheet({ name: 'note', path: 'note', kind: 'dir' });
      assert.deepEqual(actions(), ['newFile', 'newFolder', 'rename', 'clone', 'delete']);
      assert.equal(nodes['ws-context-desc'].textContent, '', 'il testo del file prima non resta');
      ws.showContextSheet({ name: 'SOUL.md', path: 'SOUL.md', kind: 'file', mode: 'help' });
      assert.deepEqual(actions(), ['openEditor']);
    """)


def test_the_new_menu_lists_file_and_folder_and_runs_the_pick() -> None:
    _run_js("""
      ws._showNewMenu();
      assert.equal(nodes['ws-context-title'].textContent, 'workspace.new');
      assert.deepEqual(actions(), ['newFile', 'newFolder']);
      assert.equal(sheet.opened, 1);
      buttons[1].fire('click', { stopPropagation() {} });
      assert.equal(sheet.closed, 1);
      assert.deepEqual(ws.picked, [['new', 'newFolder']]);
    """)


def test_both_backdrops_ignore_the_tap_that_follows_the_long_press() -> None:
    _run_js("""
      for (const open of [() => ws._showNewMenu(),
                          () => ws.showContextSheet({ name: 'a', path: 'a', kind: 'file' })]) {
        sheet.closed = 0;
        open();
        now += 100;
        sheet.onclick({ target: sheet });
        assert.equal(sheet.closed, 0, 'il tap sintetico ha richiuso il foglio appena aperto');
        sheet.onclick({ target: nodes['ws-context-actions'] });
        assert.equal(sheet.closed, 0, 'un tocco dentro non è il backdrop');
        now += 400;
        sheet.onclick({ target: sheet });
        assert.equal(sheet.closed, 1);
        assert.equal(sheet.onclick, null, 'alla chiusura il backdrop si sgancia');
        assert.equal(nodes['ws-context-cancel'].onclick, null);
      }
    """)


def test_cancel_closes_and_unhooks() -> None:
    _run_js("""
      ws._showNewMenu();
      nodes['ws-context-cancel'].onclick();
      assert.equal(sheet.closed, 1);
      assert.equal(nodes['ws-context-cancel'].onclick, null);
      assert.equal(sheet.onclick, null);
      assert.deepEqual(ws.picked, []);
      assert.equal((sheet.on.close || []).length, 0, 'lo sgancio è `once`');
    """)
