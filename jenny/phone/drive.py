"""La guida: Jenny sta usando il telefono, e si vede.

Dal primo ``phone_do`` di un turno alla sua fine il telefono ha un guidatore
che non è l'utente. Questo modulo tiene quel fatto e lo mostra: la pillola
sopra l'app (``PhonePill`` in Kotlin) e una notifica, tutte e due con «Ferma».

* :func:`begin` — la chiama ``phone_do`` prima di agire: chiude la chat
  flottante (con quella aperta l'app sotto non si legge), mostra pillola e
  notifica. Le chiamate dopo, nello stesso turno, aggiornano solo il testo.
* :func:`end_turn` — la chiama il loop a turno finito: toglie la notifica e,
  secondo da dove era partita la richiesta, riporta davanti la chat dell'app
  o lascia l'esito sulla pillola.
* :func:`stop` — «Ferma», dalla pillola o dalla notifica: i passi non ancora
  fatti non si fanno, le domande in attesa diventano un no, e il turno si
  ferma per la stessa strada di ``/stop``.

Lo stato vive in memoria: è la guida di un turno vivo, e un gateway che riparte
non ha niente da riprendere.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from jenny.phone import consent
from jenny.runtime import phone as runtime

if TYPE_CHECKING:
    from jenny.agent.tools.context import RequestContext

#: Per quanto, dopo la fine di una guida, la risposta finale va sulla pillola
#: se la chat flottante è chiusa.
RESULT_WINDOW_S = 60.0


@dataclass
class Drive:
    ident: str
    turn: str
    origin: str
    session_key: str
    channel: str
    chat_id: str


_DRIVES: dict[str, Drive] = {}
_ENDED: tuple[str, float] | None = None

# Chi ferma davvero il turno: lo registra chi costruisce il gateway.
_STOPPER: Callable[[Drive], Awaitable[None]] | None = None
# Notifica «Jenny sta usando il telefono» e la sua rimozione.
_NOTICE: Callable[[str, str], None] | None = None
_CLEAR: Callable[[str], None] | None = None


def install(
    *,
    stopper: Callable[[Drive], Awaitable[None]] | None,
    notice: Callable[[str, str], None] | None,
    clear: Callable[[str], None] | None,
) -> None:
    """Lega la guida a chi ferma un turno e alla notifica. ``None`` stacca."""
    global _STOPPER, _NOTICE, _CLEAR, _ENDED
    _STOPPER, _NOTICE, _CLEAR = stopper, notice, clear
    _DRIVES.clear()
    _ENDED = None


def _turn_key(ctx: RequestContext | None) -> str:
    from jenny.agent.tools.context import current_turn_id

    return (ctx.turn_id if ctx else None) or current_turn_id() or (ctx.session_key if ctx else "") or ""


def active() -> list[Drive]:
    return list(_DRIVES.values())


async def begin(ctx: RequestContext | None, goal: str) -> Drive:
    """Comincia (o continua) la guida del turno di *ctx*, e la mostra."""
    key = _turn_key(ctx)
    drive = _DRIVES.get(key)
    text = " ".join(goal.split())[:120] or "…"
    if drive is None:
        drive = _DRIVES[key] = Drive(
            ident=secrets.token_hex(16), turn=key, origin=consent.origin_of(ctx),
            session_key=(ctx.session_key or "") if ctx else "",
            channel=ctx.channel if ctx else "", chat_id=ctx.chat_id if ctx else "",
        )
        # Con la chat flottante aperta l'app sotto non esiste per il servizio.
        from jenny.runtime import floating

        await floating.collapse()
        if _NOTICE is not None:
            try:
                _NOTICE(drive.ident, text)
            except Exception:  # noqa: BLE001 — la notifica non ferma la guida
                logger.opt(exception=True).debug("phone: driving notice failed")
    await runtime.pill_driving(drive.ident, text)
    return drive


async def end_turn(ctx: RequestContext | None) -> None:
    """Il turno è finito: se guidava, si smette di mostrarlo. Mai solleva."""
    global _ENDED
    try:
        drive = _DRIVES.pop(_turn_key(ctx), None)
        if drive is None:
            return
        if _CLEAR is not None:
            _CLEAR(drive.ident)
        _ENDED = (drive.origin, time.monotonic())
        await runtime.pill_done(None)
        # Se lo schermo l'aveva acceso Jenny, lo rispegne.
        await runtime.sleep_again()
        if drive.origin == "app":
            # Era partita dalla chat dell'app: la risposta è lì.
            await runtime.open_chat()
    except Exception:  # noqa: BLE001 — la fine di un turno non deve cadere qui
        logger.opt(exception=True).warning("phone: could not close the driving state")


async def show_result(text: str) -> bool:
    """La risposta finale sulla pillola, se una guida partita dalla mascotte è
    appena finita. La usa il canale della mascotte quando la chat flottante è
    chiusa: senza, l'esito finirebbe solo nella chat dell'app."""
    global _ENDED
    ended = _ENDED
    if ended is None or ended[0] != "floating":
        return False
    if time.monotonic() - ended[1] > RESULT_WINDOW_S:
        return False
    _ENDED = None
    return await runtime.pill_done(" ".join(text.split())[:320])


async def stop(ident: str) -> bool:
    """«Ferma» per la guida *ident*. Falso se non è una guida in corso."""
    drive = next((d for d in _DRIVES.values() if d.ident == ident), None)
    if drive is None:
        return False
    logger.info("phone: the user pressed Stop")
    await runtime.stop()
    pending = consent.asks()
    if pending is not None:
        pending.deny_all()
    if _STOPPER is not None:
        try:
            await _STOPPER(drive)
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).warning("phone: could not stop the turn")
    return True
