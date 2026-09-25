"""Un ``browser_open`` fallito non lascia la pagina precedente senza recinto.

``JennyBrowserBridge.open`` azzerava ``scopeDomain`` all'inizio e lo ripiantava
solo a successo: ogni ritorno d'errore (pagina non partita, blocco della
guardia, errore di rete) lasciava la pagina di prima caricata e **senza
perimetro**, cioè libera di portare la sessione ovunque con un click. Durante
l'apertura il perimetro è già sospeso da ``openInFlight``; azzerarlo non serve
(M13 della revisione profonda, 25/09).

Il Kotlin non gira in CI: la regola si fissa sul **codice**, commenti e
stringhe esclusi (``support/kotlin_source.py``).
"""

from __future__ import annotations

import re

from support.kotlin_source import function_body, read_code


def _open_body() -> str:
    return function_body(read_code("JennyBrowserBridge"), "open")


def test_open_never_clears_the_scope() -> None:
    assert not re.search(r"scopeDomain\.set\(\s*null\s*\)", _open_body()), (
        "open non azzera il recinto: lo sostituisce quando la pagina si e' posata"
    )


def test_the_scope_is_replaced_only_after_every_error_return() -> None:
    body = _open_body()
    writes = [m.start() for m in re.finditer(r"scopeDomain\.set\(", body)]
    assert len(writes) == 1, "il recinto si scrive in un punto solo di open"
    returns = [m.start() for m in re.finditer(r"\breturn\b", body)]
    last_error_return = max(r for r in returns if r < writes[0])
    assert all(r < writes[0] for r in returns[:-1]), (
        "ogni ritorno d'errore viene prima della scrittura del recinto"
    )
    assert body.index("awaitSettled(") < last_error_return < writes[0]
