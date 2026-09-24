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
