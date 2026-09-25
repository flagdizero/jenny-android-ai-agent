"""La scheda di un quaderno, e il seguito di una cancellazione in casa.

Tenere premuto un quaderno nella tendina «Con chi parli» apre la sua scheda:
Apri · Metti come pagina · Rinomina · Elimina — le stesse righe, nello stesso
ordine, della scheda di un'app nel cassetto. **Una cosa si appende dal posto
dove vive** (`.agent/pagine-dal-posto-plan.md`).

`home-notebook.js` si importa vero, coi suoi vicini finti; `apps-actions.js`
invece e' vero anche lui, perche' la riga la disegna la sua `drawRow` — la
scheda e' la stessa cosa a vedersi, e una seconda copia del disegno
divergerebbe al primo ritocco.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import textwrap
from pathlib import Path

import pytest
from support.js_harness import member, requires_node, run_js, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_VICINI = {
    "api-client.js": "export const api = { getSecret() { return 'ok'; } };\n",
    "utils.js": """
export function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
export function showToast() {}
""",
    "dialog.js": "export async function confirmDialog() { return true; }\n",
    "i18n.js": "export const i18n = { t: (k) => k };\n",
    "ws-manager.js": "export const wsManager = { on() {}, off() {}, request() {} };\n",
    "theme.js": "export function currentTheme() { return {}; }\nexport function themeTokens() { return ''; }\n",
    "conversation-list.js": "export const projectKey = (n) => 'project:' + n;\n",
}

_FINTO_DOM = """
const elementi = new Map();
function creaEl(id) {
  const el = {
    id, innerHTML: '', textContent: '', open: false, onclick: null,
    showModal() { this.open = true; },
    close() { this.open = false; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  if (id) elementi.set(id, el);
  return el;
}
for (const id of ['home-notebook-sheet', 'home-notebook-sheet-title',
                  'home-notebook-sheet-actions', 'home-notebook-sheet-cancel']) creaEl(id);
globalThis.document = {
  getElementById: (id) => elementi.get(id) || null,
  createElement: () => creaEl(null),
  documentElement: { lang: 'it' },
};
globalThis.window = { addEventListener() {} };
globalThis.MutationObserver = class { observe() {} };

function rows() {
  const html = elementi.get('home-notebook-sheet-actions').innerHTML;
  return [...html.matchAll(/<button[^>]*data-action="([^"]+)"([^>]*)>/g)]
    .map(([, action, attr]) => ({ action, off: /\\bdisabled\\b/.test(attr) }));
}
"""


def _run(body: str, *, state: str | None = "free", rename: bool = False) -> None:
    porta = (
        "null"
        if state is None
        else "{ state: (k, r) => { chiamate.push(['stato', k, r]); return '" + state + "'; },"
        "  append: async (k, r) => { chiamate.push(['append', k, r]); return true; },"
        "  detach: async (k, r) => { chiamate.push(['detach', k, r]); return true; } }"
    )
    script = (
        "import assert from 'node:assert/strict';\n"
        + _FINTO_DOM
        + textwrap.dedent(
            f"""
            const {{ NotebookCard }} = await import('./home-notebook.js');
            const chiamate = [];
            const PORTA = {porta};
            const shell = {{
              homePages: () => PORTA,
              open: (n) => chiamate.push(['open', n]),
              delete: (n) => chiamate.push(['delete', n]),
            }};
            if ({json.dumps(rename)}) shell.rename = (n) => chiamate.push(['rename', n]);
            const card = new NotebookCard(shell);
            """
        )
        + body
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "home-notebook.js", root / "home-notebook.js")
        shutil.copy(ASSETS / "shared" / "apps-actions.js", root / "shared" / "apps-actions.js")
        for name, text in _VICINI.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        run_module(entry)


# ── Le righe ────────────────────────────────────────────────────────────────


def test_the_rows_are_the_app_sheets_rows_in_the_same_order() -> None:
    """Chi ha imparato la scheda di un'app ha imparato questa."""
    _run(
        "card.show('piante');\n"
        "assert.deepEqual(rows().map((r) => r.action), ['open', 'pin', 'rename', 'delete']);\n"
        "assert.deepEqual(chiamate[0], ['stato', 'conversation', 'project:piante']);\n"
        "assert.equal(document.getElementById('home-notebook-sheet').open, true);\n",
        rename=True,
    )


def test_without_a_way_to_rename_there_is_no_rename_row() -> None:
    """Una riga che non fa niente e' peggio di una riga che manca."""
    _run(
        "card.show('piante');\n"
        "assert.deepEqual(rows().map((r) => r.action), ['open', 'pin', 'delete']);\n",
        rename=False,
    )


def test_a_pinned_notebook_offers_to_unpin_it() -> None:
    _run(
        "card.show('piante');\n"
        "assert.equal(rows()[1].action, 'unpin');\n",
        state="pending",
    )


def test_with_the_pages_full_the_pin_row_is_off() -> None:
    _run(
        "card.show('piante');\n"
        "assert.equal(rows()[1].action, 'pin');\n"
        "assert.equal(rows()[1].off, true);\n",
        state="piena",
    )


def test_the_name_in_the_title_is_text_not_markup() -> None:
    """Il nome del quaderno viene dal disco: nel titolo e' testo."""
    _run(
        "card.show('<b>x');\n"
        "const t = document.getElementById('home-notebook-sheet-title').innerHTML;\n"
        "assert.ok(t.includes('&lt;b>x'), t);\n"
    )


# ── Cosa fanno ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "attesa"),
    [
        ("open", ["open", "piante"]),
        ("pin", ["append", "conversation", "project:piante"]),
        ("unpin", ["detach", "conversation", "project:piante"]),
        ("rename", ["rename", "piante"]),
        ("delete", ["delete", "piante"]),
    ],
)
def test_each_row_asks_the_shell(action, attesa) -> None:
    _run(
        f"await card.perform({json.dumps(action)}, 'piante');\n"
        f"assert.deepEqual(chiamate.at(-1), {json.dumps(attesa)});\n",
        rename=True,
    )


# ── Il seguito di una cancellazione, in casa ────────────────────────────────


def _member(source: str, name: str) -> str:
    return member(source, name, prefixes=("async ",))


def _run_seguito(body: str, *, confermato: bool, corrente: str | None) -> None:
    metodo = _member((ASSETS / "home-app.js").read_text(encoding="utf-8"), "deleteNotebook")
    script = textwrap.dedent(
        f"""
        import assert from 'node:assert/strict';
        const history = [];
        const NOTEBOOK_DELETE_WORDS = {{ confirm: 'c' }};
        async function deleteProjectFlow(name, parole) {{
          history.push(['chiede', name, parole === NOTEBOOK_DELETE_WORDS]);
          return {json.dumps(confermato)};
        }}
        const projectNameOf = (k) => (k && k.startsWith('project:') ? k.slice(8) : null);
        const projectKey = (n) => 'project:' + n;
        const sessionManager = {{ currentKey: {json.dumps(corrente)}, personalKey: 'websocket:default' }};
        const i18n = {{ t: (k) => k }};
        function showToast(t) {{ history.push(['avviso', t]); }}
        class Guscio {{
          constructor() {{
            this.who = {{ refresh: async () => history.push(['tendina']) }};
            this._drafts = new Map();
          }}
          /* La chat cambia dove sta: passare dalla regola delle pagine, dalla
             pagina Quaderni, porterebbe alla pagina chat. */
          async showConversation(k) {{ history.push(['conversation', k]); }}
          async switchConversation(k) {{ history.push(['dirottata', k]); }}
          pagesPort() {{ return {{ reload: async () => history.push(['pagine']) }}; }}
          {metodo}
        }}
        const g = new Guscio();
        """
    ) + body
    run_js(script)


def test_a_delete_asks_with_the_notebook_words() -> None:
    """In casa quel che si cancella e' un quaderno, non un progetto."""
    _run_seguito(
        "await g.deleteNotebook('piante');\n"
        "assert.deepEqual(history[0], ['chiede', 'piante', true]);\n",
        confermato=True,
        corrente=None,
    )


def test_deleting_the_notebook_you_are_in_takes_you_home() -> None:
    """Restare in una chat che non esiste piu' vorrebbe dire scrivere a vuoto."""
    _run_seguito(
        "await g.deleteNotebook('piante');\n"
        "assert.deepEqual(history.map((x) => x[0]), ['chiede', 'conversation', 'tendina', 'pagine', 'avviso']);\n"
        "assert.deepEqual(history[1], ['conversation', null]);\n",
        confermato=True,
        corrente="project:piante",
    )


def test_deleting_another_notebook_leaves_you_where_you_are() -> None:
    _run_seguito(
        "await g.deleteNotebook('piante');\n"
        "assert.ok(!history.some((x) => x[0] === 'conversation'), 'ti ha spostato');\n"
        "assert.ok(history.some((x) => x[0] === 'pagine'), 'le pagine non sono state rilette');\n"
        "assert.ok(history.some((x) => x[0] === 'tendina'), 'la tendina non si e ridisegnata');\n",
        confermato=True,
        corrente="project:altro",
    )


def test_saying_no_changes_nothing() -> None:
    _run_seguito(
        "const done = await g.deleteNotebook('piante');\n"
        "assert.equal(done, false);\n"
        "assert.deepEqual(history.map((x) => x[0]), ['chiede']);\n",
        confermato=False,
        corrente="project:piante",
    )


# ── Il guscio ───────────────────────────────────────────────────────────────


def test_back_closes_the_notebook_sheet_before_anything_else() -> None:
    """La scheda sta nel top layer, **sopra** la pagina Quaderni da cui si
    apre: Indietro chiude prima lei, e solo alla pressione dopo lascia la
    pagina."""
    app_js = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    catena = app_js.split("_closeOverlays() {", 1)[1].split("\n  }\n", 1)[0]
    fogli = re.search(r"(?m)^const LONG_PRESS_SHEETS = \[(.*)\];$", app_js)
    assert fogli and "'home-notebook-sheet'" in fogli.group(1)
    assert catena.index("LONG_PRESS_SHEETS") < catena.index("handleBack()")


def test_the_sheet_is_in_the_page_and_shipped() -> None:
    html = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    for id_ in ("home-notebook-sheet", "home-notebook-sheet-title",
                "home-notebook-sheet-actions", "home-notebook-sheet-cancel"):
        assert f'id="{id_}"' in html, id_
    manifest = (ROOT / "jenny" / "utils" / "android_assets.py").read_text(encoding="utf-8")
    assert '"assets/home-notebook.js"' in manifest


def test_the_workshop_still_asks_about_a_project() -> None:
    """Le parole sono diventate un parametro: chi non lo passa — l'officina —
    deve sentire la domanda di prima, parola per parola."""
    src = (ASSETS / "shared" / "project-delete.js").read_text(encoding="utf-8")
    parole = src.split("export const PROJECT_DELETE_WORDS = {", 1)[1].split("};", 1)[0]
    assert "confirm: 'workspace.deleteProjectConfirm'" in parole
    assert "confirmWithChat: 'workspace.deleteProjectConfirmWithChat'" in parole
    assert "failed: 'workspace.deleteProjectFailed'" in parole
    assert "export async function deleteProjectFlow(name, words = PROJECT_DELETE_WORDS)" in src


# ── Rinomina, dal lato della casa ───────────────────────────────────────────


def _run_rinomina(
    body: str, *, written: str | None, corrente: str | None, rifiuta: bool | str = False,
) -> None:
    app_js = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    metodo = _member(app_js, "renameNotebook")
    draft = _member(app_js, "_renameDraft")
    script = textwrap.dedent(
        f"""
        import assert from 'node:assert/strict';
        const {{ isOpenableProjectName }} = await import({json.dumps((ASSETS / "shared" / "conversation-list.js").as_uri())});
        const history = [];
        const projectKey = (n) => 'project:' + n;
        async function promptDialog(msg, opz) {{ history.push(['chiede', opz.initial]); return {json.dumps(written)}; }}
        const rpc = {{
          async renameProject(a, b) {{
            history.push(['rpc', a, b]);
            const rifiuto = {json.dumps(rifiuta)};
            if (rifiuto) {{
              const err = new Error('a folder named viaggi already exists');
              if (typeof rifiuto === 'string') err.code = rifiuto;
              throw err;
            }}
          }},
        }};
        const sessionManager = {{ currentKey: {json.dumps(corrente)} }};
        const i18n = {{ t: (k, p) => p && p.error !== undefined ? k + ':' + p.error
          : p && p.name !== undefined ? k + '|' + p.name : k }};
        function showToast(t, type) {{ history.push(['avviso', t, type]); }}
        class Guscio {{
          constructor() {{
            this.who = {{ refresh: async () => history.push(['tendina']) }};
            this.homePages = {{ renameConversation: (a, b) => history.push(['pagina0', a, b]) }};
            this._drafts = new Map();
            this.input = {{ value: '' }};
          }}
          async showConversation(k) {{ history.push(['conversation', k]); }}
          async switchConversation(k) {{ history.push(['dirottata', k]); }}
          pagesPort() {{ return {{ reload: async () => history.push(['pagine']) }}; }}
          {metodo}
          {draft}
        }}
        const g = new Guscio();
        """
    ) + body
    run_js(script)


def test_renaming_the_notebook_you_are_in_keeps_you_there_under_the_new_name() -> None:
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), true);\n"
        "assert.deepEqual(history[0], ['chiede', 'viaggio'], 'la domanda non parte dal nome attuale');\n"
        "assert.deepEqual(history[1], ['rpc', 'viaggio', 'viaggi']);\n"
        "assert.ok(history.some((x) => x[0] === 'pagina0' && x[2] === 'project:viaggi'));\n"
        "assert.ok(history.some((x) => x[0] === 'conversation' && x[1] === 'project:viaggi'),\n"
        "  'eri nel quaderno e non ci sei rimasta');\n"
        "assert.ok(history.some((x) => x[0] === 'tendina'));\n"
        "assert.ok(history.some((x) => x[0] === 'pagine'));\n"
        # La tendina rilegge prima del cambio: il titolo chiede alla sua cache
        # quante pagine ha il quaderno, e sul telefono la pastiglia perdeva il
        # numero (23/09/2026).
        "const order = history.map((x) => x[0]);\n"
        "assert.ok(order.indexOf('tendina') < order.indexOf('conversation'),\n"
        "  'la tendina rilegge dopo il cambio: la pastiglia perde il numero');\n",
        written=" viaggi ",
        corrente="project:viaggio",
    )


def test_renaming_another_notebook_leaves_you_where_you_are() -> None:
    _run_rinomina(
        "await g.renameNotebook('viaggio');\n"
        "assert.ok(!history.some((x) => x[0] === 'conversation'), 'ti ha spostato');\n"
        "assert.ok(history.some((x) => x[0] === 'pagine'));\n",
        written="viaggi",
        corrente=None,
    )


@pytest.mark.parametrize("written", [None, "", "   ", "viaggio"], ids=["cancel", "vuoto", "spazi", "uguale"])
def test_nothing_to_rename_asks_nothing_of_the_gateway(written) -> None:
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.deepEqual(history.map((x) => x[0]), ['chiede']);\n",
        written=written,
        corrente="project:viaggio",
    )


def test_a_name_that_would_not_open_is_said_before_the_round_trip() -> None:
    """La stessa regola del gateway, detta subito: senza, «Ricerca ETF»
    andrebbe e tornerebbe col suo rifiuto."""
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.ok(!history.some((x) => x[0] === 'rpc'), 'un nome non valido e arrivato al gateway');\n"
        "assert.deepEqual(history.at(-1), ['avviso', 'scope.invalidName', 'error']);\n",
        written="Ricerca ETF",
        corrente=None,
    )


def test_a_refused_rename_changes_nothing_at_home() -> None:
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.deepEqual(history.map((x) => x[0]), ['chiede', 'rpc', 'avviso']);\n"
        "assert.equal(history.at(-1)[2], 'error');\n",
        written="viaggi",
        corrente="project:viaggio",
        rifiuta=True,
    )


def test_a_rename_refused_while_jenny_works_there_is_said_in_the_readers_language() -> None:
    """Il rifiuto ``conflict`` (un turno, un subagent, una passata del giardiniere
    in corso) e' una condizione attesa: la sua frase sta nell'i18n, non nel testo
    inglese del server. Un altro errore resta quello di sempre, col motivo."""
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.deepEqual(history.at(-1), ['avviso', 'home.notebook.renameBusy|viaggio', 'error']);\n",
        written="viaggi",
        corrente="project:viaggio",
        rifiuta="conflict",
    )
    _run_rinomina(
        "await g.renameNotebook('viaggio');\n"
        "assert.deepEqual(history.at(-1), ['avviso',\n"
        "  'home.notebook.renameFailed:a folder named viaggi already exists', 'error']);\n",
        written="viaggi",
        corrente="project:viaggio",
        rifiuta=True,
    )


@pytest.mark.parametrize(
    ("codice", "attesa"),
    [
        ("name_taken", "home.notebook.renameTaken|viaggi"),
        ("not_found", "home.notebook.renameMissing|viaggio"),
    ],
)
def test_the_expected_refusals_are_said_in_the_readers_language(codice, attesa) -> None:
    """Q4 della revisione profonda: «a folder named viaggi already exists» finiva
    tale e quale dentro la frase italiana. Il nome occupato e' quello **nuovo**,
    il quaderno sparito e' il **vecchio**; nessuno dei due porta il testo del
    server."""
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        f"assert.deepEqual(history.at(-1), ['avviso', {json.dumps(attesa)}, 'error']);\n",
        written="viaggi",
        corrente="project:viaggio",
        rifiuta=codice,
    )


def test_the_refusal_keys_exist_in_both_languages() -> None:
    from support.js_harness import locale

    for lingua in ("it", "en"):
        notebook = locale(lingua)["home"]["notebook"]
        for key in ("renameBusy", "renameTaken", "renameMissing", "renameFailed"):
            assert notebook.get(key), f"{lingua}: casa.notebook.{key}"


def test_the_sheet_gets_its_rename_row_from_the_shell() -> None:
    """La riga «Rinomina» c'e' solo se il guscio sa rinominare: adesso sa."""
    app_js = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    card = app_js.split("notebookCard() {", 1)[1].split("\n  }\n", 1)[0]
    assert "rename: (name) => this.renameNotebook(name)" in card
