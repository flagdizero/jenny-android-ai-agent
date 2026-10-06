"""I comandi ``apps.*`` dell'interfaccia: il cestino, e (dalla prova in poi) le
risposte dell'utente alle app nuove.

Sono RPC sul WebSocket della WebUI, come i ``board.*``: le chiama solo
l'interfaccia, con il segreto della WebUI. Jenny non ha un tool che le
raggiunga, ed e' questo che le rende **dell'utente**: «Tienila» e «Svuota il
cestino» li preme una persona.
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


def _slug(params: Mapping[str, Any]) -> str:
    from jenny.apps.manifest import SLUG_RE

    value = params.get("slug")
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise CommandError("bad_request", "slug required")
    return value


async def apps_keep(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """«Tienila»: la bozza in prova diventa l'app, e il suo codice è approvato.

    Senza bozza, riapprova il codice che l'app ha adesso: e' il caso di un'app
    cambiata fuori da Jenny («Riapprova»). Dopo, il lettore (se c'e') legge
    subito, cosi' il riassunto ha un valore vero.
    """
    from jenny.apps import trust

    workspace = ctx.get_workspace_root()
    slug = _slug(params)
    draft = await _run(trust.load_draft, workspace, slug)
    if draft is not None:
        if draft.app.broken:
            raise CommandError("conflict", f"the draft is broken: {draft.app.error}")
        await _run(trust.promote_draft, workspace, slug)
    elif not (workspace / "apps" / slug / "app.json").is_file():
        raise CommandError("not_found", "nothing to keep")
    await _run(trust.approve, workspace, slug)
    await _after_keep(workspace, slug)
    return {"ok": True, **(await _run(trust.app_trust_state, workspace, slug))}


async def _after_keep(workspace: Any, slug: str) -> None:
    """Il primo giro del lettore, se l'app ne ha uno (v. ``jenny/apps/reader.py``)."""
    try:
        from jenny.apps.reader import run_reader
    except ImportError:  # pragma: no cover - il lettore arriva con la fase 5
        return
    try:
        await run_reader(workspace, slug, force=True)
    except Exception:  # noqa: BLE001 — tenere l'app non deve fallire per una lettura
        from loguru import logger

        logger.exception("apps: first read of {} after keep failed", slug)


async def apps_discard(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """«Scarta»: la bozza se ne va. Un'app che c'era resta com'era."""
    from jenny.apps import trust

    workspace = ctx.get_workspace_root()
    slug = _slug(params)
    if not await _run(trust.discard_draft, workspace, slug):
        raise CommandError("not_found", "no draft to discard")
    return {"ok": True, **(await _run(trust.app_trust_state, workspace, slug))}


APPS_COMMANDS = {
    "apps.keep": apps_keep,
    "apps.discard": apps_discard,
    "apps.trash": apps_trash_list,
    "apps.restore": apps_restore,
    "apps.purge": apps_purge,
}
