"""Un ``localStorage`` che solleva non porta giu' la pagina.

Nella WebView con i dati del
sito bloccati, in un'anteprima o con la quota piena ``localStorage`` solleva —
gia' leggendo la proprieta'. ``shared/state.js`` lo leggeva nudo **al
caricamento del modulo**, cioe' prima di tutto: l'errore si portava via l'intero
grafo degli import. Lo stesso in ``bootstrap.js`` (tema e lingua del primo
fotogramma), in ``shared/mascot.js``, ``shared/theme.js``, ``mobile-app.js`` e
nel wizard del primo avvio (oggi ``onboarding-wizard.js``).

Qui i moduli veri girano in node con uno storage che solleva a ogni accesso.
"""

from __future__ import annotations

import re

import pytest
from support.js_harness import ASSETS, requires_node, run_js

pytestmark = requires_node

_THROWING = """
import assert from 'node:assert/strict';
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  get() { throw new DOMException('denied', 'SecurityError'); },
});
"""


def test_the_state_module_loads_without_storage() -> None:
    run_js(
        _THROWING
        + f"const {{ AppState }} = await import('{(ASSETS / 'shared' / 'state.js').as_uri()}');\n"
        "assert.equal(AppState.theme, 'synthwave');\n"
    )


def test_the_mascot_preferences_fall_back_to_their_defaults() -> None:
    run_js(
        _THROWING
        + """
      globalThis.window = { dispatchEvent() {} };
      globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o?.detail; } };
      globalThis.document = { documentElement: { style: { setProperty() {} } } };
      """
        + f"const m = await import('{(ASSETS / 'shared' / 'mascot.js').as_uri()}');\n"
        """
      assert.equal(m.mascotSize(), 'sm');
      assert.equal(m.setMascotSize('lg'), 'lg');
    """
    )


def test_the_shared_helpers_read_null_and_write_nothing() -> None:
    run_js(
        _THROWING
        + f"const u = await import('{(ASSETS / 'shared' / 'utils.js').as_uri()}');\n"
        """
      assert.equal(u.readStorage('x'), null);
      assert.equal(u.writeStorage('x', '1'), false);
      u.removeStorage('x');
    """
    )


def test_the_first_paint_still_gets_its_theme() -> None:
    boot = (ASSETS / "bootstrap.js").read_text(encoding="utf-8")
    run_js(
        _THROWING
        + """
      const attrs = {};
      globalThis.document = { documentElement: {
        setAttribute: (k, v) => { attrs[k] = v; }, style: { setProperty() {} }, lang: '',
      } };
      """
        + boot
        + "\nassert.equal(attrs['data-theme'], 'synthwave');\n"
    )


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", " ", src)


@pytest.mark.parametrize(
    "path", ["mobile-app.js", "onboarding-wizard.js", "shared/theme.js", "shared/state.js"]
)
def test_no_bare_storage_access_is_left(path: str) -> None:
    """Ogni accesso passa da una lettura protetta, o sta dentro un ``try``."""
    code = _code((ASSETS / path).read_text(encoding="utf-8"))
    for m in re.finditer(r"localStorage\.(getItem|setItem|removeItem)\(", code):
        before = code[: m.start()]
        assert before.rfind("try {") > before.rfind("}\n"), (
            f"{path}: accesso nudo allo storage vicino a {code[m.start() - 60 : m.end() + 20]!r}"
        )


def test_the_launcher_opens_without_storage() -> None:
    run_js(
        _THROWING
        + """
      globalThis.window = {};
      Object.defineProperty(window, 'localStorage', { get() { throw new Error('denied'); } });
      """
        + f"const {{ usageStore }} = await import('{(ASSETS / 'shared' / 'launcher-usage-store.js').as_uri()}');\n"
        "assert.equal(usageStore(), null);\n"
    )
