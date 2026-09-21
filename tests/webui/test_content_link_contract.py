"""Un link nel testo non deve poter portare via la SPA.

Il difetto storico: né la chat né la wiki intercettavano gli ``<a href>`` del
contenuto. ``renderMarkdown`` non registra alcun renderer per ``a``, DOMPurify
conserva gli href relativi, e in wiki ``_wireWikiLinks`` cablava soltanto
``a.wikilink`` — la classe che ``jenny/webui/wiki.py`` emette **solo** per
``[[Target]]``. Quindi un ``[report](note.md)`` o un ``[cerca](www.google.com)``
scritti da Jenny (o a mano in una pagina wiki) erano navigazioni di main frame
vere, risolte sull'origine del gateway:

* la SPA veniva ricaricata **senza** il fragment ``#bs=`` — de-autenticata,
  perché il segreto di bootstrap si legge una volta sola al module-load;
* oppure, sotto ``/api/``, il documento veniva sostituito da un 404 JSON:
  ``window.mobileApp`` spariva e il tasto Indietro — il cui callback nativo è
  abilitato incondizionatamente — ingoiava ogni pressione per sempre. Su un
  telefono in cui questa app *è* il launcher significa telefono inutilizzabile.

Lato nativo il buco era doppio: ``isInternalGatewayUrl`` guardava solo host e
porta, quindi ``/html-mobile/www.google.com`` passava per "SPA"; e l'espressione
valutata a ogni back (``if (window.mobileApp) …``) valeva sempre ``"null"``, così
il guscio non poteva accorgersi di aver perso la SPA.

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``: la
WebUI non ha un runner JS con DOM.

**La meta' della wiki e' uscita di qui il 21/09/2026.** Tre banchi guardavano
``_wireWikiLinks`` in ``mobile-wiki.js``: quella vista non e' piu' in officina.
La stessa regola vale ora per il lettore della casa, e la' e' **misurata** e non
grepata — ``test_casa_reader_client.py`` la fa girare in node su un DOM finto:
relativo, wikilink dello stesso quaderno, wikilink di un altro, web, ancora,
link morto. Quel che resta qui e' la meta' della chat, che non ha un gemello.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
CHAT_JS = ASSETS / "mobile-chat.js"
WIKI_JS = ASSETS / "mobile-wiki.js"
I18N_DIR = ASSETS / "i18n"
MAIN_ACTIVITY = (
    ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "flagdizero" / "jenny" / "MainActivity.kt"
)

# Tutto ciò che, eseguito prima di preventDefault(), lascerebbe partire la
# navigazione del main frame (o la renderebbe inutile).
_ESCAPE_HATCHES = ("return", "window.open", "location", "loadWikiPage", "loadHome")


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  {name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def _chat() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _wiki() -> str:
    return WIKI_JS.read_text(encoding="utf-8")


def _assert_prevented_first(code: str, what: str) -> None:
    """Nessuna via d'uscita prima di ``preventDefault()``."""
    code = _strip_comments(code)
    idx = code.find("preventDefault()")
    assert idx > 0, f"{what}: manca preventDefault()"
    head = code[:idx]
    for hatch in _ESCAPE_HATCHES:
        assert hatch not in head, (
            f"{what}: '{hatch}' prima di preventDefault() — quel ramo lascia navigare il main frame"
        )


def test_the_chat_intercepts_every_anchor_before_anything_else() -> None:
    """Il ramo dei link sta in testa al listener della chatArea.

    Sotto ci sono già la copia dei blocchi di codice e la lightbox: se il tap
    finisse lì, l'``<a>`` che li contiene navigherebbe comunque.
    """
    listener = re.search(
        r"this\.chatArea\.addEventListener\('click', \(e\) => \{(.*?)\n    \}\);",
        _chat(),
        re.S,
    )
    assert listener, "listener click della chatArea non trovato"
    body = _strip_comments(listener.group(1))
    assert "closest('a[href]')" in body, "nessun ramo per gli <a href> del markdown"
    assert body.index("closest('a[href]')") < body.index("closest('.chat-code-copy')"), (
        "il ramo dei link deve precedere gli altri handler della chat"
    )
    assert "_handleContentLink(e, link)" in body


def test_the_chat_leaves_alone_the_anchors_it_wires_itself() -> None:
    """Il ramo in testa non deve rubare i click che qualcun altro ha già preso.

    Difetto della prima stesura di questo ramo: la chat costruisce da sé due
    ``<a>`` che vivono dentro ``.chat-msg`` (quindi dentro ``#chat-area``) e che
    hanno un listener diretto — ``.file-preview-action`` (``href="#workspace"``,
    apre il file nel Workspace) e ``.chat-file-path-link`` (``href="#"``). I
    listener diretti girano in fase target e fanno ``preventDefault()``, poi il
    click bollava fino al listener della chatArea, che lo reinterpretava come
    link del modello: ``#workspace`` non è un id della conversazione, quindi
    "Apri nell'editor" funzionava **e** mostrava il toast ``linkNotOpenable``.

    ``e.defaultPrevented`` è il segnale esatto di "questo <a> ha un padrone": la
    wiki risolve lo stesso problema con l'esenzione esplicita dei breadcrumb.
    """
    listener = re.search(
        r"this\.chatArea\.addEventListener\('click', \(e\) => \{(.*?)\n    \}\);",
        _chat(),
        re.S,
    )
    assert listener, "listener click della chatArea non trovato"
    body = _strip_comments(listener.group(1))
    assert "e.defaultPrevented" in body, (
        "senza il controllo, gli <a> già gestiti dalla chat prendono anche il trattamento "
        "riservato ai link del modello (falso toast di link inerte)"
    )
    assert body.index("e.defaultPrevented") < body.index("_handleContentLink"), (
        "il controllo deve fare da guardia alla delega, non venire dopo"
    )


def test_the_chat_anchors_skipped_by_that_guard_are_wired_elsewhere() -> None:
    """Gemello di ``..._skipped_by_the_wiki_wiring_are_wired_elsewhere``: gli
    ``<a>`` esentati sono esentati solo perché annullano già il click da sé. Se
    uno di loro perdesse il ``preventDefault()``, tornerebbe a essere una
    navigazione di main frame vera e il buco si riaprirebbe lì."""
    source = _chat()
    for cls, owner in (
        (".file-preview-action", r"async _renderFilePreview\(filePath, container\)"),
        (".chat-file-path-link", r"_makeFilePathsClickable\(container\)"),
    ):
        method = re.search(rf"\n  {owner}\s*\{{(.*?)\n  \}}", source, re.S)
        assert method, f"il metodo che crea {cls} non è stato trovato"
        code = _strip_comments(method.group(1))
        assert cls.lstrip(".") in code, f"{cls} non è più creato lì"
        handler = re.search(r"addEventListener\('click', (?:async )?\(e\) => \{(.*)", code, re.S)
        assert handler, f"{cls} non ha più un listener diretto: senza, il click bolla e basta"
        assert "e.preventDefault()" in handler.group(1), (
            f"{cls} è esentato dal ramo dei link solo perché annulla già il click da sé"
        )


def test_no_chat_link_branch_can_reach_a_navigation() -> None:
    """Ancora interna → scroll, origine diversa → fuori dalla WebView, resto → inerte."""
    body = _method(_chat(), "_handleContentLink")
    _assert_prevented_first(body, "_handleContentLink")
    code = _strip_comments(body)
    assert "startsWith('#')" in code and "_scrollToChatAnchor" in code, (
        "l'ancora interna deve diventare uno scroll, non una entry di history"
    )
    assert "url.origin !== window.location.origin" in code, (
        "senza il confronto di origine un href relativo passerebbe per link esterno e ricaricherebbe la SPA"
    )
    assert "_openOutsideWebView" in code
    assert "common.linkNotOpenable" in code, "il ramo inerte deve dirlo, non tacere"


def test_both_locales_carry_the_inert_link_message() -> None:
    for locale in ("it", "en"):
        data = json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        assert data["common"].get("linkNotOpenable"), f"chiave mancante in {locale}.json"


def test_the_shell_calls_the_spa_pages_internal_only_by_exact_path() -> None:
    """Il prefisso non basta: ``/html-mobile/www.google.com`` lo soddisferebbe.

    I documenti-guscio sono due — la casa (``index.html``) e l'officina
    (``officina.html``) — e l'elenco vive in ``isShellDocument``. Quel che non
    deve cambiare e' **come** si confrontano: per uguaglianza, uno per uno. Un
    ``startsWith`` sotto il gateway riaprirebbe il buco per intero.
    """
    kotlin = MAIN_ACTIVITY.read_text(encoding="utf-8")
    body = re.search(
        r"private fun isShellDocument\(path: String\): Boolean \{(.*?)\n    \}", kotlin, re.S
    )
    assert body, "isShellDocument non trovato"
    code = body.group(1)
    assert "path ==" in code, "i path dei gusci vanno confrontati per uguaglianza"
    assert "startsWith" not in code, "un confronto per prefisso riapre il buco"
    assert "contains" not in code, "un confronto per sottostringa riapre il buco"
    # I due gusci, per nome: se uno sparisce, la sua porta smette di aprirsi.
    assert "index.html" in code and "officina.html" in code
    # E il predicato usato dal WebViewClient deve passare di qui, non altrove.
    caller = re.search(
        r"private fun isInternalGatewayUrl\(uri: Uri\): Boolean \{(.*?)\n    \}", kotlin, re.S
    )
    assert caller and "isShellDocument(path)" in caller.group(1)
    assert 'GATEWAY_PATH = "/html-mobile/"' in kotlin


def test_a_gateway_url_that_is_not_the_spa_is_blocked_rather_than_handed_out() -> None:
    """``/api/…`` non è la SPA e non è nemmeno roba da Custom Tab: aprirlo fuori
    esporrebbe il gateway locale a un altro processo. Si blocca e basta."""
    kotlin = MAIN_ACTIVITY.read_text(encoding="utf-8")
    override = re.search(
        r"override fun shouldOverrideUrlLoading\((.*?)\n            \}", kotlin, re.S
    )
    assert override, "shouldOverrideUrlLoading non trovato"
    code = _strip_comments(override.group(1))
    assert "if (isGatewayOrigin(uri)) {" in code
    assert code.index("isGatewayOrigin(uri)") < code.index("openExternalUrl(uri)")


def test_the_back_press_asks_a_question_that_can_be_answered_no() -> None:
    """``if (window.mobileApp) …`` valeva sempre ``"null"``: SPA viva e SPA
    sparita davano al nativo esattamente la stessa risposta."""
    kotlin = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "if (window.mobileApp) window.mobileApp.handleHardwareBack()" not in kotlin
    probe = re.search(r"BACK_PRESS_JS = \"\"\"(.*?)\"\"\"", kotlin, re.S)
    assert probe, "BACK_PRESS_JS non trovato"
    js = probe.group(1)
    assert "return false;" in js and "return true;" in js, "il probe deve tornare un booleano vero"
    assert "typeof app.handleHardwareBack !== 'function'" in js
    assert "app.handleHardwareBack();" in js
    assert "setTimeout" in js, (
        "un'eccezione del back deve restare rumorosa (window.onerror), non diventare un reload"
    )
    assert 'result?.trim() != "true"' in kotlin
    assert "recoverLostSpa()" in kotlin


def test_losing_the_spa_is_recoverable_and_keeps_the_bootstrap_fragment() -> None:
    """Ricaricare senza ``#bs=`` darebbe una SPA visibile ma de-autenticata.

    La guardia si restringe al solo boot. La prima stesura escludeva anche
    ``mainFrameError``, cioè proprio lo stato in cui nient'altro recupera:
    ``onReceivedError`` chiama ``scheduleRetry()`` solo ``if (!loaded)`` e
    ``mainFrameError`` torna false solo in ``onPageStarted``, quindi una
    navigazione di main frame fallita dopo il boot lasciava la pagina d'errore
    della WebView al posto della SPA per sempre — tasto Indietro morto, che è
    esattamente il difetto che questo recupero esiste per chiudere.
    """
    kotlin = MAIN_ACTIVITY.read_text(encoding="utf-8")
    body = re.search(r"private fun recoverLostSpa\(\) \{(.*?)\n    \}", kotlin, re.S)
    assert body, "recoverLostSpa non trovato"
    code = _strip_comments(body.group(1))
    assert "loadUrl(resolvedGatewayUrl)" in code, "l'URL risolto è l'unico che porta il fragment"
    assert "GATEWAY_URL" not in code
    assert "mainFrameError" not in code, (
        "escludere mainFrameError disattiva il recupero nell'unico stato in cui serve"
    )
    assert "if (!loaded) return" in code, "il recupero va inibito solo durante il primo caricamento"
    assert "SPA_RECOVERY_MIN_INTERVAL_MS" in code, "senza debounce il recupero può ripartire a raffica"
