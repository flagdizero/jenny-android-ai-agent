"""Il comando ``cron.restore``: l'«Annulla» dopo aver tolto un promemoria.

Una scrittura, quindi un comando del WebSocket e non una route ``/api/``
(``CONTRIBUTING.md``). Pausa, ripresa ed eliminazione restano sulle route di
``cron_routes.py``, che esistevano da prima della regola.

Gira sul loop, come i gesti di quelle route e come il tool ``cron``: tutti
scrivono lo stesso ``CronService``, e due thread sul suo store sarebbero due
scrittori. La logica sta in :meth:`CronService.restore_job`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from jenny.webui.commands import CommandContext, CommandError

# Come in ``cron_routes.py``: otto caratteri di uuid, o un nome di sistema.
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def cron_restore(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Rimette il job ``id`` tolto da poco (``not_found`` se e' passato troppo)."""
    job_id = params.get("id")
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise CommandError("bad_request", "id required")
    get_cron = getattr(ctx, "get_cron_service", None)
    cron = get_cron() if get_cron else None
    if cron is None:
        raise CommandError("unavailable", "scheduler not available")
    result = cron.restore_job(job_id)
    if result == "not_found":
        raise CommandError("not_found", "nothing to restore")
    if result == "taken":
        raise CommandError("conflict", "a job with this id exists")
    return {"ok": True, "result": result}


CRON_COMMANDS = {
    "cron.restore": cron_restore,
}
