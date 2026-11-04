"""«Info app» si apre in un task suo, e Indietro torna a Jenny.

Con il solo ``FLAG_ACTIVITY_NEW_TASK``, avviata dal contesto dell'applicazione,
la schermata di sistema entrava per affinita' nel task di Impostazioni gia'
aperto: sul Titan 2, il 26/09/2026, Indietro portava a «Rete e Internet» lasciata
li' da prima, non a Jenny. Il comportamento vero si vede solo sul telefono; qui
si tiene fermo il flag che lo decide.

Due posti la aprono: «Info app» di un'app nella griglia (``InstalledAppsBridge``)
e la scheda di Jenny dove si concede un permesso che l'app non puo' piu'
chiedere (``MainActivity.openAppDetailsSettings``), che aveva il solo
``NEW_TASK``. Tutti e due da ``Intents.appDetails``.
"""

from __future__ import annotations

from support.kotlin_source import function_body, read_code, read_source


def test_app_info_gets_its_own_task() -> None:
    intents = read_source("Intents")
    at = intents.index("fun appDetails(")
    builder = intents[at:]
    assert "FLAG_ACTIVITY_NEW_DOCUMENT" in builder
    assert "ACTION_APPLICATION_DETAILS_SETTINGS" in builder


def test_both_openers_use_the_same_intent() -> None:
    bridge = read_code("InstalledAppsBridge")
    assert "Intents.appDetails(" in bridge[bridge.index("fun openAppInfo(") :]
    assert "Intents.appDetails(" in function_body(read_code("MainActivity"), "openAppDetailsSettings")
    assert "ACTION_APPLICATION_DETAILS_SETTINGS" not in read_code("InstalledAppsBridge")
