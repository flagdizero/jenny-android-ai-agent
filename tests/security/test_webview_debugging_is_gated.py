"""Il debugging delle WebView non deve arrivare acceso in una build di release.

``WebView.setWebContentsDebuggingEnabled(true)`` girava in un inizializzatore
statico senza guardia, con un ``catch`` che diceva "safe to ignore on
non-debuggable builds": una protezione che non esiste, perché su una release la
chiamata non solleva, semplicemente abilita. Ogni WebView dell'app spedita era
ispezionabile da un host che raggiungesse il socket di debug del dispositivo — e
la WebView nascosta della ricerca porta quello che l'agente naviga, cookie
compresi.

Il Kotlin non gira in CI, quindi la regola si pinna da qui come per le costanti
condivise in ``tests/snapshot/test_cross_boundary_contract.py``: chi rimettesse la
chiamata senza cancello fallisce qui invece che sul telefono di qualcuno.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from support.kotlin import strip_comments

REPO_ROOT = Path(__file__).resolve().parents[2]
ANDROID_SRC = REPO_ROOT / "android/app/src/main/java/com/flagdizero/jenny"

ENABLE_CALL = "setWebContentsDebuggingEnabled(true)"
# Il cancello guarda il flag dell'APK e non ``BuildConfig.DEBUG``: è la proprietà
# che decide se il socket di debug esiste, e AGP 8 non genera più ``BuildConfig``
# se un modulo non lo chiede.
GATE = "FLAG_DEBUGGABLE"


def _kotlin_sources() -> list[Path]:
    if not ANDROID_SRC.is_dir():
        pytest.skip("sorgente Android non presente in questo checkout")
    return sorted(ANDROID_SRC.rglob("*.kt"))


def _enclosing_function(code: str, at: int) -> str:
    """Il codice dall'ultima ``fun`` prima di *at* fino ad *at*.

    L'ultima dichiarazione prima della chiamata è la funzione che la contiene
    (nessuna ``fun`` locale in mezzo, in questi sorgenti): un cancello in
    un'altra funzione del file non conta.
    """
    starts = [m.start() for m in re.finditer(r"\bfun\s+\w+\s*\(", code[:at])]
    return code[starts[-1] if starts else 0 : at]


def test_every_enable_call_sits_behind_the_debuggable_gate() -> None:
    """Chi abilita il debugging deve anche controllare di essere in debug.

    Il controllo si cerca nel **codice** della stessa funzione, **prima** della
    chiamata: la KDoc sopra nomina ``FLAG_DEBUGGABLE`` per spiegare il
    cancello, e cercarlo nel file intero faceva passare anche un cancello
    tolto con la spiegazione rimasta.
    """
    unguarded = []
    for path in _kotlin_sources():
        code = strip_comments(path.read_text("utf-8"))
        for m in re.finditer(re.escape(ENABLE_CALL), code):
            if GATE not in _enclosing_function(code, m.start()):
                unguarded.append(path.name)

    assert not unguarded, (
        f"{ENABLE_CALL} senza controllo su {GATE} in: {', '.join(unguarded)}. "
        "Su una build di release la chiamata non solleva: abilita."
    )


def test_the_call_is_still_there_for_debug_builds() -> None:
    """Il cancello non deve essere stato chiuso cancellando la funzione.

    Senza questo, il test sopra passerebbe anche togliendo del tutto la chiamata —
    cioè misurando l'assenza della feature invece della sua protezione.
    """
    sources = _kotlin_sources()
    enabling = [p.name for p in sources if ENABLE_CALL in strip_comments(p.read_text("utf-8"))]

    assert enabling, (
        f"nessun file abilita più {ENABLE_CALL}: se la rimozione è voluta, "
        "va via anche questo test, non solo la chiamata."
    )
