"""Il tool con cui un turno silenzioso dichiara di non avere niente da dire.

Il difetto che questi test presidiano è stato misurato sul Titan 2, non dedotto:
fra il 21/08 e il 3/09/2026 quattordici bolle di riempimento sono arrivate nella
chat dell'utente (``silent``, ``x``, ``noop``, ``placeholder``, ``silent-skip``,
``CHECK_OK 1``, un «tutto a posto» in italiano, due bolle vuote…), e in una finestra di
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

from support.agent import make_loop, make_provider

from jenny.agent.tools import nothing_to_report as ntr
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.message import MessageTool
from jenny.agent.tools.nothing_to_report import NothingToReportTool, declared_marker_lines
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.cron.session_turns import monitor_session_key
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.session.turn_visibility import TurnVisibility, silent_turn_metadata

# Un turno silenzioso di lavoro interno: la sessione di un monitor del cron.
MONITOR_SESSION_KEY = monitor_session_key("rain-check")


def _tool(*, silent: bool) -> NothingToReportTool:
    tool = NothingToReportTool()
    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="default",
            session_key=MONITOR_SESSION_KEY if silent else "unified:default",
            metadata=silent_turn_metadata() if silent else {},
        )
    )
    tool.start_turn()
    return tool


class TestTheDeclarationItself:
    async def test_checked_becomes_the_ok_marker(self) -> None:
        tool = _tool(silent=True)

        result = await tool.execute(checked=True)

        assert not result.startswith("Error")
        assert tool.verdicts() == [(True, "")]
        assert declared_marker_lines(tool) == "\nCHECK_OK"

    async def test_not_checked_becomes_the_failure_with_its_reason(self) -> None:
        """Un controllo che non ha girato lo dice al tool, invece di ricordarsi
        una riga a parte nel testo."""
        tool = _tool(silent=True)

        await tool.execute(checked=False, reason="  il file\n output/x.txt non esiste ")

        assert declared_marker_lines(tool) == "\nCHECK_FAILED: il file output/x.txt non esiste"

    async def test_a_failure_without_a_reason_still_counts(self) -> None:
        tool = _tool(silent=True)

        await tool.execute(checked=False)

        assert declared_marker_lines(tool) == "\nCHECK_FAILED: no reason given"

    async def test_a_call_without_a_verdict_records_nothing(self) -> None:
        """Senza ``checked`` non si indovina: né un «tutto bene» che chiuderebbe
        come sano un controllo delegato, né un guasto inventato."""
        tool = _tool(silent=True)

        result = await tool.execute()

        assert "checked=true" in result and not result.startswith("Error")
        assert declared_marker_lines(tool) == ""

    async def test_a_failure_wins_over_an_ok_in_the_same_turn(self) -> None:
        tool = _tool(silent=True)

        await tool.execute(checked=True)
        await tool.execute(checked=False, reason="host giù")

        assert declared_marker_lines(tool) == "\nCHECK_FAILED: host giù"

    async def test_the_reason_is_one_short_line(self) -> None:
        tool = _tool(silent=True)

        await tool.execute(checked=False, reason="x" * 1000)

        assert len(tool.verdicts()[0][1]) == ntr._REASON_MAX

    def test_the_verdict_is_required_by_the_schema(self) -> None:
        params = NothingToReportTool().parameters
        assert params["required"] == ["checked"]
        assert "task" not in params["properties"]
        assert "you could not compute" in params["properties"]["checked"]["description"]


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
                session_key=MONITOR_SESSION_KEY,
                metadata=silent_turn_metadata(),
            )
        )

        await tool.execute(checked=True)

        assert ntr._NO_TURN_STATE == {}

    async def test_start_turn_resets(self) -> None:
        tool = _tool(silent=True)
        await tool.execute(checked=True)

        tool.start_turn()

        assert tool.verdicts() == []

    async def test_two_concurrent_turns_do_not_see_each_other(self) -> None:
        """Lo stato vive in un dict *dentro* una ContextVar, non in un attributo:
        l'agente principale gira un monitor e una chat nello stesso processo."""
        tool = NothingToReportTool()
        tool.set_context(
            RequestContext(
                channel="websocket",
                chat_id="default",
                session_key=MONITOR_SESSION_KEY,
                metadata=silent_turn_metadata(),
            )
        )

        async def turn(checked: bool) -> list[tuple[bool, str]]:
            tool.start_turn()
            await asyncio.sleep(0)
            await tool.execute(checked=checked, reason="giù")
            await asyncio.sleep(0)
            return tool.verdicts()

        first, second = await asyncio.gather(
            asyncio.create_task(turn(True)),
            asyncio.create_task(turn(False)),
        )

        assert first == [(True, "")]
        assert second == [(False, "giù")]


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
            "controlla la pioggia",
            session_key=MONITOR_SESSION_KEY,
            channel="websocket",
            chat_id="default",
            visibility=TurnVisibility.SILENT,
        )

    async def test_a_silent_run_that_abstains_delivers_nothing(self, tmp_path: Path) -> None:
        loop = self._loop(
            tmp_path, [self._abstain({"checked": True}), LLMResponse(content="fatto")]
        )
        delivered: list[OutboundMessage] = []

        outcome = await self._run(loop, delivered)

        assert delivered == []
        assert outcome.spoke is False

    async def test_the_declaration_reaches_the_outcome(self, tmp_path: Path) -> None:
        loop = self._loop(
            tmp_path, [self._abstain({"checked": True}), LLMResponse(content="fatto")]
        )

        outcome = await self._run(loop, [])

        assert outcome.final_text.endswith("\nCHECK_OK")

    async def test_the_declaration_never_reaches_the_session_history(
        self, tmp_path: Path
    ) -> None:
        """Il marcatore è per il registratore, non per il prompt del run dopo:
        rigiocarlo insegnerebbe al modello a scriverlo da sé."""
        loop = self._loop(
            tmp_path, [self._abstain({"checked": True}), LLMResponse(content="fatto")]
        )

        await self._run(loop, [])

        session = loop.sessions.get_or_create(MONITOR_SESSION_KEY)
        assert not any("CHECK_OK" in str(m.get("content")) for m in session.messages)

    async def test_a_visible_turn_gets_no_synthetic_marker(self, tmp_path: Path) -> None:
        loop = self._loop(
            tmp_path, [self._abstain({"checked": True}), LLMResponse(content="ciao")]
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
            tmp_path, [self._abstain({"checked": True}), LLMResponse(content="fatto")]
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
        await tool.execute(checked=True)

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
                session_key_override=MONITOR_SESSION_KEY,
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
