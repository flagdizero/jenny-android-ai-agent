"""Il tool con cui un turno silenzioso dichiara di non avere niente da dire.

Il difetto che questi test presidiano è stato misurato sul Titan 2, non dedotto:
fra il 21/08 e il 3/09/2026 quattordici bolle di riempimento sono arrivate nella
chat dell'utente (``silent``, ``x``, ``noop``, ``placeholder``, ``silent-skip``,
``CHECK_OK 1``, ``tutte le piante ok``, due bolle vuote…), e in una finestra di
logcat di 11 ore le **uniche due** chiamate a ``message`` erano entrambe
spazzatura. La causa non è il prompt: su un turno silenzioso "non ho niente da
dire" era l'*assenza* di un'azione, e un modello piccolo la codifica come
l'azione che ha.

Nessun test esistente poteva coglierlo. I fake in ``test_bound_runner.py`` e
``test_cron_monitor_could_not_check.py`` costruiscono i ``TurnOutcome`` a mano,
quindi nessuna scelta del modello passa mai da un tool vero.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.agent import make_loop, make_provider

from jenny.agent.tools import nothing_to_report as ntr
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.message import MessageTool
from jenny.agent.tools.nothing_to_report import NothingToReportTool, declared_marker_lines
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.session.keys import HEARTBEAT_SESSION_KEY
from jenny.session.turn_visibility import TurnVisibility, silent_turn_metadata


def _tool(*, silent: bool) -> NothingToReportTool:
    tool = NothingToReportTool()
    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="default",
            session_key=HEARTBEAT_SESSION_KEY if silent else "unified:default",
            metadata=silent_turn_metadata() if silent else {},
        )
    )
    tool.start_turn()
    return tool


class TestTheDeclarationItself:
    async def test_a_numbered_declaration_becomes_the_marker_line(self) -> None:
        tool = _tool(silent=True)

        result = await tool.execute(task=2)

        assert not result.startswith("Error")
        assert tool.declared_tasks() == [2]
        assert declared_marker_lines(tool) == "\nCHECK_OK 2"

    async def test_a_bare_declaration_writes_no_marker(self) -> None:
        """Il cuore della correzione, e la ragione per cui il tool ha un numero.

        ``CHECK_OK`` non dice "non ho niente da dire": dice "il controllo ha
        prodotto la sua risposta". Un marcatore anonimo viene attribuito al task
        in sospeso quando ce n'è uno solo, e ``record_followup_outcomes`` su un
        verdetto positivo cancella la voce — sequenza dei guasti ed ``escalated``
        compresi. Sintetizzarlo su un'astensione nuda chiuderebbe come sano un
        controllo di cui il turno non ha detto niente.
        """
        tool = _tool(silent=True)

        await tool.execute()

        assert tool.declared_tasks() == []
        assert declared_marker_lines(tool) == ""

    async def test_the_numbers_are_ordered_and_deduplicated(self) -> None:
        tool = _tool(silent=True)

        await tool.execute(task=3)
        await tool.execute(task=1)
        await tool.execute(task=3)

        assert declared_marker_lines(tool) == "\nCHECK_OK 3\nCHECK_OK 1"

    @pytest.mark.parametrize("written", ["2", " #2 ", "[2]", "2)"])
    async def test_the_shapes_a_model_writes_without_thinking_are_read(
        self, written: str
    ) -> None:
        tool = _tool(silent=True)

        await tool.execute(task=written)

        assert tool.declared_tasks() == [2]

    async def test_a_number_that_is_not_one_is_not_guessed(self) -> None:
        tool = _tool(silent=True)

        result = await tool.execute(task="the waterbot one")

        assert not result.startswith("Error")
        assert tool.declared_tasks() == []

    async def test_declarations_are_capped(self) -> None:
        tool = _tool(silent=True)

        for n in range(1, ntr._MAX_DECLARATIONS + 5):
            await tool.execute(task=n)

        assert len(tool.declared_tasks()) == ntr._MAX_DECLARATIONS


class TestWhereItDoesNotApply:
    async def test_a_visible_turn_is_refused(self) -> None:
        tool = _tool(silent=False)

        result = await tool.execute()

        assert "Not applicable" in result
        assert declared_marker_lines(tool) == ""

    async def test_the_refusal_is_not_charged_as_a_tool_error(self) -> None:
        """``tool_execution`` tratta ogni stringa che comincia per ``Error`` come
        un errore: appende "try a different approach" e la addebita al
        ``ToolErrorBudget``. Su un rifiuto per contesto sbagliato quello è un
        invito a insistere."""
        tool = _tool(silent=False)

        assert not (await tool.execute()).startswith("Error")

    def test_the_renderer_ignores_anything_that_is_not_this_tool(self) -> None:
        assert declared_marker_lines(None) == ""
        assert declared_marker_lines(MessageTool()) == ""


class TestThePerTurnState:
    async def test_the_shared_default_is_never_mutated(self) -> None:
        """Senza la guardia sulla sentinella, una chiamata fuori da un turno
        avvelenerebbe il dict di modulo per **ogni** turno futuro, chat comprese.
        Non è ipotetico: la FSM ha un percorso che salta BUILD — e con esso
        ``start_turn()`` — pur arrivando a costruire il ``TurnOutcome``."""
        tool = NothingToReportTool()
        tool.set_context(
            RequestContext(
                channel="websocket",
                chat_id="default",
                session_key=HEARTBEAT_SESSION_KEY,
                metadata=silent_turn_metadata(),
            )
        )

        await tool.execute(task=1)

        assert ntr._NO_TURN_STATE == {}

    async def test_start_turn_resets(self) -> None:
        tool = _tool(silent=True)
        await tool.execute(task=1)

        tool.start_turn()

        assert tool.declared_tasks() == []

    async def test_two_concurrent_turns_do_not_see_each_other(self) -> None:
        """Lo stato vive in un dict *dentro* una ContextVar, non in un attributo:
        l'agente principale gira un heartbeat e una chat nello stesso processo."""
        tool = NothingToReportTool()
        tool.set_context(
            RequestContext(
                channel="websocket",
                chat_id="default",
                session_key=HEARTBEAT_SESSION_KEY,
                metadata=silent_turn_metadata(),
            )
        )

        async def turn(number: int) -> list[int]:
            tool.start_turn()
            await asyncio.sleep(0)
            await tool.execute(task=number)
            await asyncio.sleep(0)
            return tool.declared_tasks()

        first, second = await asyncio.gather(
            asyncio.create_task(turn(1)),
            asyncio.create_task(turn(2)),
        )

        assert first == [1]
        assert second == [2]


class TestTheWholeTurn:
    """Il bug delle 14 bolle, da capo a fondo: loop vero, registry vero."""

    def _loop(self, tmp_path: Path, responses: list[LLMResponse]) -> Any:
        provider = make_provider()
        queue = list(responses)

        async def _chat(**_kwargs: Any) -> LLMResponse:
            return queue.pop(0) if len(queue) > 1 else queue[0]

        provider.chat_with_retry = _chat
        loop = make_loop(tmp_path, provider=provider)
        loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)
        return loop

    @staticmethod
    def _abstain(arguments: dict[str, Any] | None = None) -> LLMResponse:
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(id="c1", name="nothing_to_report", arguments=arguments or {})
            ],
        )

    async def _run(self, loop: Any, delivered: list[OutboundMessage]) -> Any:
        loop.bus.publish_outbound = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda msg: delivered.append(msg)
        )
        return await loop.process_direct_outcome(
            "controlla le piante",
            session_key=HEARTBEAT_SESSION_KEY,
            channel="websocket",
            chat_id="default",
            visibility=TurnVisibility.SILENT,
        )

    async def test_a_silent_run_that_abstains_delivers_nothing(self, tmp_path: Path) -> None:
        loop = self._loop(
            tmp_path, [self._abstain({"task": 1}), LLMResponse(content="fatto")]
        )
        delivered: list[OutboundMessage] = []

        outcome = await self._run(loop, delivered)

        assert delivered == []
        assert outcome.spoke is False

    async def test_the_declaration_reaches_the_outcome(self, tmp_path: Path) -> None:
        loop = self._loop(
            tmp_path, [self._abstain({"task": 1}), LLMResponse(content="fatto")]
        )

        outcome = await self._run(loop, [])

        assert outcome.final_text.endswith("\nCHECK_OK 1")

    async def test_the_declaration_never_reaches_the_session_history(
        self, tmp_path: Path
    ) -> None:
        """Il marcatore è per il registratore, non per il prompt del run dopo:
        rigiocarlo insegnerebbe al modello a scriverlo da sé."""
        loop = self._loop(
            tmp_path, [self._abstain({"task": 1}), LLMResponse(content="fatto")]
        )

        await self._run(loop, [])

        session = loop.sessions.get_or_create(HEARTBEAT_SESSION_KEY)
        assert not any("CHECK_OK" in str(m.get("content")) for m in session.messages)

    async def test_a_visible_turn_gets_no_synthetic_marker(self, tmp_path: Path) -> None:
        loop = self._loop(
            tmp_path, [self._abstain({"task": 1}), LLMResponse(content="ciao")]
        )
        loop.bus.publish_outbound = AsyncMock()  # type: ignore[method-assign]

        outcome = await loop.process_direct_outcome(
            "ciao",
            session_key="unified:default",
            channel="websocket",
            chat_id="default",
        )

        assert "CHECK_OK" not in outcome.final_text

    async def test_the_declaration_is_not_a_delivery(self, tmp_path: Path) -> None:
        """Un monitor che si astiene resta ``silenced``, non diventa ``ok``.

        È la riga ``if not outcome.spoke`` in ``bound_runner``: se il tool
        alzasse ``_sent_in_turn``, un ciclo senza nulla da dire verrebbe
        archiviato come un ciclo che ha parlato.
        """
        loop = self._loop(
            tmp_path, [self._abstain({"task": 1}), LLMResponse(content="fatto")]
        )

        await self._run(loop, [])

        message_tool = loop.tools.get("message")
        assert isinstance(message_tool, MessageTool)
        assert loop._message_tool_spoke() is False


class TestTheToolIsWhereTheTurnCanSeeIt:
    def test_it_is_registered_for_the_agent_that_runs_the_checks(
        self, tmp_path: Path
    ) -> None:
        loop = make_loop(tmp_path)

        assert isinstance(loop.tools.get("nothing_to_report"), NothingToReportTool)

    async def test_the_announce_turn_resets_it_too(self, tmp_path: Path) -> None:
        """Il turno d'annuncio non passa da BUILD ma da ``_begin_turn_tooling``:
        se il reset vivesse solo nella FSM, le dichiarazioni di un annuncio
        colerebbero in quello dopo."""
        loop = make_loop(tmp_path)
        loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)
        tool = loop.tools.get("nothing_to_report")
        assert isinstance(tool, NothingToReportTool)
        tool.start_turn()
        await tool.execute(task=9)

        loop._run_agent_loop = AsyncMock(  # type: ignore[method-assign]
            return_value=("", [], [], "stop", False)
        )
        outcome = await loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="websocket:default",
                content="[Subagent 'x' completed successfully]",
                metadata={"subagent_task_id": "sub-1"},
                session_key_override=HEARTBEAT_SESSION_KEY,
            )
        )

        assert "CHECK_OK" not in outcome.final_text


def test_the_tool_description_does_not_teach_the_routing_rule() -> None:
    """Il corpus dei prompt vieta a chiunque tranne al tool ``cron`` di
    insegnare dove vive un lavoro schedulato. La descrizione di questo tool
    entra in quel corpus (``test_prompt_corpus_scheduling``), e il vincolo qui è
    ripetuto perché chi la riscrive legge questo file, non quello."""
    text = NothingToReportTool().description + str(NothingToReportTool.parameters)

    for forbidden in ("HEARTBEAT.md", "recurring", "repeating", '"monitor"', '"reminder"'):
        assert forbidden not in text
    assert "cron" not in text.lower()


def test_the_module_is_registered_in_the_loader_list() -> None:
    from jenny.agent.tools.loader import _HARDCODED_TOOL_MODULES

    assert "nothing_to_report" in _HARDCODED_TOOL_MODULES


def test_the_tool_builds_from_a_bare_context() -> None:
    """``test_schema_wire_limits`` carica il registry con un ``MagicMock``: un
    ``create`` che interroga il ctx farebbe fallire quella sweep, non questo."""
    assert isinstance(NothingToReportTool.create(MagicMock()), NothingToReportTool)
    assert NothingToReportTool.enabled(SimpleNamespace()) is True
