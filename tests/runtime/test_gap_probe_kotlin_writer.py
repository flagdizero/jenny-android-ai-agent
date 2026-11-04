"""La fotografia del battito ha uno scrittore Kotlin, e scatta prima del battito nuovo.

``gap_history.read_probe`` legge due chiavi delle preferenze che solo il Kotlin
può scrivere. Fino al 02/11/2026 nessun Kotlin le scriveva: lo storico delle
interruzioni restava vuoto e l'officina diceva «nessuna interruzione», mentre i
test di Python passavano perché esercitavano solo la metà di Python. Qui si
fissano le due cose che il codice Python non vede: chi scrive le chiavi, e che
lo fa prima che ``GatewayService.onCreate`` rinfreschi il battito — dopo, la
fotografia direbbe sempre «adesso» e il buco sparirebbe.
"""

from __future__ import annotations

from support.kotlin_source import ANDROID_SRC, function_body, read_source

from jenny.runtime import gap_history


def test_watchdog_writes_both_probe_keys() -> None:
    source = read_source(ANDROID_SRC / "Watchdog.kt")
    body = function_body(source, "takeGapProbe")
    assert f'"{gap_history.PREF_PROBE_VALUE}"' in source
    assert f'"{gap_history.PREF_PROBE_AT}"' in source
    assert "KEY_PROBE_VALUE" in body and "KEY_PROBE_AT" in body
    prefs = read_source(ANDROID_SRC / "JennyPrefs.kt")
    assert f'const val NAME = "{gap_history.PREFS_NAME}"' in prefs
    assert "JennyPrefs.of(" in source


def test_service_takes_the_probe_before_the_new_heartbeat() -> None:
    body = function_body(read_source(ANDROID_SRC / "GatewayService.kt"), "onCreate")
    probe = body.find("Watchdog.takeGapProbe(")
    alive = body.find("Watchdog.noteAlive(")
    start = body.find("startGateway()")
    assert probe != -1, "GatewayService.onCreate non scatta la fotografia del battito"
    assert probe < alive < start
