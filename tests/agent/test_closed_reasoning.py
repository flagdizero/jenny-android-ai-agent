"""Il ragionamento dei turni chiusi non riparte, quello del giro in corso si'.

Misurato il 07/10/2026 su ``opencode-go``/``deepseek-v4.1-flash``: con i tool
DeepSeek vuole indietro il ragionamento (400 senza), ma un ``""`` sui turni gia'
chiusi e' accettato e non si paga. Nella sessione ETF del Titan i ragionamenti
erano il 56% del prompt.
"""

from __future__ import annotations

from types import SimpleNamespace

from jenny.agent.context_governor import blank_closed_reasoning, closed_reasoning_can_be_blank
from jenny.providers.openai_compat_provider import OpenAICompatProvider


def _call(cid: str) -> dict:
    return {"id": cid, "type": "function", "function": {"name": "app", "arguments": "{}"}}


HISTORY = [
    {"role": "user", "content": "primo"},
    {"role": "assistant", "content": "", "reasoning_content": "vecchio-1", "tool_calls": [_call("a")]},
    {"role": "tool", "tool_call_id": "a", "content": "ok"},
    {"role": "assistant", "content": "fatto", "reasoning_content": "vecchio-2"},
    {"role": "user", "content": "secondo"},
    {"role": "assistant", "content": "", "reasoning_content": "in-corso-1", "tool_calls": [_call("b")]},
    {"role": "tool", "tool_call_id": "b", "content": "ok"},
    # Un messaggio dell'utente iniettato a meta' giro e' `user` anche lui:
    # non chiude il turno.
    {"role": "user", "content": "[New message] e anche questo"},
    {"role": "assistant", "content": "", "reasoning_content": "in-corso-2", "tool_calls": [_call("c")]},
    {"role": "tool", "tool_call_id": "c", "content": "ok"},
]


def test_closed_turns_lose_their_reasoning_and_the_open_chain_keeps_it() -> None:
    out = blank_closed_reasoning(HISTORY)
    reasoning = [m.get("reasoning_content") for m in out if m["role"] == "assistant"]
    assert reasoning == ["", "", "in-corso-1", "in-corso-2"]


def test_the_session_is_not_touched() -> None:
    blank_closed_reasoning(HISTORY)
    assert HISTORY[1]["reasoning_content"] == "vecchio-1"


def test_without_a_closed_turn_nothing_changes() -> None:
    open_only = HISTORY[4:]
    assert blank_closed_reasoning(open_only) is open_only


def test_only_deepseek_is_blanked() -> None:
    provider = OpenAICompatProvider(api_key="k", api_base="https://opencode.ai/zen/go/v1",
                                    default_model="deepseek-v4.1-flash")
    assert closed_reasoning_can_be_blank(provider, "deepseek-v4.1-flash")
    assert closed_reasoning_can_be_blank(provider, "deepseek/deepseek-chat")
    assert not closed_reasoning_can_be_blank(provider, "kimi-k2.5")
    assert not closed_reasoning_can_be_blank(SimpleNamespace(), "deepseek-v4.1-flash")


def test_the_runner_sends_the_blanked_history() -> None:
    from jenny.agent.runner import AgentRunner

    provider = OpenAICompatProvider(api_key="k", api_base="https://api.deepseek.com",
                                    default_model="deepseek-v4.1-flash")
    runner = AgentRunner(provider)
    spec = SimpleNamespace(model="deepseek-v4.1-flash", session_key="project:x",
                           context_window_tokens=0, context_block_limit=None, max_tokens=None,
                           max_tool_result_chars=16_000, workspace=None,
                           tools=SimpleNamespace(get_definitions=lambda: []))
    sent = runner._govern_context(spec, list(HISTORY), 0)
    assert [m.get("reasoning_content") for m in sent if m["role"] == "assistant"][:2] == ["", ""]
