from __future__ import annotations

from typing import Any

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.openai_compat_provider import OpenAICompatProvider


def _openai_tools(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _anthropic_tools(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": f"{name} tool",
            "input_schema": {"type": "object", "properties": {}},
        }
        for name in names
    ]


def _marked_openai_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    if not tools:
        return []
    marked: list[str] = []
    for tool in tools:
        if "cache_control" in tool:
            marked.append((tool.get("function") or {}).get("name", ""))
    return marked


def _marked_anthropic_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    if not tools:
        return []
    return [tool.get("name", "") for tool in tools if "cache_control" in tool]


def test_openai_compat_marks_builtin_boundary_and_tail_tool() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "assistant"},
        {"role": "user", "content": "user"},
    ]
    _, marked_tools = OpenAICompatProvider._apply_cache_control(
        messages,
        _openai_tools("read_file", "write_file", "mcp_fs_ls", "mcp_git_status"),
    )
    assert _marked_openai_tool_names(marked_tools) == ["write_file", "mcp_git_status"]


def test_anthropic_marks_builtin_boundary_and_tail_tool() -> None:
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    _, _, marked_tools = AnthropicProvider._apply_cache_control(
        "system",
        messages,
        _anthropic_tools("read_file", "write_file", "mcp_fs_ls", "mcp_git_status"),
    )
    assert _marked_anthropic_tool_names(marked_tools) == ["write_file", "mcp_git_status"]


def test_openai_compat_marks_only_tail_without_mcp() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "assistant"},
        {"role": "user", "content": "user"},
    ]
    _, marked_tools = OpenAICompatProvider._apply_cache_control(
        messages,
        _openai_tools("read_file", "write_file"),
    )
    assert _marked_openai_tool_names(marked_tools) == ["write_file"]


def _messages_have_cache_control(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    return True
    return False


def _build_kwargs_for(api_base: str, model: str) -> dict[str, Any]:
    provider = OpenAICompatProvider(api_key="k", api_base=api_base, default_model=model)
    return provider._build_kwargs(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "assistant"},
            {"role": "user", "content": "user"},
        ],
        tools=_openai_tools("read_file", "write_file"),
        model=model,
        max_tokens=256,
        temperature=0.5,
        reasoning_effort=None,
        tool_choice=None,
    )


def test_build_kwargs_emits_cache_markers_for_openrouter_anthropic() -> None:
    kwargs = _build_kwargs_for(
        "https://openrouter.ai/api/v1", "anthropic/claude-3.5-sonnet"
    )
    assert _messages_have_cache_control(kwargs["messages"])
    assert _marked_openai_tool_names(kwargs["tools"]) == ["write_file"]


def test_build_kwargs_omits_cache_markers_for_vanilla_openai() -> None:
    kwargs = _build_kwargs_for("https://api.openai.com/v1", "gpt-4o")
    assert not _messages_have_cache_control(kwargs["messages"])
    assert _marked_openai_tool_names(kwargs["tools"]) == []


def test_build_kwargs_omits_cache_markers_for_openrouter_non_anthropic() -> None:
    kwargs = _build_kwargs_for("https://openrouter.ai/api/v1", "openai/gpt-4o")
    assert not _messages_have_cache_control(kwargs["messages"])
    assert _marked_openai_tool_names(kwargs["tools"]) == []


def _marked_message_indices(messages: list[dict[str, Any]]) -> list[int]:
    return [
        i for i, msg in enumerate(messages)
        if isinstance(msg.get("content"), list)
        and any(isinstance(b, dict) and "cache_control" in b for b in msg["content"])
    ]


def _tool_loop_messages() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "result"},
        ]},
    ]


def test_anthropic_marks_the_last_message() -> None:
    """In un ciclo di tool l'ultimo tool_result va in cache subito, non al giro dopo."""
    _, marked, _ = AnthropicProvider._apply_cache_control("system", _tool_loop_messages(), None)
    assert _marked_message_indices(marked) == [2]


def test_anthropic_does_not_mark_an_empty_text() -> None:
    """Un blocco di testo vuoto con cache_control e' un 400 dell'API."""
    _, marked, _ = AnthropicProvider._apply_cache_control(
        "system", [{"role": "user", "content": ""}], None,
    )
    assert _marked_message_indices(marked) == []


def test_openai_compat_marks_system_and_last_message() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "tool_call_id": "t1", "content": "result"},
    ]
    marked, _ = OpenAICompatProvider._apply_cache_control(messages, None)
    assert _marked_message_indices(marked) == [0, 3]
