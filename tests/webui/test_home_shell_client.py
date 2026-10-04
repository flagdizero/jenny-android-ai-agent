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


def _run_packages(script: str) -> None:
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
    _run_packages("""
      const app = new App();
      const seen = [];
      app._apps = { onPackageChanged: (k, p) => seen.push([k, p]) };
      app.onPackageChanged('added', 'org.example.notes');
      app.onPackageChanged('removed', 'org.example.old');
      assert.deepEqual(seen, [['added', 'org.example.notes'], ['removed', 'org.example.old']]);
    """)


def test_a_package_change_before_the_apps_page_ever_opened_builds_nothing() -> None:
    """L'elenco non e' ancora stato letto, e quando lo sara' sara' fresco: il
    guscio nativo chiama comunque, e non deve rompersi ne' costruire."""
    _run_packages("""
      const app = new App();
      app.onPackageChanged('added', 'org.example.notes');
      assert.equal(app._apps, null);
    """)


def test_the_open_app_question_has_a_public_answer() -> None:
    """La casa chiede ad `AppsActions` se c'e' una mini-app aperta: prima lo
    leggeva dal suo campo privato `_openApp`."""
    src = (APP_JS.parent / "shared" / "apps-actions.js").read_text(encoding="utf-8")
    run_js(
        "import assert from 'node:assert/strict';\n"
        "class FakeActions {\n  constructor() { this._openApp = null; }\n  "
        + member(src, "isAppOpen", prefixes=())
        + "\n}\n"
        "const a = new FakeActions();\n"
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
    ctor = member(src, "constructor", prefixes=())
    assert not re.search(r"this\.strip\??\.draw\(\)", ctor), (
        "la fila si disegna prima che le traduzioni siano arrivate"
    )
    init = member(src, "init", prefixes=("async ",))
    assert init.index("await i18n.load(") < init.index("this._applyTranslations()")
    assert "this.strip?.draw();" in member(src, "_applyTranslations")


def test_the_shell_keeps_the_native_contract_and_no_dead_doors() -> None:
    """``whenShellReady`` (con la sua coda ``_shellReadyCbs``) e ``openLauncher``
    erano copiati dall'officina e in casa non li chiamava nessuno: ne' il
    Kotlin (che chiama i sei metodi del contratto), ne' i moduli che la casa
    carica — il wizard del primo avvio, l'unico che chiama ``whenShellReady``,
    ha un documento suo (``onboarding.html``). Codice morto che sembra un
    contratto.
    ``onNativeReady`` resta: il guscio nativo lo chiama comunque."""
    src = APP_JS.read_text(encoding="utf-8")
    for name in (
        "onNativeReady",
        "goHome",
        "onPackageChanged",
        "handleHardwareBack",
        "openChat",
        "isChatOnScreen",
    ):
        member(src, name)
    for dead in ("whenShellReady", "_shellReadyCbs", "openLauncher"):
        assert dead not in src, f"{dead} e' tornato in home-app.js"
    assets = APP_JS.parent
    loaded = [*assets.glob("home-*.js"), *(assets / "shared").glob("*.js"), assets / "mobile-launcher.js"]
    for path in loaded:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\.(whenShellReady|openLauncher)\(", text), path.name
