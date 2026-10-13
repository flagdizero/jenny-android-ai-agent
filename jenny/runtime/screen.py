"""Lettura dello schermo: il lato Python degli occhi di Jenny.

Rovescio di ``ScreenSnapshots``/``ScreenBridge`` in Kotlin, con la stessa
divisione dei compiti di ``runtime/floating.py``: **la lettura la fa Kotlin**,
al tocco sulla mascotte flottante; qui arriva il riassunto di quella lettura
insieme al testo del fumetto, e da qui il tool ``screen`` ne chiede il contenuto.

Il percorso, una volta sola:

1. il tocco che apre la chat flottante fa partire ``ScreenSnapshots.capture``;
2. ``GatewayService.deliverFloatingText`` passa il riassunto della lettura
   (JSON: id, app, titolo, indirizzo…) a ``native_input.on_native_text``, che lo
   mette nei metadata del messaggio sotto :data:`SCREEN_META_KEY`;
3. il loop, per quel turno, aggiunge una riga al runtime context
   (:func:`screen_runtime_lines`) e mostra il tool ``screen``
   (:func:`has_screen`); in ogni altro turno il tool non c'è;
4. il tool chiede a ``ScreenBridge`` il contenuto per **id**: se un tocco più
   recente ha preso un'altra lettura, o sono passati dieci minuti, la risposta
   è vuota e il tool lo dice.

Fuori da Android ogni chiamata al bridge è un no-op che risponde «niente».
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

# Chiave dei metadata dell'``InboundMessage`` con il riassunto della lettura.
# Col trattino basso davanti come le altre chiavi interne (``_native_source``):
# è informazione per il loop e per il tool, non per il canale.
SCREEN_META_KEY = "_screen"

# I campi del riassunto che Kotlin manda (``Snapshot.summaryJson``) e che si
# tengono. Elenco chiuso: un campo nuovo dall'altra parte non entra nei metadata
# del turno finché qualcuno non decide qui cosa farne.
_SUMMARY_FIELDS = ("id", "takenAt", "app", "package", "title", "url", "secure",
                   "chars", "images", "screenshot")

_BRIDGE = BridgeCache("com.flagdizero.jenny.ScreenBridge")

# Stesso tetto di ``floating._BRIDGE_TIMEOUT_S``: scrivere uno screenshot PNG
# costa qualche centinaio di millisecondi, il resto è istantaneo.
_BRIDGE_TIMEOUT_S = 10


def reset_screen_state() -> None:
    """Butta il bridge in cache a un nuovo start del gateway (v. ``floating``)."""
    _BRIDGE.reset()


def parse_summary(raw: str | None) -> dict[str, Any] | None:
    """Il riassunto arrivato da Kotlin, ridotto ai campi noti; ``None`` se manca
    o non si legge. Mai solleva: un riassunto rotto non deve far perdere il
    testo dell'utente che lo accompagna."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Screen summary unreadable; message delivered without it")
        return None
    if isinstance(data, dict) and data.get("off") is True:
        # Il servizio è spento: niente da leggere, ma Jenny deve saperlo per
        # dire dove si accende (``ScreenSnapshots.offSummary``).
        return {"off": True}
    if isinstance(data, dict) and data.get("expired") is True:
        # La lettura c'era ed è scaduta: va ripresa con un tocco, non accesa.
        return {"expired": True}
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
        return None
    return {key: data[key] for key in _SUMMARY_FIELDS if key in data}


def screen_summary(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Il riassunto della lettura legato a questo messaggio, se c'è."""
    summary = (metadata or {}).get(SCREEN_META_KEY)
    return summary if isinstance(summary, dict) and summary.get("id") else None


def screen_off(metadata: Mapping[str, Any] | None) -> bool:
    """Il turno nasce dal fumetto con il servizio di lettura spento?"""
    summary = (metadata or {}).get(SCREEN_META_KEY)
    return isinstance(summary, dict) and summary.get("off") is True


def screen_expired(metadata: Mapping[str, Any] | None) -> bool:
    """Il turno nasce dal fumetto con una lettura scaduta?"""
    summary = (metadata or {}).get(SCREEN_META_KEY)
    return isinstance(summary, dict) and summary.get("expired") is True


def has_screen(metadata: Mapping[str, Any] | None) -> bool:
    """Il turno nasce dal fumetto con una lettura dello schermo?"""
    return screen_summary(metadata) is not None


def screen_runtime_lines(metadata: Mapping[str, Any] | None) -> list[str]:
    """La riga del runtime context che dice a Jenny cosa c'era sullo schermo.

    Solo i dati che servono a decidere se leggere: l'app, il titolo e
    l'indirizzo. Il contenuto no — costa token in ogni turno del fumetto, anche
    quando la domanda non c'entra niente con lo schermo, ed è testo non fidato:
    entra solo se Jenny lo chiede, e allora il turno si segna.
    """
    if screen_off(metadata):
        return [
            "Screen: the user wrote from the floating bubble, but reading the screen is "
            "turned off. If they ask about what is on it, say it can be turned on from "
            "Settings → Jenny → Reads the screen."
        ]
    if screen_expired(metadata):
        return [
            "Screen: the user wrote from the floating bubble, but the reading taken when "
            "it opened is more than ten minutes old and was discarded. If they ask about "
            "what is on the screen, ask them to close the bubble and tap the mascot again."
        ]
    summary = screen_summary(metadata)
    if summary is None:
        return []
    app = str(summary.get("app") or summary.get("package") or "an app")
    if summary.get("secure"):
        return [
            f"Screen: the user wrote from the floating bubble over {app}, which blocks "
            "screenshots (banking, passwords, incognito), so it was not read."
        ]
    parts = [f"Screen: the user wrote from the floating bubble over {app}"]
    if title := summary.get("title"):
        parts.append(f'showing "{title}"')
    if url := summary.get("url"):
        parts.append(f"({url})")
    return [" ".join(parts) + ". The `screen` tool reads what was on it, if the request is about it."]


def _resolve_bridge_class() -> Any:
    """Seam per i test, come ``floating._resolve_bridge_class``."""
    return _BRIDGE.resolve_class()


async def _call(method: str, *args: Any) -> Any:
    """Chiama un metodo del bridge su un thread, con timeout. Mai solleva:
    ``None`` se Android non c'è o la chiamata fallisce."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _BRIDGE.get(context, resolve=_resolve_bridge_class)
        return await asyncio.wait_for(
            asyncio.to_thread(getattr(bridge, method), *args), timeout=_BRIDGE_TIMEOUT_S
        )
    except Exception:
        logger.opt(exception=True).warning("ScreenBridge.{} failed", method)
        return None


async def screen_state() -> str:
    """``"on"``, ``"off"``, ``"unsupported"`` o ``"unavailable"`` (niente Android)."""
    state = await _call("state")
    return str(state) if state else "unavailable"


async def read_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    """Testo, indirizzo e immagini della lettura *snapshot_id*, o ``None`` se
    non è più quella corrente."""
    raw = await _call("read", snapshot_id)
    if not raw:
        return None
    try:
        data = json.loads(str(raw))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def write_screenshot(snapshot_id: str, path: str) -> bool:
    return bool(await _call("writeScreenshot", snapshot_id, path))


async def write_image(snapshot_id: str, n: int, path: str) -> bool:
    return bool(await _call("writeImage", snapshot_id, int(n), path))


async def open_screen_settings() -> bool:
    """Apre Impostazioni → Accessibilità, dove il servizio si accende."""
    return bool(await _call("openSettings"))
