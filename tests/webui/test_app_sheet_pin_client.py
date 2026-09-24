"""La scheda di una Jenny App, aperta con una pressione lunga dal cassetto.

In casa ha quattro righe — Apri · Metti come pagina · Modifica · Elimina — nello
stesso ordine della scheda di un quaderno: **una cosa si appende dal posto dove
vive** (`.agent/pagine-dal-posto-plan.md`). La stessa scheda la disegna
l'officina, che le pagine non le ha: li' deve restare **identica a prima**.

In node sui file veri, come `test_casa_pista_client.py`: il modulo si importa
davvero, i suoi vicini sono finti. Il DOM finto non analizza l'HTML — la scheda
lo scrive come testo — quindi si legge quel testo: quali righe, in che ordine,
quali spente e perche'.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


_VICINI = {
    "api-client.js": """
export const api = {
  cancellate: [],
  async deleteJennyApp(slug) { this.cancellate.push(slug); },
  getSecret() { return 'ok'; },
};
""",
    "utils.js": """
export const avvisi = [];
export function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
export function showToast(testo, tipo) { avvisi.push([testo, tipo]); }
""",
    "dialog.js": "export async function confirmDialog() { return true; }\n",
    "i18n.js": "export const i18n = { t: (k) => k, onLocaleChange() {} };\n",
    "ws-manager.js": "export const wsManager = { on() {}, off() {}, request() {} };\n",
    "theme.js": """
export function currentTheme() { return { scheme: 'dark', accent: '#b2543f', onAccent: '#fff' }; }
export function themeTokens() { return ''; }
""",
}

_FINTO_DOM = """
const elementi = new Map();
function creaEl(id) {
  const el = {
    id, innerHTML: '', open: false, onclick: null,
    showModal() { this.open = true; },
    close() { this.open = false; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
  };
  if (id) elementi.set(id, el);
  return el;
}
for (const id of ['jenny-app-sheet', 'jenny-app-sheet-title',
                  'jenny-app-sheet-actions', 'jenny-app-sheet-cancel']) creaEl(id);
globalThis.document = {
  getElementById: (id) => elementi.get(id) || null,
  createElement: () => creaEl(null),
  documentElement: { lang: 'it' },
  body: creaEl('body'),
};
globalThis.window = { addEventListener() {} };
globalThis.MutationObserver = class { observe() {} };

/* Le righe della scheda, lette dal testo che scrive: azione, spenta, perche'. */
function righe() {
  const html = elementi.get('jenny-app-sheet-actions').innerHTML;
  return [...html.matchAll(/<button[^>]*data-action="([^"]+)"([^>]*)>([\\s\\S]*?)<\\/button>/g)]
    .map(([, azione, attr, dentro]) => ({
      azione,
      spenta: /\\bdisabled\\b/.test(attr),
      perche: (dentro.match(/oc-sheet-reason">([^<]*)</) || [])[1] || null,
    }));
}
"""


def _run(corpo: str, *, app: dict, pagine: str | None) -> None:
    """*pagine*: il JS della porta che il guscio passa, o `None` per l'officina."""
    porta = "null" if pagine is None else pagine
    script = (
        "import assert from 'node:assert/strict';\n"
        + _FINTO_DOM
        + textwrap.dedent(
            f"""
            const {{ AppsActions }} = await import('./shared/apps-actions.js');
            const {{ api }} = await import('./shared/api-client.js');
            const {{ avvisi }} = await import('./shared/utils.js');
            const chiamate = [];
            const APP = {json.dumps(app)};
            const fonte = {{
              jennyApps: [APP],
              async loadJennyApps() {{ chiamate.push(['rilette']); }},
              onAppDataChanged() {{ return () => {{}}; }},
            }};
            const PORTA = {porta};
            const shell = {{ sendChatPrompt() {{}} }};
            if (PORTA) shell.pagine = () => PORTA;
            const azioni = new AppsActions(fonte, shell);
            azioni.showJennyAppSheet(APP.slug);
            """
        )
        + corpo
    )
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "apps-actions.js", radice / "shared" / "apps-actions.js")
        for nome, testo in _VICINI.items():
            (radice / "shared" / nome).write_text(testo, encoding="utf-8")
        entry = radice / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        proc = subprocess.run([str(_NODE), str(entry)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr or proc.stdout


ORTO = {"slug": "orto", "name": "Orto"}


def _porta(stato: str) -> str:
    return (
        "{ stato: (k, r) => { chiamate.push(['stato', k, r]); return '" + stato + "'; },"
        "  appendi: async (k, r) => { chiamate.push(['appendi', k, r]); return true; },"
        "  stacca: async (k, r) => { chiamate.push(['stacca', k, r]); return true; },"
        "  ricarica: async () => { chiamate.push(['ricarica']); } }"
    )


# ── Le righe ────────────────────────────────────────────────────────────────


def test_in_the_home_the_sheet_has_four_rows_in_order() -> None:
    """Apri · Metti come pagina · Modifica · Elimina — come la scheda di un quaderno."""
    _run(
        "assert.deepEqual(righe().map((r) => r.azione), ['open', 'pin', 'edit', 'delete']);\n"
        "assert.deepEqual(chiamate[0], ['stato', 'app', 'orto']);\n",
        app=ORTO,
        pagine=_porta("libera"),
    )


def test_in_the_workshop_the_sheet_is_exactly_as_before() -> None:
    """L'officina le pagine non le ha: niente riga, e il markup di sempre.

    «Senza rompere niente» vuol dire anche questo: la stessa funzione disegna
    le due schede, e l'unica differenza deve essere la porta che il guscio le
    passa — o non le passa.
    """
    _run(
        "assert.deepEqual(righe().map((r) => r.azione), ['open', 'edit', 'delete']);\n"
        "const html = document.getElementById('jenny-app-sheet-actions').innerHTML;\n"
        "assert.ok(!html.includes('oc-sheet-label'), 'le righe dell officina hanno cambiato forma');\n"
        "assert.ok(!html.includes('disabled'));\n",
        app=ORTO,
        pagine=None,
    )


def test_a_pinned_app_offers_to_unpin_it() -> None:
    _run(
        "assert.deepEqual(righe().map((r) => r.azione), ['open', 'unpin', 'edit', 'delete']);\n",
        app=ORTO,
        pagine=_porta("appesa"),
    )


@pytest.mark.parametrize(
    ("app", "stato", "perche"),
    [
        ({"slug": "waterbot", "name": "WaterBot", "view_kind": "external"}, "libera", "apps.pageExternal"),
        ({"slug": "rotta", "name": "Rotta", "broken": True}, "libera", "apps.pageBroken"),
        (ORTO, "piena", "apps.pageFull"),
    ],
    ids=["external", "broken", "full"],
)
def test_a_row_that_cannot_be_used_is_shown_off_with_its_reason(app, stato, perche) -> None:
    """**Spenta, non assente**: una riga che manca fa chiedere «perche' Todo si'
    e WaterBot no?», una spenta lo dice."""
    _run(
        "const r = righe()[1];\n"
        "assert.equal(r.azione, 'pin');\n"
        "assert.equal(r.spenta, true, 'la riga si puo toccare');\n"
        f"assert.equal(r.perche, {json.dumps(perche)});\n",
        app=app,
        pagine=_porta(stato),
    )


def test_a_row_that_can_be_used_is_not_off() -> None:
    _run(
        "const r = righe()[1];\n"
        "assert.equal(r.spenta, false);\n"
        "assert.equal(r.perche, null);\n",
        app=ORTO,
        pagine=_porta("libera"),
    )


# ── Cosa fanno ──────────────────────────────────────────────────────────────


def test_pin_asks_the_pages_for_this_app() -> None:
    _run(
        "await azioni._handleJennySheetAction('pin', APP);\n"
        "assert.deepEqual(chiamate.at(-1), ['appendi', 'app', 'orto']);\n",
        app=ORTO,
        pagine=_porta("libera"),
    )


def test_unpin_takes_it_off_and_says_so() -> None:
    _run(
        "await azioni._handleJennySheetAction('unpin', APP);\n"
        "assert.deepEqual(chiamate.at(-1), ['stacca', 'app', 'orto']);\n"
        "assert.deepEqual(avvisi.at(-1), ['apps.unpinned', 'success']);\n",
        app=ORTO,
        pagine=_porta("appesa"),
    )


def test_deleting_an_app_rereads_the_pages() -> None:
    """Il gateway toglie la pagina insieme all'app; la casa lo deve sapere, o
    resterebbe un pallino verso un'app che non c'e' piu'."""
    _run(
        "await azioni._handleJennySheetAction('delete', APP);\n"
        "assert.deepEqual(api.cancellate, ['orto']);\n"
        "assert.ok(chiamate.some((c) => c[0] === 'ricarica'), 'le pagine non sono state rilette');\n",
        app=ORTO,
        pagine=_porta("appesa"),
    )


def test_deleting_from_the_workshop_needs_no_pages() -> None:
    """Senza porta la cancellazione va come prima, senza inciampare."""
    _run(
        "await azioni._handleJennySheetAction('delete', APP);\n"
        "assert.deepEqual(api.cancellate, ['orto']);\n"
        "assert.deepEqual(avvisi.at(-1), ['apps.appDeleted', 'success']);\n",
        app=ORTO,
        pagine=None,
    )


def test_back_on_an_app_opened_from_the_home_closes_it() -> None:
    """In casa non ci sono le schede dell'officina: chiusa l'app, sotto c'e'
    gia' la pagina da cui l'hai aperta. `handleBack` chiamava lo `switchMode`
    dell'officina, che in casa non esiste — un TypeError a ogni Indietro, e
    l'app restava aperta (trovato il 23/09/2026 scrivendo la pagina App)."""
    _run(
        "let tolta = false;\n"
        "azioni._openApp = { slug: 'orto', depth: 1, iframe: {},\n"
        "  overlay: { classList: { remove() {} }, remove() { tolta = true; } } };\n"
        "window.mobileApp = { launcher: { isOpen: () => false } };\n"
        "assert.equal(azioni.handleBack(), true);\n"
        "assert.equal(azioni._openApp, null, 'l app e rimasta aperta');\n"
        "assert.equal(azioni.handleBack(), false, 'con niente aperto Indietro non e suo');\n",
        app=ORTO,
        pagine=_porta("libera"),
    )
