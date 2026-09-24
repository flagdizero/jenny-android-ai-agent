"""Route HTTP ``/api/subagents*`` (stato dei subagent + stop/rilancio).

Serve **verbatim** il payload di ``SubagentManager.status_snapshot``, lo stesso
che il canale WebSocket manda live come frame ``subagent_status``: una sola
forma, due trasporti. Il GET è ciò che fa sopravvivere il pannello a un reload
di pagina — su Android il processo della WebView muore spesso, e senza questa
route il pannello resterebbe vuoto fino alla transizione di stato successiva
(che con cinque job lenti può essere minuti dopo).

Oltre allo snapshot ci sono le due letture della telemetria fine —
``/{id}/activity`` (la finestra viva, con lo stesso contratto del frame WS
``subagent_activity``: vedi ``channels/subagent_activity_wire.py``) e
``/{id}/digest`` (la condensa post-mortem). La prima è ciò che permette a un
reload o a una WebSocket caduta di riprendere da ``?since=<seq>`` senza buchi;
la seconda è ciò che la chat espande sotto il messaggio di un subagent finito.

Nota di trasporto: il layer HTTP del gateway (``websockets.http11``) rifiuta al
parser ogni metodo diverso da GET *e* qualunque body, quindi anche le azioni
passano da una GET su un path d'azione (``.../restart``, ``.../cancel``).
Identica scelta, già documentata, di ``apps_api.py`` e delle route skills: non è
"sistemabile" a POST senza sostituire il layer HTTP.

Nota di layering: questo modulo non importa nulla da ``jenny/agent``. Il manager
arriva come dipendenza opaca e i suoi errori sono riconosciuti per nome di
classe (vedi ``_ACTION_ERROR_STATUS``), così la WebUI resta ignara del package
dell'agente esattamente come promette il docstring di ``status_snapshot``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import http_error, http_json_response, parse_query, query_first
from jenny.channels.subagent_activity_wire import (
    MAX_HTTP_EVENTS,
    digest_payload,
    empty_window_payload,
    window_payload,
)
from jenny.security.wire_ids import WIRE_ID_RE
from jenny.session.keys import UNIFIED_SESSION_KEY

# Gli id (task e lineage) sono esadecimali corti generati dal manager; il regex
# è volutamente più larguccio per non rompersi se il formato cambia, ma esclude
# separatori di path e caratteri non stampabili.
_ID_RE = WIRE_ID_RE

_ACTION_RE = re.compile(r"^/api/subagents/([^/]+)/(restart|cancel)$")

# Letture della telemetria fine. **Due route, non una con un parametro**, e per
# una ragione di forma: ``activity`` è una finestra volatile del ring vivo, con
# ``seq``, ``latest_seq`` e ``gap`` — cioè semantica di cursore — mentre
# ``digest`` è la condensa post-mortem, rinumerata da 1, immutabile una volta
# scritta e con i ``tool_start``/``tool_end`` già collassati. Sono due risorse
# con lifetime e cacheabilità diverse: farne una sola route con ``?digest=1``
# significherebbe una risposta la cui *forma* dipende da un query param, che è
# esattamente ciò che il resto di questo modulo evita ("una forma, due
# trasporti").
_READ_RE = re.compile(r"^/api/subagents/([^/]+)/(activity|digest)$")

# ``?since=`` accetta solo cifre: un valore malformato è un bug del client, e
# rispondere 400 lo rende visibile invece di servire silenziosamente tutto il
# ring come se il cursore fosse 0.
_SINCE_RE = re.compile(r"^\d{1,12}$")

# Errori del manager → status HTTP. Mappati per nome di classe invece che per
# ``except SubagentRestartError``: importare le classi obbligherebbe la WebUI a
# dipendere da ``jenny/agent``. 409 = il rilancio non è possibile per come sta
# il lavoro adesso; 429 = c'è un tetto di concorrenza, riprova quando uno slot
# si libera.
_ACTION_ERROR_STATUS = {
    "SubagentRestartError": 409,
    "SubagentConcurrencyLimitError": 429,
}

_EMPTY_SNAPSHOT: dict[str, list] = {"running": [], "recent": []}


class SubagentRoutes:
    """Route ``/api/subagents``, ``/{id}/{restart,cancel}`` e ``/{id}/{activity,digest}``."""

    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_subagent_manager: Callable[[], Any | None],
        log: Any = logger,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_subagent_manager = get_subagent_manager
        self._log = log

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/subagents":
            return self._snapshot(request)
        m = _ACTION_RE.match(path)
        if m:
            return await self._action(request, m.group(1), m.group(2))
        m = _READ_RE.match(path)
        if m:
            return self._read(request, m.group(1), m.group(2))
        return None

    # -- helpers ------------------------------------------------------------

    def _manager(self) -> Any | None:
        """Manager dei subagent, o ``None`` se l'agente non esiste ancora.

        Late-binding voluto: durante l'onboarding l'agente non è ancora stato
        creato, e il gateway serve già la WebUI.
        """
        try:
            return self._get_subagent_manager()
        except Exception:  # noqa: BLE001 — un getter rotto non deve dare 500
            self._log.exception("Subagent manager lookup failed")
            return None

    @staticmethod
    def _session_key(request: WsRequest) -> str | None:
        """``?session_key=`` opzionale, tradotta nella chiave core.

        Assente = nessun filtro (tutti i subagent, anche quelli del lavoro
        interno). I client passano la chiave WebUI ``websocket:default``, che
        lato core è la sessione unificata.
        """
        raw = query_first(parse_query(request.path), "session_key")
        if not raw:
            return None
        key = unquote(raw).strip()
        if not key:
            return None
        if key == "websocket:default":
            return UNIFIED_SESSION_KEY
        return key

    # -- GET /api/subagents -------------------------------------------------

    def _snapshot(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        manager = self._manager()
        if manager is None:
            # Nessun agente = nessun subagent: uno snapshot vuoto è la risposta
            # corretta, non un errore (il pannello si limita a restare chiuso).
            return http_json_response(dict(_EMPTY_SNAPSHOT))
        try:
            snapshot = manager.status_snapshot(self._session_key(request))
        except Exception:
            self._log.exception("Subagent snapshot failed")
            return http_error(500, "subagent snapshot failed")
        if not isinstance(snapshot, dict):
            self._log.warning("Subagent snapshot has unexpected type, serving empty")
            return http_json_response(dict(_EMPTY_SNAPSHOT))
        # Verbatim: nessuna riscrittura di forma o di nomi. Il consumatore del
        # frame WS e quello di questa route sono lo stesso codice.
        return http_json_response(snapshot)

    # -- GET /api/subagents/{id}/{restart,cancel} ---------------------------

    async def _action(self, request: WsRequest, raw_id: str, action: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        target_id = unquote(raw_id)
        if _ID_RE.match(target_id) is None:
            return http_error(400, "invalid subagent id")
        manager = self._manager()
        if manager is None:
            return http_error(503, "subagent manager unavailable")
        if action == "restart":
            return await self._restart(manager, target_id)
        return await self._cancel(manager, target_id)

    async def _restart(self, manager: Any, target_id: str) -> Response:
        try:
            # manual=True: il bottone premuto da un umano non viene mai rifiutato
            # dal tetto dei tentativi *automatici*.
            new_task_id = await manager.restart(target_id, manual=True)
        except RuntimeError as e:
            status = _ACTION_ERROR_STATUS.get(type(e).__name__)
            if status is None:
                self._log.exception("Subagent restart failed")
                return http_error(500, "subagent restart failed")
            # Il messaggio del manager è scritto per essere mostrato: passa così.
            return http_error(status, str(e))
        except Exception:
            self._log.exception("Subagent restart failed")
            return http_error(500, "subagent restart failed")
        return http_json_response({"restarted": True, "task_id": new_task_id})

    async def _cancel(self, manager: Any, target_id: str) -> Response:
        try:
            cancelled = await manager.cancel_task(target_id)
        except RuntimeError as e:
            status = _ACTION_ERROR_STATUS.get(type(e).__name__)
            if status is None:
                self._log.exception("Subagent cancel failed")
                return http_error(500, "subagent cancel failed")
            return http_error(status, str(e))
        except Exception:
            self._log.exception("Subagent cancel failed")
            return http_error(500, "subagent cancel failed")
        return http_json_response({"cancelled": bool(cancelled)})

    # -- GET /api/subagents/{id}/{activity,digest} ---------------------------

    def _read(self, request: WsRequest, raw_id: str, resource: str) -> Response:
        """Auth e validazione comuni alle due letture di telemetria.

        Stessa convenzione dei vicini: 401 senza token valido, 400 su un id che
        non passa il charset chiuso. Le letture **degradano** (200 con una
        finestra vuota) dove le azioni fallirebbero con 503: senza agente non
        c'è telemetria, e questo è un fatto, non un errore — il pannello resta
        semplicemente in attesa.
        """
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        task_id = unquote(raw_id)
        if _ID_RE.match(task_id) is None:
            return http_error(400, "invalid subagent id")
        if resource == "activity":
            return self._activity(request, task_id)
        return self._digest(task_id)

    def _activity_log(self) -> Any | None:
        """Il ring vivo dell'attività, o ``None``.

        Dipendenza opaca come il manager: riconosciuta per attributo
        (``activity``) e per superficie (``tail_window``), senza importare
        ``jenny/agent`` — stesso vincolo di layering del resto del modulo.
        """
        manager = self._manager()
        log: Any = getattr(manager, "activity", None) if manager is not None else None
        return log if callable(getattr(log, "tail_window", None)) else None

    def _activity(self, request: WsRequest, task_id: str) -> Response:
        """Finestra di attività da ``?since=`` (risync dopo reload o WS caduta).

        Serve la finestra **verbatim**: stessi nomi di campo del frame WS, quindi
        il client ha un solo parser e un solo modo di leggere ``gap`` — che è la
        ragione per cui questa route esiste. Su Android il processo della WebView
        muore spesso, e ripartire da ``since`` è ciò che evita un buco proprio
        nel percorso che serve a chiuderne uno.
        """
        raw_since = query_first(parse_query(request.path), "since")
        if raw_since not in (None, "") and _SINCE_RE.match(raw_since) is None:
            return http_error(400, "invalid since")
        since = int(raw_since) if raw_since else 0
        log = self._activity_log()
        if log is None:
            return http_json_response({"task_id": task_id, **empty_window_payload(since)})
        try:
            window = log.tail_window(task_id, since_seq=since, limit=MAX_HTTP_EVENTS)
        except Exception:
            self._log.exception("Subagent activity read failed")
            return http_error(500, "subagent activity read failed")
        payload = window_payload(window, limit=MAX_HTTP_EVENTS)
        if payload is None:
            self._log.warning("Subagent activity window has unexpected type, serving empty")
            payload = empty_window_payload(since)
        return http_json_response({"task_id": task_id, **payload})

    def _digest(self, task_id: str) -> Response:
        """Condensa "cosa ha fatto" di un subagent, per il blocco in chat.

        Il file persistito vince sul ring: è completo e sopravvive al task. Se
        non c'è (subagent ancora al lavoro, o digest non ancora scritto) si
        ricava la stessa condensa dal ring vivo e la si marca ``source:
        "live"``, così il client sa che è un'anteprima destinata a cambiare.
        Nessun 404: un subagent senza attività è ``events: []`` con
        ``source: "none"``, che è ciò che il client deve rendere (niente blocco
        da espandere), non un errore da mostrare.
        """
        manager = self._manager()
        store: Any = getattr(manager, "digests", None) if manager is not None else None
        if callable(getattr(store, "load", None)):
            try:
                stored = store.load(task_id)
            except Exception:  # noqa: BLE001 — un digest è un extra, non un 500
                self._log.exception("Subagent digest load failed")
                stored = None
            if isinstance(stored, list) and stored:
                return http_json_response({
                    "task_id": task_id, **digest_payload(stored, "digest"),
                })
        log = self._activity_log()
        if log is not None and callable(getattr(log, "digest", None)):
            try:
                live = log.digest(task_id)
            except Exception:  # noqa: BLE001 — vedi sopra
                self._log.exception("Subagent live digest failed")
                live = None
            if isinstance(live, list) and live:
                return http_json_response({
                    "task_id": task_id, **digest_payload(live, "live"),
                })
        return http_json_response({"task_id": task_id, **digest_payload([], "none")})
