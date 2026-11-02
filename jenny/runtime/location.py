"""Posizione del dispositivo (solo Android), via ``LocationBridge`` nativo.

Stesso pattern di ``jenny/runtime/notifier.py``: la classe Kotlin è esposta a
Python via Chaquopy (``jclass``), istanziata una volta e cachata; fuori da
Android tutto degrada a no-op senza sollevare.

Modello a due livelli (deciso col design, niente refresh periodico):

* **last-known iniettato a ogni turno** — ``location_runtime_line`` legge una
  cache (``_CURRENT``) in modo istantaneo e sincrono, e pianifica un refresh
  fire-and-forget per il turno successivo (come ``notifier.notify_delivery``).
  Così ``ContextBuilder.build_messages`` resta sincrono e non tocca mai il JNI
  sul percorso caldo. Il fix last-known è gratis (fused, già in cache dell'OS).
* **fix fresco on-demand** — il tool ``get_location`` con ``precise=true`` accende
  il radio GPS una volta sola, quando serve davvero precisione.

La posizione condivisa via Telegram (``record_telegram_location``) fa da
override *per-canale* con validità ``telegram_ttl_s``: entro il TTL le risposte
del canale Telegram la usano come "posizione attuale", oltre il TTL anche
Telegram ricade sul GPS live.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.LocationBridge")

# Ultimo fix GPS noto (aggiornato dal refresh fire-and-forget). Letto in modo
# sincrono da ``location_runtime_line``.
_CURRENT: "LocationFix | None" = None

# Override per-canale Telegram: chat_id -> fix condiviso dall'utente.
_TELEGRAM: dict[str, "LocationFix"] = {}

# Cache del reverse-geocoding: chiave = coordinate arrotondate, valore = place.
# Evita di ri-geocodificare a ogni refresh quando l'utente non si muove.
_GEOCODE_CACHE: dict[tuple[float, float], str] = {}
_GEOCODE_CACHE_MAX = 64

# Task fire-and-forget tenuti referenziati (asyncio tiene solo weakref).
_TASKS: set[asyncio.Task[Any]] = set()
# Evita di accodare più refresh in parallelo sui turni ravvicinati.
_REFRESH_INFLIGHT = False

_GEOCODE_PRECISION = 4  # ~11 m di risoluzione sulla chiave di cache

# Il tetto di una chiamata al bridge che non accende il GPS (last-known, geocoder):
# lo stesso degli altri bridge, perché un ``Geocoder`` che non risponde non deve
# tenere fermo il turno che ha chiesto la posizione.
_BRIDGE_TIMEOUT_S = 10


@dataclass(frozen=True)
class LocationFix:
    """Uno snapshot di posizione, sorgente-agnostico."""

    latitude: float
    longitude: float
    accuracy_m: float | None
    # Epoch millisecondi del fix (device time), confrontabile con time.time()*1000.
    time_ms: int
    place: str | None = None
    source: str = "gps"

    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.time_ms / 1000.0)


def reset_location_state() -> None:
    """Azzera la cache del bridge e lo stato di modulo a un nuovo start del
    gateway. Simmetrico a ``notifier.reset_notifier_state`` — chiamato da
    ``android_entry.run_gateway`` prima del nuovo event loop (una ``asyncio.Lock``
    si lega al loop su cui è awaitata la prima volta).
    """
    global _CURRENT, _REFRESH_INFLIGHT
    _BRIDGE.reset()
    _CURRENT = None
    _REFRESH_INFLIGHT = False
    _TELEGRAM.clear()
    _GEOCODE_CACHE.clear()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``LocationBridge`` via Chaquopy.

    Resta una funzione di modulo, e non una chiamata diretta a
    ``_BRIDGE.resolve_class``: è il seam su cui i test montano il finto
    bridge fuori dal telefono.
    """
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Costruisce o ritorna l'istanza cachata di ``LocationBridge`` (thread-safe)."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


def _parse_fix(raw: Any, *, source: str) -> LocationFix | None:
    """Parsa la stringa del bridge ``"lat;lng;accuracy;timeMillis;provider"``.

    Il bridge ritorna ``None``/stringa vuota quando manca il permesso o non c'è
    alcun fix; qualunque formato inatteso viene trattato come assenza (mai
    un'eccezione: la posizione è best-effort).
    """
    if not raw:
        return None
    parts = str(raw).split(";")
    if len(parts) < 4:
        return None
    try:
        lat = float(parts[0])
        lng = float(parts[1])
        accuracy = float(parts[2]) if parts[2] not in ("", "null") else None
        time_ms = int(float(parts[3]))
    except (ValueError, IndexError):
        return None
    provider = parts[4] if len(parts) > 4 and parts[4] else source
    return LocationFix(
        latitude=lat,
        longitude=lng,
        accuracy_m=accuracy,
        time_ms=time_ms,
        source=provider,
    )


async def _reverse_geocode(context: Any, lat: float, lng: float) -> str | None:
    """Reverse-geocoding via ``Geocoder`` nativo, con cache sulle coordinate
    arrotondate. Best-effort: ritorna ``None`` su qualunque errore (non in cache)."""
    key = (round(lat, _GEOCODE_PRECISION), round(lng, _GEOCODE_PRECISION))
    cached = _GEOCODE_CACHE.get(key)
    if cached is not None:
        return cached or None
    place = await _BRIDGE.call(_get_bridge, context, "reverseGeocode", lat, lng,
                               timeout=_BRIDGE_TIMEOUT_S, level="DEBUG")
    if place is None:
        return None
    place = str(place).strip()
    if len(_GEOCODE_CACHE) >= _GEOCODE_CACHE_MAX:
        _GEOCODE_CACHE.clear()
    _GEOCODE_CACHE[key] = place
    return place or None


async def _fetch_fix(cfg: Any, *, precise: bool) -> LocationFix | None:
    """Legge un fix dal bridge (last-known o fresco) e lo reverse-geocoda.

    Ritorna ``None`` fuori da Android, senza permesso, o su qualunque errore.
    """
    context = get_android_context()
    if precise:
        timeout = getattr(cfg, "fresh_timeout_s", 15)
        raw = await _BRIDGE.call(_get_bridge, context, "getFresh", int(timeout * 1000),
                                 timeout=timeout + 5, level="DEBUG")
        source = "gps"
    else:
        raw = await _BRIDGE.call(_get_bridge, context, "getLastKnown",
                                 timeout=_BRIDGE_TIMEOUT_S, level="DEBUG")
        source = "last-known"
    fix = _parse_fix(raw, source=source)
    if fix is None:
        return None
    place = await _reverse_geocode(context, fix.latitude, fix.longitude)
    if place:
        fix = LocationFix(
            latitude=fix.latitude,
            longitude=fix.longitude,
            accuracy_m=fix.accuracy_m,
            time_ms=fix.time_ms,
            place=place,
            source=fix.source,
        )
    return fix


async def refresh_current_location(cfg: Any) -> None:
    """Aggiorna la cache ``_CURRENT`` col last-known. Mai solleva."""
    global _CURRENT, _REFRESH_INFLIGHT
    try:
        if cfg is not None and getattr(cfg, "enable", True):
            fix = await _fetch_fix(cfg, precise=False)
            if fix is not None:
                _CURRENT = fix
    finally:
        _REFRESH_INFLIGHT = False


def _schedule_refresh(cfg: Any) -> None:
    """Pianifica (fire-and-forget) un refresh del last-known per il turno dopo.

    No-op fuori da Android, senza event loop, o se un refresh è già in volo.
    """
    global _REFRESH_INFLIGHT
    if _REFRESH_INFLIGHT or get_android_context() is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _REFRESH_INFLIGHT = True
    task = loop.create_task(refresh_current_location(cfg))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _humanize_age(seconds: float) -> str:
    """Età leggibile del fix per la riga di runtime context (inglese, come le
    altre etichette del blocco: ``Current Time``, ``Channel``…)."""
    if seconds < 90:
        return "just now"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"~{round(minutes)} min ago"
    hours = minutes / 60.0
    if hours < 36:
        return f"~{round(hours)} h ago"
    return f"~{round(hours / 24.0)} d ago"


def _format_line(fix: LocationFix, *, shared: bool = False) -> str:
    """Costruisce la riga da iniettare nel runtime context."""
    where = fix.place or f"{fix.latitude:.5f}, {fix.longitude:.5f}"
    coords = f"({fix.latitude:.5f}, {fix.longitude:.5f})"
    if shared:
        return f"User location (shared via Telegram): {where} {coords}"
    return f"Device location ({_humanize_age(fix.age_seconds())}): {where} {coords}"


def record_telegram_location(chat_id: str, fix: LocationFix) -> None:
    """Registra la posizione condivisa via Telegram come override del canale."""
    if not chat_id:
        return
    _TELEGRAM[chat_id] = fix


async def build_telegram_fix(cfg: Any, lat: float, lng: float) -> LocationFix:
    """Costruisce un ``LocationFix`` da coordinate Telegram, reverse-geocodando
    se possibile. Timestamp = adesso (l'utente l'ha appena condivisa)."""
    now_ms = int(time.time() * 1000)
    place: str | None = None
    context = get_android_context()
    if context is not None and cfg is not None and getattr(cfg, "enable", True):
        place = await _reverse_geocode(context, lat, lng)
    return LocationFix(
        latitude=lat,
        longitude=lng,
        accuracy_m=None,
        time_ms=now_ms,
        place=place,
        source="telegram",
    )


def location_runtime_line(channel: str | None, chat_id: str | None, cfg: Any) -> str | None:
    """Riga posizione per il runtime context del turno (pura + istantanea).

    Legge la cache e pianifica un refresh per il prossimo turno. Ritorna ``None``
    quando il toggle è off o non c'è alcun fix disponibile.
    """
    if cfg is None or not getattr(cfg, "enable", True):
        return None
    _schedule_refresh(cfg)

    # Override Telegram: valido solo entro il TTL e solo per il canale Telegram.
    if channel == "telegram" and chat_id:
        shared = _TELEGRAM.get(chat_id)
        if shared is not None:
            ttl = getattr(cfg, "telegram_ttl_s", 3600)
            if shared.age_seconds() <= ttl:
                return _format_line(shared, shared=True)
            _TELEGRAM.pop(chat_id, None)

    if _CURRENT is not None:
        return _format_line(_CURRENT)
    return None


def cached_location(cfg: Any) -> LocationFix | None:
    """L'ultimo fix in cache, senza chiedere niente al GPS: lo leggono le schede
    dei posti in chat (``/api/location/last``) per «sei qui» e «1,2 km da te».

    Col toggle spento non c'è, come per la riga del runtime context. Una cache
    vuota pianifica un giro di last-known (gratis) per la lettura dopo.
    """
    if cfg is None or not getattr(cfg, "enable", True):
        return None
    if _CURRENT is None:
        _schedule_refresh(cfg)
    return _CURRENT


@dataclass(frozen=True)
class PlaceMatch:
    """Un posto trovato per nome dal ``Geocoder`` di Android."""

    name: str
    address: str
    latitude: float
    longitude: float


# Il riquadro attorno al telefono in cui si cerca per primo: ~50 km per lato,
# che e' «qui vicino» per una città e il suo intorno. Fuori non si rinuncia: se
# nel riquadro non c'è niente si cerca ovunque.
_NEAR_BOX_DEG = 0.45
_FIND_MAX_RESULTS = 5


def _parse_matches(raw: Any) -> list[PlaceMatch]:
    """Parsa la risposta di ``LocationBridge.findPlaces``: un JSON
    ``[{"name","address","lat","lng"}]``. Qualunque formato inatteso è una
    lista vuota, mai un'eccezione."""
    if not raw:
        return []
    try:
        items = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    out: list[PlaceMatch] = []
    for item in items if isinstance(items, list) else []:
        try:
            lat = float(item["lat"])
            lng = float(item["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        out.append(
            PlaceMatch(
                name=str(item.get("name") or "").strip(),
                address=str(item.get("address") or "").strip(),
                latitude=lat,
                longitude=lng,
            )
        )
    return out


def geocoder_available() -> bool:
    """Se il telefono sa cercare un posto per nome (``Geocoder.isPresent()``).

    Su un telefono senza i servizi Google può mancare: allora ``find_place``
    non si registra e Jenny scrive il link con nome e indirizzo.
    """
    if get_android_context() is None:
        return False
    try:
        return bool(_resolve_bridge_class().isGeocoderPresent())
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Geocoder presence check failed")
        return False


async def find_places(query: str, near: LocationFix | None = None) -> list[PlaceMatch]:
    """Cerca *query* (nome e indirizzo) col ``Geocoder`` nativo.

    Con *near* cerca prima nel riquadro attorno a quel punto, e solo se lì non
    trova niente ovunque. Non serve il permesso di posizione: cercare un
    indirizzo non lo richiede.
    """
    context = get_android_context()
    if context is None or not query.strip():
        return []
    attempts: list[tuple[float, float, float, float]] = []
    if near is not None:
        attempts.append(
            (
                near.latitude - _NEAR_BOX_DEG,
                near.longitude - _NEAR_BOX_DEG,
                near.latitude + _NEAR_BOX_DEG,
                near.longitude + _NEAR_BOX_DEG,
            )
        )
    attempts.append((0.0, 0.0, 0.0, 0.0))  # tutto zero = nessun riquadro
    for box in attempts:
        raw = await _BRIDGE.call(_get_bridge, context, "findPlaces", query.strip(),
                                 _FIND_MAX_RESULTS, *box, timeout=_BRIDGE_TIMEOUT_S,
                                 default=False, level="DEBUG")
        if raw is False:  # il bridge ha fallito: un secondo giro fallirebbe uguale
            return []
        matches = _parse_matches(raw)
        if matches:
            return matches
    return []


async def get_location(cfg: Any, *, precise: bool) -> LocationFix | None:
    """Punto d'ingresso del tool on-demand.

    ``precise=False`` → last-known (dalla cache se fresca, altrimenti un giro di
    refresh); ``precise=True`` → fix fresco (accende il GPS una volta)."""
    if cfg is None or not getattr(cfg, "enable", True):
        return None
    global _CURRENT
    if precise:
        fix = await _fetch_fix(cfg, precise=True)
        # Un fix fresco è anche il migliore che si conosca: senza questo la riga
        # del runtime context e la distanza delle schede in chat restavano sul
        # last-known di prima (sull'emulatore, Mountain View di un'ora fa).
        if fix is not None and (_CURRENT is None or fix.time_ms >= _CURRENT.time_ms):
            _CURRENT = fix
        return fix
    if _CURRENT is not None:
        return _CURRENT
    return await _fetch_fix(cfg, precise=False)
