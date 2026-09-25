"""Shared HTTP helpers for the embedded WebUI gateway."""

from __future__ import annotations

import email.utils
import hmac
import http
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from websockets.datastructures import Headers
from websockets.http11 import Response

QueryParams = dict[str, list[str]]


def strip_trailing_slash(path: str) -> str:
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def normalize_config_path(path: str) -> str:
    return strip_trailing_slash(path)


def case_insensitive_header(headers: Any, key: str) -> str:
    """Read a header from websockets/http test stubs without assuming casing."""
    try:
        value = headers.get(key)
    except Exception:
        value = None
    if value is None:
        try:
            value = headers.get(key.lower())
        except Exception:
            value = None
    return str(value or "").strip()


def safe_host_header(value: str) -> str:
    """Return a safe Host header value, or empty when it should not be echoed."""
    value = value.strip()
    if not value:
        return ""
    if re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::\d{1,5})?", value):
        return value
    if re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", value):
        return value
    return ""


def host_for_url(host: str, port: int) -> str:
    host = host.strip()
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def http_json_response(
    data: dict[str, Any],
    *,
    status: int = 200,
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    header_items = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", "application/json; charset=utf-8"),
    ]
    if extra_headers:
        header_items.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(header_items), body)


def http_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    headers = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(headers), body)


def http_error(status: int, message: str | None = None) -> Response:
    body = (message or http.HTTPStatus(status).phrase).encode("utf-8")
    return http_response(body, status=status)


def parse_request_path(path_with_query: str) -> tuple[str, QueryParams]:
    """Parse normalized path and query parameters in one pass."""
    parsed = urlparse("ws://x" + path_with_query)
    path = strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query, keep_blank_values=True)


def parse_query(path_with_query: str) -> QueryParams:
    return parse_request_path(path_with_query)[1]


# Markers used to detect query params whose values must never be logged.
# Matched case-insensitively as substrings so variants like ``apiKey``,
# ``access_token`` or ``x_secret`` are covered without an exhaustive list.
_SENSITIVE_QUERY_MARKERS = ("token", "api_key", "apikey", "secret", "password")


def _is_sensitive_query_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_QUERY_MARKERS)


def redact_query_secrets(path_or_url: str) -> str:
    """Return ``path_or_url`` with sensitive query-param VALUES masked.

    Values of secret-bearing parameters (``token``, ``api_key``, ``apikey``,
    ``access_token``, ``secret``, ``password`` and case variants) are replaced
    with ``REDACTED`` while keys, ordering and the rest of the path are left
    intact. Safe to feed into any log line. This is a defensive invariant: no
    call site should log a full path with a live secret, and this helper makes
    that hard to reintroduce accidentally.

    Handles an absent query string, multiple params, empty values and mixed
    casing on parameter names.
    """
    if not path_or_url or "?" not in path_or_url:
        return path_or_url
    head, _, query = path_or_url.partition("?")
    if not query:
        return path_or_url
    redacted: list[str] = []
    for pair in query.split("&"):
        key, sep, _value = pair.partition("=")
        if sep and _is_sensitive_query_key(key):
            redacted.append(f"{key}=REDACTED")
        else:
            redacted.append(pair)
    return f"{head}?{'&'.join(redacted)}"


def query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


# I due insiemi vivono qui, accanto a ``query_first``, perché è dove finiscono a
# cercarli i quattro moduli che leggevano un booleano dalla query. Ognuno aveva
# la sua lista: quella delle skill non conosceva ``"on"`` e non strippava, quindi
# ``?disabled=on`` — la forma che manda un checkbox HTML — non disabilitava
# niente, e uno spazio finale ribaltava il valore.
TRUTHY_VALUES = ("1", "true", "yes", "on")
FALSY_VALUES = ("0", "false", "no", "off")


def parse_flag(raw: str | None) -> bool:
    """Un valore di query letto come flag: vero solo se dichiarato vero."""
    return (raw or "").strip().lower() in TRUTHY_VALUES


def is_localhost(connection: Any) -> bool:
    """Return True when the peer address is loopback.

    Kept as a general utility; privilege decisions must not depend on it.
    """
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in {"127.0.0.1", "::1", "localhost"}


def bearer_token(headers: Any) -> str | None:
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-Jenny-Auth") or headers.get("x-jenny-auth")
    if not header_token:
        return False
    return hmac.compare_digest(header_token.strip(), configured_secret)


def check_api_secret(headers: Any, path_with_query: str, secret: str) -> bool:
    """Validate a Bearer header or ?token= query param against the secret."""
    if not secret:
        return False
    supplied = bearer_token(headers) or query_first(parse_query(path_with_query), "token")
    if not supplied:
        return False
    return hmac.compare_digest(supplied, secret)


def check_app_secret(headers: Any, path_with_query: str, secret: str, slug: str) -> bool:
    """Come :func:`check_api_secret`, ma sulle route della Jenny App *slug*.

    Accetta il segreto intero (la SPA) **oppure** il token di quell'app
    (``jenny.apps.token.app_token``), che la cornice dell'app riceve al posto
    del segreto. Il token di un'altra app non combacia: lo slug è dentro l'HMAC.
    Da chiamare **solo** dalle route ``/apps/<slug>/`` e
    ``/api/apps/<slug>/actions/``: altrove si usa :func:`check_api_secret`.
    """
    if not secret:
        return False
    supplied = bearer_token(headers) or query_first(parse_query(path_with_query), "token")
    if not supplied:
        return False
    from jenny.apps.token import app_token

    given = supplied.encode("utf-8")
    if hmac.compare_digest(given, secret.encode("utf-8")):
        return True
    return hmac.compare_digest(given, app_token(secret, slug).encode("utf-8"))
