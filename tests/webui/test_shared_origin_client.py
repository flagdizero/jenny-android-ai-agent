"""`shared/origin.js`: da dove e' entrato un messaggio, detto uguale nei due gusci.

L'officina scriveva il nome del canale con l'iniziale maiuscola («Floating»,
«Notification»): il nome nel codice, in inglese anche col telefono in italiano.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, function, locale, member, requires_node, run_js

ORIGIN_JS = (ASSETS / "shared" / "origin.js").read_text(encoding="utf-8")
I18N_JS = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")


def test_neither_shell_spells_it_by_itself() -> None:
    for name in ("home-chat.js", "mobile-chat.js"):
        src = (ASSETS / name).read_text(encoding="utf-8")
        assert "originBadge(" in src, name
        assert "origin.charAt(0).toUpperCase()" not in src, name


@requires_node
def test_the_words() -> None:
    it = locale("it")
    o = it["home"]["origin"]
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(it)} }}, {member(I18N_JS, 't')} }};\n"
        "const escapeHtml = (s) => String(s).replace(/</g, '&lt;');\n"
        + ORIGIN_JS[ORIGIN_JS.index("const ORIGINS"):ORIGIN_JS.index("/** Il markup")]
        + function(ORIGIN_JS, "originBadgeHtml")
        + f"""
assert.equal(originBadgeHtml('websocket'), '');
assert.equal(originBadgeHtml(null), '');
assert.ok(originBadgeHtml('floating').endsWith({json.dumps(o["floating"])}));
assert.ok(originBadgeHtml('telegram').includes('ti-brand-telegram'));
assert.ok(originBadgeHtml('remote', 'mac').endsWith({json.dumps(o["remoteNamed"].replace("{device}", "mac"))}));
assert.ok(originBadgeHtml('<x>').endsWith('&lt;x>'), 'un canale sconosciuto si dice col suo nome, escapato');
"""
    )
