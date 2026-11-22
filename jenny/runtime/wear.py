"""Orologio: il lato Python della risposta che torna al polso.

Uscita verso ``WearBridge``, come ``jenny/runtime/floating.py`` lo è verso
``FloatingBridge``, e con la stessa divisione dei compiti: **qui si decide cosa
dire, in Kotlin come farlo arrivare**. Il Data Layer — il nodo, il percorso del
messaggio, la versione del protocollo — è una faccenda del lato Kotlin, e vive
solo nel flavor ``play`` perché è codice Google.

Nella ``foss`` la classe ``WearBridge`` non esiste: ``BridgeCache`` non la
risolve e ogni chiamata torna ``False``. Non è un guasto da raccontare — in
quella build nessuna domanda può essere arrivata dal polso, quindi non c'è
nessuna risposta da riportarci — e per questo il livello del log è ``DEBUG``.

Fuori da Android ogni chiamata è un no-op silenzioso, come nel notifier.
"""

from __future__ import annotations

from typing import Any

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.WearBridge")

# Stesso tetto degli altri bridge, e per la stessa ragione: la chiamata
# attraversa Chaquopy e può restare bloccata quanto il GIL resta preso. Dentro,
# Kotlin aspetta il Data Layer per meno di così (``WearBridge.SEND_TIMEOUT_MS``).
_BRIDGE_TIMEOUT_S = 10


def reset_wear_state() -> None:
    """Butta il bridge in cache a un nuovo start del gateway.

    Gemello di ``floating.reset_floating_state``, chiamato dallo stesso posto
    (``android_entry.run_gateway``): il bridge cachato punta al contesto del
    giro precedente, e il lock dentro ``BridgeCache`` è legato a un loop morto.
    """
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin via Chaquopy. Il seam dei test, come nel
    notifier e nella mascotte."""
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Costruisce o ritorna il ``WearBridge`` cachato (thread-safe)."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


async def send_reply(text: str, address: str | None) -> bool:
    """Manda *text* all'orologio che ha fatto la domanda. ``False`` se non è partito.

    *address* è il *thread* con cui la domanda è entrata (v.
    ``native_input.SOURCE_WEAR``): dice a quale orologio e a quale domanda
    risponde. Senza, non c'è un posto dove mandare la risposta, e non si
    indovina — la risposta è comunque nella conversazione.

    ``False`` è un esito normale: l'orologio fuori portata, l'app sul polso
    chiusa da un pezzo, la build ``foss``. Mai solleva: è il motivo per cui il
    canale resta ``send_max_retries = 1``.
    """
    clean = (text or "").strip()
    if not clean or not address:
        return False
    return bool(await _BRIDGE.call(
        _get_bridge, get_android_context(), "sendReply", address, clean,
        timeout=_BRIDGE_TIMEOUT_S, default=False, level="DEBUG",
    ))
