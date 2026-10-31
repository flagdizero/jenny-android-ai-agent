"""I comandi ``staged.*``: le bozze che aspettano un tocco dell'utente.

Una bozza nasce quando un subagent che ha letto contenuto non fidato vuole
cambiare un file che Jenny carica da sola (``agent.staged_writes``). Queste RPC
sono l'unico modo di metterla al suo posto, e le chiama solo l'interfaccia, con
il segreto della WebUI: nessun tool del modello le raggiunge. È questo che fa
della conferma una decisione dell'utente e non del modello.
"""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import Mapping
from typing import Any

from jenny.webui.commands import CommandContext, CommandError

# Una diff più lunga si tronca: la scheda deve restare leggibile su un
# telefono, e chi vuole tutto può aprire il file dopo averla scartata.
_MAX_DIFF_CHARS = 64_000


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


def _staged_id(params: Mapping[str, Any]) -> str:
    value = params.get("id")
    if not isinstance(value, str) or not value:
        raise CommandError("bad_request", "id required")
    return value


def _diff(root: Any, meta: dict[str, Any], content: bytes) -> tuple[str, bool]:
    target = root / meta["target"]
    try:
        before = target.read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        before = []
    except (OSError, UnicodeDecodeError):
        before = ["(the current file is not readable text)\n"]
    after = content.decode("utf-8", "replace").splitlines(keepends=True)
    text = "".join(difflib.unified_diff(
        before, after, fromfile=meta["target"], tofile=meta["target"] + " (draft)",
    ))
    if len(text) > _MAX_DIFF_CHARS:
        return text[:_MAX_DIFF_CHARS], True
    return text, False


def _list(root: Any) -> list[dict[str, Any]]:
    from jenny.agent.staged_writes import list_staged, read_staged

    items = []
    for meta in list_staged(root):
        found = read_staged(root, meta["id"])
        if found is None:
            continue
        diff, truncated = _diff(root, meta, found[1])
        items.append({
            "id": meta["id"],
            "target": meta["target"],
            "sources": meta.get("sources") or [],
            "created_ms": meta.get("created_ms"),
            "new_file": meta.get("target_sha256") is None,
            "target_sha256": meta.get("target_sha256"),
            "diff": diff,
            "truncated": truncated,
        })
    return items


async def staged_list(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Le bozze in attesa, dalla più vecchia, ognuna con la sua diff."""
    return {"ok": True, "staged": await _run(_list, ctx.get_workspace_root())}


async def staged_install(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Mette una bozza al suo posto, se il file è ancora quello mostrato.

    ``target_sha256`` è lo sha che la scheda aveva ricevuto da ``staged.list``
    (``null`` per un file nuovo): se il file è cambiato nel frattempo la risposta
    è ``conflict`` e non si scrive niente.
    """
    from jenny.agent.staged_writes import install

    expected = params.get("target_sha256")
    if expected is not None and not isinstance(expected, str):
        raise CommandError("bad_request", "target_sha256 must be a string or null")
    outcome = await _run(install, ctx.get_workspace_root(), _staged_id(params), expected)
    if outcome == "missing":
        raise CommandError("not_found", "no such draft")
    if outcome == "conflict":
        raise CommandError("conflict", "the file changed since the draft was shown")
    return {"ok": True}


async def staged_discard(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Butta una bozza: il file resta com'era."""
    from jenny.agent.staged_writes import discard

    if not await _run(discard, ctx.get_workspace_root(), _staged_id(params)):
        raise CommandError("not_found", "no such draft")
    return {"ok": True}


STAGED_COMMANDS = {
    "staged.list": staged_list,
    "staged.install": staged_install,
    "staged.discard": staged_discard,
}
