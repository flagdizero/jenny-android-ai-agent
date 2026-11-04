"""«La bacheca di un quaderno è cambiata»: l'avviso alla WebUI.

La bacheca si rilegge da sola dopo le scritture che fa lei (le RPC
``board.*``). Le altre — l'editore dei quaderni lanciato dalla chat, un'app
posata, un cron agganciato a un cassetto, la passata del giardiniere — fino al
09/10/2026 non lo dicevano a nessuno, e la bacheca aperta sotto la chat restava
quella di prima finché non la si riapriva. Il filo è ``runtime/ui_events``.
"""

from __future__ import annotations

from jenny.runtime import ui_events


async def board_changed(notebook: str) -> None:
    """Dillo alla WebUI. Mai un'eccezione: un avviso perso non e' un guasto."""
    await ui_events.emit({"_board_changed": True, "notebook": notebook})
