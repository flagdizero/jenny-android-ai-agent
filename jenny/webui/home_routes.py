"""Route HTTP ``/api/home/*``: le pagine della schermata iniziale.

La casa e' un launcher: di lato alla chat ci sono le pagine che l'utente ha
aggiunto (v. la tavola `Pagine`). Quell'elenco vive in ``config.json`` e non nel
browser, ed e' una scelta con un motivo: sono la schermata iniziale del
telefono, perderle a un ripristino o a una reinstallazione sarebbe la sorpresa
peggiore, e ``localStorage`` non entra nel backup cifrato.

**Qui si legge soltanto.** La scrittura e' il comando RPC ``home.pages.set``
(``jenny/webui/commands.py``): fino al 25/09/2026 era una GET col JSON
nell'indirizzo, e ``/api/`` e' per letture e parametri corti
(``.agent/design.md``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import http_error, http_json_response
from jenny.config.schema import FIXED_PAGES, MAX_PAGES, PAGE_KINDS


class HomeRoutes:
    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        log: Any = logger,
    ) -> None:
        self._check_api_token = check_api_token
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if not path.startswith("/api/home/"):
            return None
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")

        if path == "/api/home/pages":
            return self._list()
        return None

    # -- handlers --

    def _list(self) -> Response:
        """L'elenco, piu' il tetto: il client non se lo tiene scritto da sé.

        Il tetto sta nello schema (``MAX_PAGES``) perche' e' il file a
        doverlo rispettare, e arriva di qui perche' il foglio «Le pagine di
        casa» deve sapere quando smettere di offrire la riga vuota. Due copie
        di quel numero divergerebbero, e la seconda si scoprirebbe solo quando
        un salvataggio viene rifiutato.
        """
        from jenny.config.loader import load_config

        config = load_config()
        return http_json_response(
            {
                "pages": [s.model_dump() for s in config.home.pages],
                "order": list(config.home.order),
                "fixed": list(FIXED_PAGES),
                "max": MAX_PAGES,
                "kinds": list(PAGE_KINDS),
            }
        )
