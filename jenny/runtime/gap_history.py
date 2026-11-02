"""Storico dei buchi di attività: rendere visibile un gateway ucciso dall'OEM.

I gestori energetici di Samsung, Xiaomi/MIUI, Huawei/Honor, Oppo e Vivo
uccidono le app in background a prescindere da quanto bene l'app si comporti.
Quando succede a Jenny il guasto oggi è **completamente invisibile**: nessun
log, nessun errore, solo promemoria che a un certo punto smettono di arrivare e
un utente che se ne accorge giorni dopo. Questo modulo non impedisce il kill —
nessun codice applicativo può — ma lo rende osservabile a posteriori.

**Quale orologio e perché.** Il buco si misura sull'orologio di parete
(``System.currentTimeMillis`` lato Kotlin, ``time.time()`` qui). È l'unico che
sopravvive sia alla morte del processo sia al riavvio del telefono:
``elapsedRealtime`` riparte a ogni boot e ``time.monotonic``/``perf_counter``
a ogni processo, e siccome è proprio la morte del processo lo scenario da
misurare, un orologio che riparta con lui non misurerebbe niente. Il prezzo è
che l'orologio di parete può saltare (sync NTP, cambio di fuso, ora spostata a
mano): un salto all'indietro produce una durata negativa e uno in avanti una
durata assurda, quindi entrambi vengono scartati da ``_plausible`` invece di
finire nello storico come un buco inventato.

**Da dove arriva il "prima".** Non da un secondo battito. Quello del watchdog
Kotlin (``Watchdog.noteAlive`` → ``SharedPreferences["gatewayHeartbeatMs"]``)
esiste già ed è l'unico timestamp di liveness che sopravvive al processo, ma
``GatewayService.onCreate`` lo aggiorna *prima* di far partire Python: il
gateway, quando finalmente gira, ci trova sempre "adesso" e non vedrebbe mai il
buco che ha appena attraversato. Per questo la fotografia la scatta lo stesso
``GatewayService.onCreate`` un istante prima di rinfrescarlo
(``Watchdog.takeGapProbe``), e la lascia in due
chiavi separate dello stesso file di preferenze — ``gatewayGapProbeMs`` (il
valore del battito) e ``gatewayGapProbeAtMs`` (quando l'ha letto). Il buco è la
differenza fra le due, e resta corretto anche se il gateway la consuma molto
più tardi: entrambi i capi sono stati misurati prima che qualcuno toccasse il
battito.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.runtime.context import get_android_context
from jenny.utils.path import atomic_write

# Quanti buchi tenere. Serve una lista, non un contatore — "quattro buchi tutti
# fra le 2 e le 5 di notte" è una diagnosi, "quattro buchi" non lo è — ma serve
# anche che il file resti piccolo: è letto a ogni apertura delle impostazioni.
MAX_GAPS = 20

# Stesso file di preferenze di MainActivity/Watchdog: una sola
# ``SharedPreferences`` per app (vedi il commento in Watchdog.kt).
PREFS_NAME = "jenny"
PREF_PROBE_VALUE = "gatewayGapProbeMs"
PREF_PROBE_AT = "gatewayGapProbeAtMs"
# ``Context.MODE_PRIVATE``. Ricopiato invece di risolvere la costante via
# Chaquopy: è 0 dall'API 1 e non cambierà.
_MODE_PRIVATE = 0

# Oltre un mese non è un buco di attività, è un orologio saltato. Meglio
# perdere il caso limite del telefono spento per cinque settimane che
# annunciare un downtime di dieci anni perché la data era sbagliata al boot.
_MAX_PLAUSIBLE_GAP_MS = 30 * 24 * 60 * 60 * 1000

_STATE_DIR = "state"
_STATE_FILE = "power_gaps.json"


def history_path(workspace_path: Path | str) -> Path:
    """Percorso dello storico dentro il workspace."""
    return Path(workspace_path) / _STATE_DIR / _STATE_FILE


def _empty_history() -> dict[str, Any]:
    return {"version": 1, "gaps": [], "last_probe_at_ms": 0}


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _valid_gap(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    return all(_coerce_int(entry.get(key)) > 0 for key in ("start_ms", "end_ms", "duration_ms"))


def load_history(workspace_path: Path | str) -> dict[str, Any]:
    """Legge lo storico, normalizzato. Mai solleva.

    Un file illeggibile o corrotto vale come storico vuoto: è telemetria, e
    perderla non è un motivo per far fallire l'avvio del gateway o la lettura
    delle impostazioni.
    """
    path = history_path(workspace_path)
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return _empty_history()
    except (OSError, ValueError):
        logger.warning("Unreadable power gap history at {}; starting a new one", path)
        return _empty_history()
    if not isinstance(data, dict):
        return _empty_history()
    raw_gaps = data.get("gaps")
    gaps = [g for g in raw_gaps if _valid_gap(g)][-MAX_GAPS:] if isinstance(raw_gaps, list) else []
    return {
        "version": 1,
        "gaps": gaps,
        "last_probe_at_ms": max(0, _coerce_int(data.get("last_probe_at_ms"))),
    }


def _save_history(workspace_path: Path | str, history: dict[str, Any]) -> None:
    """Scrive lo storico. Mai solleva.

    ``fsync`` sul file sì, sulla directory no: stessa scelta del resto del
    repo su Android (vedi ``snapshot/restore_marker.py``), dove l'fsync di
    directory su FUSE è inaffidabile e comunque tollerato come best-effort.
    """
    try:
        atomic_write(
            history_path(workspace_path),
            json.dumps(history, ensure_ascii=False),
            fsync_dir=False,
        )
    except OSError:
        logger.opt(exception=True).warning("Could not persist the power gap history")


def _plausible(duration_ms: int) -> bool:
    """Una durata credibile: positiva e sotto il tetto di sanità.

    Il ``> 0`` è il filtro sul salto all'indietro dell'orologio: là il "prima"
    risulta successivo al "dopo" e la sottrazione dà un numero negativo, che
    non è un buco corto ma una misura non fatta.
    """
    return 0 < duration_ms <= _MAX_PLAUSIBLE_GAP_MS


def read_probe() -> tuple[int, int] | None:
    """La coppia (battito osservato, istante dell'osservazione) da Kotlin.

    ``None`` fuori da Android, o quando ``Watchdog.takeGapProbe`` non ha ancora
    scattato nessuna fotografia (prima installazione, dati cancellati). Legge le
    ``SharedPreferences`` direttamente dal Context: nessun metodo nuovo sul
    ``PowerBridge``, perché non c'è niente da calcolare lato Kotlin — solo due
    long da leggere.
    """
    context = get_android_context()
    if context is None:
        return None
    try:
        prefs = context.getSharedPreferences(PREFS_NAME, _MODE_PRIVATE)
        value = int(prefs.getLong(PREF_PROBE_VALUE, 0))
        observed_at = int(prefs.getLong(PREF_PROBE_AT, 0))
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Could not read the gap probe from SharedPreferences")
        return None
    if value <= 0 or observed_at <= 0:
        return None
    return value, observed_at


def record_probe(
    workspace_path: Path | str,
    probe: tuple[int, int] | None,
    *,
    threshold_min: int,
) -> dict[str, Any] | None:
    """Valuta una fotografia e, se il buco supera la soglia, lo registra.

    Ritorna il buco registrato o ``None`` (nessuna fotografia, già valutata,
    durata non credibile, o buco sotto soglia). Sincrona e senza dipendenze da
    Android: è qui che vivono le regole, e si testa senza bridge.

    ``last_probe_at_ms`` avanza anche quando non si registra niente. Senza,
    ogni riavvio del gateway rivaluterebbe la stessa fotografia e lo stesso
    buco finirebbe nello storico una volta per riavvio.
    """
    if probe is None:
        return None
    started_ms, ended_ms = probe
    history = load_history(workspace_path)
    if ended_ms <= history["last_probe_at_ms"]:
        return None
    duration_ms = ended_ms - started_ms
    recorded: dict[str, Any] | None = None
    if _plausible(duration_ms) and duration_ms >= max(1, int(threshold_min)) * 60_000:
        recorded = {"start_ms": started_ms, "end_ms": ended_ms, "duration_ms": duration_ms}
        history["gaps"] = (history["gaps"] + [recorded])[-MAX_GAPS:]
        logger.warning(
            "Activity gap of {} min detected (from {} to {}): "
            "the gateway was not running, the device likely killed it",
            duration_ms // 60_000,
            started_ms,
            ended_ms,
        )
    history["last_probe_at_ms"] = ended_ms
    _save_history(workspace_path, history)
    return recorded


def recent_gaps(workspace_path: Path | str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Gli ultimi buchi, dal più recente. Mai solleva."""
    gaps = load_history(workspace_path)["gaps"]
    ordered = list(reversed(gaps))
    return ordered[: max(0, int(limit))]


async def record_startup_gap() -> dict[str, Any] | None:
    """Registra il buco attraversato prima di questo avvio. Mai solleva.

    Chiamata una volta all'avvio del gateway (``GatewayContainer.run``). Fuori
    da Android non c'è nessuna fotografia e la funzione non fa niente: il file
    dello storico non viene nemmeno creato.

    Il config se lo legge da sé, come ``apply_service_lock`` e
    ``apply_watchdog_config`` che la precedono nella stessa sequenza di avvio:
    passarglielo dall'esterno la legava all'unico attributo del container che i
    test costruiscono senza ``__init__``, e la rendeva l'unica delle tre a
    dipendere dal chiamante.

    In un thread perché tocca due volte l'I/O bloccante (JNI verso le
    ``SharedPreferences`` e la scrittura atomica del file) e l'avvio del
    gateway non deve fermarsi ad aspettarla.
    """
    try:
        from jenny.config.loader import load_config

        config = load_config()
        workspace_path = config.workspace_path
        threshold_min = int(getattr(config.power, "gap_warning_min", 60))
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Power gap check skipped: unreadable config")
        return None
    try:
        return await asyncio.to_thread(
            lambda: record_probe(workspace_path, read_probe(), threshold_min=threshold_min)
        )
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning("Power gap check failed")
        return None
