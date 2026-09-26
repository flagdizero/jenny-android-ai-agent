"""I contatori di Dream in Memoria dicono «1 run», non «1 runs».

Sul Titan 2 il 26/09/2026 la sezione Dream scriveva «1 runs since the last
review pass». Il singolare ha la sua chiave (``…One``), come ``countOne``.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, locale, member, requires_node, run_js

_SETTINGS = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
_I18N = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")


def _render(lang: str, state: dict) -> list[str]:
    out = run_js(f"""
const i18n = {{ locale: '{lang}', translations: {{ '{lang}': {json.dumps(locale(lang), ensure_ascii=False)} }},
{member(_I18N, "t")} }};
const escapeHtml = (s) => s;
class Fake {{
  _hint(html) {{ return html; }}
{member(_SETTINGS, "_renderReviewState")}
}}
console.log(JSON.stringify(new Fake()._renderReviewState({json.dumps(state)}).split('<br>')));
""")
    return json.loads(out.strip().splitlines()[-1])


@requires_node
def test_one_run_is_singular_and_more_are_plural() -> None:
    assert _render("en", {"runs_since_review": 1, "stuck_runs": 1, "nothing_new_runs": 1}) == [
        "1 run since the last review pass.",
        "1 run with writes attempted and none landing.",
        "1 run with nothing to consolidate.",
    ]
    assert _render("en", {"runs_since_review": 3, "stuck_runs": 0, "nothing_new_runs": 2}) == [
        "3 runs since the last review pass.",
        "2 runs in a row with nothing to consolidate.",
    ]


@requires_node
def test_italian_has_the_same_keys() -> None:
    lines = _render("it", {"runs_since_review": 1, "stuck_runs": 0, "nothing_new_runs": 0})
    assert lines == ["1 run dall'ultima passata di review."]
