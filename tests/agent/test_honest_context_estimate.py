"""La stima dei token conta quel che parte davvero.

Sul Titan, il 07/10/2026, il log diceva «50.135/131.072» mentre il provider
contava 165.309 token: la stima guardava solo il testo dei messaggi, e i
ragionamenti (il 56% del prompt) e gli argomenti dei tool non esistevano per
lei. La compattazione non scattava mai.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jenny.agent.usage_accounting import calibrate
from jenny.utils import token_calibration
from jenny.utils.helpers import (
    IMAGE_PROMPT_TOKENS,
    estimate_prompt_tokens,
    estimate_prompt_tokens_chain,
    set_honest_prompt_estimate,
)

REASONING_TURN = [
    {"role": "user", "content": "aggiorna l'app"},
    {"role": "assistant", "content": "", "reasoning_content": "Hmm. " * 8_000,
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "app", "arguments": "{\"x\": \"" + "y" * 8_000 + "\"}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "ok"},
]


def test_reasoning_and_tool_arguments_count() -> None:
    honest = estimate_prompt_tokens(REASONING_TURN)
    assert honest > (40_000 + 8_000) // 4


def test_the_old_estimate_is_still_there_behind_the_switch() -> None:
    try:
        set_honest_prompt_estimate(False)
        assert estimate_prompt_tokens(REASONING_TURN) < 100
    finally:
        set_honest_prompt_estimate(True)


def test_an_image_costs_a_fixed_amount_not_its_base64() -> None:
    message = {"role": "user", "content": [
        {"type": "text", "text": "guarda"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 400_000}}]}
    assert estimate_prompt_tokens([message]) < IMAGE_PROMPT_TOKENS + 50


def test_the_estimate_learns_from_the_provider() -> None:
    """Il provider conta 3,7 caratteri per token, la stima 4: dopo una chiamata
    vera la stima di quel modello si corregge."""
    spec = SimpleNamespace(model="flash", session_key="project:etf",
                           tools=SimpleNamespace(get_definitions=lambda: []))
    raw = estimate_prompt_tokens(REASONING_TURN)
    calibrate(spec, REASONING_TURN, int(raw * 1.08))
    corrected, source = estimate_prompt_tokens_chain(None, "flash", REASONING_TURN)
    assert source == "heuristic" and corrected == pytest.approx(raw * 1.08, rel=0.01)
    assert estimate_prompt_tokens_chain(None, "other-model", REASONING_TURN)[0] == raw
    assert token_calibration.last_real("project:etf") == int(raw * 1.08)


def test_a_wild_measure_is_clamped_and_a_tiny_one_ignored() -> None:
    token_calibration.record("m", real=10_000_000, estimated=10_000)
    assert token_calibration.factor("m") == 2.0
    token_calibration.record("n", real=900, estimated=500)
    assert token_calibration.factor("n") == 1.0


def _reasoning_session(turns: int):
    from jenny.session.manager import Session

    session = Session(key="project:etf")
    for i in range(turns):
        session.messages += [
            {"role": "user", "content": f"domanda {i}"},
            {"role": "assistant", "content": "fatto", "reasoning_content": "Hmm. " * 160},
        ]
    return session


def _consolidator(tmp_path, session):
    from unittest.mock import AsyncMock, MagicMock

    from jenny.agent.memory import Consolidator, MemoryStore

    provider = MagicMock(spec=["chat_with_retry"])
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="- [durable] un fatto"))
    sessions = MagicMock()
    sessions.get_or_create = MagicMock(return_value=session)
    c = Consolidator(
        store=MemoryStore(tmp_path), provider=provider, model="flash", sessions=sessions,
        context_window_tokens=1000, max_completion_tokens=100,
        build_messages=lambda history, **_kw: list(history),
        get_tool_definitions=MagicMock(return_value=[]),
    )
    c._SAFETY_BUFFER = 0
    return c, provider


async def test_a_session_full_of_reasoning_gets_compacted(tmp_path) -> None:
    """Il testo e' poco, i ragionamenti tanti: prima la compattazione restava
    «idle»; ora archivia."""
    session = _reasoning_session(8)
    consolidator, provider = _consolidator(tmp_path, session)
    await consolidator.maybe_consolidate_by_tokens(session)
    assert provider.chat_with_retry.await_count >= 1
    assert session.last_consolidated > 0


async def test_before_a_turn_only_one_summary_runs_inline(tmp_path) -> None:
    session = _reasoning_session(24)
    consolidator, provider = _consolidator(tmp_path, session)
    await consolidator.maybe_consolidate_by_tokens(session, max_rounds=consolidator.INLINE_ROUNDS)
    assert provider.chat_with_retry.await_count == 1
