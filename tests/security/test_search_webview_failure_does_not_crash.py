"""Una WebView di ricerca che non nasce non abbatte il processo.

``AgenticSearchBridge.evaluateOnPage`` posta sul main thread la creazione della
WebView e il ``loadUrl``. Senza ``try``, un costruttore che solleva — succede
mentre Android aggiorna il provider WebView — lancia l'eccezione sul main
thread, e il processo muore con il gateway dentro. Il browser dell'agente era
già protetto (``MainHop``); la ricerca no (M12 della revisione profonda, 25/09).

Il Kotlin non gira in CI: la regola si fissa sul **codice**, commenti e
stringhe esclusi (``support/kotlin_source.py``).
"""

from __future__ import annotations

import re

from support.kotlin_source import block_after, block_at, function_body, read_code


def _posted_block() -> str:
    body = function_body(read_code("AgenticSearchBridge"), "evaluateOnPage")
    return block_after(body, r"handler\.post\s*")


def test_the_posted_block_is_all_inside_a_try() -> None:
    posted = _posted_block()
    inner = posted[1:-1].strip()
    assert inner.startswith("try"), "il blocco sul main deve aprirsi con un try"
    guarded = block_at(inner, 0)
    for call in ("ensureWebView()", ".loadUrl("):
        assert call in guarded, f"{call} deve stare dentro il try"


def test_the_failure_is_reported_and_releases_the_wait() -> None:
    posted = _posted_block()
    m = re.search(r"catch\s*\(\s*e\s*:\s*Exception\s*\)\s*", posted)
    assert m, "manca il catch (e: Exception) nel blocco sul main"
    handler = block_at(posted, m.end())
    assert "errorRef.set(" in handler, "l'errore va detto al chiamante"
    assert "latch.countDown()" in handler, (
        "e l'attesa va sbloccata, o il chiamante aspetta il tetto intero"
    )
