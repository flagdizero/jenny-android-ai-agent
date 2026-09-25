"""Il parser SSE di Chat Completions e quello della Responses API sono lo stesso.

Erano due copie (``OpenAICompatProvider._iter_chat_completion_sse`` e
``openai_responses.parsing.iter_sse``) che differivano solo nel testo del log.
Qui si fissa che, sugli stessi flussi, danno gli eventi giusti — compresi i
casi di bordo: righe ``data:`` multiple, ``[DONE]``, JSON rotto, commenti,
righe ``event:``, fine flusso senza la riga vuota finale.

Gli eventi attesi sono scritti a mano, flusso per flusso. Confrontare i due
parser fra loro non bastava: il metodo di Chat Completions oggi *delega* a
``iter_sse``, quindi il confronto era vero per costruzione — anche con un
parser che sbagliasse allo stesso modo in entrambi.
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


STREAMS: dict[str, tuple[list[str], list[dict]]] = {
    "one_event": (['data: {"a": 1}', ""], [{"a": 1}]),
    "two_events": (['data: {"a": 1}', "", 'data: {"b": 2}', ""], [{"a": 1}, {"b": 2}]),
    "multi_line_data": (['data: {"a":', "data: 1}", ""], [{"a": 1}]),
    "done_marker": (['data: {"a": 1}', "", "data: [DONE]", ""], [{"a": 1}]),
    "broken_json": (["data: {nope", "", 'data: {"ok": true}', ""], [{"ok": True}]),
    "comment_and_event": ([": ping", "event: message", 'data: {"x": "y"}', ""], [{"x": "y"}]),
    "no_trailing_blank": (['data: {"a": 1}', "", 'data: {"last": 1}'], [{"a": 1}, {"last": 1}]),
    "blank_lines_only": (["", "", ""], []),
    "empty": ([], []),
}

PARSERS = {
    "chat_completions": OpenAICompatProvider._iter_chat_completion_sse,
    "responses": iter_sse,
}


async def _collect(gen) -> list:
    return [event async for event in gen]


@pytest.mark.parametrize("parser", sorted(PARSERS))
@pytest.mark.parametrize("name", sorted(STREAMS))
async def test_each_parser_yields_the_expected_events(name: str, parser: str) -> None:
    lines, expected = STREAMS[name]
    assert await _collect(PARSERS[parser](_Lines(lines))) == expected
