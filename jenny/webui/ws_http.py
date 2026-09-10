"""HTTP API handler extracted from WebSocketChannel.

Handles all non-WebSocket HTTP routes: bootstrap, sessions, settings,
media, commands, sidebar state, static file serving, and token management.

Also houses shared HTTP utility functions used by both this module and
``websocket.py`` to avoid circular imports.
"""

from __future__ import annotations

import datetime
import json
import mimetypes
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import (
    case_insensitive_header as _case_insensitive_header,
)
from jenny.channels.http_utils import (
    host_for_url as _host_for_url,
)
from jenny.channels.http_utils import (
    http_error as _http_error,
)
from jenny.channels.http_utils import (
    http_json_response as _http_json_response,
)
from jenny.channels.http_utils import (
    http_response as _http_response,
)
from jenny.channels.http_utils import (
    is_localhost as _is_localhost,
)
from jenny.channels.http_utils import (
    issue_route_secret_matches as _issue_route_secret_matches,
)
from jenny.channels.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from jenny.channels.http_utils import (
    parse_query as _parse_query,
)
from jenny.channels.http_utils import (
    parse_request_path as _parse_request_path,
)
from jenny.channels.http_utils import (
    query_first as _query_first,
)
from jenny.channels.http_utils import (
    redact_query_secrets as _redact_query_secrets,
)
from jenny.channels.http_utils import (
    safe_host_header as _safe_host_header,
)
from jenny.config.paths import get_workspace_path
from jenny.session.keys import UNIFIED_SESSION_KEY, is_project_session_key
from jenny.session.webui_turns import websocket_turn_wall_started_at
from jenny.webui.android_apps_api import (
    launch_android_app,
    open_android_app_info,
    uninstall_android_app,
    webui_android_apps_payload,
)
from jenny.webui.file_preview import WebUIFilePreviewError, file_preview_payload
from jenny.webui.hidden_android_apps import (
    read_hidden_android_apps,
    write_hidden_android_apps,
)
from jenny.webui.media_gateway import WebUIMediaGateway
from jenny.webui.transcript import build_webui_thread_response
from jenny.webui.workspaces import WebUIWorkspaceController

_SLOW_WEBUI_HTTP_LOG_MS = 1_000

if TYPE_CHECKING:
    from jenny.bus.queue import MessageBus
    from jenny.session.manager import SessionManager


def _decode_api_key(raw_key: str) -> str | None:
    key = unquote(raw_key)
    _api_key_re = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
    if _api_key_re.match(key) is None:
        return None
    return key


_ANDROID_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.]{1,255}$")

def _default_model_name_from_config() -> str | None:
    try:
        from jenny.config.loader import load_config
        model = load_config().agents.defaults.model.strip()
        return model or None
    except Exception as e:
        logger.debug("bootstrap model_name could not load from config: {}", e)
        return None


def _default_provider_name_from_config() -> str | None:
    """Nome config del provider attivo (stesso valore stampato dalla factory).

    A runtime il provider cambia solo via settings, che riscrivono la config su
    disco: leggerla qui è sempre allineato allo stato vivo (i preset scambiano
    modello/routing, mai il provider attivo).
    """
    try:
        from jenny.config.loader import load_config
        name = load_config().get_active_provider().name.strip()
        return name or None
    except Exception as e:
        logger.debug("bootstrap provider could not load from config: {}", e)
        return None


def _resolve_bootstrap_model_name(
    runtime_name: Callable[[], str | None] | None,
) -> str:
    if runtime_name is not None:
        try:
            raw = runtime_name()
        except Exception as e:
            logger.debug("bootstrap runtime model resolver failed: {}", e)
        else:
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped:
                    return stripped
    return _default_model_name_from_config() or ""


def _json_safe(value):
    """Recursively convert non-serializable types (e.g. datetime.date) to ISO strings."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# GatewayHTTPHandler
# ---------------------------------------------------------------------------


class GatewayHTTPHandler:
    """Handles all HTTP routes served alongside the WebSocket endpoint.

    Routes HTTP requests and delegates stateful work to explicit gateway
    services owned by the composition layer.
    """

    def __init__(
        self,
        *,
        config: Any,  # WebSocketConfig
        session_manager: SessionManager | None,
        runtime_model_name: Callable[[], str | None] | None,
        bus: MessageBus,
        media: WebUIMediaGateway,
        workspaces: WebUIWorkspaceController,
        skills_workspace_path: Path,
        disabled_skills: set[str] | None = None,
        snapshot_service: Any | None = None,
        get_subagent_manager: Callable[[], Any | None] | None = None,
        get_cron_service: Callable[[], Any | None] | None = None,
        log: Any = logger,
        onboarding_event: Any | None = None,
        on_settings_changed: Callable[[], None] | None = None,
        on_telegram_changed: Callable[[], None] | None = None,
        on_jobs_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.session_manager = session_manager
        self.static_dist_path = (get_workspace_path() / "ui").resolve()
        # Integrità della SPA a runtime: la dir servita ``workspace/ui`` è
        # scrivibile dai tool dell'agente e viene riallineata al package solo
        # dalla sync all'avvio del gateway. Un reload "a caldo" della WebView
        # (senza restart del processo → senza sync) riservirebbe altrimenti una
        # copia manomessa. Per il contenuto attivo (HTML/JS/CSS) serviamo i byte
        # canonici del package, mai il disco. Cache lazy (una lettura per file).
        from jenny.utils.android_assets import _UI_MANIFEST

        self._ui_manifest_set = frozenset(_UI_MANIFEST)
        self._canonical_ui_cache: dict[str, bytes | None] = {}
        self.runtime_model_name = runtime_model_name
        self.bus = bus
        self.media = media
        self.workspaces = workspaces
        self.skills_workspace_path = skills_workspace_path
        self.disabled_skills = disabled_skills or set()
        self._log = log

        from jenny.webui.settings_routes import WebUISettingsRouter

        self.settings_routes = WebUISettingsRouter(
            bus=bus,
            logger=self._log,
            check_api_token=self.check_api_secret,
            parse_query=_parse_query,
            json_response=_http_json_response,
            error_response=_http_error,
            session_manager=session_manager,
            onboarding_event=onboarding_event,
            on_settings_changed=on_settings_changed,
            on_telegram_changed=on_telegram_changed,
            on_jobs_changed=on_jobs_changed,
        )

        from jenny.webui.skills_routes import SkillsRoutes
        from jenny.webui.wiki_routes import WikiRoutes

        self.skills_routes = SkillsRoutes(
            check_api_token=self.check_api_secret,
            json_response=_http_json_response,
            error_response=_http_error,
            parse_query=_parse_query,
            query_first=_query_first,
            skills_workspace_path=self.skills_workspace_path,
            disabled_skills=self.disabled_skills,
        )
        # get_workspace_root passato come lambda (non bound method) così un
        # eventuale override/patch dell'attributo sull'handler è risolto a
        # call-time (i test lo patchano; e il composition root può cambiarlo).
        self.wiki_routes = WikiRoutes(
            check_api_token=self.check_api_secret,
            get_workspace_root=lambda: self._get_workspace_root(),
            json_safe=_json_safe,
        )

        from jenny.webui.workspace_routes import WorkspaceRoutes

        self.workspace_routes = WorkspaceRoutes(
            check_api_token=self.check_api_secret,
            get_workspace_root=lambda: self._get_workspace_root(),
        )

        from jenny.webui.apps_routes import AppsRoutes

        self.apps_routes = AppsRoutes(
            check_api_token=self.check_api_secret,
            get_workspace_root=lambda: self._get_workspace_root(),
            log=self._log,
        )

        from jenny.webui.backup_routes import BackupRoutes

        self._snapshot_service = snapshot_service
        self._backup_manager: Any | None = None
        self.backup_routes = BackupRoutes(
            check_api_token=self.check_api_secret,
            get_backup_manager=self._get_backup_manager,
            log=self._log,
        )

        from jenny.webui.cron_routes import CronRoutes

        # Getter late-binding come quello dei subagent: ``GatewayContainer.cron``
        # nasce ``None`` e la WebUI e' servita anche durante l'onboarding.
        self._get_cron_service = get_cron_service
        self.cron_routes = CronRoutes(
            check_api_token=self.check_api_secret,
            get_cron_service=lambda: (
                self._get_cron_service() if self._get_cron_service is not None else None
            ),
            log=self._log,
        )

        from jenny.webui.subagent_routes import SubagentRoutes

        # Getter late-binding: durante l'onboarding l'agente (e con lui il
        # SubagentManager) non esiste ancora, ma la WebUI è già servita.
        self._get_subagent_manager = get_subagent_manager
        self.subagent_routes = SubagentRoutes(
            check_api_token=self.check_api_secret,
            get_subagent_manager=self._resolve_subagent_manager,
            log=self._log,
        )

    def _resolve_subagent_manager(self) -> Any | None:
        if self._get_subagent_manager is None:
            return None
        return self._get_subagent_manager()

    def _get_backup_manager(self) -> Any | None:
        """Costruisce (una volta) il BackupManager sopra lo SnapshotService."""
        if self._snapshot_service is None:
            return None
        if self._backup_manager is None:
            from jenny.snapshot.backup import BackupManager

            self._backup_manager = BackupManager(self._snapshot_service)
        return self._backup_manager

    # -- Token management ---------------------------------------------------

    def check_api_secret(self, request: WsRequest) -> bool:
        from jenny.channels.http_utils import check_api_secret as _check

        return _check(request.headers, request.path, self.config.token_issue_secret.strip())

    # -- Main dispatch ------------------------------------------------------

    async def dispatch(self, connection: Any, request: WsRequest) -> Any | None:
        """Route an HTTP request. Returns Response or None."""
        got, _ = _parse_request_path(request.path)
        started = time.perf_counter()
        response: Any | None = None

        try:
            response = await self._dispatch_resolved(connection, request, got)
            return response
        finally:
            self._log_slow_http(got, response, started)

    async def _dispatch_resolved(
        self,
        connection: Any,
        request: WsRequest,
        got: str,
    ) -> Any | None:
        # Bootstrap
        if got == "/webui/bootstrap":
            return self._handle_bootstrap(connection, request)

        # Settings routes (delegated)
        response = await self.settings_routes.dispatch(request, got)
        if response is not None:
            return response

        # Session routes
        response = await self._dispatch_session_routes(request, got)
        if response is not None:
            return response

        # Media routes
        response = self._dispatch_media_routes(request, got)
        if response is not None:
            return response

        # Misc routes
        response = await self._dispatch_misc_routes(connection, request, got)
        if response is not None:
            return response

        # API 404 (never serve SPA for /api/ routes)
        if got.startswith("/api/"):
            return _http_error(404, "API route not found")

        # Jenny App static files (authed, no SPA fallback) — delegated
        apps_static = await self.apps_routes.dispatch(request, got)
        if apps_static is not None:
            return apps_static

        # Static SPA serving
        if self.static_dist_path is not None:
            response = self._serve_static(got)
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    def _to_core_session_key(self, decoded_key: str) -> str:
        """Translate a WebUI-facing session key to the core session key.

        The agent loop routes all messages through ``unified:default``, but the
        WebSocket channel still exposes ``websocket:default`` to clients.  API
        callers use ``websocket:default``; this method maps it to the actual
        file key.
        """
        if decoded_key == "websocket:default":
            return UNIFIED_SESSION_KEY
        return decoded_key

    def _log_slow_http(self, path: str, response: Any | None, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms < _SLOW_WEBUI_HTTP_LOG_MS:
            return
        if not (path.startswith("/api/") or path == "/webui/bootstrap"):
            return
        status = getattr(response, "status_code", None)
        self._log.warning(
            "slow webui http route path={} status={} duration_ms={}",
            _redact_query_secrets(path),
            status if status is not None else "none",
            elapsed_ms,
        )

    # -- Token issue --------------------------------------------------------

    # -- Bootstrap ----------------------------------------------------------

    def _handle_bootstrap(self, connection: Any, request: Any) -> Response:
        secret = self.config.token_issue_secret.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return _http_error(401, "Unauthorized")
        elif not _is_localhost(connection):
            return _http_error(403, "bootstrap requires a token issue secret")

        ws_url = self._bootstrap_ws_url(request)
        expected_path = _normalize_config_path(self.config.path)
        return _http_json_response(
            {
                "ws_path": expected_path,
                "ws_url": ws_url,
                "model_name": _resolve_bootstrap_model_name(self.runtime_model_name),
                "provider": _default_provider_name_from_config() or "",
            }
        )



    def _bootstrap_ws_url(self, request: Any) -> str:
        headers = getattr(request, "headers", {}) or {}
        host = _safe_host_header(_case_insensitive_header(headers, "Host"))
        if not host:
            host = _host_for_url(self.config.host, self.config.port)
        proto = _case_insensitive_header(headers, "X-Forwarded-Proto")
        proto = proto.split(",", 1)[0].strip().lower()
        secure = proto in {"https", "wss"} or bool(self.config.ssl_certfile.strip())
        scheme = "wss" if secure else "ws"
        expected_path = _normalize_config_path(self.config.path)
        return f"{scheme}://{host}{expected_path}"

    # -- Session routes -----------------------------------------------------

    async def _dispatch_session_routes(self, request: WsRequest, got: str) -> Response | None:
        m = re.match(r"^/api/sessions/([^/]+)/webui-thread$", got)
        if m:
            return self._handle_webui_thread_get(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-preview$", got)
        if m:
            return self._handle_file_preview(request, m.group(1))

        return None

    def _handle_webui_thread_get(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_webui_readable_session_key(decoded_key):
            return _http_error(404, "session not found")
        core_key = self._to_core_session_key(decoded_key)
        scope = self.workspaces.scope_for_session_key(core_key)
        session_messages: list[dict[str, Any]] | None = None
        if self.session_manager is not None:
            session_data = self.session_manager.read_session_file(core_key)
            raw_messages = session_data.get("messages") if isinstance(session_data, dict) else None
            if isinstance(raw_messages, list):
                session_messages = [m for m in raw_messages if isinstance(m, dict)]
        query = _parse_query(request.path)
        raw_limit = _query_first(query, "limit")
        limit: int | None = None
        if raw_limit is not None and raw_limit.strip():
            try:
                limit = int(raw_limit)
            except ValueError:
                return _http_error(400, "invalid limit")
        before = _query_first(query, "before")
        data = build_webui_thread_response(
            decoded_key,
            augment_user_media=self.media.augment_transcript_media,
            augment_assistant_media=self.media.augment_transcript_media,
            augment_assistant_text=lambda text: self.media.rewrite_local_markdown_images(
                text,
                workspace_path=scope.project_path,
            ),
            session_messages=session_messages,
            limit=limit,
            before=before,
            # Un progetto appena creato non ha ancora scambiato un messaggio, e
            # la sua chat deve aprirsi lo stesso: vuota, non con un 404.
            allow_empty=is_project_session_key(core_key),
        )
        if data is None:
            return _http_error(404, "webui thread not found")
        data["workspace_scope"] = scope.payload()
        started_at = websocket_turn_wall_started_at("default")
        if started_at is not None:
            data["run_started_at"] = started_at
        return _http_json_response(data)

    def _handle_file_preview(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_webui_readable_session_key(decoded_key):
            return _http_error(404, "session not found")
        core_key = self._to_core_session_key(decoded_key)
        path = _query_first(_parse_query(request.path), "path")
        try:
            payload = file_preview_payload(
                path,
                scope=self.workspaces.scope_for_session_key(core_key),
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    # -- Media routes -------------------------------------------------------

    def _dispatch_media_routes(self, request: WsRequest, got: str) -> Response | None:
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2), request)
        return None

    def _handle_media_fetch(
        self, sig: str, payload: str, request: WsRequest | None = None
    ) -> Response:
        return self.media.serve_signed_media(
            sig,
            payload,
            request=request,
        )

    # -- Misc routes --------------------------------------------------------

    async def _dispatch_misc_routes(
        self, connection: Any, request: WsRequest, got: str
    ) -> Response | None:
        skills_response = self.skills_routes.dispatch(request, got)
        if skills_response is not None:
            return skills_response
        if got == "/api/webui/android-apps":
            return await self._handle_webui_android_apps(request)
        m = re.match(r"^/api/webui/android-apps/([^/]+)/launch$", got)
        if m:
            return await self._handle_webui_android_app_launch(request, m.group(1))
        m = re.match(r"^/api/webui/android-apps/([^/]+)/uninstall$", got)
        if m:
            return await self._handle_webui_android_app_uninstall(request, m.group(1))
        m = re.match(r"^/api/webui/android-apps/([^/]+)/app-info$", got)
        if m:
            return await self._handle_webui_android_app_info(request, m.group(1))
        if got == "/api/webui/commands":
            return self._handle_webui_commands(request)
        if got == "/api/webui/hidden-apps":
            return self._handle_webui_hidden_apps(request)
        if got == "/api/webui/hidden-apps/update":
            return self._handle_webui_hidden_apps_update(request)
        if got == "/api/client-log":
            return self._handle_client_log(request)
        apps_response = await self.apps_routes.dispatch(request, got)
        if apps_response is not None:
            return apps_response

        backup_response = await self.backup_routes.dispatch(request, got)
        if backup_response is not None:
            return backup_response

        # Stato della programmazione (delegato a CronRoutes)
        cron_response = await self.cron_routes.dispatch(request, got)
        if cron_response is not None:
            return cron_response

        # Stato/controlli dei subagent (delegato a SubagentRoutes)
        subagent_response = await self.subagent_routes.dispatch(request, got)
        if subagent_response is not None:
            return subagent_response

        # Wiki + audit routes (delegated to WikiRoutes)
        wiki_response = await self.wiki_routes.dispatch(request, got)
        if wiki_response is not None:
            return wiki_response

        # Workspace CRUD routes (delegated to WorkspaceRoutes)
        workspace_response = await self.workspace_routes.dispatch(request, got)
        if workspace_response is not None:
            return workspace_response

        return None

    async def _handle_webui_android_apps(self, request: WsRequest) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(await webui_android_apps_payload())

    async def _handle_webui_android_app_launch(self, request: WsRequest, raw_package: str) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        package = unquote(raw_package)
        if not package or _ANDROID_PACKAGE_RE.match(package) is None:
            return _http_error(400, "invalid package name")
        launched = await launch_android_app(package)
        if not launched:
            return _http_error(404, "app not found or launch failed")
        return _http_json_response({"launched": True})

    async def _handle_webui_android_app_uninstall(
        self, request: WsRequest, raw_package: str
    ) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        package = unquote(raw_package)
        if not package or _ANDROID_PACKAGE_RE.match(package) is None:
            return _http_error(400, "invalid package name")
        ok = await uninstall_android_app(package)
        return _http_json_response({"ok": ok})

    async def _handle_webui_android_app_info(
        self, request: WsRequest, raw_package: str
    ) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        package = unquote(raw_package)
        if not package or _ANDROID_PACKAGE_RE.match(package) is None:
            return _http_error(400, "invalid package name")
        ok = await open_android_app_info(package)
        return _http_json_response({"ok": ok})

    def _handle_client_log(self, request: WsRequest) -> Response:
        """Riporta errori/anomalie del WebUI nel log del gateway.

        Il WebView logga gli errori JS solo nella console chromium (visibile
        soltanto via adb con tag separato): senza questo canale un errore
        client-side è invisibile nei log Python del gateway. Best-effort e
        advisory: input troncato, livelli whitelisted, risponde sempre 200.
        """
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        level = _query_first(query, "level")
        if level not in ("warning", "error"):
            level = "error"
        # ``_query_first`` restituisce None se il parametro manca, e affettare
        # None solleva TypeError: una richiesta senza ``source`` o ``message``
        # faceva fallire con 500 la rotta che nel docstring qui sopra promette
        # di rispondere sempre 200 — e per giunta è la rotta che esiste per
        # *segnalare* i guasti del client. ``level`` aveva già il suo default;
        # questi due no.
        source = (_query_first(query, "source") or "")[:100] or "unknown"
        message = (_query_first(query, "message") or "")[:800]
        logger.log(
            level.upper(),
            "[webui-client] source={} {}",
            source,
            message,
        )
        return _http_json_response({"ok": True})

    def _handle_webui_commands(self, request: WsRequest) -> Response:
        """I comandi slash **di questa conversazione**, per la tendina del composer.

        La tabella e' ``command/specs.py::BUILTIN_COMMAND_SPECS``, cioe' la
        stessa che compone ``/help``: la WebUI non tiene un secondo elenco. Su
        ``api`` e non su ``rpc`` perche' e' una lettura (la divisione sta in
        testa a ``assets/shared/rpc-client.js``).

        **Il filtro e' qui e non nel client** (31/08/2026). La tendina teneva due
        righe di `if (spec.scope === 'project' && !inProject) continue`, cioe' una
        seconda copia della regola nel posto che quel modulo esiste per non
        avere; e un filtro lato client e' comunque solo cosmetica — non c'e'
        autocomplete sullo ``/``, quindi la regola vera e' il cancello del
        dispatch (:mod:`jenny.command.scope`). Servendo l'elenco giusto, le due
        superfici non possono divergere.

        Senza ``key`` si elencano tutti: e' "scope non noto", non "chat
        personale". Un client vecchio che non lo manda continua a vedere quel che
        vedeva, e il dispatch lo fermerebbe comunque.

        Sono i comandi *predefiniti* e non "quelli registrati nel router": due
        di essi (``/tidy``, ``/init``) non passano dal router perche' si
        espandono nel turno (v. ``agent/loop.py::PROJECT_INIT_COMMAND``), e sono
        proprio i due che l'utente ha meno modo di scoprire da solo.
        """
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        from jenny.command.scope import visible_specs

        raw_key = _query_first(_parse_query(request.path), "key")
        session_key: str | None = None
        if raw_key:
            decoded = _decode_api_key(raw_key)
            if decoded is None:
                return _http_error(400, "invalid session key")
            session_key = self._to_core_session_key(decoded)

        return _http_json_response(
            {"commands": [spec.as_dict() for spec in visible_specs(session_key)]}
        )

    def _handle_webui_hidden_apps(self, request: WsRequest) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(read_hidden_android_apps())

    def _handle_webui_hidden_apps_update(self, request: WsRequest) -> Response:
        if not self.check_api_secret(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        raw_state = _query_first(query, "state")
        if raw_state is None:
            return _http_error(400, "missing state")
        try:
            decoded = json.loads(raw_state)
        except json.JSONDecodeError:
            return _http_error(400, "state must be JSON")
        if not isinstance(decoded, dict):
            return _http_error(400, "state must be an object")
        try:
            state = write_hidden_android_apps(decoded)
        except ValueError as e:
            return _http_error(400, str(e))
        except OSError:
            self._log.exception("failed to write hidden android apps state")
            return _http_error(500, "failed to write hidden apps state")
        return _http_json_response(state)

    # -- Wiki routes --------------------------------------------------------

    def _get_workspace_root(self) -> Path:
        """Get workspace root directory."""
        from jenny.config.paths import get_workspace_path
        return get_workspace_path()

    # -- Static file serving ------------------------------------------------

    # Tipi di contenuto "attivo" (esecuzione JS / rendering documento) per cui
    # l'integrità è security-relevant. Font e immagini non eseguono codice e
    # restano serviti dal disco.
    _CANONICAL_UI_SUFFIXES = (".html", ".js", ".css")

    def _canonical_ui_bytes(self, rel: str) -> bytes | None:
        """Byte canonici di un asset UI attivo letti dal package (fidati).

        Ritorna ``None`` quando ``rel`` non è nel manifest, non è un tipo
        attivo, o la lettura dal package fallisce: in tutti questi casi il
        chiamante ripiega sulla copia su disco. Vedi la nota in ``__init__``.
        """
        if not rel.endswith(self._CANONICAL_UI_SUFFIXES):
            return None
        if rel not in self._ui_manifest_set:
            return None
        if rel not in self._canonical_ui_cache:
            try:
                from jenny.utils.android_assets import read_asset

                self._canonical_ui_cache[rel] = read_asset("jenny.templates.ui", rel)
            except Exception:  # noqa: BLE001 - lettura best-effort, fallback al disco
                self._canonical_ui_cache[rel] = None
        return self._canonical_ui_cache[rel]

    def _serve_static(self, request_path: str) -> Response | None:
        assert self.static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        # Strip html-mobile/ prefix — JS imports use /html-mobile/assets/...
        # but files live at templates/ui/assets/...
        if rel.startswith("html-mobile/"):
            rel = rel[len("html-mobile/"):]
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        candidate = (self.static_dist_path / rel).resolve()
        try:
            candidate.relative_to(self.static_dist_path)
        except ValueError:
            return _http_error(403, "Forbidden")
        served_rel = rel
        if not candidate.is_file():
            # SPA fallback: percorsi sconosciuti servono la shell dell'app.
            # L'index.html canonico basta anche se la copia su disco manca.
            index = self.static_dist_path / "index.html"
            if not index.is_file() and self._canonical_ui_bytes("index.html") is None:
                return None
            candidate = index
            served_rel = "index.html"
        # Contenuto attivo (HTML/JS/CSS del manifest): sempre dai byte canonici
        # del package, mai dalla copia su disco scrivibile. Fallback al disco per
        # font/immagini e per eventuali file fuori manifest.
        body = self._canonical_ui_bytes(served_rel)
        if body is None:
            try:
                body = candidate.read_bytes()
            except OSError as e:
                self._log.warning("static: failed to read {}: {}", candidate, e)
                return _http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        if candidate.name == "index.html":
            cache = "no-cache"
        else:
            cache = "no-cache"
        # ACAO lets opaque-origin app iframes load shared kit assets (fonts
        # especially: @font-face fetches are CORS-mode). Public content, no
        # credentials, so "*" is safe.
        extra_headers = [
            ("Cache-Control", cache),
            ("Access-Control-Allow-Origin", "*"),
        ]
        # M1 (migliorie/webui.md): defense-in-depth CSP for the SPA shell,
        # ENFORCING dal 18 lug 2026 dopo smoke test pulito (era Report-Only).
        # Applied only to index.html (the document that hosts the SPA); the
        # individual assets don't need a page-level policy. The inline
        # <script> blocks were already extracted to assets/bootstrap.js so
        # script-src 'self' holds.
        if candidate.name == "index.html":
            extra_headers.append((
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "font-src 'self'; connect-src 'self' ws: wss:; "
                # La vista esterna di una Jenny App e' servita dal proxy su
                # loopback (``apps/proxy.py``) su una porta EFFIMERA, quindi e'
                # un'altra origine e non e' 'self'. Senza questa direttiva
                # ricadeva su ``default-src 'self'`` e la CSP della shell
                # bloccava il proxio della shell stessa:
                # ``net::ERR_BLOCKED_BY_CSP``, misurato sul device il 01/09/2026.
                # La porta non e' prevedibile qui (l'header si scrive servendo
                # index.html, prima che un proxy esista), da cui il carattere
                # jolly sulla sola porta.
                #
                # Cosa concede: la shell puo' incorniciare qualunque porta di
                # 127.0.0.1. La CSP qui e' defense-in-depth contro
                # un'iniezione nella SPA, e per un'iniezione le porte loopback
                # del telefono non sono un canale di esfiltrazione — sono sulla
                # stessa macchina su cui gia' gira. Le direttive che contano
                # contro quello scenario (``script-src``, ``connect-src``,
                # ``object-src``, ``base-uri``) restano intatte.
                "frame-src 'self' http://127.0.0.1:*; "
                "object-src 'none'; base-uri 'none'",
            ))
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=extra_headers,
        )


def _is_webui_readable_session_key(key: str) -> bool:
    """Le chiavi che la WebUI puo' chiedere: la conversazione, e i progetti.

    Non e' un filtro estetico, e' cio' che tiene fuori dalle route HTTP le
    sessioni interne (``cron:``, ``dream:``, ``subagent:``, ``heartbeat``): sono
    lavoro del sistema, non conversazioni, e non devono essere leggibili da qui.

    Si chiamava ``_is_websocket_channel_session_key`` e guardava il solo prefisso
    ``websocket:``. Con le sessioni-progetto quella forma avrebbe risposto **404
    a ogni progetto**, cioe' la chat di un progetto non si sarebbe potuta aprire.
    """
    return key.startswith("websocket:") or is_project_session_key(key)
