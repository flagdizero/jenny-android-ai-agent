"""Esecuzione richiesta modello + finalizzazione per l'``AgentRunner``.

`RequestExecutionMixin` isola la chiamata al provider (`_request_model`: build
kwargs, streaming/think-extraction, progress file-edit) e i percorsi di
finalizzazione (retry di chiusura, no-tools, budget esaurito, fallback max-iter).
Mixato in ``AgentRunner``: gli altri metodi/attributi risolvono per MRO. Nessun
import runtime verso ``runner`` → nessun ciclo.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.hook import AgentHook, AgentHookContext
from jenny.agent.progress_events import (
    invoke_file_edit_progress,
    on_progress_accepts_file_edit_events,
)
from jenny.providers.base import LLMResponse
from jenny.utils.file_edit_streaming import StreamingFileEditTracker
from jenny.utils.helpers import IncrementalAnswerStepper
from jenny.utils.prompt_templates import render_template
from jenny.utils.runtime import (
    build_budget_exhausted_finalization_message,
    build_finalization_retry_message,
    is_blank_text,
)

if TYPE_CHECKING:
    from jenny.agent.runner import AgentRunSpec


@dataclass(frozen=True, slots=True)
class RequestOverrides:
    """Parametri di generazione che sovrascrivono lo ``spec`` per una richiesta.

    Esiste perché i recovery devono poter cambiare *i parametri* della richiesta
    (non solo la conversazione) senza mutare lo ``spec``, che è la config del
    chiamante e vale per tutto il run. Portata da ``_RunCounters`` e resettata
    coi contatori dopo una fase tool riuscita.
    """

    max_tokens: int | None = None
    reasoning_effort: str | None = None


class RequestExecutionMixin:
    """Chiamata modello + finalizzazione (mixin di AgentRunner)."""

    def _build_request_kwargs(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        overrides: RequestOverrides | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "tools": tools,
            "model": spec.model,
            "retry_mode": spec.provider_retry_mode,
            "on_retry_wait": spec.retry_wait_callback,
        }
        max_tokens = spec.max_tokens
        reasoning_effort = spec.reasoning_effort
        if overrides is not None:
            if overrides.max_tokens is not None:
                max_tokens = overrides.max_tokens
            if overrides.reasoning_effort is not None:
                reasoning_effort = overrides.reasoning_effort
        if spec.temperature is not None:
            kwargs["temperature"] = spec.temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        if spec.tool_choice is not None:
            kwargs["tool_choice"] = spec.tool_choice
        return kwargs

    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        hook: AgentHook,
        context: AgentHookContext,
        overrides: RequestOverrides | None = None,
    ):
        timeout_s: float | None = spec.llm_timeout_s
        if timeout_s is None:
            # Default to a finite timeout to avoid per-session lock starvation when an LLM
            # request hangs indefinitely (e.g. gateway/network stall).
            # Set JENNY_LLM_TIMEOUT_S=0 to disable.
            from jenny.config.runtime_env import llm_timeout_s

            timeout_s = llm_timeout_s()
        if timeout_s is not None and timeout_s <= 0:
            timeout_s = None

        kwargs = self._build_request_kwargs(
            spec,
            messages,
            tools=spec.tools.get_definitions(),
            overrides=overrides,
        )
        wants_streaming = hook.wants_streaming()
        wants_progress_streaming = (
            not wants_streaming
            and spec.stream_progress_deltas
            and spec.progress_callback is not None
            and getattr(self.provider, "supports_progress_deltas", False) is True
        )

        progress_state: dict[str, bool] | None = None
        live_file_edits: StreamingFileEditTracker | None = None

        if (
            spec.progress_callback is not None
            and on_progress_accepts_file_edit_events(spec.progress_callback)
        ):
            async def _emit_live_file_edits(events: list[dict[str, Any]]) -> None:
                await invoke_file_edit_progress(spec.progress_callback, events)

            live_file_edits = StreamingFileEditTracker(
                workspace=spec.workspace,
                tools=spec.tools,
                emit=_emit_live_file_edits,
            )

        async def _tool_call_delta(delta: dict[str, Any]) -> None:
            if live_file_edits is not None:
                await live_file_edits.update(delta)

        if wants_streaming:
            async def _stream(delta: str) -> None:
                if delta:
                    context.streamed_content = True
                await hook.on_stream(context, delta)

            async def _thinking(delta: str) -> None:
                if not delta:
                    return
                context.streamed_reasoning = True
                await hook.emit_reasoning(delta)

            async def _stream_recover() -> None:
                await hook.on_stream_end(context, resuming=True)

            coro = self.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream,
                on_thinking_delta=_thinking,
                on_tool_call_delta=_tool_call_delta if live_file_edits is not None else None,
                on_stream_recover=_stream_recover,
            )
        elif wants_progress_streaming:
            stepper = IncrementalAnswerStepper()
            progress_state = {"reasoning_open": False}

            async def _stream_progress(delta: str) -> None:
                if not delta:
                    return
                incremental, emitted = await stepper.feed(delta, hook.emit_reasoning)
                if emitted:
                    context.streamed_reasoning = True
                    progress_state["reasoning_open"] = True

                if incremental:
                    if progress_state["reasoning_open"]:
                        await hook.emit_reasoning_end()
                        progress_state["reasoning_open"] = False
                    context.streamed_content = True
                    await spec.progress_callback(incremental)

            coro = self.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream_progress,
                on_tool_call_delta=_tool_call_delta if live_file_edits is not None else None,
            )
        else:
            coro = self.provider.chat_with_retry(**kwargs)

        # Streaming requests already have provider-level idle timeouts
        # (JENNY_STREAM_IDLE_TIMEOUT_S). Do not also apply the outer wall-clock
        # LLM timeout here, or healthy long reasoning streams can be killed just
        # because total elapsed time exceeded JENNY_LLM_TIMEOUT_S.
        outer_timeout_s = None if (wants_streaming or wants_progress_streaming) else timeout_s
        try:
            response = (
                await coro if outer_timeout_s is None
                else await asyncio.wait_for(coro, timeout=outer_timeout_s)
            )
            if live_file_edits is not None:
                await live_file_edits.flush()
                if response.should_execute_tools:
                    live_file_edits.apply_final_call_ids(response.tool_calls)
                await live_file_edits.error_unmatched(
                    response.tool_calls if response.should_execute_tools else [],
                    "Tool call did not complete.",
                )
        except asyncio.TimeoutError:
            if outer_timeout_s is None:
                return LLMResponse(
                    content="Error calling LLM: stream stalled",
                    finish_reason="error",
                    error_kind="timeout",
                )
            return LLMResponse(
                content=f"Error calling LLM: timed out after {outer_timeout_s:g}s",
                finish_reason="error",
                error_kind="timeout",
            )
        if progress_state and progress_state.get("reasoning_open"):
            await hook.emit_reasoning_end()
        return response

    async def _request_finalization_retry(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        overrides: RequestOverrides | None = None,
    ) -> tuple[LLMResponse, list[dict[str, Any]]]:
        retry_messages = self._finalization_retry_messages(messages)
        response = await self._request_no_tools(spec, retry_messages, overrides)
        return response, retry_messages

    @staticmethod
    def _finalization_retry_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        retry_messages.append(build_finalization_retry_message())
        return retry_messages

    async def _try_finalize_after_max_iterations(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
        usage: dict[str, int],
    ) -> str | None:
        retry_messages = self._budget_exhausted_finalization_messages(
            messages, getattr(spec, "max_iterations_finalization_prompt", None),
        )
        try:
            response = await self._request_no_tools(spec, retry_messages)
        except Exception:
            logger.exception(
                "Budget-exhausted finalization failed for {}; using fallback",
                spec.session_key or "default",
            )
            return None

        raw_usage = self._usage_or_estimate(spec, retry_messages, response)
        self._accumulate_usage(usage, raw_usage)
        if response.finish_reason == "error" or response.has_tool_calls:
            logger.warning(
                "Budget-exhausted finalization returned finish_reason='{}' "
                "with {} tool call(s) for {}; using fallback",
                response.finish_reason,
                len(response.tool_calls),
                spec.session_key or "default",
            )
            return None

        context = AgentHookContext(
            iteration=spec.max_iterations,
            messages=messages,
            response=response,
            usage=dict(raw_usage),
            session_key=spec.session_key,
        )
        clean = hook.finalize_content(context, response.content)
        if is_blank_text(clean):
            return None
        return clean

    async def _request_no_tools(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        overrides: RequestOverrides | None = None,
    ) -> LLMResponse:
        kwargs = self._build_request_kwargs(spec, messages, tools=None, overrides=overrides)
        return await self.provider.chat_with_retry(**kwargs)

    @staticmethod
    def _budget_exhausted_finalization_messages(
        messages: list[dict[str, Any]],
        prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        retry_messages.append(build_budget_exhausted_finalization_message(prompt))
        return retry_messages

    @staticmethod
    def _max_iterations_fallback(spec: AgentRunSpec) -> str:
        if spec.max_iterations_message:
            return spec.max_iterations_message.format(
                max_iterations=spec.max_iterations,
            )
        return render_template(
            "agent/max_iterations_message.md",
            strip=True,
            max_iterations=spec.max_iterations,
        )
