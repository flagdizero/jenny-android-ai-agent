"""«I dati di un'app sono cambiati»: l'avviso alla WebUI.

L'avviso (``app_data_changed`` sul WebSocket) lo ascoltano lo schermo aperto
dell'app, che si rilegge, e la bacheca, che ridisegna la polaroid col valore
nuovo. Lo mandava solo il tool di un'app quando scriveva Jenny: una scrittura
dallo schermo, o un lettore che ricalcola, non lo diceva a nessuno, e la
polaroid restava indietro finché non si riapriva Jenny (06/10/2026, sul
telefono). Il filo è ``runtime/ui_events``.
"""

from __future__ import annotations

from jenny.runtime import ui_events


async def data_changed(slug: str) -> None:
    """Dillo alla WebUI. Mai un'eccezione: un avviso perso non e' un guasto."""
    await ui_events.emit({"_app_data_changed": True, "app_slug": slug})
