"""La forma del thinking dipende dal modello.

Dal 4.7 in poi ``{"type": "enabled", "budget_tokens": N}`` e ``temperature`` sono
un 400: ``claude-haiku-5-5`` su OpenCode Go rifiutava cosi' ogni richiesta con
effort ``medium``, il default. Quei modelli vogliono ``{"type": "adaptive"}`` e la
profondita' in ``output_config.effort``.
"""

from __future__ import annotations

import pytest

from jenny.providers.anthropic_provider import AnthropicProvider, _thinking_style

MESSAGES = [{"role": "user", "content": "ciao"}]


def _kwargs(model: str, effort: str | None, temperature: float = 0.1) -> dict:
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    return provider._build_kwargs(MESSAGES, None, model, 4096, temperature, effort, None)


@pytest.mark.parametrize(("model", "style"), [
    ("claude-haiku-5-5", "adaptive"),
    ("claude-opus-5-5", "adaptive"),
    ("claude-sonnet-5", "adaptive"),
    ("claude-opus-4-7", "adaptive"),
    ("claude-opus-4-8", "adaptive"),
    ("claude-fable-5-1", "adaptive"),
    ("claude-mythos-5-1", "adaptive"),
    ("claude-opus-4-6", "budget"),
    ("claude-sonnet-4-6", "budget"),
    ("claude-haiku-4-5", "budget"),
    ("claude-opus-4-5-20251101", "budget"),
    ("claude-opus-4-20250514", "budget"),
    ("claude-3-5-sonnet-20241022", "budget"),
    ("minimax-m3", "budget"),
    ("qwen3.8-max", "budget"),
])
def test_style_by_model(model: str, style: str) -> None:
    assert _thinking_style(model) == style


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_new_models_get_adaptive_thinking_and_effort_without_temperature(effort: str) -> None:
    kwargs = _kwargs("claude-haiku-5-5", effort)
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": effort}
    assert "temperature" not in kwargs
    assert kwargs["max_tokens"] == 4096


def test_adaptive_effort_leaves_the_level_to_the_model() -> None:
    kwargs = _kwargs("claude-opus-5-5", "adaptive")
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert "output_config" not in kwargs
    assert "temperature" not in kwargs


@pytest.mark.parametrize("effort", [None, "none", "minimal"])
def test_new_models_without_thinking_send_neither_thinking_nor_temperature(effort) -> None:
    kwargs = _kwargs("claude-sonnet-5-5", effort)
    assert "thinking" not in kwargs
    assert "output_config" not in kwargs
    assert "temperature" not in kwargs


def test_older_and_unknown_models_keep_the_budget() -> None:
    for model in ("claude-sonnet-4-6", "claude-haiku-4-5", "minimax-m3"):
        kwargs = _kwargs(model, "medium")
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        assert kwargs["temperature"] == 1.0
        assert "output_config" not in kwargs
