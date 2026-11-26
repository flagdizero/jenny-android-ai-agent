"""I comandi ``memory.*``: la stanza «Cosa ricorda di te».

Scritture, quindi comandi del WebSocket e non route ``/api/``
(``CONTRIBUTING.md``). La logica sta in ``agent/memory_service.py``; qui c'e' la
porta: i parametri, i codici d'errore, e il thread — leggere l'archivio intero
e riscrivere un file sono millisecondi di disco che il loop non deve aspettare.

- ``memory.list`` → le due sezioni (le frasi, col tema quelle di «Di te»),
  «Tolte da te», ``dreaming`` se Dream sta lavorando adesso e ``sorting`` se
  le frasi senza tema si stanno ordinando (parte da qui, in sottofondo);
- ``memory.correct {file, id, text, reason}`` con ``reason`` ``changed`` o
  ``wrong`` (la stanza manda sempre ``wrong``: la frase di prima non torna);
- ``memory.remove {file, id}``;
- ``memory.restore {id, lock?}``: «Rimetti», e l'«Annulla» del cestino (con
  ``lock`` com'era la voce prima).

Un id che non c'e' piu' risponde ``conflict``: la voce e' cambiata nel frattempo,
e la stanza rilegge invece di scriverci sopra.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from jenny.webui.commands import CommandContext, CommandError, _require_workspace_flag


def _root(ctx: CommandContext) -> Path:
    return Path(ctx.get_workspace_root())


def _id(params: Mapping[str, Any], key: str = "id", *, required: bool = True) -> str | None:
    value = params.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.isalnum() or len(value) > 64:
        raise CommandError("bad_request", f"{key} required")
    return value


async def _run(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    from jenny.agent.memory_service import MemoryEditError

    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except MemoryEditError as exc:
        raise CommandError(exc.code, str(exc)) from exc


async def memory_list(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent import memory_themes
    from jenny.agent.memory_service import list_memory

    root = _root(ctx)
    out = await _run(list_memory, root)
    llm = ctx.get_llm() if getattr(ctx, "get_llm", None) else None
    if out.pop("unsorted", 0):
        # Le frasi senza tema si ordinano in sottofondo: la stanza le mostra
        # subito cosi' come sono, e si rilegge finche' ``sorting`` e' vero.
        memory_themes.sort_in_background(root, llm)
    out["sorting"] = memory_themes.sorting(root)
    return out


async def memory_correct(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent.memory_service import correct

    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")
    file_key = params.get("file")
    if not isinstance(file_key, str):
        raise CommandError("bad_request", "file required")
    return await _run(correct, _root(ctx), file_key, _id(params), params.get("text"),
                      str(params.get("reason") or ""))


async def memory_remove(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent.memory_service import remove

    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")
    file_key = params.get("file")
    if not isinstance(file_key, str):
        raise CommandError("bad_request", "file required")
    return await _run(remove, _root(ctx), file_key, _id(params))


async def memory_restore(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    from jenny.agent.memory_service import restore

    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")
    lock = params.get("lock", True)
    if not isinstance(lock, bool):
        raise CommandError("bad_request", "lock must be a boolean")
    return await _run(restore, _root(ctx), _id(params), lock=lock)


MEMORY_COMMANDS = {
    "memory.list": memory_list,
    "memory.correct": memory_correct,
    "memory.remove": memory_remove,
    "memory.restore": memory_restore,
}
