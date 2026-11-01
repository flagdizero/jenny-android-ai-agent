"""La copia Telegram di un avviso di quaderno dice da quale quaderno viene.

In Telegram tutto arriva nella stessa chat personale: senza il nome davanti, un
avviso del quaderno ``piante`` è indistinguibile da uno personale. Il prefisso
``[piante]`` va solo sulla copia ``_mirror``: la consegna primaria sta già nella
chat del quaderno, e lì il nome sarebbe rumore — come lo sarebbe nella sessione
del quaderno, dove la riga registrata resta il testo nudo.

I test passano dalle due strade che arrivano al fan-out: un monitor del quaderno
(il contesto di consegna di ``bound_runner``, poi il tool ``message`` dentro il
turno silenzioso) e un invio cross-canale del tool ``message`` verso la chat del
quaderno (``_proactive_fanout``).
"""

from __future__ import annotations

from typing import Any

import pytest
from support.aio import drain_nowait

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.message import MessageTool
from jenny.board.cron_link import make_link, notebook_delivery
from jenny.board.model import NOTEBOOK
from jenny.bus.events import INTERNAL_CHANNEL, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.bound_runner import _bound_session_delivery_context
from jenny.cron.types import CronJob, CronPayload
from jenny.runtime.delivery import ChannelDeliverer, with_notebook_prefix
from jenny.session.keys import UNIFIED_SESSION_KEY

TELEGRAM_CHAT = "42"


class RecordingSessions:
    """Session manager finto: tiene le righe per chiave."""

    def __init__(self) -> None:
        self.rows: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}

    def get_or_create(self, key: str) -> Any:
        rows = self.rows.setdefault(key, [])

        class _Session:
            def add_message(self, role: str, content: str, **extra: Any) -> None:
                rows.append((role, content, extra))

        return _Session()

    def save(self, session: Any) -> None:
        pass


def _deliverer(bus: MessageBus, sessions: RecordingSessions) -> ChannelDeliverer:
    return ChannelDeliverer(
        bus=bus,
        session_manager=sessions,
        extra_targets=lambda: [("telegram", TELEGRAM_CHAT)],
    )


def _bubbles(bus: MessageBus) -> dict[str, OutboundMessage]:
    published = [m for m in drain_nowait(bus.outbound) if not m.metadata.get("_turn_end")]
    assert len(published) == 2, published
    return {m.channel: m for m in published}


def _monitor_job(origin: dict[str, str], *, board: dict[str, str] | None) -> CronJob:
    return CronJob(
        id="j1",
        name="annaffiatura",
        payload=CronPayload(mode="monitor", message="controlla il terriccio", board=board,
                            **origin),
    )


async def _ring_monitor(job: CronJob, deliverer: ChannelDeliverer, text: str) -> None:
    """Il monitor parla: contesto di consegna di ``bound_runner``, poi ``message``."""
    channel, chat_id, metadata = _bound_session_delivery_context(
        job, turn_seed=f"cron:{job.id}", source_label=job.name, monitor=True
    )
    tool = MessageTool(send_callback=deliverer.deliver)
    tool.set_context(RequestContext(channel=channel, chat_id=chat_id, metadata=metadata))
    result = await tool.execute(content=text)
    assert result.startswith("Message sent"), result


async def test_a_notebook_monitor_reaches_telegram_with_the_notebook_name() -> None:
    bus, sessions = MessageBus(), RecordingSessions()
    job = _monitor_job(notebook_delivery("piante"), board=make_link("piante", NOTEBOOK))

    await _ring_monitor(job, _deliverer(bus, sessions), "Il basilico ha sete.")

    by_channel = _bubbles(bus)
    webui, telegram = by_channel["websocket"], by_channel["telegram"]
    assert webui.chat_id == "project:piante"
    assert webui.content == "Il basilico ha sete."
    assert not webui.metadata.get("_mirror")
    assert telegram.chat_id == TELEGRAM_CHAT
    assert telegram.content == "[piante] Il basilico ha sete."
    assert telegram.metadata.get("_mirror") is True
    # Una riga sola, nella sessione del quaderno, col testo nudo: la copia
    # Telegram non tocca la sessione personale.
    assert list(sessions.rows) == ["project:piante"]
    assert [row[1] for row in sessions.rows["project:piante"]] == ["Il basilico ha sete."]


async def test_a_personal_monitor_reaches_telegram_without_a_prefix() -> None:
    bus, sessions = MessageBus(), RecordingSessions()
    origin = {"session_key": UNIFIED_SESSION_KEY, "origin_channel": "websocket",
              "origin_chat_id": "default"}
    job = _monitor_job(origin, board=None)

    await _ring_monitor(job, _deliverer(bus, sessions), "Domani piove.")

    by_channel = _bubbles(bus)
    assert by_channel["websocket"].content == "Domani piove."
    assert by_channel["telegram"].content == "Domani piove."
    assert list(sessions.rows) == [UNIFIED_SESSION_KEY]
    assert len(sessions.rows[UNIFIED_SESSION_KEY]) == 1


async def test_a_cross_channel_send_to_a_notebook_chat_gets_the_prefix() -> None:
    """``message`` da un lavoro interno verso la chat del quaderno: ``_proactive_fanout``."""
    bus, sessions = MessageBus(), RecordingSessions()
    tool = MessageTool(send_callback=_deliverer(bus, sessions).deliver)
    tool.set_context(RequestContext(channel=INTERNAL_CHANNEL, chat_id="dream"))

    result = await tool.execute(content="Ho rinvasato la menta.", channel="websocket",
                                chat_id="project:piante")
    assert result.startswith("Message sent"), result

    by_channel = _bubbles(bus)
    assert by_channel["websocket"].chat_id == "project:piante"
    assert by_channel["websocket"].content == "Ho rinvasato la menta."
    assert by_channel["telegram"].content == "[piante] Ho rinvasato la menta."
    assert list(sessions.rows) == ["project:piante"]


async def test_an_invalid_notebook_chat_is_personal_and_gets_no_prefix() -> None:
    """``project:../fuori`` non è un quaderno: cade sulla chat personale, senza nome."""
    bus, sessions = MessageBus(), RecordingSessions()
    await _deliverer(bus, sessions).deliver(
        OutboundMessage(channel="websocket", chat_id="project:../fuori", content="ciao"),
        record=True,
        proactive=True,
    )
    by_channel = _bubbles(bus)
    assert by_channel["telegram"].content == "ciao"
    assert list(sessions.rows) == [UNIFIED_SESSION_KEY]


@pytest.mark.parametrize(
    ("content", "media", "expected"),
    [
        ("Annaffia.", False, "[piante] Annaffia."),
        ("  Annaffia.\n", False, "[piante] Annaffia."),
        # Un blocco markdown in apertura resta un blocco: il nome sta sopra.
        ("- basilico\n- menta", False, "[piante]\n- basilico\n- menta"),
        ("## Oggi\nAnnaffia.", False, "[piante]\n## Oggi\nAnnaffia."),
        ("1. basilico", False, "[piante]\n1. basilico"),
        ("> nota", False, "[piante]\n> nota"),
        ("```\nlog\n```", False, "[piante]\n```\nlog\n```"),
        # Un allegato senza testo: il nome diventa la didascalia.
        ("", True, "[piante]"),
        ("", False, ""),
    ],
)
def test_prefix_shape(content: str, media: bool, expected: str) -> None:
    assert with_notebook_prefix(content, "piante", has_media=media) == expected


def test_no_notebook_no_prefix() -> None:
    assert with_notebook_prefix("- uno", None) == "- uno"


# --- Il promemoria di un quaderno -------------------------------------------
#
# Un promemoria non è un turno silenzioso: la sua risposta finale la consegna il
# loop nella chat del quaderno (col ``turn_end`` del coordinator), e il turno la
# registra nella sessione del quaderno. ``bound_runner`` ne manda poi le sole
# copie per Telegram; se invece il turno parla col tool ``message``, le copie le
# fa il ``ChannelDeliverer`` alla consegna.


@pytest.fixture
def piante(tmp_path, monkeypatch):
    from jenny.board.store import mutate_board

    root = tmp_path / "workspace" / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: None)
    monkeypatch.setattr(
        "jenny.board.cron_link.notebook_root",
        lambda name: root if name == "piante" else None,
    )

    async def _quiet(_name):
        pass

    monkeypatch.setattr("jenny.board.notify.board_changed", _quiet)
    return root


class _Recorder:
    def write_run_record(self, run_id, record):
        pass


class _LoopLike:
    """Il loop, per quel che ne vede ``bound_runner``: consegna e torna l'esito.

    ``reply`` è la risposta finale (pubblicata sul bus come fa il loop, già
    streammata nella WebUI); ``via_tool`` fa invece parlare il turno col tool
    ``message`` vero, nel contesto del turno.
    """

    def __init__(self, bus: MessageBus, deliverer: ChannelDeliverer, *, reply: str = "",
                 via_tool: str = "", error: bool = False) -> None:
        from support.sessions import FakeSessions

        from jenny.agent.tools.registry import ToolRegistry

        self.tools = ToolRegistry()
        self.sessions = FakeSessions()
        self.mirror_delivery = deliverer.mirror
        self._bus, self._deliverer = bus, deliverer
        self._reply, self._via_tool, self._error = reply, via_tool, error
        self.received: list[Any] = []

    async def submit_cron_turn(self, msg):
        from jenny.agent.turn_types import TurnOutcome

        self.received.append(msg)
        if self._via_tool:
            tool = MessageTool(send_callback=self._deliverer.deliver)
            tool.set_context(RequestContext(channel=msg.channel, chat_id=msg.chat_id,
                                            metadata=msg.metadata))
            assert (await tool.execute(content=self._via_tool)).startswith("Message sent")
            return TurnOutcome.spoke_via_tool(final_text="ok")
        meta = {**msg.metadata, "_streamed": True}
        if self._error:
            meta["error"] = {"kind": "provider"}
        final = OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                content=self._reply, metadata=meta)
        await self._bus.publish_outbound(final)
        return TurnOutcome.delivered(final)


def _reminder(tmp_path, *, notebook: bool) -> CronJob:
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule

    origin = (notebook_delivery("piante") if notebook else
              {"session_key": UNIFIED_SESSION_KEY, "origin_channel": "websocket",
               "origin_chat_id": "default"})
    return CronService(tmp_path / "jobs.json").add_job(
        "annaffia", CronSchedule(kind="every", every_ms=3_600_000), "annaffia il basilico",
        board=make_link("piante", NOTEBOOK) if notebook else None, **origin,
    )


async def _ring(job: CronJob, agent: _LoopLike) -> None:
    from jenny.cron.bound_runner import run_bound_cron_job

    await run_bound_cron_job(job, agent=agent, cron=_Recorder())


async def test_a_notebook_reminder_reply_reaches_telegram_with_the_notebook_name(
    piante, tmp_path
) -> None:
    bus, sessions = MessageBus(), RecordingSessions()
    agent = _LoopLike(bus, _deliverer(bus, sessions), reply="È ora di annaffiare il basilico.")

    await _ring(_reminder(tmp_path, notebook=True), agent)

    published = drain_nowait(bus.outbound)
    # La chat del quaderno la riceve una volta sola, dal loop; il ``turn_end``
    # è del coordinator del turno, e il deliverer non ne aggiunge un secondo.
    assert [(m.channel, m.chat_id) for m in published] == [
        ("websocket", "project:piante"), ("telegram", TELEGRAM_CHAT)
    ]
    webui, telegram = published
    assert webui.content == "È ora di annaffiare il basilico."
    assert telegram.content == "[piante] È ora di annaffiare il basilico."
    assert telegram.metadata.get("_mirror") is True
    # Una copia è un messaggio finale: con ``_streamed`` il dispatcher la scarterebbe.
    assert "_streamed" not in telegram.metadata
    # La riga in cronologia l'ha già scritta il turno, nella sessione del quaderno.
    assert sessions.rows == {}


async def test_a_notebook_reminder_that_speaks_with_message_reaches_telegram_once(
    piante, tmp_path
) -> None:
    bus, sessions = MessageBus(), RecordingSessions()
    agent = _LoopLike(bus, _deliverer(bus, sessions), via_tool="Annaffia il basilico.")

    await _ring(_reminder(tmp_path, notebook=True), agent)

    published = drain_nowait(bus.outbound)
    assert [(m.channel, m.chat_id) for m in published] == [
        ("websocket", "project:piante"), ("telegram", TELEGRAM_CHAT)
    ]
    assert published[0].content == "Annaffia il basilico."
    assert published[1].content == "[piante] Annaffia il basilico."
    assert sessions.rows == {}


async def test_a_personal_reminder_stays_in_its_chat(piante, tmp_path) -> None:
    """Uno personale suona nella chat da cui è stato chiesto: nessuna copia."""
    bus, sessions = MessageBus(), RecordingSessions()
    agent = _LoopLike(bus, _deliverer(bus, sessions), reply="Sveglia!")

    await _ring(_reminder(tmp_path, notebook=False), agent)

    published = drain_nowait(bus.outbound)
    assert [(m.channel, m.content) for m in published] == [("websocket", "Sveglia!")]
    assert "_cron_mirror" not in agent.received[0].metadata


async def test_a_failed_notebook_reminder_stays_in_the_notebook(piante, tmp_path) -> None:
    """La bolla d'errore del provider non è un promemoria: in Telegram non va."""
    bus, sessions = MessageBus(), RecordingSessions()
    agent = _LoopLike(bus, _deliverer(bus, sessions), reply="Non riesco a rispondere.",
                      error=True)

    await _ring(_reminder(tmp_path, notebook=True), agent)

    assert [m.channel for m in drain_nowait(bus.outbound)] == ["websocket"]


def test_the_agent_is_handed_the_mirror() -> None:
    """``bound_runner`` trova il gancio con ``getattr``, perché fuori dal gateway
    non c'è: senza questo collegamento un promemoria di quaderno resterebbe nella
    sua chat senza che nulla lo dica."""
    import inspect

    from jenny.runtime.container import GatewayContainer

    source = inspect.getsource(GatewayContainer)
    assert "agent.mirror_delivery = self._deliverer.mirror" in source
