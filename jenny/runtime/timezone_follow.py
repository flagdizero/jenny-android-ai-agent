"""Il telefono cambia fuso: Kotlin lo dice, il gateway lo segue.

Il fuso si rilevava una volta sola, all'avvio (``android_entry.run_gateway``),
e il gateway è un servizio che resta su per giorni: atterrati in un altro
paese, «ogni giorno alle 7» suonava ancora alle 7 di casa. Android manda
``ACTION_TIMEZONE_CHANGED``; ``GatewayService`` la gira qui, e da qui parte
:meth:`jenny.runtime.container.GatewayContainer.follow_timezone`.

Stessa disciplina di ``power.on_wake_tick``: Kotlin entra da un thread JNI, e
l'unica cosa lecita da lì è ``call_soon_threadsafe`` sul loop del gateway.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from loguru import logger

_LOOP: asyncio.AbstractEventLoop | None = None
_ON_CHANGE: Callable[[str | None], None] | None = None


def bind(on_change: Callable[[str | None], None]) -> None:
    """Aggancia il loop corrente: da chiamare **dal** loop del gateway."""
    global _LOOP, _ON_CHANGE
    _LOOP = asyncio.get_running_loop()
    _ON_CHANGE = on_change


def reset_timezone_follow() -> None:
    """Stacca il loop: allo spegnimento del gateway, e prima di ogni suo riavvio.

    Un secondo ``asyncio.run`` non può ereditare il loop del primo
    (``android_entry._reset_loop_bound_state``).
    """
    global _LOOP, _ON_CHANGE
    _LOOP = None
    _ON_CHANGE = None


def on_timezone_changed(tz_id: str | None = None) -> bool:
    """Il fuso del telefono è cambiato: chiamata da Kotlin, da un thread di lavoro.

    *tz_id* è il nome IANA che porta l'intent (``Intent.EXTRA_TIMEZONE``); può
    mancare, e allora lo rilegge chi riceve. ``False`` — mai un'eccezione verso
    Kotlin — quando non c'è un gateway agganciato: non si perde niente, perché
    l'avvio rilegge il fuso e ``CronService.start`` lo confronta con quello in
    cui i job sono scritti.
    """
    loop, on_change = _LOOP, _ON_CHANGE
    if loop is None or on_change is None or loop.is_closed():
        return False
    try:
        loop.call_soon_threadsafe(on_change, str(tz_id or "").strip() or None)
    except RuntimeError:
        logger.opt(exception=True).debug("Timezone change could not reach the event loop")
        return False
    return True
