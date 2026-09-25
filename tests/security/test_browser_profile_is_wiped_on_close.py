"""Il profilo «incognito» del browser dell'agente si svuota davvero alla chiusura.

``JennyBrowserBridge.close`` chiamava ``ProfileStore.deleteProfile`` dal thread
del chiamante (Python) dentro un ``catch`` muto. L'API è ``@UiThread``, e anche
sul main rifiuta un profilo già caricato in memoria (lo dice la documentazione
di ``ProfileStore``): la cancellazione non riusciva mai, e nessuno lo vedeva.
Cookie e login sopravvivevano a ``browser_close`` e ai riavvii (H4 della
revisione profonda, 25/09).

La regola ha tre pezzi, e il Kotlin non gira in CI: si fissa sul **codice**
(commenti e stringhe esclusi, v. ``support/kotlin_source.py``).
"""

from __future__ import annotations

import re

from support.kotlin_source import block_after, function_body, read_code


def _code() -> str:
    return read_code("JennyBrowserBridge")


def test_close_wipes_the_profile_on_the_main_thread_after_destroy() -> None:
    close = function_body(_code(), "close")
    hop = block_after(close, r"MainHop\.call\(")
    assert ".destroy()" in hop, "la WebView si distrugge nel salto sul main"
    assert "wipeProfileOnMain(" in hop, (
        "il profilo si svuota nel salto sul main: ProfileStore/Profile sono @UiThread"
    )
    assert hop.index(".destroy()") < hop.index("wipeProfileOnMain("), (
        "prima si distrugge la WebView: deleteProfile rifiuta un profilo con WebView vive"
    )
    outside = close.replace(hop, "")
    assert "ProfileStore" not in outside and "deleteProfile" not in outside, (
        "niente API del profilo fuori dal main thread"
    )


def test_the_wipe_clears_what_the_profile_exposes() -> None:
    """``deleteProfile`` nello stesso processo solleva sempre: si svuota il resto."""
    wipe = function_body(_code(), "wipeProfileOnMain")
    for call in (".removeAllCookies(", ".deleteAllData(", ".deleteProfile("):
        assert call in wipe, f"manca {call} nella pulizia del profilo"


def test_a_leftover_profile_is_discarded_before_it_is_loaded() -> None:
    """L'unico momento in cui ``deleteProfile`` può riuscire: prima del caricamento."""
    ensure = function_body(_code(), "ensureWebViewOnMain")
    discard = ensure.find("discardLeftoverProfileOnMain(")
    load = ensure.find(".getOrCreateProfile(")
    assert discard != -1 and load != -1
    assert discard < load
    body = function_body(_code(), "discardLeftoverProfileOnMain")
    assert ".deleteProfile(" in body


def test_no_silent_catch_in_the_bridge() -> None:
    """Un ``catch`` vuoto è quello che ha nascosto il difetto per un mese."""
    empty = re.findall(r"catch\s*\([^)]*\)\s*\{\s*\}", _code())
    assert not empty, f"catch muti nel ponte del browser: {empty}"
