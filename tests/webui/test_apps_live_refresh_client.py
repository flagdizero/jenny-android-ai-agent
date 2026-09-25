"""Le Jenny App si accorgono dei cambi che arrivano dal gateway.

Due frame: ``apps_list_changed`` (un turno ha scritto in ``apps/``) e
``app_data_changed`` (un'azione di un'app e' girata come tool). Dal 21 al
24/09/2026 non li ascoltava nessuno (`98a0230` si era portato via i gestori
con la scheda «App»): il cassetto non vedeva un'app appena creata e la mini-app
aperta non si rileggeva. Il banco generale sui frame senza ascoltatore e'
``test_ws_events_have_listeners_contract.py``; qui si prova che l'ascolto fa
la cosa giusta.

In node, coi moduli veri (`shared/apps-source.js`, `shared/apps-actions.js`) e
i vicini finti; il WebSocket e' un ``EventTarget`` che il banco fa parlare.
La pagina app della casa ha il suo caso in ``test_casa_pista_client.py``.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node

_VICINI = {
    # Ogni lettura delle Jenny App si conta: e' la domanda di quasi ogni caso.
    "api-client.js": """
export const api = {
  letture: 0,
  async getJennyApps() { this.letture += 1; return { apps: [{ slug: 'orto', name: 'Orto' }] }; },
  async getAndroidApps() { return { apps: [] }; },
  getSecret() { return 'ok'; },
};
""",
    "utils.js": """
export function escapeHtml(s) { return String(s); }
export function showToast() {}
""",
    "dialog.js": "export async function confirmDialog() { return true; }\n",
    "i18n.js": "export const i18n = { t: (k) => k, locale: 'it' };\n",
    "ws-manager.js": "export const wsManager = new EventTarget();\n",
    "theme.js": """
export function currentTheme() { return { scheme: 'dark', accent: '#b2543f', onAccent: '#fff' }; }
export function themeTokens() { return ''; }
""",
}

_PRELUDIO = """
import assert from 'node:assert/strict';
globalThis.window = { addEventListener() {} };
globalThis.document = { documentElement: { lang: 'it' } };
globalThis.MutationObserver = class { observe() {} };
const { AppsSource } = await import('./shared/apps-source.js');
const { AppsActions } = await import('./shared/apps-actions.js');
const { api } = await import('./shared/api-client.js');
const { wsManager } = await import('./shared/ws-manager.js');

/* Un frame come lo consegna `ws-manager` vero: `chat:message` col frame nel
   `detail`. */
function frame(msg) {
  wsManager.dispatchEvent(new CustomEvent('chat:message', { detail: msg }));
}
const giro = () => new Promise((r) => setTimeout(r, 0));

/* Una mini-app aperta sopra tutto, con la finestra che ricorda la posta. */
function apri(azioni, slug) {
  const posta = [];
  azioni._openApp = { slug, iframe: { contentWindow: { postMessage: (m) => posta.push(m) } } };
  return posta;
}
"""


def _run(corpo: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        for nome in ("apps-source.js", "apps-actions.js"):
            shutil.copy(ASSETS / "shared" / nome, radice / "shared" / nome)
        for nome, testo in _VICINI.items():
            (radice / "shared" / nome).write_text(testo, encoding="utf-8")
        entry = radice / "prova.mjs"
        entry.write_text(_PRELUDIO + textwrap.dedent(corpo), encoding="utf-8")
        run_module(entry)


def test_a_list_already_read_is_read_again() -> None:
    _run(
        """
        const fonte = new AppsSource();
        await fonte.loadJennyApps();
        assert.equal(api.letture, 1);
        let avvisi = 0;
        fonte.addChangeListener(() => { avvisi += 1; });
        frame({ event: 'apps_list_changed' });
        // La rilettura non torna «in caricamento»: il cassetto aperto non
        // deve riaccendere lo scheletro per una risposta che arriva subito.
        assert.equal(fonte.isLoadingLists(), true, 'le Android non sono lette: resta vero');
        assert.equal(fonte._jennyLoaded, true);
        await giro();
        assert.equal(api.letture, 2);
        assert.equal(avvisi, 1, 'chi guarda il cassetto deve saperlo');
        """
    )


def test_a_list_nobody_asked_for_is_not_read() -> None:
    """Nessuno l'ha chiesta: non c'e' niente da tenere aggiornato, e la prima
    lettura avverra' quando servira'."""
    _run(
        """
        new AppsSource();
        frame({ event: 'apps_list_changed' });
        await giro();
        assert.equal(api.letture, 0);
        """
    )


def test_the_open_app_hears_that_its_data_changed() -> None:
    _run(
        """
        const fonte = new AppsSource();
        const azioni = new AppsActions(fonte, { sendChatPrompt() {} });
        const posta = apri(azioni, 'orto');
        frame({ event: 'app_data_changed', slug: 'orto' });
        assert.deepEqual(posta, [{ type: 'jenny:data-changed', slug: 'orto' }]);
        """
    )


def test_another_app_data_change_is_not_forwarded() -> None:
    _run(
        """
        const fonte = new AppsSource();
        const azioni = new AppsActions(fonte, { sendChatPrompt() {} });
        const posta = apri(azioni, 'orto');
        frame({ event: 'app_data_changed', slug: 'lampo' });
        frame({ event: 'app_data_changed' });
        assert.deepEqual(posta, []);
        // E senza app aperta non si rompe niente.
        azioni._openApp = null;
        frame({ event: 'app_data_changed', slug: 'orto' });
        """
    )


def test_a_listener_that_throws_does_not_silence_the_others() -> None:
    _run(
        """
        const fonte = new AppsSource();
        const visti = [];
        const errore = console.error;
        console.error = () => {};
        fonte.onAppDataChanged(() => { throw new Error('boom'); });
        fonte.onAppDataChanged((slug) => visti.push(slug));
        frame({ event: 'app_data_changed', slug: 'orto' });
        console.error = errore;
        assert.deepEqual(visti, ['orto']);
        """
    )


def test_unrelated_frames_do_nothing() -> None:
    _run(
        """
        const fonte = new AppsSource();
        await fonte.loadJennyApps();
        const visti = [];
        fonte.onAppDataChanged((slug) => visti.push(slug));
        for (const event of ['delta', 'turn_end', 'message', 'runtime_model_updated']) {
          frame({ event, slug: 'orto' });
        }
        await giro();
        assert.equal(api.letture, 1);
        assert.deepEqual(visti, []);
        """
    )


def test_an_older_answer_does_not_overwrite_a_newer_one() -> None:
    """Da quando anche il gateway fa rileggere l'elenco (``apps_list_changed``),
    due letture possono accavallarsi: vince l'ultima partita, non l'ultima
    arrivata. Stessa guardia che ``loadAndroidApps`` ha già (``_seqAndroid``)."""
    _run(
        """
        const fonte = new AppsSource();
        const attese = [];
        api.getJennyApps = () => new Promise((r) => attese.push(r));
        const prima = fonte.loadJennyApps();
        const seconda = fonte.loadJennyApps();
        attese[1]({ apps: [{ slug: 'nuova', name: 'Nuova' }] });
        await seconda;
        attese[0]({ apps: [{ slug: 'vecchia', name: 'Vecchia' }] });
        await prima;
        assert.deepEqual(fonte.jennyApps.map((a) => a.slug), ['nuova']);
        """
    )


def test_a_failed_refresh_keeps_the_list_that_was_fine() -> None:
    """Una rilettura fallita (un attimo di gateway occupato) non svuota un elenco
    che era buono, e non accende l'errore nel cassetto."""
    _run(
        """
        const fonte = new AppsSource();
        await fonte.loadJennyApps();
        api.getJennyApps = async () => { throw new Error('giù'); };
        frame({ event: 'apps_list_changed' });
        await giro();
        assert.deepEqual(fonte.jennyApps.map((a) => a.slug), ['orto']);
        assert.equal(fonte.jennyListFailed(), false);
        """
    )


def test_a_first_reading_that_fails_still_says_so() -> None:
    """Senza un elenco buono da tenere, il guasto resta un guasto."""
    _run(
        """
        const fonte = new AppsSource();
        api.getJennyApps = async () => { throw new Error('giù'); };
        await fonte.loadJennyApps();
        assert.deepEqual(fonte.jennyApps, []);
        assert.equal(fonte.jennyListFailed(), true);
        api.getJennyApps = async () => ({ apps: [{ slug: 'orto' }] });
        await fonte.loadJennyApps();
        assert.equal(fonte.jennyListFailed(), false, 'una lettura buona spegne il guasto');
        """
    )
