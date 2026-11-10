"""Rinominare un quaderno dal file manager dell'officina passa da ``project.rename``.

Il file manager mandava ``workspace.rename`` anche per la radice di un quaderno,
e il server quel comando lo rifiuta sempre (la conversazione resterebbe sotto il
nome vecchio): dall'officina un quaderno non si rinominava mai. Per Elimina lo
stesso file sceglieva già la porta giusta; ora lo fa anche il rinomino.

Il metodo si estrae dal sorgente e gira in node su una classe finta, come in
``test_workspace_sheets_client.py``.
"""

from __future__ import annotations

from support.js_harness import ASSETS, function, member, requires_node, run_js

pytestmark = requires_node

_SOURCE = (ASSETS / "mobile-workspace.js").read_text(encoding="utf-8")

_HARNESS = f"""
import assert from 'node:assert/strict';
const i18n = {{ t: (k, p) => k + (p ? ':' + JSON.stringify(p) : '') }};
const toasts = [];
const showToast = (text, kind) => toasts.push([text, kind]);
const promptDialog = async () => 'orto';
const confirmDialog = async () => true;
const parentPath = (p) => p.split('/').slice(0, -1).join('/');
const calls = [];
let refuse = null;
const rpc = {{
  renameProject: async (...a) => {{ calls.push(['project', ...a]); if (refuse) throw refuse; }},
  renameWorkspace: async (...a) => {{ calls.push(['workspace', ...a]); }},
}};
{function(_SOURCE, "workspaceErrorText")}
{function(_SOURCE, "projectRenameErrorText")}
class Workspace {{
  constructor() {{ this.currentPath = ''; this.currentDir = 'wikis'; this.projects = ['piante']; }}
  async _projectAt(path) {{
    const name = path.split('/').pop();
    return path.startsWith('wikis/') && this.projects.includes(name) ? name : null;
  }}
  async navigateTo() {{}}
{member(_SOURCE, "handleSheetAction")}
}}
const ws = new Workspace();
"""


def test_a_notebook_is_renamed_with_its_conversation() -> None:
    run_js(_HARNESS + """
await ws.handleSheetAction('rename', { path: 'wikis/piante', name: 'piante', kind: 'dir' });
assert.deepEqual(calls, [['project', 'piante', 'orto']]);
assert.deepEqual(toasts, []);
""")


def test_any_other_folder_still_goes_through_the_file_manager() -> None:
    run_js(_HARNESS + """
await ws.handleSheetAction('rename', { path: 'note/vecchie', name: 'vecchie', kind: 'dir' });
assert.deepEqual(calls, [['workspace', 'note/vecchie', 'note/orto']]);
""")


def test_a_refused_notebook_rename_is_said_in_the_user_s_language() -> None:
    run_js(_HARNESS + """
refuse = Object.assign(new Error('a plan holds this notebook'), { code: 'plan_running' });
await ws.handleSheetAction('rename', { path: 'wikis/piante', name: 'piante', kind: 'dir' });
assert.equal(toasts.length, 1);
assert.ok(toasts[0][0].startsWith('home.plan.lockRename'), toasts[0][0]);
""")
