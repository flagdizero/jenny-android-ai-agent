"""La WebView della ricerca e di ``web_fetch`` passa dalla guardia del browser.

Fino al 02/11/2026 solo la sessione di browser filtrava le richieste verso gli
indirizzi privati: la WebView di ``AgenticSearchBridge`` caricava la pagina col
suo JavaScript acceso e nessun ``shouldInterceptRequest``, e Python controllava
soltanto l'URL finale, a richieste già partite. Qui si fissa che il suo client
chieda lo **stesso** verdetto, non una copia delle reti.
"""

from __future__ import annotations

from support.kotlin_source import function_body, read_code


def test_the_search_client_intercepts_every_request() -> None:
    code = read_code("AgenticSearchBridge")
    intercept = function_body(code, "shouldInterceptRequest")
    assert "JennyBrowserBridge.blockedResponse(" in intercept
    assert "isForMainFrame" in intercept, "il frame principale rifiutato diventa un errore"


def test_the_verdict_is_shared_not_copied() -> None:
    code = read_code("AgenticSearchBridge")
    assert "isBlockedAddress" not in code and "BLOCKED_V4" not in code
    bridge = read_code("JennyBrowserBridge")
    companion = bridge[bridge.index("companion object") :]
    assert "internal fun blockedResponse(" in companion


def test_the_search_webview_gets_the_page_side_guard() -> None:
    """``fetchUrl`` carica pagine qualunque col JavaScript acceso: senza la
    guardia lato pagina potevano aprire ``ws://192.168.x.y``, che
    ``shouldInterceptRequest`` non vede. La guardia è la stessa del browser,
    installata dal companion, non una copia."""
    ensure = function_body(read_code("AgenticSearchBridge"), "ensureWebView")
    assert "JennyBrowserBridge.installNetworkGuardOnMain(" in ensure
    bridge = read_code("JennyBrowserBridge")
    companion = bridge[bridge.index("companion object") :]
    companion = companion[: companion.index("\n    private val handler")]
    assert "internal fun installNetworkGuardOnMain(" in companion
    assert "class NetworkGuard" in companion
    assert "private val guardDns" in companion, "il pool del DNS è di processo, come la cache"
    assert "inner class NetworkGuard" not in bridge


def test_the_security_model_says_the_search_webview_has_the_script() -> None:
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs/internals/security-model.md").read_text(
        encoding="utf-8"
    )
    flat = " ".join(doc.split())
    assert "but not its document-start script" not in flat
    assert "has the browser's HTTP check (every redirect and subresource) and its document-start script" in flat
