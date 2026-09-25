"""Ogni percorso che chiama il provider deve dire a quale conversazione appartiene.

Gli header di OpenCode li mette il provider (``tests/providers/test_opencode_session_header.py``),
ma il provider può solo leggere quello che qualcuno ha dichiarato prima di lui.
Qui si verifica che quel qualcuno esista **su tutti** i percorsi che
chiamano il provider in questo albero, e non solo sul turno dell'utente.

Perché il turno da solo non basta: negli altri client la stessa integrazione si è
rotta proprio sulle chiamate ausiliarie — generazione del messaggio di commit,
pre-analisi di un'immagine — che partono fuori dal contesto del turno e restano
senza header. Il sintomo non è un errore: è un degrado silenzioso, cache mancata
e nei casi peggiori un 400 che fa ripiegare la richiesta altrove.

In Jenny i percorsi sono due: ``AgentRunner.run`` (il turno, e con lui cron,
Dream e heartbeat) e ``Consolidator.archive`` (la compattazione). Il terzo era
``classify_mood``, l'umore della mascotte: dal 24/09/2026 l'umore si legge dagli
emoji e non chiama piu' nessun provider.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jenny.agent.memory import Consolidator, MemoryStore
from jenny.config.schema import AgentDefaults
from jenny.providers.base import LLMProvider, LLMResponse
from jenny.providers.opencode import SESSION_HEADER, session_headers

GO_BASE = "https://opencode.ai/zen/go/v1"


def _observed_id() -> str:
    """L'ID che un provider OpenCode manderebbe se chiamato proprio adesso."""
    return session_headers(GO_BASE, fallback_id="NESSUNO-SCOPE")[SESSION_HEADER]


def _spying_provider() -> tuple[MagicMock, list[str]]:
    """Provider finto che annota, a ogni chiamata, la conversazione dichiarata."""
    seen: list[str] = []

    async def chat_with_retry(*args, **kwargs):
        seen.append(_observed_id())
        return LLMResponse(content="ok", tool_calls=[], usage={})

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = chat_with_retry
    return provider, seen


# --- il turno -----------------------------------------------------------------------


class TestTheTurn:

    async def _run(self, session_key: str | None) -> list[str]:
        from jenny.agent.runner import AgentRunner, AgentRunSpec

        provider, seen = _spying_provider()
        tools = MagicMock()
        tools.get_definitions.return_value = []
        await AgentRunner(provider).run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "ciao"}],
            tools=tools,
            model="m",
            max_iterations=2,
            max_tool_result_chars=AgentDefaults().max_tool_result_chars,
            session_key=session_key,
        ))
        return seen

    async def test_the_turn_declares_its_session(self) -> None:
        seen = await self._run("unified:default")
        assert seen and seen[0] != "NESSUNO-SCOPE"

    async def test_different_conversations_give_different_ids(self) -> None:
        # È il caso vero: cron, Dream e heartbeat arrivano qui con il loro
        # ``session_key_override`` e devono restare conversazioni distinte.
        user = await self._run("unified:default")
        dream = await self._run("internal:dream")
        heartbeat = await self._run("internal:heartbeat")
        assert len({user[0], dream[0], heartbeat[0]}) == 3

    async def test_the_same_session_gives_the_same_id_across_turns(self) -> None:
        first = await self._run("unified:default")
        second = await self._run("unified:default")
        assert first[0] == second[0]

    async def test_the_scope_closes_again_at_turn_end(self) -> None:
        await self._run("unified:default")
        assert _observed_id() == "NESSUNO-SCOPE"

    async def test_a_turn_without_session_does_not_break(self) -> None:
        seen = await self._run(None)
        assert seen == ["NESSUNO-SCOPE"]


# --- la compattazione ---------------------------------------------------------------


class TestTheCompaction:

    @pytest.fixture
    def _consolidator(self, tmp_path):
        provider, seen = _spying_provider()
        consolidator = Consolidator(
            store=MemoryStore(tmp_path),
            provider=provider,
            model="m",
            sessions=MagicMock(),
            context_window_tokens=1000,
            build_messages=MagicMock(return_value=[]),
            get_tool_definitions=MagicMock(return_value=[]),
            max_completion_tokens=100,
        )
        return consolidator, seen

    async def test_archive_declares_the_session_it_is_summarizing(
        self, _consolidator,
    ) -> None:
        consolidator, seen = _consolidator
        await consolidator.archive(
            [{"role": "user", "content": "ciao"}], session_key="unified:default",
        )
        assert seen and seen[0] != "NESSUNO-SCOPE"

    async def test_is_the_same_conversation_as_the_turn(self, _consolidator) -> None:
        # Compattare non è un'altra conversazione: è la stessa, riassunta. Se
        # l'ID divergesse, il gateway le vedrebbe come due.
        consolidator, seen = _consolidator
        await consolidator.archive(
            [{"role": "user", "content": "ciao"}], session_key="unified:default",
        )
        from jenny.providers.opencode import conversation_scope

        with conversation_scope("unified:default"):
            expected = _observed_id()
        assert seen[0] == expected

    async def test_without_key_does_not_break(self, _consolidator) -> None:
        consolidator, seen = _consolidator
        await consolidator.archive([{"role": "user", "content": "ciao"}])
        assert seen == ["NESSUNO-SCOPE"]
