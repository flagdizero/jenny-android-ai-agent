"""``GatewayService.startForegroundCompat`` non cade quando Android rifiuta l'avvio.

Il primo tentativo chiede anche il tipo ``location``. Da Android 12 un avvio in
foreground da background puo' essere rifiutato con
``ForegroundServiceStartNotAllowedException`` — una ``IllegalStateException``,
non una ``SecurityException``: quel ramo prendeva solo la seconda, e la prima
usciva dal metodo facendo cadere il servizio prima del ripiego senza
``location``, che la stessa eccezione la gestisce gia'.

La classe madre e non la figlia: ``ForegroundServiceStartNotAllowedException``
nasce con l'API 31, e nominarla in un ``catch`` sotto la 31 non si puo'.
"""

from __future__ import annotations

import re

from support.kotlin_source import block_after, function_body, read_code


def _start() -> str:
    return function_body(read_code("GatewayService"), "startForegroundCompat")


def test_the_location_attempt_catches_a_start_not_allowed() -> None:
    body = _start()
    location = block_after(body, r"hasLocationPermission\(\)\s*\)")
    catches = re.findall(r"catch \(e: (\w+)\)", location)
    assert "SecurityException" in catches, catches
    assert "IllegalStateException" in catches, (
        "ForegroundServiceStartNotAllowedException esce dal tentativo con location"
    )
    assert "ForegroundServiceStartNotAllowedException" not in body, (
        "una classe che sotto l'API 31 non esiste"
    )


def test_a_refused_location_attempt_falls_back_or_keeps_the_type_it_has() -> None:
    body = _start()
    location = block_after(body, r"hasLocationPermission\(\)\s*\)")
    tail = location[location.index("catch (e: IllegalStateException)") :]
    handler = tail[: tail.index("}") + 1]
    assert "if (hasLocationType) return true" in handler
    assert "throw" not in handler
    after = body[body.index(location) + len(location) :]
    assert "FOREGROUND_SERVICE_TYPE_SPECIAL_USE)" in after, "il ripiego senza location"
    assert re.search(r"catch \(e: Exception\)", after), "il ripiego resta l'ultima rete"


def test_the_location_type_follows_the_same_permission_as_the_tool() -> None:
    """Con la posizione approssimativa (solo COARSE) il servizio non chiedeva mai
    il tipo ``location``: guardava FINE soltanto, mentre ``LocationBridge``
    accetta l'una o l'altra. Una regola sola, quella del ponte."""
    code = read_code("GatewayService")
    at = code.index("fun hasLocationPermission(")
    check = code[at : code.index("\n", at)]
    assert "LocationBridge.hasPermission(this)" in check
    assert "ACCESS_FINE_LOCATION" not in check
