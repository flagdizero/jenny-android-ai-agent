from types import SimpleNamespace

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.base import LLMProvider, LLMResponse
from jenny.providers.openai_compat_provider import OpenAICompatProvider


def test_openai_compat_error_captures_retry_after_from_headers() -> None:
    err = Exception("boom")
    err.doc = None
    err.response = SimpleNamespace(
        text='{"error":{"message":"Rate limit exceeded"}}',
        headers={"Retry-After": "20"},
    )

    response = OpenAICompatProvider._handle_error(err)

    assert response.retry_after == 20.0


def test_anthropic_error_captures_retry_after_from_headers() -> None:
    err = Exception("boom")
    err.response = SimpleNamespace(
        headers={"Retry-After": "20"},
    )

    response = AnthropicProvider._handle_error(err)

    assert response.retry_after == 20.0


# ── Il Retry-After ha un tetto anche in standard ──────────────────────────────


async def test_a_huge_retry_after_is_capped_in_standard_mode(monkeypatch) -> None:
    """``Retry-After: 3600`` in modalità standard bloccava la sessione ~3 ore.

    La modalità persistente aveva già il suo tetto (``_PERSISTENT_MAX_DELAY``);
    la standard aspettava alla lettera, tre volte. Ora ogni attesa si ferma a
    ``_STANDARD_MAX_DELAY``.
    """

    class _RateLimited(LLMProvider):
        async def chat(self, **_kwargs):
            return LLMResponse(
                content="Error: rate limited", finish_reason="error",
                error_status_code=429, error_retry_after_s=3600.0,
            )

        def get_default_model(self) -> str:
            return "m"

    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)
    response = await _RateLimited().chat_with_retry(messages=[{"role": "user", "content": "x"}])

    assert response.finish_reason == "error"
    # Prima: 3 × 3600 s. Ora ogni attesa si ferma a un minuto al massimo.
    assert sum(slept) <= 60 * len(LLMProvider._CHAT_RETRY_DELAYS)


# ── Un server sovraccarico non è un rate limit ────────────────────────────────

_OPENCODE_OVERLOADED = (
    'Error calling LLM: HTTP 429 from https://opencode.ai/zen/go/v1/chat/completions: '
    '{"error":{"type":"server_error","message":"Upstream request failed: [overloaded] '
    'the server is overloaded; retry shortly"}}'
)


def _overloaded(**extra) -> LLMResponse:
    return LLMResponse(
        content=_OPENCODE_OVERLOADED, finish_reason="error", error_status_code=429,
        error_type="server_error", **extra,
    )


async def test_an_overloaded_429_waits_longer_than_its_retry_after(monkeypatch) -> None:
    """Il 05/11/2026 OpenCode Go mandava «overloaded» con ``Retry-After: 2``: tre
    attese da 2 s, otto secondi in tutto, e il turno perso dentro il sovraccarico."""

    class _Overloaded(LLMProvider):
        async def chat(self, **_kwargs):
            return _overloaded(error_retry_after_s=2.0)

        def get_default_model(self) -> str:
            return "m"

    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("jenny.providers.base.asyncio.sleep", _fake_sleep)
    await _Overloaded().chat_with_retry(messages=[{"role": "user", "content": "x"}])

    assert slept == list(LLMProvider._OVERLOAD_RETRY_DELAYS)
    assert len(slept) == len(LLMProvider._CHAT_RETRY_DELAYS)


def test_an_overloaded_429_is_told_as_the_providers_trouble() -> None:
    from jenny.providers.retry_policy import classify_user_error, is_overloaded_response

    assert is_overloaded_response(_overloaded())
    assert classify_user_error(_overloaded()) == "server"
    rate_limited = LLMResponse(
        content="Error: rate limit exceeded", finish_reason="error", error_status_code=429,
    )
    assert not is_overloaded_response(rate_limited)
    assert classify_user_error(rate_limited) == "rate_limit"
    bad_request = LLMResponse(
        content="Error: overloaded", finish_reason="error", error_status_code=400,
    )
    assert not is_overloaded_response(bad_request)
