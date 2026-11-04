from unittest.mock import patch, sentinel

import pytest

from jenny.providers.endpoint_budget import (
    DEFAULT_REQUEST_TIMEOUT_S,
    LOCAL_REQUEST_TIMEOUT_S,
    read_timeout_s,
)
from jenny.providers.openai_compat_provider import OpenAICompatProvider


async def test_openai_compat_provider_defers_http_client_until_first_use() -> None:
    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test"
    )
    assert provider._http_client is None
    await provider._ensure_client()
    assert provider._http_client is not None


async def test_openai_compat_provider_sets_timeout_on_http_client() -> None:
    with patch(
        "httpx.AsyncClient",
        return_value=sentinel.http_client,
    ) as mock_http_client:
        provider = OpenAICompatProvider(
            api_key="test-key",
            api_base="http://127.0.0.1:11434/v1",
            default_model="test",
        )
        await provider._ensure_client()

    client_kwargs = mock_http_client.call_args.kwargs
    # Endpoint in loopback: il limite è quello lungo, perché il prompt
    # processing di un model server locale può durare minuti. La read copre in
    # più il budget del primo token.
    assert client_kwargs["timeout"].connect == LOCAL_REQUEST_TIMEOUT_S
    assert client_kwargs["timeout"].read == read_timeout_s(local=True)
    assert client_kwargs["limits"].keepalive_expiry == 0
    assert provider._http_client is sentinel.http_client


async def test_openai_compat_provider_keeps_the_tight_timeout_for_remote() -> None:
    with patch(
        "httpx.AsyncClient",
        return_value=sentinel.http_client,
    ) as mock_http_client:
        provider = OpenAICompatProvider(
            api_key="test-key",
            api_base="https://api.groq.com/openai/v1",
            default_model="test",
        )
        await provider._ensure_client()

    timeout = mock_http_client.call_args.kwargs["timeout"]
    assert timeout.connect == DEFAULT_REQUEST_TIMEOUT_S
    assert timeout.read == read_timeout_s(local=False)


async def test_openai_compat_provider_timeout_can_be_overridden_by_env(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_OPENAI_COMPAT_TIMEOUT_S", "45")

    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test"
    )
    await provider._ensure_client()

    # L'override vale per connect/write/pool; la read non scende sotto il
    # budget del primo token, che ha il suo knob.
    assert provider._http_client.timeout.connect == 45.0
    assert provider._http_client.timeout.read == read_timeout_s(local=False)


async def test_the_shared_env_name_also_applies_here(monkeypatch) -> None:
    """Il knob è del trasporto, non di un provider: vale su entrambi i rami."""
    monkeypatch.setenv("JENNY_LLM_HTTP_TIMEOUT_S", "450")

    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test"
    )
    await provider._ensure_client()

    assert provider._http_client.timeout.read == 450.0


@pytest.mark.parametrize("bad", ["0", "-5", "abc"])
async def test_a_malformed_timeout_falls_back_to_the_default(monkeypatch, bad: str) -> None:
    """Zero non disabilita il timeout: httpx lo prende alla lettera."""
    monkeypatch.setenv("JENNY_OPENAI_COMPAT_TIMEOUT_S", bad)

    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test"
    )
    await provider._ensure_client()

    assert provider._http_client.timeout.connect == DEFAULT_REQUEST_TIMEOUT_S
