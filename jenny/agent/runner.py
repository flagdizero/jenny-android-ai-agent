"""Shared execution loop for tool-using agents."""

from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from loguru import logger

from jenny.agent.context_governor import (
    extract_context_limit,
    is_context_length_error,
    snip_history,
)
from jenny.agent.history_repair import (
    BACKFILL_CONTENT,
    MICROCOMPACT_KEEP_RECENT,
    backfill_missing_tool_results,
    drop_orphan_tool_results,
    microcompact,
)
from jenny.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from jenny.agent.request_execution import RequestExecutionMixin, RequestOverrides
from jenny.agent.response_outcome import (
    ResponseOutcome,
    classify_response,
    reported_completion_tokens,
)
from jenny.agent.tool_error_policy import (
    classify_violation,
)
from jenny.agent.tool_execution import ToolErrorBudget, ToolExecutionMixin
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.usage_accounting import (
    accumulate_usage,
    merge_usage,
    usage_or_estimate,
)
from jenny.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from jenny.providers.opencode import conversation_scope
from jenny.utils.helpers import (
    build_assistant_message,
    extract_reasoning,
    maybe_persist_tool_result,
    merge_message_content,
    truncate_text,
)
from jenny.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    OUTPUT_TRUNCATED_MESSAGE,
    build_goal_continue_message,
    build_length_recovery_message,
    ensure_nonempty_tool_result,
    is_blank_text,
    looks_like_user_question,
)

GoalContinueMessage = str | Callable[[], str | None]

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
_ARREARAGE_ERROR_MESSAGE = (
    "The AI provider rejected the request because the API key is out of quota or the "
    "account is in arrears. Please top up / check the billing status of your API key and try again."
)
_MAX_CONTEXT_LENGTH_RETRIES = 2  # Max attempts to recover from context_length errors per turn
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"
_PARTIAL_CONTENT_INTERRUPTED_MARKER = "[response was interrupted by an error]"
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
# Tentativi per il troncamento a contenuto vuoto. Ogni tentativo deve differire
# dal precedente (budget raddoppiato, poi anche effort abbassato): ripetere una
# richiesta identica contro lo stesso tetto non può che ri-troncare.
_MAX_BLANK_TRUNCATION_RETRIES = 2
# Effort di fallback, per impedire al thinking di rimangiarsi tutto il budget.
# Normalmente applicato dal secondo tentativo (così, se il gateway rifiuta il
# parametro, un tentativo col solo budget alzato è già stato speso), ma subito
# quando la finestra non lascia spazio per alzare il budget.
_TRUNCATION_FALLBACK_EFFORT = "low"
_TRUNCATION_LOW_EFFORTS = frozenset({"none", "minimal", "minimum", "low"})
# Margine lasciato libero nella finestra quando si alza il budget di output.
_OUTPUT_BUDGET_HEADROOM = 2048
_MAX_INJECTIONS_PER_TURN = 3
_MAX_INJECTION_CYCLES = 5
# Backstop per run sulle goal-continuation sintetiche. Alto di proposito: un goal
# che avanza davvero (ogni nudge preceduto da lavoro con i tool, v.
# ``_goal_continue_allowed``) non deve sbatterci mai contro; serve solo a rendere
# impossibile un loop illimitato dentro i 200 giri di ``max_tool_iterations``.
_MAX_GOAL_CONTINUE_CYCLES = 30
# Oltre questa soglia il log passa a WARNING: un turno con dieci nudge è
# anomalo e deve essere visibile in logcat senza doverlo cercare.
_GOAL_CONTINUE_WARN_AT = 10
# read_file is the recovery path for persisted results; exempting it prevents persist->read->persist loops.
_TOOL_RESULT_OFFLOAD_EXEMPT_TOOLS = frozenset({"read_file"})

# Retro-compat: costanti spostate in agent.history_repair, ri-esportate qui coi
# vecchi nomi underscore così i call-site/test esistenti restano invariati.
_BACKFILL_CONTENT = BACKFILL_CONTENT
_MICROCOMPACT_KEEP_RECENT = MICROCOMPACT_KEEP_RECENT


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for a single agent execution."""

    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    max_iterations: int
    max_tool_result_chars: int
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    hook: AgentHook | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = None
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False
    # Quanti errori tool recuperabili il run tollera prima che
    # ``fail_on_tool_error`` scatti. ``None`` = comportamento storico (il primo
    # errore chiude il run); un intero attiva il budget di
    # ``ToolErrorBudget``. Senza ``fail_on_tool_error`` e ignorato: nessun errore
    # tool e fatale e non c'e niente da razionare.
    tool_error_budget: int | None = None
    workspace: Path | None = None
    session_key: str | None = None
    context_window_tokens: int | None = None
    context_block_limit: int | None = None
    provider_retry_mode: str = "standard"
    progress_callback: Any | None = None
    stream_progress_deltas: bool = True
    retry_wait_callback: Any | None = None
    checkpoint_callback: Any | None = None
    injection_callback: Any | None = None
    llm_timeout_s: float | None = None
    tool_choice: str | None = None
    goal_active_predicate: Callable[[], bool] | None = None
    goal_continue_message: GoalContinueMessage | None = None
    finalize_on_max_iterations: bool = True
    on_context_overflow: Callable[[int], Any] | None = None  # Called when context_length error; receives current window, returns new window


@dataclass(slots=True)
class AgentRunResult:
    """Outcome of a shared agent execution."""

    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False
    images_stripped: bool = False
    # True quando un goal sostenuto era attivo ma il run ha rifiutato di
    # spronarlo: sta aspettando l'utente. Il product layer lo usa per
    # parcheggiare il goal invece di lasciare che il modello lo chiuda.
    goal_stalled: bool = False


@dataclass
class _RunCounters:
    """Mutable per-run state shared across the branches of ``_run_core``.

    Raggruppa i ~13 locali che i rami del loop leggono e scrivono, con gli stessi
    default della versione monolitica. Serve a rendere impossibile la classe di bug
    "un ramo dimentica di resettare un contatore": ora i reset (es.
    ``empty_content_retries``/``length_recovery_count`` dopo i tool) sono attributi
    di questo oggetto passato per riferimento agli helper.
    """

    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )
    error: str | None = None
    stop_reason: str = "completed"
    tool_events: list[dict[str, str]] = field(default_factory=list)
    external_lookup_counts: dict[str, int] = field(default_factory=dict)
    # Per-turn throttle for repeated attempts against the same outside target.
    workspace_violation_counts: dict[str, int] = field(default_factory=dict)
    # Tolleranza agli errori tool, contata sul RUN e non sul turno: e la sua
    # continuita fra un turno e l'altro che rende osservabile un agente che
    # sbaglia un po' ogni volta senza mai fermarsi.
    tool_errors: ToolErrorBudget = field(default_factory=ToolErrorBudget)
    empty_content_retries: int = 0
    length_recovery_count: int = 0
    blank_truncation_retries: int = 0
    context_length_retries: int = 0
    # Override di generazione per la prossima richiesta (budget/effort alzati dal
    # recovery del troncamento). Resettato coi contatori dopo una fase tool.
    request_overrides: RequestOverrides | None = None
    had_injections: bool = False
    injection_cycles: int = 0
    images_stripped: bool = False
    # Budget e memoria del progresso per le goal-continuation sintetiche.
    # ``tools_at_last_goal_continue`` è il valore di ``len(tools_used)`` all'ultimo
    # nudge: se non è cresciuto, fra un nudge e l'altro non è stato eseguito nessun
    # tool e ripetere «continua» non può che riprodurre lo stesso testo.
    goal_continue_cycles: int = 0
    tools_at_last_goal_continue: int = 0
    goal_stalled: bool = False


class AgentRunner(RequestExecutionMixin, ToolExecutionMixin):
    """Run a tool-capable LLM loop without product-layer concerns."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    @classmethod
    def _append_injected_messages(
        cls,
        messages: list[dict[str, Any]],
        injections: list[dict[str, Any]],
    ) -> None:
        """Append injected user messages while preserving role alternation."""
        for injection in injections:
            if (
                messages
                and injection.get("role") == "user"
                and messages[-1].get("role") == "user"
            ):
                merged = dict(messages[-1])
                merged["content"] = merge_message_content(
                    merged.get("content"),
                    injection.get("content"),
                )
                messages[-1] = merged
                continue
            messages.append(injection)

    async def _try_drain_injections(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        assistant_message: dict[str, Any] | None,
        injection_cycles: int,
        *,
        phase: str = "after error",
        iteration: int | None = None,
        allow_goal_continue: bool = False,
        state: _RunCounters | None = None,
    ) -> tuple[bool, int]:
        """Drain pending injections. Returns (should_continue, updated_cycles).

        If injections are found and we haven't exceeded _MAX_INJECTION_CYCLES,
        append them to *messages* (and emit a checkpoint if *assistant_message*
        and *iteration* are both provided) and return (True, cycles+1) so the
        caller continues the iteration loop.  Otherwise return (False, cycles).

        ``state`` va passato solo dal call-site che abilita ``allow_goal_continue``:
        la continuation sintetica ha un budget suo (v. ``_goal_continue_allowed``),
        separato da ``injection_cycles``, che conta i messaggi reali dell'utente.
        """
        injections: list[dict[str, Any]] = []
        real_injection = False
        if injection_cycles < _MAX_INJECTION_CYCLES:
            injections = await self._drain_injections(spec)
            real_injection = bool(injections)
        if not injections and allow_goal_continue and assistant_message is not None:
            if self._goal_continue_allowed(spec, state, assistant_message):
                injections = [self._build_goal_continue_message(spec)]
                if state is not None:
                    state.goal_continue_cycles += 1
                    state.tools_at_last_goal_continue = len(state.tools_used)
                    # Il goal sta avanzando di nuovo: un rifiuto precedente in
                    # questo stesso run non deve più farlo parcheggiare.
                    state.goal_stalled = False
        if not injections:
            return False, injection_cycles
        if real_injection:
            injection_cycles += 1
        if assistant_message is not None:
            messages.append(assistant_message)
            if iteration is not None:
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "final_response",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [],
                    },
                )
        self._append_injected_messages(messages, injections)
        if real_injection:
            logger.info(
                "Injected {} follow-up message(s) {} ({}/{})",
                len(injections), phase, injection_cycles, _MAX_INJECTION_CYCLES,
            )
        else:
            cycles = state.goal_continue_cycles if state is not None else 0
            # Il contatore nella riga è ciò che rende un runaway leggibile in
            # logcat: nove righe identiche non dicevano che erano nove.
            log = logger.warning if cycles >= _GOAL_CONTINUE_WARN_AT else logger.info
            log(
                "Injected sustained-goal continuation {} ({}/{})",
                phase, cycles, _MAX_GOAL_CONTINUE_CYCLES,
            )
        return True, injection_cycles

    def _goal_continue_allowed(
        self,
        spec: AgentRunSpec,
        state: _RunCounters | None,
        assistant_message: dict[str, Any],
    ) -> bool:
        """Whether to synthesize a "keep working on your goal" nudge right now.

        Il nudge esiste per un caso preciso: il modello ha fatto un pezzo di lavoro
        e si è fermato a commentarlo mentre il goal è ancora aperto. Fuori da quel
        caso è dannoso — il 2026-08-12 un flusso guidato (``app-creator``, che fa
        UNA domanda per turno) ne ha ricevuti 9 in 45 secondi, uno per ogni domanda
        rivolta all'utente, finché il modello non si è liberato chiudendo il goal
        con un recap falso. Tre condizioni, tutte necessarie:

        1. **progresso**: almeno un tool andato a buon fine dall'ultimo nudge
           (``tools_used`` conta solo gli ``ok``). Spronare chi ha appena prodotto
           solo testo riproduce lo stesso testo, per definizione; spronare chi ha
           solo tool falliti rimanda a sbattere sullo stesso muro.
        2. **non una domanda all'utente** (euristica, v. ``looks_like_user_question``):
           chi aspetta una risposta non può avanzare da solo.
        3. **budget**: ``_MAX_GOAL_CONTINUE_CYCLES`` per run.

        Quando nega con un goal attivo alza ``state.goal_stalled``: il turno finisce
        normalmente e il product layer parcheggia il goal (resta ``active``) invece
        di lasciare che il modello lo chiuda per uscire.
        """
        predicate = spec.goal_active_predicate
        if predicate is None or not predicate():
            return False
        if state is None:
            # Nessuno stato = nessun budget da spendere: comportamento storico.
            return True

        reason: str | None = None
        if len(state.tools_used) <= state.tools_at_last_goal_continue:
            reason = "no tool progress since the last continuation"
        elif looks_like_user_question(str(assistant_message.get("content") or "")):
            reason = "final response asks the user something"
        elif state.goal_continue_cycles >= _MAX_GOAL_CONTINUE_CYCLES:
            reason = f"cap reached ({_MAX_GOAL_CONTINUE_CYCLES})"
        if reason is None:
            return True

        state.goal_stalled = True
        logger.info(
            "Sustained-goal continuation withheld for {}: {} (nudges this run: {})",
            spec.session_key or "default", reason, state.goal_continue_cycles,
        )
        return False

    def _build_goal_continue_message(self, spec: AgentRunSpec) -> dict[str, str]:
        custom = spec.goal_continue_message
        if callable(custom):
            try:
                custom = custom()
            except Exception:
                logger.exception("goal_continue_message callback failed")
                custom = None
        return build_goal_continue_message(custom)

    async def _drain_injections(self, spec: AgentRunSpec) -> list[dict[str, Any]]:
        """Drain pending user messages via the injection callback.

        Returns normalized user messages (capped by
        ``_MAX_INJECTIONS_PER_TURN``), or an empty list when there is
        nothing to inject. Messages beyond the cap are logged so they
        are not silently lost.
        """
        if spec.injection_callback is None:
            return []
        try:
            signature = inspect.signature(spec.injection_callback)
            accepts_limit = (
                "limit" in signature.parameters
                or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in signature.parameters.values()
                )
            )
            if accepts_limit:
                items = await spec.injection_callback(limit=_MAX_INJECTIONS_PER_TURN)
            else:
                items = await spec.injection_callback()
        except Exception:
            logger.exception("injection_callback failed")
            return []
        if not items:
            return []
        injected_messages: list[dict[str, Any]] = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, dict) and item.get("role") == "user" and "content" in item:
                if self._has_injection_content(item.get("content")):
                    injected_messages.append(item)
                continue
            if isinstance(item, dict):
                continue
            content = getattr(item, "content") if hasattr(item, "content") else str(item)
            if self._has_injection_content(content):
                injected_messages.append({"role": "user", "content": content})
        if len(injected_messages) > _MAX_INJECTIONS_PER_TURN:
            dropped = len(injected_messages) - _MAX_INJECTIONS_PER_TURN
            logger.warning(
                "Injection callback returned {} messages, capping to {} ({} dropped)",
                len(injected_messages), _MAX_INJECTIONS_PER_TURN, dropped,
            )
            injected_messages = injected_messages[:_MAX_INJECTIONS_PER_TURN]
        return injected_messages

    @staticmethod
    def _has_injection_content(content: Any) -> bool:
        if content is None:
            return False
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return bool(content)
        return True

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        """Esegue un turno dichiarando a quale conversazione appartiene.

        Lo scope sta **qui** e non sui singoli ``provider.chat*`` perché questo è
        il punto d'ingresso unico del turno: ci passano le quattro chiamate di
        ``request_execution`` e tutto ciò che il turno innesca. Ne segue che
        cron, Dream e heartbeat — che arrivano di qui con il loro
        ``session_key_override`` — ottengono da soli un ID stabile e distinto,
        che è esattamente ciò che OpenCode chiede. Un subagent costruisce un
        ``AgentRunner`` proprio e apre quindi il suo, senza ereditare questo.
        """
        with conversation_scope(spec.session_key):
            return await self._run_with_hooks(spec)

    async def _run_with_hooks(self, spec: AgentRunSpec) -> AgentRunResult:
        hook = spec.hook or AgentHook()
        messages = list(spec.initial_messages)
        context = AgentRunHookContext(messages=deepcopy(messages))

        try:
            await hook.before_run(context)
            result = await self._run_core(spec, hook, messages)
        except asyncio.CancelledError as exc:
            context.messages = deepcopy(messages)
            context.stop_reason = "cancelled"
            context.error = None
            context.exception = exc
            raise
        except Exception as exc:
            context.messages = deepcopy(messages)
            context.stop_reason = "error"
            context.error = f"Error: {type(exc).__name__}: {exc}"
            context.exception = exc
            await hook.on_error(context)
            raise
        else:
            context.messages = deepcopy(result.messages)
            context.final_content = result.final_content
            context.tools_used = list(result.tools_used)
            context.usage = dict(result.usage)
            context.stop_reason = result.stop_reason
            context.error = result.error
            context.tool_events = deepcopy(result.tool_events)
            context.had_injections = result.had_injections
            context.exception = None
            if context.error is not None:
                await hook.on_error(context)
            await hook.after_run(context)
            return result
        finally:
            context.messages = deepcopy(messages)
            if context.exception is None:
                await hook.on_finally(context)
            else:
                try:
                    await hook.on_finally(context)
                except Exception:
                    logger.exception(
                        "AgentHook.on_finally error after {}",
                        context.stop_reason or "run exception",
                    )

    async def _run_core(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
    ) -> AgentRunResult:
        # ``state`` raggruppa i contatori/accumulatori condivisi tra i rami; gli
        # helper lo mutano per riferimento (stessa istanza), così i reset e le
        # accumulazioni restano visibili all'iterazione successiva.
        state = _RunCounters(tool_errors=ToolErrorBudget.from_spec(spec))

        for iteration in range(spec.max_iterations):
            messages_for_model = self._govern_context(spec, messages, iteration)
            context = AgentHookContext(
                iteration=iteration,
                messages=messages,
                session_key=spec.session_key,
            )
            await hook.before_iteration(context)
            # Il budget effettivo va catturato *prima* della chiamata: è quello
            # che il classificatore confronta con l'usage riportato per capire se
            # la risposta è finita contro il tetto.
            request_overrides = state.request_overrides
            output_budget = self._effective_max_tokens(spec, request_overrides)
            response = await self._request_model(
                spec, messages_for_model, hook, context, request_overrides
            )
            context.response = response
            context.tool_calls = list(response.tool_calls)
            if response.images_stripped:
                state.images_stripped = True

            reasoning_text, cleaned_content = extract_reasoning(
                response.reasoning_content,
                response.thinking_blocks,
                response.content,
            )
            response.content = cleaned_content
            raw_usage = self._usage_or_estimate(spec, messages_for_model, response)
            context.usage = dict(raw_usage)
            self._accumulate_usage(state.usage, raw_usage)
            if reasoning_text and not context.streamed_reasoning:
                await hook.emit_reasoning(reasoning_text)
                await hook.emit_reasoning_end()
                context.streamed_reasoning = True

            # --- Tool phase (muta ``messages`` in-place, checkpoint a due fasi) ---
            if response.should_execute_tools:
                if await self._run_tool_phase(
                    spec, hook, messages, context, response, state, iteration
                ) == "continue":
                    continue
                break

            if response.has_tool_calls:
                logger.warning(
                    "Ignoring tool calls under finish_reason='{}' for {}",
                    response.finish_reason,
                    spec.session_key or "default",
                )

            clean = hook.finalize_content(context, response.content)

            # Esito nominale: il dispatch sotto è uno switch su questo, non una
            # catena di guardie che si escludono per accidente (vedi
            # ``agent/response_outcome.py``).
            outcome = classify_response(response, clean, max_tokens=output_budget)

            # --- Troncamento senza testo utile: ritenta con parametri diversi ---
            if outcome is ResponseOutcome.TRUNCATED_BLANK:
                if await self._recover_blank_truncation(
                    spec, hook, context, response, state, raw_usage, output_budget, iteration
                ) == "continue":
                    continue

            # --- Empty-content retry + finalization retry ---
            if outcome is ResponseOutcome.EMPTY:
                verdict, response, clean = await self._recover_empty_content(
                    spec, hook, context, response, clean, raw_usage, state,
                    messages_for_model, iteration,
                )
                if verdict == "continue":
                    continue

            # --- Length recovery (output troncato con testo parziale) ---
            if outcome is ResponseOutcome.TRUNCATED_WITH_TEXT:
                if await self._recover_length(
                    spec, hook, context, response, clean, messages, state, iteration
                ) == "continue":
                    continue

            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

            # Check for mid-turn injections BEFORE signaling stream end.
            # If injections are found we keep the stream alive (resuming=True)
            # so streaming channels don't prematurely finalize the card.
            should_continue, state.injection_cycles = await self._try_drain_injections(
                spec, messages, assistant_message, state.injection_cycles,
                phase="after final response",
                iteration=iteration,
                allow_goal_continue=True,
                state=state,
            )
            if should_continue:
                state.had_injections = True

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                await hook.after_iteration(context)
                continue

            # --- Context length error recovery ---
            if await self._recover_context_overflow(
                spec, hook, context, response, messages, state, iteration
            ) == "continue":
                continue

            # --- Terminal: LLM error ---
            if response.finish_reason == "error":
                if await self._finish_on_error(
                    spec, hook, context, response, clean, messages, state
                ) == "continue":
                    continue
                break
            # --- Terminal: blank final response ---
            if is_blank_text(clean):
                if await self._finish_on_blank(
                    spec, hook, context, messages, state,
                    truncated=outcome is ResponseOutcome.TRUNCATED_BLANK,
                ) == "continue":
                    continue
                break

            # --- Happy path: final response ---
            messages.append(assistant_message or build_assistant_message(
                clean,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            ))
            await self._emit_checkpoint(
                spec,
                {
                    "phase": "final_response",
                    "iteration": iteration,
                    "model": spec.model,
                    "assistant_message": messages[-1],
                    "completed_tool_results": [],
                    "pending_tool_calls": [],
                },
            )
            state.final_content = clean
            context.final_content = state.final_content
            context.stop_reason = state.stop_reason
            await hook.after_iteration(context)
            break
        else:
            await self._finalize_max_iterations(spec, hook, messages, state)

        return AgentRunResult(
            final_content=state.final_content,
            messages=messages,
            tools_used=state.tools_used,
            usage=state.usage,
            stop_reason=state.stop_reason,
            error=state.error,
            tool_events=state.tool_events,
            had_injections=state.had_injections,
            images_stripped=state.images_stripped,
            # Solo uno stallo vero: se il turno è finito col budget di iterazioni
            # esaurito, la continuazione interna (``session.turn_continuation``) ha
            # ancora la parola e il goal non sta aspettando nessuno.
            goal_stalled=state.goal_stalled and state.stop_reason != "max_iterations",
        )

    # --- Branch handlers estratti da ``_run_core`` (behavior-identical) ---
    # Ognuno riceve gli STESSI oggetti mutabili del driver (``messages``,
    # ``context``, ``state``, ``spec``), non copie, e restituisce un verdetto di
    # controllo flusso esplicito. NON riordinare le sequenze
    # ``on_stream_end``/``after_iteration``/drain: differiscono per ramo di
    # proposito (vedi il drain "after final response" nel driver, che precede
    # ``on_stream_end`` per tenere viva la card in streaming).

    def _govern_context(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        iteration: int,
    ) -> list[dict[str, Any]]:
        try:
            # Keep the persisted conversation untouched. Context governance
            # may repair or compact historical messages for the model, but
            # those synthetic edits must not shift the append boundary used
            # later when the caller saves only the new turn.
            messages_for_model = self._drop_orphan_tool_results(messages)
            messages_for_model = self._backfill_missing_tool_results(messages_for_model)
            messages_for_model = self._microcompact(messages_for_model)
            messages_for_model = self._apply_tool_result_budget(spec, messages_for_model)
            messages_for_model = self._snip_history(spec, messages_for_model)
            # Snipping may have created new orphans; clean them up.
            messages_for_model = self._drop_orphan_tool_results(messages_for_model)
            messages_for_model = self._backfill_missing_tool_results(messages_for_model)
        except Exception:
            logger.exception(
                "Context governance failed on turn {} for {}; applying minimal repair",
                iteration,
                spec.session_key or "default",
            )
            try:
                messages_for_model = self._drop_orphan_tool_results(messages)
                messages_for_model = self._backfill_missing_tool_results(messages_for_model)
            except Exception:
                messages_for_model = messages
        return messages_for_model

    async def _run_tool_phase(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
        context: AgentHookContext,
        response: LLMResponse,
        state: _RunCounters,
        iteration: int,
    ) -> Literal["continue", "break"]:
        if hook.wants_streaming():
            await hook.on_stream_end(context, resuming=True)

        assistant_message = build_assistant_message(
            response.content or "",
            tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
            reasoning_content=response.reasoning_content,
            thinking_blocks=response.thinking_blocks,
        )
        messages.append(assistant_message)
        await self._emit_checkpoint(
            spec,
            {
                "phase": "awaiting_tools",
                "iteration": iteration,
                "model": spec.model,
                "assistant_message": assistant_message,
                "completed_tool_results": [],
                "pending_tool_calls": [tc.to_openai_tool_call() for tc in response.tool_calls],
            },
        )

        await hook.before_execute_tools(context)

        results, new_events, fatal_error = await self._execute_tools(
            spec,
            response.tool_calls,
            state.external_lookup_counts,
            state.workspace_violation_counts,
            tool_errors=state.tool_errors,
        )
        state.tool_events.extend(new_events)
        state.tools_used.extend(
            tool_call.name
            for tool_call, event in zip(response.tool_calls, new_events)
            if event.get("status") == "ok"
        )
        context.tool_results = list(results)
        context.tool_events = list(new_events)
        completed_tool_results: list[dict[str, Any]] = []
        for tool_call, result in zip(response.tool_calls, results):
            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "content": self._normalize_tool_result(
                    spec,
                    tool_call.id,
                    tool_call.name,
                    result,
                ),
            }
            messages.append(tool_message)
            completed_tool_results.append(tool_message)
        if fatal_error is not None:
            state.error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
            state.final_content = state.error
            state.stop_reason = "tool_error"
            self._append_final_message(messages, state.final_content)
            context.final_content = state.final_content
            context.error = state.error
            context.stop_reason = state.stop_reason
            await hook.after_iteration(context)
            should_continue, state.injection_cycles = await self._try_drain_injections(
                spec, messages, None, state.injection_cycles,
                phase="after tool error",
            )
            if should_continue:
                state.had_injections = True
                return "continue"
            return "break"
        await self._emit_checkpoint(
            spec,
            {
                "phase": "tools_completed",
                "iteration": iteration,
                "model": spec.model,
                "assistant_message": assistant_message,
                "completed_tool_results": completed_tool_results,
                "pending_tool_calls": [],
            },
        )
        state.empty_content_retries = 0
        state.length_recovery_count = 0
        # Il contatore si azzera (l'iterazione può ri-escalare se serve) ma
        # ``request_overrides`` no: il budget più alto è una proprietà di quanto
        # è verboso *questo turno*, non del singolo fallimento risolto. Ributtarlo
        # al valore di partenza garantirebbe di ri-sbattere sullo stesso muro,
        # sprecando un'altra chiamata per riscoprire ciò che già sappiamo.
        state.blank_truncation_retries = 0
        # Checkpoint 1: drain injections after tools, before next LLM call
        _drained, state.injection_cycles = await self._try_drain_injections(
            spec, messages, None, state.injection_cycles,
            phase="after tool execution",
        )
        if _drained:
            state.had_injections = True
        await hook.after_iteration(context)
        return "continue"

    async def _recover_empty_content(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        context: AgentHookContext,
        response: LLMResponse,
        clean: str | None,
        raw_usage: dict[str, int],
        state: _RunCounters,
        messages_for_model: list[dict[str, Any]],
        iteration: int,
    ) -> tuple[Literal["continue", "proceed"], LLMResponse, str | None]:
        if response.finish_reason != "error" and is_blank_text(clean):
            state.empty_content_retries += 1
            if state.empty_content_retries < _MAX_EMPTY_RETRIES:
                logger.warning(
                    "Empty response on turn {} for {} ({}/{}); retrying",
                    iteration,
                    spec.session_key or "default",
                    state.empty_content_retries,
                    _MAX_EMPTY_RETRIES,
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=False)
                await hook.after_iteration(context)
                return "continue", response, clean
            logger.warning(
                "Empty response on turn {} for {} after {} retries; attempting finalization",
                iteration,
                spec.session_key or "default",
                state.empty_content_retries,
            )
            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=False)
            response, retry_messages = await self._request_finalization_retry(
                spec, messages_for_model, state.request_overrides
            )
            retry_usage = self._usage_or_estimate(spec, retry_messages, response)
            self._accumulate_usage(state.usage, retry_usage)
            raw_usage = self._merge_usage(raw_usage, retry_usage)
            context.response = response
            context.usage = dict(raw_usage)
            context.tool_calls = list(response.tool_calls)
            clean = hook.finalize_content(context, response.content)
        return "proceed", response, clean

    async def _recover_length(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        context: AgentHookContext,
        response: LLMResponse,
        clean: str | None,
        messages: list[dict[str, Any]],
        state: _RunCounters,
        iteration: int,
    ) -> Literal["continue", "proceed"]:
        # Il troncamento lo stabilisce il chiamante via ``classify_response``, che
        # riconosce anche i provider che non riportano ``finish_reason == "length"``.
        # Qui resta solo l'invariante di questo ramo: c'è testo da continuare.
        if not is_blank_text(clean):
            state.length_recovery_count += 1
            if state.length_recovery_count <= _MAX_LENGTH_RECOVERIES:
                logger.info(
                    "Output truncated on turn {} for {} ({}/{}, finish_reason='{}', "
                    "completion={}); continuing",
                    iteration,
                    spec.session_key or "default",
                    state.length_recovery_count,
                    _MAX_LENGTH_RECOVERIES,
                    response.finish_reason,
                    reported_completion_tokens(response),
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)
                messages.append(build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                ))
                messages.append(build_length_recovery_message())
                await hook.after_iteration(context)
                return "continue"
        return "proceed"

    def _effective_max_tokens(
        self, spec: AgentRunSpec, overrides: RequestOverrides | None
    ) -> int | None:
        """Tetto di output effettivo per la prossima richiesta.

        Quando ``spec.max_tokens`` è ``None`` il tetto reale è quello del
        provider: senza questo fallback il rilevamento del troncamento sarebbe
        cieco proprio nelle configurazioni che non impostano ``maxTokens``.
        """
        if overrides is not None and overrides.max_tokens is not None:
            return overrides.max_tokens
        if spec.max_tokens is not None:
            return spec.max_tokens
        generation = getattr(self.provider, "generation", None)
        fallback = getattr(generation, "max_tokens", None)
        return fallback if isinstance(fallback, int) and fallback > 0 else None

    def _escalated_output_budget(
        self,
        spec: AgentRunSpec,
        current: int | None,
        raw_usage: dict[str, int],
    ) -> int | None:
        """Budget di output raddoppiato, o ``None`` se non c'è spazio per crescere.

        Il vincolo è la finestra di contesto residua: alzare il tetto oltre lo
        spazio disponibile scambierebbe un troncamento con un errore di context
        length.
        """
        if not current or current <= 0:
            return None
        target = current * 2
        window = spec.context_window_tokens
        if window is None:
            # Finestra non dichiarata (costruzione diretta dello spec): senza
            # finestra non si può affermare che lo spazio manchi, quindi si alza
            # comunque. Un eventuale overflow resta coperto da
            # ``_recover_context_overflow``.
            return target
        prompt_tokens = max(0, raw_usage.get("prompt_tokens", 0))
        room = window - prompt_tokens - _OUTPUT_BUDGET_HEADROOM
        if room <= current:
            return None
        return min(target, room)

    async def _recover_blank_truncation(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        context: AgentHookContext,
        response: LLMResponse,
        state: _RunCounters,
        raw_usage: dict[str, int],
        output_budget: int | None,
        iteration: int,
    ) -> Literal["continue", "proceed"]:
        """Ritenta un troncamento a contenuto vuoto cambiando i *parametri*.

        Non tocca la conversazione, di proposito: qui il modello non ha prodotto
        niente di visibile, quindi non c'è nessun output parziale da continuare —
        e siccome diverse API scartano il ``reasoning_content`` dei turni
        precedenti, chiedere di "continuare da dove eri" sarebbe un'istruzione
        che il modello non può soddisfare. Cambia invece ciò che ha causato il
        muro: prima il budget, poi anche l'effort del reasoning.
        """
        if state.blank_truncation_retries >= _MAX_BLANK_TRUNCATION_RETRIES:
            logger.warning(
                "Output truncated with no usable content on turn {} for {} after {} "
                "retries (finish_reason='{}', completion={}, budget={}); giving up",
                iteration,
                spec.session_key or "default",
                state.blank_truncation_retries,
                response.finish_reason,
                reported_completion_tokens(response),
                output_budget,
            )
            return "proceed"

        state.blank_truncation_retries += 1
        previous = state.request_overrides
        new_budget = self._escalated_output_budget(spec, output_budget, raw_usage)
        effort = previous.reasoning_effort if previous is not None else None
        # L'effort si abbassa dal secondo tentativo: se il gateway rifiuta il
        # parametro, un tentativo col solo budget alzato è già stato speso. Ma se
        # il budget non può crescere (finestra piena) quello stadio non esiste, e
        # l'effort è l'unica leva: usarla subito invece di arrendersi.
        staged = state.blank_truncation_retries >= 2 or new_budget is None
        if staged and effort is None:
            current_effort = (spec.reasoning_effort or "").lower()
            if current_effort not in _TRUNCATION_LOW_EFFORTS:
                effort = _TRUNCATION_FALLBACK_EFFORT
        if new_budget is None and effort is None:
            logger.warning(
                "Output truncated with no usable content on turn {} for {} "
                "(finish_reason='{}', completion={}, budget={}); no headroom to "
                "raise the output budget, giving up",
                iteration,
                spec.session_key or "default",
                response.finish_reason,
                reported_completion_tokens(response),
                output_budget,
            )
            return "proceed"

        state.request_overrides = RequestOverrides(
            max_tokens=new_budget if new_budget is not None else (
                previous.max_tokens if previous is not None else None
            ),
            reasoning_effort=effort,
        )
        logger.warning(
            "Output truncated with no usable content on turn {} for {} ({}/{}, "
            "finish_reason='{}', completion={}); retrying with max_tokens={} "
            "reasoning_effort={}",
            iteration,
            spec.session_key or "default",
            state.blank_truncation_retries,
            _MAX_BLANK_TRUNCATION_RETRIES,
            response.finish_reason,
            reported_completion_tokens(response),
            state.request_overrides.max_tokens,
            state.request_overrides.reasoning_effort,
        )
        if hook.wants_streaming():
            await hook.on_stream_end(context, resuming=True)
        await hook.after_iteration(context)
        return "continue"

    async def _recover_context_overflow(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        context: AgentHookContext,
        response: LLMResponse,
        messages: list[dict[str, Any]],
        state: _RunCounters,
        iteration: int,
    ) -> Literal["continue", "proceed"]:
        if (
            response.finish_reason == "error"
            and self._is_context_length_error(response)
            and state.context_length_retries < _MAX_CONTEXT_LENGTH_RETRIES
        ):
            state.context_length_retries += 1

            # Try to extract the model's real limit from the error message
            detected_limit = self._extract_context_limit(response)
            current_window = spec.context_window_tokens
            if detected_limit and (current_window is None or detected_limit < current_window):
                new_window = detected_limit
            elif current_window is not None:
                # Shrink by 50% as a heuristic
                new_window = max(2048, int(current_window * 0.5))
            else:
                # Finestra non dichiarata e nessun limite nell'errore: non c'è
                # niente da dimezzare. Prima questo ramo faceva aritmetica su
                # None e sollevava TypeError *dentro* il recovery, trasformando
                # un overflow recuperabile in un errore di tipo. Inventare una
                # finestra sarebbe peggio: si arrende e lascia emergere l'errore
                # vero del provider, che almeno dice cos'è successo.
                logger.warning(
                    "Context length overflow on turn {} for {} with no declared "
                    "context window and no limit in the error; cannot shrink",
                    iteration,
                    spec.session_key or "default",
                )
                return "proceed"

            logger.warning(
                "Context length overflow on turn {} for {} ({}/{}): "
                "reducing window {} -> {} and retrying",
                iteration,
                spec.session_key or "default",
                state.context_length_retries,
                _MAX_CONTEXT_LENGTH_RETRIES,
                spec.context_window_tokens,
                new_window,
            )

            # Notify the caller (e.g. loop.py) so it can update the consolidator
            if spec.on_context_overflow is not None:
                try:
                    result = spec.on_context_overflow(new_window)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.debug("on_context_overflow callback failed", exc_info=True)

            # Mutazione in-place dello stesso ``spec`` (invariante preservata):
            # ``_govern_context`` al giro successivo del loop rifà lo snip con
            # questa finestra ridotta, quindi non serve ricalcolarlo qui.
            spec.context_window_tokens = new_window

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=True)
            await hook.after_iteration(context)
            return "continue"
        return "proceed"

    async def _finish_on_error(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        context: AgentHookContext,
        response: LLMResponse,
        clean: str | None,
        messages: list[dict[str, Any]],
        state: _RunCounters,
    ) -> Literal["continue", "break"]:
        if LLMProvider.is_arrearage_response(response):
            state.final_content = _ARREARAGE_ERROR_MESSAGE
        else:
            state.final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
        state.stop_reason = "error"
        state.error = state.final_content
        if response.partial_content:
            self._append_interrupted_partial_content(messages, response.partial_content)
        else:
            self._append_model_error_placeholder(messages)
        context.final_content = state.final_content
        context.error = state.error
        context.stop_reason = state.stop_reason
        await hook.after_iteration(context)
        should_continue, state.injection_cycles = await self._try_drain_injections(
            spec, messages, None, state.injection_cycles,
            phase="after LLM error",
        )
        if should_continue:
            state.had_injections = True
            return "continue"
        return "break"

    async def _finish_on_blank(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        context: AgentHookContext,
        messages: list[dict[str, Any]],
        state: _RunCounters,
        *,
        truncated: bool = False,
    ) -> Literal["continue", "break"]:
        # Due esiti distinti sotto lo stesso sintomo (contenuto vuoto): dirlo
        # all'utente, invece di collassarli sul messaggio generico, è ciò che
        # rende il limite di token diagnosticabile senza leggere i log.
        state.final_content = (
            OUTPUT_TRUNCATED_MESSAGE if truncated else EMPTY_FINAL_RESPONSE_MESSAGE
        )
        state.stop_reason = "output_truncated" if truncated else "empty_final_response"
        state.error = state.final_content
        self._append_final_message(messages, state.final_content)
        context.final_content = state.final_content
        context.error = state.error
        context.stop_reason = state.stop_reason
        await hook.after_iteration(context)
        should_continue, state.injection_cycles = await self._try_drain_injections(
            spec, messages, None, state.injection_cycles,
            phase="after empty response",
        )
        if should_continue:
            state.had_injections = True
            return "continue"
        return "break"

    async def _finalize_max_iterations(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
        state: _RunCounters,
    ) -> None:
        state.stop_reason = "max_iterations"
        # Drain any remaining injections so they are appended to the
        # conversation history instead of being re-published as
        # independent inbound messages by _dispatch's finally block.
        # We include them before the no-tools finalization pass so the
        # final response can account for every known follow-up.
        drained_after_max_iterations, state.injection_cycles = await self._try_drain_injections(
            spec, messages, None, state.injection_cycles,
            phase="after max_iterations",
        )
        if drained_after_max_iterations:
            state.had_injections = True
        state.final_content = None
        if spec.finalize_on_max_iterations:
            state.final_content = await self._try_finalize_after_max_iterations(
                spec,
                hook,
                messages,
                state.usage,
            )
        if state.final_content is None:
            state.final_content = self._max_iterations_fallback(spec)
        self._append_final_message(messages, state.final_content)

    # Contabilità usage estratta in ``agent/usage_accounting.py``. Delegatori
    # sottili: le funzioni di stima ricevono ``self.provider`` esplicitamente.
    def _usage_or_estimate(
        self, spec: AgentRunSpec, messages: list[dict[str, Any]], response: LLMResponse
    ) -> dict[str, int]:
        return usage_or_estimate(self.provider, spec, messages, response)

    @staticmethod
    def _accumulate_usage(target: dict[str, int], addition: dict[str, int]) -> None:
        accumulate_usage(target, addition)

    @staticmethod
    def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        return merge_usage(left, right)

    # Classificazione errori di boundary estratta in ``agent/tool_error_policy.py``.

    def _classify_violation(
        self,
        *,
        raw_text: str,
        soft_payload: str,
        event: dict[str, str],
        tool_call: ToolCallRequest,
        workspace_violation_counts: dict[str, int],
    ) -> tuple[Any, dict[str, str], BaseException | None] | None:
        return classify_violation(
            raw_text=raw_text,
            soft_payload=soft_payload,
            event=event,
            tool_call=tool_call,
            workspace_violation_counts=workspace_violation_counts,
        )

    async def _emit_checkpoint(
        self,
        spec: AgentRunSpec,
        payload: dict[str, Any],
    ) -> None:
        callback = spec.checkpoint_callback
        if callback is not None:
            await callback(payload)

    @staticmethod
    def _append_final_message(messages: list[dict[str, Any]], content: str | None) -> None:
        if not content:
            return
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            if messages[-1].get("content") == content:
                return
            messages[-1] = build_assistant_message(content)
            return
        messages.append(build_assistant_message(content))

    @staticmethod
    def _append_model_error_placeholder(messages: list[dict[str, Any]]) -> None:
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            return
        messages.append(build_assistant_message(_PERSISTED_MODEL_ERROR_PLACEHOLDER))

    @staticmethod
    def _append_interrupted_partial_content(messages: list[dict[str, Any]], partial_content: str) -> None:
        """Persist text streamed to the user before a mid-stream exception aborted
        the response, so model history matches what was actually shown on screen
        instead of the generic placeholder (#audit mid-stream-exception loss)."""
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            return
        messages.append(build_assistant_message(
            f"{partial_content}\n\n{_PARTIAL_CONTENT_INTERRUPTED_MARKER}"
        ))

    def _normalize_tool_result(
        self,
        spec: AgentRunSpec,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> Any:
        result = ensure_nonempty_tool_result(tool_name, result)
        if tool_name in _TOOL_RESULT_OFFLOAD_EXEMPT_TOOLS:
            # Exempt tools bound their own output; skip generic offload and truncation.
            return result
        try:
            content = maybe_persist_tool_result(
                spec.workspace,
                spec.session_key,
                tool_call_id,
                result,
                max_chars=spec.max_tool_result_chars,
            )
        except Exception:
            logger.exception(
                "Tool result persist failed for {} in {}; using raw result",
                tool_call_id,
                spec.session_key or "default",
            )
            content = result
        if isinstance(content, str) and len(content) > spec.max_tool_result_chars:
            return truncate_text(content, spec.max_tool_result_chars)
        return content

    # Riparazione della history estratta in ``agent/history_repair.py`` (modulo
    # leaf, casa unica condivisa con session/memory). Questi restano come
    # delegatori sottili per non toccare i call-site interni né i test che
    # invocano ``AgentRunner._drop_orphan_tool_results(...)`` ecc.
    @staticmethod
    def _drop_orphan_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return drop_orphan_tool_results(messages)

    @staticmethod
    def _backfill_missing_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return backfill_missing_tool_results(messages)

    @staticmethod
    def _microcompact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return microcompact(messages)

    def _apply_tool_result_budget(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        updated = messages
        for idx, message in enumerate(messages):
            if message.get("role") != "tool":
                continue
            normalized = self._normalize_tool_result(
                spec,
                str(message.get("tool_call_id") or f"tool_{idx}"),
                str(message.get("name") or "tool"),
                message.get("content"),
            )
            if normalized != message.get("content"):
                if updated is messages:
                    updated = [dict(m) for m in messages]
                updated[idx]["content"] = normalized
        return updated

    # --- Context length error recovery helpers ---

    # Pattern to extract the model's actual context limit from OpenAI-style error messages.
    # e.g. "This model's maximum context length is 8192 tokens."
    # Rilevamento context-limit estratto in ``agent/context_governor.py``; qui
    # restano i delegatori, che i test di ``AgentRunner._is_context_*`` usano.

    @staticmethod
    def _is_context_length_error(response: LLMResponse) -> bool:
        return is_context_length_error(response)

    @staticmethod
    def _extract_context_limit(response: LLMResponse) -> int | None:
        return extract_context_limit(response)

    def _snip_history(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Logica estratta in ``agent/context_governor.snip_history`` (Fase 2.3);
        # delegatore sottile (zero churn ai call-site/test).
        return snip_history(self.provider, spec, messages)

    def _partition_tool_batches(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
    ) -> list[list[ToolCallRequest]]:
        if not spec.concurrent_tools:
            return [[tool_call] for tool_call in tool_calls]

        batches: list[list[ToolCallRequest]] = []
        current: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            get_tool = getattr(spec.tools, "get", None)
            tool = get_tool(tool_call.name) if callable(get_tool) else None
            # getattr con default, coerente col lookup difensivo della riga sopra:
            # ``spec.tools.get`` non ha un tipo noto, quindi il tool che restituisce
            # non è tipabile — e un tool senza l'attributo non deve far esplodere
            # il batching, deve solo non essere batchabile.
            can_batch = bool(tool is not None and getattr(tool, "concurrency_safe", False))
            if can_batch:
                current.append(tool_call)
                continue
            if current:
                batches.append(current)
                current = []
            batches.append([tool_call])
        if current:
            batches.append(current)
        return batches
