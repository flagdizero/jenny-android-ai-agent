"""`shared/when.js`: «oggi alle», «ieri alle», detto una volta per tutti.

Erano quattro coppie di chiavi uguali e due modi di contare i giorni (a blocchi
di 24 ore dalla mezzanotte, o sul calendario): il cassetto, gli snapshot, i
buchi di batteria e l'ultimo controllo degli aggiornamenti. Qui la regola che
resta, il calendario.
"""

from __future__ import annotations

import json

from support.js_harness import ASSETS, function, locale, member, requires_node, run_js

WHEN_JS = (ASSETS / "shared" / "when.js").read_text(encoding="utf-8")
I18N_JS = (ASSETS / "shared" / "i18n.js").read_text(encoding="utf-8")

pytestmark = requires_node


def test_today_yesterday_and_farther() -> None:
    it = locale("it")
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(it)} }}, {member(I18N_JS, 't')} }};\n"
        + "\n".join(function(WHEN_JS, n) for n in ("dayOffset", "dayAndTime"))
        + """
const at = (y, m, d, h, min) => new Date(y, m, d, h, min).getTime();
const now = at(2026, 10, 2, 0, 10);
assert.equal(dayOffset(at(2026, 10, 1, 23, 50), now), 1, 'venti minuti prima di mezzanotte e\\u2019 ieri');
assert.equal(dayOffset(now, now), 0);
assert.equal(dayOffset(at(2026, 10, 3, 9, 0), now), -1);
assert.equal(dayOffset(at(2026, 9, 24, 12, 0), now), 9, 'attraverso il cambio d\\u2019ora');
assert.match(dayAndTime(at(2026, 10, 2, 0, 5), { now }), /^oggi alle 00:05$/);
assert.match(dayAndTime(at(2026, 10, 1, 23, 50), { now }), /^ieri alle 23:50$/);
assert.equal(dayAndTime(at(2026, 9, 31, 12, 0), { now }), '');
assert.match(dayAndTime(at(2026, 10, 3, 9, 0), { now }), /^oggi alle /, 'il futuro conta come oggi');
assert.match(dayAndTime(at(2026, 10, 2, 9, 5), { now: at(2026, 10, 2, 10, 0), hour: 'numeric' }), /^oggi alle 9:05$/);
"""
    )


def test_ago_says_one_hour_and_counts_days_on_the_calendar() -> None:
    """Da 60 a 119 minuti diceva «1 ore fa», e i giorni erano ``floor(ore/24)``:
    47 ore fa, che sul calendario e' l'altro ieri, diceva «ieri». Era in
    ``conversation-list.js``, che non importa niente; sta qui, accanto a
    ``dayOffset``."""
    it = locale("it")
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(it)} }}, {member(I18N_JS, 't')} }};\n"
        + "\n".join(function(WHEN_JS, n) for n in ("dayOffset", "ago"))
        + """
const now = new Date(2026, 10, 2, 9, 0).getTime();
const sec = (d) => d.getTime() / 1000;
const before = (minutes) => (now / 1000) - minutes * 60;
assert.equal(ago(0, now), '');
assert.equal(ago(before(1), now), 'adesso');
assert.equal(ago(before(30), now), '30 min fa');
assert.equal(ago(before(90), now), '1 ora fa');
assert.equal(ago(before(150), now), '2 ore fa');
assert.equal(ago(sec(new Date(2026, 10, 1, 8, 0)), now), 'ieri');
assert.equal(ago(sec(new Date(2026, 9, 31, 10, 0)), now), '2 giorni fa', '47 ore fa e\\u2019 l\\u2019altro ieri');
"""
    )


def test_short_date_and_the_near_day() -> None:
    """«12 ott» era scritto sette volte (le note, le foto, il cestino delle app,
    il cassetto, i blocchi della bacheca, la copia su Drive), e «domenica alle
    3:12» due (gli snapshot e la copia su Drive), con due chiavi uguali."""
    it = locale("it")
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const i18n = {{ locale: 'it', translations: {{ it: {json.dumps(it)} }}, {member(I18N_JS, 't')} }};\n"
        + "\n".join(function(WHEN_JS, n) for n in ("dayOffset", "dayAndTime", "shortDate", "nearDay"))
        + """
const at = (y, m, d, h, min) => new Date(y, m, d, h, min);
assert.equal(shortDate('2026-10-12T09:00:00'), '12 ott');
assert.equal(shortDate(at(2026, 9, 14, 9, 0), { month: 'long' }), '14 ottobre');
assert.equal(shortDate(at(2026, 9, 14, 9, 0).getTime()), '14 ott');
assert.equal(shortDate(''), '');
assert.equal(shortDate('non una data'), '');
const now = at(2026, 10, 2, 10, 0).getTime();
assert.match(nearDay(at(2026, 10, 2, 3, 12), { now }), /^oggi alle 3:12$/);
assert.match(nearDay(at(2026, 10, 1, 3, 12), { now }), /^ieri alle 3:12$/);
assert.match(nearDay(at(2026, 9, 29, 3, 12), { now }), /^giovedì alle 3:12$/);
assert.equal(nearDay(at(2026, 9, 20, 3, 12), { now }), '');
"""
    )
