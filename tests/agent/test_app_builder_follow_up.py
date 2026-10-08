"""Un ritocco a un'app torna al costruttore che la conosce.

Il 08/10/2026 sul Titan sei ritocchi al Portafoglio sono stati sei ``app-builder``
nuovi: ognuno rileggeva la guida da capo, vedeva solo il brief e aggiungeva il suo
pezzo, finche' l'utente ha chiamato il codice «un casino». Ora ``spawn`` manda il
ritocco al costruttore che ha gia' lavorato a quell'app, quando la sua
conversazione si puo' ancora riprendere.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jenny.agent.runner import AgentRunResult, AgentRunSpec
from jenny.agent.tools import app_tool
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.spawn import SpawnTool
from tests.agent.test_subagent_send import _completing_runner, _failing_runner, _manager

KEY = "unified:default"


def _spawn_tool(sm) -> SpawnTool:
    tool = SpawnTool(manager=sm)
    tool.set_context(RequestContext(channel="websocket", chat_id="default", session_key=KEY))
    return tool


async def _settle(sm) -> None:
    await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)


async def _builder_lineage(sm, slug: str = "portafoglio") -> str:
    """Un costruttore che ha finito, e che ha toccato *slug*."""
    before = set(sm._lineages)
    await _spawn_tool(sm).execute(task=f"write `{slug}`", agent_type="app-builder")
    await _settle(sm)
    (lineage,) = set(sm._lineages) - before
    app_tool._BUILDERS[(KEY, slug)] = lineage
    return lineage


async def test_a_follow_up_goes_back_to_the_builder_that_knows_the_app(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    seen = _completing_runner(sm, content="built")
    lineage = await _builder_lineage(sm)

    out = await _spawn_tool(sm).execute(
        task="Nella app `portafoglio` i tasti devono impostare il totale",
        agent_type="app-builder")
    assert out.startswith("Sent to the app-builder that already worked on `portafoglio`"), out
    assert lineage in out
    await _settle(sm)
    # Ripreso con la sua conversazione, non rifatto da capo.
    resumed = seen[-1].initial_messages
    assert resumed[-1]["content"].startswith("Nella app `portafoglio`")
    assert any(m.get("content") == "built" for m in resumed)
    assert list(sm._lineages) == [lineage]


async def test_the_app_named_by_the_parameter_wins(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    _completing_runner(sm)
    await _builder_lineage(sm)
    out = await _spawn_tool(sm).execute(task="i tasti impostano il totale",
                                        agent_type="app-builder", app="portafoglio")
    assert out.startswith("Sent to the app-builder"), out


async def test_another_app_or_two_apps_get_a_new_builder(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    _completing_runner(sm)
    await _builder_lineage(sm, "portafoglio")
    await _builder_lineage(sm, "spesa")
    tool = _spawn_tool(sm)
    for task in ("una app nuova `meteo`", "copia `spesa` dentro `portafoglio`"):
        out = await tool.execute(task=task, agent_type="app-builder")
        assert not out.startswith("Sent to"), out
    # Un'altra conversazione non vede i costruttori di questa.
    other = SpawnTool(manager=sm)
    other.set_context(RequestContext(channel="websocket", chat_id="project:orto",
                                     session_key="project:orto"))
    out = await other.execute(task="ritocca `spesa`", agent_type="app-builder")
    assert not out.startswith("Sent to"), out


async def test_a_builder_that_failed_is_not_resumed(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    _failing_runner(sm)
    lineage = await _builder_lineage(sm)
    assert not sm.continuable(lineage)
    out = await _spawn_tool(sm).execute(task="ritocca `portafoglio`", agent_type="app-builder")
    assert not out.startswith("Sent to"), out


async def test_a_conversation_too_long_starts_a_new_builder(tmp_path: Path) -> None:
    sm = _manager(tmp_path)

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        big = [{"role": "tool", "content": "x" * (sm.CONTINUE_MAX_HISTORY_CHARS + 1)}]
        messages = list(spec.initial_messages) + big + [{"role": "assistant", "content": "ok"}]
        return AgentRunResult(final_content="ok", messages=messages, stop_reason="completed")

    sm.runner.run = _run  # type: ignore[method-assign]
    lineage = await _builder_lineage(sm)
    assert sm._history.load(lineage)
    assert not sm.continuable(lineage)


async def test_a_builder_leaves_its_lineage_on_the_app_it_changes(tmp_path: Path,
                                                                  monkeypatch) -> None:
    from jenny.agent.tools.app_tool import AppBuildTool, builder_for

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: workspace)
    manifest = {"name": "Spesa", "description": "list",
                "actions": [{"name": "add", "description": "Add", "kind": "storage",
                             "op": "append", "collection": "items",
                             "params": {"what": {"type": "string"}}}],
                "summary": {"collection": "items", "view": "list",
                            "columns": {"what": {"label": "What"}}}}
    tool = AppBuildTool(origin_session_key=KEY, lineage_id="lin42")
    await tool.execute(action="draft", slug="spesa", files={"app.json": json.dumps(manifest)})
    assert builder_for(KEY, "spesa") == "lin42"
    # Chi non e' un subagent (nessun lineage) non lascia niente.
    await AppBuildTool(origin_session_key=KEY).execute(action="show", slug="spesa")
    assert builder_for(KEY, "spesa") == "lin42"


def test_the_builder_tools_know_their_lineage(tmp_path: Path) -> None:
    from jenny.agent.agent_types import get_agent_type

    sm = _manager(tmp_path)
    registry = sm._build_tools(agent_type=get_agent_type("app-builder"),
                               origin_session_key=KEY, lineage_id="lin7")
    tool = registry.get("app")
    assert tool is not None and tool._lineage == "lin7"

