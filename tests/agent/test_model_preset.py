"""Tests for runtime model preset switching on AgentLoop."""

from __future__ import annotations

import pytest
from support.agent import make_loop

PRESETS = {
    "fast": {
        "label": "Fast",
        "model": "fast-model",
        "max_tokens": 2048,
        "context_window_tokens": 32_768,
        "temperature": 0.3,
        "reasoning_effort": None,
    },
    "deep": {
        "model": "deep-model",
        "context_window_tokens": 262_144,
        "reasoning_effort": "high",
    },
}


async def test_set_model_preset_switches_model_and_window(tmp_path):
    loop = make_loop(tmp_path, model_presets=PRESETS, patch_deps=True)
    await loop.set_model_preset("fast")
    assert loop.model == "fast-model"
    assert loop.context_window_tokens == 32_768
    assert loop.model_preset == "fast"
    assert loop.provider.generation.max_tokens == 2048
    assert loop.provider.generation.temperature == 0.3


async def test_set_model_preset_applies_reasoning_effort(tmp_path):
    loop = make_loop(tmp_path, model_presets=PRESETS, patch_deps=True)
    await loop.set_model_preset("deep")
    assert loop.model == "deep-model"
    assert loop.provider.generation.reasoning_effort == "high"
    await loop.set_model_preset("fast")
    assert loop.provider.generation.reasoning_effort is None


async def test_set_model_preset_unknown_raises_keyerror(tmp_path):
    loop = make_loop(tmp_path, model_presets=PRESETS, patch_deps=True)
    with pytest.raises(KeyError, match="unknown model preset"):
        await loop.set_model_preset("nope")
    assert loop.model_preset is None


def test_model_preset_setter_switches_and_clears(tmp_path):
    loop = make_loop(tmp_path, model_presets=PRESETS, patch_deps=True)
    loop.model_preset = "fast"
    assert loop.model == "fast-model"
    assert loop.model_preset == "fast"
    loop.model_preset = None
    assert loop.model_preset is None
    # model settings stay on the last applied preset values
    assert loop.model == "fast-model"


def test_initial_model_preset_applied_at_startup(tmp_path):
    loop = make_loop(
        tmp_path,
        model_presets=PRESETS,
        patch_deps=True,
        initial_model_preset="deep",
    )
    assert loop.model == "deep-model"
    assert loop.context_window_tokens == 262_144
    assert loop.model_preset == "deep"


def test_initial_model_preset_unknown_falls_back(tmp_path):
    loop = make_loop(
        tmp_path,
        model_presets=PRESETS,
        patch_deps=True,
        initial_model_preset="ghost",
    )
    assert loop.model_preset is None
    assert loop.model == "test-model"


async def _effort_sent(loop, hide: tuple[str, ...] = ("plan_propose",)) -> object:
    """Il ``reasoning_effort`` che un turno di Jenny manda al provider (assente: ``"-"``).

    Il registro del turno e' quello del loop meno *hide*: di default un turno
    normale, che puo' delegare (``spawn``) e non sta scrivendo un piano.
    """
    from unittest.mock import AsyncMock, MagicMock

    from jenny.agent.tools.registry import ToolRegistryView
    from jenny.providers.base import LLMResponse

    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    loop.tools.get_definitions = MagicMock(return_value=[])
    tools = ToolRegistryView(loop.tools, lambda name, _tool: name in hide)
    tools.get_definitions = MagicMock(return_value=[])
    await loop._run_agent_loop([], tools=tools)
    return loop.provider.chat_with_retry.call_args.kwargs.get("reasoning_effort", "-")


def _with_global_effort(loop, effort: str | None):
    loop.provider.generation.reasoning_effort = effort
    return loop


async def test_jenny_reasoning_reaches_her_turn(tmp_path):
    """Il suo ragionamento va nella richiesta; senza, decide il provider (per tutti)."""
    low = make_loop(tmp_path, bare=True, patch_deps=True, main_reasoning_effort="low")
    _with_global_effort(low, "medium")
    assert await _effort_sent(low) == "low"
    same = make_loop(tmp_path, bare=True, patch_deps=True, main_reasoning_effort=None)
    _with_global_effort(same, "medium")
    assert await _effort_sent(same) == "-"


async def test_jennys_reasoning_only_where_she_delegates(tmp_path):
    """Senza ``spawn`` (core mode) il lavoro e' suo, con ``plan_propose`` scrive un piano.

    In entrambi i casi decide il ragionamento di tutti, non il suo ``low``.
    """
    loop = make_loop(tmp_path, bare=True, patch_deps=True, main_reasoning_effort="low")
    _with_global_effort(loop, "medium")
    assert await _effort_sent(loop, hide=("spawn", "plan_propose")) == "-"
    assert await _effort_sent(loop, hide=()) == "-"
    assert await _effort_sent(loop) == "low"


async def test_jennys_reasoning_stays_off_when_everyones_is_off(tmp_path):
    """Ragionamento di tutti spento: ``low`` accenderebbe il thinking a chi l'ha tolto."""
    loop = make_loop(tmp_path, bare=True, patch_deps=True, main_reasoning_effort="low")
    _with_global_effort(loop, None)
    assert await _effort_sent(loop) == "-"


async def test_a_preset_with_its_own_reasoning_wins_over_jennys(tmp_path):
    """«deep» chiede ``high`` a tutti: Jenny non lo smentisce col suo ``low``."""
    loop = make_loop(
        tmp_path, bare=True, model_presets=PRESETS, patch_deps=True, main_reasoning_effort="low",
    )
    _with_global_effort(loop, "medium")
    await loop.set_model_preset("deep")
    assert await _effort_sent(loop) == "-"
    assert loop.provider.generation.reasoning_effort == "high"
    # «fast» lo lascia al provider (``None``), per tutti: e' il ragionamento
    # spento, e Jenny non lo riaccende col suo ``low``.
    await loop.set_model_preset("fast")
    assert loop.provider.generation.reasoning_effort is None
    assert await _effort_sent(loop) == "-"


def test_jennys_reasoning_is_low_by_default_and_null_means_same_as_helpers():
    from jenny.config.schema import Config

    assert Config().agents.defaults.main_reasoning_effort == "low"
    config = Config.model_validate({"agents": {"defaults": {"mainReasoningEffort": None}}})
    assert config.agents.defaults.main_reasoning_effort is None
    assert config.agents.defaults.reasoning_effort == "medium"
