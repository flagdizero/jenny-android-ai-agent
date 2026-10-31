"""Classificazione degli errori per la retry policy (estratto da LLMProvider).

Costanti (marker/token) + classificatori PURI: transient vs terminale, 429
retryable vs quota/arrearage. L'ENGINE di retry (``chat_with_retry`` /
``_run_with_retry``) e il parsing di ``retry-after`` restano in ``base.py`` e
usano queste funzioni.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jenny.providers.base import LLMResponse

TRANSIENT_ERROR_MARKERS = (
    "429",
    "rate limit",
    "500",
    "502",
    "503",
    "504",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "server error",
    "temporarily unavailable",
    "速率限制",
    "访问量过大",
)
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})
TRANSIENT_ERROR_KINDS = frozenset({"timeout", "connection"})
NON_RETRYABLE_429_ERROR_TOKENS = frozenset(
    {
        "insufficient_quota",
        "quota_exceeded",
        "quota_exhausted",
        "billing_hard_limit_reached",
        "insufficient_balance",
        "credit_balance_too_low",
        "billing_not_active",
        "payment_required",
    }
)
RETRYABLE_429_ERROR_TOKENS = frozenset(
    {
        "rate_limit_exceeded",
        "rate_limit_error",
        "too_many_requests",
        "request_limit_exceeded",
        "requests_limit_exceeded",
        "overloaded_error",
    }
)
NON_RETRYABLE_429_TEXT_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "quota exceeded",
    "quota exhausted",
    "billing hard limit",
    "billing_hard_limit_reached",
    "billing not active",
    "insufficient balance",
    "insufficient_balance",
    "credit balance too low",
    "payment required",
    "out of credits",
    "out of quota",
    "exceeded your current quota",
)
RETRYABLE_429_TEXT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "retry after",
    "try again in",
    "temporarily unavailable",
    "overloaded",
    "concurrency limit",
    "速率限制",
)


def normalize_error_token(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    return token or None


def is_transient_error(content: str | None) -> bool:
    err = (content or "").lower()
    return any(marker in err for marker in TRANSIENT_ERROR_MARKERS)


def is_retryable_429_response(response: "LLMResponse") -> bool:
    type_token = normalize_error_token(response.error_type)
    code_token = normalize_error_token(response.error_code)
    semantic_tokens = {token for token in (type_token, code_token) if token is not None}
    if any(token in NON_RETRYABLE_429_ERROR_TOKENS for token in semantic_tokens):
        return False

    content = (response.content or "").lower()
    if any(marker in content for marker in NON_RETRYABLE_429_TEXT_MARKERS):
        return False

    if any(token in RETRYABLE_429_ERROR_TOKENS for token in semantic_tokens):
        return True
    if any(marker in content for marker in RETRYABLE_429_TEXT_MARKERS):
        return True
    # Unknown 429 defaults to WAIT+retry.
    return True


def is_transient_response(response: "LLMResponse") -> bool:
    """Prefer structured error metadata, fallback to text markers for legacy providers."""
    if response.error_should_retry is not None:
        return bool(response.error_should_retry)

    kind = (response.error_kind or "").strip().lower()

    if response.error_status_code is not None:
        status = int(response.error_status_code)
        if status == 429:
            return is_retryable_429_response(response)
        if status in RETRYABLE_STATUS_CODES or status >= 500:
            return True
        # Uno status definitivo (400, 401, 403, 404...) decide da solo: i
        # marker testuali sono il ripiego per chi non ha uno status, e su un
        # corpo d'errore arbitrario mentono — «requested 250000 tokens» contiene
        # «500», «timeout must be between 1 and 600» contiene «timeout» — e
        # facevano ritentare fino a dieci volte un errore che non passa mai.
        return kind in TRANSIENT_ERROR_KINDS

    if kind in TRANSIENT_ERROR_KINDS:
        return True

    return is_transient_error(response.content)


def is_arrearage_response(response: "LLMResponse") -> bool:
    """Detect API-key arrearage / quota / billing errors that won't clear on retry."""
    if response.error_status_code is not None and int(response.error_status_code) == 402:
        return True

    type_token = normalize_error_token(response.error_type)
    code_token = normalize_error_token(response.error_code)
    if any(
        token in NON_RETRYABLE_429_ERROR_TOKENS
        for token in (type_token, code_token)
        if token is not None
    ):
        return True

    content = (response.content or "").lower()
    return any(marker in content for marker in NON_RETRYABLE_429_TEXT_MARKERS)


def classify_user_error(response: "LLMResponse") -> str:
    """Il tipo d'errore *per chi legge la chat*: quale frase mostrargli.

    Uno fra ``credit``, ``auth``, ``rate_limit``, ``server``, ``timeout``,
    ``connection``, ``generic`` — le chiavi ``error.<tipo>`` di
    ``jenny/runtime/user_text.py``, e il valore di ``error.kind`` nel payload
    WebSocket. Diverso da :func:`is_transient_response`, che decide se ritentare:
    qui si decide cosa dire, dopo che i tentativi sono finiti.

    Prima i metadati strutturati (lo status, poi ``error_kind``); il testo solo
    per il credito, dove i provider non mettono uno status affidabile.
    """
    if is_arrearage_response(response):
        return "credit"
    status = response.error_status_code
    if status is not None:
        code = int(status)
        if code in (401, 403):
            return "auth"
        if code == 429:
            return "rate_limit"
        if code >= 500:
            return "server"
        if code == 408:
            return "timeout"
    kind = (response.error_kind or "").strip().lower()
    if kind in ("timeout", "connection"):
        return kind
    return "generic"


def extract_error_type_code(payload: Any) -> tuple[str | None, str | None]:
    data: dict[str, Any] | None = None
    if isinstance(payload, dict):
        data = payload
    elif isinstance(payload, str):
        text = payload.strip()
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
    if not isinstance(data, dict):
        return None, None

    error_obj = data.get("error")
    type_value = data.get("type")
    code_value = data.get("code")
    if isinstance(error_obj, dict):
        type_value = error_obj.get("type") or type_value
        code_value = error_obj.get("code") or code_value

    return normalize_error_token(type_value), normalize_error_token(code_value)
