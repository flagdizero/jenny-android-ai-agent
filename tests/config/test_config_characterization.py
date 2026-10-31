"""Knob runtime via env-var e risoluzione dello schema.

Coperti qui (pure/schema):
- `JENNY_STREAM_IDLE_TIMEOUT_S` via `resolve_stream_idle_timeout_s`, che sta
  nel layer unico dei knob (`config/runtime_env.py`).
- Lo schema `Config` si istanzia coi sub-config dei tool gia' risolti, senza
  `model_rebuild`.

Il knob `JENNY_MAX_CONCURRENT_REQUESTS` (che richiede un AgentLoop, e quindi
la fixture `loop_factory` disponibile solo in tests/agent/) e' provato in
`tests/agent/test_env_knobs.py`. Il file nacque come «caratterizzazione» prima
della Fase 3, quando questi knob si leggevano direttamente da `os.environ`.
"""

from __future__ import annotations

# Le manopole di streaming stanno nel layer ``config/runtime_env.py`` insieme a
# tutti gli altri knob ``JENNY_*``: ``providers/base.py`` ne ri-implementava il
# parsing, e il posto in cui si viene a sapere quali knob esistono è uno solo.
from jenny.config.runtime_env import (
    DEFAULT_FIRST_OUTPUT_TIMEOUT_S,
    DEFAULT_LOCAL_FIRST_OUTPUT_TIMEOUT_S,
    DEFAULT_STREAM_IDLE_TIMEOUT_S,
    MAX_STREAM_IDLE_TIMEOUT_S,
    resolve_first_output_timeout_s,
    resolve_stream_idle_timeout_s,
)


def test_stream_idle_timeout_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_STREAM_IDLE_TIMEOUT_S", "12.5")
    assert resolve_stream_idle_timeout_s() == 12.5


def test_stream_idle_timeout_defaults_and_clamps() -> None:
    assert resolve_stream_idle_timeout_s(env_value=None) == DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert resolve_stream_idle_timeout_s(env_value="") == DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert resolve_stream_idle_timeout_s(env_value="-1") == DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert resolve_stream_idle_timeout_s(env_value="999999") == MAX_STREAM_IDLE_TIMEOUT_S


def test_first_output_timeout_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S", "42")
    assert resolve_first_output_timeout_s() == 42.0
    assert resolve_first_output_timeout_s(local=True) == 42.0


def test_first_output_timeout_defaults_and_clamps() -> None:
    assert resolve_first_output_timeout_s(env_value=None) == DEFAULT_FIRST_OUTPUT_TIMEOUT_S
    assert (
        resolve_first_output_timeout_s(local=True, env_value=None)
        == DEFAULT_LOCAL_FIRST_OUTPUT_TIMEOUT_S
    )
    assert resolve_first_output_timeout_s(env_value="-1") == DEFAULT_FIRST_OUTPUT_TIMEOUT_S
    assert resolve_first_output_timeout_s(env_value="999999") == MAX_STREAM_IDLE_TIMEOUT_S


def test_first_output_budget_is_longer_than_the_inter_chunk_idle() -> None:
    """Il silenzio prima del primo token non va misurato come uno stallo."""
    assert DEFAULT_FIRST_OUTPUT_TIMEOUT_S > DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert DEFAULT_LOCAL_FIRST_OUTPUT_TIMEOUT_S > DEFAULT_FIRST_OUTPUT_TIMEOUT_S


def test_config_schema_resolves_tool_subconfigs() -> None:
    """La dance model_rebuild produce un Config con i sub-config dei tool risolti."""
    from jenny.config.schema import Config

    cfg = Config()
    assert cfg.tools is not None
    # I sub-config dei tool sono modelli concreti (non ForwardRef irrisolti).
    assert cfg.tools.model_dump() is not None
