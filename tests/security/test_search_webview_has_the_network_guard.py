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
