"""Il guscio della casa, pezzi piccoli.

**Un pacchetto installato o rimosso arriva alla pagina App.** Il guscio nativo
chiama `window.mobileApp.onPackageChanged(kind, pkg)` a ogni broadcast di
sistema. In casa il metodo era vuoto (il cassetto era «una tavola del giro
dopo»), e quando il cassetto e' diventato la pagina App nessuno l'ha collegato:
con Jenny come launcher, un'app appena presa dal Play Store non si trovava fino
al riavvio.

**La mappa nasce una volta sola**, anche se la sua linguetta si tocca due volte
prima che il modulo (e D3) sia arrivato. E poi: la domanda pubblica «c'e'
un'app aperta?», e il primo disegno della fila dopo le traduzioni.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "home-app.js"

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
    assert "import('./home-map.js')" in metodo
    metodo = metodo.replace("import('./home-map.js')", "caricaMappa()")
    harness = (
        "import assert from 'node:assert/strict';\n"
        "let nate = 0, disegni = 0, lascia = null, rotto = false;\n"
        "class HomeMap {\n"
        "  constructor() { nate += 1; }\n"
        "  async draw() { disegni += 1; }\n"
        "}\n"
        "function caricaMappa() {\n"
        "  return new Promise((r, no) => { lascia = () => (rotto ? no(new Error('rete')) : r({ HomeMap })); });\n"
        "}\n"
        "class App {\n  constructor() { this.map = null; }\n  openPage() {}\n  "
        + metodo
        + "\n}\n"
    )
    run_js(harness + script)


def test_two_taps_before_the_module_arrives_make_one_map() -> None:
    """Due `HomeMap` sullo stesso SVG sono due simulazioni che si contendono
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
      assert.ok(app.map instanceof HomeMap);
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


def test_the_open_app_question_has_a_public_answer() -> None:
    """La casa chiede ad `AppsActions` se c'e' una mini-app aperta: prima lo
    leggeva dal suo campo privato `_openApp`."""
    src = (APP_JS.parent / "shared" / "apps-actions.js").read_text(encoding="utf-8")
    run_js(
        "import assert from 'node:assert/strict';\n"
        "class Azioni {\n  constructor() { this._openApp = null; }\n  "
        + member(src, "isAppOpen", prefixes=())
        + "\n}\n"
        "const a = new Azioni();\n"
        "assert.equal(a.isAppOpen(), false);\n"
        "a._openApp = { slug: 'orto' };\n"
        "assert.equal(a.isAppOpen(), true);\n"
    )
    assert "_openApp" not in APP_JS.read_text(encoding="utf-8"), (
        "la casa legge di nuovo il campo privato delle azioni"
    )


def test_the_row_is_first_drawn_once_the_words_have_arrived() -> None:
    """Il costruttore disegnava la fila prima di `i18n.load`: i nomi delle
    pagine fisse uscivano come chiavi grezze («casa.fila.app») per il tempo
    del bootstrap. Il primo disegno spetta a `_applyTranslations`, che `init`
    chiama dopo aver caricato le parole.

    Sul sorgente, perche' la domanda e' *quando* si disegna nel costruttore e
    in `init`, che nessun banco puo' eseguire interi."""
    src = APP_JS.read_text(encoding="utf-8")
    costruttore = member(src, "constructor", prefixes=())
    assert not re.search(r"this\.strip\??\.disegna\(\)", costruttore), (
        "la fila si disegna prima che le traduzioni siano arrivate"
    )
    init = member(src, "init", prefixes=("async ",))
    assert init.index("await i18n.load(") < init.index("this._applyTranslations()")
    assert "this.strip?.disegna();" in member(src, "_applyTranslations")
