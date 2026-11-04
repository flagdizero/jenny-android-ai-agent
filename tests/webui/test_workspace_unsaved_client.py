"""Il testo non salvato dell'editor del workspace non si perde per strada.

Tre difetti dell'officina, in node sui metodi veri di
``mobile-workspace.js`` (gli altri sono stub: il DOM finto misurerebbe se'
stesso):

* il buffer sporco si perdeva senza una domanda. Il cassetto di
  Memoria ridisegna la scheda (``mount`` → ``navigateTo``), ``navigateTo``
  riportava la vista a ``explorer`` e l'editor diventava irraggiungibile;
  aprire un altro file (``openFile`` → ``_enterEditorView``) lo sovrascriveva.
  E ``openFile`` non aveva un token: la risposta di una lettura vecchia poteva
  arrivare dopo quella nuova e aprire il file sbagliato.
* ``saveFile`` rimetteva ``_dirty = false`` dopo l'``await``: quel
  che si era scritto *durante* il salvataggio risultava salvato e non lo era.
* il «Scarica» dei binari era un ``<a download>`` verso
  ``/api/workspace/download``, senza il Bearer: 401, sempre.
"""

from __future__ import annotations

import json
import re

from support.js_harness import ASSETS, locale, member, requires_node, run_js

WORKSPACE_JS = ASSETS / "mobile-workspace.js"

pytestmark = requires_node

# I metodi veri. Quelli che la correzione aggiunge ci sono solo dopo: il banco li
# prende se ci sono, cosi' prima della correzione i test falliscono sul
# comportamento e non su un metodo che manca.
_REAL = (
    "mount",
    "navigateTo",
    "_explorerOnScreen",
    "openFile",
    "_enterEditorView",
    "openWithSystemApp",
    "saveFile",
    "renderBinary",
    "saveToDownloads",
    "_mayReplaceBuffer",
    "_downloadBinary",
)

_HARNESS = """
import assert from 'node:assert/strict';
const T = __IT__;
const i18n = { t: (k, p) => {
  let s = k.split('.').reduce((o, x) => o?.[x], T) ?? k;
  for (const [a, b] of Object.entries(p || {})) s = s.replace('{' + a + '}', b);
  return s;
} };
const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const getFileExtension = (p) => (p.includes('.') ? p.split('.').pop() : '');
const fileHelpText = () => '';
const IMAGE_EXTS = new Set(['png']);
const KNOWN_BINARY_EXTS = new Set(['zip']);
const toasts = [];
const showToast = (m, kind) => toasts.push([m, kind]);

/* La conferma: il banco decide la risposta e conta le domande. */
const dialog = { answer: true, asked: 0 };
const confirmDialog = async () => { dialog.asked++; return dialog.answer; };

/* Le letture dal gateway, risolte a mano dal banco nell'ordine che vuole. */
const reads = [];
const fetchedUrls = [];
const api = {
  listWorkspace: async () => ({ items: [] }),
  readWorkspaceFile(path) {
    return new Promise((resolve, reject) => reads.push({ path, resolve, reject }));
  },
  getWorkspaceDownloadUrl: (p) => `/api/workspace/download?path=${encodeURIComponent(p)}`,
  downloadWorkspaceBlob: async (p) => { fetchedUrls.push(p); return new Blob(['x']); },
};
const saves = [];
const rpc = {
  writeWorkspaceFile(path, content, base) {
    return new Promise((resolve, reject) => saves.push({ path, content, base, resolve, reject }));
  },
};

const clicks = [];
const created = [];
globalThis.document = {
  querySelector: () => null,
  createElement: () => {
    const a = { click() { clicks.push({ href: a.href, download: a.download }); }, remove() {} };
    created.push(a);
    return a;
  },
  body: { appendChild() {} },
};
globalThis.URL.createObjectURL = () => 'blob:finto';
globalThis.URL.revokeObjectURL = () => {};

const shell = { modes: [] };
globalThis.window = { mobileApp: { switchMode: (m) => shell.modes.push(m) } };

/* Un editor finto: il testo e basta. */
function fakeEditor(text) {
  return { text, getValue() { return this.text; }, getInputField: () => ({ blur() {} }) };
}

class WorkspaceController {
  constructor() {
    this.currentDir = '';
    this.currentPath = '';
    this.viewMode = 'explorer';
    this._navToken = 0;
    this._openToken = 0;
    this._dirty = false;
    this.editor = null;
    this.gridEl = { isConnected: true, innerHTML: '' };
    this.emptyEl = { style: {}, querySelector: () => null };
    this.viewerEl = { innerHTML: '', classList: { add() {}, remove() {} }, querySelector: () => null,
      querySelectorAll: () => [] };
    this.rendered = [];
    this.shown = [];
  }
  renderBreadcrumb() {}
  renderGrid() {}
  showEditorView() { this.shown.push('editor'); }
  showExplorerView() { this.shown.push('explorer'); }
  _syncHeaderBack() {}
  _showNewMenu() {}
  showContextSheet() {}
  previewImage() {}
  renderCodeViewer(name, content) { this.rendered.push(content); this.editor = fakeEditor(content); }
  renderError(m) { this.rendered.push('error:' + m); }
__METHODS__
}

const tick = () => new Promise((r) => setTimeout(r, 0));

/* Un editor aperto su *path* e modificato. */
async function dirtyEditorOn(c, path, text) {
  const p = c.openFile(path);
  await tick();
  reads.shift().resolve({ content: 'originale' });
  await p;
  c.editor.text = text;
  c._dirty = true;
}
"""


def _harness() -> str:
    source = WORKSPACE_JS.read_text(encoding="utf-8")
    methods = []
    for name in _REAL:
        if re.search(rf"\n  (?:async )?{re.escape(name)}\(", source):
            methods.append("  " + member(source, name))
    it = locale("it")
      # La funzione vera che traduce gli errori del file manager, accanto alla classe.
    helper = re.search(r"\nfunction workspaceErrorText\(err\) \{\n.*?\n\}\n", source, re.S)
    assert helper, "workspaceErrorText not found in mobile-workspace.js"
    return _HARNESS.replace("__IT__", json.dumps(it, ensure_ascii=False)).replace(
        "__METHODS__", "\n".join(methods)
    ) + helper.group(0)


def _run(script: str) -> None:
    run_js(_harness() + "\n" + script + "\nconsole.log('ok');")


# -- Il buffer sporco non si perde senza una domanda ------------------------


def test_redrawing_the_card_keeps_a_dirty_editor_reachable() -> None:
    """Il cassetto ridisegna la scheda e chiama ``mount``: la vista del file
    modificato deve restare quella, o ``activate`` rimanda a Memoria e il testo
    resta in un viewer nascosto che nessuno puo' piu' raggiungere."""
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'note/a.md', 'scritto a mano');
c.mount({ querySelector: () => ({ isConnected: true, addEventListener() {} }) });
await tick();
assert.equal(c.viewMode, 'editor', 'il ridisegno della scheda ha chiuso l editor sporco');
assert.equal(c._dirty, true);
assert.equal(c.editor.getValue(), 'scritto a mano');
assert.equal(dialog.asked, 0, 'un ridisegno automatico non fa domande');
""")


def test_opening_another_file_asks_before_dropping_the_edits() -> None:
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'a.md', 'scritto a mano');

dialog.answer = false;
await c.openFile('b.md');
assert.equal(dialog.asked, 1, 'nessuna conferma prima di buttare le modifiche');
assert.equal(reads.length, 0, 'ha letto il file nuovo nonostante il no');
assert.equal(c.currentPath, 'a.md');
assert.equal(c.editor.getValue(), 'scritto a mano');
assert.equal(c._dirty, true);

dialog.answer = true;
const p = c.openFile('b.md');
await tick();
reads.shift().resolve({ content: 'bi' });
await p;
assert.equal(dialog.asked, 2);
assert.equal(c.currentPath, 'b.md');
assert.equal(c._dirty, false);
""")


def test_reopening_the_file_being_edited_keeps_the_edits() -> None:
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'a.md', 'scritto a mano');
await c.openFile('a.md');
assert.equal(dialog.asked, 0);
assert.equal(reads.length, 0, 'ha riletto dal disco il file che si sta modificando');
assert.equal(c.editor.getValue(), 'scritto a mano');
assert.equal(c.viewMode, 'editor');
""")


def test_a_stale_read_does_not_open_the_wrong_file() -> None:
    """Due tocchi in fila: la lettura del primo arriva dopo quella del secondo.
    Vince l'ultimo tocco, non l'ultima risposta."""
    _run("""
const c = new WorkspaceController();
const first = c.openFile('lento.md');
const second = c.openFile('veloce.md');
await tick();
const [slow, fast] = reads.splice(0);
fast.resolve({ content: 'veloce' });
await second;
slow.resolve({ content: 'lento' });
await first;
assert.equal(c.currentPath, 'veloce.md');
assert.deepEqual(c.rendered, ['veloce']);
""")


def test_edits_typed_while_the_new_file_is_read_are_asked_about() -> None:
    """Il buffer era pulito quando si e' toccato l'altro file, e si e' scritto
    nell'editor mentre la lettura era in volo: la seconda domanda, a lettura
    arrivata, e' l'unica che le vede."""
    _run("""
const c = new WorkspaceController();
const opening = c.openFile('a.md');
await tick();
reads.shift().resolve({ content: 'originale' });
await opening;
assert.equal(c._dirty, false);

dialog.answer = false;
const p = c.openFile('b.md');
await tick();
assert.equal(dialog.asked, 0, 'un buffer pulito non fa domande');
c.editor.text = 'scritto durante la lettura';
c._dirty = true;
reads.shift().resolve({ content: 'bi' });
await p;
assert.equal(dialog.asked, 1, 'le modifiche scritte durante la lettura sparite senza chiedere');
assert.equal(c.currentPath, 'a.md');
assert.equal(c.editor.getValue(), 'scritto durante la lettura');
assert.equal(c._dirty, true);
assert.deepEqual(c.rendered, ['originale']);

dialog.answer = true;
const q = c.openFile('b.md');
await tick();
reads.shift().resolve({ content: 'bi' });
await q;
assert.equal(c.currentPath, 'b.md');
assert.equal(c._dirty, false);
""")


def test_a_binary_fallback_asks_before_dropping_the_edits() -> None:
    """Senza il ponte nativo l'apertura con l'app di sistema ripiega sulla vista
    dell'editor: anche quella strada sovrascriveva il buffer."""
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'a.md', 'scritto a mano');
dialog.answer = false;
await c.openWithSystemApp('pacco.zip', 'pacco.zip');
assert.equal(dialog.asked, 1);
assert.equal(c.currentPath, 'a.md');
assert.equal(c._dirty, true);
""")


# -- Quel che si scrive durante il salvataggio resta da salvare -------------


def test_typing_during_a_save_keeps_the_buffer_dirty() -> None:
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'a.md', 'prima versione');
const p = c.saveFile();
await tick();
assert.equal(saves.length, 1);
c.editor.text = 'prima versione, e poi altro';
saves.shift().resolve({});
await p;
assert.equal(c._dirty, true, 'il testo scritto durante il salvataggio risulta salvato');
""")


def test_a_save_of_the_current_text_cleans_the_buffer() -> None:
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'a.md', 'versione');
const p = c.saveFile();
await tick();
saves.shift().resolve({});
await p;
assert.equal(c._dirty, false);
""")


# -- «Scarica» porta il Bearer ----------------------------------------------


def test_the_binary_download_carries_the_credentials() -> None:
    """Il link nudo verso ``/api/workspace/download`` non porta il Bearer: il
    gateway risponde 401. Il «Scarica» deve passare dal ponte nativo, o da una
    lettura autenticata quando il ponte non c'e'."""
    _run("""
const c = new WorkspaceController();
c.renderBinary('pacco.zip', 'dir/pacco.zip');
assert.ok(!/href="\\/api\\/workspace\\/download/.test(c.viewerEl.innerHTML),
  'il link senza credenziali e ancora li');

// Col ponte: la stessa strada del «Salva in Download» del foglio azioni.
const saved = [];
window.JennyNative = { saveToDownloads: async (p) => { saved.push(p); return true; } };
await c._downloadBinary('dir/pacco.zip', 'pacco.zip');
assert.deepEqual(saved, ['dir/pacco.zip']);

// Senza ponte: blob letto col token, poi un link locale.
delete window.JennyNative;
await c._downloadBinary('dir/pacco.zip', 'pacco.zip');
assert.deepEqual(fetchedUrls, ['dir/pacco.zip']);
assert.deepEqual(clicks, [{ href: 'blob:finto', download: 'pacco.zip' }]);
""")


# -- Il salvataggio dice da che testo e' partito -----------------------------


def test_a_save_sends_the_text_the_editor_opened() -> None:
    """Senza ``base`` il server non puo' sapere se la copia dell'editor e'
    vecchia: per ``config.json`` sovrascriveva quel che le Impostazioni avevano
    scritto intanto."""
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'config.json', '{"nuovo": 1}');
const p = c.saveFile();
await tick();
assert.equal(saves[0].base, 'originale');
saves.shift().resolve({ content: '{\\n  "nuovo": 1\\n}' });
await p;

c.editor.text = '{"nuovo": 2}';
c._dirty = true;
const q = c.saveFile();
await tick();
assert.equal(saves[0].base, '{\\n  "nuovo": 1\\n}',
  'la base del secondo salvataggio non e il testo che il server ha scritto');
saves.shift().resolve({});
await q;

const r = c.saveFile();
await tick();
assert.equal(saves[0].base, '{"nuovo": 2}', 'senza content la base e il testo salvato');
saves.shift().resolve({});
await r;
""")


def test_a_conflict_says_the_file_changed_and_keeps_the_edits() -> None:
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'nota.md', 'mia versione');
const p = c.saveFile();
await tick();
saves.shift().reject(Object.assign(new Error('file changed on disk'), { code: 'conflict' }));
await p;
assert.deepEqual(toasts, [[T.workspace.changedOnDisk, 'error']]);
assert.equal(c._dirty, true);
assert.equal(c._editorBase, 'originale', 'un salvataggio fallito ha spostato la base');
""")


def test_a_file_that_failed_to_open_has_no_base() -> None:
    """La base e' del file aperto: una lettura fallita non si porta dietro quella
    del file di prima."""
    _run("""
const c = new WorkspaceController();
await dirtyEditorOn(c, 'a.md', 'x');
c._dirty = false;
const p = c.openFile('b.md');
await tick();
reads.shift().reject(Object.assign(new Error('boom'), { status: 500 }));
await p;
assert.equal(c.currentPath, 'b.md');
assert.equal(c._editorBase, null);
""")
