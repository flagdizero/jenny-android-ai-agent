"""La scheda di una Jenny App, aperta con una pressione lunga dal cassetto.

In casa ha quattro righe — Apri · Metti come pagina · Modifica · Elimina — nello
stesso ordine della scheda di un quaderno: **una cosa si appende dal posto dove
vive** (`.agent/pagine-dal-posto-plan.md`). La stessa scheda la disegna
l'officina, che le pagine non le ha: li' deve restare **identica a prima**.

In node sui file veri, come `test_home_track_client.py`: il modulo si importa
davvero, i suoi vicini sono finti. Il DOM finto non analizza l'HTML — la scheda
lo scrive come testo — quindi si legge quel testo: quali righe, in che ordine,
quali spente e perche'.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import textwrap
from pathlib import Path

import pytest
from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node


_NEIGHBORS = {
    "api-client.js": """
export const api = {
  cancellate: [],
  async deleteJennyApp(slug) { this.cancellate.push(slug); },
  getSecret() { return 'ok'; },
};
""",
    "utils.js": """
export const notices = [];
export function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
export function showToast(text, type) { notices.push([text, type]); }
""",
    "dialog.js": "export async function confirmDialog() { return true; }\n",
    "i18n.js": "export const i18n = { t: (k) => k };\n",
    "ws-manager.js": "export const wsManager = { on() {}, off() {}, request() {} };\n",
    "theme.js": """
export function currentTheme() { return { scheme: 'dark', accent: '#b2543f', onAccent: '#fff' }; }
export function themeTokens() { return ''; }
""",
}

_FAKE_DOM = """
const elements = new Map();
function createEl(id) {
  const el = {
    id, innerHTML: '', open: false, onclick: null,
    showModal() { this.open = true; },
    close() { this.open = false; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
  };
  if (id) elements.set(id, el);
  return el;
}
for (const id of ['jenny-app-sheet', 'jenny-app-sheet-title',
                  'jenny-app-sheet-actions', 'jenny-app-sheet-cancel']) createEl(id);
globalThis.document = {
  getElementById: (id) => elements.get(id) || null,
  createElement: () => createEl(null),
  documentElement: { lang: 'it' },
  body: createEl('body'),
};
globalThis.window = { addEventListener() {} };
globalThis.MutationObserver = class { observe() {} };

/* Le righe della scheda, lette dal testo che scrive: azione, spenta, perche'. */
function rows() {
  const html = elements.get('jenny-app-sheet-actions').innerHTML;
  return [...html.matchAll(/<button[^>]*data-action="([^"]+)"([^>]*)>([\\s\\S]*?)<\\/button>/g)]
    .map(([, action, attr, inside]) => ({
      action,
      off: /\\bdisabled\\b/.test(attr),
      why: (inside.match(/oc-sheet-reason">([^<]*)</) || [])[1] || null,
    }));
}
"""


def _run(body: str, *, app: dict, pages: str | None) -> None:
    """*pagine*: il JS della porta che il guscio passa, o `None` per l'officina."""
    door = "null" if pages is None else pages
    script = (
        "import assert from 'node:assert/strict';\n"
        + _FAKE_DOM
        + textwrap.dedent(
            f"""
            const {{ AppsActions }} = await import('./shared/apps-actions.js');
            const {{ api }} = await import('./shared/api-client.js');
            const {{ notices }} = await import('./shared/utils.js');
            const calls = [];
            const APP = {json.dumps(app)};
            const source = {{
              jennyApps: [APP],
              async loadJennyApps() {{ calls.push(['rilette']); }},
              onAppDataChanged() {{ return () => {{}}; }},
            }};
            const DOOR = {door};
            const shell = {{ sendChatPrompt() {{}} }};
            if (DOOR) shell.homePages = () => DOOR;
            const actions = new AppsActions(source, shell);
            actions.showJennyAppSheet(APP.slug);
            """
        )
        + body
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "apps-actions.js", root / "shared" / "apps-actions.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        run_module(entry)


GARDEN = {"slug": "orto", "name": "Orto"}


def _door(state: str) -> str:
    return (
        "{ state: (k, r) => { calls.push(['stato', k, r]); return '" + state + "'; },"
        "  append: async (k, r) => { calls.push(['appendi', k, r]); return true; },"
        "  detach: async (k, r) => { calls.push(['stacca', k, r]); return true; },"
        "  reload: async () => { calls.push(['ricarica']); } }"
    )


# ── Le righe ────────────────────────────────────────────────────────────────


def test_in_the_home_the_sheet_has_four_rows_in_order() -> None:
    """Apri · Metti come pagina · Modifica · Elimina — come la scheda di un quaderno."""
    _run(
        "assert.deepEqual(rows().map((r) => r.action), ['open', 'pin', 'edit', 'delete']);\n"
        "assert.deepEqual(calls[0], ['stato', 'app', 'orto']);\n",
        app=GARDEN,
        pages=_door("free"),
    )


def test_in_the_workshop_the_sheet_is_exactly_as_before() -> None:
    """L'officina le pagine non le ha: niente riga, e il markup di sempre.

    «Senza rompere niente» vuol dire anche questo: la stessa funzione disegna
    le due schede, e l'unica differenza deve essere la porta che il guscio le
    passa — o non le passa.
    """
    _run(
        "assert.deepEqual(rows().map((r) => r.action), ['open', 'edit', 'delete']);\n"
        "const html = document.getElementById('jenny-app-sheet-actions').innerHTML;\n"
        "assert.ok(!html.includes('oc-sheet-label'), 'le righe dell officina hanno cambiato forma');\n"
        "assert.ok(!html.includes('disabled'));\n",
        app=GARDEN,
        pages=None,
    )


def test_a_pinned_app_offers_to_unpin_it() -> None:
    _run(
        "assert.deepEqual(rows().map((r) => r.action), ['open', 'unpin', 'edit', 'delete']);\n",
        app=GARDEN,
        pages=_door("pending"),
    )


@pytest.mark.parametrize(
    ("app", "state", "why"),
    [
        ({"slug": "waterbot", "name": "WaterBot", "view_kind": "external"}, "free", "apps.pageExternal"),
        ({"slug": "rotta", "name": "Rotta", "broken": True}, "free", "apps.pageBroken"),
        (GARDEN, "piena", "apps.pageFull"),
    ],
    ids=["external", "broken", "full"],
)
def test_a_row_that_cannot_be_used_is_shown_off_with_its_reason(app, state, why) -> None:
    """**Spenta, non assente**: una riga che manca fa chiedere «perche' Todo si'
    e WaterBot no?», una spenta lo dice."""
    _run(
        "const r = rows()[1];\n"
        "assert.equal(r.action, 'pin');\n"
        "assert.equal(r.off, true, 'la riga si puo toccare');\n"
        f"assert.equal(r.why, {json.dumps(why)});\n",
        app=app,
        pages=_door(state),
    )


def test_a_row_that_can_be_used_is_not_off() -> None:
    _run(
        "const r = rows()[1];\n"
        "assert.equal(r.off, false);\n"
        "assert.equal(r.why, null);\n",
        app=GARDEN,
        pages=_door("free"),
    )


# ── Cosa fanno ──────────────────────────────────────────────────────────────


def test_pin_asks_the_pages_for_this_app() -> None:
    _run(
        "await actions._handleJennySheetAction('pin', APP);\n"
        "assert.deepEqual(calls.at(-1), ['appendi', 'app', 'orto']);\n",
        app=GARDEN,
        pages=_door("free"),
    )


def test_unpin_takes_it_off_and_says_so() -> None:
    _run(
        "await actions._handleJennySheetAction('unpin', APP);\n"
        "assert.deepEqual(calls.at(-1), ['stacca', 'app', 'orto']);\n"
        "assert.deepEqual(notices.at(-1), ['apps.unpinned', 'success']);\n",
        app=GARDEN,
        pages=_door("pending"),
    )


def test_deleting_an_app_rereads_the_pages() -> None:
    """Il gateway toglie la pagina insieme all'app; la casa lo deve sapere, o
    resterebbe un pallino verso un'app che non c'e' piu'."""
    _run(
        "await actions._handleJennySheetAction('delete', APP);\n"
        "assert.deepEqual(api.cancellate, ['orto']);\n"
        "assert.ok(calls.some((c) => c[0] === 'ricarica'), 'le pagine non sono state rilette');\n",
        app=GARDEN,
        pages=_door("pending"),
    )


def test_deleting_from_the_workshop_needs_no_pages() -> None:
    """Senza porta la cancellazione va come prima, senza inciampare."""
    _run(
        "await actions._handleJennySheetAction('delete', APP);\n"
        "assert.deepEqual(api.cancellate, ['orto']);\n"
        "assert.deepEqual(notices.at(-1), ['apps.appDeleted', 'success']);\n",
        app=GARDEN,
        pages=None,
    )


def test_back_on_an_app_opened_from_the_home_closes_it() -> None:
    """In casa non ci sono le schede dell'officina: chiusa l'app, sotto c'e'
    gia' la pagina da cui l'hai aperta. `handleBack` chiamava lo `switchMode`
    dell'officina, che in casa non esiste — un TypeError a ogni Indietro, e
    l'app restava aperta (trovato il 23/09/2026 scrivendo la pagina App)."""
    _run(
        "let tolta = false;\n"
        "actions._openApp = { slug: 'orto', depth: 1, iframe: {},\n"
        "  overlay: { classList: { remove() {} }, remove() { tolta = true; } } };\n"
        "window.mobileApp = { launcher: { isOpen: () => false } };\n"
        "assert.equal(actions.handleBack(), true);\n"
        "assert.equal(actions._openApp, null, 'l app e rimasta aperta');\n"
        "assert.equal(actions.handleBack(), false, 'con niente aperto Indietro non e suo');\n",
        app=GARDEN,
        pages=_door("free"),
    )
