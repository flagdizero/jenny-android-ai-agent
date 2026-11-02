"""Tests for message_repair.enforce_role_alternation."""

from jenny.providers.base import _SYNTHETIC_USER_CONTENT
from jenny.providers.message_repair import enforce_role_alternation


class TestEnforceRoleAlternation:
    """Verify trailing-assistant removal and consecutive same-role merging."""

    def test_empty_messages(self):
        assert enforce_role_alternation([]) == []

    def test_no_change_needed(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "Bye"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 4
        assert result[-1]["role"] == "user"

    def test_trailing_assistant_removed(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_multiple_trailing_assistants_removed(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "A"},
            {"role": "assistant", "content": "B"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_consecutive_user_messages_merged(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "How are you?"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 1
        assert "Hello" in result[0]["content"]
        assert "How are you?" in result[0]["content"]

    def test_consecutive_assistant_messages_merged(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "assistant", "content": "How can I help?"},
            {"role": "user", "content": "Thanks"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 3
        assert "Hello!" in result[1]["content"]
        assert "How can I help?" in result[1]["content"]

    def test_system_messages_not_merged(self):
        msgs = [
            {"role": "system", "content": "System A"},
            {"role": "system", "content": "System B"},
            {"role": "user", "content": "Hi"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 3
        assert result[0]["content"] == "System A"
        assert result[1]["content"] == "System B"

    def test_tool_messages_not_merged(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result1", "tool_call_id": "1"},
            {"role": "tool", "content": "result2", "tool_call_id": "2"},
            {"role": "user", "content": "Next"},
        ]
        result = enforce_role_alternation(msgs)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 2

    def test_consecutive_assistant_keeps_later_tool_call_message(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Previous reply"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result1", "tool_call_id": "1"},
            {"role": "user", "content": "Next"},
        ]
        result = enforce_role_alternation(msgs)
        assert result[1]["role"] == "assistant"
        assert result[1]["tool_calls"] == [{"id": "1"}]
        assert result[1]["content"] is None
        assert result[2]["role"] == "tool"

    def test_consecutive_assistant_does_not_overwrite_existing_tool_call_message(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "assistant", "content": "Later plain assistant"},
            {"role": "tool", "content": "result1", "tool_call_id": "1"},
            {"role": "user", "content": "Next"},
        ]
        result = enforce_role_alternation(msgs)
        assert result[1]["role"] == "assistant"
        assert result[1]["tool_calls"] == [{"id": "1"}]
        assert result[1]["content"] is None
        assert result[2]["role"] == "tool"

    def test_list_and_string_user_turns_are_joined_not_dropped(self):
        """Due ``user`` di fila, uno a blocchi: si uniscono.

        Prima vinceva l'ultimo e il primo spariva: un messaggio rimasto senza
        risposta (turno fallito) seguito da una foto perdeva il testo, e al
        contrario la foto perdeva la domanda. Il testo diventa un blocco
        ``text`` accanto agli altri.
        """
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
        msgs = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "Messaggio rimasto senza risposta"},
            {"role": "user", "content": [{"type": "text", "text": "guarda"}, image]},
        ]
        result = enforce_role_alternation(msgs)
        assert [m["role"] for m in result] == ["system", "user"]
        assert result[1]["content"] == [
            {"type": "text", "text": "Messaggio rimasto senza risposta"},
            {"type": "text", "text": "guarda"},
            image,
        ]

        reverse = enforce_role_alternation([
            {"role": "user", "content": [{"type": "text", "text": "A"}]},
            {"role": "user", "content": "B"},
        ])
        assert reverse[0]["content"] == [
            {"type": "text", "text": "A"}, {"type": "text", "text": "B"},
        ]
        # L'originale non si tocca.
        assert msgs[2]["content"][0] == {"type": "text", "text": "guarda"}
        assert len(msgs[2]["content"]) == 2

    def test_original_messages_not_mutated(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "World"},
        ]
        original_first = dict(msgs[0])
        enforce_role_alternation(msgs)
        assert msgs[0] == original_first
        assert len(msgs) == 2

    def test_trailing_assistant_recovered_as_user_when_only_system_remains(self):
        """Subagent result injected as assistant message must not be silently dropped.

        When build_messages(current_role="assistant") produces [system, assistant],
        enforce_role_alternation would drop the assistant, leaving only [system].
        Most providers (e.g. Zhipu/GLM error 1214) reject such requests.
        The trailing assistant should be recovered as a user message instead.
        """
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "assistant", "content": "Subagent completed successfully."},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert "Subagent completed successfully." in result[1]["content"]

    def test_recovered_turn_does_not_carry_tool_calls(self):
        """Un turno ``user`` non può portare ``tool_calls``, e quelle sono spaiate.

        Il recupero riscriveva il ruolo e basta: ``tool_calls`` è fra le chiavi
        ammesse e questa normalizzazione gira per ultima, quindi
        ``{"role": "user", "tool_calls": [...]}`` finiva sul filo. Le chiamate
        erano per giunta in coda, cioè senza i loro risultati: spaiate comunque.
        """
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "assistant",
                "content": "Let me look that up.",
                "tool_calls": [{"id": "c1", "function": {"name": "web_search"}}],
            },
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[1]["role"] == "user"
        assert "tool_calls" not in result[1]
        assert "Let me look that up." in result[1]["content"]

    def test_recovered_turn_is_never_left_empty(self):
        """Un assistant con *solo* chiamate resta senza niente da leggere.

        Togliere ``tool_calls`` da ``{"content": None, "tool_calls": [...]}``
        lascerebbe un turno vuoto, invalido quanto quello da cui si scappava.
        """
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "function": {"name": "web_search"}}],
            },
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[1]["role"] == "user"
        assert "tool_calls" not in result[1]
        assert result[1]["content"] == _SYNTHETIC_USER_CONTENT

    def test_trailing_assistant_not_recovered_when_user_message_present(self):
        """Recovery should NOT happen when a user message already exists."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[-1]["role"] == "user"

    def test_trailing_assistant_recovered_with_tool_result_preceding(self):
        """When only [system, tool, assistant] remains, recovery is not needed
        because tool messages are valid non-system content."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "tool", "content": "result", "tool_call_id": "1"},
            {"role": "assistant", "content": "Done."},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[-1]["role"] == "tool"

    def test_only_assistant_messages(self):
        msgs = [
            {"role": "assistant", "content": "A"},
            {"role": "assistant", "content": "B"},
        ]
        result = enforce_role_alternation(msgs)
        assert result == []

    def test_realistic_conversation(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "And 3+3?"},
            {"role": "user", "content": "(please be quick)"},
            {"role": "assistant", "content": "6"},
        ]
        result = enforce_role_alternation(msgs)
        assert len(result) == 4
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "user"
        assert "And 3+3?" in result[3]["content"]
        assert "(please be quick)" in result[3]["content"]

    def test_leading_assistant_after_system_inserts_synthetic_user(self):
        """When the first non-system message is assistant (no tool_calls), a
        synthetic user message is inserted to prevent GLM error 1214."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "previous reply"},
            {"role": "tool", "tool_call_id": "tc_1", "content": "result"},
            {"role": "assistant", "content": "after tool"},
        ]
        result = enforce_role_alternation(msgs)
        non_system = [m for m in result if m["role"] != "system"]
        assert non_system[0]["role"] == "user"
        assert non_system[0]["content"] == _SYNTHETIC_USER_CONTENT
        # The original assistant should follow.
        assert non_system[1]["role"] == "assistant"

    def test_leading_assistant_with_tool_calls_not_patched(self):
        """An assistant message with tool_calls at the start is left as-is
        because tool messages will follow and some providers accept this."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "result"},
        ]
        result = enforce_role_alternation(msgs)
        non_system = [m for m in result if m["role"] != "system"]
        # The assistant has tool_calls so it should NOT be patched.
        assert non_system[0]["role"] == "assistant"
        assert non_system[0].get("tool_calls") is not None

    def test_user_after_system_not_patched(self):
        """Normal system→user sequence is not modified."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = enforce_role_alternation(msgs)
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "hello"


def _warnings_while(fn):
    from loguru import logger as loguru_logger

    records: list[str] = []
    handler = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        result = fn()
    finally:
        loguru_logger.remove(handler)
    return result, records


def test_dropping_a_trailing_assistant_with_text_is_logged():
    """Il 02/10/2026 il risultato di un subagent e' sparito qui in silenzio: un
    testo tolto dalla coda del prompt deve almeno lasciare una riga nel log."""
    msgs = [
        {"role": "user", "content": "is it done?"},
        {"role": "assistant", "content": "[Subagent 'x' completed successfully]"},
    ]
    result, records = _warnings_while(lambda: enforce_role_alternation(msgs))
    assert result[-1]["role"] == "user"
    assert any("trailing assistant" in r for r in records)


def test_recovering_the_only_turn_is_not_logged_as_a_drop():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "assistant", "content": "kept as user"},
    ]
    result, records = _warnings_while(lambda: enforce_role_alternation(msgs))
    assert result[-1]["role"] == "user"
    assert not records


def test_anthropic_logs_the_same_drop():
    from jenny.providers.anthropic_conversion import AnthropicConversionMixin

    msgs = [
        {"role": "user", "content": "is it done?"},
        {"role": "assistant", "content": [{"type": "text", "text": "a result"}]},
    ]
    result, records = _warnings_while(lambda: AnthropicConversionMixin._merge_consecutive(msgs))
    assert result[-1]["role"] == "user"
    assert any("trailing assistant" in r for r in records)
