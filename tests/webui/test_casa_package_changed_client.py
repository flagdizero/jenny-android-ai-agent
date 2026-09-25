"""Un pacchetto Android installato o rimosso arriva alla pagina App.

Il guscio nativo chiama `window.mobileApp.onPackageChanged(kind, pkg)` a ogni
broadcast di sistema. In casa il metodo era vuoto (il cassetto era «una tavola
del giro dopo»), e quando il cassetto e' diventato la pagina App nessuno l'ha
collegato: con Jenny come launcher, un'app appena presa dal Play Store non si
trovava fino al riavvio.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "casa-app.js"

pytestmark = requires_node


def _run_pacchetti(script: str) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    harness = (
        "import assert from 'node:assert/strict';\n"
        "class App {\n  constructor() { this._apps = null; }\n  "
        + member(src, "onPackageChanged")
        + "\n}\n"
    )
    run_js(harness + script)


def test_an_installed_app_reaches_the_apps_page() -> None:
    """Il metodo era vuoto: un'app presa dal Play Store non compariva nella
    pagina App fino al riavvio — con Jenny come launcher, un'app che non si
    trova."""
    _run_pacchetti("""
      const app = new App();
      const visti = [];
      app._apps = { onPackageChanged: (k, p) => visti.push([k, p]) };
      app.onPackageChanged('added', 'org.example.notes');
      app.onPackageChanged('removed', 'org.example.old');
      assert.deepEqual(visti, [['added', 'org.example.notes'], ['removed', 'org.example.old']]);
    """)


def test_a_package_change_before_the_apps_page_ever_opened_builds_nothing() -> None:
    """L'elenco non e' ancora stato letto, e quando lo sara' sara' fresco: il
    guscio nativo chiama comunque, e non deve rompersi ne' costruire."""
    _run_pacchetti("""
      const app = new App();
      app.onPackageChanged('added', 'org.example.notes');
      assert.equal(app._apps, null);
    """)
