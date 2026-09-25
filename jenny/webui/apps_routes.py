"""Adapter di route HTTP per Jenny Apps (estratto da ws_http).

Gestisce lo static server delle app (``/apps/{slug}/...``), la lista
(``/api/webui/apps``) e l'esecuzione azioni (``/api/apps/{slug}/actions/{action}``).
Stesso pattern router di ``SkillsRoutes``/``WikiRoutes``/``WorkspaceRoutes``.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.apps.manifest import ACTION_NAME_RE
from jenny.apps.manifest import SLUG_RE as APP_SLUG_RE
from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    http_response,
    parse_query,
    query_first,
)

# Request-line budget is 8192 bytes (websockets); leave headroom for path+token.
APP_PARAMS_MAX_CHARS = 6000
# App iframes run with an opaque origin (sandbox without allow-same-origin):
# every action fetch is cross-origin, so responses need ACAO on ALL statuses.
# Auth stays the explicit ?token= param (never cookies), so "*" is safe.
APP_CORS_HEADERS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Cache-Control", "no-store"),
]


class AppsRoutes:
    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_workspace_root: Callable[[], Path],
        log: Any,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_workspace_root = get_workspace_root
        self._log = log
        # slug -> AppViewProxy vivo. Un solo proxy per app: riaprire la stessa
        # vista riusa il listener invece di lasciarne uno per apertura.
        #
        # Non c'e' un hook di shutdown nel container che li chiuda: i listener
        # sono nel processo del gateway e muoiono con lui, e per il caso in cui
        # la UI non mandi la chiusura c'e' l'idle timeout del proxy.
        self._view_proxies: dict[str, Any] = {}

    def _check_apps_enabled(self) -> Response | None:
        """Verifica ``config.apps.enabled``, fail-**closed**.

        Un ``except: pass`` faceva passare la richiesta quando ``load_config()``
        solleva, e questo gate protegge anche ``_static``, che serve byte
        arbitrari da ``workspace/apps/<slug>/app/``: un errore di config
        scavalcava in silenzio una feature dichiarata spenta. Il gemello in
        ``workspace_routes._require_workspace_flag`` risponde 503 nello stesso
        caso, con una docstring che spiega perché deve. Il token resta comunque
        richiesto, quindi era una falla di policy, non di autenticazione.
        """
        from jenny.config.loader import load_config

        try:
            enabled = load_config().apps.enabled
        except Exception:
            self._log.exception("apps gate: could not read config; refusing")
            return http_error(503, "apps are unavailable")
        if not enabled:
            return http_error(503, "apps are disabled")
        return None

    def _apps_config_values(self) -> tuple[float, int]:
        """I due limiti del runtime app, con i default che vengono dallo schema.

        I valori non si riscrivono a mano: duplicarli qui li fa divergere dallo
        schema alla prima modifica di uno dei due.
        """
        from jenny.config.loader import load_config
        from jenny.config.schema import AppsConfig

        try:
            apps = load_config().apps
        except Exception:
            apps = AppsConfig()
        return apps.http_timeout_s, apps.max_collection_bytes

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path.startswith("/apps/"):
            return self._static(request, path)
        if path == "/api/webui/apps":
            return self._list(request)
        m = re.match(r"^/api/webui/apps/([^/]+)/delete$", path)
        if m:
            return await self._delete(request, m.group(1))
        m = re.match(r"^/api/webui/apps/([^/]+)/view$", path)
        if m:
            return await self._view(request, m.group(1))
        m = re.match(r"^/api/webui/apps/([^/]+)/view/close$", path)
        if m:
            return await self._view_close(request, m.group(1))
        m = re.match(r"^/api/apps/([^/]+)/actions/([^/]+)$", path)
        if m:
            return await self._action(request, m.group(1), m.group(2))
        return None

    def _list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return disabled
        from jenny.webui.apps_api import list_apps_payload

        return http_json_response(list_apps_payload(self._get_workspace_root()))

    async def _delete(self, request: WsRequest, raw_slug: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return disabled
        slug = unquote(raw_slug)
        if not slug or APP_SLUG_RE.match(slug) is None:
            return http_error(400, "invalid app slug")
        from jenny.webui.apps_api import delete_app

        try:
            esito = delete_app(self._get_workspace_root(), slug)
        except ValueError:
            return http_error(400, "invalid app slug")
        except FileNotFoundError:
            return http_error(404, "app not found")
        except Exception as e:
            self._log.warning("app delete {} failed: {}", slug, e)
            return http_error(500, "internal error")
        from jenny.webui.casa_pages import detach_pages_quietly

        await detach_pages_quietly("app", slug, log=self._log)
        return http_json_response(esito)

    def _resolve_view_app(self, raw_slug: str) -> tuple[str, str] | Response:
        """``(slug, base_url)`` per una vista esterna, o il ``Response`` d'errore."""
        slug = unquote(raw_slug)
        if not slug or APP_SLUG_RE.match(slug) is None:
            return http_error(400, "invalid app slug")
        from jenny.apps.manifest import find_app

        app = find_app(self._get_workspace_root(), slug)
        if app is None:
            return http_error(404, "app not found")
        if app.broken or app.manifest is None:
            return http_error(409, f"app is broken: {app.error}")
        if app.manifest.view_kind != "external":
            return http_error(400, "app has no external view")
        if not app.manifest.server_base_url:
            # _parse_manifest lo garantisce per view.kind 'external'; qui e' per
            # il type checker e per il caso in cui quella regola cambi.
            return http_error(409, "external view without a server.baseUrl")
        return slug, app.manifest.server_base_url

    async def _view(self, request: WsRequest, raw_slug: str) -> Response:
        """Apre (o riusa) il proxy su loopback e torna l'URL d'ingresso.

        L'URL torna alla SPA, che lo incornicia. Non e' un redirect: la SPA deve
        vedere l'URL per montare l'iframe con il sandbox giusto (v. openApp).
        """
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return disabled
        resolved = self._resolve_view_app(raw_slug)
        if isinstance(resolved, Response):
            return resolved
        slug, base_url = resolved

        existing = self._view_proxies.get(slug)
        if existing is not None and existing.base_url == base_url:
            return http_json_response({"url": existing.entry_url}, extra_headers=APP_CORS_HEADERS)
        if existing is not None:
            # Il baseUrl e' cambiato sotto: il vecchio listener punta altrove.
            await existing.close()
            self._view_proxies.pop(slug, None)

        from jenny.apps.proxy import AppViewProxy, AppViewProxyError

        try:
            proxy = AppViewProxy(slug, base_url)
            url = await proxy.start()
        except AppViewProxyError as exc:
            return http_json_response({"ok": False, "error": str(exc)}, status=403,
                                      extra_headers=APP_CORS_HEADERS)
        except OSError as exc:
            self._log.warning("app view proxy for {} failed to bind: {}", slug, exc)
            return http_error(500, "internal error")
        self._view_proxies[slug] = proxy
        return http_json_response({"url": url}, extra_headers=APP_CORS_HEADERS)

    async def _view_close(self, request: WsRequest, raw_slug: str) -> Response:
        """Chiude il listener quando la UI chiude la vista."""
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        slug = unquote(raw_slug)
        if not slug or APP_SLUG_RE.match(slug) is None:
            return http_error(400, "invalid app slug")
        proxy = self._view_proxies.pop(slug, None)
        if proxy is not None:
            await proxy.close()
        return http_json_response({"ok": True}, extra_headers=APP_CORS_HEADERS)

    async def _action(self, request: WsRequest, raw_slug: str, raw_action: str) -> Response:
        def respond(payload: dict, status: int) -> Response:
            return http_json_response(payload, status=status, extra_headers=APP_CORS_HEADERS)

        if not self._check_api_token(request):
            return respond({"ok": False, "error": "Unauthorized"}, 401)
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return respond({"ok": False, "error": "apps are disabled"}, 503)

        slug = unquote(raw_slug)
        action = unquote(raw_action)
        if not slug or APP_SLUG_RE.match(slug) is None:
            return respond({"ok": False, "error": "invalid app slug"}, 400)
        if not action or ACTION_NAME_RE.match(action) is None:
            return respond({"ok": False, "error": "invalid action name"}, 400)

        raw_params = query_first(parse_query(request.path), "params") or ""
        if len(raw_params) > APP_PARAMS_MAX_CHARS:
            return respond(
                {"ok": False, "error": f"params exceed {APP_PARAMS_MAX_CHARS} chars"}, 413
            )

        from jenny.webui.apps_api import execute_app_action_payload

        http_timeout_s, max_collection_bytes = self._apps_config_values()
        try:
            payload, status = await execute_app_action_payload(
                self._get_workspace_root(),
                slug,
                action,
                raw_params,
                http_timeout_s=http_timeout_s,
                max_collection_bytes=max_collection_bytes,
            )
        except Exception as e:
            self._log.warning("app action {}/{} failed: {}", slug, action, e)
            return respond({"ok": False, "error": "internal error"}, 500)
        return respond(payload, status)

    def _static(self, request: WsRequest, got: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return disabled

        parts = got[len("/apps/") :].split("/", 1)
        slug = unquote(parts[0])
        rel = unquote(parts[1]) if len(parts) > 1 and parts[1] else "index.html"
        if not slug or APP_SLUG_RE.match(slug) is None:
            return http_error(400, "invalid app slug")
        if ".." in rel.split("/") or rel.startswith("/"):
            return http_error(403, "Forbidden")

        # Only the app/ subfolder is web-reachable: manifest, AGENT.md and
        # data/ stay off the wire — data is only accessible through actions.
        apps_root = (self._get_workspace_root() / "apps").resolve()
        candidate = (apps_root / slug / "app" / rel).resolve()
        try:
            candidate.relative_to(apps_root / slug / "app")
        except ValueError:
            return http_error(403, "Forbidden")
        if not candidate.is_file():
            return http_error(404, "Not Found")
        try:
            body = candidate.read_bytes()
        except OSError as e:
            self._log.warning("app static: failed to read {}: {}", candidate, e)
            return http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        return http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", "no-store")],
        )

