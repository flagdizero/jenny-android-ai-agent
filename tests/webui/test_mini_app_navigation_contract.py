"""Le mini-app in iframe: Indietro sale di un livello, e non lascia macerie.

Tre difetti storici, tutti sulla stessa faglia — l'iframe ha origine opaca, e il
parent non può guardarci dentro:

1. L'SDK navigava con ``history.pushState``. Contrariamente a quanto si pensava,
   in un iframe ``sandbox="allow-scripts"`` **riesce**: verificato in Blink su
   hash, query e path relativo. Il guaio è che le entry finiscono nella joint
   session history del WebView, condivisa col main frame — due pushState del
   figlio portano ``history.length`` da 2 a 4, e dopo ``iframe.remove()`` resta
   4. ``closeApp()`` smonta solo l'overlay, quindi dopo la ✕ restavano una o più
   pressioni di Indietro *morte*: nessun popstate, nessun cambio di URL, niente.
2. Un ``<dialog>`` aperto dentro l'app era invisibile al primo livello della
   catena (che interroga solo il documento del parent), e la skill che genera le
   app prescrive proprio ``<dialog>``: Indietro chiudeva **tutta** l'app
   portandosi via il form a metà.
3. La skill ``app-creator`` non nominava ``jenny.navigate()`` da nessuna parte —
   ed è la skill, non i docs, ciò che l'agente ha in contesto mentre scrive
   un'app: ogni schermata interna nasceva quindi non dichiarata.

Il contratto è: la joint history resta proprietà esclusiva di
``pushNav``/``replaceNav`` della SPA, la profondità dell'app è pura contabilità
dichiarata via ``jenny:nav-state``, e i dialog contano come livelli.

Asserzioni sul sorgente, nello stile di
``test_back_navigation_contract.py``: la WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
SDK_JS = ASSETS / "apps" / "jenny-sdk.js"
APPS_JS = ASSETS / "shared" / "apps-actions.js"
SKILL_DIR = ROOT / "jenny" / "skills" / "app-creator"


def _sdk() -> str:
    return SDK_JS.read_text(encoding="utf-8")


def _apps() -> str:
    return APPS_JS.read_text(encoding="utf-8")


def _fn(source: str, name: str) -> str:
    """Corpo di una ``function name(...)`` di primo livello dell'SDK."""
    body = re.search(rf"\n  function {name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato in jenny-sdk.js"
    return body.group(1)


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  {name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def _no_comments(source: str) -> str:
    """I commenti citano il difetto storico: vanno tolti prima di cercarlo."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//.*", "", source)


# ── #10: nessuna entry lasciata nella joint session history ──


def test_the_sdk_never_touches_the_browser_history() -> None:
    """La history del WebView è condivisa e non potabile: l'iframe non la scrive.

    Non è che ``pushState`` fallisca — riesce. È che nessuno può più togliere
    quelle entry quando l'app si chiude.
    """
    code = _no_comments(_sdk())
    for forbidden in ("history.pushState", "history.replaceState", "history.back",
                      "history.go", "location.hash ="):
        assert forbidden not in code, f"jenny-sdk.js scrive la history con {forbidden}"


def test_navigate_only_counts_a_level_and_declares_it() -> None:
    body = _fn(_sdk(), "navigate")
    assert "navStack.push(state)" in body, "la profondità è contabile: una pila di stati logici"
    assert "postNavState()" in body, "senza dichiararlo, il parent non sa che c'è un livello sopra"


def test_back_replays_the_previous_level_with_a_synthetic_popstate() -> None:
    """Tolta la history vera, l'evento va risintetizzato: le app ascoltano ``popstate``."""
    body = _fn(_sdk(), "back")
    assert "navStack.length <= 1" in body, "al primo livello back() non fa nulla: esce l'app"
    assert "navStack.pop()" in body
    assert "new PopStateEvent('popstate'" in body, (
        "senza l'evento sintetico l'app resta ferma sulla schermata di dettaglio"
    )
    assert "postNavState()" in body


def test_the_dead_can_go_back_field_is_gone() -> None:
    """Nessun consumatore lo leggeva: il parent decide da ``depth``."""
    assert "canGoBack" not in _sdk()


def test_closing_the_app_needs_no_history_unwinding() -> None:
    """Il corollario del fix strutturale: ``closeApp`` non deve svolgere niente."""
    body = _method(_apps(), "closeApp")
    assert "history." not in body, (
        "se closeApp deve svolgere la history vuol dire che l'SDK ha ricominciato a scriverla"
    )
    assert "open.overlay.remove()" in body


# ── #8: un <dialog> dentro l'app è un livello ──


def test_the_open_dialogs_are_counted_into_the_declared_depth() -> None:
    """Il parent non li vede (origine opaca): li dichiara l'app."""
    body = _fn(_sdk(), "postNavState")
    assert "depth: navStack.length + dialogsOpen" in body, (
        "un dialog aperto deve alzare la profondità, altrimenti Indietro chiude l'app intera"
    )


def test_the_sdk_watches_the_open_attribute_of_the_dialogs() -> None:
    code = _sdk()
    assert "new MutationObserver(syncDialogs)" in code
    assert "attributeFilter: ['open']" in code, "l'apertura di un <dialog> è un cambio d'attributo"
    assert "childList: true" in code, "un <dialog> può anche essere inserito già aperto"
    assert "document.querySelectorAll('dialog[open]').length" in _fn(_sdk(), "syncDialogs")


def test_go_back_dismisses_the_topmost_dialog_before_the_screen() -> None:
    """L'ordine è quello della catena dei livelli della SPA, che qui non arriva."""
    handler = _no_comments(_sdk())
    assert "if (!closeTopDialog()) back();" in handler, (
        "il dialog più in alto va congedato per primo"
    )
    body = _fn(_sdk(), "closeTopDialog")
    assert "new Event('cancel', { cancelable: true })" in body, (
        "semantica di Esc: chi rifiuta la chiusura va rispettato, come _dismissTopDialog nella SPA"
    )
    assert "return true" in body, "la pressione è consumata anche se la chiusura è stata rifiutata"


def test_the_parent_routes_the_press_into_the_app_while_depth_is_above_one() -> None:
    body = _method(_apps(), "handleBack")
    assert "open.depth > 1" in body
    assert "{ type: 'jenny:go-back' }" in body
    assert "this.closeApp();" in body, "all'ultimo livello la pressione esce dall'app"


def test_the_declared_depth_is_clamped_before_being_trusted() -> None:
    """Arriva da codice dell'app: un NaN o un numero assurdo bloccherebbe Indietro."""
    body = _method(_apps(), "_onAppMessage")
    assert "Math.min(99, Math.max(1," in body


def test_the_two_sides_agree_on_the_protocol_strings() -> None:
    sdk, apps = _sdk(), _apps()
    for message in ("jenny:nav-state", "jenny:go-back"):
        assert message in sdk and message in apps, f"{message} non è più un contratto condiviso"


# ── #22: la skill è ciò che l'agente ha in contesto ──


def test_the_app_creator_skill_teaches_the_navigation_contract() -> None:
    """I docs lo documentavano già; la skill — che è quella caricata in contesto
    quando l'agente scrive un'app — non lo nominava affatto."""
    skill = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(SKILL_DIR.rglob("*.md"))
    )
    assert "jenny.navigate(" in skill, "l'agente non può usare ciò che la skill non nomina"
    assert "jenny.back()" in skill
    assert "popstate" in skill, "dichiarare il livello senza ridisegnare la schermata non basta"
    assert re.search(r"back button closes the whole app", skill), (
        "la regola va enunciata come conseguenza, non come dettaglio d'API"
    )
