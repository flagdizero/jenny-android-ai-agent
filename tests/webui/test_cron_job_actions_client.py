"""Il dettaglio di un job in officina: i gesti che offre e come li compie.

Fino al 26/09/2026 il dettaglio era una scheda da leggere: per fermare un
promemoria bisognava chiederlo a Jenny. Qui si prova la colla fra la scheda e
la route — quali bottoni per quale stato, la conferma prima di eliminare, e che
cosa dice un rifiuto del server. I due metodi si ritagliano dal sorgente e
girano in node con i collaboratori finti, come gli altri banchi dell'officina.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, locale, member, requires_node, run_js

_SETTINGS = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
_I18N = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")

pytestmark = requires_node


def _run(scenario: str) -> dict:
    out = run_js(f"""
import assert from 'node:assert/strict';

const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(locale("it"), ensure_ascii=False)} }},
{member(_I18N, "t")} }};
const escapeHtml = (s) => String(s);
const log = [];
let dialogChoice = null;
let confirmAnswer = true;
let apiFails = null;
const detailDialog = async (opts) => {{ log.push(['dialog', opts.actions]); return dialogChoice; }};
const confirmDialog = async (msg, ok) => {{ log.push(['confirm', msg, ok]); return confirmAnswer; }};
const showToast = (msg, tone) => log.push(['toast', msg, tone]);
const api = {{
  async cronJobAction(id, action) {{
    log.push(['api', id, action]);
    if (apiFails) {{ const e = new Error('no'); e.status = apiFails; throw e; }}
    return {{ result: action }};
  }},
}};

class Fake {{
  _renderCronHeartbeat() {{ return ''; }}
  _cronStatusText(s) {{ return s; }}
  async _loadCron() {{ log.push(['reload']); }}
{member(_SETTINGS, "_showCronJobDialog", prefixes=("async ",))}
{member(_SETTINGS, "_runCronAction", prefixes=("async ",))}
}}

const row = (over = {{}}) => ({{
  id: 'ab12cd34', name: 'Acqua al basilico', purpose: null, message: 'annaffia',
  heartbeat: null, runs: [], actions: ['pause', 'remove'], pausedAtMs: null, ...over,
}});
const s = new Fake();
{scenario}
console.log(JSON.stringify(log));
""")
    return json.loads(out.strip().splitlines()[-1])


def test_an_active_job_offers_pause_and_delete_with_the_right_words() -> None:
    log = _run("await s._showCronJobDialog(row());")
    assert log == [["dialog", [
        {"id": "pause", "label": "Metti in pausa"},
        {"id": "remove", "label": "Elimina"},
    ]]]


def test_a_paused_job_offers_resume_first() -> None:
    log = _run("await s._showCronJobDialog(row({ actions: ['resume', 'remove'], pausedAtMs: 1 }));")
    assert log[0][1][0] == {"id": "resume", "label": "Riprendi", "variant": "primary"}


def test_a_system_job_offers_nothing() -> None:
    assert _run("await s._showCronJobDialog(row({ actions: [] }));") == [["dialog", []]]


def test_pause_goes_straight_to_the_server_and_reloads() -> None:
    log = _run("dialogChoice = 'pause'; await s._showCronJobDialog(row());")
    assert log[1:] == [
        ["api", "ab12cd34", "pause"],
        ["toast", "Messo in pausa.", "success"],
        ["reload"],
    ]


def test_delete_asks_first_and_a_no_touches_nothing() -> None:
    log = _run("dialogChoice = 'remove'; confirmAnswer = false; await s._showCronJobDialog(row());")
    assert log[1][0] == "confirm"
    assert "Acqua al basilico" in log[1][1]
    assert log[1][2] == "Elimina"
    assert len(log) == 2, "dopo un no non deve partire niente"


def test_delete_after_a_yes_goes_through() -> None:
    log = _run("dialogChoice = 'remove'; await s._showCronJobDialog(row());")
    assert [e[0] for e in log] == ["dialog", "confirm", "api", "toast", "reload"]
    assert log[2] == ["api", "ab12cd34", "remove"]


def test_an_expired_one_shot_says_why_it_cannot_resume() -> None:
    log = _run("apiFails = 409; await s._runCronAction(row(), 'resume');")
    assert log[1] == ["toast", "Era un promemoria per una volta sola e la sua ora è passata: "
                      "si può solo eliminare.", "error"]
    assert log[-1] == ["reload"]


def test_an_unknown_choice_does_nothing() -> None:
    assert _run("await s._runCronAction(row(), 'run');") == []
