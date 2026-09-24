"""Base LLM provider interface."""

import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import json_repair
from loguru import logger

from jenny.providers.message_repair import (
    SYNTHETIC_USER_CONTENT,
    enforce_role_alternation,
    sanitize_empty_content,
    strip_image_content,
    strip_image_content_inplace,
)
from jenny.providers.retry_policy import (
    extract_error_type_code,
    is_arrearage_response,
    is_transient_response,
)


class StreamTimeout(asyncio.TimeoutError):
    """Timeout di streaming che si porta dietro quale budget è scaduto.

    Serve a distinguere due messaggi diversi: "il modello non ha ancora detto
    niente" e "lo stream si è piantato a metà".
    """

    def __init__(self, waited_s: float, *, saw_output: bool) -> None:
        super().__init__()
        self.waited_s = waited_s
        self.saw_output = saw_output


class ProviderHTTPError(RuntimeError):
    """Errore HTTP di un provider, con addosso i metadati che lo classificano.

    Esiste perché un ``RuntimeError`` nudo li perde tutti. La catena a valle —
    ``_error_metadata`` → ``is_transient_response`` — legge lo status per
    decidere se ritentare, e senza status ripiega sul testo, dove il marker
    ``"429"`` fa passare per transitorio anche un ``insufficient_quota`` che non
    lo è: quella richiesta veniva ritentata a vuoto fino a esaurire i tentativi.
    Gli attributi hanno i nomi che le eccezioni dell'SDK OpenAI espongono
    (``status_code``, ``headers``, ``body``), così chi le legge non deve
    distinguere le due origini.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: Any = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers
        self.body = body


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""
    id: str
    name: str
    arguments: Any
    extra_content: dict[str, Any] | None = None
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def to_openai_tool_call(self) -> dict[str, Any]:
        """Serialize to an OpenAI-style tool_call payload."""
        arguments = (
            self.arguments
            if isinstance(self.arguments, str)
            else json.dumps(self.arguments, ensure_ascii=False)
        )
        tool_call = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": arguments,
            },
        }
        if self.extra_content:
            tool_call["extra_content"] = self.extra_content
        if self.provider_specific_fields:
            tool_call["provider_specific_fields"] = self.provider_specific_fields
        if self.function_provider_specific_fields:
            tool_call["function"]["provider_specific_fields"] = self.function_provider_specific_fields
        return tool_call


def describe_exc(exc: BaseException) -> str:
    """Descrizione non vuota di un'eccezione, per i messaggi d'errore utente.

    ``str(exc)`` e' vuoto per un'intera famiglia di eccezioni che qui arrivano
    di continuo: tutti i timeout e gli errori di connessione di httpx
    (``ReadTimeout``, ``ConnectTimeout``, ``ConnectError``,
    ``RemoteProtocolError``) si costruiscono senza messaggio, e cosi' fa
    ``StreamTimeout`` qui sopra. Interpolato in un f-string produceva
    ``"Error calling LLM: "`` — cioe' l'utente vedeva un errore troncato ai due
    punti, e nemmeno il log permetteva di risalire alla causa. Il nome della
    classe e' l'unica informazione che resta, ed e' meglio del nulla.
    """
    return str(exc) or type(exc).__name__


def parse_tool_arguments(arguments: Any) -> Any:
    """Parse provider tool arguments without guessing executable parameters.

    Valid JSON object strings become dicts. Empty strings become no-arg calls.
    Malformed JSON and JSON array/scalar values are preserved so ToolRegistry
    can reject them before execution.
    """
    if arguments is None:
        return {}
    if not isinstance(arguments, str):
        return arguments

    stripped = arguments.strip()
    if not stripped:
        return {}

    try:
        parsed = json.loads(stripped)
    except Exception:
        return arguments
    return arguments if parsed is None else parsed


def tool_arguments_object_for_replay(arguments: Any) -> dict[str, Any]:
    """Return object-shaped arguments for provider history replay only.

    This compatibility path may repair malformed JSON because it only shapes
    existing conversation history for provider protocols. Do not use it for
    newly generated tool calls that are about to execute.
    """
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return {}

    stripped = arguments.strip()
    if not stripped:
        return {}

    try:
        parsed = json.loads(stripped)
    except Exception:
        try:
            parsed = json_repair.loads(stripped)
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def tool_arguments_json_for_replay(arguments: Any) -> str:
    """Return JSON object string arguments for provider history replay only."""
    return json.dumps(tool_arguments_object_for_replay(arguments), ensure_ascii=False)


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    retry_after: float | None = None  # Provider supplied retry wait in seconds.
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1, MiMo etc.
    thinking_blocks: list[dict] | None = None  # Anthropic extended thinking
    # Text streamed to the user before a mid-stream exception aborted the
    # response (finish_reason == "error"). None/empty for every other error
    # scenario (auth/quota/non-retryable errors with no prior content, etc.).
    partial_content: str | None = None
    # Structured error metadata used by retry policy when finish_reason == "error".
    error_status_code: int | None = None
    error_kind: str | None = None  # e.g. "timeout", "connection"
    error_type: str | None = None  # Provider/type semantic, e.g. insufficient_quota.
    error_code: str | None = None  # Provider/code semantic, e.g. rate_limit_exceeded.
    error_retry_after_s: float | None = None
    error_should_retry: bool | None = None
    # True quando questa risposta viene dal retry-senza-immagini di
    # _chat_with_retry (il modello ha rifiutato il turno con immagini
    # allegate). Il product layer (loop.py) lo usa per avvisare l'utente.
    images_stripped: bool = False

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0

    @property
    def should_execute_tools(self) -> bool:
        """Tools execute only when has_tool_calls AND finish_reason is a tool-capable stop.
        Blocks gateway-injected calls under ``refusal`` / ``content_filter`` / ``error`` (#3220)."""
        if not self.has_tool_calls:
            return False
        return self.finish_reason in ("tool_calls", "function_call", "stop")


@dataclass(frozen=True)
class GenerationSettings:
    """Default generation settings."""

    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_effort: str | None = None


# Ri-esportato da message_repair per retro-compatibilità (era definito qui).
_SYNTHETIC_USER_CONTENT = SYNTHETIC_USER_CONTENT


class LLMProvider(ABC):
    """Base class for LLM providers."""

    supports_progress_deltas = False

    # Nome del provider come definito in config (``providers.providers[].name``),
    # stampato dalla factory alla creazione; serve alla WebUI per il branding.
    provider_name: str | None = None

    _CHAT_RETRY_DELAYS = (1, 2, 4)
    _PERSISTENT_MAX_DELAY = 60
    _PERSISTENT_IDENTICAL_ERROR_LIMIT = 10
    _RETRY_HEARTBEAT_CHUNK = 30
    # Classificazione retry estratta in ``providers/retry_policy.py``.

    _SENTINEL = object()

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base
        self.generation: GenerationSettings = GenerationSettings()

    # Normalizzazione messaggi estratta in ``providers/message_repair.py``.
    # Delegatori statici sottili: preservano i call-site interni/provider e i
    # test che invocano ``LLMProvider._enforce_role_alternation(...)`` ecc.
    @staticmethod
    def _sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sanitize_empty_content(messages)

    @staticmethod
    def _tool_name(tool: dict[str, Any]) -> str:
        """Extract tool name from either OpenAI or Anthropic-style tool schemas."""
        name = tool.get("name")
        if isinstance(name, str):
            return name
        fn = tool.get("function")
        if isinstance(fn, dict):
            fname = fn.get("name")
            if isinstance(fname, str):
                return fname
        return ""

    @classmethod
    def _tool_cache_marker_indices(cls, tools: list[dict[str, Any]]) -> list[int]:
        """Return cache marker indices: builtin/MCP boundary and tail index."""
        if not tools:
            return []

        tail_idx = len(tools) - 1
        last_builtin_idx: int | None = None
        for i in range(tail_idx, -1, -1):
            if not cls._tool_name(tools[i]).startswith("mcp_"):
                last_builtin_idx = i
                break

        ordered_unique: list[int] = []
        for idx in (last_builtin_idx, tail_idx):
            if idx is not None and idx not in ordered_unique:
                ordered_unique.append(idx)
        return ordered_unique

    @staticmethod
    def _sanitize_request_messages(
        messages: list[dict[str, Any]],
        allowed_keys: frozenset[str],
    ) -> list[dict[str, Any]]:
        """Keep only provider-safe message keys and normalize assistant content."""
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed_keys}
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    @abstractmethod
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
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            tool_choice: Tool selection strategy ("auto", "required", or specific tool dict).

        Returns:
            LLMResponse with content and/or tool calls.
        """
        pass

    # Delegatori sottili verso ``providers/retry_policy.py`` (preservano i
    # call-site interni e i test che usano ``LLMProvider._is_transient_response``
    # / ``.is_arrearage_response`` ecc.).
    @classmethod
    def _is_transient_response(cls, response: LLMResponse) -> bool:
        return is_transient_response(response)

    @classmethod
    def is_arrearage_response(cls, response: LLMResponse) -> bool:
        return is_arrearage_response(response)

    @classmethod
    def _extract_error_type_code(cls, payload: Any) -> tuple[str | None, str | None]:
        return extract_error_type_code(payload)

    @staticmethod
    def _error_headers(e: Exception) -> Any:
        """Gli header di un errore: quelli dell'eccezione, se li porta, poi quelli
        della risposta. ``ProviderHTTPError`` se li tiene addosso perché a quel
        punto la risposta è già chiusa: è la fonte più vicina all'errore."""
        headers = getattr(e, "headers", None)
        if headers is None:
            headers = getattr(getattr(e, "response", None), "headers", None)
        return headers

    @staticmethod
    def _error_payload(e: Exception) -> Any:
        """Il corpo dell'errore, nella forma in cui c'è: ``body``, ``doc``, il testo
        della risposta, il suo JSON.

        La lettura di ``.text`` è protetta: su una risposta in streaming non ancora
        letta solleva ``ResponseNotRead``, e qui l'errore vero è *e*, non il
        fallimento della lettura. Fino al 24/09/2026 la protezione c'era solo
        dal lato Anthropic, e dal lato OpenAI-compat era il gestore d'errore a
        sollevare.
        """
        response = getattr(e, "response", None)
        try:
            payload = (
                getattr(e, "body", None)
                or getattr(e, "doc", None)
                or getattr(response, "text", None)
            )
        except Exception:
            payload = None
        if payload is None and response is not None:
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    payload = response_json()
                except Exception:
                    payload = None
        return payload

    @classmethod
    def _error_metadata(cls, e: Exception, *, payload: Any = None) -> dict[str, Any]:
        """I campi ``error_*`` di un ``LLMResponse`` d'errore, uguali per ogni provider.

        Erano copiati in ``AnthropicProvider._handle_error`` e in
        ``OpenAICompatProvider._extract_error_metadata``, e già divergenti (la
        lettura protetta del corpo, l'ordine degli header). *payload* si passa se
        il chiamante l'ha già letto; ``error_retry_after_s`` viene dagli header e
        basta — il ripiego sul testo del messaggio lo fa chi decide l'attesa.
        """
        response = getattr(e, "response", None)
        headers = cls._error_headers(e)
        if payload is None:
            payload = cls._error_payload(e)
        error_type, error_code = cls._extract_error_type_code(payload)

        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        should_retry: bool | None = None
        if headers is not None:
            raw = headers.get("x-should-retry")
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered == "true":
                    should_retry = True
                elif lowered == "false":
                    should_retry = False

        error_kind: str | None = None
        error_name = e.__class__.__name__.lower()
        if "timeout" in error_name:
            error_kind = "timeout"
        elif "connection" in error_name:
            error_kind = "connection"

        return {
            "error_status_code": int(status_code) if status_code is not None else None,
            "error_kind": error_kind,
            "error_type": error_type,
            "error_code": error_code,
            "error_retry_after_s": cls._extract_retry_after_from_headers(headers),
            "error_should_retry": should_retry,
        }

    @staticmethod
    def _enforce_role_alternation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return enforce_role_alternation(messages)

    @staticmethod
    def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        return strip_image_content(messages)

    @staticmethod
    def _strip_image_content_inplace(messages: list[dict[str, Any]]) -> bool:
        return strip_image_content_inplace(messages)

    async def _safe_chat(self, **kwargs: Any) -> LLMResponse:
        """Call chat() and convert unexpected exceptions to error responses."""
        try:
            return await self.chat(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return LLMResponse(content=f"Error calling LLM: {describe_exc(exc)}", finish_reason="error")

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
        """Stream a chat completion, calling *on_content_delta* for each text chunk.

        *on_thinking_delta* is reserved for providers that expose incremental
        thinking/reasoning on the wire; the default fallback invokes neither
        callback for native deltas (only the optional single *on_content_delta*
        after :meth:`chat`).

        Returns the same ``LLMResponse`` as :meth:`chat`.  The default
        implementation falls back to a non-streaming call and delivers the
        full content as a single delta.  Providers that support native
        streaming should override this method.
        """
        _ = on_thinking_delta, on_tool_call_delta
        response = await self.chat(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
        )
        if on_content_delta and response.content:
            await on_content_delta(response.content)
        return response

    async def _safe_chat_stream(self, **kwargs: Any) -> LLMResponse:
        """Call chat_stream() and convert unexpected exceptions to error responses."""
        try:
            return await self.chat_stream(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return LLMResponse(content=f"Error calling LLM: {describe_exc(exc)}", finish_reason="error")

    async def chat_stream_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_stream_recover: Callable[[], Awaitable[None]] | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Call chat_stream() with retry on transient provider failures."""
        if max_tokens is self._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        has_streamed_content = False
        # Text (never tool-call JSON, see on_tool_call_delta) streamed in the
        # segment currently in flight, and text already flushed from prior
        # segments that stalled and were retried. When a stall is recovered
        # from, the in-flight segment's text is preserved here so the final
        # persisted response reflects everything the user actually saw on
        # screen, not just the last retry attempt (#audit stall-retry loss).
        current_segment_parts: list[str] = []
        prior_segments_text: list[str] = []

        async def _tracking_delta(text: str) -> None:
            nonlocal has_streamed_content
            if text:
                has_streamed_content = True
                current_segment_parts.append(text)
            if on_content_delta:
                await on_content_delta(text)

        async def _recover_stream() -> None:
            nonlocal has_streamed_content
            if current_segment_parts:
                prior_segments_text.append("".join(current_segment_parts))
                current_segment_parts.clear()
            if on_stream_recover:
                await on_stream_recover()
            has_streamed_content = False

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
            on_content_delta=_tracking_delta if on_content_delta is not None else None,
            on_thinking_delta=on_thinking_delta,
            on_tool_call_delta=on_tool_call_delta,
        )
        response = await self._run_with_retry(
            self._safe_chat_stream,
            kw,
            messages,
            retry_mode=retry_mode,
            on_retry_wait=on_retry_wait,
            should_retry_guard=lambda: not has_streamed_content,
            on_stream_recover=_recover_stream if on_stream_recover else None,
        )
        if prior_segments_text:
            # Concatenate text-only content from stalled-and-retried segments
            # ahead of the final attempt's content, in the order it was shown
            # to the user, so history/model context matches the screen.
            prior_text = "".join(prior_segments_text)
            response = replace(response, content=prior_text + (response.content or ""))
        return response

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Call chat() with retry on transient provider failures.

        Parameters default to ``self.generation`` when not explicitly passed,
        so callers no longer need to thread temperature / max_tokens /
        reasoning_effort through every layer. Explicit ``None`` is also
        normalized to the provider's generation defaults so that downstream
        ``_build_kwargs`` never sees ``None`` for ``max_tokens`` / ``temperature``
        (which would crash ``max(1, max_tokens)``).
        """
        if max_tokens is self._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
        )
        return await self._run_with_retry(
            self._safe_chat,
            kw,
            messages,
            retry_mode=retry_mode,
            on_retry_wait=on_retry_wait,
        )

    @classmethod
    def _extract_retry_after(cls, content: str | None) -> float | None:
        text = (content or "").lower()
        patterns = (
            r"retry after\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)?",
            r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)",
            r"wait\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)\s*before retry",
            r"retry[_-]?after[\"'\s:=]+(\d+(?:\.\d+)?)",
        )
        for idx, pattern in enumerate(patterns):
            match = re.search(pattern, text)
            if not match:
                continue
            value = float(match.group(1))
            unit = match.group(2) if idx < 3 else "s"
            return cls._to_retry_seconds(value, unit)
        return None

    @classmethod
    def _to_retry_seconds(cls, value: float, unit: str | None = None) -> float:
        normalized_unit = (unit or "s").lower()
        if normalized_unit in {"ms", "milliseconds"}:
            return max(0.1, value / 1000.0)
        if normalized_unit in {"m", "min", "minutes"}:
            return max(0.1, value * 60.0)
        return max(0.1, value)

    @classmethod
    def _extract_retry_after_from_headers(cls, headers: Any) -> float | None:
        if not headers:
            return None

        def _header_value(name: str) -> Any:
            if hasattr(headers, "get"):
                value = headers.get(name) or headers.get(name.title())
                if value is not None:
                    return value
            if isinstance(headers, dict):
                for key, value in headers.items():
                    if isinstance(key, str) and key.lower() == name.lower():
                        return value
            return None

        with suppress(TypeError, ValueError):
            retry_ms = _header_value("retry-after-ms")
            if retry_ms is not None:
                value = float(retry_ms) / 1000.0
                if value > 0:
                    return value

        retry_after = _header_value("retry-after")
        if retry_after is None:
            return None
        retry_after_text = str(retry_after).strip()
        if not retry_after_text:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", retry_after_text):
            return cls._to_retry_seconds(float(retry_after_text), "s")
        try:
            retry_at = parsedate_to_datetime(retry_after_text)
        except Exception:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        remaining = (retry_at - datetime.now(retry_at.tzinfo)).total_seconds()
        return max(0.1, remaining)

    @classmethod
    def _extract_retry_after_from_response(cls, response: LLMResponse) -> float | None:
        if response.error_retry_after_s is not None and response.error_retry_after_s > 0:
            return response.error_retry_after_s
        if response.retry_after is not None and response.retry_after > 0:
            return response.retry_after
        return cls._extract_retry_after(response.content)

    async def _sleep_with_heartbeat(
        self,
        delay: float,
        *,
        attempt: int,
        persistent: bool,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        remaining = max(0.0, delay)
        while remaining > 0:
            if on_retry_wait:
                kind = "persistent retry" if persistent else "retry"
                await on_retry_wait(
                    f"Model request failed, {kind} in {max(1, int(round(remaining)))}s "
                    f"(attempt {attempt})."
                )
            chunk = min(remaining, self._RETRY_HEARTBEAT_CHUNK)
            await asyncio.sleep(chunk)
            remaining -= chunk

    async def _run_with_retry(
        self,
        call: Callable[..., Awaitable[LLMResponse]],
        kw: dict[str, Any],
        original_messages: list[dict[str, Any]],
        *,
        retry_mode: str,
        on_retry_wait: Callable[[str], Awaitable[None]] | None,
        should_retry_guard: Callable[[], bool] | None = None,
        on_stream_recover: Callable[[], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        attempt = 0
        delays = list(self._CHAT_RETRY_DELAYS)
        persistent = retry_mode == "persistent"
        last_response: LLMResponse | None = None
        last_error_key: str | None = None
        identical_error_count = 0
        while True:
            attempt += 1
            response = await call(**kw)
            if response.finish_reason != "error":
                return response
            last_response = response
            if should_retry_guard is not None and not should_retry_guard():
                is_timeout = (response.error_kind or "").lower() == "timeout"
                if is_timeout:
                    if on_stream_recover:
                        logger.warning(
                            "LLM stream stalled after content was emitted; "
                            "starting a new stream segment and retrying"
                        )
                        await on_stream_recover()
                    else:
                        logger.warning(
                            "LLM stream stalled after content was emitted; "
                            "suppressing delta callbacks and retrying"
                        )
                        kw["on_content_delta"] = None
                        kw["on_thinking_delta"] = None
                        kw["on_tool_call_delta"] = None
                        should_retry_guard = None
                else:
                    logger.warning(
                        "LLM stream failed after content was emitted; skipping retry"
                    )
                    return response
            error_key = ((response.content or "").strip().lower() or None)
            if error_key and error_key == last_error_key:
                identical_error_count += 1
            else:
                last_error_key = error_key
                identical_error_count = 1 if error_key else 0

            if not self._is_transient_response(response):
                stripped = self._strip_image_content(original_messages)
                if stripped is not None and stripped != kw["messages"]:
                    logger.warning(
                        "Non-transient LLM error with image content, retrying without images"
                    )
                    retry_kw = dict(kw)
                    retry_kw["messages"] = stripped
                    result = await call(**retry_kw)
                    # Permanently strip images from the original messages so
                    # subsequent iterations do not repeat the error-retry cycle.
                    if result.finish_reason != "error":
                        self._strip_image_content_inplace(original_messages)
                        result.images_stripped = True
                    return result
                return response

            if persistent and identical_error_count >= self._PERSISTENT_IDENTICAL_ERROR_LIMIT:
                logger.warning(
                    "Stopping persistent retry after {} identical transient errors: {}",
                    identical_error_count,
                    (response.content or "")[:120].lower(),
                )
                if on_retry_wait:
                    await on_retry_wait(
                        f"Persistent retry stopped after {identical_error_count} identical errors."
                    )
                return response

            if not persistent and attempt > len(delays):
                logger.warning(
                    "LLM request failed after {} retries, giving up: {}",
                    attempt,
                    (response.content or "")[:120].lower(),
                )
                if on_retry_wait:
                    await on_retry_wait(
                        f"Model request failed after {attempt} retries, giving up."
                    )
                break

            base_delay = delays[min(attempt - 1, len(delays) - 1)]
            delay = self._extract_retry_after_from_response(response) or base_delay
            if persistent:
                delay = min(delay, self._PERSISTENT_MAX_DELAY)

            logger.warning(
                "LLM transient error (attempt {}{}), retrying in {}s: {}",
                attempt,
                "+" if persistent and attempt > len(delays) else f"/{len(delays)}",
                int(round(delay)),
                (response.content or "")[:120].lower(),
            )
            await self._sleep_with_heartbeat(
                delay,
                attempt=attempt,
                persistent=persistent,
                on_retry_wait=on_retry_wait,
            )

        return last_response if last_response is not None else await call(**kw)

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass
