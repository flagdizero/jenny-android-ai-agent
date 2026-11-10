"""Fase 5.1 — Contratto d'errore tool strutturato (`ToolResult`).

Prima (baseline 0.4): il registry decideva con `result.startswith("Error")`,
mis-classificando output legittimi. Ora un tool che ritorna
`ToolResult.success(...)` non è mai mis-classificato; i tool legacy che ritornano
`str` mantengono la vecchia convenzione (retro-compat).
"""

from __future__ import annotations

from typing import Any

from jenny.agent.tools.base import Tool
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.tools.result import ToolResult

_HINT = "[Analyze the error above and try a different approach.]"


class _StructuredEchoTool(Tool):
    """Tool migrato: ritorna ToolResult.success anche se il testo inizia per 'Error'."""

    @property
    def name(self) -> str:
        return "echo_report"

    @property
    def description(self) -> str:
        return "returns a report string that happens to start with 'Error'"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> Any:
        return ToolResult.success("Error budget report: 0 incidents this week — all good.")


class _StructuredFailTool(_StructuredEchoTool):
    @property
    def name(self) -> str:
        return "echo_fail"

    async def execute(self, **kwargs: Any) -> Any:
        return ToolResult.failure("disk is full", code="io")


class _LegacyErrorTool(_StructuredEchoTool):
    @property
    def name(self) -> str:
        return "echo_legacy"

    async def execute(self, **kwargs: Any) -> Any:
        return "Error: legacy convention still classified as failure"


async def test_structured_success_starting_with_error_is_not_misclassified() -> None:
    registry = ToolRegistry()
    registry.register(_StructuredEchoTool())
    result = await registry.execute("echo_report", {})
    assert result.startswith("Error budget report")
    assert _HINT not in result  # niente più hint su un successo legittimo


async def test_structured_failure_gets_hint() -> None:
    registry = ToolRegistry()
    registry.register(_StructuredFailTool())
    result = await registry.execute("echo_fail", {})
    assert "disk is full" in result
    assert _HINT in result


async def test_legacy_error_string_still_treated_as_failure() -> None:
    registry = ToolRegistry()
    registry.register(_LegacyErrorTool())
    result = await registry.execute("echo_legacy", {})
    assert _HINT in result  # retro-compat: convenzione stringly-typed preservata


# ── Sul filo l'errore ha una forma sola ──────────────────────────────────────


def test_a_structured_failure_always_renders_as_an_error_text() -> None:
    """Un fallimento strutturato esce col prefisso anche se il tool non l'ha
    scritto, o ha risposto a blocchi: chi guarda il testo deve vederlo."""
    from jenny.agent.tools.result import is_error_text, render_for_model

    plain = render_for_model(ToolResult.failure("`args` must be an object."))
    assert plain == "Error: `args` must be an object."
    already = render_for_model(ToolResult.failure("Error: no such app"))
    assert already == "Error: no such app"
    blocks = render_for_model(
        ToolResult.failure("could not draw", content=[{"type": "text", "text": "x"}])
    )
    assert blocks == "Error: could not draw"
    assert all(is_error_text(r) for r in (plain, already, blocks))
    assert render_for_model(ToolResult.success([{"type": "text", "text": "x"}])) == [
        {"type": "text", "text": "x"}
    ]


def test_nobody_else_compares_the_prefix_by_hand() -> None:
    """Un ``startswith("Error")`` scritto altrove è una seconda regola su cosa
    sia un errore di tool. Restano fuori due controlli che un tool fa sul
    ritorno di una *sua* funzione, non sull'esito di un tool."""
    import re
    from pathlib import Path

    own_helpers = {"jenny/agent/tools/self.py", "jenny/agent/tools/notebook.py"}
    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"""startswith\(\s*\(?\s*["']Error""")
    found = sorted(
        str(path.relative_to(root))
        for path in (root / "jenny" / "agent").rglob("*.py")
        # ``result.py`` è la casa della regola, e la racconta nella sua docstring.
        if path.name != "result.py" and pattern.search(path.read_text(encoding="utf-8"))
    )
    assert set(found) <= own_helpers, found
