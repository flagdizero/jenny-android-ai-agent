"""Anthropic provider — httpx-only integration for Claude models."""

from __future__ import annotations

import asyncio
import json
import ssl
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from typing import Any

import httpx
from loguru import logger

from jenny.config.runtime_env import (
    resolve_first_output_timeout_s,
    resolve_stream_idle_timeout_s,
)
from jenny.providers.anthropic_conversion import (
    AnthropicConversionMixin,
    _gen_tool_id,
    derive_tool_id,
    replayable_thinking_blocks,
)
from jenny.providers.anthropic_usage import merge_raw_usage, normalize_usage
from jenny.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    describe_exc,
    parse_tool_arguments,
)
from jenny.providers.body_merge import deep_merge
from jenny.providers.endpoint_budget import is_local_endpoint, request_timeout_s
from jenny.providers.opencode import session_headers
from jenny.providers.tool_ids import dedupe_tool_ids, unique_tool_ids_in_history


class AnthropicProvider(AnthropicConversionMixin, LLMProvider):
    """LLM provider using the Anthropic Messages API over httpx for Claude models.

    Handles message format conversion (OpenAI → Anthropic Messages API),
    prompt caching, extended thinking, tool calls, and streaming.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "claude-sonnet-4-20250514",
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_query: dict[str, str] | None = None,
        api_type: str = "auto",
        ssl_context: ssl.SSLContext | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._extra_body = extra_body or {}
        self._extra_query = extra_query or {}
        if api_type and api_type != "auto":
            # ``api_type`` sceglie fra Chat Completions e Responses API, che sono
            # due dialetti OpenAI: qui non significa niente. Meglio dirlo che
            # ignorarlo in silenzio, come si faceva con extra_body/extra_query.
            logger.warning(
                "Provider apiType={!r} has no meaning for the Anthropic format; ignoring",
                api_type,
            )
        # Contesto TLS del provider: ``None`` = default di httpx. Lo costruisce
        # il factory (v. ``providers/tls.py``), qui si inoltra e basta.
        self._ssl_context = ssl_context
        # Ripiego di ``x-opencode-session`` per le richieste che partono senza
        # scope di conversazione: v. ``providers/opencode.py``.
        self._session_affinity_id = uuid.uuid4().hex
        self._http_client: httpx.AsyncClient | None = None
        self._init_http_client()

    def _init_http_client(self) -> None:
        base_url = self._normalize_base_url(self.api_base or "https://api.anthropic.com")
        headers: dict[str, str] = {
            "x-api-key": self.api_key or "no-key",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self.extra_headers:
            headers.update(self.extra_headers)
        # Il timeout era 120s scritti a mano, senza override e senza il caso
        # loopback: rendeva irraggiungibile il budget lungo per il primo token
        # (``resolve_first_output_timeout_s``, 300s di default), perché la read
        # timeout scadeva prima. Adesso la regola è quella condivisa.
        self._is_local = is_local_endpoint(self.api_base)
        self._http_client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=request_timeout_s(local=self._is_local),
            # Senza CA di provider resta ``True``, che e' esattamente il default
            # di httpx: la fiducia di default non la ridefiniamo noi.
            verify=self._ssl_context or True,
        )

    def _request_headers(self) -> dict[str, str] | None:
        """Header da sovrapporre a quelli del client, o ``None`` se non servono.

        Il client httpx monta i suoi header una volta sola alla costruzione
        (``_init_http_client``), ma ``x-opencode-session`` cambia con la
        conversazione: va quindi passato per richiesta. ``None`` e non ``{}``
        fuori da OpenCode, così ogni altro endpoint riceve la chiamata
        *identica* a quella di prima invece di un merge a vuoto.

        Il gate legge ``api_base`` grezzo: ``_normalize_base_url`` toglie il
        ``/v1`` finale ma l'host resta, e l'host è tutto ciò che guarda.

        **Quello che l'utente ha scritto in ``extraHeaders`` vince**, e va tolto
        qui a mano: gli ``extraHeaders`` stanno sul client, e in httpx un header
        per richiesta *sovrascrive* quello del client, cioè il contrario del
        ramo OpenAI — lì finiscono entrambi nello stesso dict e basta un
        ``setdefault``. Senza questo filtro l'unica via per forzare un header su
        questo formato smetterebbe di funzionare proprio sui nomi che servono.
        Il confronto è case-insensitive perché i nomi degli header lo sono.
        """
        headers = session_headers(self.api_base, fallback_id=self._session_affinity_id)
        if headers and self.extra_headers:
            taken = {name.lower() for name in self.extra_headers}
            headers = {k: v for k, v in headers.items() if k.lower() not in taken}
        return headers or None

    @staticmethod
    def _normalize_base_url(api_base: str) -> str:
        """Anthropic SDK appends /v1 to request paths internally."""
        normalized = api_base.rstrip("/")
        if normalized.endswith("/v1"):
            return normalized[: -len("/v1")]
        return normalized

    @classmethod
    def _handle_error(cls, e: Exception, *, partial_content: str | None = None) -> LLMResponse:
        # I metadati sono quelli di ogni provider (``LLMProvider._error_metadata``);
        # qui resta il messaggio, che per Anthropic è il corpo dell'errore.
        payload = cls._error_payload(e)
        payload_text = payload if isinstance(payload, str) else str(payload) if payload is not None else ""
        msg = f"Error: {payload_text.strip()[:500]}" if payload_text.strip() else f"Error calling LLM: {describe_exc(e)}"
        metadata = cls._error_metadata(e, payload=payload)
        retry_after = metadata["error_retry_after_s"]
        if retry_after is None:
            retry_after = LLMProvider._extract_retry_after(msg)
        # Qui il ritardo scritto nel messaggio va anche nel campo strutturato,
        # com'è sempre stato per Anthropic: chi decide l'attesa li legge entrambi.
        metadata["error_retry_after_s"] = retry_after
        return LLMResponse(
            content=msg,
            finish_reason="error",
            retry_after=retry_after,
            partial_content=partial_content or None,
            **metadata,
        )

    @staticmethod
    def _strip_prefix(model: str) -> str:
        if model.startswith("anthropic/"):
            return model[len("anthropic/"):]
        return model

    # ------------------------------------------------------------------
    # Message conversion: OpenAI chat format → Anthropic Messages API
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # Build API kwargs
    # ------------------------------------------------------------------

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        model_name = self._strip_prefix(model or self.default_model)
        # Passaggio di unicità sul formato *interno*, prima della conversione:
        # è la stessa funzione che usa l'OpenAI-compat provider, e serve a
        # risanare le sessioni in cui un id duplicato è già stato persistito —
        # senza, ogni richiesta successiva rimanda lo stesso duplicato e la
        # conversazione resta murata finché non la si cancella. Su una history
        # sana è un no-op.
        prepared = unique_tool_ids_in_history(
            self._sanitize_empty_content(messages),
            fresh_id=_gen_tool_id,
            derive_id=derive_tool_id,
        )
        system, anthropic_msgs = self._convert_messages(prepared)
        anthropic_tools = self._convert_tools(tools)

        system, anthropic_msgs, anthropic_tools = self._apply_cache_control(
            system, anthropic_msgs, anthropic_tools,
        )

        max_tokens = max(1, max_tokens)
        thinking_enabled = bool(reasoning_effort) and reasoning_effort.lower() != "none"

        # Several Anthropic models (opus-4-7, opus-4-8, fable) deprecated the
        # `temperature` parameter — the API returns 400 if it is present.
        _model_lower = model_name.lower()
        omit_temperature = any(m in _model_lower for m in ("opus-4-7", "opus-4-8", "fable"))

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
        }

        if system:
            kwargs["system"] = system

        if reasoning_effort == "adaptive":
            # Adaptive thinking: model decides when and how much to think
            # Supported on claude-sonnet-4-6 and claude-opus-4-6.
            # Also auto-enables interleaved thinking between tool calls.
            kwargs["thinking"] = {"type": "adaptive"}
            if not omit_temperature:
                kwargs["temperature"] = 1.0
        elif thinking_enabled:
            budget_map = {"low": 1024, "medium": 4096, "high": max(8192, max_tokens)}
            budget = budget_map.get(reasoning_effort.lower(), 4096)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["max_tokens"] = max(max_tokens, budget + 4096)
            if not omit_temperature:
                kwargs["temperature"] = 1.0
        elif not omit_temperature:
            kwargs["temperature"] = temperature

        # ``tool_choice: "none"`` si rispetta togliendo i tool dal body: l'unico
        # modo che ogni gateway accetta. Lasciarli con un tool_choice assente
        # significa lasciare il default (auto), cioè non rispettarlo affatto.
        if anthropic_tools and str(tool_choice or "").strip().lower() != "none":
            kwargs["tools"] = anthropic_tools
            tc = self._convert_tool_choice(tool_choice, thinking_enabled)
            if tc:
                kwargs["tool_choice"] = tc

        # ``extra_headers`` viaggia negli header, dove ``_init_http_client`` lo
        # ha già messo. Qui dentro finiva nel CORPO della richiesta — residuo di
        # quando questo provider passava dall'SDK, per cui era un kwarg del
        # client — e l'API rifiuta i campi di body che non conosce.
        if self._extra_body:
            kwargs = deep_merge(kwargs, self._extra_body)

        return kwargs

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _unique_stream_id(raw_id: str, index: Any, used: set[str]) -> str:
        """Id libero per un ``tool_use`` appena annunciato in streaming.

        Come ``tool_ids._unique_id``, ma applicato all'apertura del blocco
        anziché a stream concluso: qui l'id viene subito emesso ai consumatori
        dei delta, e cambiarlo dopo li disallineerebbe.
        """
        if raw_id and raw_id not in used:
            return raw_id
        seed = raw_id or "toolu"
        position = index if isinstance(index, int) else 0
        salt = 1
        while True:
            candidate = derive_tool_id(seed, position, salt)
            if candidate not in used:
                return candidate
            salt += 1

    @staticmethod
    def _unique_call_ids(blocks: list[dict[str, Any]]) -> list[str]:
        """Id univoci per i tool_use di *una* risposta, in ordine di arrivo.

        GLM (e chiunque parli questo wire-format dietro un gateway) riusa lo
        stesso id per le chiamate parallele. Va corretto qui, in parsing, non
        solo in invio: a valle l'id è una chiave, e un duplicato fa scomparire un
        evento dal transcript e fa collidere due risultati grossi sullo stesso
        file su disco (vedi ``providers/tool_ids.py``).
        """
        return dedupe_tool_ids(
            [block.get("id") for block in blocks],
            replacement=lambda raw, idx: derive_tool_id(str(raw or "toolu"), idx),
        )

    @classmethod
    def _parse_response_dict(cls, response: dict[str, Any]) -> LLMResponse:
        """Parse a dict Anthropic Messages API response."""
        content_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        thinking_blocks: list[dict[str, Any]] = []

        for block in response.get("content", []):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if text:
                    content_parts.append(text)
            elif block_type == "tool_use":
                tool_uses.append(block)
            elif block_type in ("thinking", "redacted_thinking"):
                thinking_blocks.append(block)

        tool_calls = [
            ToolCallRequest(
                id=unique_id,
                name=block.get("name", ""),
                arguments=block.get("input", {}),
            )
            for block, unique_id in zip(tool_uses, cls._unique_call_ids(tool_uses))
        ]

        stop_reason = response.get("stop_reason") or "stop"
        stop_map = {"tool_use": "tool_calls", "end_turn": "stop", "max_tokens": "length"}
        finish_reason = stop_map.get(stop_reason, stop_reason)

        usage = normalize_usage(response.get("usage") or {})

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            thinking_blocks=replayable_thinking_blocks(thinking_blocks) or None,
        )

    # ------------------------------------------------------------------
    # HTTP streaming helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _iter_anthropic_sse(
        response: httpx.Response,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield parsed Anthropic Messages API SSE events as dicts."""
        event_type: str | None = None
        data_buffer: list[str] = []

        def _flush() -> dict[str, Any] | None:
            nonlocal event_type
            data = "\n".join(data_buffer).strip()
            event_type_local = event_type
            event_type = None
            data_buffer.clear()
            if not data or data == "[DONE]":
                return None
            try:
                parsed = json.loads(data)
            except Exception:
                logger.warning("Failed to parse Anthropic SSE JSON: {}", data[:200])
                return None
            if isinstance(parsed, dict) and event_type_local:
                parsed["_event_type"] = event_type_local
            return parsed

        async for line in response.aiter_lines():
            if line == "":
                event = _flush()
                if event is not None:
                    yield event
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
                continue
            if line.startswith("data:"):
                data_buffer.append(line[5:].strip())
                continue

        event = _flush()
        if event is not None:
            yield event

    async def _http_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> LLMResponse:
        """Non-streaming Anthropic chat via httpx."""
        kwargs = self._build_kwargs(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        response = await self._http_client.post(
            "/v1/messages", json=kwargs, params=self._extra_query or None,
            headers=self._request_headers(),
        )
        # HTTPStatusError sale intatta fino a chat(): riavvolgerla in una
        # RuntimeError butterebbe via ``.response``, e con essa status code,
        # retry-after e error_type di cui vive la retry policy.
        response.raise_for_status()
        return self._parse_response_dict(response.json())

    async def _http_chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Streaming Anthropic chat via httpx."""
        kwargs = self._build_kwargs(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        kwargs["stream"] = True
        idle_timeout_s = resolve_stream_idle_timeout_s()
        first_output_timeout_s = max(
            resolve_first_output_timeout_s(local=self._is_local), idle_timeout_s,
        )
        # Prima del primo blocco di contenuto vale il budget lungo: il modello
        # sta ancora ragionando e il silenzio è previsto.
        saw_output = False

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_blocks: dict[str, dict[str, Any]] = {}
        thinking_buffers: dict[str, dict[str, Any]] = {}
        finish_reason = "stop"
        raw_usage: dict[str, Any] = {}

        try:
            async with self._http_client.stream(
                "POST", "/v1/messages", json=kwargs, params=self._extra_query or None,
                headers=self._request_headers(),
            ) as response:
                if response.status_code >= 400:
                    # Il body di uno stream non è ancora stato letto e il
                    # context manager lo chiude prima che l'except giri: senza
                    # questa aread() ogni accesso a ``.text`` a valle solleva
                    # ResponseNotRead e maschera lo status reale.
                    with suppress(Exception):
                        await response.aread()
                response.raise_for_status()
                sse_iter = self._iter_anthropic_sse(response).__aiter__()
                while True:
                    try:
                        event = await asyncio.wait_for(
                            sse_iter.__anext__(),
                            timeout=idle_timeout_s if saw_output else first_output_timeout_s,
                        )
                    except StopAsyncIteration:
                        break

                    event_type = event.get("_event_type") or event.get("type")
                    if not saw_output and event_type in (
                        "content_block_start", "content_block_delta",
                    ):
                        saw_output = True
                    if event_type == "content_block_start":
                        block = event.get("content_block") or {}
                        index = event.get("index", 0)
                        block_type = block.get("type")
                        if block_type == "tool_use":
                            # Deduplica QUI, non alla fine dello stream: i
                            # consumatori dei delta (tracker file-edit, UI)
                            # chiavano per ``call_id``, e un id corretto solo a
                            # posteriori li lascerebbe con eventi live su un id e
                            # quelli finali su un altro.
                            call_id = self._unique_stream_id(
                                str(block.get("id") or ""),
                                index,
                                {buf["id"] for buf in tool_blocks.values()},
                            )
                            tool_blocks[str(index)] = {
                                "id": call_id,
                                "name": block.get("name", ""),
                                "arguments": "",
                            }
                            if on_tool_call_delta:
                                await on_tool_call_delta({
                                    "index": index,
                                    "call_id": call_id,
                                    "name": str(block.get("name") or ""),
                                    "arguments_delta": "",
                                })
                        elif block_type == "thinking":
                            thinking_buffers[str(index)] = {
                                "type": "thinking",
                                "thinking": str(block.get("thinking") or ""),
                                "signature": str(block.get("signature") or ""),
                            }
                        elif block_type == "redacted_thinking":
                            thinking_buffers[str(index)] = {
                                "type": "redacted_thinking",
                                "data": str(block.get("data") or ""),
                            }
                    elif event_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        index = event.get("index", 0)
                        delta_type = delta.get("type")
                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                content_parts.append(text)
                                if on_content_delta:
                                    await on_content_delta(text)
                        elif delta_type == "thinking_delta":
                            text = delta.get("thinking", "")
                            if text:
                                reasoning_parts.append(text)
                                buf = thinking_buffers.get(str(index))
                                if buf is not None and "thinking" in buf:
                                    buf["thinking"] += text
                                if on_thinking_delta:
                                    await on_thinking_delta(text)
                        elif delta_type == "signature_delta":
                            # La firma è ciò che rende un blocco thinking
                            # RIMANDABILE indietro: con thinking + tool use
                            # l'API pretende di riavere i blocchi firmati del
                            # turno, e senza questo delta non ne conservavamo
                            # nemmeno uno.
                            buf = thinking_buffers.get(str(index))
                            if buf is not None and "signature" in buf:
                                buf["signature"] += str(delta.get("signature") or "")
                        elif delta_type == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            if partial:
                                buf = tool_blocks.get(str(index))
                                if buf is not None:
                                    buf["arguments"] += partial
                                if on_tool_call_delta:
                                    await on_tool_call_delta({
                                        "index": index,
                                        "call_id": str(buf.get("id") if buf else ""),
                                        "name": str(buf.get("name") if buf else ""),
                                        "arguments_delta": partial,
                                    })
                    elif event_type == "message_start":
                        # Gli input token e le voci di cache arrivano SOLO qui:
                        # ``message_delta`` porta gli output. Leggendo solo
                        # quello, ``prompt_tokens`` restava a zero e il
                        # risparmio del prompt caching era invisibile.
                        message = event.get("message")
                        if isinstance(message, dict):
                            merge_raw_usage(raw_usage, message.get("usage"))
                    elif event_type == "message_delta":
                        delta = event.get("delta") or {}
                        if delta.get("stop_reason"):
                            finish_reason = delta["stop_reason"]
                        merge_raw_usage(raw_usage, event.get("usage"))

        except asyncio.TimeoutError:
            waited_s = idle_timeout_s if saw_output else first_output_timeout_s
            return LLMResponse(
                content=(
                    f"Error calling LLM: stream stalled for more than "
                    f"{waited_s:g} seconds"
                    if saw_output
                    else f"Error calling LLM: no output from the model within "
                    f"{waited_s:g} seconds"
                ),
                finish_reason="error",
                error_kind="timeout",
            )
        except httpx.HTTPStatusError as e:
            # Passa da _handle_error: status code, retry-after e error_type
            # arrivano così alla retry policy, che altrimenti vedrebbe solo
            # testo e non riproverebbe un 429.
            return self._handle_error(
                e, partial_content="".join(content_parts) or None,
            )
        except Exception as exc:
            # Mirrors LLMProvider._safe_chat_stream's generic error message so
            # behavior is unchanged when there is no partial content to carry;
            # this only adds partial_content when the stream had already
            # produced text before crashing (#audit mid-stream-exception loss).
            return LLMResponse(
                content=f"Error calling LLM: {describe_exc(exc)}",
                finish_reason="error",
                partial_content="".join(content_parts) or None,
            )

        stop_map = {"tool_use": "tool_calls", "end_turn": "stop", "max_tokens": "length"}
        bufs = list(tool_blocks.values())
        # ``parse_tool_arguments``, non la variante "for_replay": queste tool
        # call stanno per essere ESEGUITE, e la variante di replay ripara il JSON
        # malformato (vedi il suo docstring in ``base.py``). Su uno stream
        # troncato a metà di un ``input_json_delta`` quella riparazione
        # inventerebbe argomenti plausibili e li si eseguirebbe; meglio lasciare
        # la stringa grezza, che il registry rifiuta.
        tool_calls = [
            ToolCallRequest(
                id=unique_id,
                name=buf.get("name", ""),
                arguments=parse_tool_arguments(buf.get("arguments", "{}")),
            )
            for buf, unique_id in zip(bufs, self._unique_call_ids(bufs))
        ]
        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=stop_map.get(finish_reason, finish_reason),
            usage=normalize_usage(raw_usage),
            reasoning_content="".join(reasoning_parts) or None,
            thinking_blocks=replayable_thinking_blocks(thinking_buffers.values()) or None,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        try:
            return await self._http_chat(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
        except Exception as e:
            return self._handle_error(e)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        return await self._http_chat_stream(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
            on_content_delta=on_content_delta,
            on_thinking_delta=on_thinking_delta,
            on_tool_call_delta=on_tool_call_delta,
        )

    def get_default_model(self) -> str:
        return self.default_model
