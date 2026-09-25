"""``browser_open`` non risponde «ok» se la pagina non è mai partita.

``JennyBrowserBridge.open`` crea la WebView e lancia ``loadUrl`` con un salto
sul main thread (``MainHop.call``). Quel salto torna il *fallback* se il blocco
solleva — il costruttore della WebView, mentre Android aggiorna il provider — o
se il main thread non risponde in tempo. Con ``Unit`` come esito, ``open`` non
se ne accorgeva: aspettava un caricamento inesistente e rispondeva
``{"ok":true}`` con indirizzo e titolo vuoti, che al modello sembra una pagina
bianca aperta davvero. Trovato dalla revisione finale della pulizia (25/09).

Il Kotlin non gira in CI: la regola si fissa sul sorgente, come in
``test_webview_debugging_is_gated.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BRIDGE = (
    Path(__file__).resolve().parents[2]
    / "android/app/src/main/java/com/flagdizero/jenny/JennyBrowserBridge.kt"
)


def _open_body() -> str:
    if not BRIDGE.is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    src = BRIDGE.read_text(encoding="utf-8")
    m = re.search(r"\n    fun open\(url: String.*?\n    \}\n", src, re.S)
    assert m, "JennyBrowserBridge.open non trovato"
    return m.group(0)


def test_open_reads_whether_the_page_started() -> None:
    body = _open_body()
    assert "MainHop.call(10_000L, false, TAG)" in body, (
        "il salto sul main thread deve dire se la pagina è partita (fallback false)"
    )
    refusal = "if (!started && gate.compareAndSet(GATE_OPEN, GATE_ABANDONED))"
    assert refusal in body
    before_wait = body.split("awaitSettled(", 1)[0]
    assert '"error"' in before_wait.split(refusal, 1)[1], (
        "senza partenza si risponde con un errore prima di aspettare il caricamento"
    )


def test_a_timed_out_open_cannot_load_its_page_later() -> None:
    """Un tetto scaduto non toglie il blocco dalla coda del main: gira dopo. Se
    allora caricasse, la pagina partirebbe dopo il «non e' partita»: una
    navigazione che nessuno aspetta. Il cancello si prende **prima**
    di ``loadUrl``, e il «no» lo chiude prima di rispondere: uno dei due soltanto."""
    body = _open_body()
    block = body.split("MainHop.call(10_000L, false, TAG)", 1)[1].split("\n        }\n", 1)[0]
    take = "if (!gate.compareAndSet(GATE_OPEN, GATE_LOADING)) return@call false"
    assert take in block
    assert block.index(take) < block.index("loadUrl(url)"), (
        "il cancello va preso prima di far partire la pagina"
    )


def test_an_abandoned_open_builds_no_webview() -> None:
    """Il blocco in ritardo trova il cancello gia' chiuso: esce prima di costruire
    una WebView che non servira' (con la sonda del 25/09 ne restava una viva,
    ``url=null``, fino a ``browser_close``)."""
    body = _open_body()
    block = body.split("MainHop.call(10_000L, false, TAG)", 1)[1].split("\n        }\n", 1)[0]
    skip = "if (gate.get() == GATE_ABANDONED) return@call false"
    assert skip in block
    assert block.index(skip) < block.index("ensureWebViewOnMain()")


def test_a_missing_webview_counts_as_not_started() -> None:
    body = _open_body()
    assert "webView ?: return@call false" in body, (
        "una WebView che non c'è dopo ensureWebViewOnMain non è una pagina partita"
    )
