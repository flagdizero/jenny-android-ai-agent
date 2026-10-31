"""Sustained goal tools on the main agent (Codex-style).

Follow the built-in **long-goal** skill for lifecycle rules and how to phrase
objectives (especially **idempotent**, compaction-safe goals). Load that skill
from the skills listing (path shown there) before composing ``long_task.goal`` text.

``long_task`` registers an objective on the session (JSON-serializable metadata).
Active objectives are mirrored each turn into the Runtime Context block (see
``jenny.session.goal_state.goal_state_runtime_lines``) so compaction cannot hide them.
Work proceeds in ordinary agent turns (same runner, compaction as configured).
Call ``complete_goal`` when the sustained objective should stop being tracked:
finished successfully, or cancelled / superseded / redirected—in every case the recap should match reality.

There is **no** sub-agent orchestrator and **no** special WebSocket ``agent_ui`` stream.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING, Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
)
from jenny.session.goal_state import (
    GOAL_STATE_KEY,
    goal_state_raw,
    parse_goal_state,
    plan_open_in_session,
)

if TYPE_CHECKING:
    from jenny.session.manager import SessionManager


def _iso_now() -> str:
    return datetime.now().isoformat()


# Tetti di lunghezza per i due campi di testo libero, applicati in ``execute()``
# e NON dichiarati come ``maxLength`` nello schema. Un `maxLength` su un campo
# così capiente diventa una regola di ripetizione enorme nelle grammatiche di
# tool-calling e fa rifiutare l'intera richiesta (v. WIRE_STRING_LIMIT in
# ``schema.py``). Controllandoli qui il limite resta identico per il modello, ma
# non costa nulla sul filo.
_GOAL_MAX_CHARS = 12_000
_RECAP_MAX_CHARS = 8_000


class _GoalToolsMixin(ContextAware):
    """Shared routing context + Session lookup."""

    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions
        # Each subclass gets its own ContextVar so concurrent tasks across
        # different tool types (LongTaskTool vs CompleteGoalTool) do not
        # interfere with each other.
        self._request_ctx: ContextVar[RequestContext | None] = ContextVar(
            f"{self.__class__.__name__}_request_ctx",
            default=None,
        )

    def set_context(self, ctx: RequestContext) -> None:
        self._request_ctx.set(ctx)

    def _session(self):
        request_ctx = self._request_ctx.get()
        if request_ctx is None:
            return None
        key = request_ctx.session_key
        if not key:
            return None
        return self._sessions.get_or_create(key)


@tool_parameters(
    tool_parameters_schema(
        goal=StringSchema(
            "Sustained objective for this chat thread. First read the built-in **long-goal** skill, "
            "especially its Start fast section, then call this promptly once the user's intent is clear. "
            "The goal must still be idempotent, self-contained, bounded, and explicit about done-ness; "
            "do not delay this tool call to over-plan, research, or decide execution details. "
            "Keep it under 12000 characters.",
        ),
        ui_summary=StringSchema(
            "Optional one-line label for session lists / logs (≤120 chars).",
            max_length=120,
            nullable=True,
        ),
        required=["goal"],
    )
)
class LongTaskTool(Tool, _GoalToolsMixin):
    """Begin or replace focus on a long-running objective stored on the session."""

    _scopes = {"core", "orchestrator"}

    def __init__(self, sessions: Any) -> None:
        _GoalToolsMixin.__init__(self, sessions)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        sess = getattr(ctx, "sessions", None)
        assert sess is not None  # guarded by enabled()
        return cls(sessions=sess)

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @property
    def name(self) -> str:
        return "long_task"

    @property
    def description(self) -> str:
        return (
            "Mark this thread as a sustained long-running task. "
            "First read the built-in **long-goal** skill, especially its Start fast section; then call this "
            "as soon as the user's intent is clear. Write a good idempotent goal, but do not delay the tool "
            "call with long planning, research, or execution-detail thinking. "
            "The active goal is mirrored in Runtime Context each turn. Use normal tools until done, then call "
            "complete_goal when the objective is satisfied, cancelled, or replaced. "
            "If a goal is already active, finish it or call complete_goal before registering another."
        )

    async def execute(self, goal: str, ui_summary: str | None = None, **kwargs: Any) -> str:
        # Primo di tutti i controlli, e prima dei tetti di lunghezza: in sola
        # lettura nessuna chiamata puo' andare a buon fine, quindi un "accorcia
        # e richiama" manderebbe il modello a riprovare una strada chiusa.
        # Un goal sostenuto e' della stessa famiglia di un job cron — sopravvive
        # al turno, alla conversazione e al riavvio, e cambia il comportamento
        # futuro (wall timeout LLM, chip del goal, iniezione "keep working") —
        # quindi si chiude come ``CronTool``: con la frase condivisa, non con un
        # errore di confine. Un errore di confine ("outside allowed directory")
        # manderebbe il modello a cercare un'altra strada; qui non ce n'e'.
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        sess = self._session()
        if sess is None:
            return (
                "Error: long_task requires an active chat session (missing routing context)."
            )
        # Un piano a lista e un goal non convivono nella stessa sessione: due
        # cose che spingono il lavoro avanti, ciascuna col suo «continua»,
        # litigherebbero sul turno. Il piano è dell'utente (l'ha approvato lui),
        # quindi cede il goal.
        if plan_open_in_session(sess.key):
            return (
                "Error: this notebook has an open plan (proposed or running), and a "
                "sustained goal cannot run next to it. Work inside the plan's steps, or "
                "let the user finish or stop the plan first."
            )
        if len(goal) > _GOAL_MAX_CHARS:
            return (
                f"Error: goal must be at most {_GOAL_MAX_CHARS} characters "
                f"(got {len(goal)}). Tighten it and call again."
            )
        prior = parse_goal_state(goal_state_raw(sess.metadata))
        if isinstance(prior, dict) and prior.get("status") == "active":
            return (
                "Error: a sustained goal is already active. "
                "Use complete_goal when finished, or ask the user before replacing it."
            )

        summary = (ui_summary or "").strip()[:120]
        blob = {
            "status": "active",
            "objective": goal.strip(),
            "ui_summary": summary,
            "started_at": _iso_now(),
        }
        sess.metadata[GOAL_STATE_KEY] = blob
        self._sessions.save(sess)
        extra = f"\nSummary line: {summary}" if summary else ""
        return (
            "Goal recorded. Keep working toward the objective using ordinary tools. "
            "When fully done (verified against what was asked), call complete_goal with a "
            f"short recap.{extra}"
        )


@tool_parameters(
    tool_parameters_schema(
        recap=StringSchema(
            "Brief recap for the user (plain text). When the goal succeeded, confirm outcomes; "
            "if the user cancelled, pivoted, or replaced the objective, say so honestly. "
            "Keep it under 8000 characters.",
            nullable=True,
        ),
        required=[],
    )
)
class CompleteGoalTool(Tool, _GoalToolsMixin):
    """Mark the active sustained goal finished after all required work is verified."""

    _scopes = {"core", "orchestrator"}

    def __init__(self, sessions: Any) -> None:
        _GoalToolsMixin.__init__(self, sessions)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        sess = getattr(ctx, "sessions", None)
        assert sess is not None
        return cls(sessions=sess)

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @property
    def name(self) -> str:
        return "complete_goal"

    @property
    def description(self) -> str:
        return (
            "End bookkeeping for the active sustained goal. "
            "Use when the objective is fully achieved and verified—recap what was delivered. "
            "Also call when the user cancels, redirects, or replaces the goal: recap must reflect "
            "what actually happened (not necessarily success). "
            "If no goal is active, the tool reports that and leaves metadata unchanged."
        )

    async def execute(self, recap: str | None = None, **kwargs: Any) -> str:
        # NESSUN cancello di sola lettura qui, ed e' una decisione, non una
        # dimenticanza: la si rilegga se questo tool cambia mestiere.
        # ``long_task`` si chiude perche' *crea* un'obbligazione durevole; questo
        # non puo' crearne nessuna — riscrive lo stato di un blob che esiste
        # gia', e la sola transizione possibile e' active -> completed. E se
        # fosse chiuso, un turno in sola lettura che ha davvero soddisfatto un
        # obiettivo di sola lettura ("scopri X e dimmelo") non avrebbe modo di
        # fermare l'iniezione "keep working" (v. ``_goal_continue`` in
        # ``loop.py``): ogni turno successivo verrebbe spronato verso un
        # obiettivo gia' raggiunto.
        sess = self._session()
        if sess is None:
            return "Error: complete_goal requires an active chat session."
        if recap is not None and len(recap) > _RECAP_MAX_CHARS:
            return (
                f"Error: recap must be at most {_RECAP_MAX_CHARS} characters "
                f"(got {len(recap)}). Shorten it and call again."
            )
        prior = parse_goal_state(goal_state_raw(sess.metadata))
        if not isinstance(prior, dict) or prior.get("status") != "active":
            return "No active goal to complete."

        ended = _iso_now()
        sess.metadata[GOAL_STATE_KEY] = {
            **prior,
            "status": "completed",
            "completed_at": ended,
            "recap": (recap or "").strip(),
        }
        self._sessions.save(sess)
        tail = (recap or "").strip()
        if tail:
            return f"Goal marked complete ({ended}). Recap:\n{tail}"
        return f"Goal marked complete ({ended})."


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [LongTaskTool, CompleteGoalTool]
