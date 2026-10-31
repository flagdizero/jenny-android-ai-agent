"""Spawn tool for creating background subagents."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from jenny.agent.agent_types import (
    AGENT_TYPE_NAMES,
    DEFAULT_AGENT_TYPE,
    PLANNING_HELPER_TYPES,
    UnknownAgentTypeError,
)
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext, current_request_context
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
        label=StringSchema(
            "Short label the user sees while the subagent works: 2-5 words naming "
            "what it does, in the language you are speaking with the user (e.g. "
            "'flight prices to Lisbon'). Without one the user sees the first words "
            "of the task, cut off."
        ),
        agent_type=StringSchema(
            "Which kind of subagent to run. Pick deliberately — it decides which "
            "tools the subagent gets. "
            "'researcher': web search and fetch, an interactive browser session "
            "(browser_open/snapshot/do/read/close — for pages behind a cookie wall, "
            "a login or a site's own search box), read/list/write files, NO code "
            "execution — use it for gathering material online. "
            "'writer': read/list/write files and apply_patch, NO network — use it "
            "for docs and synthesis of material already gathered, outside a notebook "
            "(in a notebook, the notebook-editor writes). "
            "'coder': filesystem, search, apply_patch, python_exec, exec sessions "
            "and logs, no web tools (python_exec reaches public sites only through "
            "its http_get/http_post) — use it to write or change code. "
            "'analyst': python_exec plus read/list/write, no web tools (same "
            "python_exec network) — use it for computation, data and charts. "
            "'sysadmin': SSH to the machines the user registered (run commands, "
            "start and follow long jobs, transfer files) plus read/list/write, NO "
            "network and NO local code execution — the only type that reaches a "
            "remote machine, and the only way to do so. "
            "'app-builder': writes and tests a new version of one of the user's apps (the "
            "app tool, read/list/grep), NO network — use it to create or change an app, then "
            "publish its work with `app action=publish`. It writes code only: it cannot use "
            "the app's actions (add a record, mark an item), so do that yourself after "
            "publishing. "
            "'notebook-editor': only in a notebook's chat — changes this notebook as the "
            "user asks: its pages, the map and the board (cards, which pages each holds, "
            "drawers, reminders, threads), NO network. Every change to a notebook goes to it. "
            "'operator': the whole subagent toolset except SSH (fallback for tasks that fit "
            "none of the above; never a way around a missing tool).",
            enum=AGENT_TYPE_NAMES,
        ),
        app=StringSchema(
            "For agent_type=app-builder: the slug of the app it works on. A follow-up on "
            "an app goes back to the builder that already worked on it, which knows the code."
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

    async def _to_known_builder(self, session_key: str, task: str,
                                app: str | None) -> str | None:
        """Un ritocco a un'app va al costruttore che ci ha gia' lavorato.

        Il 08/10/2026 sul Titan sei ritocchi al Portafoglio sono stati sei
        costruttori nuovi: ognuno rileggeva la guida (20-60 s), vedeva solo il
        brief e aggiungeva il suo pezzo, finche' l'utente ha chiamato il codice
        «un casino». La regola «follow-up: subagent_send» Jenny non l'ha mai
        applicata; qui la applica lo strumento. L'app si prende da ``app`` o, se
        manca, dal brief, quando nomina una sola delle app che un costruttore ha
        toccato in questa conversazione.
        """
        import re

        from jenny.agent.subagent import SubagentSendError
        from jenny.agent.tools.app_tool import apps_with_builders, builder_for

        slug = (app or "").strip()
        if not slug:
            named = [s for s in apps_with_builders(session_key)
                     if re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", task)]
            slug = named[0] if len(named) == 1 else ""
        lineage = builder_for(session_key, slug) if slug else None
        if lineage is None or not self._manager.continuable(lineage):
            return None
        try:
            result = await self._manager.send(lineage, task)
        except SubagentSendError:
            return None
        return (f"Sent to the app-builder that already worked on `{slug}` [{lineage}]: it "
                f"knows the code and continues from it. {result.text}")

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
        app: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        # Import locale: ``jenny.agent.subagent`` importa il ToolLoader, che
        # importa questo modulo. A livello di modulo l'import sarebbe circolare.
        from jenny.agent.subagent import (
            SubagentCapabilityError,
            SubagentConcurrencyLimitError,
        )

        # Mentre si prepara un piano si guarda il quaderno, non il web: solo
        # l'editore, in sola lettura (``agent_types.PLANNING_HELPER_TYPES``).
        request = current_request_context()
        wanted = agent_type or DEFAULT_AGENT_TYPE
        if (
            request is not None
            and request.metadata.get("plan_mode") is True
            and wanted not in PLANNING_HELPER_TYPES
        ):
            return (
                f"Cannot spawn a '{wanted}' while preparing a plan: now you only look at the "
                "notebook. A notebook-editor can take stock of it for you (read-only). What "
                "must be searched on the web goes into the plan as a `research` step, with "
                "the item marked `web` in the coverage."
            )
        # Le app si scrivono solo per una conversazione con l'utente: un
        # costruttore lanciato da un cron, dal giardiniere o da Dream non potrebbe
        # comunque scrivere (il tool ``app`` lo rifiuta), quindi lo si dice qui,
        # prima di spendere un giro intero.
        if (agent_type or "") == "app-builder":
            from jenny.session.keys import is_personal_session_key, is_project_session_key

            key = self._session_key.get() or ""
            if not (is_personal_session_key(key) or is_project_session_key(key)):
                return ("Cannot spawn an app-builder here: apps are written only for a "
                        "conversation with the user (the personal chat or a notebook's chat).")
            routed = await self._to_known_builder(key, task, app)
            if routed is not None:
                return routed
        # Un quaderno lo cambia l'editore, e solo l'editore: il ``writer`` scriveva
        # le pagine senza poter toccare la bacheca, e Jenny doveva scegliere fra due
        # tipi che si sovrapponevano. Lo separa il codice, non il prompt.
        if (agent_type or "") in ("notebook-editor", "writer"):
            from jenny.session.keys import is_project_session_key

            in_notebook = is_project_session_key(self._session_key.get() or "")
            if agent_type == "notebook-editor" and not in_notebook:
                return ("Cannot spawn a notebook-editor here: it changes a notebook only from "
                        "that notebook's chat.")
            if agent_type == "writer" and in_notebook:
                return ("Cannot spawn a writer in a notebook: spawn a notebook-editor, which "
                        "writes the pages and the board.")
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
