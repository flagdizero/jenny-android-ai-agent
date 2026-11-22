"""Il comando ``phone.update``: le impostazioni di «Usa il telefono».

Una scrittura, quindi un comando del WebSocket e non una route ``/api/``
(``CONTRIBUTING.md``). La logica sta in ``phone_api.py``; qui c'è la porta.

**Solo dal telefono.** Chi è collegato da lontano non accende le mani di Jenny
né toglie la conferma: è la stessa regola delle route che agiscono sul telefono
(``phone_only.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jenny.webui.commands import CommandContext, CommandError


async def phone_update(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Cambia la modalità (``mode``) e/o toglie un'app da «Sempre» (``forget``)."""
    from jenny.channels.ws_remote import on_remote_listener
    from jenny.webui.phone_api import MODES, update_phone

    if on_remote_listener():
        raise CommandError("phone_only", "this is changed from the phone itself")
    mode = params.get("mode")
    forget = params.get("forget")
    if mode is not None and mode not in MODES:
        raise CommandError("bad_request", "mode must be off, confirm or free")
    if forget is not None and not isinstance(forget, str):
        raise CommandError("bad_request", "forget must be a string")
    return {"ok": True, "phone": await update_phone(mode=mode, forget=forget)}


PHONE_COMMANDS = {
    "phone.update": phone_update,
}
