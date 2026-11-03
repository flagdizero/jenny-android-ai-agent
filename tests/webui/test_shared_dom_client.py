"""`shared/dom.js`: i mattoni dei nodi della casa, scritti una volta.

Erano copiati in testa a dieci moduli, e le copie divergevano su `null` come
testo. Qui la regola, e il controllo che nessuno se li riscriva in casa.
`home-plan.js` resta fuori finché l'altra sessione lo ridisegna, e
`shared/place-card.js` costruisce in un documento che riceve (`doc`), non in
quello della pagina.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, function, requires_node, run_js

DOM_JS = (ASSETS / "shared" / "dom.js").read_text(encoding="utf-8")
EXEMPT = {"home-plan.js", "shared/place-card.js", "shared/dom.js"}


def _modules():
    for pattern in ("home-*.js", "mobile-*.js", "shared/*.js"):
        yield from ASSETS.glob(pattern)


def test_no_module_keeps_its_own_copy() -> None:
    """Della casa, dell'officina e dei moduli condivisi: anche un metodo
    (``_icon(name)``) che rifà ``icon()`` e' una copia."""
    copies = [
        str(f.relative_to(ASSETS)) for f in _modules()
        if str(f.relative_to(ASSETS)) not in EXEMPT
        and re.search(r"(?m)^(?:function (?:el|button|icon)\(|const (?:el|icon|button) = |  _icon\()",
                      f.read_text(encoding="utf-8"))
    ]
    assert copies == []


def test_the_plan_lock_toast_is_written_once() -> None:
    hand = [
        f.name for f in ASSETS.glob("home-*.js")
        if "err?.code === 'plan_running' ? 'home.plan.lock'" in f.read_text(encoding="utf-8")
        or "err?.code === 'plan_running' ? i18n.t('home.plan.lock')" in f.read_text(encoding="utf-8")
    ]
    assert hand == []


@requires_node
def test_helpers() -> None:
    run_js(
        "import assert from 'node:assert/strict';\n"
        "const document = { createElement: (tag) => ({ tag, attrs: {}, listeners: {},"
        " setAttribute(k, v) { this.attrs[k] = v; },"
        " addEventListener(t, fn) { this.listeners[t] = fn; } }) };\n"
        "const toasts = [];\n"
        "const showToast = (m, kind) => toasts.push([m, kind]);\n"
        "const i18n = { t: (k) => `t:${k}` };\n"
        + "\n".join(function(DOM_JS, n) for n in ("el", "button", "icon", "iconHtml", "writeFailedToast"))
        + """
assert.equal(el('p', 'a', null).textContent, undefined, 'null non e\\u2019 un testo');
assert.equal(el('p', '', 'x').className, undefined);
assert.equal(el('p', 'a', 0).textContent, 0);
const b = button('b', 'Ok');
assert.equal(b.type, 'button');
assert.deepEqual(Object.keys(b.listeners), [], 'senza onClick, nessun listener');
const i = icon('ti-x', 'big');
assert.equal(i.className, 'ti ti-x big');
assert.equal(i.attrs['aria-hidden'], 'true');
assert.equal(iconHtml('check'), '<i class="ti ti-check" aria-hidden="true"></i>');
writeFailedToast({ code: 'plan_running' }, 'board.failed');
writeFailedToast({ code: 'conflict' }, 'board.failed');
writeFailedToast(null, 'board.failed');
assert.deepEqual(toasts, [['t:home.plan.lock', 'error'], ['t:board.failed', 'error'],
  ['t:board.failed', 'error']]);
"""
    )
