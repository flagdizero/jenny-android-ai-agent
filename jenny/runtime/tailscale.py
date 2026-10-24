"""Tailscale tenuto su da Jenny, finché l'accesso remoto è acceso (solo Android).

Misurato sul Titan ad agosto 2026: Tailscale, una volta ucciso (tipicamente dal
«chiudi tutto» dei Recenti), non torna più su da solo, e la VPN always-on di
quella ROM non lo riporta. Con l'accesso remoto acceso, Jenny senza Tailscale è
irraggiungibile da fuori proprio quando serve: quindi lo riaccende lei, anche
se l'ha spento l'utente (deciso il 24/10/2026: chi vuole Tailscale spento
spegne l'accesso remoto). La politica — quando e quanto spesso — sta in
``channels.ws_remote.RemoteListener``; qui ci sono solo i due gesti:

- **il messaggio** ``com.tailscale.ipn.CONNECT_VPN`` al ricevitore esportato di
  Tailscale (``IPNReceiver``, lo stesso che usano Tasker e simili): invisibile;
- **l'app aperta** per un attimo, e poi di nuovo Jenny davanti: l'unico gesto
  misurato che riporta su il tunnel dopo un kill (~2 s), da usare solo quando
  il messaggio non è bastato.

Chiamate dirette via Chaquopy (``jclass``), senza un bridge Kotlin: sono tre
intent, niente stato da tenere. La visibilità del pacchetto la dà già la
``<queries>`` MAIN/LAUNCHER del manifest. Fuori da Android tutto è un no-op.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from jenny.runtime.context import get_android_context

PACKAGE = "com.tailscale.ipn"
_RECEIVER = "com.tailscale.ipn.IPNReceiver"
_ACTIVITY = "com.tailscale.ipn.MainActivity"
_CONNECT = "com.tailscale.ipn.CONNECT_VPN"
_JENNY_ACTIVITY = "com.flagdizero.jenny.MainActivity"

# Quanto resta davanti l'app di Tailscale prima di tornare a Jenny: il tunnel
# torna su ~2 s dopo che la sua schermata è disegnata.
_BACK_AFTER_S = 3.0


def _intent_class() -> Any:
    from java import jclass  # importabile solo sotto Chaquopy

    return jclass("android.content.Intent")


def installed(context: Any) -> bool:
    try:
        context.getPackageManager().getPackageInfo(PACKAGE, 0)
        return True
    except Exception:
        return False


def _broadcast_connect(context: Any) -> None:
    intent = _intent_class()(_CONNECT)
    intent.setClassName(PACKAGE, _RECEIVER)
    context.sendBroadcast(intent)


def _start(context: Any, package: str, activity: str, *, reorder: bool = False) -> None:
    intent_cls = _intent_class()
    intent = intent_cls()
    intent.setClassName(package, activity)
    flags = intent_cls.FLAG_ACTIVITY_NEW_TASK | intent_cls.FLAG_ACTIVITY_NO_ANIMATION
    if reorder:
        flags |= intent_cls.FLAG_ACTIVITY_REORDER_TO_FRONT
    intent.addFlags(flags)
    context.startActivity(intent)


async def revive(*, open_app: bool) -> bool:
    """Chiede a Tailscale di riconnettersi; con *open_app*, apre anche l'app.

    Vero se il gesto è partito (non se il tunnel è tornato: quello lo vede il
    giro dell'ascoltatore). Non solleva: un tentativo fallito aspetta il prossimo.
    """
    context = get_android_context()
    if context is None:
        return False
    try:
        if not await asyncio.to_thread(installed, context):
            logger.warning("remote access: Tailscale is not installed, cannot bring it back")
            return False
        await asyncio.to_thread(_broadcast_connect, context)
        if not open_app:
            return True
        await asyncio.to_thread(_start, context, PACKAGE, _ACTIVITY)
        await asyncio.sleep(_BACK_AFTER_S)
        # Di nuovo Jenny davanti. Con Jenny home, ``MainActivity.redirectToHome``
        # passa la mano al task della home invece di farne un secondo.
        await asyncio.to_thread(
            _start, context, context.getPackageName(), _JENNY_ACTIVITY, reorder=True
        )
        return True
    except Exception:
        logger.opt(exception=True).warning("remote access: could not ask Tailscale to reconnect")
        return False
