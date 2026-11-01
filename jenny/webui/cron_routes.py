"""Route HTTP ``/api/webui/cron``: lo stato della programmazione, e tre gesti.

Adattatore sottile sopra ``webui/cron_api.webui_cron_payload``: la forma del
payload e le sue decisioni stanno la', qui c'e' soltanto il trasporto — token,
thread, codici di stato.

**I gesti sono tre, e sono questi tre apposta** (26/09/2026):
``/api/webui/cron/<id>/pause``, ``.../resume`` e ``.../remove``. Fino ad allora il
pannello era in sola lettura, per due ragioni, e una sola regge ancora:

- *Un secondo scrittore* accanto al tool ``cron`` sarebbe la forma di difetto che
  ``config/store.py::mutate`` documenta. Ma queste route non scrivono lo store:
  chiamano ``CronService.set_paused`` e ``remove_job`` sullo **stesso** servizio
  che usa il tool, nello stesso processo e sullo stesso loop — lo stesso imbuto,
  non un altro. Per questo girano sul loop e non in un thread, come le chiamate
  del tool: due thread sullo stesso ``self._store`` sarebbero proprio due
  scrittori.
- *«Esegui adesso»* (``run_job(force=True)``) resta fuori: accoda un turno
  d'agente, cioe' spende token e puo' far partire una consegna. Un pulsante che
  costa soldi e parla all'utente non entra in un pannello per comodita'.

Il perche' dei tre: fermare un promemoria che dava fastidio voleva dire
convincere Jenny a cancellarlo. Solo i job dell'utente; un job di sistema
risponde 409 ``protected``, come ``remove_job`` rifiuta gia'. Non 403: il client
tratta 401 e 403 come token scaduto e ricarica la SPA, e un rifiuto del servizio
non e' un problema di credenziali.

Il servizio arriva come **getter** e non come oggetto, per la stessa ragione di
``SubagentRoutes``: ``GatewayContainer.cron`` nasce ``None`` e durante
l'onboarding la WebUI e' gia' servita.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import http_error, http_json_response
from jenny.webui.cron_api import snapshot_cron, webui_cron_payload

_PATH = "/api/webui/cron"
_ACTION_RE = re.compile(r"^/api/webui/cron/([^/]+)/(pause|resume|remove)$")
# Gli id dei job: otto caratteri di uuid per quelli dell'utente, un nome per
# quelli di sistema (``dream``, ``update_check``). Niente punti ne' percento:
# l'id arriva da un path, e non deve poterne comporre un altro.
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Cosa risponde il servizio → codice HTTP. 200 anche per ``unchanged``: la cosa
# chiesta e' gia' vera, e il pannello deve solo ridisegnarsi.
_STATUS = {
    "paused": 200,
    "resumed": 200,
    "removed": 200,
    "unchanged": 200,
    "not_found": 404,
    # Conflitto con lo stato del job, non credenziali: v. il cappello.
    "protected": 409,
    "expired": 409,
}


class CronRoutes:
    """La route della sezione «Programmazione» delle impostazioni."""

    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_cron_service: Callable[[], Any | None],
        log: Any = logger,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_cron_service = get_cron_service
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        """``None`` se il path non e' suo: mangiare il dispatch fermerebbe le
        famiglie di route montate dopo questa."""
        if path == _PATH:
            return await self._get(request)
        m = _ACTION_RE.match(path)
        if m:
            return await self._act(request, m.group(1), m.group(2))
        return None

    async def _act(self, request: WsRequest, job_id: str, action: str) -> Response:
        """Pausa, ripresa o eliminazione: **sul loop**, v. il cappello.

        Un job di un quaderno vive anche sulla sua bacheca: l'eliminazione passa da
        ``cron_link.detach_job`` (che ripulisce la bacheca), e pausa e ripresa le
        dicono alla bacheca aperta.
        """
        from jenny.board.cron_link import detach_job
        from jenny.board.notify import board_changed
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        if not _JOB_ID_RE.match(job_id):
            return http_error(400, "invalid job id")
        try:
            cron = self._get_cron_service()
        except Exception:
            self._log.exception("Cron routes: the service getter raised")
            cron = None
        if cron is None:
            return http_error(503, "scheduler not available")
        try:
            job = cron.get_job(job_id)
            notebook = ((job.payload.board or {}).get("notebook") if job is not None else None)
            if action == "remove":
                result = await detach_job(cron, job_id)
            else:
                result = cron.set_paused(job_id, action == "pause")
                if notebook and result in ("paused", "resumed"):
                    await board_changed(str(notebook))
        except Exception:
            self._log.exception("Cron {} {} failed", action, job_id)
            return http_error(500, f"cron {action} failed")
        status = _STATUS.get(result, 500)
        if status != 200:
            return http_error(status, str(result))
        return http_json_response({"result": result})

    async def _get(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        try:
            cron = self._get_cron_service()
        except Exception:
            # Il getter e' una lambda sul container: se solleva, il pannello non
            # deve diventare un 500 — e' lo stesso caso di "non c'e' ancora".
            self._log.exception("Cron routes: the service getter raised")
            cron = None
        try:
            # Lo store si legge qui, sul loop, come lo leggono il timer, il tool
            # e i gesti qui sopra: in un thread ``list_jobs`` riassegnava
            # ``CronService._store`` sotto di loro, e un giro del timer salvava
            # poi la copia vecchia (il job appena eseguito ripartiva).
            snapshot = snapshot_cron(cron)
            # Il resto in un thread: la config da disco.
            payload = await asyncio.to_thread(webui_cron_payload, snapshot)
        except Exception:
            self._log.exception("Cron status failed")
            return http_error(500, "cron status failed")
        return http_json_response(payload)
