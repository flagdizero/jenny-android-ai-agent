"""OpenCode Go vuole ``x-opencode-session``, e nessun altro provider deve accorgersene.

Go chiede ai client di terze parti di identificarsi con uno user agent proprio e
di mandare un ID di sessione stabile per conversazione, con cui il gateway tiene
insieme routing e prompt caching; senza, risponde ``Request is missing
x-opencode-session and cannot be routed efficiently``.

Metà di questo file verifica che l'header arrivi. **L'altra metà verifica che non
arrivi altrove**, ed è la parte che vale di più: il supporto a Go è un ramo dentro
il percorso che serve *tutti* i provider, quindi il modo in cui può far danno non
è smettere di funzionare — è cambiare in silenzio le richieste verso OpenAI,
Groq, OpenRouter o Anthropic.

Le richieste si catturano con ``httpx.MockTransport``, cioè si guarda l'header
che parte davvero: un mock sui kwargs direbbe solo che il codice ha passato un
dict, non che httpx l'ha fuso con quelli del client.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.openai_compat_provider import OpenAICompatProvider
from jenny.providers.opencode import (
    SESSION_HEADER,
    conversation_scope,
    session_headers,
    uses_opencode,
)

GO_BASE = "https://opencode.ai/zen/go/v1"

_OPENAI_BODY = json.dumps({
    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}).encode()

_ANTHROPIC_BODY = json.dumps({
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}).encode()


def _capture_openai(provider: OpenAICompatProvider) -> list[httpx.Request]:
    """Sostituisce il client del provider con uno che registra le richieste."""
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_OPENAI_BODY, headers={
            "content-type": "application/json",
        })

    provider._http_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return seen


def _capture_anthropic(provider: AnthropicProvider, *, body: bytes = _ANTHROPIC_BODY,
                       content_type: str = "application/json") -> list[httpx.Request]:
    """Come sopra, ma tenendo gli header di default che il client Anthropic monta.

    Il client vero li fissa alla costruzione (``x-api-key``, ``anthropic-version``):
    ricostruirlo senza perderebbe proprio la fusione che c'è da verificare.
    """
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    original = provider._http_client
    assert original is not None
    provider._http_client = httpx.AsyncClient(
        base_url=original.base_url,
        headers=original.headers,
        transport=httpx.MockTransport(_handler),
    )
    return seen


def _openai(api_base: str, **kwargs: Any) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        api_key="k", api_base=api_base, default_model="m", **kwargs,
    )


# --- il gate ------------------------------------------------------------------------


class TestIlGate:
    """``uses_opencode`` decide tutto, e guarda solo l'host."""

    def test_recognizes_the_go_base(self) -> None:
        assert uses_opencode(GO_BASE) is True

    def test_recognizes_the_base_without_v1(self) -> None:
        # È la forma che serve al ramo Anthropic dopo ``_normalize_base_url``.
        assert uses_opencode("https://opencode.ai/zen/go") is True

    def test_ignores_case(self) -> None:
        assert uses_opencode("https://OpenCode.ai/zen/go/v1") is True

    def test_does_not_recognize_the_others(self) -> None:
        for base in (
            "https://api.openai.com/v1",
            "https://api.groq.com/openai/v1",
            "https://openrouter.ai/api/v1",
            "https://api.anthropic.com",
            "http://127.0.0.1:8080/v1",
            "",
        ):
            assert uses_opencode(base) is False, base

    def test_no_base_is_not_opencode(self) -> None:
        assert uses_opencode(None) is False


# --- l'identificativo di conversazione --------------------------------------------


class TestConversationId:
    """Opaco, stabile per conversazione, distinto fra conversazioni."""

    def test_is_stable_within_the_same_scope(self) -> None:
        with conversation_scope("unified:default"):
            first = session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER]
            second = session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER]
        assert first == second

    def test_is_different_across_conversations(self) -> None:
        with conversation_scope("unified:default"):
            user = session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER]
        with conversation_scope("internal:dream"):
            dream = session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER]
        assert user != dream

    def test_does_not_expose_the_session_key(self) -> None:
        # Le chiavi nominano canale e chat: in chiaro sarebbero un dato personale
        # regalato a un terzo. Va mandato l'hash.
        with conversation_scope("telegram:123456789"):
            value = session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER]
        assert "telegram" not in value
        assert "123456789" not in value

    def test_the_scope_closes_again(self) -> None:
        with conversation_scope("unified:default"):
            pass
        assert session_headers(GO_BASE, fallback_id="ripiego")[SESSION_HEADER] == "ripiego"

    def test_the_nested_scope_restores_the_outer_one(self) -> None:
        with conversation_scope("unified:default"):
            outer = session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER]
            with conversation_scope("internal:cron"):
                inner = session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER]
            assert session_headers(GO_BASE, fallback_id="x")[SESSION_HEADER] == outer
        assert inner != outer

    def test_without_scope_falls_back_instead_of_omitting(self) -> None:
        # Un header assente è un fallimento documentato dal gateway; un header
        # costante è solo caching peggiore. Fra i due si sceglie il secondo.
        headers = session_headers(GO_BASE, fallback_id="per-istanza")
        assert headers[SESSION_HEADER] == "per-istanza"


# --- provider OpenAI-compat ---------------------------------------------------------


class TestOpenAICompatTowardOpenCode:

    async def test_the_request_carries_session_and_user_agent(self) -> None:
        provider = _openai(GO_BASE)
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert len(seen) == 1
        assert seen[0].headers[SESSION_HEADER]
        assert seen[0].headers["user-agent"].startswith("jenny/")

    async def test_two_turns_of_the_same_conversation_have_the_same_id(self) -> None:
        provider = _openai(GO_BASE)
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "uno"}])
            await provider.chat([{"role": "user", "content": "due"}])
        assert seen[0].headers[SESSION_HEADER] == seen[1].headers[SESSION_HEADER]

    async def test_different_conversations_have_different_ids(self) -> None:
        provider = _openai(GO_BASE)
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "uno"}])
        with conversation_scope("internal:heartbeat"):
            await provider.chat([{"role": "user", "content": "due"}])
        assert seen[0].headers[SESSION_HEADER] != seen[1].headers[SESSION_HEADER]

    async def test_also_applies_to_the_responses_api(self) -> None:
        # Go serve Grok e GPT Luna su ``/responses``: è lo stesso ``_send_request``,
        # e questo test è ciò che lo tiene vero.
        provider = _openai(GO_BASE, api_type="responses")
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider._http_request("/responses", {"model": "grok-4.6"})
        assert seen[0].url.path.endswith("/responses")
        assert seen[0].headers[SESSION_HEADER]

    async def test_without_scope_still_sends_a_stable_id(self) -> None:
        provider = _openai(GO_BASE)
        seen = _capture_openai(provider)
        await provider.chat([{"role": "user", "content": "uno"}])
        await provider.chat([{"role": "user", "content": "due"}])
        assert seen[0].headers[SESSION_HEADER] == provider._session_affinity_id
        assert seen[1].headers[SESSION_HEADER] == provider._session_affinity_id

    async def test_the_user_user_agent_wins(self) -> None:
        provider = _openai(GO_BASE, extra_headers={"User-Agent": "mio/1.0"})
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].headers["user-agent"] == "mio/1.0"
        # ...ma la sessione resta, perché non è cosmetica.
        assert seen[0].headers[SESSION_HEADER]

    async def test_the_user_session_wins(self) -> None:
        provider = _openai(GO_BASE, extra_headers={SESSION_HEADER: "mia"})
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].headers[SESSION_HEADER] == "mia"


class TestOpenAICompatTowardTheOthers:
    """Il cuore: fuori da OpenCode non cambia niente."""

    async def test_direct_openai_receives_nothing_from_opencode(self) -> None:
        provider = _openai("https://api.openai.com/v1")
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert SESSION_HEADER not in seen[0].headers
        assert "jenny/" not in seen[0].headers.get("user-agent", "")

    async def test_groq_receives_nothing_from_opencode(self) -> None:
        provider = _openai("https://api.groq.com/openai/v1")
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert SESSION_HEADER not in seen[0].headers

    async def test_a_local_endpoint_receives_nothing_from_opencode(self) -> None:
        provider = _openai("http://127.0.0.1:8080/v1")
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert SESSION_HEADER not in seen[0].headers

    async def test_openrouter_keeps_its_attribution_headers(self) -> None:
        provider = _openai("https://openrouter.ai/api/v1")
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        headers = seen[0].headers
        assert SESSION_HEADER not in headers
        assert headers["http-referer"] == "https://github.com/flagdizero/jenny-android-ai-agent"
        assert headers["x-openrouter-title"] == "Jenny"
        assert headers["x-openrouter-categories"] == "android-agent,personal-agent"

    async def test_x_session_affinity_stays_on_all(self) -> None:
        for base in (GO_BASE, "https://api.openai.com/v1", "https://openrouter.ai/api/v1"):
            provider = _openai(base)
            seen = _capture_openai(provider)
            with conversation_scope("unified:default"):
                await provider.chat([{"role": "user", "content": "ciao"}])
            assert seen[0].headers["x-session-affinity"] == provider._session_affinity_id, base

    async def test_authorization_and_content_type_remain(self) -> None:
        provider = _openai(GO_BASE)
        seen = _capture_openai(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].headers["authorization"] == "Bearer k"
        assert seen[0].headers["content-type"] == "application/json"


# --- provider Anthropic -------------------------------------------------------------


class TestAnthropicTowardOpenCode:
    """Go serve MiniMax, Qwen e Union Alpha in formato Messages."""

    async def test_the_non_streaming_request_carries_the_session(self) -> None:
        provider = AnthropicProvider(api_key="k", api_base=GO_BASE)
        seen = _capture_anthropic(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].url.path.endswith("/v1/messages")
        assert seen[0].headers[SESSION_HEADER]
        assert seen[0].headers["user-agent"].startswith("jenny/")

    async def test_the_streaming_request_carries_the_session(self) -> None:
        events = (
            b'event: message_start\ndata: {"type":"message_start","message":{"usage":{}}}\n\n'
            b'event: message_delta\ndata: {"type":"message_delta",'
            b'"delta":{"stop_reason":"end_turn"},"usage":{}}\n\n'
        )
        provider = AnthropicProvider(api_key="k", api_base=GO_BASE)
        seen = _capture_anthropic(provider, body=events, content_type="text/event-stream")
        with conversation_scope("unified:default"):
            await provider.chat_stream(messages=[{"role": "user", "content": "ciao"}])
        assert seen[0].headers[SESSION_HEADER]

    async def test_the_client_headers_survive(self) -> None:
        provider = AnthropicProvider(api_key="k", api_base=GO_BASE)
        seen = _capture_anthropic(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].headers["x-api-key"] == "k"
        assert seen[0].headers["anthropic-version"] == "2023-06-01"

    async def test_the_user_user_agent_wins(self) -> None:
        # ``extraHeaders`` sta sul client e un header per richiesta lo
        # sovrascriverebbe: senza il filtro in ``_request_headers`` l'unica via
        # per forzare un header su questo formato smetterebbe di funzionare.
        provider = AnthropicProvider(
            api_key="k", api_base=GO_BASE, extra_headers={"User-Agent": "mio/1.0"},
        )
        seen = _capture_anthropic(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].headers["user-agent"] == "mio/1.0"
        assert seen[0].headers[SESSION_HEADER]

    async def test_the_user_session_wins(self) -> None:
        provider = AnthropicProvider(
            api_key="k", api_base=GO_BASE, extra_headers={SESSION_HEADER: "mia"},
        )
        seen = _capture_anthropic(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].headers[SESSION_HEADER] == "mia"

    async def test_the_comparison_ignores_case(self) -> None:
        # I nomi degli header sono case-insensitive: ``Authorization`` scritto
        # dall'utente deve bloccare anche una nostra ``authorization``.
        provider = AnthropicProvider(
            api_key="k", api_base=GO_BASE, extra_headers={"USER-AGENT": "mio/1.0"},
        )
        seen = _capture_anthropic(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert seen[0].headers["user-agent"] == "mio/1.0"


class TestAnthropicTowardTheOthers:

    def test_toward_anthropic_no_per_request_header_is_passed(self) -> None:
        # ``None`` e non ``{}``: la chiamata verso Anthropic resta letteralmente
        # quella di prima invece di un merge a vuoto.
        provider = AnthropicProvider(api_key="k", api_base="https://api.anthropic.com")
        with conversation_scope("unified:default"):
            assert provider._request_headers() is None

    def test_toward_opencode_they_are_passed(self) -> None:
        provider = AnthropicProvider(api_key="k", api_base=GO_BASE)
        with conversation_scope("unified:default"):
            headers = provider._request_headers()
        assert headers is not None
        assert SESSION_HEADER in headers

    async def test_anthropic_receives_nothing_from_opencode(self) -> None:
        provider = AnthropicProvider(api_key="k", api_base="https://api.anthropic.com")
        seen = _capture_anthropic(provider)
        with conversation_scope("unified:default"):
            await provider.chat([{"role": "user", "content": "ciao"}])
        assert SESSION_HEADER not in seen[0].headers
        assert "jenny/" not in seen[0].headers.get("user-agent", "")
