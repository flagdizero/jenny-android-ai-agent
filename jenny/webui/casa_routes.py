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
from jenny.config.schema import MAX_SCHERMATE, SPECIE_SCHERMATA, Config, SchermataConfig


async def stacca_pagine_di(kind: str, ref: str) -> int:
    """Toglie da ``casa.schermate`` le pagine che puntano a (*kind*, *ref*).

    La chiamano le due cancellazioni — un'app, un quaderno — **dopo** aver
    cancellato: la cancellazione e' I/O lento e fuori dal lucchetto ci deve
    restare, qui dentro c'e' solo un filtro.

    **Non contraddice** la regola per cui il server non toglie una pagina da
    se' quando il suo contenuto sparisce (v. la testata di
    ``tests/webui/test_casa_schermate_routes.py``). Li' la cosa se ne va per
    altre strade e nessuno ha deciso niente sulla pagina; qui l'utente ha
    **cancellato la cosa** dalla sua scheda, e la pagina e' della cosa. Tenerla
    vorrebbe dire un pallino verso il nulla, lasciato li' apposta.

    Torna quante ne ha tolte. Se non ce n'erano il file non si tocca.
    """
    from jenny.config import store

    tolte = 0

    def _applica(config: Config) -> bool:
        nonlocal tolte
        prima = list(config.casa.schermate)
        dopo = [s for s in prima if not (s.kind == kind and s.ref == ref)]
        tolte = len(prima) - len(dopo)
        if not tolte:
            return False
        config.casa.schermate = dopo
        return True

    await store.mutate(_applica)
    return tolte


async def rinomina_pagine_di(kind: str, ref: str, nuovo_ref: str) -> int:
    """Le pagine di (*kind*, *ref*) seguono la cosa sotto *nuovo_ref*.

    Il gemello di :func:`stacca_pagine_di`, per il rinomino di un quaderno: la
    pagina salva ``project:<nome>``, e senza questo un rinomino la lascerebbe
    puntata a un nome che non esiste piu'. Stesso ordine: dopo, fuori dal
    lucchetto per tutto quel che e' lento. Torna quante ne ha spostate.
    """
    from jenny.config import store

    spostate = 0

    def _applica(config: Config) -> bool:
        nonlocal spostate
        spostate = 0
        for s in config.casa.schermate:
            if s.kind == kind and s.ref == ref:
                s.ref = nuovo_ref
                spostate += 1
        return spostate > 0

    await store.mutate(_applica)
    return spostate


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
        """
        from jenny.config import store

        grezzo = query_first(parse_query(request.path), "v")
        if grezzo is None:
            return http_error(400, "missing v")
        try:
            dati = json.loads(grezzo)
        except ValueError:
            return http_error(400, "invalid v")
        if not isinstance(dati, list):
            return http_error(400, "v must be a list")

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

        def _applica(config: Config) -> bool:
            prima = [s.model_dump() for s in config.casa.schermate]
            dopo = [s.model_dump() for s in schermate]
            if prima == dopo:
                return False
            config.casa.schermate = list(schermate)
            return True

        try:
            await store.mutate(_applica)
        except Exception:
            self._log.exception("Saving the casa pages failed")
            return http_error(500, "could not save the pages")
        return http_json_response(
            {"ok": True, "schermate": [s.model_dump() for s in schermate]}
        )
