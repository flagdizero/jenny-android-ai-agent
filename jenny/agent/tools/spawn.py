"""Spawn tool for creating background subagents."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from jenny.agent.agent_types import (
    AGENT_TYPE_NAMES,
    DEFAULT_AGENT_TYPE,
    UnknownAgentTypeError,
)
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.schema import (
    BooleanSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.security.workspace_access import current_workspace_scope

if TYPE_CHECKING:
    from jenny.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
        agent_type=StringSchema(
            "Which kind of subagent to run. Pick deliberately — it decides which "
            "tools the subagent gets. "
            "'researcher': web search and fetch, an interactive browser session "
            "(browser_open/snapshot/do/read/close — for pages behind a cookie wall, "
            "a login or a site's own search box), read/list/write files, NO code "
            "execution — use it for gathering material online. "
            "'writer': read/list/write files and apply_patch, NO network — use it "
            "for docs, wiki pages and synthesis of material already gathered. "
            "'coder': filesystem, search, apply_patch, python_exec, exec sessions "
            "and logs, NO network — use it to write or change code. "
            "'analyst': python_exec plus read/list/write, NO network — use it for "
            "computation, data and charts. "
            "'sysadmin': SSH to the machines the user registered (run commands, "
            "start and follow long jobs, transfer files) plus read/list/write, NO "
            "network and NO local code execution — the only type that reaches a "
            "remote machine, and the only way to do so. "
            "'operator': the whole subagent toolset except SSH (fallback for tasks that fit "
            "none of the above; never a way around a missing tool).",
            enum=AGENT_TYPE_NAMES,
        ),
        quick=BooleanSchema(
            description=(
                "Mark this as a short job (a single lookup, a quick check). One "
                "concurrency slot is reserved for quick jobs, so a fan-out of "
                "long subagents can never leave you unable to serve the user. "
                "Set it only when the task really is short — a long job in the "
                "reserved slot defeats the purpose."
            ),
        ),
        temperature=NumberSchema(
            description=(
                "Optional sampling temperature for the subagent "
                "(0.0 = deterministic, higher = more creative). "
                "Defaults to the agent type's temperature."
            ),
            minimum=0.0,
            maximum=2.0,
        ),
        required=["task"],
    )
)
class SpawnTool(Tool, ContextAware):
    """Tool to spawn a subagent for background task execution."""

    _scopes = {"core", "orchestrator"}

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel: ContextVar[str] = ContextVar("spawn_origin_channel", default="internal")
        self._origin_chat_id: ContextVar[str] = ContextVar("spawn_origin_chat_id", default="direct")
        self._session_key: ContextVar[str] = ContextVar("spawn_session_key", default="internal:direct")
        self._origin_message_id: ContextVar[str | None] = ContextVar(
            "spawn_origin_message_id",
            default=None,
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(manager=ctx.subagent_manager)

    def set_context(self, ctx: RequestContext) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel.set(ctx.channel)
        self._origin_chat_id.set(ctx.chat_id)
        self._session_key.set(ctx.session_key or f"{ctx.channel}:{ctx.chat_id}")
        self._origin_message_id.set(ctx.message_id)

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for anything multi-step: the subagent's tool output stays "
            "out of this conversation, which is the point. "
            "The subagent will complete the task and report back when done — the "
            "result arrives on its own, do not poll for it. "
            "Choose 'agent_type' deliberately and set 'quick' for short jobs. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )

    async def execute(
        self,
        task: str,
        label: str | None = None,
        temperature: float | None = None,
        agent_type: str | None = None,
        quick: bool = False,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        # Import locale: ``jenny.agent.subagent`` importa il ToolLoader, che
        # importa questo modulo. A livello di modulo l'import sarebbe circolare.
        from jenny.agent.subagent import (
            SubagentCapabilityError,
            SubagentConcurrencyLimitError,
        )

        # L'invariante di concorrenza vive nel manager (dove sta lo stato); qui
        # si formatta soltanto il messaggio per il modello.
        try:
            return await self._manager.spawn(
                task=task,
                label=label,
                origin_channel=self._origin_channel.get(),
                origin_chat_id=self._origin_chat_id.get(),
                session_key=self._session_key.get(),
                origin_message_id=self._origin_message_id.get(),
                temperature=temperature,
                workspace_scope=current_workspace_scope(),
                agent_type=agent_type or DEFAULT_AGENT_TYPE,
                quick=bool(quick),
            )
        except UnknownAgentTypeError as e:
            # Il modello inventa un tipo: l'errore torna come testo con l'elenco
            # dei validi, non come traceback.
            return f"Cannot spawn subagent: {e}"
        except SubagentCapabilityError as e:
            # Il tipo esiste ma la sua capacita e spenta. Va detto all'utente,
            # non aggirato: rilanciare su un altro tipo gli darebbe un risultato
            # inventato al posto di un interruttore da alzare.
            return (
                f"Cannot spawn a '{e.agent_type}' subagent: {e.reason}. "
                f"Without {', '.join(e.tools)} this agent type cannot do the job. "
                "Tell the user what to turn on — do not retry with another agent type."
            )
        except SubagentConcurrencyLimitError as e:
            hint = (
                "One slot is kept free for short tasks."
                if e.reserved
                else "Wait for a running subagent to complete before spawning a new one."
            )
            return (
                f"Cannot spawn subagent: concurrency limit reached "
                f"({e.running}/{e.limit} running). {hint}"
            )


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [SpawnTool]
