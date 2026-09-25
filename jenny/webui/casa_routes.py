"""Route HTTP ``/api/casa/*``: le pagine della schermata iniziale.

La casa e' un launcher: di lato alla chat ci sono le pagine che l'utente ha
aggiunto (v. la tavola `Pagine`). Quell'elenco vive in ``config.json`` e non nel
browser, ed e' una scelta con un motivo: sono la schermata iniziale del
telefono, perderle a un ripristino o a una reinstallazione sarebbe la sorpresa
peggiore, e ``localStorage`` non entra nel backup cifrato.

**Perche' si scrive con una GET.** Il livello HTTP del gateway (``websockets``
http11) rifiuta qualunque metodo diverso da GET **e qualunque body**, al
parser, prima di arrivare qui — lo dice gia' ``apps_api`` per le azioni delle
app. Quindi l'elenco nuovo viaggia url-encoded in un parametro. Non e' una
svista da "correggere" in POST: senza sostituire il livello HTTP non puo'
funzionare.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    parse_query,
    query_first,
)
from jenny.config.schema import (
    MAX_SCHERMATE,
    PAGINE_FISSE,
    SPECIE_SCHERMATA,
    Config,
    SchermataConfig,
    ordine_normale,
)


class CasaRoutes:
    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        log: Any = logger,
    ) -> None:
        self._check_api_token = check_api_token
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if not path.startswith("/api/casa/"):
            return None
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")

        if path == "/api/casa/schermate":
            return self._elenco()
        if path == "/api/casa/schermate/set":
            return await self._salva(request)
        return None

    # -- handlers --

    def _elenco(self) -> Response:
        """L'elenco, piu' il tetto: il client non se lo tiene scritto da sé.

        Il tetto sta nello schema (``MAX_SCHERMATE``) perche' e' il file a
        doverlo rispettare, e arriva di qui perche' il foglio «Le pagine di
        casa» deve sapere quando smettere di offrire la riga vuota. Due copie
        di quel numero divergerebbero, e la seconda si scoprirebbe solo quando
        un salvataggio viene rifiutato.
        """
        from jenny.config.loader import load_config

        config = load_config()
        return http_json_response(
            {
                "schermate": [s.model_dump() for s in config.casa.schermate],
                "ordine": list(config.casa.ordine),
                "fisse": list(PAGINE_FISSE),
                "max": MAX_SCHERMATE,
                "specie": list(SPECIE_SCHERMATA),
            }
        )

    async def _salva(self, request: WsRequest) -> Response:
        """L'elenco intero, non una riga.

        Aggiungere, togliere e spostare sono la stessa scrittura: mandare
        l'elenco completo toglie di mezzo tre rotte e, soprattutto, il caso in
        cui due di quelle si incrociano lasciando un ordine che nessuno ha
        chiesto.

        ``v`` e' ``{schermate, ordine}``. Fino al 24/09/2026 valeva anche il solo
        elenco delle schermate, com'era prima del 23/09, ma il suo unico
        mittente (``api.setSchermate``) non c'e' piu', e UI e gateway escono
        dallo stesso APK. L'ordine che arriva deve essere **esattamente** le
        fisse piu' le schermate, ognuna una volta: la tolleranza di
        :func:`ordine_normale` e' per il file, non per chi scrive.
        """
        from jenny.config import store

        grezzo = query_first(parse_query(request.path), "v")
        if grezzo is None:
            return http_error(400, "missing v")
        try:
            dati = json.loads(grezzo)
        except ValueError:
            return http_error(400, "invalid v")
        if not isinstance(dati, dict):
            return http_error(400, "v must be {schermate, ordine}")
        if not isinstance(dati.get("schermate"), list):
            return http_error(400, "v.schermate must be a list")
        ordine = dati.get("ordine")
        dati = dati["schermate"]

        # Validare **prima** di entrare in `mutate`: dentro si tiene un lock per
        # tutta la durata della callback, e una `ValueError` alzata li' dentro
        # lo fa attraversare a un'eccezione invece che a un 400.
        try:
            schermate = [SchermataConfig(**riga) for riga in dati]
        except Exception as exc:  # noqa: BLE001 — il messaggio va all'utente
            return http_error(400, str(exc))
        if len(schermate) > MAX_SCHERMATE:
            return http_error(400, f"too many pages (max {MAX_SCHERMATE})")
        identificativi = [s.id for s in schermate]
        if len(set(identificativi)) != len(identificativi):
            return http_error(400, "duplicate page id")
        riservati = sorted(set(identificativi) & set(PAGINE_FISSE))
        if riservati:
            return http_error(400, f"reserved page id: {', '.join(riservati)}")
        attesi = [*PAGINE_FISSE, *identificativi]
        if (
            not isinstance(ordine, list)
            or not all(isinstance(v, str) for v in ordine)
            or sorted(ordine) != sorted(attesi)
        ):
            return http_error(
                400, "ordine must list every fixed page and every page id, once each"
            )

        def _applica(config: Config) -> bool:
            prima = [s.model_dump() for s in config.casa.schermate]
            dopo = [s.model_dump() for s in schermate]
            ordine_dopo = ordine_normale(ordine, identificativi)
            if prima == dopo and config.casa.ordine == ordine_dopo:
                return False
            config.casa.schermate = list(schermate)
            config.casa.ordine = ordine_dopo
            return True

        try:
            await store.mutate(_applica)
        except Exception:
            self._log.exception("Saving the casa pages failed")
            return http_error(500, "could not save the pages")
        from jenny.config.loader import load_config

        return http_json_response(
            {
                "ok": True,
                "schermate": [s.model_dump() for s in schermate],
                "ordine": list(load_config().casa.ordine),
            }
        )
