"""HTTP route adapter for WebUI Settings APIs.

Keep WebUI Settings route handlers here, not in ``channels/websocket.py``.
The websocket channel owns transport concerns; this module owns WebUI Settings
request mapping and response shaping.

Le quattro scritture che portano un **segreto** non sono piu' qui: la chiave del
provider (``provider/update``, ``provider-models``), il token Telegram
(``telegram/save``) e la password SSH (``ssh/host/save``) viaggiavano nella
query, cioe' nella riga di richiesta che log e traceback vedono.
Sono comandi dell'RPC WebSocket in ``webui/commands.py``
(``settings.provider.models``/``update``, ``telegram.save``, ``ssh.host.save``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.bus.queue import MessageBus
from jenny.channels.http_utils import QueryParams, parse_flag, query_first
from jenny.webui.phone_only import refuse_if_remote
from jenny.webui.settings_api import (
    WebUISettingsError,
    delete_provider,
    power_diagnostics_payload,
    run_update_check,
    settings_payload,
    start_update_install,
    update_agent_settings,
    update_floating_settings,
    update_location_settings,
    update_power_settings,
    update_status_payload,
    update_web_search_settings,
)
from jenny.webui.ssh_api import (
    accept_ssh_host_key,
    delete_ssh_host,
    generate_ssh_key,
    probe_ssh_host_key,
    ssh_settings_payload,
    update_ssh_settings,
)
from jenny.webui.worker_settings import (
    GARDENER_REARM_KEYS,
    MEMORY_REARM_KEYS,
    update_memory_settings,
    update_worker_settings,
)


async def _enrich_floating(payload: dict) -> None:
    """Aggiunge ``floating.active`` al payload delle impostazioni.

    Fuori da Android la sezione dichiara già ``available: false`` e non c'è
    niente da chiedere: si esce senza toccare il bridge. Un errore qui non deve
    costare la pagina intera — ``floating_active`` non solleva, ma la guardia
    resta perché questa funzione sta nel percorso di **lettura** di tutte le
    impostazioni, non solo di quella riga.
    """
    section = payload.get("floating")
    if not isinstance(section, dict) or not section.get("available"):
        return
    from jenny.runtime.floating import floating_active
    from jenny.runtime.screen import screen_state

    section["active"] = await floating_active()
    # La lettura dello schermo sta sotto la mascotte perché vive di lei: si
    # legge solo al tocco sulla finestra flottante. «on», «off» (servizio
    # spento in Accessibilità) o «unsupported» (Android sotto il 14).
    section["screen"] = await screen_state()


class WebUISettingsRouter:
    """Route WebUI Settings HTTP requests behind a transport-neutral boundary."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        logger: Any,
        check_api_token: Callable[[WsRequest], bool],
        parse_query: Callable[[str], QueryParams],
        json_response: Callable[[dict[str, Any]], Response],
        error_response: Callable[[int, str | None], Response],
        on_settings_changed: Callable[[], None] | None = None,
        on_telegram_changed: Callable[[], None] | None = None,
        on_jobs_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.bus = bus
        self.logger = logger
        self._check_api_token = check_api_token
        self._parse_query = parse_query
        self._json_response = json_response
        self._error_response = error_response
        self._on_settings_changed = on_settings_changed
        self._on_telegram_changed = on_telegram_changed
        # Secondo gancio, e non un allargamento del primo:
        # ``on_settings_changed`` ricostruisce provider e modello, questo ri-arma
        # un job del cron. Chi cambia l'intervallo del giardiniere non deve far
        # ricostruire il provider, e chi cambia il modello non deve far ripartire
        # gli orologi.
        self._on_jobs_changed = on_jobs_changed

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/settings":
            return await self._handle_settings(request)
        if path == "/api/settings/update":
            return await self._handle_settings_update(request)
        if path == "/api/settings/provider/delete":
            return await self._handle_settings_provider_delete(request)
        if path == "/api/settings/memory/update":
            return await self._handle_settings_memory_update(request)
        if path == "/api/settings/workers/update":
            return await self._handle_settings_workers_update(request)
        if path == "/api/settings/web-search/update":
            return await self._handle_settings_web_search_update(request)
        if path == "/api/settings/location/update":
            return await self._handle_settings_location_update(request)
        if path == "/api/settings/floating/update":
            return await self._handle_settings_floating_update(request)
        if path == "/api/settings/floating/screen-settings":
            return await self._handle_screen_settings_open(request)
        if path == "/api/settings/floating/permission-settings":
            return await self._handle_overlay_permission_open(request)
        if path == "/api/settings/power/update":
            return await self._handle_settings_power_update(request)
        if path == "/api/settings/power/diagnostics":
            return await self._handle_settings_power_diagnostics(request)
        if path == "/api/settings/ssh":
            return self._handle_ssh_settings(request)
        if path == "/api/settings/ssh/update":
            return await self._handle_mutation(request, update_ssh_settings, "ssh settings update")
        if path == "/api/settings/ssh/host/delete":
            return await self._handle_mutation(request, delete_ssh_host, "ssh host delete")
        if path == "/api/settings/ssh/key/generate":
            return await self._handle_mutation(request, generate_ssh_key, "ssh key generation")
        if path == "/api/settings/ssh/host-key/probe":
            return await self._handle_mutation(request, probe_ssh_host_key, "ssh host key probe")
        if path == "/api/settings/ssh/host-key/accept":
            return await self._handle_mutation(request, accept_ssh_host_key, "ssh host key accept")
        if path == "/api/updates/check":
            return await self._handle_update_check(request)
        if path == "/api/updates/install":
            return await self._handle_update_install(request)
        if path == "/api/updates/status":
            return self._handle_update_status(request)
        if path == "/api/telegram/status":
            return self._handle_telegram_status(request)
        if path == "/api/telegram/unpair":
            return await self._handle_telegram_unpair(request)
        if path == "/api/telegram/update":
            return await self._handle_telegram_enabled(request)
        return None

    def _query(self, request: WsRequest) -> QueryParams:
        return self._parse_query(request.path)

    def _authorized(self, request: WsRequest) -> bool:
        return self._check_api_token(request)

    def _unauthorized(self) -> Response:
        return self._error_response(401, "Unauthorized")

    def _fire(self, callback: Callable[..., None] | None, what: str, *args: Any) -> None:
        """Un gancio dopo un salvataggio riuscito; il suo guasto finisce nel log."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            self.logger.exception("{} callback failed {}", what, args or "")

    def _fire_settings_changed(self) -> None:
        self._fire(self._on_settings_changed, "on_settings_changed")

    def _fire_jobs_changed(self, worker: str) -> None:
        self._fire(self._on_jobs_changed, "on_jobs_changed", worker)

    async def _handle_settings(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        payload = settings_payload()
        # ``enabled`` dice cosa ha chiesto l'utente, ``active`` se Android
        # gliela lascia aprire. Va chiesto alla finestra a **ogni** lettura e
        # non solo dopo un tocco dell'interruttore: il permesso si revoca da
        # una schermata di sistema, fuori da qui, e la riga che lo spiega deve
        # comparire tutte le volte che è vera. Asincrono perché la risposta
        # attraversa Chaquopy; il costo è una chiamata per apertura del
        # pannello.
        await _enrich_floating(payload)
        return self._json_response(payload)

    async def _handle_settings_update(self, request: WsRequest) -> Response:
        # Si chiama a ogni salvataggio riuscito, senza guardare *quali* campi
        # sono arrivati. Qui c'era un elenco di nomi tenuto a mano
        # (``_GENERATION_KEYS``) e gli mancava gia' ``context_window_tokens``:
        # la rotta lo accetta e lo scrive, ma non essendo nell'elenco il gancio
        # non partiva — config giusto su disco, «Saved!» nella UI, e l'agente
        # vivo che continuava con la finestra vecchia. Lo stesso difetto della
        # #12 un piano piu' su, e lo stesso di prima ancora con i parametri di
        # generazione.
        #
        # L'elenco non si allunga, si cancella: quel sapere esiste gia',
        # completo sullo schema e testato, in ``provider_fingerprint``. Chiamare
        # sempre costa una ``load_config()`` e un ``model_dump()`` su un file
        # che ``store.mutate()`` ha appena riscritto, perche' la guardia esce
        # *prima* di costruire il provider quando non e' cambiato niente —
        # ritorno anticipato aggiunto col fix della #12, ed e' cio' che rende
        # sicuro questo "sempre".
        return await self._handle_mutation(
            request,
            update_agent_settings,
            "settings update",
            on_success=lambda query, payload: self._fire_settings_changed(),
        )

    async def _handle_settings_memory_update(self, request: WsRequest) -> Response:
        """Le manopole di Dream e i tetti della memoria lunga."""
        def after(query: QueryParams, payload: dict[str, Any]) -> None:
            # Presenza della chiave e non cambio del valore: v. il commento su
            # ``MEMORY_REARM_KEYS``. Ri-armare a pianificazione identica non
            # sposta la prossima scadenza; perdersi una transizione di
            # ``enabled`` sì.
            if any(key in query for key in MEMORY_REARM_KEYS):
                self._fire_jobs_changed("dream")

        return await self._handle_mutation(
            request, update_memory_settings, "memory settings update", on_success=after,
        )

    async def _handle_settings_workers_update(self, request: WsRequest) -> Response:
        """Il giardiniere e la compattazione delle chat di progetto."""
        def after(query: QueryParams, payload: dict[str, Any]) -> None:
            if any(key in query for key in GARDENER_REARM_KEYS):
                self._fire_jobs_changed("gardener")

        return await self._handle_mutation(
            request, update_worker_settings, "worker settings update", on_success=after,
        )

    async def _handle_settings_provider_delete(self, request: WsRequest) -> Response:
        async def handler(query: QueryParams) -> dict[str, Any]:
            return await delete_provider({"name": _query_param(query, "name")})

        # ``on_success`` gira solo dopo una cancellazione riuscita, quindi un
        # nome c'era per forza: la condizione qui non decideva niente.
        return await self._handle_mutation(
            request,
            handler,
            "provider delete",
            on_success=lambda query, payload: self._fire_settings_changed(),
        )

    async def _handle_settings_web_search_update(self, request: WsRequest) -> Response:
        return await self._handle_mutation(
            request, update_web_search_settings, "web search settings update",
            failure="failed to update web search settings",
        )

    async def _handle_settings_location_update(self, request: WsRequest) -> Response:
        return await self._handle_mutation(
            request, update_location_settings, "location settings update",
            failure="failed to update location settings",
        )

    async def _handle_settings_floating_update(self, request: WsRequest) -> Response:
        # Nessun _fire_settings_changed: la mascotte non è un pezzo dell'agente
        # da ricostruire a caldo, ed è già stata applicata al bridge dentro
        # ``update_floating_settings``. Niente requires_restart, per la stessa
        # ragione: quella riga esiste apposta perché non ce ne sia bisogno.
        return await self._handle_mutation(
            request, update_floating_settings, "floating settings update",
            failure="failed to update floating settings", pre=refuse_if_remote,
        )

    async def _handle_screen_settings_open(self, request: WsRequest) -> Response:
        """Apre Impostazioni → Accessibilità, dove si accende la lettura dello
        schermo. Un'app non può accendere da sé un servizio di accessibilità:
        il massimo che può fare è portare l'utente sulla pagina giusta."""
        if not self._authorized(request):
            return self._unauthorized()
        if (refused := refuse_if_remote()) is not None:
            return refused
        from jenny.runtime.screen import open_screen_settings

        return self._json_response({"opened": await open_screen_settings()})

    async def _handle_overlay_permission_open(self, request: WsRequest) -> Response:
        """Apre la pagina di sistema del permesso «Mostra sopra altre app»: come
        per l'Accessibilità, un'app non se lo concede da sé, e il massimo è
        portare l'utente sull'interruttore giusto."""
        if not self._authorized(request):
            return self._unauthorized()
        if (refused := refuse_if_remote()) is not None:
            return refused
        from jenny.runtime.floating import open_permission_settings

        return self._json_response({"opened": await open_permission_settings()})

    async def _handle_settings_power_update(self, request: WsRequest) -> Response:
        # Nessun _fire_settings_changed: il wakelock di servizio si prende
        # all'avvio del gateway e non c'è niente da ricostruire a caldo. La
        # risposta porta requires_restart, la UI lo dice a parole.
        return await self._handle_mutation(
            request, update_power_settings, "power settings update",
            failure="failed to update power settings",
        )

    async def _handle_settings_power_diagnostics(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await power_diagnostics_payload()
        except Exception:
            self.logger.exception("power diagnostics failed")
            return self._error_response(500, "failed to read power diagnostics")
        return self._json_response(payload)

    # -- Aggiornamenti ------------------------------------------------------ #

    async def _handle_update_check(self, request: WsRequest) -> Response:
        """Controllo aggiornamenti forzato dalle impostazioni.

        In GET come ogni scrittura di questa WebUI (v. ``_handle_update_install``
        per il perché). È l'unica strada che l'utente ha per sapere se il
        meccanismo è ancora vivo: il job periodico gira ogni ventiquattr'ore e
        i suoi fallimenti finiscono solo nel log, che su un telefono non legge
        nessuno. La protezione contro le chiamate ripetute sta in
        ``run_update_check``, che è dove vive il lock.
        """
        def after(query: QueryParams, payload: dict[str, Any]) -> None:
            version = payload.get("version") or {}
            self.logger.info(
                "[updates] manual check: status={!r} available={} latest={!r}",
                payload.get("status"),
                version.get("update_available"),
                version.get("latest"),
            )

        return await self._handle_mutation(
            request, lambda _query: run_update_check(), "manual update check",
            failure="failed to run the update check", on_success=after,
        )

    async def _handle_update_install(self, request: WsRequest) -> Response:
        """Avvia l'installazione dell'update annunciato nel payload versione.

        Il dispatch è per path, non per metodo, come per tutte le scritture di
        questa WebUI: il server HTTP è quello di ``websockets``, che rifiuta
        qualunque metodo diverso da GET prima ancora di arrivare qui. La UI
        chiama quindi in GET; se un giorno il trasporto accettasse POST, questa
        route lo servirebbe senza modifiche.
        """
        def after(query: QueryParams, payload: dict[str, Any]) -> None:
            # Loggato anche quando va bene: un'installazione è l'unica azione
            # della WebUI che si porta via il processo, e senza questa riga il
            # log si interrompe senza spiegare perché.
            self.logger.info(
                "[updates] install requested: ok={} state={!r} detail={!r}",
                payload.get("ok"), payload.get("state"), payload.get("detail"),
            )

        return await self._handle_mutation(
            request, lambda _query: start_update_install(), "update install start",
            failure="failed to start the update installation", pre=refuse_if_remote,
            on_success=after,
        )

    def _handle_update_status(self, request: WsRequest) -> Response:
        """Fase e progresso dell'installazione, per il polling della UI."""
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_status_payload()
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("update status failed")
            return self._error_response(500, "failed to read the update status")
        return self._json_response(payload)

    # -- SSH ---------------------------------------------------------------- #

    def _handle_ssh_settings(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            return self._json_response(ssh_settings_payload())
        except Exception:
            self.logger.exception("failed to load ssh settings")
            return self._error_response(500, "failed to load ssh settings")

    async def _handle_mutation(
        self,
        request: WsRequest,
        handler: Callable[[QueryParams], Awaitable[dict[str, Any]]],
        what: str,
        *,
        on_success: Callable[[QueryParams, dict[str, Any]], None] | None = None,
        failure: str | None = None,
        pre: Callable[[], Response | None] | None = None,
    ) -> Response:
        """Tronco comune delle route che scrivono: auth, errori applicativi, 500 muto.

        Una sola funzione perché le route differiscono *solo* per il gestore e
        per cosa fanno dopo: duplicare il blocco try/except è il modo più facile
        per lasciarne una che fa trapelare il messaggio di un'eccezione
        inattesa nel corpo della risposta. Le route SSH lo usavano già; quelle
        dei settings lo ri-scrivevano a mano senza l'ultimo ``except``, e sono
        proprio quelle che chiamano ``store.mutate()``, cioè il disco.

        ``on_success`` gira **solo** dopo un salvataggio riuscito e riceve la
        query e il payload: è lì che vivono i rearm dei job e i rebuild del
        provider, che non devono partire se la scrittura è fallita.

        ``pre`` gira dopo l'autenticazione e prima del gestore, e una sua risposta
        chiude lì: è ``refuse_if_remote`` per le route che toccano il telefono.
        ``failure`` è il testo del 500, se non è «*what* failed».
        """
        if not self._authorized(request):
            return self._unauthorized()
        if pre is not None and (refused := pre()) is not None:
            return refused
        query = self._query(request)
        try:
            payload = await handler(query)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("{} failed", what)
            return self._error_response(500, failure or f"{what} failed")
        if on_success is not None:
            on_success(query, payload)
        return self._json_response(payload)

    # -- Telegram ---------------------------------------------------------- #

    def _fire_telegram_changed(self) -> None:
        """Applica la config Telegram a caldo (ricrea/ferma il canale)."""
        self._fire(self._on_telegram_changed, "on_telegram_changed")

    def _handle_telegram_status(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        from jenny.webui.telegram_api import telegram_status_payload

        try:
            return self._json_response(telegram_status_payload())
        except Exception:
            self.logger.exception("telegram status failed")
            return self._error_response(500, "failed to load telegram status")

    async def _handle_telegram_unpair(self, request: WsRequest) -> Response:
        from jenny.webui.telegram_api import unpair_telegram

        return await self._handle_mutation(
            request, lambda _query: unpair_telegram(), "telegram unpair",
            failure="failed to unpair telegram",
            on_success=lambda query, payload: self._fire_telegram_changed(),
        )

    async def _handle_telegram_enabled(self, request: WsRequest) -> Response:
        from jenny.webui.telegram_api import set_telegram_enabled

        # ``parse_flag`` e' vero solo se il valore e' dichiarato vero, quindi un
        # parametro assente o storto spegne. Va bene per un toggle — il client
        # manda sempre ``true``/``false`` esplicito — ed e' il verso prudente:
        # il caso ambiguo lascia il canale fermo, non lo accende.
        return await self._handle_mutation(
            request,
            lambda query: set_telegram_enabled(parse_flag(_query_param(query, "enabled"))),
            "telegram enabled toggle",
            failure="failed to update telegram channel",
            on_success=lambda query, payload: self._fire_telegram_changed(),
        )


def _query_param(query: QueryParams, key: str) -> str:
    return query_first(query, key) or ""
