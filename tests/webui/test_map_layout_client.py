"""La disposizione della mappa segue il quaderno quando cambia nome, e se ne va con lui.

Gli spilli della mappa stanno in ``.jenny/map-layout.json``, chiave il nome del
quaderno. Fino al 26/09/2026 ne' ``renameNotebook`` ne' la cancellazione
toccavano il file: rinominato, un quaderno perdeva la sua disposizione;
cancellato, la lasciava li', e un quaderno nuovo con lo stesso nome ereditava
gli spilli di pagine che non aveva.

Il modulo vero (``shared/map-layout.js``) e il giro di cancellazione vero
(``shared/project-delete.js``), con un workspace finto in memoria.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from support.js_harness import requires_node, run_module

SHARED = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets" / "shared"
pytestmark = requires_node

_FAKES = {
    "api-client.js": """
export const disk = { files: new Map(), readFails: false, writes: 0, writeFails: false };
export const api = {
  describeProject: async () => ({}),
  async readWorkspaceFile(path) {
    if (disk.readFails) throw Object.assign(new Error('rete'), { status: 500 });
    if (!disk.files.has(path)) throw Object.assign(new Error('non c\\'e\\''), { status: 404 });
    return { content: disk.files.get(path) };
  },
};
""",
    "rpc-client.js": """
import { disk } from './api-client.js';
export const rpc = {
  deleted: [],
  async writeWorkspaceFile(path, content) {
    if (disk.writeFails) throw new Error('disco pieno');
    disk.writes += 1;
    disk.files.set(path, content);
  },
  async deleteProject(name) {
    if (globalThis.refuse) throw Object.assign(new Error('no'), { code: 'conflict' });
    this.deleted.push(name);
  },
};
""",
    "i18n.js": "export const i18n = { t: (k) => k };\n",
    "utils.js": "export function showToast() {}\n",
    "dialog.js": "export const answer = { yes: true };\n"
    "export async function confirmDialog() { return answer.yes; }\n",
}

_PRELUDE = """
import assert from 'node:assert/strict';
import { disk } from './api-client.js';
import { rpc } from './rpc-client.js';
import { answer } from './dialog.js';
const { MAP_LAYOUT_FILE, moveLayoutKey, dropLayoutKey } = await import('./map-layout.js');
const { deleteProjectFlow } = await import('./project-delete.js');
console.warn = () => {};
const put = (obj) => disk.files.set(MAP_LAYOUT_FILE, JSON.stringify(obj));
const layout = () => JSON.parse(disk.files.get(MAP_LAYOUT_FILE));
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("map-layout.js", "project-delete.js"):
            shutil.copy(SHARED / name, root / name)
        for name, text in _FAKES.items():
            (root / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + body + "\nconsole.log('ok');\n", encoding="utf-8")
        assert run_module(entry).strip().endswith("ok")


def test_a_rename_moves_the_pins_and_leaves_the_others_alone() -> None:
    _run("""
      put({ piante: { 'a.md': [1, 2] }, erbe: { 'b.md': [3, 4] } });
      assert.equal(await moveLayoutKey('piante', 'orto'), true);
      assert.deepEqual(layout(), { orto: { 'a.md': [1, 2] }, erbe: { 'b.md': [3, 4] } });
    """)


def test_stale_pins_under_the_new_name_are_not_inherited() -> None:
    """Spilli lasciati da un omonimo cancellato prima di questa correzione: non
    sono del quaderno rinominato, che ha i suoi — o nessuno."""
    _run("""
      put({ vecchio: { 'x.md': [9, 9] } });
      await moveLayoutKey('piante', 'vecchio');
      assert.deepEqual(layout(), {}, 'il quaderno rinominato ha ereditato spilli non suoi');
      put({ piante: { 'a.md': [1, 2] }, vecchio: { 'x.md': [9, 9] } });
      await moveLayoutKey('piante', 'vecchio');
      assert.deepEqual(layout(), { vecchio: { 'a.md': [1, 2] } });
    """)


def test_nothing_to_move_writes_nothing() -> None:
    _run("""
      assert.equal(await moveLayoutKey('piante', 'orto'), false, 'senza file non c\\'e\\' niente da spostare');
      put({ erbe: {} });
      assert.equal(await moveLayoutKey('piante', 'orto'), false);
      assert.equal(await dropLayoutKey('piante'), false);
      assert.equal(disk.writes, 0);
    """)


def test_an_unreadable_file_is_never_rewritten() -> None:
    """La regola di ``_loadPins``: riscrivere da una lettura fallita
    cancellerebbe le disposizioni di tutti gli altri quaderni."""
    _run("""
      put({ piante: {}, erbe: { 'b.md': [3, 4] } });
      disk.readFails = true;
      assert.equal(await moveLayoutKey('piante', 'orto'), false);
      assert.equal(await dropLayoutKey('piante'), false);
      disk.readFails = false;
      disk.files.set(MAP_LAYOUT_FILE, '[1, 2]');
      assert.equal(await dropLayoutKey('piante'), false);
      assert.equal(disk.writes, 0);
    """)


def test_a_failed_write_does_not_throw() -> None:
    _run("""
      put({ piante: {} });
      disk.writeFails = true;
      assert.equal(await moveLayoutKey('piante', 'orto'), false);
      assert.equal(await dropLayoutKey('piante'), false);
    """)


def test_deleting_a_notebook_drops_its_pins() -> None:
    _run("""
      put({ piante: { 'a.md': [1, 2] }, erbe: { 'b.md': [3, 4] } });
      assert.equal(await deleteProjectFlow('piante'), true);
      assert.deepEqual(layout(), { erbe: { 'b.md': [3, 4] } },
        'un quaderno nuovo con lo stesso nome erediterebbe questi spilli');
    """)


def test_a_deletion_that_did_not_happen_keeps_the_pins() -> None:
    _run("""
      put({ piante: { 'a.md': [1, 2] } });
      answer.yes = false;
      assert.equal(await deleteProjectFlow('piante'), false);
      answer.yes = true;
      globalThis.refuse = true;
      assert.equal(await deleteProjectFlow('piante'), false);
      assert.deepEqual(layout(), { piante: { 'a.md': [1, 2] } });
      assert.equal(disk.writes, 0);
    """)


def test_a_layout_that_cannot_be_dropped_does_not_fail_the_deletion() -> None:
    _run("""
      put({ piante: {} });
      disk.writeFails = true;
      assert.equal(await deleteProjectFlow('piante'), true);
      assert.deepEqual(rpc.deleted, ['piante']);
    """)
