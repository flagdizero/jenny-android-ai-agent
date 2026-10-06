"""«I dati di un'app sono cambiati»: il filo che lo porta alla WebUI.

L'avviso (``app_data_changed`` sul WebSocket) lo ascoltano lo schermo aperto
dell'app, che si rilegge, e la bacheca, che ridisegna la polaroid col valore
nuovo. Lo mandava solo il tool di un'app quando scriveva Jenny: una scrittura
dallo schermo, o un lettore che ricalcola, non lo diceva a nessuno, e la
polaroid restava indietro finché non si riapriva Jenny (06/10/2026, sul
telefono). Il gateway registra qui come spedirlo
(``GatewayContainer``); senza, non succede niente.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

_notifier: Callable[[str], Awaitable[None]] | None = None


def set_notifier(fn: Callable[[str], Awaitable[None]] | None) -> None:
    global _notifier
    _notifier = fn


async def data_changed(slug: str) -> None:
    """Dillo alla WebUI. Mai un'eccezione: un avviso perso non e' un guasto."""
    if _notifier is None:
        return
    try:
        await _notifier(slug)
    except Exception as exc:  # noqa: BLE001
        logger.debug("app data-changed notify failed for {}: {}", slug, exc)
