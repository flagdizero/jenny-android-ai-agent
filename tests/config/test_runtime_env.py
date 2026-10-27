"""Test per jenny.config.runtime_env — layer unico dei knob operativi ``JENNY_*``.

Copre, per ogni knob esposto: default (nessuna env), override valido via env,
valori non parsabili (fallback silenzioso al default, loggato come warning) e
il parsing dei tipi (float vs int).
"""

from __future__ import annotations

import pytest

from jenny.config import runtime_env

# ---------------------------------------------------------------------------
# max_concurrent_requests — JENNY_MAX_CONCURRENT_REQUESTS (int, default 3)
# ---------------------------------------------------------------------------


def test_max_concurrent_requests_default_without_env(monkeypatch):
    monkeypatch.delenv("JENNY_MAX_CONCURRENT_REQUESTS", raising=False)
    assert runtime_env.max_concurrent_requests() == 3


def test_max_concurrent_requests_custom_default_param(monkeypatch):
    monkeypatch.delenv("JENNY_MAX_CONCURRENT_REQUESTS", raising=False)
    assert runtime_env.max_concurrent_requests(default=7) == 7


def test_max_concurrent_requests_env_override(monkeypatch):
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "10")
    assert runtime_env.max_concurrent_requests() == 10


def test_max_concurrent_requests_env_allows_non_positive():
    """``<= 0`` è un valore valido (significa "illimitato", decide il chiamante)."""
    import os

    os.environ["JENNY_MAX_CONCURRENT_REQUESTS"] = "0"
    try:
        assert runtime_env.max_concurrent_requests() == 0
    finally:
        del os.environ["JENNY_MAX_CONCURRENT_REQUESTS"]


def test_max_concurrent_requests_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "not-a-number")
    assert runtime_env.max_concurrent_requests() == 3


def test_max_concurrent_requests_float_string_is_invalid_for_int(monkeypatch):
    """``int("3.5")`` solleva ValueError: deve ricadere sul default, non troncare."""
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "3.5")
    assert runtime_env.max_concurrent_requests() == 3


def test_max_concurrent_requests_empty_string_uses_default(monkeypatch):
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "")
    assert runtime_env.max_concurrent_requests() == 3


def test_max_concurrent_requests_whitespace_only_uses_default(monkeypatch):
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "   ")
    assert runtime_env.max_concurrent_requests() == 3


def test_max_concurrent_requests_accepts_negative(monkeypatch):
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "-1")
    assert runtime_env.max_concurrent_requests() == -1


# ---------------------------------------------------------------------------
# llm_timeout_s — JENNY_LLM_TIMEOUT_S (float, default 300.0)
# ---------------------------------------------------------------------------


def test_llm_timeout_default_without_env(monkeypatch):
    monkeypatch.delenv("JENNY_LLM_TIMEOUT_S", raising=False)
    assert runtime_env.llm_timeout_s() == 300.0


def test_llm_timeout_env_override_float(monkeypatch):
    monkeypatch.setenv("JENNY_LLM_TIMEOUT_S", "45.5")
    assert runtime_env.llm_timeout_s() == 45.5


def test_llm_timeout_env_override_integer_string_parses_as_float(monkeypatch):
    monkeypatch.setenv("JENNY_LLM_TIMEOUT_S", "60")
    value = runtime_env.llm_timeout_s()
    assert value == 60.0
    assert isinstance(value, float)


def test_llm_timeout_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("JENNY_LLM_TIMEOUT_S", "abc")
    assert runtime_env.llm_timeout_s() == 300.0


def test_llm_timeout_disabled_via_non_positive(monkeypatch):
    """``<= 0`` disabilita il timeout (decisione del chiamante, non del layer)."""
    monkeypatch.setenv("JENNY_LLM_TIMEOUT_S", "0")
    assert runtime_env.llm_timeout_s() == 0.0


# ---------------------------------------------------------------------------
# ws_send_timeout_s — JENNY_WS_SEND_TIMEOUT_S (float, default 12.0)
# ---------------------------------------------------------------------------


def test_ws_send_timeout_default_without_env(monkeypatch):
    monkeypatch.delenv("JENNY_WS_SEND_TIMEOUT_S", raising=False)
    assert runtime_env.ws_send_timeout_s() == 12.0


def test_ws_send_timeout_env_override(monkeypatch):
    monkeypatch.setenv("JENNY_WS_SEND_TIMEOUT_S", "5")
    assert runtime_env.ws_send_timeout_s() == 5.0


def test_ws_send_timeout_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("JENNY_WS_SEND_TIMEOUT_S", "twelve")
    assert runtime_env.ws_send_timeout_s() == 12.0


# ---------------------------------------------------------------------------
# goal_inactivity_ttl_h — JENNY_GOAL_INACTIVITY_TTL_H (float, default 12.0)
# ---------------------------------------------------------------------------


def test_goal_inactivity_ttl_default_without_env(monkeypatch):
    monkeypatch.delenv("JENNY_GOAL_INACTIVITY_TTL_H", raising=False)
    assert runtime_env.goal_inactivity_ttl_h() == 12.0


def test_goal_inactivity_ttl_env_override(monkeypatch):
    monkeypatch.setenv("JENNY_GOAL_INACTIVITY_TTL_H", "24.5")
    assert runtime_env.goal_inactivity_ttl_h() == 24.5


def test_goal_inactivity_ttl_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("JENNY_GOAL_INACTIVITY_TTL_H", "")
    assert runtime_env.goal_inactivity_ttl_h() == 12.0


# ---------------------------------------------------------------------------
# tool_timeout_s — JENNY_TOOL_TIMEOUT_S (float, default 300.0)
# ---------------------------------------------------------------------------


def test_tool_timeout_default_without_env(monkeypatch):
    monkeypatch.delenv("JENNY_TOOL_TIMEOUT_S", raising=False)
    assert runtime_env.tool_timeout_s() == 300.0


def test_tool_timeout_env_override(monkeypatch):
    monkeypatch.setenv("JENNY_TOOL_TIMEOUT_S", "90")
    assert runtime_env.tool_timeout_s() == 90.0


def test_tool_timeout_disabled_via_non_positive(monkeypatch):
    monkeypatch.setenv("JENNY_TOOL_TIMEOUT_S", "-5")
    assert runtime_env.tool_timeout_s() == -5.0


def test_tool_timeout_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("JENNY_TOOL_TIMEOUT_S", "null")
    assert runtime_env.tool_timeout_s() == 300.0


# ---------------------------------------------------------------------------
# Warning loggato sul fallback (comportamento osservabile, non solo il valore)
# ---------------------------------------------------------------------------


def test_invalid_int_env_logs_warning(monkeypatch):
    """Il fallback su valore non valido deve essere osservabile via log (loguru)."""
    from loguru import logger as loguru_logger

    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "nope")
    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        runtime_env.max_concurrent_requests()
    finally:
        loguru_logger.remove(handler_id)

    assert any("JENNY_MAX_CONCURRENT_REQUESTS" in r and "nope" in r for r in records)


def test_invalid_float_env_logs_warning(monkeypatch):
    from loguru import logger as loguru_logger

    monkeypatch.setenv("JENNY_LLM_TIMEOUT_S", "nope")
    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        runtime_env.llm_timeout_s()
    finally:
        loguru_logger.remove(handler_id)

    assert any("JENNY_LLM_TIMEOUT_S" in r and "nope" in r for r in records)


_KNOBS = [
    ("max_concurrent_requests", "JENNY_MAX_CONCURRENT_REQUESTS", 3),
    ("llm_timeout_s", "JENNY_LLM_TIMEOUT_S", 300.0),
    ("ws_send_timeout_s", "JENNY_WS_SEND_TIMEOUT_S", 12.0),
    ("goal_inactivity_ttl_h", "JENNY_GOAL_INACTIVITY_TTL_H", 12.0),
    ("tool_timeout_s", "JENNY_TOOL_TIMEOUT_S", 300.0),
]


@pytest.mark.parametrize("func_name,env_name,default", _KNOBS)
def test_each_knob_documents_its_own_env_var_name(monkeypatch, func_name, env_name, default):
    """Guard di coerenza: ogni knob risponde solo al proprio env var, non ad altri.

    Con tutte le altre variabili impostate resta al default; con la sua cambia.
    """
    for _, other, _ in _KNOBS:
        if other != env_name:
            monkeypatch.setenv(other, "7")
    monkeypatch.delenv(env_name, raising=False)
    func = getattr(runtime_env, func_name)
    assert func() == default
    monkeypatch.setenv(env_name, "7")
    assert func() == 7


# ---------------------------------------------------------------------------
# Knob dove zero NON vuol dire "disabilitato"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["0", "-1", "-0.5"])
def test_ws_send_timeout_rejects_non_positive(monkeypatch, bad):
    """Zero qui non è permissivo: ``wait_for(timeout=0)`` fa fallire ogni send.

    Il risultato sarebbe scollegare tutti i client, non "nessun limite".
    """
    monkeypatch.setenv("JENNY_WS_SEND_TIMEOUT_S", bad)

    assert runtime_env.ws_send_timeout_s() == 12.0


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_goal_ttl_rejects_non_positive(monkeypatch, bad):
    """Un TTL a zero non è "non scadere mai": scade ogni goal al primo turno."""
    monkeypatch.setenv("JENNY_GOAL_INACTIVITY_TTL_H", bad)

    assert runtime_env.goal_inactivity_ttl_h() == 12.0


def test_a_positive_value_still_wins_on_both(monkeypatch):
    monkeypatch.setenv("JENNY_WS_SEND_TIMEOUT_S", "30")
    monkeypatch.setenv("JENNY_GOAL_INACTIVITY_TTL_H", "48")

    assert runtime_env.ws_send_timeout_s() == 30.0
    assert runtime_env.goal_inactivity_ttl_h() == 48.0


def test_zero_still_means_disabled_where_it_is_a_choice(monkeypatch):
    """La distinzione è di significato: qui lo zero è documentato e va preservato."""
    monkeypatch.setenv("JENNY_LLM_TIMEOUT_S", "0")
    monkeypatch.setenv("JENNY_TOOL_TIMEOUT_S", "0")
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "0")

    assert runtime_env.llm_timeout_s() == 0.0
    assert runtime_env.tool_timeout_s() == 0.0
    assert runtime_env.max_concurrent_requests() == 0


def test_non_positive_ws_timeout_logs_the_fallback(monkeypatch):
    from loguru import logger as loguru_logger

    monkeypatch.setenv("JENNY_WS_SEND_TIMEOUT_S", "0")
    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        runtime_env.ws_send_timeout_s()
    finally:
        loguru_logger.remove(handler_id)

    assert any("JENNY_WS_SEND_TIMEOUT_S" in r and "non-positive" in r for r in records)
