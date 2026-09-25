"""``MainHop.call`` scrive nel log e torna il fallback anche sul main thread.

La KDoc lo prometteva «sempre», ma il ramo che esegue il blocco sul posto (già
sul main) non aveva il ``try``: un'eccezione lì risaliva al chiamante. Oggi
nessun chiamante arriva dal main thread, ma la regola scritta deve valere
sulla carta e nel codice. Visto dalla revisione finale della pulizia (25/09).

Il Kotlin non gira in CI: la regola si fissa sul sorgente — **senza commenti**
e cercando la struttura, non le parole: il commento del ramo dice già «log e
*fallback*», e un ``try`` o un ``catch`` scritti in prosa facevano passare il
banco con il codice tornato a ``return block()``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from support.kotlin import block_after, strip_comments

MAIN_HOP = (
    Path(__file__).resolve().parents[2]
    / "android/app/src/main/java/com/flagdizero/jenny/MainHop.kt"
)

# ``block()`` dentro un ``try``, con un ``catch (e: Exception)`` subito dopo.
_TRY_BLOCK_CATCH = re.compile(
    r"\btry\s*\{[^{}]*\bblock\(\)[^{}]*\}\s*catch\s*\(\s*e\s*:\s*Exception\s*\)\s*\{([^{}]*)\}"
)


def _call_body() -> str:
    if not MAIN_HOP.is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    code = strip_comments(MAIN_HOP.read_text(encoding="utf-8"))
    m = re.search(r"\bfun\s*<T>\s*call\(", code)
    assert m, "MainHop.call non trovato"
    body = block_after(code, m.end())
    assert body is not None, "corpo di MainHop.call non trovato"
    return body


def test_the_in_place_branch_catches_and_falls_back() -> None:
    body = _call_body()
    m = re.search(r"if\s*\(\s*Looper\.myLooper\(\)\s*==\s*Looper\.getMainLooper\(\)\s*\)", body)
    assert m, "il ramo sul posto non c'è più"
    branch = block_after(body, m.end())
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
    posted = block_after(body, m.end())
    assert posted is not None
    caught = _TRY_BLOCK_CATCH.search(posted)
    assert caught, "il blocco postato esegue block() senza try/catch"
    assert re.search(r"\bLog\.e\(", caught.group(1)), "il catch postato non scrive nel log"
