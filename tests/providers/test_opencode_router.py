"""OpenCode Go: ogni modello parla un solo dei tre endpoint, e Jenny lo impara.

Il difetto di partenza, visto sul telefono l'08/10/2026: con il provider
``opencode-go`` in formato OpenAI, ``gpt-6-luna`` rispondeva
``HTTP 400 … /chat/completions: {"type":"error","error":{"type":
"ModelProtocolUnsupported","message":"Model does not support this protocol."}}``
— GPT su Go si parla solo su ``/responses``.

Le regole che questo file tiene ferme:

- si cambia endpoint **solo** su ``ModelProtocolUnsupported``: ogni altro errore
  (il 400 di privacy di Muse, una chiave sbagliata) arriva all'utente intatto;
- l'endpoint che risponde si ricorda, e il turno dopo ci va dritto;
- se nessuno accetta il modello si restituisce il primo errore, non l'ultimo;
- in streaming non si ripete un turno che ha già emesso qualcosa.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jenny.config.schema import Config, ProviderConfig
from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.base import LLMProvider, LLMResponse
from jenny.providers.factory import make_provider
from jenny.providers.openai_compat_provider import OpenAICompatProvider
from jenny.providers.opencode_router import (
    EndpointMemory,
    OpenCodeRouter,
    endpoint_order,
    is_protocol_mismatch,
)

GO_BASE = "https://opencode.ai/zen/go/v1"

# Il corpo esatto del rifiuto, copiato dal log del telefono.
MISMATCH_BODY = (
    '{"type":"error","error":{"type":"ModelProtocolUnsupported",'
    '"message":"Model does not support this protocol."}}'
)


def _mismatch() -> LLMResponse:
    return LLMResponse(
        content=f"Error calling LLM: HTTP 400 from {GO_BASE}/x: {MISMATCH_BODY}",
        finish_reason="error",
        error_status_code=400,
        error_type="modelprotocolunsupported",
    )


def _privacy_error() -> LLMResponse:
    return LLMResponse(
        content="Error calling LLM: HTTP 400 … This Go model trains on request data.",
        finish_reason="error",
        error_status_code=400,
        error_type="server_error",
    )


class _FakeEndpoint(LLMProvider):
    """Un endpoint finto: risponde con le risposte in coda e conta le chiamate."""

    def __init__(self, name: str, *responses: LLMResponse, deltas: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.name = name
        self._responses = list(responses)
        self._deltas = deltas
        self.calls: list[str | None] = []
        self.closed = False

    def _next(self) -> LLMResponse:
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

    async def chat(self, messages: list[dict[str, Any]], tools=None, model=None,
                   max_tokens=4096, temperature=0.7, reasoning_effort=None,
                   tool_choice=None) -> LLMResponse:
        self.calls.append(model)
        return self._next()

    async def chat_stream(self, messages, tools=None, model=None, max_tokens=4096,
                          temperature=0.7, reasoning_effort=None, tool_choice=None,
                          on_content_delta=None, on_thinking_delta=None,
                          on_tool_call_delta=None) -> LLMResponse:
        self.calls.append(model)
        for text in self._deltas:
            if on_content_delta:
                await on_content_delta(text)
        return self._next()

    async def _close_http_client(self) -> None:
        self.closed = True

    def get_default_model(self) -> str:
        return "m"


def _router(
    *, memory: EndpointMemory | None = None, provider_format: str = "openai_compat",
    api_type: str = "auto", **endpoints: _FakeEndpoint,
) -> OpenCodeRouter:
    router = OpenCodeRouter(
        api_key="k", api_base=GO_BASE, default_model="gpt-6-luna",
        provider_format=provider_format, api_type=api_type, memory=memory,
    )
    router._inner.update(endpoints)
    return router


def _ok(text: str = "ciao") -> LLMResponse:
    return LLMResponse(content=text, finish_reason="stop")


_USER = [{"role": "user", "content": "ciao"}]


# --- il riconoscimento --------------------------------------------------------------


class TestRiconoscimento:
    def test_the_go_refusal_is_a_mismatch(self) -> None:
        assert is_protocol_mismatch(_mismatch())

    def test_the_text_alone_is_enough(self) -> None:
        # Un corpo che non si è lasciato leggere come JSON non porta error_type.
        response = LLMResponse(content=f"Error: {MISMATCH_BODY}", finish_reason="error")
        assert is_protocol_mismatch(response)

    def test_other_errors_are_not(self) -> None:
        assert not is_protocol_mismatch(_privacy_error())

    def test_a_reply_that_mentions_it_is_not(self) -> None:
        # Un modello che *parla* dell'errore non è l'errore.
        assert not is_protocol_mismatch(_ok("ModelProtocolUnsupported vuol dire…"))


class TestOrdine:
    def test_openai_format_starts_from_chat_completions(self) -> None:
        assert endpoint_order("openai_compat", "auto") == ["chat", "responses", "messages"]

    def test_explicit_responses_goes_first(self) -> None:
        assert endpoint_order("openai_compat", "responses") == ["responses", "chat", "messages"]

    def test_anthropic_format_starts_from_messages(self) -> None:
        assert endpoint_order("anthropic", "auto") == ["messages", "chat", "responses"]


# --- l'instradamento ----------------------------------------------------------------


class TestInstradamento:
    async def test_a_refused_model_moves_to_the_next_endpoint(self) -> None:
        chat, responses = _FakeEndpoint("chat", _mismatch()), _FakeEndpoint("responses", _ok())
        router = _router(chat=chat, responses=responses)

        result = await router.chat(_USER)

        assert result.content == "ciao"
        assert chat.calls == ["gpt-6-luna"]
        assert responses.calls == ["gpt-6-luna"]

    async def test_the_next_turn_goes_straight_to_the_learned_endpoint(self) -> None:
        chat, responses = _FakeEndpoint("chat", _mismatch()), _FakeEndpoint("responses", _ok())
        router = _router(chat=chat, responses=responses)

        await router.chat(_USER)
        await router.chat(_USER)

        assert len(chat.calls) == 1
        assert len(responses.calls) == 2
        assert router.endpoint_for("gpt-6-luna") == "responses"

    async def test_other_errors_do_not_move(self) -> None:
        # Il 400 di privacy di Muse: cambiare endpoint non lo risolverebbe, e
        # l'utente deve leggere *quella* causa.
        chat = _FakeEndpoint("chat", _privacy_error())
        responses = _FakeEndpoint("responses", _ok())
        router = _router(chat=chat, responses=responses)

        result = await router.chat(_USER)

        assert result.error_type == "server_error"
        assert responses.calls == []
        assert router.endpoint_for("gpt-6-luna") == "chat"

    async def test_an_error_teaches_nothing(self) -> None:
        # Muse ha ricevuto il 400 di privacy proprio su /chat/completions, che
        # per lui è l'endpoint sbagliato: un errore non conferma il protocollo.
        memory = EndpointMemory(None)
        router = _router(memory=memory, chat=_FakeEndpoint("chat", _privacy_error()))
        await router.chat(_USER)
        assert memory.get(GO_BASE, "gpt-6-luna") is None

    async def test_when_nobody_accepts_the_first_error_wins(self) -> None:
        first = _mismatch()
        first.content = "il primo"
        router = _router(
            chat=_FakeEndpoint("chat", first),
            responses=_FakeEndpoint("responses", _mismatch()),
            messages=_FakeEndpoint("messages", _mismatch()),
        )
        result = await router.chat(_USER)
        assert result.content == "il primo"

    async def test_a_stale_memory_is_forgotten(self) -> None:
        memory = EndpointMemory(None)
        memory.set(GO_BASE, "gpt-6-luna", "messages")
        messages = _FakeEndpoint("messages", _mismatch())
        chat = _FakeEndpoint("chat", _ok())
        router = _router(memory=memory, messages=messages, chat=chat)

        result = await router.chat(_USER)

        assert result.content == "ciao"
        assert messages.calls and chat.calls
        assert memory.get(GO_BASE, "gpt-6-luna") == "chat"

    async def test_models_are_learned_separately(self) -> None:
        chat = _FakeEndpoint("chat", _ok())
        router = _router(chat=chat)
        await router.chat(_USER, model="deepseek-v4-flash")
        assert router.endpoint_for("deepseek-v4-flash") == "chat"
        assert router.endpoint_for("gpt-6-luna") == "chat"  # mai visto: l'ordine di default

    async def test_the_anthropic_format_tries_messages_first(self) -> None:
        messages = _FakeEndpoint("messages", _mismatch())
        chat = _FakeEndpoint("chat", _ok())
        router = _router(provider_format="anthropic", messages=messages, chat=chat)
        await router.chat(_USER, model="glm-5.3")
        assert messages.calls == ["glm-5.3"]
        assert chat.calls == ["glm-5.3"]


class TestStreaming:
    async def test_a_refused_stream_moves_on_and_streams_from_the_next(self) -> None:
        chat = _FakeEndpoint("chat", _mismatch())
        responses = _FakeEndpoint("responses", _ok("ciao"), deltas=("ci", "ao"))
        router = _router(chat=chat, responses=responses)
        seen: list[str] = []

        async def on_delta(text: str) -> None:
            seen.append(text)

        result = await router.chat_stream(_USER, on_content_delta=on_delta)

        assert result.content == "ciao"
        assert seen == ["ci", "ao"]

    async def test_a_stream_that_already_spoke_is_not_repeated(self) -> None:
        # Non è il rifiuto che conosciamo (arriva prima di ogni output):
        # ripetere il turno duplicherebbe il testo già mostrato.
        chat = _FakeEndpoint("chat", _mismatch(), deltas=("mezza frase",))
        responses = _FakeEndpoint("responses", _ok())
        router = _router(chat=chat, responses=responses)

        async def on_delta(text: str) -> None:
            pass

        result = await router.chat_stream(_USER, on_content_delta=on_delta)

        assert result.finish_reason == "error"
        assert responses.calls == []

    async def test_retries_go_through_the_router(self) -> None:
        # La retry policy della base gira sul router, quindi un turno ritentato
        # passa di nuovo dall'instradamento invece di inchiodarsi a un endpoint.
        chat, responses = _FakeEndpoint("chat", _mismatch()), _FakeEndpoint("responses", _ok())
        router = _router(chat=chat, responses=responses)
        result = await router.chat_stream_with_retry(messages=_USER)
        assert result.content == "ciao"


# --- la memoria su disco -------------------------------------------------------------


class TestMemoria:
    def test_survives_a_rebuild(self, tmp_path: Path) -> None:
        path = tmp_path / "opencode_endpoints.json"
        EndpointMemory(path).set(GO_BASE, "gpt-6-luna", "responses")
        assert EndpointMemory(path).get(GO_BASE, "gpt-6-luna") == "responses"

    def test_is_scoped_to_the_base(self, tmp_path: Path) -> None:
        memory = EndpointMemory(tmp_path / "m.json")
        memory.set(GO_BASE, "gpt-6-luna", "responses")
        assert memory.get("https://opencode.ai/zen/v1", "gpt-6-luna") is None
        assert memory.get(GO_BASE + "/", "gpt-6-luna") == "responses"

    def test_a_corrupt_file_is_an_empty_memory(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        path.write_text("{non è json", encoding="utf-8")
        assert EndpointMemory(path).get(GO_BASE, "gpt-6-luna") is None

    def test_unknown_endpoints_are_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        path.write_text(json.dumps({"endpoints": {f"{GO_BASE}|m": "gemini"}}), encoding="utf-8")
        assert EndpointMemory(path).get(GO_BASE, "m") is None

    def test_forget_removes_from_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        EndpointMemory(path).set(GO_BASE, "m", "chat")
        EndpointMemory(path).forget(GO_BASE, "m")
        assert EndpointMemory(path).get(GO_BASE, "m") is None


# --- il resto di Jenny vede un provider qualunque -------------------------------------


class TestFacciata:
    def test_closed_reasoning_follows_the_chat_rule(self) -> None:
        router = _router()
        assert router.closed_reasoning_can_be_blank("deepseek-v4-flash") is True
        assert router.closed_reasoning_can_be_blank("gpt-6-luna") is False

    async def test_aclose_closes_every_inner_endpoint(self) -> None:
        chat, responses = _FakeEndpoint("chat", _ok()), _FakeEndpoint("responses", _ok())
        router = _router(chat=chat, responses=responses)
        await router.aclose()
        assert chat.closed and responses.closed

    def test_inner_endpoints_share_the_configuration(self) -> None:
        router = OpenCodeRouter(
            api_key="k", api_base=GO_BASE, default_model="m",
            extra_headers={"X-Mio": "1"},
        )
        chat = router._provider_for("chat")
        responses = router._provider_for("responses")
        messages = router._provider_for("messages")
        assert isinstance(chat, OpenAICompatProvider) and chat._api_type == "chat_completions"
        assert isinstance(responses, OpenAICompatProvider) and responses._api_type == "responses"
        assert isinstance(messages, AnthropicProvider)
        for inner in (chat, responses, messages):
            assert inner.api_base == GO_BASE
            assert inner.api_key == "k"


class TestFactory:
    def _config(self, **provider: Any) -> Config:
        config = Config()
        config.providers.providers = [ProviderConfig(name="go", api_key="k", **provider)]
        config.agents.defaults.model = "gpt-6-luna"
        return config

    def test_an_opencode_base_builds_the_router(self) -> None:
        provider = make_provider(self._config(format="openai_compat", api_base=GO_BASE))
        assert isinstance(provider, OpenCodeRouter)
        assert provider.get_default_model() == "gpt-6-luna"
        assert provider.provider_name == "go"
        assert provider.endpoint_for() == "chat"

    def test_the_anthropic_format_on_opencode_builds_the_router(self) -> None:
        provider = make_provider(self._config(format="anthropic", api_base=GO_BASE))
        assert isinstance(provider, OpenCodeRouter)
        assert provider.endpoint_for() == "messages"

    @pytest.mark.parametrize("base", ["https://api.deepseek.com", "https://api.openai.com/v1"])
    def test_other_bases_are_untouched(self, base: str) -> None:
        provider = make_provider(self._config(format="openai_compat", api_base=base))
        assert isinstance(provider, OpenAICompatProvider)


# --- sul filo: i provider veri, il rifiuto vero -------------------------------------


class TestSulFilo:
    """Il router con i provider interni veri e il corpo del 400 visto sul telefono.

    È il test che prova la catena intera: ``ProviderHTTPError`` → ``_handle_error``
    → ``error_type`` normalizzato → riconoscimento → secondo endpoint. Con gli
    endpoint finti sopra si prova la logica; qui che i pezzi veri la alimentino.
    """

    async def test_gpt_on_chat_completions_lands_on_responses(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(400, content=MISMATCH_BODY.encode(), headers={
                    "content-type": "application/json",
                })
            return httpx.Response(200, json={
                "status": "completed",
                "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": "ciao da luna"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 3},
            })

        router = OpenCodeRouter(api_key="k", api_base=GO_BASE, default_model="gpt-6-luna")
        for name in ("chat", "responses"):
            inner = router._provider_for(name)
            assert isinstance(inner, OpenAICompatProvider)
            inner._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        result = await router.chat(_USER)

        assert result.finish_reason != "error", result.content
        assert result.content == "ciao da luna"
        assert paths == ["/zen/go/v1/chat/completions", "/zen/go/v1/responses"]
        assert router.endpoint_for() == "responses"
