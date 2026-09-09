"""Tests for the shared openai_responses converters and parsers."""

import json
from unittest.mock import MagicMock, patch

import pytest

from jenny.providers.openai_compat_provider import OpenAICompatProvider
from jenny.providers.openai_responses.converters import (
    convert_messages,
    convert_tools,
    convert_user_message,
    split_tool_call_id,
)
from jenny.providers.openai_responses.parsing import (
    consume_sse_with_reasoning,
    map_finish_reason,
    parse_response_output,
)

# ======================================================================
# converters - split_tool_call_id
# ======================================================================


class TestSplitToolCallId:
    def test_plain_id(self):
        assert split_tool_call_id("call_abc") == ("call_abc", None)

    def test_compound_id(self):
        assert split_tool_call_id("call_abc|fc_1") == ("call_abc", "fc_1")

    def test_compound_empty_item_id(self):
        assert split_tool_call_id("call_abc|") == ("call_abc", None)

    def test_none(self):
        assert split_tool_call_id(None) == ("call_0", None)

    def test_empty_string(self):
        assert split_tool_call_id("") == ("call_0", None)

    def test_non_string(self):
        assert split_tool_call_id(42) == ("call_0", None)


# ======================================================================
# converters - convert_user_message
# ======================================================================


class TestConvertUserMessage:
    def test_string_content(self):
        result = convert_user_message("hello")
        assert result == {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}

    def test_text_block(self):
        result = convert_user_message([{"type": "text", "text": "hi"}])
        assert result["content"] == [{"type": "input_text", "text": "hi"}]

    def test_image_url_block(self):
        result = convert_user_message([
            {"type": "image_url", "image_url": {"url": "https://img.example/a.png"}},
        ])
        assert result["content"] == [
            {"type": "input_image", "image_url": "https://img.example/a.png", "detail": "auto"},
        ]

    def test_mixed_text_and_image(self):
        result = convert_user_message([
            {"type": "text", "text": "what's this?"},
            {"type": "image_url", "image_url": {"url": "https://img.example/b.png"}},
        ])
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "input_text"
        assert result["content"][1]["type"] == "input_image"

    def test_empty_list_falls_back(self):
        result = convert_user_message([])
        assert result["content"] == [{"type": "input_text", "text": ""}]

    def test_none_falls_back(self):
        result = convert_user_message(None)
        assert result["content"] == [{"type": "input_text", "text": ""}]

    def test_image_without_url_skipped(self):
        result = convert_user_message([{"type": "image_url", "image_url": {}}])
        assert result["content"] == [{"type": "input_text", "text": ""}]

    def test_meta_fields_not_leaked(self):
        """_meta on content blocks must never appear in converted output."""
        result = convert_user_message([
            {"type": "text", "text": "hi", "_meta": {"path": "/tmp/x"}},
        ])
        assert "_meta" not in result["content"][0]

    def test_non_dict_items_skipped(self):
        result = convert_user_message(["just a string", 42])
        assert result["content"] == [{"type": "input_text", "text": ""}]


# ======================================================================
# converters - convert_messages
# ======================================================================


class TestConvertMessages:
    def test_system_extracted_as_instructions(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        instructions, items = convert_messages(msgs)
        assert instructions == "You are helpful."
        assert len(items) == 1
        assert items[0]["role"] == "user"

    def test_multiple_system_messages_last_wins(self):
        msgs = [
            {"role": "system", "content": "first"},
            {"role": "system", "content": "second"},
            {"role": "user", "content": "x"},
        ]
        instructions, _ = convert_messages(msgs)
        assert instructions == "second"

    def test_user_message_converted(self):
        _, items = convert_messages([{"role": "user", "content": "hello"}])
        assert items[0]["role"] == "user"
        assert items[0]["content"][0]["type"] == "input_text"

    def test_assistant_text_message(self):
        _, items = convert_messages([
            {"role": "assistant", "content": "I'll help"},
        ])
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "assistant"
        assert items[0]["content"][0]["type"] == "output_text"
        assert items[0]["content"][0]["text"] == "I'll help"

    def test_assistant_empty_content_skipped(self):
        _, items = convert_messages([{"role": "assistant", "content": ""}])
        assert len(items) == 0

    def test_assistant_with_tool_calls(self):
        _, items = convert_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_abc|fc_1",
                "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
            }],
        }])
        assert items[0]["type"] == "function_call"
        assert items[0]["call_id"] == "call_abc"
        assert items[0]["id"] == "fc_1"
        assert items[0]["name"] == "get_weather"
        assert items[0]["arguments"] == '{"city": "SF"}'

    def test_assistant_tool_call_history_repairs_malformed_arguments(self):
        _, items = convert_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_abc|fc_1",
                "function": {"name": "read_file", "arguments": '{path:"foo.txt"}'},
            }],
        }])

        assert json.loads(items[0]["arguments"]) == {"path": "foo.txt"}

    def test_duplicate_response_item_ids_are_made_unique(self):
        """Codex rejects replayed Responses input items with duplicate ids."""
        _, items = convert_messages([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_a|rs_same",
                    "function": {"name": "first", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_a|rs_same", "content": "ok"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_b|rs_same",
                    "function": {"name": "second", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_b|rs_same", "content": "ok"},
        ])
        function_call_ids = [
            item["id"] for item in items if item.get("type") == "function_call"
        ]
        assert function_call_ids == ["rs_same", "rs_same_2"]
        assert len(function_call_ids) == len(set(function_call_ids))

    def test_fallback_response_item_ids_are_unique_with_multiple_tool_calls(self):
        _, items = convert_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_a", "function": {"name": "first", "arguments": "{}"}},
                {"id": "call_b", "function": {"name": "second", "arguments": "{}"}},
            ],
        }])
        function_call_ids = [
            item["id"] for item in items if item.get("type") == "function_call"
        ]
        assert function_call_ids == ["fc_0", "fc_0_2"]
        assert len(function_call_ids) == len(set(function_call_ids))

    def test_assistant_with_tool_calls_no_id(self):
        """Fallback IDs when tool_call.id is missing."""
        _, items = convert_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": "f1", "arguments": "{}"}}],
        }])
        assert items[0]["call_id"] == "call_0"
        assert items[0]["id"].startswith("fc_")

    def test_tool_message(self):
        _, items = convert_messages([{
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": "result text",
        }])
        assert items[0]["type"] == "function_call_output"
        assert items[0]["call_id"] == "call_abc"
        assert items[0]["output"] == "result text"

    def test_tool_message_dict_content(self):
        _, items = convert_messages([{
            "role": "tool",
            "tool_call_id": "call_1",
            "content": {"key": "value"},
        }])
        assert items[0]["output"] == '{"key": "value"}'

    def test_non_standard_keys_not_leaked(self):
        """Extra keys on messages must not appear in converted items."""
        _, items = convert_messages([{
            "role": "user",
            "content": "hi",
            "extra_field": "should vanish",
            "_meta": {"path": "/tmp"},
        }])
        item = items[0]
        assert "extra_field" not in str(item)
        assert "_meta" not in str(item)

    def test_full_conversation_roundtrip(self):
        """System + user + assistant(tool_call) + tool -> correct structure."""
        msgs = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Weather in SF?"},
            {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "c1|fc1",
                    "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"temp":72}'},
        ]
        instructions, items = convert_messages(msgs)
        assert instructions == "Be concise."
        assert len(items) == 3  # user, function_call, function_call_output
        assert items[0]["role"] == "user"
        assert items[1]["type"] == "function_call"
        assert items[2]["type"] == "function_call_output"


# ======================================================================
# converters - tool result image content
# ======================================================================


class TestToolResultImageContent:
    """``read_file`` su un'immagine ritorna blocchi ``image_url``.

    Stessa famiglia del difetto chiuso sul ramo Anthropic (v.
    ``tests/providers/test_anthropic_tool_result.py``): lì i blocchi arrivavano
    non convertiti e l'API li rifiutava, qui ``json.dumps`` riusciva sempre e
    versava il data URI nel prompt come testo, senza errore e senza traccia.

    ``function_call_output.output`` accetta una lista di input part, quindi la
    consegna non è un segnaposto: è l'immagine.
    """

    DATA_URI = "data:image/png;base64,QUJDREVG"
    IMAGE_BLOCKS = [
        {
            "type": "image_url",
            "image_url": {"url": DATA_URI},
            "_meta": {"path": "/w/shot.png"},
        },
        {"type": "text", "text": "(Image file: shot.png)"},
    ]

    @staticmethod
    def _tool_output(content):
        _, items = convert_messages([{
            "role": "tool", "tool_call_id": "c1", "name": "read_file",
            "content": content,
        }])
        assert items[0]["type"] == "function_call_output"
        return items[0]["output"]

    def test_image_block_becomes_a_native_input_image(self):
        """Il difetto: era un data URI dentro una stringa JSON."""
        output = self._tool_output(self.IMAGE_BLOCKS)
        assert isinstance(output, list)
        assert output[0] == {
            "type": "input_image", "image_url": self.DATA_URI, "detail": "auto",
        }

    def test_no_json_scaffolding_reaches_the_model(self):
        """La prova del difetto: nessun blocco serializzato come testo.

        Con il difetto l'output era ``'[{"type": "image_url", ...}]'``, cioè il
        modello leggeva l'impalcatura e il base64 al posto dell'immagine.
        """
        output = self._tool_output(self.IMAGE_BLOCKS)
        rendered = json.dumps(output, ensure_ascii=False)
        assert '"type": "image_url"' not in rendered
        assert '\"type\": \"image_url\"' not in rendered

    def test_sibling_text_block_survives(self):
        """Il testo che accompagna l'immagine resta, come input_text."""
        output = self._tool_output(self.IMAGE_BLOCKS)
        assert {"type": "input_text", "text": "(Image file: shot.png)"} in output

    def test_meta_never_goes_on_the_wire(self):
        """``_meta`` è nostro: l'API non lo conosce e lo rifiuterebbe."""
        output = self._tool_output(self.IMAGE_BLOCKS)
        assert "_meta" not in json.dumps(output, ensure_ascii=False)

    def test_unusable_image_block_is_dropped_not_stringified(self):
        """Un ``image_url`` senza URL non ha nulla da dire: si salta."""
        output = self._tool_output([
            {"type": "image_url", "image_url": {"url": self.DATA_URI}},
            {"type": "image_url", "image_url": {}},
        ])
        assert output == [
            {"type": "input_image", "image_url": self.DATA_URI, "detail": "auto"},
        ]

    def test_unknown_block_is_degraded_to_text_not_lost(self):
        """In un tool result un pezzo sparito in silenzio è peggio di uno grezzo."""
        output = self._tool_output([
            {"type": "image_url", "image_url": {"url": self.DATA_URI}},
            {"type": "resource", "uri": "file:///w/a.bin"},
        ])
        assert output[0]["type"] == "input_image"
        assert output[1]["type"] == "input_text"
        assert "file:///w/a.bin" in output[1]["text"]

    def test_image_only_list_without_url_falls_back_to_json(self):
        """Nessuna part costruibile: si torna al JSON, che qui non porta base64."""
        content = [{"type": "image_url", "image_url": {}}]
        assert self._tool_output(content) == json.dumps(content, ensure_ascii=False)

    def test_text_only_list_keeps_json_behaviour(self):
        """Regressione: senza immagini la serializzazione resta quella di prima."""
        content = [{"type": "text", "text": "riga 1"}, {"type": "text", "text": "riga 2"}]
        assert self._tool_output(content) == json.dumps(content, ensure_ascii=False)

    def test_string_content_untouched(self):
        """Il caso normale — una stringa — non passa da nessuna conversione."""
        assert self._tool_output("plain result") == "plain result"

    def test_dict_content_keeps_json_behaviour(self):
        """Un dict non è una lista di blocchi: resta JSON."""
        content = {"temp": 72}
        assert self._tool_output(content) == json.dumps(content, ensure_ascii=False)


class TestResponsesBodyCarriesToolImages:
    """Le due strade — ``chat`` e ``chat_stream`` — hanno un solo convertitore.

    ``_build_responses_body`` è il punto in cui entrambe passano
    (``openai_compat_provider.py``), quindi provarlo una volta copre lo
    streaming e il non-streaming; il sanitizer dei messaggi sta in mezzo, ed è
    l'altro posto in cui l'immagine potrebbe perdersi.
    """

    @staticmethod
    def _provider():
        p = OpenAICompatProvider.__new__(OpenAICompatProvider)
        p.default_model = "gpt-5"
        p._spec = type("Spec", (), {"name": "openai"})()
        p._effective_base = "https://api.openai.com/v1"
        p.api_base = "https://api.openai.com/v1"
        p._api_type = "responses"
        p._responses_failures = {}
        p._responses_tripped_at = {}
        p._extra_body = {}
        return p

    def test_tool_image_survives_the_whole_body_build(self):
        messages = [
            {"role": "user", "content": "guarda questa"},
            {
                "role": "assistant", "content": None,
                "tool_calls": [
                    {"id": "c1|fc1", "function": {"name": "read_file", "arguments": "{}"}},
                ],
            },
            {
                "role": "tool", "tool_call_id": "c1", "name": "read_file",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,QUJDREVG"},
                        "_meta": {"path": "/w/shot.png"},
                    },
                    {"type": "text", "text": "(Image file: shot.png)"},
                ],
            },
        ]
        body = self._provider()._build_responses_body(
            messages, None, "gpt-5", 100, 0.0, None, None,
        )
        outputs = [i for i in body["input"] if i.get("type") == "function_call_output"]
        output = outputs[0]["output"]
        assert output[0]["type"] == "input_image"
        assert output[0]["image_url"] == "data:image/png;base64,QUJDREVG"
        assert "_meta" not in json.dumps(body["input"], ensure_ascii=False)


# ======================================================================
# converters - convert_tools
# ======================================================================


class TestConvertTools:
    def test_standard_function_tool(self):
        tools = [{"type": "function", "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }}]
        result = convert_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["name"] == "get_weather"
        assert result[0]["description"] == "Get weather"
        assert "properties" in result[0]["parameters"]

    def test_tool_without_name_skipped(self):
        tools = [{"type": "function", "function": {"parameters": {}}}]
        assert convert_tools(tools) == []

    def test_tool_without_function_wrapper(self):
        """Direct dict without type=function wrapper."""
        tools = [{"name": "f1", "description": "d", "parameters": {}}]
        result = convert_tools(tools)
        assert result[0]["name"] == "f1"

    def test_missing_optional_fields_default(self):
        tools = [{"type": "function", "function": {"name": "f"}}]
        result = convert_tools(tools)
        assert result[0]["description"] == ""
        assert result[0]["parameters"] == {}

    def test_multiple_tools(self):
        tools = [
            {"type": "function", "function": {"name": "a", "parameters": {}}},
            {"type": "function", "function": {"name": "b", "parameters": {}}},
        ]
        assert len(convert_tools(tools)) == 2


# ======================================================================
# parsing - map_finish_reason
# ======================================================================


class TestMapFinishReason:
    def test_completed(self):
        assert map_finish_reason("completed") == "stop"

    def test_incomplete(self):
        assert map_finish_reason("incomplete") == "length"

    def test_failed(self):
        assert map_finish_reason("failed") == "error"

    def test_cancelled(self):
        assert map_finish_reason("cancelled") == "error"

    def test_none_defaults_to_stop(self):
        assert map_finish_reason(None) == "stop"

    def test_unknown_defaults_to_stop(self):
        assert map_finish_reason("some_new_status") == "stop"


# ======================================================================
# parsing - parse_response_output
# ======================================================================


class TestParseResponseOutput:
    def test_text_response(self):
        resp = {
            "output": [{"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "Hello!"}]}],
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        result = parse_response_output(resp)
        assert result.content == "Hello!"
        assert result.finish_reason == "stop"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        assert result.tool_calls == []

    def test_tool_call_response(self):
        resp = {
            "output": [{
                "type": "function_call",
                "call_id": "call_1", "id": "fc_1",
                "name": "get_weather",
                "arguments": '{"city": "SF"}',
            }],
            "status": "completed",
            "usage": {},
        }
        result = parse_response_output(resp)
        assert result.content is None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].arguments == {"city": "SF"}
        assert result.tool_calls[0].id == "call_1|fc_1"

    def test_malformed_tool_arguments_logged(self):
        """Malformed JSON arguments should log a warning and remain non-object."""
        resp = {
            "output": [{
                "type": "function_call",
                "call_id": "c1", "id": "fc1",
                "name": "f", "arguments": "{bad json",
            }],
            "status": "completed", "usage": {},
        }
        with patch("jenny.providers.openai_responses.parsing.logger") as mock_logger:
            result = parse_response_output(resp)
        assert result.tool_calls[0].arguments == "{bad json"
        mock_logger.warning.assert_called_once()
        assert "Failed to parse tool call arguments" in str(mock_logger.warning.call_args)

    @pytest.mark.parametrize("arguments", [[], False, 0])
    def test_falsy_non_object_tool_arguments_preserved(self, arguments):
        resp = {
            "output": [{
                "type": "function_call",
                "call_id": "c1",
                "id": "fc1",
                "name": "f",
                "arguments": arguments,
            }],
            "status": "completed",
            "usage": {},
        }

        result = parse_response_output(resp)

        assert result.tool_calls[0].arguments == arguments
        assert type(result.tool_calls[0].arguments) is type(arguments)

    def test_reasoning_content_extracted(self):
        resp = {
            "output": [
                {"type": "reasoning", "summary": [
                    {"type": "summary_text", "text": "I think "},
                    {"type": "summary_text", "text": "therefore I am."},
                ]},
                {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "42"}]},
            ],
            "status": "completed", "usage": {},
        }
        result = parse_response_output(resp)
        assert result.content == "42"
        assert result.reasoning_content == "I think therefore I am."

    def test_empty_output(self):
        resp = {"output": [], "status": "completed", "usage": {}}
        result = parse_response_output(resp)
        assert result.content is None
        assert result.tool_calls == []

    def test_incomplete_status(self):
        resp = {"output": [], "status": "incomplete", "usage": {}}
        result = parse_response_output(resp)
        assert result.finish_reason == "length"

    def test_sdk_model_object(self):
        """parse_response_output should handle SDK objects with model_dump()."""
        mock = MagicMock()
        mock.model_dump.return_value = {
            "output": [{"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "sdk"}]}],
            "status": "completed",
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        }
        result = parse_response_output(mock)
        assert result.content == "sdk"
        assert result.usage["prompt_tokens"] == 1

    def test_usage_maps_responses_api_keys(self):
        """Responses API uses input_tokens/output_tokens, not prompt_tokens/completion_tokens."""
        resp = {
            "output": [],
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }
        result = parse_response_output(resp)
        assert result.usage["prompt_tokens"] == 100
        assert result.usage["completion_tokens"] == 50
        assert result.usage["total_tokens"] == 150


# ======================================================================
# parsing - consume_sse
# ======================================================================


class _SseResponse:
    def __init__(self, events: list[dict]):
        self._events = events

    async def aiter_lines(self):
        for event in self._events:
            yield f"data: {json.dumps(event)}"
            yield ""


class TestConsumeSse:
    @pytest.mark.asyncio
    async def test_reasoning_summary_delta_extracted(self):
        response = _SseResponse([
            {"type": "response.reasoning_summary_text.delta", "delta": "thinking "},
            {"type": "response.reasoning_summary_text.delta", "delta": "briefly"},
            {"type": "response.output_text.delta", "delta": "answer"},
            {"type": "response.completed", "response": {"status": "completed"}},
        ])

        content, tool_calls, finish_reason, usage, reasoning = await consume_sse_with_reasoning(
            response,
        )

        assert content == "answer"
        assert tool_calls == []
        assert finish_reason == "stop"
        assert usage == {}
        assert reasoning == "thinking briefly"

    @pytest.mark.asyncio
    async def test_reasoning_summary_from_completed_response(self):
        response = _SseResponse([
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {"type": "reasoning", "summary": [
                            {"type": "summary_text", "text": "cached "},
                            {"type": "summary_text", "text": "summary"},
                        ]},
                    ],
                },
            },
        ])

        _, _, _, _, reasoning = await consume_sse_with_reasoning(response)

        assert reasoning == "cached summary"

    @pytest.mark.asyncio
    async def test_reasoning_summary_from_done_item(self):
        response = _SseResponse([
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "done summary"}],
                },
            },
            {"type": "response.completed", "response": {"status": "completed", "output": []}},
        ])

        _, _, _, _, reasoning = await consume_sse_with_reasoning(response)

        assert reasoning == "done summary"

    @pytest.mark.asyncio
    async def test_reasoning_summary_part_done_extracted(self):
        response = _SseResponse([
            {
                "type": "response.reasoning_summary_part.done",
                "part": {"type": "summary_text", "text": "part summary"},
            },
            {"type": "response.completed", "response": {"status": "completed"}},
        ])

        _, _, _, _, reasoning = await consume_sse_with_reasoning(response)

        assert reasoning == "part summary"

    @pytest.mark.asyncio
    async def test_raw_sse_usage_extracted(self):
        response = _SseResponse([
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
            },
        ])

        _, _, _, usage, _ = await consume_sse_with_reasoning(response)

        assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    @pytest.mark.asyncio
    async def test_tool_call_done_arguments_callback(self):
        response = _SseResponse([
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "call_id": "c1",
                    "id": "fc1",
                    "name": "write_file",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.done",
                "call_id": "c1",
                "arguments": '{"path":"a.txt","content":"hello\\n"}',
            },
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "c1",
                    "id": "fc1",
                    "name": "write_file",
                    "arguments": '{"path":"a.txt","content":"hello\\n"}',
                },
            },
            {"type": "response.completed", "response": {"status": "completed"}},
        ])
        deltas: list[dict] = []

        async def cb(delta: dict) -> None:
            deltas.append(delta)

        await consume_sse_with_reasoning(response, on_tool_call_delta=cb)

        assert deltas == [
            {"call_id": "c1", "name": "write_file", "arguments_delta": ""},
            {
                "call_id": "c1",
                "name": "write_file",
                "arguments": '{"path":"a.txt","content":"hello\\n"}',
            },
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("arguments", [[], False, 0])
    async def test_falsy_non_object_tool_arguments_preserved(self, arguments):
        response = _SseResponse([
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "call_id": "c1",
                    "id": "fc1",
                    "name": "f",
                    "arguments": "",
                },
            },
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "c1",
                    "id": "fc1",
                    "name": "f",
                    "arguments": arguments,
                },
            },
            {"type": "response.completed", "response": {"status": "completed"}},
        ])

        _, tool_calls, _, _, _ = await consume_sse_with_reasoning(response)

        assert tool_calls[0].arguments == arguments
        assert type(tool_calls[0].arguments) is type(arguments)


class TestCachedTokensOnTheResponsesPath:
    """Il risparmio da cache non deve risultare zero su questo endpoint.

    L'API Responses riporta i token in cache sotto
    ``usage.input_tokens_details.cached_tokens``; il normalizzatore non lo
    leggeva, quindi ogni turno su /responses dichiarava zero cache — mentre il
    ramo Chat-Completions la normalizza da tre posti diversi.
    """

    @staticmethod
    def _usage(usage: dict) -> dict:
        return parse_response_output(
            {"output": [], "status": "completed", "usage": usage}
        ).usage

    def test_input_tokens_details_is_read(self):
        usage = self._usage({
            "input_tokens": 1200, "output_tokens": 80, "total_tokens": 1280,
            "input_tokens_details": {"cached_tokens": 1024},
        })
        assert usage["cached_tokens"] == 1024
        assert usage["prompt_tokens"] == 1200

    def test_a_compatible_gateway_may_use_the_chat_completions_name(self):
        usage = self._usage({
            "input_tokens": 500, "output_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 384},
        })
        assert usage["cached_tokens"] == 384

    def test_a_top_level_field_is_read_too(self):
        usage = self._usage({"input_tokens": 300, "output_tokens": 10, "cached_tokens": 256})
        assert usage["cached_tokens"] == 256

    def test_no_cache_leaves_the_key_out(self):
        """Assente e zero sono la stessa cosa: la chiave non compare.

        Lo stesso fa ``_extract_usage`` sul ramo compat, e la contabilità a
        valle distingue "nessun dato" da "zero risparmio" sulla presenza.
        """
        assert "cached_tokens" not in self._usage({"input_tokens": 100, "output_tokens": 5})
        assert "cached_tokens" not in self._usage({
            "input_tokens": 100, "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 0},
        })

    def test_nested_details_are_unwrapped_when_they_stay_objects(self):
        """``usage`` e i suoi dettagli possono arrivare come oggetti, non come dict.

        ``parse_response_output`` srotola la risposta con ``model_dump``/``vars``,
        ma quel passaggio non scende nei figli: ``input_tokens_details`` può
        restare un oggetto, e letto come dict darebbe zero cache.
        """
        class Details:
            def __init__(self) -> None:
                self.cached_tokens = 777

        class Usage:
            def __init__(self) -> None:
                self.input_tokens = 900
                self.output_tokens = 30
                self.total_tokens = 930
                self.input_tokens_details = Details()

        class Response:
            def __init__(self) -> None:
                self.output: list = []
                self.status = "completed"
                self.usage = Usage()

        usage = parse_response_output(Response()).usage
        assert usage["cached_tokens"] == 777
        assert usage["prompt_tokens"] == 900
