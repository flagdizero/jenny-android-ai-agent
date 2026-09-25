"""La scheda di un quaderno, e il seguito di una cancellazione in casa.

Tenere premuto un quaderno nella tendina «Con chi parli» apre la sua scheda:
Apri · Metti come pagina · Rinomina · Elimina — le stesse righe, nello stesso
ordine, della scheda di un'app nel cassetto. **Una cosa si appende dal posto
dove vive** (`.agent/pagine-dal-posto-plan.md`).

`casa-quaderno.js` si importa vero, coi suoi vicini finti; `apps-actions.js`
invece e' vero anche lui, perche' la riga la disegna la sua `disegnaRiga` — la
scheda e' la stessa cosa a vedersi, e una seconda copia del disegno
divergerebbe al primo ritocco.
"""

from __future__ import annotations

import json
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
for (const id of ['casa-quaderno-sheet', 'casa-quaderno-sheet-title',
                  'casa-quaderno-sheet-actions', 'casa-quaderno-sheet-cancel']) creaEl(id);
globalThis.document = {
  getElementById: (id) => elementi.get(id) || null,
  createElement: () => creaEl(null),
  documentElement: { lang: 'it' },
};
globalThis.window = { addEventListener() {} };
globalThis.MutationObserver = class { observe() {} };

function righe() {
  const html = elementi.get('casa-quaderno-sheet-actions').innerHTML;
  return [...html.matchAll(/<button[^>]*data-action="([^"]+)"([^>]*)>/g)]
    .map(([, azione, attr]) => ({ azione, spenta: /\\bdisabled\\b/.test(attr) }));
}
"""


def _run(corpo: str, *, stato: str | None = "libera", rinomina: bool = False) -> None:
    porta = (
        "null"
        if stato is None
        else "{ stato: (k, r) => { chiamate.push(['stato', k, r]); return '" + stato + "'; },"
        "  appendi: async (k, r) => { chiamate.push(['appendi', k, r]); return true; },"
        "  stacca: async (k, r) => { chiamate.push(['stacca', k, r]); return true; } }"
    )
    script = (
        "import assert from 'node:assert/strict';\n"
        + _FINTO_DOM
        + textwrap.dedent(
            f"""
            const {{ SchedaQuaderno }} = await import('./casa-quaderno.js');
            const chiamate = [];
            const PORTA = {porta};
            const guscio = {{
              pagine: () => PORTA,
              apri: (n) => chiamate.push(['apri', n]),
              elimina: (n) => chiamate.push(['elimina', n]),
            }};
            if ({json.dumps(rinomina)}) guscio.rinomina = (n) => chiamate.push(['rinomina', n]);
            const scheda = new SchedaQuaderno(guscio);
            """
        )
        + corpo
    )
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        shutil.copy(ASSETS / "casa-quaderno.js", radice / "casa-quaderno.js")
        shutil.copy(ASSETS / "shared" / "apps-actions.js", radice / "shared" / "apps-actions.js")
        for nome, testo in _VICINI.items():
            (radice / "shared" / nome).write_text(testo, encoding="utf-8")
        entry = radice / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        run_module(entry)


# ── Le righe ────────────────────────────────────────────────────────────────


def test_the_rows_are_the_app_sheets_rows_in_the_same_order() -> None:
    """Chi ha imparato la scheda di un'app ha imparato questa."""
    _run(
        "scheda.mostra('piante');\n"
        "assert.deepEqual(righe().map((r) => r.azione), ['open', 'pin', 'rename', 'delete']);\n"
        "assert.deepEqual(chiamate[0], ['stato', 'conversazione', 'project:piante']);\n"
        "assert.equal(document.getElementById('casa-quaderno-sheet').open, true);\n",
        rinomina=True,
    )


def test_without_a_way_to_rename_there_is_no_rename_row() -> None:
    """Una riga che non fa niente e' peggio di una riga che manca."""
    _run(
        "scheda.mostra('piante');\n"
        "assert.deepEqual(righe().map((r) => r.azione), ['open', 'pin', 'delete']);\n",
        rinomina=False,
    )


def test_a_pinned_notebook_offers_to_unpin_it() -> None:
    _run(
        "scheda.mostra('piante');\n"
        "assert.equal(righe()[1].azione, 'unpin');\n",
        stato="appesa",
    )


def test_with_the_pages_full_the_pin_row_is_off() -> None:
    _run(
        "scheda.mostra('piante');\n"
        "assert.equal(righe()[1].azione, 'pin');\n"
        "assert.equal(righe()[1].spenta, true);\n",
        stato="piena",
    )


def test_the_name_in_the_title_is_text_not_markup() -> None:
    """Il nome del quaderno viene dal disco: nel titolo e' testo."""
    _run(
        "scheda.mostra('<b>x');\n"
        "const t = document.getElementById('casa-quaderno-sheet-title').innerHTML;\n"
        "assert.ok(t.includes('&lt;b>x'), t);\n"
    )


# ── Cosa fanno ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("azione", "attesa"),
    [
        ("open", ["apri", "piante"]),
        ("pin", ["appendi", "conversazione", "project:piante"]),
        ("unpin", ["stacca", "conversazione", "project:piante"]),
        ("rename", ["rinomina", "piante"]),
        ("delete", ["elimina", "piante"]),
    ],
)
def test_each_row_asks_the_shell(azione, attesa) -> None:
    _run(
        f"await scheda.fai({json.dumps(azione)}, 'piante');\n"
        f"assert.deepEqual(chiamate.at(-1), {json.dumps(attesa)});\n",
        rinomina=True,
    )


# ── Il seguito di una cancellazione, in casa ────────────────────────────────


def _member(source: str, name: str) -> str:
    return member(source, name, prefixes=("async ",))


def _run_seguito(corpo: str, *, confermato: bool, corrente: str | None) -> None:
    metodo = _member((ASSETS / "casa-app.js").read_text(encoding="utf-8"), "deleteNotebook")
    script = textwrap.dedent(
        f"""
        import assert from 'node:assert/strict';
        const storia = [];
        const NOTEBOOK_DELETE_WORDS = {{ confirm: 'c' }};
        async function deleteProjectFlow(nome, parole) {{
          storia.push(['chiede', nome, parole === NOTEBOOK_DELETE_WORDS]);
          return {json.dumps(confermato)};
        }}
        const projectNameOf = (k) => (k && k.startsWith('project:') ? k.slice(8) : null);
        const projectKey = (n) => 'project:' + n;
        const sessionManager = {{ currentKey: {json.dumps(corrente)}, personalKey: 'websocket:default' }};
        const i18n = {{ t: (k) => k }};
        function showToast(t) {{ storia.push(['avviso', t]); }}
        class Guscio {{
          constructor() {{
            this.who = {{ refresh: async () => storia.push(['tendina']) }};
            this._drafts = new Map();
          }}
          /* La chat cambia dove sta: passare dalla regola delle pagine, dalla
             pagina Quaderni, porterebbe alla pagina chat. */
          async mostraConversazione(k) {{ storia.push(['conversazione', k]); }}
          async switchConversation(k) {{ storia.push(['dirottata', k]); }}
          portaPagine() {{ return {{ ricarica: async () => storia.push(['pagine']) }}; }}
          {metodo}
        }}
        const g = new Guscio();
        """
    ) + corpo
    run_js(script)


def test_a_delete_asks_with_the_notebook_words() -> None:
    """In casa quel che si cancella e' un quaderno, non un progetto."""
    _run_seguito(
        "await g.deleteNotebook('piante');\n"
        "assert.deepEqual(storia[0], ['chiede', 'piante', true]);\n",
        confermato=True,
        corrente=None,
    )


def test_deleting_the_notebook_you_are_in_takes_you_home() -> None:
    """Restare in una chat che non esiste piu' vorrebbe dire scrivere a vuoto."""
    _run_seguito(
        "await g.deleteNotebook('piante');\n"
        "assert.deepEqual(storia.map((x) => x[0]), ['chiede', 'conversazione', 'tendina', 'pagine', 'avviso']);\n"
        "assert.deepEqual(storia[1], ['conversazione', null]);\n",
        confermato=True,
        corrente="project:piante",
    )


def test_deleting_another_notebook_leaves_you_where_you_are() -> None:
    _run_seguito(
        "await g.deleteNotebook('piante');\n"
        "assert.ok(!storia.some((x) => x[0] === 'conversazione'), 'ti ha spostato');\n"
        "assert.ok(storia.some((x) => x[0] === 'pagine'), 'le pagine non sono state rilette');\n"
        "assert.ok(storia.some((x) => x[0] === 'tendina'), 'la tendina non si e ridisegnata');\n",
        confermato=True,
        corrente="project:altro",
    )


def test_saying_no_changes_nothing() -> None:
    _run_seguito(
        "const fatto = await g.deleteNotebook('piante');\n"
        "assert.equal(fatto, false);\n"
        "assert.deepEqual(storia.map((x) => x[0]), ['chiede']);\n",
        confermato=False,
        corrente="project:piante",
    )


# ── Il guscio ───────────────────────────────────────────────────────────────


def test_back_closes_the_notebook_sheet_before_anything_else() -> None:
    """La scheda sta nel top layer, **sopra** la pagina Quaderni da cui si
    apre: Indietro chiude prima lei, e solo alla pressione dopo lascia la
    pagina."""
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    catena = app_js.split("_closeOverlays() {", 1)[1].split("\n  }\n", 1)[0]
    assert "'casa-quaderno-sheet'" in catena
    assert catena.index("casa-quaderno-sheet") < catena.index("handleBack()")


def test_the_sheet_is_in_the_page_and_shipped() -> None:
    html = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    for id_ in ("casa-quaderno-sheet", "casa-quaderno-sheet-title",
                "casa-quaderno-sheet-actions", "casa-quaderno-sheet-cancel"):
        assert f'id="{id_}"' in html, id_
    manifest = (ROOT / "jenny" / "utils" / "android_assets.py").read_text(encoding="utf-8")
    assert '"assets/casa-quaderno.js"' in manifest


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
    corpo: str, *, scritto: str | None, corrente: str | None, rifiuta: bool | str = False,
) -> None:
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    metodo = _member(app_js, "renameNotebook")
    bozza = _member(app_js, "_rinominaBozza")
    script = textwrap.dedent(
        f"""
        import assert from 'node:assert/strict';
        const {{ isOpenableProjectName }} = await import({json.dumps((ASSETS / "shared" / "conversation-list.js").as_uri())});
        const storia = [];
        const projectKey = (n) => 'project:' + n;
        async function promptDialog(msg, opz) {{ storia.push(['chiede', opz.initial]); return {json.dumps(scritto)}; }}
        const rpc = {{
          async renameProject(a, b) {{
            storia.push(['rpc', a, b]);
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
        function showToast(t, tipo) {{ storia.push(['avviso', t, tipo]); }}
        class Guscio {{
          constructor() {{
            this.who = {{ refresh: async () => storia.push(['tendina']) }};
            this.pagine = {{ rinominaConversazione: (a, b) => storia.push(['pagina0', a, b]) }};
            this._drafts = new Map();
            this.input = {{ value: '' }};
          }}
          async mostraConversazione(k) {{ storia.push(['conversazione', k]); }}
          async switchConversation(k) {{ storia.push(['dirottata', k]); }}
          portaPagine() {{ return {{ ricarica: async () => storia.push(['pagine']) }}; }}
          {metodo}
          {bozza}
        }}
        const g = new Guscio();
        """
    ) + corpo
    run_js(script)


def test_renaming_the_notebook_you_are_in_keeps_you_there_under_the_new_name() -> None:
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), true);\n"
        "assert.deepEqual(storia[0], ['chiede', 'viaggio'], 'la domanda non parte dal nome attuale');\n"
        "assert.deepEqual(storia[1], ['rpc', 'viaggio', 'viaggi']);\n"
        "assert.ok(storia.some((x) => x[0] === 'pagina0' && x[2] === 'project:viaggi'));\n"
        "assert.ok(storia.some((x) => x[0] === 'conversazione' && x[1] === 'project:viaggi'),\n"
        "  'eri nel quaderno e non ci sei rimasta');\n"
        "assert.ok(storia.some((x) => x[0] === 'tendina'));\n"
        "assert.ok(storia.some((x) => x[0] === 'pagine'));\n"
        # La tendina rilegge prima del cambio: il titolo chiede alla sua cache
        # quante pagine ha il quaderno, e sul telefono la pastiglia perdeva il
        # numero (23/09/2026).
        "const ordine = storia.map((x) => x[0]);\n"
        "assert.ok(ordine.indexOf('tendina') < ordine.indexOf('conversazione'),\n"
        "  'la tendina rilegge dopo il cambio: la pastiglia perde il numero');\n",
        scritto=" viaggi ",
        corrente="project:viaggio",
    )


def test_renaming_another_notebook_leaves_you_where_you_are() -> None:
    _run_rinomina(
        "await g.renameNotebook('viaggio');\n"
        "assert.ok(!storia.some((x) => x[0] === 'conversazione'), 'ti ha spostato');\n"
        "assert.ok(storia.some((x) => x[0] === 'pagine'));\n",
        scritto="viaggi",
        corrente=None,
    )


@pytest.mark.parametrize("scritto", [None, "", "   ", "viaggio"], ids=["annulla", "vuoto", "spazi", "uguale"])
def test_nothing_to_rename_asks_nothing_of_the_gateway(scritto) -> None:
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.deepEqual(storia.map((x) => x[0]), ['chiede']);\n",
        scritto=scritto,
        corrente="project:viaggio",
    )


def test_a_name_that_would_not_open_is_said_before_the_round_trip() -> None:
    """La stessa regola del gateway, detta subito: senza, «Ricerca ETF»
    andrebbe e tornerebbe col suo rifiuto."""
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.ok(!storia.some((x) => x[0] === 'rpc'), 'un nome non valido e arrivato al gateway');\n"
        "assert.deepEqual(storia.at(-1), ['avviso', 'scope.invalidName', 'error']);\n",
        scritto="Ricerca ETF",
        corrente=None,
    )


def test_a_refused_rename_changes_nothing_at_home() -> None:
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.deepEqual(storia.map((x) => x[0]), ['chiede', 'rpc', 'avviso']);\n"
        "assert.equal(storia.at(-1)[2], 'error');\n",
        scritto="viaggi",
        corrente="project:viaggio",
        rifiuta=True,
    )


def test_a_rename_refused_while_jenny_works_there_is_said_in_the_readers_language() -> None:
    """Il rifiuto ``conflict`` (un turno, un subagent, una passata del giardiniere
    in corso) e' una condizione attesa: la sua frase sta nell'i18n, non nel testo
    inglese del server. Un altro errore resta quello di sempre, col motivo."""
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        "assert.deepEqual(storia.at(-1), ['avviso', 'casa.quaderno.renameBusy|viaggio', 'error']);\n",
        scritto="viaggi",
        corrente="project:viaggio",
        rifiuta="conflict",
    )
    _run_rinomina(
        "await g.renameNotebook('viaggio');\n"
        "assert.deepEqual(storia.at(-1), ['avviso',\n"
        "  'casa.quaderno.renameFailed:a folder named viaggi already exists', 'error']);\n",
        scritto="viaggi",
        corrente="project:viaggio",
        rifiuta=True,
    )


@pytest.mark.parametrize(
    ("codice", "attesa"),
    [
        ("name_taken", "casa.quaderno.renameTaken|viaggi"),
        ("not_found", "casa.quaderno.renameMissing|viaggio"),
    ],
)
def test_the_expected_refusals_are_said_in_the_readers_language(codice, attesa) -> None:
    """Q4 della revisione profonda: «a folder named viaggi already exists» finiva
    tale e quale dentro la frase italiana. Il nome occupato e' quello **nuovo**,
    il quaderno sparito e' il **vecchio**; nessuno dei due porta il testo del
    server."""
    _run_rinomina(
        "assert.equal(await g.renameNotebook('viaggio'), false);\n"
        f"assert.deepEqual(storia.at(-1), ['avviso', {json.dumps(attesa)}, 'error']);\n",
        scritto="viaggi",
        corrente="project:viaggio",
        rifiuta=codice,
    )


def test_the_refusal_keys_exist_in_both_languages() -> None:
    from support.js_harness import locale

    for lingua in ("it", "en"):
        quaderno = locale(lingua)["casa"]["quaderno"]
        for chiave in ("renameBusy", "renameTaken", "renameMissing", "renameFailed"):
            assert quaderno.get(chiave), f"{lingua}: casa.quaderno.{chiave}"


def test_the_sheet_gets_its_rename_row_from_the_shell() -> None:
    """La riga «Rinomina» c'e' solo se il guscio sa rinominare: adesso sa."""
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    scheda = app_js.split("schedaQuaderno() {", 1)[1].split("\n  }\n", 1)[0]
    assert "rinomina: (nome) => this.renameNotebook(nome)" in scheda
