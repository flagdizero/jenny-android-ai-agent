"""Il primo avvio dice nella sua lingua perché la chiave non è stata salvata.

Il server prova la chiave con una domanda vera prima di salvarla
(``settings_api.save_onboarding``) e, se il provider non risponde, torna
``{ok: false, probe: <tipo>}``. Qui, in jsdom, i metodi veri del wizard:
l'avviso tradotto, i bottoni giusti per ogni tipo («Salva lo stesso» solo dove
la chiave può essere giusta), e la lista dei modelli che dice lo stato con le
frasi della casa invece del ``message`` inglese del server.
"""

from __future__ import annotations

import json
import re

from support.home_dom import requires_jsdom
from support.js_harness import ASSETS, member, run_js

pytestmark = requires_jsdom

WIZARD_JS = ASSETS / "onboarding-wizard.js"
CATALOG_JS = ASSETS / "shared" / "model-catalog.js"
I18N = ASSETS / "i18n"


def _const(source: str, name: str) -> str:
    m = re.search(rf"^(?:export )?(const {name} = .*?;)$", source, re.S | re.M)
    assert m, f"{name} non trovata"
    return m.group(1)


def _bench(scenario: str) -> str:
    source = WIZARD_JS.read_text(encoding="utf-8")
    catalog = CATALOG_JS.read_text(encoding="utf-8")
    methods = "\n".join(
        member(source, name)
        for name in ("_showProbeFailure", "_clearProbeFailure", "_save", "_modelsNote")
    ).replace("await import('./shared/rpc-client.js')", "await importRpc()")
    return run_js(
        "import assert from 'node:assert/strict';\n"
        "import { createRequire } from 'node:module';\n"
        "const require = createRequire(import.meta.url);\n"
        "const { JSDOM } = require('jsdom');\n"
        "const dom = new JSDOM('<div id=\"c\"></div>');\n"
        "globalThis.document = dom.window.document;\n"
        "const i18n = { t: (k) => k, locale: 'it' };\n"
        "const escapeHtml = (s) => String(s);\n"
        "const DEFAULT_BOT_NAME = 'Jenny';\n"
        "const toasts = [];\n"
        "const showToast = (m) => toasts.push(m);\n"
        "const sent = [];\n"
        "let answer = async () => ({ ok: true });\n"
        "const importRpc = async () => ({ rpc: { saveOnboarding: (p) => { sent.push(p); return answer(p); } } });\n"
        f"{_const(catalog, 'CATALOG_NOTE_KEYS')}\n"
        f"{_const(source, 'PROBE_SAVE_ANYWAY')}\n"
        f"{_const(source, 'PROBE_KINDS')}\n"
        "class W {\n"
        "  constructor() {\n"
        "    this.contentEl = document.getElementById('c');\n"
        "    this.contentEl.innerHTML = '<input id=\"bot-name\" value=\"Jenny\">'\n"
        "      + '<div id=\"probe-notice\" hidden></div>';\n"
        "    Object.assign(this, { step: 2, model: 'gpt-x', saving: false, _probeFailure: null,\n"
        "      providerName: 'p', format: 'openai_compat', apiKey: 'k', apiBase: '', botName: '' });\n"
        "    this.log = [];\n"
        "  }\n"
        "  _showLoadingOverlay(skip) { this.log.push(['overlay', skip]); }\n"
        "  _hideLoadingOverlay() {}\n"
        "  _goBackToStep1() { this.log.push(['back']); this.step = 1; }\n"
        "  _showCustomModelField() { this.log.push(['custom']); }\n"
        "  render() { this.log.push(['render', this.step]); }\n"
        f"{methods}\n"
        "}\n"
        "const tick = () => new Promise((r) => setTimeout(r, 0));\n"
        "const box = (w) => w.contentEl.querySelector('#probe-notice');\n"
        "const actions = (w) => [...box(w).querySelectorAll('[data-probe-action]')]\n"
        "  .map((b) => b.dataset.probeAction);\n"
        f"{scenario}\n"
        "console.log('ok');\n"
    )


def test_a_rejected_key_says_so_and_offers_no_save_anyway() -> None:
    out = _bench("""
answer = async () => ({ ok: false, probe: 'auth' });
const w = new W();
await w._save();
assert.equal(w.step, 2, 'una chiave rifiutata non porta allo step dopo');
assert.equal(w.saving, false);
assert.equal(box(w).hidden, false);
assert.match(box(w).textContent, /onboarding\\.probe\\.auth/);
assert.deepEqual(actions(w), ['key', 'retry']);
assert.equal(sent[0].skip_probe, false);
assert.deepEqual(w.log[0], ['overlay', false]);

// «Cambia chiave» torna al campo della chiave.
box(w).querySelector('[data-probe-action="key"]').click();
assert.deepEqual(w.log.at(-1), ['back']);
""")
    assert out.strip() == "ok"


def test_a_network_failure_can_be_saved_anyway() -> None:
    out = _bench("""
for (const kind of ['connection', 'timeout', 'server', 'rate_limit', 'generic']) {
  answer = async () => ({ ok: false, probe: kind });
  const w = new W();
  await w._save();
  assert.deepEqual(actions(w), ['key', 'retry', 'anyway'], kind);
  assert.match(box(w).textContent, /onboarding\\.probe\\.mayBeRight/, kind);
}

answer = async () => ({ ok: false, probe: 'timeout' });
const w = new W();
await w._save();
sent.length = 0;
answer = async () => ({ ok: true, status: 'ok' });
box(w).querySelector('[data-probe-action="anyway"]').click();
await tick(); await tick();
assert.equal(sent.length, 1);
assert.equal(sent[0].skip_probe, true, '«Salva lo stesso» salta la prova');
assert.equal(w.step, 3);
assert.equal(box(w).hidden, true, "l'avviso resta dopo un salvataggio riuscito");
""")
    assert out.strip() == "ok"


def test_retry_asks_again_with_the_probe() -> None:
    out = _bench("""
answer = async () => ({ ok: false, probe: 'credit' });
const w = new W();
await w._save();
assert.deepEqual(actions(w), ['key', 'retry']);
answer = async () => ({ ok: true });
box(w).querySelector('[data-probe-action="retry"]').click();
await tick(); await tick();
assert.equal(sent.length, 2);
assert.equal(sent[1].skip_probe, false);
assert.equal(w.step, 3);
""")
    assert out.strip() == "ok"


def test_a_missing_model_sends_to_the_model_not_the_key() -> None:
    out = _bench("""
answer = async () => ({ ok: false, probe: 'model' });
const w = new W();
await w._save();
assert.deepEqual(actions(w), ['model', 'retry']);
box(w).querySelector('[data-probe-action="model"]').click();
assert.deepEqual(w.log.at(-1), ['custom']);
assert.equal(box(w).hidden, true);
assert.equal(w._probeFailure, null);
""")
    assert out.strip() == "ok"


def test_an_unknown_kind_is_said_as_generic() -> None:
    out = _bench("""
answer = async () => ({ ok: false, probe: 'something-new' });
const w = new W();
await w._save();
assert.match(box(w).textContent, /onboarding\\.probe\\.generic/);
""")
    assert out.strip() == "ok"


def test_a_server_refusal_is_not_shown_in_english() -> None:
    out = _bench("""
answer = async () => {
  const e = new Error('model is required');
  e.code = 'bad_request';
  throw e;
};
const w = new W();
await w._save();
assert.deepEqual(toasts, ['onboarding.failedToSave']);
answer = async () => { throw new Error('common.requestTimeout'); };
await w._save();
assert.deepEqual(toasts.at(-1), 'common.requestTimeout', 'un errore del client è già tradotto');
assert.equal(w.saving, false);
""")
    assert out.strip() == "ok"


def test_the_model_list_says_the_status_in_the_users_language() -> None:
    """La lista vuota diceva il ``message`` inglese del server («The provider
    rejected the configured credential.»): ora lo stato, con le frasi di casa e
    officina, e ``rejected`` distinto da ``not_configured``."""
    out = _bench("""
const w = new W();
const said = (status) => { w._modelsStatus = status; return w._modelsNote(); };
assert.equal(said('rejected'), 'common.modelsKeyRejected');
assert.equal(said('not_configured'), 'common.modelsNeedKey');
assert.equal(said('missing_api_base'), 'onboarding.modelsNeedBase');
assert.equal(said('error'), 'onboarding.couldNotFetch');
assert.equal(said(''), 'onboarding.noModels');
""")
    assert out.strip() == "ok"
    source = WIZARD_JS.read_text(encoding="utf-8")
    assert "resp.message" not in source, "il wizard mostra ancora il messaggio inglese del server"
    assert "e.message" not in member(source, "_loadModels")


def test_every_probe_sentence_exists_in_both_languages() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")
    kinds = set(re.findall(r"'([a-z_]+)'", _const(source, "PROBE_SAVE_ANYWAY")))
    kinds |= set(re.findall(r"'([a-z_]+)'", _const(source, "PROBE_KINDS")))
    # I tipi del server: quelli di ``classify_user_error`` più ``model`` e ``config``.
    assert kinds == {
        "auth", "credit", "rate_limit", "server", "timeout", "connection", "generic",
        "model", "config",
    }
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        probe = data["onboarding"]["probe"]
        for key in kinds | {"trying", "mayBeRight", "changeKey", "changeModel", "retry",
                            "saveAnyway"}:
            assert probe.get(key), f"{locale}: onboarding.probe.{key}"
        assert data["common"]["modelsKeyRejected"]
        assert data["onboarding"]["modelsNeedBase"]
