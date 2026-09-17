"""Mascotte flottante: il lato Python della finestra che sta sopra le altre app.

Uscita verso `FloatingBridge`, come `jenny/runtime/notifier.py` lo è verso
`NotifierBridge`, e con la stessa divisione dei compiti: **qui si decide la
volontà — se la mascotte esiste e cosa deve dire — e in Kotlin la finestra**.
Nessuna geometria passa di qua: dove sta parcheggiata, quanto è grande e quando
si toglie di mezzo perché l'app è davanti sono domande sulla finestra, e vivono
dove la finestra vive.

Due sole cose attraversano il confine:

* ``apply_floating_config()`` all'avvio del gateway — accendi o spegni, letto da
  ``config.floating.enabled``;
* ``show_reply()`` a ogni risposta — il testo da disegnare nel fumetto, chiamato
  da ``jenny/channels/floating.py``.

Fuori da Android ogni chiamata è un no-op silenzioso, come nel notifier: il
contesto non c'è, non c'è nessuna finestra, e non è un errore.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.FloatingBridge")

# Stesso tetto del notifier, e per la stessa ragione: la chiamata attraversa
# Chaquopy e può restare bloccata quanto il GIL resta preso. Un fumetto che non
# si disegna è un peccato; un canale bloccato è un turno che non finisce.
_BRIDGE_TIMEOUT_S = 10


def reset_floating_state() -> None:
    """Butta il bridge in cache a un nuovo start del gateway.

    Simmetrico a ``notifier.reset_notifier_state``, e chiamato dallo stesso
    posto (``android_entry.run_gateway``): il bridge cachato punta al contesto
    del giro precedente, e il lock dentro ``BridgeCache`` è legato a un loop
    morto.
    """
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin via Chaquopy.

    Resta una funzione di modulo invece di una chiamata diretta a
    ``_BRIDGE.resolve_class``: è il seam che i test sostituiscono per stare in
    piedi fuori dal telefono, esattamente come fa il notifier.
    """
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Costruisce o ritorna il ``FloatingBridge`` cachato (thread-safe)."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


async def _call(method: str, *args: Any) -> bool:
    """Chiama un metodo del bridge su un thread, con timeout. Mai solleva.

    ``asyncio.to_thread`` e non una chiamata diretta: siamo sul loop del
    gateway e l'attraversamento Chaquopy è bloccante. È la stessa disciplina di
    ``notifier.post_alert``, e il motivo per cui il canale può restare
    ``send_max_retries = 1``: qui dentro non esce mai un'eccezione, quindi non
    c'è niente che il dispatcher possa ritentare.
    """
    context = get_android_context()
    if context is None:
        return False
    try:
        bridge = await _get_bridge(context)
        result = await asyncio.wait_for(
            asyncio.to_thread(getattr(bridge, method), *args), timeout=_BRIDGE_TIMEOUT_S
        )
    except Exception:
        logger.opt(exception=True).warning("FloatingBridge.{} failed", method)
        return False
    return bool(result)


async def show_reply(text: str) -> bool:
    """Disegna *text* nel fumetto. ``False`` se non è arrivato a schermo.

    ``False`` è un esito normale e non un guasto: la mascotte può essere spenta
    in config, il permesso può mancare, l'app può essere in primo piano — in
    tutti quei casi la risposta è comunque nella conversazione, che è dove vive
    davvero. Chi chiama logga e tira dritto.
    """
    clean = (text or "").strip()
    if not clean:
        return False
    return await _call("showReply", clean)


async def apply_floating_config() -> bool:
    """Spinge ``config.floating.enabled`` al bridge. Da chiamare all'avvio.

    Va spinto **anche quando è spento**, e non è ridondanza: la finestra vive
    nel processo del ``GatewayService``, che sopravvive a un riavvio del gateway
    (v. ``GatewayService.runGatewayUntilGivenUp``). Un ``False`` esplicito è
    l'unica cosa che smonta una mascotte rimasta a schermo da un giro in cui il
    flag era acceso. Senza, spegnerla dalle impostazioni non farebbe nulla fino
    al riavvio dell'app — che è il difetto che questa riga esiste per evitare.
    """
    if get_android_context() is None:
        return False
    from jenny.config.loader import load_config

    try:
        section = load_config().floating
        enabled = bool(section.enabled)
        hold_s = int(section.reply_hold_s)
    except Exception:
        logger.opt(exception=True).warning("Could not read floating config; overlay left alone")
        return False
    ok = await _call("setEnabled", enabled, hold_s)
    logger.info("Floating mascot: enabled={} hold={}s applied={}", enabled, hold_s, ok)
    return ok
