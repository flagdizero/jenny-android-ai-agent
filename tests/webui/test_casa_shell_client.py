"""Il guscio della casa, pezzi piccoli: i pacchetti Android e la mappa.

**Un pacchetto installato o rimosso arriva alla pagina App.** Il guscio nativo chiama `window.mobileApp.onPackageChanged(kind, pkg)` a ogni
broadcast di sistema. In casa il metodo era vuoto (il cassetto era «una tavola
del giro dopo»), e quando il cassetto e' diventato la pagina App nessuno l'ha
collegato: con Jenny come launcher, un'app appena presa dal Play Store non si
trovava fino al riavvio.

**La mappa nasce una volta sola**, anche se la sua linguetta si tocca due volte
prima che il modulo (e D3) sia arrivato.
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


# ── La mappa, nata una volta sola ────────────────────────────────────────────


def _run_mappa(script: str) -> None:
    """`_drawMap` col suo `import()` sostituito da un caricatore finto che
    risponde quando il caso lo lascia: l'import vero non si puo' fare da qui."""
    src = APP_JS.read_text(encoding="utf-8")
    metodo = member(src, "_drawMap", prefixes=("async ",))
    assert "import('./casa-map.js')" in metodo
    metodo = metodo.replace("import('./casa-map.js')", "caricaMappa()")
    harness = (
        "import assert from 'node:assert/strict';\n"
        "let nate = 0, disegni = 0, lascia = null, rotto = false;\n"
        "class CasaMap {\n"
        "  constructor() { nate += 1; }\n"
        "  async draw() { disegni += 1; }\n"
        "}\n"
        "function caricaMappa() {\n"
        "  return new Promise((r, no) => { lascia = () => (rotto ? no(new Error('rete')) : r({ CasaMap })); });\n"
        "}\n"
        "class App {\n  constructor() { this.map = null; }\n  openPage() {}\n  "
        + metodo
        + "\n}\n"
    )
    run_js(harness + script)


def test_two_taps_before_the_module_arrives_make_one_map() -> None:
    """Due `CasaMap` sullo stesso SVG sono due simulazioni che si contendono
    i nodi: il secondo tocco arrivato prima del modulo ne faceva nascere
    un'altra."""
    _run_mappa("""
      const app = new App();
      const a = app._drawMap({}, 'orto');
      const b = app._drawMap({}, 'orto');
      lascia();
      await Promise.all([a, b]);
      assert.equal(nate, 1, 'due mappe per due tocchi');
      assert.equal(disegni, 2);
      assert.ok(app.map instanceof CasaMap);
    """)


def test_a_module_that_failed_to_load_is_tried_again() -> None:
    _run_mappa("""
      const app = new App();
      rotto = true;
      const a = app._drawMap({}, 'orto');
      lascia();
      await assert.rejects(a);
      rotto = false;
      const b = app._drawMap({}, 'orto');
      lascia();
      await b;
      assert.equal(nate, 1, 'dopo un import fallito la mappa non nasce piu\\u2019');
    """)
