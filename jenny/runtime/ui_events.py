"""«La casa è cambiata»: il filo che porta un avviso alla WebUI.

Un avviso è un frame senza testo sul WebSocket, coi suoi metadati
(``{"_board_changed": True, "notebook": …}``): la bacheca aperta si ridisegna,
lo schermo di un'app si rilegge, il contatore delle bozze si aggiorna. Il
gateway registra qui come spedirlo (``GatewayContainer._wire_ui_events``);
senza, non succede niente. Il vocabolario degli avvisi resta a chi li manda:
:func:`jenny.board.notify.board_changed`, :func:`jenny.apps.notify.data_changed`
e le bozze dei subagent (``agent.staged_writes``).

Prima erano due moduli gemelli, ognuno col suo notificatore, e tre funzioni di
cablaggio nel container.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from loguru import logger

_publisher: Callable[[dict[str, Any]], Awaitable[None]] | None = None


def set_publisher(fn: Callable[[dict[str, Any]], Awaitable[None]] | None) -> None:
    """Come spedire un avviso (i suoi metadati); ``None`` lo spegne."""
    global _publisher
    _publisher = fn


async def emit(event: Mapping[str, Any]) -> None:
    """Dillo alla WebUI. Mai un'eccezione: un avviso perso non e' un guasto."""
    if _publisher is None:
        return
    try:
        await _publisher(dict(event))
    except Exception as exc:  # noqa: BLE001
        logger.debug("ui event {} not delivered: {}", dict(event), exc)
