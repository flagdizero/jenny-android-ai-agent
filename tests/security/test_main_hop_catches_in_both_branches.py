"""``MainHop.call`` scrive nel log e torna il fallback anche sul main thread.

La KDoc lo prometteva «sempre», ma il ramo che esegue il blocco sul posto (già
sul main) non aveva il ``try``: un'eccezione lì risaliva al chiamante. Oggi
nessun chiamante arriva dal main thread, ma la regola scritta deve valere
sulla carta e nel codice.

Il Kotlin non gira in CI: la regola si fissa sul sorgente — **senza commenti**
e cercando la struttura, non le parole: il commento del ramo dice già «log e
*fallback*», e un ``try`` o un ``catch`` scritti in prosa facevano passare il
banco con il codice tornato a ``return block()``.
"""

from __future__ import annotations

import re

import pytest
from support.kotlin_source import ANDROID_SRC, block_at, code_only


def _inner(code: str, start: int) -> str | None:
    """Il contenuto del blocco che si apre alla prima graffa da *start*."""
    try:
        return block_at(code, start)[1:-1]
    except (ValueError, AssertionError):
        return None


MAIN_HOP = ANDROID_SRC / "MainHop.kt"

# ``block()`` dentro un ``try``, con un ``catch (e: Throwable)`` subito dopo: un
# ``Error`` sul main abbatte il processo quanto un'eccezione.
_TRY_BLOCK_CATCH = re.compile(
    r"\btry\s*\{[^{}]*\bblock\(\)[^{}]*\}\s*catch\s*\(\s*e\s*:\s*Throwable\s*\)\s*\{([^{}]*)\}"
)


def _call_body() -> str:
    if not MAIN_HOP.is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    code = code_only(MAIN_HOP.read_text(encoding="utf-8"))
    m = re.search(r"\bfun\s*<T>\s*call\(", code)
    assert m, "MainHop.call non trovato"
    body = _inner(code, m.end())
    assert body is not None, "corpo di MainHop.call non trovato"
    return body


def test_the_in_place_branch_catches_and_falls_back() -> None:
    body = _call_body()
    m = re.search(r"if\s*\(\s*Looper\.myLooper\(\)\s*==\s*Looper\.getMainLooper\(\)\s*\)", body)
    assert m, "il ramo sul posto non c'è più"
    branch = _inner(body, m.end())
    assert branch is not None, "il ramo sul posto non è più un blocco"
    assert re.search(r"\breturn\s+try\b", branch), "il ramo sul posto non torna l'esito del try"
    caught = _TRY_BLOCK_CATCH.search(branch)
    assert caught, "il ramo sul posto esegue block() senza try/catch"
    assert re.search(r"\bLog\.e\(", caught.group(1)), "il catch sul posto non scrive nel log"
    assert re.search(r"\bfallback\b", caught.group(1)), "il catch sul posto non vale fallback"


def test_the_posted_branch_catches_too() -> None:
    body = _call_body()
    m = re.search(r"\.post\s*(?=\{)", body)
    assert m, "il salto sul main Looper non c'è più"
    posted = _inner(body, m.end())
    assert posted is not None
    caught = _TRY_BLOCK_CATCH.search(posted)
    assert caught, "il blocco postato esegue block() senza try/catch"
    assert re.search(r"\bLog\.e\(", caught.group(1)), "il catch postato non scrive nel log"


def test_a_timed_out_block_does_not_run_later() -> None:
    """A tetto scaduto ``call`` risponde *fallback*; il blocco restava in coda e
    girava dopo, facendo ciò che si era appena detto non fatto.
    Ora il blocco postato prende lo stato prima di partire, e il chiamante lo
    abbandona prima di rispondere: uno dei due soltanto."""
    body = _call_body()
    m = re.search(r"\.post\s*(?=\{)", body)
    assert m
    posted = _inner(body, m.end())
    assert posted is not None
    take = posted.index("state.compareAndSet(PENDING, RUNNING)")
    assert take < posted.index("block()"), "lo stato si prende prima di eseguire"
    assert "return@post" in posted[take : posted.index("block()")]
    after = body[body.index("done.await(timeoutMs"):]
    abandon = after.index("state.compareAndSet(PENDING, ABANDONED)")
    assert abandon < after.index("return fallback", abandon)
    # Partito allo scadere: se ne aspetta l'esito invece di smentirlo.
    assert after.index("done.await()") > abandon


def test_only_the_browser_close_asks_to_run_late() -> None:
    """La pulizia di ``close`` deve arrivare anche in ritardo: è l'unico salto
    che lo chiede, e lo chiede per nome."""
    callers = []
    for name in ("FloatingBridge", "JennyBrowserBridge", "AgenticSearchBridge"):
        path = MAIN_HOP.with_name(f"{name}.kt")
        if path.is_file():
            code = code_only(path.read_text(encoding="utf-8"))
            callers += [(name, c) for c in re.findall(r"MainHop\.call\(([^)]*)\)", code)]
    late = [c for c in callers if "runLate" in c[1]]
    assert late == [("JennyBrowserBridge", "10_000L, Unit, TAG, runLate = true")], late
