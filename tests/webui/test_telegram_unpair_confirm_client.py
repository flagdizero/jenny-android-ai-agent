"""«Scollega» di Telegram chiede conferma prima di scollegare.

Era l'unica azione distruttiva dell'officina senza conferma (audit sul Titan 2,
26/09/2026). Scollegare non butta il token, ma da quel momento Jenny non risponde
su Telegram e non ci manda avvisi, finche' qualcuno non manda al bot il codice
nuovo: nessun segnale lo dice. Un tocco sbagliato in Mani bastava.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, locale, member, requires_node, run_js

_PAIRING = (ASSETS / "shared" / "telegram-pairing.js").read_text(encoding="utf-8")
_I18N = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")


def _run(answer: bool) -> dict:
    out = run_js(f"""
const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(locale("it"), ensure_ascii=False)} }},
{member(_I18N, "t")} }};
const calls = {{ unpair: 0, render: 0, asked: [] }};
const api = {{ unpairTelegram: async () => {{ calls.unpair += 1; return {{ paired: false }}; }} }};
const showToast = () => {{}};
const confirmDialog = async (message, okText) => {{ calls.asked.push([message, okText]); return {str(answer).lower()}; }};

class Widget {{
  constructor() {{
    this._busy = false;
    this.status = {{ paired: true, paired_username: 'mario', bot_username: 'jenny_bot' }};
  }}
  render() {{ calls.render += 1; }}
{member(_PAIRING, "_unpair")}
}}

const w = new Widget();
await w._unpair();
calls.busyAfter = w._busy;
console.log(JSON.stringify(calls));
""")
    return json.loads(out.strip().splitlines()[-1])


@requires_node
def test_a_refused_confirm_unpairs_nothing() -> None:
    calls = _run(False)

    assert len(calls["asked"]) == 1
    assert calls["unpair"] == 0
    assert calls["render"] == 0
    assert calls["busyAfter"] is False


@requires_node
def test_a_given_confirm_unpairs_once() -> None:
    calls = _run(True)

    assert calls["unpair"] == 1
    assert calls["render"] == 1
    message, ok_text = calls["asked"][0]
    assert "@mario" in message
    assert ok_text == locale("it")["settings"]["telegram"]["unpair"]


def test_the_confirm_speaks_both_languages() -> None:
    for lang in ("it", "en"):
        text = locale(lang)["settings"]["telegram"]["unpairConfirm"]
        assert "{who}" in text, lang
