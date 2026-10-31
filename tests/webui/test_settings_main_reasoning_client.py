"""La select «Ragionamento di Jenny» si ferma quando quello di tutti è spento.

Con ``reasoning_effort`` vuoto («decide il provider») il ragionamento di Jenny
non si applica (``AgentLoop._turn_reasoning_effort``): la select lo dice
restando disattivata, invece di offrire una scelta che non conta.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, locale, member, requires_node, run_js

_SETTINGS = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
_I18N = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")


def _main_select(agent: dict) -> str:
    out = run_js(f"""
const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(locale("it"), ensure_ascii=False)} }},
{member(_I18N, "t")} }};
const escapeHtml = (s) => String(s);
class Fake {{
  _field() {{ return ''; }}
{member(_SETTINGS, "_select")}
{member(_SETTINGS, "_renderParameters")}
}}
const html = new Fake()._renderParameters({{ agent: {json.dumps(agent)} }});
const tag = html.match(/<select[^>]*data-key="main_reasoning_effort"[^>]*>/)[0];
console.log(JSON.stringify(tag));
""")
    return json.loads(out.strip().splitlines()[-1])


@requires_node
def test_the_select_is_off_when_everyones_reasoning_is_off() -> None:
    assert "disabled" in _main_select({"reasoning_effort": None, "main_reasoning_effort": "low"})


@requires_node
def test_the_select_works_when_everyones_reasoning_is_on() -> None:
    assert "disabled" not in _main_select(
        {"reasoning_effort": "medium", "main_reasoning_effort": "low"}
    )
