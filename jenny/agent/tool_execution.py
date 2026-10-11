"""Esecuzione dei tool per l'``AgentRunner`` (estratto da runner.py).

`ToolExecutionMixin` isola il fan-out dei tool-call di un turno
(`_execute_tools`) e l'esecuzione del singolo tool (`_run_tool`): tracking degli
eventi file-edit, progress, classificazione violazioni (via metodi del runner
risolti per MRO). Nessun import runtime verso ``runner`` → nessun ciclo.

Qui vive anche :class:`ToolErrorBudget`, la tolleranza di un run agli errori
tool recuperabili: sta in questo modulo e non in ``runner`` perche e proprio qui
che viene speso, e perche ``runner`` importa da qui (l'inverso sarebbe un ciclo).
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.hook import ToolResultHookContext
from jenny.agent.progress_events import (
    invoke_file_edit_progress,
    on_progress_accepts_file_edit_events,
)
from jenny.agent.tool_error_policy import TOOL_ERROR_RETRY_HINT
from jenny.agent.tools.result import ToolResult
from jenny.config.runtime_env import tool_timeout_s
from jenny.providers.base import ToolCallRequest
from jenny.security.subagent_policy import policy_after_tool, policy_before_tool
from jenny.utils.file_edit_events import (
    build_file_edit_end_event,
    build_file_edit_error_event,
    build_file_edit_start_event,
    prepare_file_edit_trackers,
)
from jenny.utils.runtime import repeated_external_lookup_error

if TYPE_CHECKING:
    from jenny.agent.runner import AgentRunSpec

# Rapporto fra il tetto consecutivo e quello totale di un budget. Con budget 3:
# 3 errori di fila sono tollerati, 6 in tutto. Il totale e un multiplo del
# consecutivo di proposito — cosi resta UN solo numero da configurare — e vale
# "puoi sbagliare la stessa classe di cosa due volte nell'arco del lavoro".
_TOTAL_BUDGET_MULTIPLIER = 2

# Violazioni di boundary di sicurezza tollerate, indipendenti dal budget
# ordinario. Una, non ``limit``: il payload di un blocco SSRF dice
# esplicitamente di smettere e di non ritentare per altre vie (vedi
# ``SSRF_BOUNDARY_NOTE``), e una violazione di workspace ripetuta viene gia
# escalata. La prima serve a far cambiare strada; la seconda dimostra che non e
# servita, e non c'e nessun "recupero" da comprare con altri tentativi.
_SAFETY_BOUNDARY_ALLOWANCE = 1


@dataclass(slots=True)
class ToolErrorBudget:
    """Tolleranza di un singolo run agli errori tool recuperabili.

    Un solo knob (``limit``) governa tre contatori, perche "errore tool" copre
    tre fallimenti che non si assomigliano:

    * ``consecutive`` — errori ordinari di fila, azzerato da ogni tool call
      riuscita. E il rilevatore di *flailing*: un agente che non riesce a
      piazzare una sola chiamata buona in ``limit`` tentativi non sta
      recuperando, sta girando a vuoto.
    * ``total`` — errori ordinari del run, mai azzerato, tetto
      ``limit * _TOTAL_BUDGET_MULTIPLIER``. Esiste perche il successo non
      dimostra il recupero: un agente che alterna una chiamata buona e una
      sbagliata progredisce sulla carta e brucia tutto il budget di iterazioni
      sullo stesso errore, e un contatore che si azzera a ogni successo non
      potrebbe vederlo *mai*.
    * ``boundary`` — violazioni di boundary di sicurezza, contate a parte con
      allowance ``_SAFETY_BOUNDARY_ALLOWANCE``: non sono errori da cui imparare
      riprovando, quindi non si pagano col budget degli errori onesti.

    ``limit=None`` e la modalita storica: con ``fail_on_tool_error`` il primo
    errore ordinario chiude il run e le violazioni di boundary non lo chiudono
    mai. I chiamanti che non passano ``tool_error_budget`` restano su questa.
    """

    limit: int | None = None
    consecutive: int = 0
    total: int = 0
    boundary: int = 0

    @classmethod
    def from_spec(cls, spec: AgentRunSpec) -> ToolErrorBudget:
        budget = spec.tool_error_budget
        return cls(limit=max(0, int(budget)) if budget is not None else None)

    @property
    def total_limit(self) -> int:
        return 0 if self.limit is None else self.limit * _TOTAL_BUDGET_MULTIPLIER

    @property
    def boundary_limit(self) -> int:
        return 0 if self.limit is None else min(_SAFETY_BOUNDARY_ALLOWANCE, self.limit)

    def credit(self) -> None:
        """Registra una tool call riuscita.

        Azzera la SOLA serie consecutiva: ``total`` non si rifonde, altrimenti il
        contatore che esiste per vedere l'alternanza buono/sbagliato sarebbe
        proprio quello che l'alternanza cancella.
        """
        self.consecutive = 0

    def spend_ordinary(self) -> str | None:
        """Scala un errore tool ordinario; ritorna il motivo dell'abort, o ``None``."""
        self.consecutive += 1
        self.total += 1
        if self.limit is None:
            return "fail_on_tool_error is set and no tool error budget was configured"
        if self.consecutive > self.limit:
            return (
                f"{self.consecutive} recoverable tool errors in a row "
                f"(budget {self.limit})"
            )
        if self.total > self.total_limit:
            return (
                f"{self.total} recoverable tool errors in this run "
                f"(budget {self.total_limit})"
            )
        return None

    def spend_boundary(self) -> str | None:
        """Scala una violazione di boundary; ritorna il motivo dell'abort, o ``None``."""
        self.boundary += 1
        if self.limit is None:
            # Modalita storica: una violazione di boundary non ha mai chiuso un
            # run e non inizia a farlo qui (vedi ``classify_violation``, che
            # restituisce sempre un errore soft).
            return None
        if self.boundary > self.boundary_limit:
            return (
                f"{self.boundary} safety-boundary violations in this run "
                f"(allowance {self.boundary_limit})"
            )
        return None


class ToolExecutionMixin:
    """Esecuzione tool-call del turno (mixin di AgentRunner)."""

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
        *,
        tool_errors: ToolErrorBudget | None = None,
    ) -> tuple[list[Any], list[dict[str, str]], BaseException | None]:
        # Il budget arriva dal driver del run (``_RunCounters``) perche e
        # contabilita di RUN, non di turno: senza continuita fra i turni un
        # agente che sbaglia una volta per turno non esaurirebbe mai nulla.
        # Il fallback per-chiamata copre i call-site che invocano il fan-out
        # direttamente.
        budget = tool_errors if tool_errors is not None else ToolErrorBudget.from_spec(spec)
        batches = self._partition_tool_batches(spec, tool_calls)
        tool_results: list[tuple[Any, dict[str, str], BaseException | None]] = []
        for batch in batches:
            if spec.concurrent_tools and len(batch) > 1:
                batch_results = await asyncio.gather(*(
                    self._run_tool(
                        spec, tool_call, external_lookup_counts, workspace_violation_counts,
                        tool_errors=budget,
                    )
                    for tool_call in batch
                ))
                tool_results.extend(batch_results)
            else:
                batch_results = []
                for tool_call in batch:
                    result = await self._run_tool(
                        spec, tool_call, external_lookup_counts, workspace_violation_counts,
                        tool_errors=budget,
                    )
                    tool_results.append(result)
                    batch_results.append(result)

        results: list[Any] = []
        events: list[dict[str, str]] = []
        fatal_error: BaseException | None = None
        for result, event, error in tool_results:
            results.append(result)
            events.append(event)
            if error is not None and fatal_error is None:
                fatal_error = error
        return results, events, fatal_error

    def _tool_error_outcome(
        self,
        spec: AgentRunSpec,
        budget: ToolErrorBudget,
        *,
        tool_name: str,
        detail: str,
        legacy_exc: BaseException | None = None,
        exc_type: type[BaseException] = RuntimeError,
        boundary: bool = False,
    ) -> BaseException | None:
        """Eccezione che chiude il run per questo errore tool, o ``None``.

        UNICO punto in cui un errore tool diventa fatale. La contabilita sul
        budget avviene sempre (il budget e il registro veritiero del run), l'abort
        solo per i chiamanti che hanno chiesto ``fail_on_tool_error``: senza quel
        flag ogni errore torna al modello come feedback, come prima.
        """
        reason = budget.spend_boundary() if boundary else budget.spend_ordinary()
        if reason is None or not spec.fail_on_tool_error:
            return None
        if budget.limit is None:
            # Modalita storica: l'eccezione e quella di sempre, verbatim.
            return legacy_exc if legacy_exc is not None else RuntimeError(detail)
        logger.warning(
            "Tool error budget exhausted for {} on tool {}: {}",
            spec.session_key or "default",
            tool_name,
            reason,
        )
        collapsed = detail.replace("\n", " ").strip()
        return exc_type(f"stopped after {reason}; last failure: {collapsed}")

    def _with_boundary_outcome(
        self,
        spec: AgentRunSpec,
        budget: ToolErrorBudget,
        handled: tuple[Any, dict[str, str], BaseException | None],
        *,
        tool_name: str,
        detail: str,
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        """Aggiunge il verdetto di budget all'esito di ``classify_violation``.

        ``classify_violation`` decide *cosa dire* al modello di una violazione e
        restituisce sempre un errore soft; quanto a lungo si possa continuare a
        violare lo stesso boundary non e una sua domanda, ed e questa.
        """
        payload, event, _ = handled
        return payload, event, self._tool_error_outcome(
            spec, budget, tool_name=tool_name, detail=detail, boundary=True,
        )

    async def _emit_tool_result(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        params: Any,
        started: float,
        *,
        result: Any = None,
        error: BaseException | str | None = None,
    ) -> None:
        """Notifica all'hook che questa tool call e finita. Non solleva mai.

        INVARIANTE, applicata in :meth:`_run_tool`: **ogni** ramo di uscita passa
        da qui — lookup ripetuto bloccato, errore di prepare, timeout, eccezione,
        stringa ``Error``, violazione di boundary, successo. Un ramo che non
        emette e un tool fallito che scompare dallo stream di attivita, cioe
        proprio la classe di difetto per cui questo hook esiste. L'unica uscita
        senza evento e ``CancelledError``, che non e un esito del tool: la
        chiamata resta aperta e il digest la marca ``incomplete``.

        Le eccezioni dell'hook vengono inghiottite qui, a differenza degli hook
        semantici del runner: questo e osservazione: la tool call e gia stata
        eseguita e un consumatore rotto non ha il diritto di farla fallire.
        """
        emit = getattr(spec.hook, "after_execute_tool", None)
        if emit is None:
            return
        arguments = params if isinstance(params, dict) else tool_call.arguments
        try:
            await emit(ToolResultHookContext(
                name=tool_call.name,
                call_id=str(tool_call.id or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
                result=result,
                error=error,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            ))
        except Exception:
            logger.exception(
                "AgentHook.after_execute_tool error on tool {}", tool_call.name
            )

    async def _run_tool(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
        *,
        tool_errors: ToolErrorBudget | None = None,
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        # ``started`` prima di qualunque controllo: la durata che l'hook riporta e
        # quella della *chiamata*, non del solo ``execute()``, perche e quella che
        # un utente vede passare guardando il pannello.
        started = time.monotonic()
        budget = tool_errors if tool_errors is not None else ToolErrorBudget.from_spec(spec)
        hint = TOOL_ERROR_RETRY_HINT
        lookup_error = repeated_external_lookup_error(
            tool_call.name,
            tool_call.arguments,
            external_lookup_counts,
        )
        if lookup_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated external lookup blocked",
            }
            await self._emit_tool_result(
                spec, tool_call, tool_call.arguments, started, result=lookup_error,
            )
            return lookup_error + hint, event, self._tool_error_outcome(
                spec, budget,
                tool_name=tool_call.name,
                detail=lookup_error,
                legacy_exc=RuntimeError(lookup_error),
            )
        prepare_call = getattr(spec.tools, "prepare_call", None)
        tool, params, prep_error = None, tool_call.arguments, None
        if callable(prepare_call):
            with suppress(Exception):
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared
        if prep_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            # Emesso PRIMA della classificazione, quindi copre entrambe le uscite
            # sotto (violazione di boundary e errore ordinario): il testo grezzo e
            # lo stesso, ed e da quello che la riga leggibile viene costruita.
            await self._emit_tool_result(
                spec, tool_call, params, started, result=prep_error,
            )
            handled = self._classify_violation(
                raw_text=prep_error,
                soft_payload=prep_error + hint,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return self._with_boundary_outcome(
                    spec, budget, handled, tool_name=tool_call.name, detail=prep_error,
                )
            return prep_error + hint, event, self._tool_error_outcome(
                spec, budget,
                tool_name=tool_call.name,
                detail=prep_error,
                legacy_exc=RuntimeError(prep_error),
            )
        # J1 (``security.subagent_policy``): un subagent che ha letto contenuto
        # non fidato e dati personali non manda più niente fuori, tranne verso i
        # siti del compito. Il rifiuto torna come risultato normale e accreditato:
        # non è un errore del modello, e contarlo chiuderebbe il lavoro.
        refusal = policy_before_tool(tool_call.name, params)
        if refusal is not None:
            budget.credit()
            await self._emit_tool_result(spec, tool_call, params, started, result=refusal)
            return refusal, {
                "name": tool_call.name, "status": "ok", "detail": "refused: junction",
            }, None
        emit_file_edit_events = (
            spec.progress_callback is not None
            and on_progress_accepts_file_edit_events(spec.progress_callback)
        )
        progress_callback = spec.progress_callback if emit_file_edit_events else None
        file_edit_trackers = (
            prepare_file_edit_trackers(
                call_id=tool_call.id,
                tool_name=tool_call.name,
                tool=tool,
                workspace=spec.workspace,
                params=params if isinstance(params, dict) else None,
            )
            if progress_callback is not None
            else None
        )
        if file_edit_trackers and progress_callback is not None:
            await invoke_file_edit_progress(
                progress_callback,
                [build_file_edit_start_event(
                    file_edit_tracker,
                    params if isinstance(params, dict) else None,
                ) for file_edit_tracker in file_edit_trackers],
            )
        try:
            timeout_s = tool_timeout_s()
            if timeout_s is not None and timeout_s <= 0:
                timeout_s = None
            if tool is not None:
                exec_coro = tool.execute(**params)
            else:
                exec_coro = spec.tools.execute(tool_call.name, params)
            if timeout_s is None:
                result = await exec_coro
            else:
                result = await asyncio.wait_for(exec_coro, timeout=timeout_s)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            # Rete di sicurezza: un tool che non ritorna mai bloccherebbe l'intero
            # turno (UI ferma su "Agent running"). Lo trattiamo come soft error e
            # lo restituiamo al modello, così il turno prosegue e si chiude.
            if file_edit_trackers and progress_callback is not None:
                await invoke_file_edit_progress(
                    progress_callback,
                    [
                        build_file_edit_error_event(file_edit_tracker, "timed out")
                        for file_edit_tracker in file_edit_trackers
                    ],
                )
            timeout_msg = (
                f"tool '{tool_call.name}' timed out after {timeout_s:g}s"
                if timeout_s is not None
                else f"tool '{tool_call.name}' timed out"
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": timeout_msg,
            }
            payload = f"Error: {timeout_msg}{hint}"
            await self._emit_tool_result(
                spec, tool_call, params, started, result=f"Error: {timeout_msg}",
            )
            return payload, event, self._tool_error_outcome(
                spec, budget,
                tool_name=tool_call.name,
                detail=timeout_msg,
                legacy_exc=TimeoutError(timeout_msg),
                exc_type=TimeoutError,
            )
        except BaseException as exc:
            if file_edit_trackers and progress_callback is not None:
                await invoke_file_edit_progress(
                    progress_callback,
                    [
                        build_file_edit_error_event(file_edit_tracker, str(exc))
                        for file_edit_tracker in file_edit_trackers
                    ],
                )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            payload = f"Error: {type(exc).__name__}: {exc}"
            # L'eccezione viaggia come oggetto, non come stringa: il formatter ne
            # ricava il nome di classe (``failed (ValueError)``, ``raised
            # ValueError`` per ``python_exec``), che il payload testuale
            # prefissato ``Error:`` non gli permetterebbe di riconoscere.
            await self._emit_tool_result(spec, tool_call, params, started, error=exc)
            handled = self._classify_violation(
                raw_text=str(exc),
                # Preserve legacy exception payloads without the retry hint.
                soft_payload=payload,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return self._with_boundary_outcome(
                    spec, budget, handled, tool_name=tool_call.name, detail=str(exc),
                )
            return payload, event, self._tool_error_outcome(
                spec, budget,
                tool_name=tool_call.name,
                detail=f"{type(exc).__name__}: {exc}",
                legacy_exc=exc,
            )

        # Tool migrati a ToolResult: srotola qui. Questo path chiama
        # tool.execute() direttamente e salta registry.execute() (dove l'esito
        # strutturato verrebbe reso): senza srotolamento il dataclass grezzo
        # finirebbe nella history e json.dumps fallirebbe.
        if isinstance(result, ToolResult):
            rendered = result.render()
            if not result.ok:
                msg = rendered if isinstance(rendered, str) else (
                    result.error.message if result.error else "tool failed"
                )
                result = msg if msg.startswith("Error") else f"Error: {msg}"
            else:
                result = rendered

        if isinstance(result, str) and result.startswith("Error"):
            if file_edit_trackers and progress_callback is not None:
                await invoke_file_edit_progress(
                    progress_callback,
                    [
                        build_file_edit_error_event(file_edit_tracker, result)
                        for file_edit_tracker in file_edit_trackers
                    ],
                )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": result.replace("\n", " ").strip()[:120],
            }
            # Come nel ramo di prepare: emesso PRIMA della classificazione, cosi
            # copre sia la violazione di boundary sia l'errore ordinario. Il
            # payload passa dopo lo srotolamento di ``ToolResult``, quindi e la
            # stessa stringa ``Error: ...`` che vede il modello.
            await self._emit_tool_result(
                spec, tool_call, params, started, result=result,
            )
            handled = self._classify_violation(
                raw_text=result,
                soft_payload=result + hint,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return self._with_boundary_outcome(
                    spec, budget, handled, tool_name=tool_call.name, detail=result,
                )
            return result + hint, event, self._tool_error_outcome(
                spec, budget,
                tool_name=tool_call.name,
                detail=result,
                legacy_exc=RuntimeError(result),
            )

        budget.credit()
        # Dentro un subagent, un tool riuscito può marcare il lavoro come non
        # fidato (web, browser) o privato (posizione, log): v.
        # ``security.subagent_policy``. Fuori da un subagent è una no-op.
        policy_after_tool(tool_call.name, params)
        if file_edit_trackers and progress_callback is not None:
            await invoke_file_edit_progress(
                progress_callback,
                [build_file_edit_end_event(
                    file_edit_tracker,
                    params if isinstance(params, dict) else None,
                ) for file_edit_tracker in file_edit_trackers],
            )

        await self._emit_tool_result(spec, tool_call, params, started, result=result)

        detail = "" if result is None else str(result)
        detail = detail.replace("\n", " ").strip()
        if not detail:
            detail = "(empty)"
        elif len(detail) > 120:
            detail = detail[:120] + "..."
        return result, {"name": tool_call.name, "status": "ok", "detail": detail}, None
