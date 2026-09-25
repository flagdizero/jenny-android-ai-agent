"""Il giro di cancellazione di un progetto (``shared/project-delete.js``), col modulo vero.

Dalla seconda revisione (25/09/2026) il gateway rifiuta ``project.delete`` con
``conflict`` mentre qualcuno sta ancora scrivendo in quel quaderno (un turno, un
subagent, una passata del giardiniere). È una condizione attesa, e va detta com'è
— «sto ancora lavorando lì» — invece del fallimento generico «non ho potuto
eliminare». Chi chiama passa le sue parole: in casa un *quaderno*, in officina un
*progetto*; chi non ha la frase ricade su quella di sempre.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "jenny" / "templates" / "ui" / "assets" / "shared"

pytestmark = requires_node

_VICINI = {
    "api-client.js": "export const api = { describeProject: async () => ({}) };\n",
    "i18n.js": "export const i18n = { t: (k, p) => k + ':' + (p && p.name) };\n",
    "utils.js": "export const toasts = []; export function showToast(t, kind) { toasts.push([t, kind]); }\n",
    "dialog.js": "export async function confirmDialog() { return true; }\n",
}


def _run(code: str | None, words: str) -> None:
    rifiuto = "null" if code is None else json.dumps(code)
    script = f"""
import assert from 'node:assert/strict';
const {{ deleteProjectFlow, PROJECT_DELETE_WORDS }} = await import('./project-delete.js');
const {{ toasts }} = await import('./utils.js');
globalThis.__rifiuto = {rifiuto};
const words = {words};
const esito = await deleteProjectFlow('viaggio', words);
globalThis.__esito = {{ esito, toasts }};
console.log(JSON.stringify(globalThis.__esito));
"""
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        shutil.copy(SHARED / "project-delete.js", radice / "project-delete.js")
        for nome, testo in _VICINI.items():
            (radice / nome).write_text(testo, encoding="utf-8")
        (radice / "rpc-client.js").write_text(
            "export const rpc = { async deleteProject() {"
            " const c = globalThis.__rifiuto; if (c === null) return {};"
            " const e = new Error('refused'); if (c) e.code = c; throw e; } };\n",
            encoding="utf-8",
        )
        (radice / "prova.mjs").write_text(script + _ATTESE[(code, words)], encoding="utf-8")
        run_module(radice / "prova.mjs")


_NB = "{ confirm: 'c', confirmWithChat: 'cc', failed: 'nb.failed', busy: 'nb.busy' }"
_SENZA_BUSY = "{ confirm: 'c', confirmWithChat: 'cc', failed: 'nb.failed' }"
_ATTESE = {
    ("conflict", "PROJECT_DELETE_WORDS"):
        "assert.equal(esito, false);\n"
        "assert.deepEqual(toasts, [['workspace.deleteProjectBusy:viaggio', 'error']]);\n",
    ("conflict", _NB):
        "assert.deepEqual(toasts, [['nb.busy:viaggio', 'error']]);\n",
    ("bad_request", _NB):
        "assert.deepEqual(toasts, [['nb.failed:viaggio', 'error']]);\n",
    ("conflict", _SENZA_BUSY):
        "assert.deepEqual(toasts, [['nb.failed:viaggio', 'error']]);\n",
    (None, _NB):
        "assert.equal(esito, true);\nassert.deepEqual(toasts, []);\n",
}


@pytest.mark.parametrize(("code", "words"), list(_ATTESE), ids=[
    "officina-conflict", "casa-conflict", "casa-altro-errore", "senza-frase-busy", "riuscita",
])
def test_the_refusal_says_what_happened(code, words) -> None:
    _run(code, words)
