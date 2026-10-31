"""Il prompt di un aiutante dice il suo budget e che le letture vanno a gruppi.

Prova del 24/10/2026: nessun prompt diceva a un aiutante che aveva un budget, e
quello della bacheca ha chiesto 39 file uno per risposta. Il numero e quello che
il run usera davvero: il tetto del tipo, stretto da quello di config.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from jenny.agent.agent_types import get_agent_type
from jenny.agent.subagent import SubagentManager
from jenny.bus.queue import MessageBus

PARALLEL = "When you need several files, ask for all of them in the same response: they run together."


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    return SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(), model="m",
        max_tool_result_chars=16_000, stall_threshold_s=0.0, **kw,
    )


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_prompt_states_the_type_budget(tmp_path: Path) -> None:
    sm = _manager(tmp_path, max_iterations=200)
    writer = get_agent_type("writer")

    prompt = _flat(sm._build_subagent_prompt(agent_type=writer))

    assert writer.max_iterations == 40
    assert "Your budget is 40 model calls." in prompt
    assert PARALLEL in prompt


def test_the_config_ceiling_narrows_the_stated_budget(tmp_path: Path) -> None:
    sm = _manager(tmp_path, max_iterations=12)

    prompt = _flat(sm._build_subagent_prompt(agent_type=get_agent_type("writer")))

    assert "Your budget is 12 model calls." in prompt


def test_without_a_type_the_config_budget_is_stated(tmp_path: Path) -> None:
    sm = _manager(tmp_path, max_iterations=25)
    assert "Your budget is 25 model calls." in _flat(sm._build_subagent_prompt())


SAVE = "Save what the task asks you to produce (pages, sources) as you go;"


def test_a_writable_helper_saves_what_the_task_asks_and_reports(tmp_path: Path) -> None:
    """«Salva il lavoro su file» spingeva a scrivere rapporti in file (prova 2:
    ``outputs/`` nel quaderno): si salva cio' che il compito chiede, il resoconto
    va nella risposta."""
    sm = _manager(tmp_path, max_iterations=25)
    prompt = _flat(sm._build_subagent_prompt())
    assert SAVE in prompt
    assert "end with a report of what is done and what remains." in prompt
    assert "Save your work to files" not in prompt


def test_a_readonly_helper_is_not_told_to_save(tmp_path: Path) -> None:
    from jenny.security.workspace_access import build_workspace_scope

    sm = _manager(tmp_path, max_iterations=25)
    scope = build_workspace_scope(tmp_path, "restricted").without_write_access()
    prompt = _flat(sm._build_subagent_prompt(workspace_scope=scope))
    assert "Your budget is 25 model calls." in prompt
    assert SAVE not in prompt
