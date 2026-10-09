"""«La bacheca di un quaderno è cambiata»: il filo che lo porta alla WebUI.

La bacheca si rilegge da sola dopo le scritture che fa lei (le RPC
``board.*``). Le altre — l'editore dei quaderni lanciato dalla chat, un'app
posata, un cron agganciato a un cassetto, la passata del giardiniere — fino al
09/10/2026 non lo dicevano a nessuno, e la bacheca aperta sotto la chat restava
quella di prima finché non la si riapriva. Il gateway registra qui come
spedirlo (``GatewayContainer``); senza, non succede niente.

Gemello di ``jenny/apps/notify.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

_notifier: Callable[[str], Awaitable[None]] | None = None


def set_notifier(fn: Callable[[str], Awaitable[None]] | None) -> None:
    global _notifier
    _notifier = fn


async def board_changed(notebook: str) -> None:
    """Dillo alla WebUI. Mai un'eccezione: un avviso perso non e' un guasto."""
    if _notifier is None:
        return
    try:
        await _notifier(notebook)
    except Exception as exc:  # noqa: BLE001
        logger.debug("board-changed notify failed for {}: {}", notebook, exc)
