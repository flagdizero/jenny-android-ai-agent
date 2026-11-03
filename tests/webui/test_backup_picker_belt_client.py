"""Un picker di backup che non risponde non blocca i backup per sempre.

``runImportFlow`` (e l'export)
aspettava la risposta del nativo in una Promise senza cintura: se non arrivava
mai — il picker ucciso, un ``evaluateJavascript`` perso — restava appesa, e con
lei ``_busy``. Export, import e restore morivano in silenzio fino al
ricaricamento. I dialoghi dello stesso file la cintura l'avevano gia'.

Il modulo vero (``shared/backup-flow.js``) gira in node con i vicini finti e
un orologio a mano.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import ASSETS, requires_node, run_module

pytestmark = requires_node

_NEIGHBORS = {
    "api-client.js": "export const api = { noteBackupExported: async () => {}, "
    "exportBackup: async () => ({ staged_path: '/s', suggested_filename: 'b.jbk' }) };\n",
    "i18n.js": "export const i18n = { t: (k) => k };\n",
    "utils.js": "export const toasts = []; export function showToast(m) { toasts.push(m); }\n"
    "export const sizeText = (n) => `${n} B`;\n",
    # Le finestre qui non contano: si misura l'attesa del nativo.
    "dialog.js": "export function formDialog() { return Promise.resolve(null); }\n",
}

_PRELUDE = """
import assert from 'node:assert/strict';
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
globalThis.clearTimeout = (id) => { if (timers[id - 1]) timers[id - 1].fn = null; };
const fire = () => { for (const t of timers.splice(0)) t.fn?.(); };
const docListeners = {};
globalThis.document = {
  visibilityState: 'visible',
  addEventListener(t, fn) { (docListeners[t] ||= new Set()).add(fn); },
  removeEventListener(t, fn) { docListeners[t]?.delete(fn); },
};
const setVisible = (v) => {
  document.visibilityState = v ? 'visible' : 'hidden';
  for (const fn of [...(docListeners.visibilitychange || [])]) fn();
};
let opened = 0;
globalThis.window = globalThis;
globalThis.JennyNative = {
  importBackup() { opened += 1; }, exportBackup() { opened += 1; }, restartApp() {},
};
const flow = await import('./shared/backup-flow.js');
const settle = () => new Promise((r) => queueMicrotask(r));
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "backup-flow.js", root / "shared" / "backup-flow.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
        run_module(entry)


def test_a_silent_picker_is_a_cancel_once_back_on_the_page() -> None:
    _run(
        """
        const first = flow.runImportFlow();
        await settle();
        assert.equal(opened, 1);
        setVisible(false);          // il picker e' davanti
        fire();                     // quanto si vuole: nessuna cintura mentre si sceglie
        setVisible(true);           // si torna, e il nativo non dice niente
        assert.equal(timers.at(-1).ms, flow.NATIVE_ANSWER_GRACE_MS);
        fire();
        assert.equal(await first, false);
        // Non e' rimasto occupato: un secondo tentativo riapre il picker.
        const second = flow.runImportFlow();
        await settle();
        assert.equal(opened, 2);
        window.jennyBackup.onImportPicked(false);
        assert.equal(await second, false);
        """
    )


def test_an_answer_before_the_grace_wins_and_a_late_one_does_nothing() -> None:
    _run(
        """
        const p = flow.runImportFlow();
        await settle();
        setVisible(false);
        setVisible(true);
        window.jennyBackup.onImportPicked(false);
        assert.equal(await p, false);
        fire();                     // la cintura scaduta dopo non tocca niente
        window.jennyBackup.onImportPicked(true);   // tardiva, senza padrone
        """
    )


def test_the_export_has_the_same_belt() -> None:
    """L'export passa da una passphrase in un dialogo: qui basta che la sua
    attesa del nativo sia la stessa, cintura compresa."""
    src = (ASSETS / "shared" / "backup-flow.js").read_text(encoding="utf-8")
    export = src.split("export function runExportFlow", 1)[1].split("\nexport function", 1)[0]
    assert "_awaitNative('export'" in export, "export senza cintura"
    assert "_pending.export =" not in export
