"""I comandi ``apps.*`` dell'interfaccia: il cestino delle app.

Sono RPC sul WebSocket della WebUI, come i ``board.*``: le chiama solo
l'interfaccia, con il segreto della WebUI. Jenny non ha un tool che le
raggiunga, ed e' questo che le rende **dell'utente**: «Elimina per sempre» lo
preme una persona.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from jenny.webui.commands import CommandContext, CommandError


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


def _trash_id(params: Mapping[str, Any]) -> str:
    value = params.get("id")
    if not isinstance(value, str) or not value:
        raise CommandError("bad_request", "id required")
    return value


async def apps_trash_list(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Il cestino delle app, la voce più recente per prima."""
    from jenny.apps.trash import list_trash

    return {"ok": True, "trash": await _run(list_trash, ctx.get_workspace_root())}


async def apps_restore(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Rimette un'app dal cestino (``id`` = la voce del cestino)."""
    from jenny.apps.trash import restore_app

    try:
        slug = await _run(restore_app, ctx.get_workspace_root(), _trash_id(params))
    except ValueError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    except FileNotFoundError as exc:
        raise CommandError("not_found", str(exc)) from exc
    except FileExistsError as exc:
        raise CommandError("conflict", str(exc)) from exc
    return {"ok": True, "slug": slug}


async def apps_purge(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Butta per sempre una voce del cestino, dati compresi."""
    from jenny.apps.trash import purge_app

    try:
        slug = await _run(purge_app, ctx.get_workspace_root(), _trash_id(params))
    except ValueError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    except FileNotFoundError as exc:
        raise CommandError("not_found", str(exc)) from exc
    return {"ok": True, "slug": slug}


APPS_COMMANDS = {
    "apps.trash": apps_trash_list,
    "apps.restore": apps_restore,
    "apps.purge": apps_purge,
}
