"""Cosa diventano gli errori del provider, per Anthropic e OpenAI-compat insieme.

Le due ``_handle_error`` avevano ognuna la propria copia dei metadati d'errore
(stato, ``x-should-retry``, tipo di guasto, tipo/codice dal payload, header),
ed erano gia' divergenti: la lettura di ``.text`` era protetta solo da una
parte, l'ordine degli header diverso. Questo banco fissa l'esito per ogni forma
di eccezione che conta. Due righe descrivono un cambiamento voluto (24/09/2026):
una risposta in streaming non ancora letta non fa piu' esplodere il gestore
d'errore di OpenAI-compat, e gli header portati dall'eccezione valgono anche
per Anthropic.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.openai_compat_provider import OpenAICompatProvider


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class _NotRead:
    """Una risposta in streaming chiusa prima di leggerla: ``.text`` solleva."""

    headers = _Headers({"retry-after": "7"})
    status_code = 529

    @property
    def text(self):
        raise RuntimeError("ResponseNotRead")

    def json(self):
        raise RuntimeError("ResponseNotRead")


class RateLimitError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


def _exc(cls=Exception, msg="boom", **attrs):
    e = cls(msg)
    for key, value in attrs.items():
        setattr(e, key, value)
    return e


def _cases():
    return {
        "status_on_response": _exc(RateLimitError, response=SimpleNamespace(
            status_code=429,
            headers=_Headers({"retry-after": "12", "x-should-retry": "true"}),
            text='{"error":{"type":"rate_limit_error","code":"rl"}}',
        )),
        "headers_on_exception": _exc(
            RateLimitError, status_code=503,
            headers=_Headers({"retry-after": "3", "x-should-retry": "false"}),
            response=SimpleNamespace(status_code=503, headers=_Headers({"retry-after": "99"}), text="oops"),
        ),
        "body_json": _exc(status_code=400, body={"error": {"type": "invalid_request_error", "message": "bad"}}),
        "timeout": _exc(APITimeoutError, "timed out"),
        "connection": _exc(APIConnectionError, "refused"),
        "retry_in_message": _exc(status_code=429, body="Please retry after 20 seconds"),
        "not_read": _exc(RateLimitError, response=_NotRead()),
    }


_FIELDS = (
    "content", "retry_after", "partial_content", "error_status_code", "error_kind",
    "error_type", "error_code", "error_retry_after_s", "error_should_retry",
)

_RATE = ('Error: {"error":{"type":"rate_limit_error","code":"rl"}}', 12.0, "pc", 429, None,
         "rate_limit_error", "rl", 12.0, True)
_EXC_HEADERS = ("Error: oops", 3.0, "pc", 503, None, None, None, 3.0, False)
_BODY = ("Error: {'error': {'type': 'invalid_request_error', 'message': 'bad'}}", None, "pc",
         400, None, "invalid_request_error", None, None, None)
_TIMEOUT = ("Error calling LLM: timed out", None, "pc", None, "timeout", None, None, None, None)
_CONN = ("Error calling LLM: refused", None, "pc", None, "connection", None, None, None, None)
_NOT_READ = ("Error calling LLM: boom", 7.0, "pc", 529, None, None, None, 7.0, None)

EXPECTED = {
    ("anthropic", "status_on_response"): _RATE,
    ("openai", "status_on_response"): _RATE,
    ("anthropic", "headers_on_exception"): _EXC_HEADERS,
    ("openai", "headers_on_exception"): _EXC_HEADERS,
    ("anthropic", "body_json"): _BODY,
    ("openai", "body_json"): _BODY,
    ("anthropic", "timeout"): _TIMEOUT,
    ("openai", "timeout"): _TIMEOUT,
    ("anthropic", "connection"): _CONN,
    ("openai", "connection"): _CONN,
    # Il ritardo scritto nel messaggio: Anthropic lo mette anche in
    # ``error_retry_after_s``, OpenAI-compat solo in ``retry_after``. Chi decide
    # l'attesa ricade dall'uno all'altro, quindi l'effetto e' lo stesso.
    ("anthropic", "retry_in_message"): ("Error: Please retry after 20 seconds", 20.0, "pc", 429,
                                        None, None, None, 20.0, None),
    ("openai", "retry_in_message"): ("Error: Please retry after 20 seconds", 20.0, "pc", 429,
                                     None, None, None, None, None),
    ("anthropic", "not_read"): _NOT_READ,
    ("openai", "not_read"): _NOT_READ,
}

_PROVIDERS = {"anthropic": AnthropicProvider, "openai": OpenAICompatProvider}


@pytest.mark.parametrize(("provider", "case"), sorted(EXPECTED))
def test_the_error_becomes_the_expected_response(provider: str, case: str) -> None:
    response = _PROVIDERS[provider]._handle_error(_cases()[case], partial_content="pc")
    assert tuple(getattr(response, f) for f in _FIELDS) == EXPECTED[(provider, case)]
