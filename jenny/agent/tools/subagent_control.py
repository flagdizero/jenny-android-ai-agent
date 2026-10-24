"""Tool di controllo dei subagent per l'orchestratore.

Scope ``orchestrator`` e solo quello: mai ``subagent``. Un subagent non deve
poter guidare i propri fratelli, e l'assenza di ``spawn`` nello scope subagent e
esattamente cio che oggi impedisce la ricorsione — questi tool non devono
aprirle una porta laterale.

``subagent_status`` porta una guardia anti-polling: dare a un LLM un tool che
risponde "non ancora finito" significa vederlo richiamare lo stesso tool
all'iterazione dopo, e ancora, per aspettare un risultato che l'annuncio di
completamento gli consegna gratis. Vedi :meth:`SubagentStatusTool._poll_refusal`.

Le guardie per-turno di questo modulo si delimitano sul turn id dell'AgentLoop
(``RequestContext.turn_id``), non sul ``message_id``: quello e un id di routing
che il canale WebSocket non manda mai, quindi una guardia che vi si appoggia e
armata solo nei test. Vedi :meth:`_SubagentControlTool._turn_identity`.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.schema import (
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)

# Quanti caratteri di riassunto del risultato entrano nell'elenco compatto. Il
# testo completo si ottiene chiedendo un singolo ``task_id``.
_SUMMARY_PREVIEW_CHARS = 200


class _SubagentControlTool(Tool, ContextAware):
    """Base dei tool di controllo: manager + contesto della richiesta."""

    _scopes = {"orchestrator"}

    def __init__(self, manager: Any) -> None:
        self._manager = manager
        self._session_key: ContextVar[str | None] = ContextVar(
            f"{type(self).__name__}_session_key", default=None
        )
        # Identita del turno in corso. ContextVar perche l'istanza del tool e
        # condivisa fra turni concorrenti di sessioni diverse.
        self._turn_id: ContextVar[str | None] = ContextVar(
            f"{type(self).__name__}_turn_id", default=None
        )
        # Un solo ERROR per istanza quando l'identita manca: la condizione e
        # sistemica (un bug di wiring), non per-chiamata.
        self._missing_turn_logged = False

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "subagent_manager", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(manager=ctx.subagent_manager)

    def set_context(self, ctx: RequestContext) -> None:
        self._session_key.set(ctx.session_key or f"{ctx.channel}:{ctx.chat_id}")
        self._turn_id.set(ctx.turn_id)

    def _belongs_here(self, task_id: str) -> bool:
        """``task_id`` (o lineage) è un subagent partito da questa sessione?

        ``subagent_status`` guarda solo i subagent della propria sessione, ma
        ``cancel``/``restart``/``send`` passavano l'id al manager così com'era:
        da un quaderno si fermava, rilanciava o pilotava un subagent della
        chat personale. Stesso filtro dello stato — i vivi da
        ``status_snapshot``, i finiti da ``list_records`` (tutti, non solo i
        recenti) — e fail-closed senza sessione: queste azioni cambiano
        qualcosa, lo stato no.
        """
        key = self._session_key.get()
        if not key or not task_id:
            return False
        snapshot = self._manager.status_snapshot(key)
        for entry in snapshot.get("running") or []:
            if task_id in (entry.get("task_id"), entry.get("lineage_id")):
                return True
        return any(
            task_id in (record.task_id, record.lineage_id)
            for record in self._manager.list_records(key)
        )

    def _turn_identity(self) -> str | None:
        """Identita del turno per le guardie per-turno, o ``None`` se assente.

        L'``AgentLoop`` lega un turn id a ogni turno (``_dispatch`` per il bus,
        ``process_direct`` per cron e comandi), quindi in produzione l'identita
        c'e sempre: se manca e un bug di wiring, non una condizione normale.

        Scelta deliberata su cosa fare quando manca: le guardie restano
        permissive — rifiutare su un'identita che non delimita alcun turno
        vorrebbe dire negare la prima, legittima ``subagent_status`` di un turno
        nuovo, cioe rispondere "Refused" a un utente che chiede cosa sta girando
        — ma l'assenza esce a ERROR nei log. Una guardia disarmata in silenzio e
        peggio di nessuna guardia: si legge come una protezione che non c'e, ed e
        esattamente cosi che questa e rimasta morta in produzione.
        """
        turn = self._turn_id.get()
        if turn is None and not self._missing_turn_logged:
            self._missing_turn_logged = True
            logger.error(
                "{}: no turn identity (RequestContext.turn_id is None), the "
                "per-turn guard is disarmed for this agent. Every turn "
                "dispatched by AgentLoop binds one (see "
                "jenny.agent.tools.context.bind_turn_id): this is a wiring bug.",
                self.name,
            )
        return turn


@tool_parameters(
    tool_parameters_schema(
        task_id=StringSchema(
            "Optional task id (or lineage id) to inspect in detail. "
            "Omit it for a snapshot of everything."
        ),
    )
)
class SubagentStatusTool(_SubagentControlTool):
    """Snapshot dei subagent in corso e di quelli terminati di recente."""

    def __init__(self, manager: Any, registry: Any = None) -> None:
        super().__init__(manager)
        self._registry = registry
        # Bookkeeping della guardia: turno e valore di ``registry.exec_seq``
        # dell'ultima chiamata. Attributi d'istanza e non ContextVar: la
        # guardia deve ricordare *fra* tool call, non dentro una sola.
        self._last_turn: str | None = None
        self._last_seq: int | None = None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(manager=ctx.subagent_manager, registry=getattr(ctx, "registry", None))

    @property
    def name(self) -> str:
        return "subagent_status"

    @property
    def description(self) -> str:
        return (
            "Show running and recently finished subagents (id, type, state, "
            "elapsed time, last tool, result summary). "
            "Results are delivered to you automatically when a subagent "
            "finishes: NEVER call this to wait for one. Call it when the user "
            "asks what is running, or before cancelling or restarting something."
        )

    @property
    def read_only(self) -> bool:
        return True

    def _poll_refusal(self, turn: str | None) -> str | None:
        """Rifiuto se questa e la seconda chiamata consecutiva nello stesso turno.

        "Consecutiva" = nessun'altra tool call in mezzo, misurata sul contatore
        del registry: se fra due chiamate a ``subagent_status`` il contatore e
        avanzato di uno solo, quell'uno e questa stessa chiamata. Il turno e
        quello di :meth:`_SubagentControlTool._turn_identity`, che documenta
        anche il comportamento quando l'identita manca.

        Un rifiuto secco e preferibile a una policy scritta nel prompt: sotto
        pressione il modello la ignora, e il costo del polling lo paga l'utente
        in token e in latenza.
        """
        seq = getattr(self._registry, "exec_seq", None)
        if turn is None or not isinstance(seq, int):
            return None
        if turn == self._last_turn and self._last_seq is not None and seq == self._last_seq + 1:
            return (
                "Refused: subagent_status was already called in this turn and no "
                "other tool ran in between. Subagent results are announced to you "
                "automatically when they finish — polling this tool cannot make "
                "that happen sooner, it only burns tokens. Answer the user with "
                "what the previous call already told you, or do other work."
            )
        return None

    def _remember_call(self, turn: str | None) -> None:
        seq = getattr(self._registry, "exec_seq", None)
        self._last_turn = turn
        self._last_seq = seq if isinstance(seq, int) else None

    async def execute(self, task_id: str | None = None, **kwargs: Any) -> str:
        # Identita risolta una volta sola: due letture significherebbero due
        # righe di log nel caso degenere.
        turn = self._turn_identity()
        refusal = self._poll_refusal(turn)
        # Il bookkeeping si aggiorna anche quando si rifiuta: altrimenti la
        # terza chiamata di fila troverebbe il contatore avanzato di due e
        # passerebbe, trasformando la guardia in "polling a chiamate alterne".
        self._remember_call(turn)
        if refusal is not None:
            return refusal

        snapshot = self._manager.status_snapshot(self._session_key.get())
        if task_id:
            return self._render_detail(snapshot, task_id)
        return self._render_overview(snapshot)

    @staticmethod
    def _running_line(entry: dict[str, Any]) -> str:
        parts = [
            f"[{entry['task_id']}] {entry['label']}",
            f"type={entry['agent_type']}",
            f"state={entry['state']}",
            f"phase={entry['phase']}",
            f"iter={entry['iteration']}",
            f"elapsed={entry['elapsed_s']:.0f}s",
            f"idle={entry['idle_s']:.0f}s",
        ]
        if entry.get("last_tool"):
            parts.append(f"last_tool={entry['last_tool']}")
        if entry.get("attempt", 1) > 1:
            parts.append(f"attempt={entry['attempt']}")
        return "- " + ", ".join(parts)

    @staticmethod
    def _recent_line(entry: dict[str, Any], *, preview: int) -> str:
        ago = max(0.0, time.time() - float(entry.get("ended_at") or 0.0))
        parts = [
            f"[{entry['task_id']}] {entry['label']}",
            f"type={entry['agent_type']}",
            f"state={entry['state']}",
        ]
        if entry.get("stop_reason"):
            parts.append(f"stop_reason={entry['stop_reason']}")
        parts.append(f"ended={ago / 60:.0f}m ago")
        if entry.get("resumable"):
            # Fermato al budget con la storia intatta: la mossa e continuare, non
            # rilanciare da zero (``subagent_send``).
            parts.append("resumable=yes (continue it with subagent_send)")
        parts.append(f"restartable={'yes' if entry.get('can_restart') else 'no (attempt cap)'}")
        line = "- " + ", ".join(parts)
        summary = (entry.get("result_summary") or "").strip()
        if summary:
            if preview and len(summary) > preview:
                summary = summary[:preview].rstrip() + "…"
            line += "\n  " + summary.replace("\n", " ")
        return line

    def _render_overview(self, snapshot: dict[str, Any]) -> str:
        running = snapshot.get("running") or []
        recent = snapshot.get("recent") or []
        lines: list[str] = [f"Running subagents ({len(running)}):"]
        if running:
            lines.extend(self._running_line(e) for e in running)
        else:
            lines.append("- none")
        lines.append("")
        lines.append(f"Recently finished ({len(recent)}):")
        if recent:
            lines.extend(self._recent_line(e, preview=_SUMMARY_PREVIEW_CHARS) for e in recent)
        else:
            lines.append("- none")
        lines.append("")
        lines.append(
            "Reminder: finished subagents report to you on their own. "
            "Do not call subagent_status again to wait."
        )
        return "\n".join(lines)

    def _render_detail(self, snapshot: dict[str, Any], task_id: str) -> str:
        for entry in snapshot.get("running") or []:
            if task_id in (entry["task_id"], entry["lineage_id"]):
                return "Running:\n" + self._running_line(entry)
        for entry in snapshot.get("recent") or []:
            if task_id in (entry["task_id"], entry["lineage_id"]):
                # Nel dettaglio il riassunto va per intero: e la ragione per cui
                # si chiede un singolo id.
                return "Finished:\n" + self._recent_line(entry, preview=0)
        return (
            f"No subagent found for id '{task_id}'. It may have expired from the "
            "recent list. Call subagent_status without arguments to see what exists."
        )


@tool_parameters(
    tool_parameters_schema(
        task_id=StringSchema("Task id of the running subagent to cancel"),
        required=["task_id"],
    )
)
class SubagentCancelTool(_SubagentControlTool):
    """Ferma un singolo subagent vivo."""

    @property
    def name(self) -> str:
        return "subagent_cancel"

    @property
    def description(self) -> str:
        return (
            "Cancel one running subagent by task id. The other subagents keep "
            "running. A cancelled subagent reports nothing back, so tell the user "
            "what you cancelled and why."
        )

    async def execute(self, task_id: str, **kwargs: Any) -> str:
        if not self._belongs_here(task_id):
            return (
                f"Nothing to cancel: no subagent [{task_id}] was started from this "
                "conversation (check subagent_status)."
            )
        stopped = await self._manager.cancel_task(task_id)
        if stopped:
            return f"Cancelled subagent [{task_id}]."
        return (
            f"Nothing to cancel: subagent [{task_id}] is not running "
            "(already finished, or the id is wrong — check subagent_status)."
        )


@tool_parameters(
    tool_parameters_schema(
        task_id=StringSchema("Task id (or lineage id) of the subagent to relaunch"),
        extra_instructions=StringSchema(
            "Optional corrective note appended to the original task, e.g. what "
            "went wrong and what to do differently this time."
        ),
        required=["task_id"],
    )
)
class SubagentRestartTool(_SubagentControlTool):
    """Rilancia il lavoro di un subagent come tentativo successivo."""

    @property
    def name(self) -> str:
        return "subagent_restart"

    @property
    def description(self) -> str:
        return (
            "Relaunch a failed or stalled subagent's work as a new attempt, "
            "optionally with a corrective note. Same job, same lineage — not a new "
            "task, but it starts again from zero. For a subagent that stopped at "
            "its budget use subagent_send instead: it continues from where it "
            "stopped. Automatic relaunches are capped per job: when the cap is "
            "reached, report it to the user instead of retrying differently."
        )

    async def execute(
        self,
        task_id: str,
        extra_instructions: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Import locale: ``jenny.agent.subagent`` importa il ToolLoader, che
        # importa questo modulo (stesso motivo di ``spawn.py``).
        from jenny.agent.subagent import (
            SubagentConcurrencyLimitError,
            SubagentRestartError,
        )

        if not self._belongs_here(task_id):
            return (
                f"Cannot restart subagent [{task_id}]: no subagent with this id was "
                "started from this conversation. Call subagent_status to see yours."
            )
        try:
            return await self._manager.restart(
                task_id,
                extra_instructions=extra_instructions,
                # Mai ``manual=True``: il tetto dei rilanci automatici esiste
                # proprio per l'orchestratore. Il bottone dell'utente e altrove.
                manual=False,
            )
        except SubagentRestartError as e:
            return f"Cannot restart subagent [{task_id}]: {e}"
        except SubagentConcurrencyLimitError as e:
            hint = (
                "One slot is kept free for short tasks."
                if e.reserved
                else "Wait for a running subagent to finish first."
            )
            return (
                f"Cannot restart subagent [{task_id}]: concurrency limit reached "
                f"({e.running}/{e.limit} running). {hint}"
            )


@tool_parameters(
    tool_parameters_schema(
        task_id=StringSchema("Task id (or lineage id) of the subagent to talk to"),
        message=StringSchema(
            "What to tell the subagent: a correction, a change of direction, or "
            "extra information. Write it as an instruction to the subagent, not as "
            "a description of it."
        ),
        quick=BooleanSchema(
            description=(
                "Mark the continuation as a short job so it may use the reserved "
                "concurrency slot. Only for genuinely small follow-ups."
            ),
        ),
        required=["task_id", "message"],
    )
)
class SubagentSendTool(_SubagentControlTool):
    """Parla a un subagent: iniezione, resume o rilancio, deciso dal manager."""

    def __init__(self, manager: Any) -> None:
        super().__init__(manager)
        # Guardia anti-ripetizione: (task_id, messaggio) gia inviati NEL TURNO
        # corrente. Una iniezione e silenziosa (il subagent non risponde
        # subito), quindi il modello e tentato di rimandarla credendo che sia
        # andata perduta. Il set si svuota al cambio di turno, cosi non cresce
        # per la vita del processo.
        self._sent_turn: str | None = None
        self._sent: set[tuple[str, str]] = set()

    @property
    def name(self) -> str:
        return "subagent_send"

    @property
    def description(self) -> str:
        return (
            "Send a message to a subagent you already started: a correction, a "
            "change of direction, extra context. Use this instead of spawning a "
            "fresh subagent for a follow-up on the same job — the subagent keeps "
            "what it already did. It works whether the subagent is still running "
            "(the message reaches it without stopping it), finished or stopped at "
            "its budget (it resumes from its own conversation, with what it read "
            "and wrote), or failed (the job is relaunched from zero with your "
            "message as a corrective note). The reply comes back on its own: do "
            "not poll subagent_status after sending."
        )

    def _duplicate_refusal(self, task_id: str, message: str) -> str | None:
        """Rifiuta lo stesso messaggio allo stesso subagent nello stesso turno.

        Il turno e quello di :meth:`_SubagentControlTool._turn_identity`, come
        per :meth:`SubagentStatusTool._poll_refusal`. Senza identita la guardia
        resta disarmata *e* svuota il set: tenerlo senza un confine in cui
        azzerarlo significherebbe rifiutare a vita un rinvio legittimo in un
        turno successivo, e farlo crescere per tutta la vita del processo.
        """
        turn = self._turn_identity()
        if turn is None:
            self._sent_turn = None
            self._sent.clear()
            return None
        if turn != self._sent_turn:
            self._sent_turn = turn
            self._sent.clear()
        if (task_id, message.strip()) in self._sent:
            return (
                f"Refused: you already sent this exact message to subagent "
                f"[{task_id}] in this turn. It was delivered — a subagent does not "
                "acknowledge messages, it just acts on them at its next step. "
                "Repeating it only makes the subagent read the same instruction "
                "twice. Tell the user what you sent, or do other work."
            )
        return None

    def _remember_send(self, task_id: str, message: str) -> None:
        """Registra solo un invio ANDATO A BUON FINE.

        Un invio rifiutato (pool pieno, target sconosciuto) non deve armare la
        guardia: riprovare dopo aver liberato uno slot e legittimo, mentre
        ripetere un messaggio consegnato non lo e.

        ``_sent_turn`` e autorevole: lo ha appena impostato (o azzerato)
        ``_duplicate_refusal``, che ``execute`` chiama sempre prima di qui.
        """
        if self._sent_turn is not None:
            self._sent.add((task_id, message.strip()))

    async def execute(
        self,
        task_id: str,
        message: str,
        quick: bool | None = None,
        **kwargs: Any,
    ) -> str:
        # Import locale: ``jenny.agent.subagent`` importa il ToolLoader, che
        # importa questo modulo (stesso motivo di ``spawn.py``).
        from jenny.agent.subagent import (
            SubagentConcurrencyLimitError,
            SubagentRestartError,
            SubagentSendError,
        )

        if not self._belongs_here(task_id):
            return (
                f"Cannot send to subagent [{task_id}]: no subagent with this id was "
                "started from this conversation. Call subagent_status to see yours."
            )
        refusal = self._duplicate_refusal(task_id, message or "")
        if refusal is not None:
            return refusal

        try:
            outcome = await self._manager.send(
                task_id,
                message,
                quick=None if quick is None else bool(quick),
            )
        except SubagentSendError as e:
            return f"Cannot send to subagent [{task_id}]: {e}"
        except SubagentRestartError as e:
            return (
                f"Cannot send to subagent [{task_id}]: it has no resumable "
                f"conversation and it cannot be relaunched either ({e})."
            )
        except SubagentConcurrencyLimitError as e:
            hint = (
                "One slot is kept free for short tasks — mark the follow-up as quick "
                "if it really is short."
                if e.reserved
                else "Wait for a running subagent to finish first."
            )
            return (
                f"Cannot send to subagent [{task_id}]: continuing it needs a "
                f"concurrency slot and the pool is full ({e.running}/{e.limit} "
                f"running). {hint}"
            )
        self._remember_send(task_id, message or "")
        return outcome.text


# Registrazione esplicita dei tool di questo modulo: il ToolLoader legge questa
# lista invece della reflection dir(). Un nuovo tool va aggiunto qui.
TOOLS = [
    SubagentStatusTool,
    SubagentCancelTool,
    SubagentRestartTool,
    SubagentSendTool,
]
