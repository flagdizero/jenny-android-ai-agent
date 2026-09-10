"""Route HTTP ``/api/webui/cron`` (stato della programmazione, sola lettura).

Adattatore sottile sopra ``webui/cron_api.webui_cron_payload``: la forma del
payload e le sue decisioni stanno la', qui c'e' soltanto il trasporto — token,
thread, codici di stato.

**Sola lettura, e non per pigrizia.** Il servizio ha gia' un imbuto di scrittura
— il tool ``cron``, piu' ``action.jsonl`` per il merge fra istanze — e aggiungere
un secondo scrittore da un'altra superficie e' la forma di difetto che
``config/store.py::mutate`` documenta: chi tiene una copia vecchia cancella in
silenzio quel che l'altro ha appena scritto. In piu' «esegui adesso»
(``run_job(force=True)``) accoda un turno d'agente, cioe' spende token e puo' far
partire una consegna: un pulsante che costa soldi e parla all'utente non entra in
un pannello di diagnostica per comodita'.

Il servizio arriva come **getter** e non come oggetto, per la stessa ragione di
``SubagentRoutes``: ``GatewayContainer.cron`` nasce ``None`` e durante
l'onboarding la WebUI e' gia' servita.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import http_error, http_json_response
from jenny.webui.cron_api import webui_cron_payload

_PATH = "/api/webui/cron"


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
        return None

    async def _get(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        try:
            cron = self._get_cron_service()
        except Exception:
            # Il getter e' una lambda sul container: se solleva, il pannello non
            # deve diventare un 500 — e' lo stesso caso di "non c'e' ancora".
            self._log.exception("Cron routes: il getter del servizio ha sollevato")
            cron = None
        try:
            # In un thread: ``webui_cron_payload`` legge lo store del cron sotto
            # il lock del file — lo stesso che prende un ``add_job`` del tool — la
            # config da disco e ``HEARTBEAT.md``. Sul loop del gateway quel lock
            # bloccherebbe la chat e la WebSocket, non solo questa risposta.
            payload = await asyncio.to_thread(webui_cron_payload, cron)
        except Exception:
            self._log.exception("Cron status failed")
            return http_error(500, "cron status failed")
        return http_json_response(payload)
