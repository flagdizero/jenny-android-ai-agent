"""I tool del piano a lista: proporre, chiudere un passo, saltarlo, fermarsi su una domanda.

Sono la metà del modello di un ciclo che guida il codice (``jenny/plan/driver.py``):
il driver apre ogni passo, Jenny lo chiude con uno di questi. Nessuno di loro
approva, ferma o torna indietro: quelle sono decisioni dell'utente, e passano
dalle RPC ``plan.*`` che nessun tool raggiunge.

**Si vedono solo quando servono** (``AgentLoop._turn_tool_view``):
``plan_propose`` nel turno di pianificazione (il tasto Piano, metadato
``plan_mode``), gli altri tre quando la sessione ha un passo ``now``. Fuori da
un piano non esistono: non costano prompt e non tentano il modello.

Il driver lo lega il composition root dopo aver costruito l'agente
(:meth:`PlanTool.bind_driver`), come la callback del tool ``message``: i tool
nascono prima del driver, che ha bisogno dell'agente per nascere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_context
from jenny.agent.tools.schema import (
    ArraySchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.plan.model import COVERAGE_STRATEGIES, MAX_STEPS, MIN_STEPS, STEP_KINDS

if TYPE_CHECKING:
    from jenny.plan.driver import PlanDriver

_NO_DRIVER = "Error: plans are not available right now."


class PlanTool(Tool):
    """Base dei quattro tool: il driver legato tardi e la sessione del turno."""

    _scopes = {"core", "orchestrator"}

    def __init__(self) -> None:
        self._driver: PlanDriver | None = None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    def bind_driver(self, driver: PlanDriver | None) -> None:
        self._driver = driver

    @staticmethod
    def _session_key() -> str | None:
        ctx = current_request_context()
        return ctx.session_key if ctx is not None else None


@tool_parameters(
    tool_parameters_schema(
        title=StringSchema("The whole job in a few words (one line, at most 80 characters)."),
        steps=ArraySchema(
            ObjectSchema(
                title=StringSchema(
                    "What the user reads on the plan card: their language and words, one "
                    "line, at most 60 characters."
                ),
                detail=StringSchema(
                    "For you: exactly what you will do in this step, which pages or files, "
                    "and how you will know it is done. You read it again when the step "
                    "starts, maybe after a restart."
                ),
                done_when=StringSchema(
                    "When this step is done, with the quality bar, e.g. \"every page has at "
                    "least 3 concrete facts with their source; 'not documented' only after "
                    "a search that found nothing, with the queries tried\"."
                ),
                kind=StringSchema("The kind of step.", enum=list(STEP_KINDS)),
                same_as=StringSchema(
                    "When revising a proposal: the id of the previous version's step this "
                    "one comes from.",
                    nullable=True,
                ),
                required=["title", "detail", "done_when"],
            ),
            description=f"The steps, in order: {MIN_STEPS} to {MAX_STEPS}.",
        ),
        coverage=ArraySchema(
            ObjectSchema(
                item=StringSchema("One thing the job needs, e.g. a page to write."),
                have=StringSchema("What the notebook already has for it (you read it)."),
                missing=StringSchema("What is missing."),
                strategy=StringSchema(
                    "How the plan fills it: notebook (the notebook is enough), web (a "
                    "research step searches it and names it in its detail), leave (it stays "
                    "uncovered: say why). Never ask: ask the user before proposing.",
                    enum=[c for c in COVERAGE_STRATEGIES if c != "ask"],
                ),
                why=StringSchema("Why this strategy; required for leave."),
                required=["item", "have", "missing", "strategy"],
            ),
            description="What the job needs, item by item, and how each is covered.",
        ),
        assumptions=ArraySchema(
            StringSchema("One short sentence."),
            description=(
                "Optional: the decisions you took on your own (what is deleted, what is "
                "kept, rules for numbers). Approving the plan approves them."
            ),
        ),
        notes=StringSchema(
            "Optional: anything the user should know before approving.", nullable=True
        ),
        required=["title", "steps", "coverage"],
    )
)
class PlanProposeTool(PlanTool):
    """Propone un piano a lista al posto di cominciare il lavoro."""

    @property
    def name(self) -> str:
        return "plan_propose"

    @property
    def description(self) -> str:
        return (
            "Propose a step-by-step plan for a big job in this notebook. The user sees it "
            "as a card with Approve and Review; nothing starts until they approve, and then "
            "the code opens the steps one at a time. A new call replaces a plan that is "
            "still only proposed. Call it once, then end your turn."
        )

    async def execute(
        self,
        title: Any = None,
        steps: Any = None,
        notes: Any = None,
        coverage: Any = None,
        assumptions: Any = None,
        **kwargs: Any,
    ) -> str:
        if self._driver is None:
            return _NO_DRIVER
        return await self._driver.propose(
            self._session_key(),
            title,
            steps,
            notes if isinstance(notes, str) else None,
            coverage=coverage,
            assumptions=assumptions,
        )


@tool_parameters(
    tool_parameters_schema(
        note=StringSchema(
            "One line on what changed in this step, e.g. \"3 pages written, 1 source "
            "discarded\"."
        ),
        required=["note"],
    )
)
class PlanStepDoneTool(PlanTool):
    """Chiude il passo corrente del piano: fatto."""

    @property
    def name(self) -> str:
        return "plan_step_done"

    @property
    def description(self) -> str:
        return (
            "Close the plan step you are working on: it is finished. Call it after the "
            "results of every helper you started for the step are in. The code then "
            "photographs the notebook and opens the next step by itself."
        )

    async def execute(self, note: Any = None, **kwargs: Any) -> str:
        if self._driver is None:
            return _NO_DRIVER
        return await self._driver.step_done(self._session_key(), note)


@tool_parameters(
    tool_parameters_schema(
        reason=StringSchema("Why there is nothing to do in this step, honestly, in one line."),
        required=["reason"],
    )
)
class PlanStepSkipTool(PlanTool):
    """Salta il passo corrente, con il motivo."""

    @property
    def name(self) -> str:
        return "plan_step_skip"

    @property
    def description(self) -> str:
        return (
            "Close the plan step you are working on without doing it, because there is "
            "nothing to do (it was already done, or it no longer makes sense). The reason "
            "is shown to the user."
        )

    async def execute(self, reason: Any = None, **kwargs: Any) -> str:
        if self._driver is None:
            return _NO_DRIVER
        return await self._driver.step_skip(self._session_key(), reason)


@tool_parameters(
    tool_parameters_schema(
        question=StringSchema("The one clear question the user must answer to go on."),
        required=["question"],
    )
)
class PlanBlockedTool(PlanTool):
    """Ferma il piano su una domanda per l'utente."""

    @property
    def name(self) -> str:
        return "plan_blocked"

    @property
    def description(self) -> str:
        return (
            "Stop the plan on the current step because you need the user: a choice, a "
            "missing fact, a permission. The question goes on the plan card; their answer "
            "restarts the step."
        )

    async def execute(self, question: Any = None, **kwargs: Any) -> str:
        if self._driver is None:
            return _NO_DRIVER
        return await self._driver.blocked(self._session_key(), question)


# Registrazione esplicita (``loader.py``).
TOOLS = [PlanProposeTool, PlanStepDoneTool, PlanStepSkipTool, PlanBlockedTool]
