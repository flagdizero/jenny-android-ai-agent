"""HTTP action executor: proxy one typed action onto the app's external server.

The browser→gateway leg is GET-only (websockets http11 parser), but this
proxy→server leg is real httpx, so any declared method works. Targets are
checked with the app-server SSRF policy (LAN allowed, loopback/metadata
blocked) and redirects are never followed so a server can't bounce the proxy
to a blocked address.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx
from loguru import logger

from jenny.apps.manifest import PLACEHOLDER_RE, AppAction, AppManifest
from jenny.security.fetch import JENNY_USER_AGENT
from jenny.security.network import validate_app_server_target

DEFAULT_TIMEOUT_S = 20.0
MAX_RESPONSE_BYTES = 512 * 1024


class HttpActionError(Exception):
    """Structured http-action failure; ``status`` maps to an HTTP-ish code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def build_request(
    manifest: AppManifest, action: AppAction, params: dict
) -> tuple[str, str, dict, dict | None]:
    """Return (method, url, query_params, json_body) for the outbound request."""
    assert action.method is not None and action.path is not None
    if not manifest.server_base_url:
        raise HttpActionError("app has no server.baseUrl configured")

    placeholders = set(PLACEHOLDER_RE.findall(action.path))
    path = action.path
    for name in placeholders:
        if name not in params:
            raise HttpActionError(f"missing path parameter '{name}'")
        path = path.replace("{" + name + "}", quote(str(params[name]), safe=""))

    leftover = {k: v for k, v in params.items() if k not in placeholders}
    url = manifest.server_base_url.rstrip("/") + path

    if action.method in ("GET", "DELETE"):
        return action.method, url, leftover, None
    return action.method, url, {}, leftover


async def execute_http_action(
    slug: str,
    manifest: AppManifest,
    action: AppAction,
    params: dict,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """Execute one http action; returns a JSON-safe result dict."""
    method, url, query, body = build_request(manifest, action, params)

    if manifest.server_auth:
        # Fail-closed: l'app dichiara di richiedere autenticazione ma non esiste
        # ancora un credential store. Chiamare l'endpoint senza credenziali
        # (vecchio comportamento) era una violazione silenziosa del contratto:
        # o si perde la richiesta o si prende un 401. Rifiutiamo in modo
        # esplicito finché il secrets store non sarà implementato (roadmap).
        #
        # Da settembre 2026 ``_parse_manifest`` rifiuta ``server.auth`` al
        # caricamento, quindi per la via normale (``load_app`` → ``find_app``)
        # qui non si arriva più. Il controllo resta perché questa funzione
        # accetta un ``AppManifest`` qualunque — costruito a mano, o prodotto da
        # un parser che un domani riammettesse il campo: è l'ultima linea, e
        # perderla renderebbe il rifiuto una proprietà del validatore invece che
        # dell'esecutore.
        logger.warning(
            "App '{}' declares server.auth but no credential store is configured; "
            "refusing the action (fail-closed).",
            slug,
        )
        raise HttpActionError(
            "server.auth is declared but no credential store is configured; "
            "the action is refused until app-server credentials are supported",
            status=501,
        )

    ok, error = await asyncio.to_thread(validate_app_server_target, url)  # DNS fuori dal loop
    if not ok:
        raise HttpActionError(f"blocked server target: {error}", status=403)

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=False
        ) as client:
            async with client.stream(
                method, url, params=query or None, json=body,
                headers={"User-Agent": JENNY_USER_AGENT},
            ) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise HttpActionError(
                            f"server response exceeds {MAX_RESPONSE_BYTES} bytes", status=502
                        )
    except HttpActionError:
        raise
    except httpx.HTTPError as exc:
        raise HttpActionError(f"request to app server failed: {exc}", status=502) from exc

    content_type = response.headers.get("content-type", "")
    text = raw.decode(response.encoding or "utf-8", errors="replace")
    data: object = text
    if "json" in content_type:
        try:
            import json

            data = json.loads(text)
        except ValueError:
            data = text

    return {"ok": response.is_success, "status": response.status_code, "data": data}
