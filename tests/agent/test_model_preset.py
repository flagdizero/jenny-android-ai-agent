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


async def _effort_sent(loop) -> object:
    """Il ``reasoning_effort`` che un turno di Jenny manda al provider (assente: ``"-"``)."""
    from unittest.mock import AsyncMock, MagicMock

    from jenny.providers.base import LLMResponse

    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    loop.tools.get_definitions = MagicMock(return_value=[])
    await loop._run_agent_loop([])
    return loop.provider.chat_with_retry.call_args.kwargs.get("reasoning_effort", "-")


async def test_jenny_reasoning_reaches_her_turn(tmp_path):
    """Il suo ragionamento va nella richiesta; senza, decide il provider (per tutti)."""
    low = make_loop(tmp_path, bare=True, patch_deps=True, main_reasoning_effort="low")
    assert await _effort_sent(low) == "low"
    same = make_loop(tmp_path, bare=True, patch_deps=True, main_reasoning_effort=None)
    assert await _effort_sent(same) == "-"


async def test_a_preset_with_its_own_reasoning_wins_over_jennys(tmp_path):
    """«deep» chiede ``high`` a tutti: Jenny non lo smentisce col suo ``low``."""
    loop = make_loop(
        tmp_path, bare=True, model_presets=PRESETS, patch_deps=True, main_reasoning_effort="low",
    )
    await loop.set_model_preset("deep")
    assert await _effort_sent(loop) == "-"
    assert loop.provider.generation.reasoning_effort == "high"
    # «fast» non sceglie il ragionamento: torna quello di Jenny.
    await loop.set_model_preset("fast")
    assert await _effort_sent(loop) == "low"


def test_jennys_reasoning_is_low_by_default_and_null_means_same_as_helpers():
    from jenny.config.schema import Config

    assert Config().agents.defaults.main_reasoning_effort == "low"
    config = Config.model_validate({"agents": {"defaults": {"mainReasoningEffort": None}}})
    assert config.agents.defaults.main_reasoning_effort is None
    assert config.agents.defaults.reasoning_effort == "medium"
