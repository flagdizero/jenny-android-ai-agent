"""Il parser SSE di Chat Completions e quello della Responses API sono lo stesso.

Erano due copie (``OpenAICompatProvider._iter_chat_completion_sse`` e
``openai_responses.parsing.iter_sse``) che differivano solo nel testo del log.
Qui si fissa che, sugli stessi flussi, danno gli stessi eventi — compresi i
casi di bordo: righe ``data:`` multiple, ``[DONE]``, JSON rotto, commenti,
righe ``event:``, fine flusso senza la riga vuota finale.
"""

from __future__ import annotations

import pytest

from jenny.providers.openai_compat_provider import OpenAICompatProvider
from jenny.providers.openai_responses.parsing import iter_sse


class _Lines:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


STREAMS = {
    "one_event": ['data: {"a": 1}', ""],
    "two_events": ['data: {"a": 1}', "", 'data: {"b": 2}', ""],
    "multi_line_data": ['data: {"a":', "data: 1}", ""],
    "done_marker": ['data: {"a": 1}', "", "data: [DONE]", ""],
    "broken_json": ["data: {nope", "", 'data: {"ok": true}', ""],
    "comment_and_event": [": ping", "event: message", 'data: {"x": "y"}', ""],
    "no_trailing_blank": ['data: {"a": 1}', "", 'data: {"last": 1}'],
    "blank_lines_only": ["", "", ""],
    "empty": [],
}


async def _collect(gen) -> list:
    return [event async for event in gen]


@pytest.mark.parametrize("name", sorted(STREAMS))
async def test_both_parsers_yield_the_same_events(name: str) -> None:
    lines = STREAMS[name]
    compat = await _collect(OpenAICompatProvider._iter_chat_completion_sse(_Lines(lines)))
    responses = await _collect(iter_sse(_Lines(lines)))
    assert compat == responses


async def test_the_edge_cases_mean_what_they_should() -> None:
    assert await _collect(iter_sse(_Lines(STREAMS["multi_line_data"]))) == [{"a": 1}]
    assert await _collect(iter_sse(_Lines(STREAMS["done_marker"]))) == [{"a": 1}]
    assert await _collect(iter_sse(_Lines(STREAMS["broken_json"]))) == [{"ok": True}]
    assert await _collect(iter_sse(_Lines(STREAMS["no_trailing_blank"]))) == [{"a": 1}, {"last": 1}]
    assert await _collect(iter_sse(_Lines(STREAMS["comment_and_event"]))) == [{"x": "y"}]
